"""Respaldo diario de la asistencia fuera del servidor (EP-14 US-14.7, D5).

Volcado logico de empleados, carnets anulados y marcas en JSON comprimido. El
destino sale de la variable RESPALDO_DESTINO:
- `s3://bucket/prefijo`: almacenamiento compatible con S3 (boto3, con las
  variables AWS_* de siempre y AWS_ENDPOINT_URL si no es Amazon);
- una carpeta con ruta absoluta (para pruebas o un disco montado).
Cualquier otra cosa es un error: un destino mal escrito no puede terminar en el
disco del contenedor, que se pierde.

El nombre de cada respaldo lleva el entorno (RAILWAY_ENVIRONMENT_NAME), asi
staging y produccion no se pisan aunque compartan el bucket.

Las empresas, locales y usuarios a los que apuntan los datos no van en este
respaldo: son de la base principal y se respaldan con ella.
"""

import gzip
import json
import os
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path

from django.core import serializers
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, connection, transaction
from django.utils import timezone
from django.utils.text import slugify

from .models import CarnetAnulado, Empleado, Horario, HorarioCambio, Marca, ReglasAsistencia

RETENCION = 30
PREFIJO = "asistencia-"
EXTENSION = ".json.gz"
# Orden de carga: marcas, carnets anulados y horarios apuntan al empleado.
# Reglas y horarios entraron con EP-14 D18; un respaldo anterior no los trae y
# restaura igual (quedan vacios).
MODELOS = (Empleado, CarnetAnulado, Marca, ReglasAsistencia, Horario, HorarioCambio)
TANDA = 2000


class RespaldoError(Exception):
    pass


@dataclass
class Restauracion:
    cantidades: dict
    # Referencias a usuarios que ya no existen: quedan vacias, como en la base viva.
    usuarios_vaciados: int


class _Codificador(DjangoJSONEncoder):
    # El serializador "json" de Django recorta las horas al milisegundo; aca no.
    def default(self, o):
        if isinstance(o, (datetime, time)):
            return o.isoformat()
        return super().default(o)


def generar_respaldo() -> bytes:
    salida = BytesIO()
    en_transaccion = connection.in_atomic_block
    with transaction.atomic(), gzip.GzipFile(fileobj=salida, mode="wb", mtime=0) as archivo:
        if connection.vendor == "postgresql" and not en_transaccion:
            # Una sola foto de la base para las tres tablas.
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        archivo.write(b"[")
        separador = b""
        for modelo in MODELOS:
            # De a tandas: las marcas no se borran nunca y crecen todos los anos.
            for objeto in modelo.objects.order_by("pk").iterator(chunk_size=TANDA):
                fila = serializers.serialize("python", [objeto])[0]
                archivo.write(separador + json.dumps(fila, cls=_Codificador, ensure_ascii=False).encode("utf-8"))
                separador = b","
        archivo.write(b"]")
    return salida.getvalue()


def entorno_actual() -> str:
    return slugify(os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "") or "local"


def nombre_de_respaldo(entorno: str, cuando=None) -> str:
    return f"{PREFIJO}{entorno}-{timezone.localtime(cuando).strftime('%Y%m%d-%H%M%S')}{EXTENSION}"


def fecha_de_respaldo(nombre: str, entorno: str) -> date | None:
    """La fecha de un respaldo de `entorno`, o None si el nombre es de otro."""
    coincide = re.fullmatch(rf"{re.escape(PREFIJO + entorno)}-(\d{{8}})-\d{{6}}{re.escape(EXTENSION)}", nombre)
    if not coincide:
        return None
    try:
        return datetime.strptime(coincide[1], "%Y%m%d").date()
    except ValueError:
        return None


def respaldos_del_entorno(destino, entorno: str) -> list[str]:
    return sorted(n for n in destino.listar() if fecha_de_respaldo(n, entorno))


def hay_datos() -> bool:
    return any(modelo.objects.exists() for modelo in MODELOS)


def _referencias_externas(objeto):
    for campo in type(objeto)._meta.concrete_fields:
        # one_to_one: las reglas apuntan a su empresa con un OneToOne.
        if (campo.many_to_one or campo.one_to_one) and campo.related_model not in MODELOS:
            yield campo, getattr(objeto, campo.attname)


