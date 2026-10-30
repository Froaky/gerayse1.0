"""Resumen del mes por empleado (EP-14 D18, US-14.11 y US-14.14).

Aca vive la cuenta; el Excel solo la muestra. Reglas:
- horas del dia: las de los tramos con entrada y salida (`jornadas.py`);
- horas extra del dia = horas del dia - jornada de la empresa del empleado, si
  da positivo. La jornada es una regla editable (`ReglasAsistencia`);
- dia trabajado: tiene al menos una entrada;
- dia sin salida: tiene una entrada que no se cerro;
- marcas corregidas: las cargadas a mano por administracion;
- llegada tarde: la entrada N del dia (a minuto) despues de la entrada N del
  horario (`Horario`); se suman los minutos del mes y se marca si pasan la
  tolerancia de la empresa. Si tenia dos entradas y fichó una, la segunda no
  suma tarde: queda senalada;
- falta: dia con horario (no franco) sin ninguna marca. Solo hasta ayer: hoy
  todavia puede llegar;
- dia sin horario: no cuenta como falta ni tardanza. Se cuentan los de hasta
  ayer, desde el alta del empleado y hasta su baja, para ver que local no cargo.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

from .jornadas import Tramo, a_minuto, horas_del_dia, jornadas_del_mes
from .models import Horario, Marca, ReglasAsistencia


@dataclass
class DiaResumen:
    fecha: date
    tramos: list[Tramo]
    horas: timedelta
    extra: timedelta
    horario: Horario | None = None
    tarde: timedelta = timedelta()
    # El dia ya paso y el empleado estaba dado de alta: cuenta para faltas y dias sin horario.
    cuenta: bool = False

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

    @property
    def franco(self) -> bool:
        return self.horario is not None and self.horario.franco

    @property
    def falta(self) -> bool:
        return self.cuenta and self.horario is not None and not self.horario.franco and not self.tramos

    @property
    def sin_horario(self) -> bool:
        return self.cuenta and self.horario is None

    @property
    def turno_sin_fichar(self) -> bool:
        """Tenia dos entradas y vino a una sola."""
        if self.horario is None or self.franco or not self.trabajado:
            return False
        return len(self.horario.entradas) > sum(1 for t in self.tramos if t.entrada is not None)


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

    @property
    def llegadas_tarde(self) -> int:
        return sum(1 for d in self.dias if d.tarde)

    @property
    def minutos_tarde(self) -> int:
        return int(sum((d.tarde for d in self.dias), timedelta()).total_seconds() // 60)

    @property
    def pasa_la_tolerancia(self) -> bool:
        return self.minutos_tarde > self.reglas.tolerancia_mensual_minutos

    @property
    def faltas(self) -> int:
        return sum(1 for d in self.dias if d.falta)

    @property
    def dias_sin_horario(self) -> int:
        return sum(1 for d in self.dias if d.sin_horario)


def _tarde(fecha: date, tramos: list[Tramo], horario: Horario | None) -> timedelta:
    if horario is None or horario.franco:
        return timedelta()
    reales = [t.entrada for t in tramos if t.entrada is not None]
    total = timedelta()
    for programada, real in zip(horario.entradas, reales):
        esperada = timezone.make_aware(datetime.combine(fecha, programada))
        total += max(a_minuto(timezone.localtime(real.momento)) - esperada, timedelta())
    return total


def _dias_que_cuentan(empleado, hoy: date) -> tuple[date, date]:
    """Desde el alta hasta ayer (o hasta el dia de la baja)."""
    desde = timezone.localdate(empleado.creado_en) if empleado.creado_en else date.min
    hasta = hoy - timedelta(days=1)
    if not empleado.activo and empleado.dado_de_baja_en:
        hasta = min(hasta, timezone.localdate(empleado.dado_de_baja_en))
    return desde, hasta


def calcular_dia(fecha: date, tramos: list[Tramo], reglas: ReglasAsistencia, horario=None, cuenta=False) -> DiaResumen:
    horas = horas_del_dia(tramos)
    return DiaResumen(
        fecha=fecha,
        tramos=tramos,
        horas=horas,
        extra=max(horas - reglas.jornada, timedelta()),
        horario=horario,
        tarde=_tarde(fecha, tramos, horario),
        cuenta=cuenta,
    )


def calcular_mes(empleado, marcas, anio: int, mes: int, reglas: ReglasAsistencia, horarios=None, hoy=None) -> ResumenMes:
    """`horarios`: {fecha: Horario} del empleado en el mes."""
    horarios = horarios or {}
    desde, hasta = _dias_que_cuentan(empleado, hoy or timezone.localdate())
    dias = [
        calcular_dia(fecha, tramos, reglas, horarios.get(fecha), cuenta=desde <= fecha <= hasta)
        for fecha, tramos in jornadas_del_mes(marcas, anio, mes).items()
    ]
    return ResumenMes(empleado=empleado, reglas=reglas, dias=dias)


def calcular_meses(empleados_y_marcas, anio: int, mes: int, hoy=None) -> list[ResumenMes]:
    """`empleados_y_marcas`: [(empleado, marcas del mes y alrededores)]. Las
    reglas de cada empresa y los horarios del mes se leen una vez, sin escribir."""
    empleados_y_marcas = list(empleados_y_marcas)
    reglas = ReglasAsistencia.de_empresas({empleado.empresa for empleado, _ in empleados_y_marcas})
    primero = date(anio, mes, 1)
    siguiente = date(anio + (mes == 12), mes % 12 + 1, 1)
    horarios = {}
    for horario in Horario.objects.filter(
        empleado__in=[empleado.pk for empleado, _ in empleados_y_marcas], fecha__gte=primero, fecha__lt=siguiente
    ):
        horarios.setdefault(horario.empleado_id, {})[horario.fecha] = horario
    return [
        calcular_mes(empleado, marcas, anio, mes, reglas[empleado.empresa_id], horarios.get(empleado.pk), hoy)
        for empleado, marcas in empleados_y_marcas
    ]
