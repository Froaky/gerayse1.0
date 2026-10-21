"""Respaldo diario de la asistencia fuera del servidor (EP-14 US-14.7, D5).

Volcado logico de empleados, carnets anulados y marcas en JSON comprimido. El
destino sale de la variable RESPALDO_DESTINO:
- `s3://bucket/prefijo`: almacenamiento compatible con S3 (boto3, con las
  variables AWS_* de siempre y AWS_ENDPOINT_URL si no es Amazon);
- cualquier otra cosa: una carpeta local (para pruebas o un disco montado).

Las empresas, locales y usuarios a los que apuntan los datos no van en este
respaldo: son de la base principal y se respaldan con ella.
"""

import gzip
import json
import os
import urllib.request
from datetime import datetime, time
from pathlib import Path

from django.core import serializers
from django.core.management.color import no_style
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from .models import CarnetAnulado, Empleado, Marca

RETENCION = 30
PREFIJO = "asistencia-"
EXTENSION = ".json.gz"
# Orden de carga: las marcas y los carnets anulados apuntan al empleado.
MODELOS = (Empleado, CarnetAnulado, Marca)


class RespaldoError(Exception):
    pass


class _Codificador(DjangoJSONEncoder):
    # El serializador "json" de Django recorta las horas al milisegundo; aca no.
    def default(self, o):
        if isinstance(o, (datetime, time)):
            return o.isoformat()
        return super().default(o)


def generar_respaldo() -> bytes:
    objetos = [obj for modelo in MODELOS for obj in modelo.objects.order_by("pk")]
    texto = json.dumps(serializers.serialize("python", objetos), cls=_Codificador, ensure_ascii=False)
    return gzip.compress(texto.encode("utf-8"))


def nombre_de_respaldo(cuando=None) -> str:
    return f"{PREFIJO}{timezone.localtime(cuando).strftime('%Y%m%d-%H%M%S')}{EXTENSION}"


def hay_datos() -> bool:
    return any(modelo.objects.exists() for modelo in MODELOS)


@transaction.atomic
def restaurar_respaldo(contenido: bytes, *, reemplazar: bool = False) -> dict:
    try:
        datos = json.loads(gzip.decompress(contenido).decode("utf-8"))
        if not isinstance(datos, list):
            raise ValueError("no es una lista de objetos")
        objetos = list(serializers.deserialize("python", datos))
    except (OSError, EOFError, ValueError, TypeError, serializers.base.DeserializationError) as error:
        raise RespaldoError(f"El archivo no es un respaldo de asistencia: {error}") from error
    ajenos = {type(o.object).__name__ for o in objetos if type(o.object) not in MODELOS}
    if ajenos:
        raise RespaldoError(f"El respaldo trae datos que no son de asistencia: {', '.join(sorted(ajenos))}")
    if hay_datos():
        if not reemplazar:
            raise RespaldoError("Ya hay datos de asistencia. Para pisarlos usar --reemplazar.")
        for modelo in reversed(MODELOS):
            modelo.objects.all().delete()
    for objeto in objetos:
        objeto.save()
    # En Postgres la secuencia de ids tiene que seguir despues del ultimo restaurado.
    with connection.cursor() as cursor:
        for sentencia in connection.ops.sequence_reset_sql(no_style(), MODELOS):
            cursor.execute(sentencia)
    return {modelo.__name__: modelo.objects.count() for modelo in MODELOS}


class CarpetaLocal:
    def __init__(self, ruta):
        self.ruta = Path(ruta)

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
    if destino.startswith("s3://"):
        bucket, _, prefijo = destino[len("s3://"):].partition("/")
        if not bucket:
            raise RespaldoError("RESPALDO_DESTINO no tiene bucket: usar s3://bucket/prefijo.")
        return S3(bucket, prefijo)
    return CarpetaLocal(destino)


def podar(destino, retencion: int = RETENCION) -> list[str]:
    """Deja los ultimos `retencion` respaldos; los nombres ordenan por fecha."""
    viejos = destino.listar()[:-retencion]
    for nombre in viejos:
        destino.borrar(nombre)
    return viejos


def avisar_falla(mensaje: str) -> None:
    url = (os.environ.get("RESPALDO_AVISO_URL") or "").strip()
    if not url:
        return
    cuerpo = json.dumps({"text": f"Gerayse: fallo el respaldo de asistencia. {mensaje}"}).encode("utf-8")
    pedido = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(pedido, timeout=10):
        pass
