"""Resumen del mes por empleado (EP-14 D18, US-14.11 y US-14.14).

Aca vive la cuenta; el Excel solo la muestra. Reglas:
- horas del dia: las de los tramos con entrada y salida (`jornadas.py`);
- horas extra del dia = horas del dia - jornada de la empresa del empleado, si
  da positivo. La jornada es una regla editable (`ReglasAsistencia`);
- dia trabajado: tiene al menos una entrada;
- dia sin salida: un dia que ya paso con una entrada que no se cerro (hoy
  todavia puede estar trabajando);
- marcas corregidas: las cargadas a mano por administracion;
- llegada tarde: cada entrada del horario (`Horario`, una o dos con turno
  cortado) toma la primera entrada fichada dentro de su franja, que va desde 2
  horas antes de la hora hasta 8 horas despues (sin pasar de las 5 de la mañana
  del dia siguiente), y corta a mitad de camino con la entrada esperada vecina
  (la del otro turno, o la del dia de al lado). Llega tarde si fichó despues de
  la hora (a minuto). Asi quien viene solo al 2.º turno se compara con el 2.º,
  un turno de las 0:30 toma al que llego a las 23:55 del dia anterior, y una
  entrada de la noche anterior o de la mañana siguiente no tapa ni la tardanza
  ni la falta de otro turno. Cada entrada tarde es una llegada tarde; se suman
  los minutos del mes y se marca si pasan la tolerancia de la empresa;
- llego antes y se quedo: un turno sin entrada en su franja, a cuya hora estaba
  trabajando en un tramo cerrado que no empezo con la entrada de otro turno
  (llego mas de 2 horas antes, aunque sea el dia anterior). No es tarde, ni se
  senala, ni es falta. Un tramo que empezo con la entrada de otro turno (doble
  turno de corrido) no cuenta: sus marcas son las mismas que las de una salida
  olvidada, asi que se senala para que se revise;
- turno sin fichar: un dia que ya paso en que fichó una entrada propia, pero a
  ese turno no le toco ninguna ni llego antes y se quedo; se senala, no es falta;
- falta: dia con horario (no franco) en que no fichó nada propio (una entrada
  que le toco a un turno de otro dia no cuenta) y ningun turno tuvo entrada ni
  lo encontro trabajando. Solo hasta ayer: hoy todavia puede llegar. El dia de
  la baja cuentan solo los turnos que empezaron antes de la baja;
- dia sin horario: no cuenta como falta ni tardanza. Se cuentan los de hasta
  ayer, desde el alta del empleado y hasta su baja, para ver que local no cargo.

Limites conocidos:
- "ayer" se corta a medianoche. Un Excel bajado de madrugada puede mostrar el
  turno de noche de ayer como falta o sin salida aunque todavia este dentro de
  su franja; bajado mas tarde, se corrige solo;
- el ultimo dia del mes depende del horario del dia 1 del siguiente (el corte a
  mitad de camino): si despues se carga o se corrige el dia 1, el Excel del mes
  puede cambiar y hay que volver a bajarlo;
- un doble turno de corrido se senala ("Falta" o "No fichó"); para que cuente,
  en Marcas se parte el tramo con una salida y una entrada a la hora del
  segundo turno.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from .jornadas import Tramo, a_minuto, armar_tramos, horas_del_dia, jornadas_del_mes
from .models import Horario, Marca, ReglasAsistencia

# Franja de cada entrada del horario: cuanto antes de la hora puede llegar (y
# contar para ese turno) y hasta cuanto despues sigue siendo una llegada tarde.
ANTES_DE_HORA = timedelta(hours=2)
DESPUES_DE_HORA = timedelta(hours=8)
# Un turno que empieza antes de medianoche no toma entradas de la mañana
# siguiente: su franja no pasa de esta hora del dia siguiente al de su horario.
# Quien falto a la noche y entra temprano al otro dia tiene una falta, no una
# llegada tarde de siete horas. Un turno cargado pasada la medianoche (0:30) ya
# es de ese dia y tiene sus 8 horas.
FIN_DE_LA_NOCHE = time(5)


def _minuto(marca) -> datetime:
    return a_minuto(timezone.localtime(marca.momento))


def _cubre(tramo: Tramo, momento: datetime) -> bool:
    """Si a esa hora estaba trabajando: un tramo cerrado que la contiene."""
    return (
        tramo.entrada is not None
        and tramo.salida is not None
        and _minuto(tramo.entrada) <= momento < _minuto(tramo.salida)
    )


@dataclass
class Turno:
    """Una entrada del horario y la entrada fichada que le corresponde."""

    esperada: datetime
    real: datetime | None = None
    # Sin entrada en su franja pero llego antes y se quedo: la entrada (a minuto)
    # del tramo en que estaba trabajando a esa hora.
    cubierto: datetime | None = None

    @property
    def tarde(self) -> timedelta:
        if self.real is None:
            return timedelta()
        return max(self.real - self.esperada, timedelta())


@dataclass
class DiaResumen:
    fecha: date
    tramos: list[Tramo]
    horas: timedelta
    extra: timedelta
    horario: Horario | None = None
    turnos: list[Turno] = field(default_factory=list)
    # El dia ya termino (es anterior a hoy).
    pasado: bool = False
    # Ademas el empleado estaba dado de alta: cuenta para faltas y dias sin horario.
    cuenta: bool = False
    # Entradas de esta fila que le tocaron al turno de otro dia: {entrada (a minuto): dia de ese turno}.
    ajenas: dict = field(default_factory=dict)

    def _ajena(self, tramo: Tramo) -> bool:
        return tramo.entrada is not None and _minuto(tramo.entrada) in self.ajenas

    @property
    def entrada_propia(self) -> bool:
        """Fichó una entrada que no es la del turno de otro dia."""
        return any(t.entrada is not None and not self._ajena(t) for t in self.tramos)

    @property
    def fichajes_propios(self) -> bool:
        """Una entrada propia, o una salida suelta (su entrada se anulo)."""
        return self.entrada_propia or any(t.entrada is None for t in self.tramos)

    @property
    def trabajado(self) -> bool:
        return any(t.entrada is not None for t in self.tramos)

    @property
    def sin_salida(self) -> bool:
        return self.pasado and any(t.entrada is not None and t.salida is None for t in self.tramos)

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
    def tarde(self) -> timedelta:
        return sum((t.tarde for t in self.turnos), timedelta())

    @property
    def llegadas_tarde(self) -> int:
        return sum(1 for t in self.turnos if t.tarde)

    @property
    def vino(self) -> bool:
        return self.fichajes_propios or any(t.real is not None or t.cubierto is not None for t in self.turnos)

    @property
    def falta(self) -> bool:
        # Sin turnos no hay falta: un franco no tiene, y el dia de la baja solo
        # quedan los que empezaron antes de la baja.
        return self.cuenta and bool(self.turnos) and not self.vino

    @property
    def sin_horario(self) -> bool:
        return self.cuenta and self.horario is None

    @property
    def turnos_sin_fichar(self) -> list[int]:
        """Numeros (1 o 2) de los turnos sin entrada, en un dia que ya paso y en
        el que fichó alguna entrada propia: si no vino, es falta; si solo hay una
        salida suelta o la entrada de otro dia, eso ya se senala aparte. Un turno
        al que llego antes y se quedo no cuenta."""
        if not self.pasado or not self.entrada_propia:
            return []
        return [
            numero
            for numero, turno in enumerate(self.turnos, start=1)
            if turno.real is None and turno.cubierto is None
        ]


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
        return sum(d.llegadas_tarde for d in self.dias)

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


def _turnos_del_horario(horarios: dict) -> list[tuple[datetime, date]]:
    """Todas las entradas esperadas, en orden: (momento, dia del horario)."""
    esperadas = []
    for fecha, horario in horarios.items():
        if horario.franco:
            continue
        for hora in horario.entradas:
            esperadas.append((timezone.make_aware(datetime.combine(fecha, hora)), fecha))
    esperadas.sort()
    return esperadas


def asignar_turnos(horarios: dict, entradas: list[datetime]) -> dict[date, list[Turno]]:
    """A cada entrada del horario le toca la primera entrada fichada (ya a minuto
    y en orden) de su franja: de `ANTES_DE_HORA` antes a `DESPUES_DE_HORA`
    despues (sin pasar de `FIN_DE_LA_NOCHE` del dia siguiente), cortando a mitad
    de camino con la entrada esperada vecina. Las franjas no se pisan, asi que
    una entrada fichada le toca a un solo turno."""
    esperadas = _turnos_del_horario(horarios)
    turnos = {}
    for indice, (esperada, fecha) in enumerate(esperadas):
        fin_de_la_noche = timezone.make_aware(datetime.combine(fecha + timedelta(days=1), FIN_DE_LA_NOCHE))
        desde, hasta = esperada - ANTES_DE_HORA, min(esperada + DESPUES_DE_HORA, fin_de_la_noche)
        if indice > 0:
            anterior = esperadas[indice - 1][0]
            desde = max(desde, anterior + (esperada - anterior) / 2)
        if indice + 1 < len(esperadas):
            siguiente = esperadas[indice + 1][0]
            hasta = min(hasta, esperada + (siguiente - esperada) / 2)
        real = next((entrada for entrada in entradas if desde <= entrada < hasta), None)
        turnos.setdefault(fecha, []).append(Turno(esperada=esperada, real=real))
    return turnos


def _antes_de_la_baja(empleado, turnos: dict) -> None:
    """El dia de la baja quedan solo los turnos que empezaron antes de la baja
    (o con entrada, por una marca cargada a mano): a los de despues ya no podia
    venir, el carnet le da "de baja"."""
    if empleado.activo or not empleado.dado_de_baja_en:
        return
    baja = timezone.localtime(empleado.dado_de_baja_en)
    if baja.date() in turnos:
        turnos[baja.date()] = [t for t in turnos[baja.date()] if t.esperada < baja or t.real is not None]


def _cubrir(turnos: dict, tramos: list[Tramo]) -> None:
    """A cada turno sin entrada en su franja le busca un tramo cerrado abierto a
    su hora que no haya empezado con la entrada de otro turno: llego antes y se
    quedo (puede venir del dia anterior). Un tramo que empezo con la entrada de
    otro turno no cubre: un doble turno de corrido tiene las mismas marcas que
    una salida olvidada, y eso se senala."""
    reales = {t.real for lista in turnos.values() for t in lista if t.real is not None}
    libres = [t for t in tramos if t.entrada is not None and t.salida is not None and _minuto(t.entrada) not in reales]
    for lista in turnos.values():
        for turno in lista:
            if turno.real is None:
                turno.cubierto = next((_minuto(t.entrada) for t in libres if _cubre(t, turno.esperada)), None)


def _ajenas(fecha: date, tramos: list[Tramo], dia_del_turno: dict) -> dict:
    """Las entradas de la fila que le tocaron al turno de otro dia (por ejemplo,
    la de las 23:55 para el turno de las 0:30), con el dia de ese turno."""
    ajenas = {}
    for tramo in tramos:
        if tramo.entrada is not None:
            minuto = _minuto(tramo.entrada)
            otro = dia_del_turno.get(minuto)
            if otro is not None and otro != fecha:
                ajenas[minuto] = otro
    return ajenas


def _dias_que_cuentan(empleado, hoy: date) -> tuple[date, date]:
    """Desde el alta hasta ayer (o hasta el dia de la baja; ese dia, solo los
    turnos de antes de la baja: `_antes_de_la_baja`)."""
    desde = timezone.localdate(empleado.creado_en) if empleado.creado_en else date.min
    hasta = hoy - timedelta(days=1)
    if not empleado.activo and empleado.dado_de_baja_en:
        hasta = min(hasta, timezone.localdate(empleado.dado_de_baja_en))
    return desde, hasta


def calcular_dia(fecha: date, tramos: list[Tramo], reglas: ReglasAsistencia, horario=None, turnos=None, *,
                 pasado=False, cuenta=False, ajenas=None) -> DiaResumen:
    horas = horas_del_dia(tramos)
    return DiaResumen(
        fecha=fecha,
        tramos=tramos,
        horas=horas,
        extra=max(horas - reglas.jornada, timedelta()),
        horario=horario,
        turnos=turnos or [],
        pasado=pasado,
        cuenta=cuenta,
        ajenas=ajenas or {},
    )


def calcular_mes(empleado, marcas, anio: int, mes: int, reglas: ReglasAsistencia, horarios=None, hoy=None) -> ResumenMes:
    """`horarios`: {fecha: Horario} del empleado en el mes y, si hay, el dia de
    antes y el de despues (para las franjas que cruzan la medianoche)."""
    horarios = horarios or {}
    hoy = hoy or timezone.localdate()
    desde, hasta = _dias_que_cuentan(empleado, hoy)
    vigentes = [m for m in marcas if not m.anulada]
    entradas = sorted(_minuto(m) for m in vigentes if m.tipo == Marca.Tipo.ENTRADA)
    turnos = asignar_turnos(horarios, entradas)
    _antes_de_la_baja(empleado, turnos)
    # Con todas las marcas, no solo las del mes: un tramo del ultimo dia del mes
    # anterior puede cubrir un turno del dia 1.
    _cubrir(turnos, armar_tramos(vigentes))
    # La entrada que tomo cada turno, o la del tramo en que estaba trabajando: si
    # quedo en la fila de otro dia, en esa fila es "ajena". Si una cubre dos
    # turnos, queda con el primero.
    dia_del_turno = {}
    for fecha in sorted(turnos):
        for turno in turnos[fecha]:
            for entrada in (turno.real, turno.cubierto):
                if entrada is not None:
                    dia_del_turno.setdefault(entrada, fecha)
    dias = [
        calcular_dia(
            fecha,
            tramos,
            reglas,
            horarios.get(fecha),
            turnos.get(fecha),
            pasado=fecha < hoy,
            cuenta=desde <= fecha <= hasta,
            ajenas=_ajenas(fecha, tramos, dia_del_turno),
        )
        for fecha, tramos in jornadas_del_mes(marcas, anio, mes).items()
    ]
    return ResumenMes(empleado=empleado, reglas=reglas, dias=dias)


def calcular_meses(empleados_y_marcas, anio: int, mes: int, hoy=None) -> list[ResumenMes]:
    """`empleados_y_marcas`: [(empleado, marcas del mes y alrededores)]. Las
    reglas de cada empresa y los horarios del mes (con el dia de antes y el de
    despues) se leen una vez, sin escribir."""
    empleados_y_marcas = list(empleados_y_marcas)
    reglas = ReglasAsistencia.de_empresas({empleado.empresa for empleado, _ in empleados_y_marcas})
    desde = date(anio, mes, 1) - timedelta(days=1)
    hasta = date(anio + (mes == 12), mes % 12 + 1, 1)
    horarios = {}
    for horario in Horario.objects.filter(
        empleado__in=[empleado.pk for empleado, _ in empleados_y_marcas], fecha__gte=desde, fecha__lte=hasta
    ).order_by():
        horarios.setdefault(horario.empleado_id, {})[horario.fecha] = horario
    return [
        calcular_mes(empleado, marcas, anio, mes, reglas[empleado.empresa_id], horarios.get(empleado.pk), hoy)
        for empleado, marcas in empleados_y_marcas
    ]