def _completar_referencias(objetos) -> int:
    """Empresas y locales tienen que existir; un usuario que ya no existe queda
    vacio (las FK a usuario son SET_NULL, como paso en la base viva)."""
    pedidos = defaultdict(set)
    for objeto in objetos:
        for campo, valor in _referencias_externas(objeto):
            if valor is not None:
                pedidos[campo.related_model].add(valor)
    existentes = {
        modelo: set(modelo._base_manager.filter(pk__in=ids).values_list("pk", flat=True))
        for modelo, ids in pedidos.items()
    }
    faltantes, vaciados = defaultdict(set), 0
    for objeto in objetos:
        for campo, valor in _referencias_externas(objeto):
            if valor is None or valor in existentes[campo.related_model]:
                continue
            if campo.null:
                setattr(objeto, campo.attname, None)
                vaciados += 1
            else:
                faltantes[campo.related_model.__name__].add(valor)
    if faltantes:
        detalle = "; ".join(f"{modelo} {', '.join(map(str, sorted(ids)))}" for modelo, ids in sorted(faltantes.items()))
        raise RespaldoError(
            f"El respaldo apunta a datos que no estan en la base: {detalle}. "
            "Primero hay que restaurar la base principal."
        )
    return vaciados


@transaction.atomic
def restaurar_respaldo(contenido: bytes, *, reemplazar: bool = False) -> Restauracion:
    try:
        datos = json.loads(gzip.decompress(contenido).decode("utf-8"))
        if not isinstance(datos, list):
            raise ValueError("no es una lista de objetos")
        objetos = list(serializers.deserialize("python", datos))
    except (
        OSError, EOFError, ValueError, TypeError, KeyError, AttributeError, serializers.base.DeserializationError
    ) as error:
        raise RespaldoError(f"El archivo no es un respaldo de asistencia: {error}") from error
    ajenos = {type(o.object).__name__ for o in objetos if type(o.object) not in MODELOS}
    if ajenos:
        raise RespaldoError(f"El respaldo trae datos que no son de asistencia: {', '.join(sorted(ajenos))}")
    vaciados = _completar_referencias([o.object for o in objetos])
    if hay_datos():
        if not reemplazar:
            raise RespaldoError("Ya hay datos de asistencia. Para pisarlos usar --reemplazar.")
        for modelo in reversed(MODELOS):
            modelo.objects.all().delete()
    for objeto in objetos:
        # Las tablas estan vacias: solo INSERT, sin el UPDATE previo de save().
        objeto.save(force_insert=True)
    try:
        # En Postgres las FK se chequean recien al confirmar. Si fallaran ahi,
        # el reinicio de ids de abajo (que no vuelve atras) dejaria las
        # secuencias corridas y cada fichaje siguiente daria error.
        connection.check_constraints(table_names=[modelo._meta.db_table for modelo in MODELOS])
    except IntegrityError as error:
        raise RespaldoError(f"El respaldo tiene referencias rotas: {error}") from error
    # La secuencia de ids tiene que seguir despues del ultimo restaurado.
    with connection.cursor() as cursor:
        for sentencia in connection.ops.sequence_reset_sql(no_style(), MODELOS):
            cursor.execute(sentencia)
    return Restauracion({modelo.__name__: modelo.objects.count() for modelo in MODELOS}, vaciados)


class CarpetaLocal:
    def __init__(self, ruta):
        self.ruta = Path(ruta)

    def describir(self, nombre: str) -> str:
        return str(self.ruta / nombre)

    def subir(self, nombre: str, contenido: bytes) -> None:
        self.ruta.mkdir(parents=True, exist_ok=True)
        (self.ruta / nombre).write_bytes(contenido)

    def bajar(self, nombre: str) -> bytes:
        return (self.ruta / nombre).read_bytes()

    def listar(self) -> list[str]:
        if not self.ruta.is_dir():
            return []
        return sorted(p.name for p in self.ruta.iterdir() if p.name.startswith(PREFIJO) and p.name.endswith(EXTENSION))

    def borrar(self, nombre: str) -> None:
        (self.ruta / nombre).unlink()


