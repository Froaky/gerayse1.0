"""Fichaje con el carnet (EP-14 US-14.4).

Reglas (epica EP-14, "Reglas de negocio"):
- la hora es la del servidor al recibir el escaneo;
- si el empleado tiene una entrada abierta de menos de 16 horas, el escaneo es
  su salida; si no, es una entrada nueva (asi una salida despues de medianoche
  cierra la entrada de la noche anterior);
- el mismo carnet dentro de los 2 minutos no genera marca (doble escaneo);
- carnet anulado, codigo desconocido o empleado de baja no generan marca;
- la marca guarda el local del cajero (su sucursal base).
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import CODIGO_CARNET_PATRON, CarnetAnulado, Empleado, Marca
from .permissions import ensure_fichar

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
    # Lo que manda la camara o un lector USB: sin espacios ni saltos, en
    # mayusculas (un lector con Bloq Mayus al reves manda minusculas).
    return "".join(str(texto or "").split()).upper()


@transaction.atomic
def fichar(*, actor, codigo: str) -> Fichaje:
    ensure_fichar(actor)
    sucursal = actor.sucursal_base
    if sucursal is None:
        return Fichaje(Resultado.SIN_LOCAL, "Tu usuario no tiene un local asignado: no se puede fichar.")
    codigo = normalizar_codigo(codigo)
    if not CODIGO_CARNET_PATRON.fullmatch(codigo):
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
    ultima = empleado.marcas.order_by("-momento", "-pk").first()
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
