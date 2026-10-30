"""Tramos y jornadas del mes de un empleado (EP-14 US-14.6).

Es la regla de tramos que usan el Excel del mes, la pantalla Por local y el
resumen del mes (D18). Antes vivia en `excel.py`; se separo para que el calculo
del resumen no dependa del armado del Excel. Reglas (epica EP-14):
- las marcas anuladas no cuentan;
- una entrada se cierra con la siguiente salida si pasaron menos de 16 horas
  (la misma regla del fichaje), asi una salida despues de medianoche cuenta en
  el dia de la entrada;
- en el mismo instante va primero la salida: una salida y una entrada cargadas
  a mano a la misma hora parten un tramo en dos;
- las horas del dia suman solo los tramos con entrada y salida; un tramo sin
  salida (o una salida sin entrada) no suma y queda senalado.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

from .fichaje import ENTRADA_ABIERTA_MAXIMA
from .models import Marca


@dataclass
class Tramo:
    entrada: Marca | None
    salida: Marca | None

    @property
    def dia(self) -> date:
        return timezone.localtime((self.entrada or self.salida).momento).date()

    @property
    def horas(self) -> timedelta | None:
        if self.entrada is None or self.salida is None:
            return None
        # A minuto, como se muestran la entrada y la salida: 08:00:50 a 16:00:05
        # tiene que dar 8:00 y no 7:59.
        return a_minuto(self.salida.momento) - a_minuto(self.entrada.momento)

    @property
    def corregido(self) -> bool:
        return any(m is not None and m.origen == Marca.Origen.MANUAL for m in (self.entrada, self.salida))


def ventana_del_mes(anio: int, mes: int) -> tuple[datetime, datetime]:
    """Desde 16 horas antes del primer dia hasta 16 horas despues del ultimo:
    asi entran la salida de madrugada del ultimo dia y se puede descartar la
    salida del dia 1 que cierra una entrada del mes anterior."""
    inicio = timezone.make_aware(datetime(anio, mes, 1))
    siguiente = timezone.make_aware(datetime(anio + (mes == 12), mes % 12 + 1, 1))
    return inicio - ENTRADA_ABIERTA_MAXIMA, siguiente + ENTRADA_ABIERTA_MAXIMA


def orden_de_marca(marca) -> tuple:
    """Por momento y, en el mismo instante, la salida antes que la entrada: asi
    una salida y una entrada cargadas a mano a la misma hora parten un tramo en
    dos, sin importar en que orden se cargaron."""
    return (marca.momento, marca.tipo == Marca.Tipo.ENTRADA, marca.pk)


def armar_tramos(marcas) -> list[Tramo]:
    """`marcas` vigentes (no anuladas) de un empleado; se ordenan aca."""
    tramos, abierta = [], None
    for marca in sorted(marcas, key=orden_de_marca):
        if marca.tipo == Marca.Tipo.ENTRADA:
            if abierta is not None:
                tramos.append(Tramo(abierta, None))
            abierta = marca
            continue
        if abierta is not None and marca.momento - abierta.momento < ENTRADA_ABIERTA_MAXIMA:
            tramos.append(Tramo(abierta, marca))
        else:
            if abierta is not None:
                tramos.append(Tramo(abierta, None))
            tramos.append(Tramo(None, marca))
        abierta = None
    if abierta is not None:
        tramos.append(Tramo(abierta, None))
    return tramos


def jornadas_del_mes(marcas, anio: int, mes: int) -> dict[date, list[Tramo]]:
    dias = {date(anio, mes, d): [] for d in range(1, calendar.monthrange(anio, mes)[1] + 1)}
    for tramo in armar_tramos([m for m in marcas if not m.anulada]):
        if tramo.dia in dias:
            dias[tramo.dia].append(tramo)
    return dias


def horas_del_dia(tramos: list[Tramo]) -> timedelta:
    return sum((t.horas for t in tramos if t.horas is not None), timedelta())


def a_minuto(momento: datetime) -> datetime:
    return momento.replace(second=0, microsecond=0)