class S3:
    def __init__(self, bucket: str, prefijo: str):
        import boto3  # Solo hace falta en el servidor que respalda a S3.

        self.cliente = boto3.client("s3", endpoint_url=os.environ.get("AWS_ENDPOINT_URL") or None)
        self.bucket = bucket
        self.prefijo = f"{prefijo.strip('/')}/" if prefijo.strip("/") else ""

    def describir(self, nombre: str) -> str:
        return f"s3://{self.bucket}/{self.prefijo}{nombre}"

    def subir(self, nombre: str, contenido: bytes) -> None:
        self.cliente.put_object(Bucket=self.bucket, Key=self.prefijo + nombre, Body=contenido)

    def bajar(self, nombre: str) -> bytes:
        return self.cliente.get_object(Bucket=self.bucket, Key=self.prefijo + nombre)["Body"].read()

    def listar(self) -> list[str]:
        nombres = []
        paginas = self.cliente.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefijo + PREFIJO)
        for pagina in paginas:
            for objeto in pagina.get("Contents", []):
                nombre = objeto["Key"][len(self.prefijo):]
                if nombre.endswith(EXTENSION):
                    nombres.append(nombre)
        return sorted(nombres)

    def borrar(self, nombre: str) -> None:
        self.cliente.delete_object(Bucket=self.bucket, Key=self.prefijo + nombre)


def destino_configurado():
    destino = (os.environ.get("RESPALDO_DESTINO") or "").strip()
    if not destino:
        raise RespaldoError("Falta la variable RESPALDO_DESTINO.")
    esquema, separador, resto = destino.partition("://")
    if separador:
        if esquema.lower() != "s3":
            raise RespaldoError(f"RESPALDO_DESTINO no se entiende ({destino!r}): usar s3://bucket/prefijo.")
        bucket, _, prefijo = resto.partition("/")
        if not bucket:
            raise RespaldoError("RESPALDO_DESTINO no tiene bucket: usar s3://bucket/prefijo.")
        return S3(bucket, prefijo)
    if not Path(destino).is_absolute():
        raise RespaldoError(
            f"RESPALDO_DESTINO no se entiende ({destino!r}): usar s3://bucket/prefijo o una carpeta con ruta absoluta."
        )
    return CarpetaLocal(destino)


def podar(destino, entorno: str, *, hoy: date | None = None, retencion: int = RETENCION) -> list[str]:
    """Borra los respaldos de `entorno` que tienen mas de `retencion` dias, pero
    deja siempre los ultimos `retencion` aunque sean mas viejos (por si el cron
    estuvo parado). Los de otros entornos no se tocan."""
    limite = (hoy or timezone.localdate()) - timedelta(days=retencion)
    propios = respaldos_del_entorno(destino, entorno)
    viejos = [n for n in propios[:-retencion] if fecha_de_respaldo(n, entorno) < limite]
    for nombre in viejos:
        destino.borrar(nombre)
    return viejos


def _pedir(url: str, cuerpo: bytes | None = None) -> None:
    headers = {"Content-Type": "application/json"} if cuerpo is not None else {}
    pedido = urllib.request.Request(url, data=cuerpo, headers=headers, method="POST" if cuerpo is not None else "GET")
    with urllib.request.urlopen(pedido, timeout=10):
        pass


def avisar_falla(mensaje: str) -> bool:
    """Devuelve False si no hay a quien avisar."""
    url = (os.environ.get("RESPALDO_AVISO_URL") or "").strip()
    if not url:
        return False
    _pedir(url, json.dumps({"text": f"Gerayse: fallo el respaldo de asistencia. {mensaje}"}).encode("utf-8"))
    return True


def avisar_ok() -> bool:
    """Ping a un monitor que alerta si un dia no llega (cron parado, proceso
    muerto): las fallas que no pasan por avisar_falla."""
    url = (os.environ.get("RESPALDO_OK_URL") or "").strip()
    if not url:
        return False
    _pedir(url)
    return True
