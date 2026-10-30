"""Resumen del mes por empleado (EP-14 D18, US-14.11).

Aca vive la cuenta; el Excel solo la muestra. Reglas:
- horas del dia: las de los tramos con entrada y salida (`jornadas.py`);
- horas extra del dia = horas del dia - jornada de la empresa del empleado, si
  da positivo. La jornada es una regla editable (`ReglasAsistencia`);
- dia trabajado: tiene al menos una entrada;
- dia sin salida: tiene una entrada que no se cerro;
- marcas corregidas: las cargadas a mano por administracion.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from .jornadas import Tramo, horas_del_dia, jornadas_del_mes
from .models import Marca, ReglasAsistencia


@dataclass
class DiaResumen:
    fecha: date
    tramos: list[Tramo]
    horas: timedelta
    extra: timedelta

    @property
    def trabajado(self) -> bool:
        return any(t.entrada is not None for t in self.tramos)

    @property
    def sin_salida(self) -> bool:
        return any(t.entrada is not None and t.salida is None for t in self.tramos)

    @property
    def marcas_corregidas(self) -> int:
        return sum(
            1
            for tramo in self.tramos
            for marca in (tramo.entrada, tramo.salida)
            if marca is not None and marca.origen == Marca.Origen.MANUAL
        )


@dataclass
class ResumenMes:
    empleado: object
    reglas: ReglasAsistencia
    dias: list[DiaResumen]

    @property
    def horas(self) -> timedelta:
        return sum((d.horas for d in self.dias), timedelta())

    @property
    def extra(self) -> timedelta:
        return sum((d.extra for d in self.dias), timedelta())

    @property
    def dias_trabajados(self) -> int:
        return sum(1 for d in self.dias if d.trabajado)

    @property
    def dias_sin_salida(self) -> int:
        return sum(1 for d in self.dias if d.sin_salida)

    @property
    def marcas_corregidas(self) -> int:
        return sum(d.marcas_corregidas for d in self.dias)


def calcular_dia(fecha: date, tramos: list[Tramo], reglas: ReglasAsistencia) -> DiaResumen:
    horas = horas_del_dia(tramos)
    return DiaResumen(fecha=fecha, tramos=tramos, horas=horas, extra=max(horas - reglas.jornada, timedelta()))


def calcular_mes(empleado, marcas, anio: int, mes: int, reglas: ReglasAsistencia) -> ResumenMes:
    jornadas = jornadas_del_mes(marcas, anio, mes)
    dias = [calcular_dia(fecha, tramos, reglas) for fecha, tramos in jornadas.items()]
    return ResumenMes(empleado=empleado, reglas=reglas, dias=dias)


def calcular_meses(empleados_y_marcas, anio: int, mes: int) -> list[ResumenMes]:
    """`empleados_y_marcas`: [(empleado, marcas del mes y alrededores)]. Las
    reglas de cada empresa se leen una vez, sin escribir."""
    empleados_y_marcas = list(empleados_y_marcas)
    reglas = ReglasAsistencia.de_empresas({empleado.empresa for empleado, _ in empleados_y_marcas})
    return [
        calcular_mes(empleado, marcas, anio, mes, reglas[empleado.empresa_id])
        for empleado, marcas in empleados_y_marcas
    ]
