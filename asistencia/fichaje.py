"""Fichaje con el carnet (EP-14 US-14.4).

Reglas (epica EP-14, "Reglas de negocio"):
- la hora es la del servidor al recibir el escaneo;
- si el empleado tiene una entrada abierta de menos de 16 horas, el escaneo es
  su salida; si no, es una entrada nueva (asi una salida despues de medianoche
  cierra la entrada de la noche anterior);
- el mismo carnet dentro de los 2 minutos no genera marca (doble escaneo);
- carnet anulado, codigo desconocido o empleado de baja no generan marca;
- la marca guarda el local del cajero (su sucursal base). Quien administra
  asistencia puede fichar eligiendo el local (D11): por ejemplo un admin sin
  sucursal base. Un cajero con otros locales asignados en su ficha elige entre
  los suyos (D14).
"""

import re
from dataclasses import dataclass
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .models import CODIGO_CARNET_ANTERIOR, CODIGO_CARNET_PATRON, CarnetAnulado, Empleado, Marca
from .permissions import can_administrar, ensure_fichar

ENTRADA_ABIERTA_MAXIMA = timedelta(hours=16)
DOBLE_ESCANEO = timedelta(minutes=2)


class Resultado:
    REGISTRADA = "registrada"
    REPETIDA = "repetida"
    ANULADO = "anulado"
    DESCONOCIDO = "desconocido"
    DE_BAJA = "de_baja"
    SIN_LOCAL = "sin_local"


@dataclass
class Fichaje:
    resultado: str
    mensaje: str
    empleado: Empleado | None = None
    marca: Marca | None = None

    @property
    def registrada(self) -> bool:
        return self.resultado == Resultado.REGISTRADA


def normalizar_codigo(texto) -> str:
    # Lo que manda la camara o un lector USB, o lo que se tipea: sin espacios,
    # saltos, puntos ni guiones ("483 920", "483-920"), en mayusculas (un lector
    # con Bloq Mayus al reves manda minusculas en los codigos anteriores).
    return re.sub(r"[\s.\-]", "", str(texto or "")).upper()


def _ensure_local_elegible(actor, sucursal) -> None:
    # La vista solo ofrece locales del alcance; esto es la segunda linea: un
    # cajero solo puede mandar uno de sus locales asignados, y nadie uno
    # inactivo o de una empresa que no tiene permitida.
    if not sucursal.activa or not actor.empresas_permitidas.filter(pk=sucursal.empresa_id).exists():
        raise PermissionDenied("Ese local no está entre los de tus empresas.")
    if can_administrar(actor):
        return
    if not actor.locales_de_fichaje().filter(pk=sucursal.pk).exists():
        raise PermissionDenied("Ese local no está entre los tuyos para fichar.")


@transaction.atomic
def fichar(*, actor, codigo: str, sucursal=None) -> Fichaje:
    """`sucursal`: el local elegido por quien administra; si no viene, la
    sucursal base del usuario."""
    ensure_fichar(actor)
    if sucursal is not None and sucursal.pk != actor.sucursal_base_id:
        _ensure_local_elegible(actor, sucursal)
    sucursal = sucursal or actor.sucursal_base
    if sucursal is None:
        return Fichaje(Resultado.SIN_LOCAL, "Tu usuario no tiene un local asignado: no se puede fichar.")
    codigo = normalizar_codigo(codigo)
    # Un codigo con el formato anterior se busca igual: si es de un carnet que
    # quedo anulado, el aviso dice que use el carnet nuevo.
    if not (CODIGO_CARNET_PATRON.fullmatch(codigo) or CODIGO_CARNET_ANTERIOR.fullmatch(codigo)):
        return Fichaje(Resultado.DESCONOCIDO, "Ese QR no es un carnet de asistencia.")
    # El lock serializa dos escaneos del mismo carnet que llegan juntos: el
    # segundo ve la marca del primero y cae en el doble escaneo.
    empleado = (
        Empleado.objects.select_for_update(of=("self",))
        .select_related("empresa", "sucursal")
        .filter(codigo_carnet=codigo)
        .first()
    )
    if empleado is None:
        anulado = CarnetAnulado.objects.select_related("empleado").filter(codigo=codigo).first()
        if anulado is not None:
            return Fichaje(
                Resultado.ANULADO,
                f"Ese carnet de {anulado.empleado.nombre} está anulado. Tiene que usar el carnet nuevo.",
                empleado=anulado.empleado,
            )
        return Fichaje(Resultado.DESCONOCIDO, "Ese carnet no existe en el padrón.")
    if not empleado.activo:
        return Fichaje(Resultado.DE_BAJA, f"{empleado.nombre} está dado de baja: no puede fichar.", empleado=empleado)

    ahora = timezone.now()
    # Una marca anulada no cuenta para nada: ni para alternar ni como doble escaneo.
    # En el mismo instante la ultima es la entrada, como en los tramos
    # (jornadas.orden_de_marca): una salida y una entrada cargadas a mano a la
    # misma hora dejan abierta la entrada.
    ultima = empleado.marcas.filter(anulada=False).order_by("-momento", "tipo", "-pk").first()
    if ultima is not None and ahora - ultima.momento < DOBLE_ESCANEO:
        segundos = max(int((ahora - ultima.momento).total_seconds()), 0)
        return Fichaje(
            Resultado.REPETIDA,
            f"Ya quedó registrada la {ultima.get_tipo_display().lower()} de {empleado.nombre} hace {segundos} segundos.",
            empleado=empleado,
            marca=ultima,
        )
    if ultima is not None and ultima.tipo == Marca.Tipo.ENTRADA and ahora - ultima.momento < ENTRADA_ABIERTA_MAXIMA:
        tipo = Marca.Tipo.SALIDA
    else:
        tipo = Marca.Tipo.ENTRADA
    marca = Marca.objects.create(
        empleado=empleado,
        tipo=tipo,
        momento=ahora,
        sucursal=sucursal,
        origen=Marca.Origen.ESCANEO,
        registrada_por=actor,
    )
    hora = timezone.localtime(ahora).strftime("%H:%M")
    return Fichaje(Resultado.REGISTRADA, f"{marca.get_tipo_display()} registrada a las {hora}.", empleado=empleado, marca=marca)
