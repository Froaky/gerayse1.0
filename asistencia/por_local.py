"""Asistencia de un dia por local (EP-14 D15): para cada local, quien esta
trabajando ahora, quien ya salio, las horas de cada uno y quien de ese local
no ficho.

Usa la misma regla de tramos que el Excel del mes (excel.armar_tramos): una
entrada se cierra con la siguiente salida si pasaron menos de 16 horas, y el
tramo cuenta en el dia de la entrada. Las marcas anuladas no cuentan. Cada
tramo va al local donde se ficho la entrada (o la salida, si no hubo entrada).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from .excel import armar_tramos
from .fichaje import ENTRADA_ABIERTA_MAXIMA
from .models import Empleado, Marca

NOMBRE_OCULTO = "Empleado de otra empresa"


@dataclass
class FilaEmpleado:
    nombre: str
    empleado_id: int | None  # None si es de una empresa fuera del alcance
    tramos: list = field(default_factory=list)
    horas: timedelta = timedelta()  # tramos cerrados
    en_curso: timedelta = timedelta()  # el tramo abierto de quien esta trabajando
    trabajando_desde: datetime | None = None
    sin_salida: bool = False
    salida_sin_entrada: bool = False
    corregida: bool = False

    @property
    def ultima_salida(self):
        salidas = [t.salida.momento for t in self.tramos if t.salida is not None]
        return max(salidas) if salidas else None

    @property
    def primera_marca(self):
        return min((t.entrada or t.salida).momento for t in self.tramos)

    @property
    def horas_texto(self) -> str:
        return _texto_horas(self.horas)

    @property
    def lleva_texto(self) -> str:
        """Lo trabajado en el dia contando el tramo en curso."""
        return _texto_horas(self.horas + self.en_curso)


@dataclass
class ResumenLocal:
    sucursal: object
    filas: list = field(default_factory=list)
    sin_marcas: list = field(default_factory=list)

    @property
    def trabajando(self) -> int:
        return sum(1 for fila in self.filas if fila.trabajando_desde is not None)


def _a_minuto(momento):
    return momento.replace(second=0, microsecond=0)


def _texto_horas(duracion: timedelta) -> str:
    minutos = max(int(duracion.total_seconds() // 60), 0)
    return f"{minutos // 60}:{minutos % 60:02d}"


def asistencia_del_dia(fecha: date, sucursales, empresas_visibles, ahora=None) -> list[ResumenLocal]:
    """`sucursales`: los locales a mostrar (los del alcance de quien mira).
    `empresas_visibles`: ids de empresas cuyos empleados se pueden nombrar."""
    ahora = ahora or timezone.now()
    sucursales = list(sucursales)
    por_id = {s.pk: s for s in sucursales}
    empresas_visibles = set(empresas_visibles)
    inicio = timezone.make_aware(datetime.combine(fecha, time.min))
    desde = inicio - ENTRADA_ABIERTA_MAXIMA
    hasta = inicio + timedelta(days=1) + ENTRADA_ABIERTA_MAXIMA
    es_hoy = fecha == timezone.localdate(ahora)

    vigentes = Marca.objects.filter(anulada=False, momento__gte=desde, momento__lt=hasta)
    con_marcas_aca = vigentes.filter(sucursal_id__in=por_id).values_list("empleado_id", flat=True).distinct()
    marcas = vigentes.filter(empleado_id__in=con_marcas_aca).select_related("empleado").order_by("empleado_id", "momento", "pk")
    por_empleado = defaultdict(list)
    for marca in marcas:
        por_empleado[marca.empleado_id].append(marca)

    filas = {}  # (sucursal_id, empleado_id) -> FilaEmpleado
    ficharon_el_dia = set()
    for empleado_id, suyas in por_empleado.items():
        empleado = suyas[0].empleado
        for tramo in armar_tramos(suyas):
            if tramo.dia != fecha:
                continue
            ficharon_el_dia.add(empleado_id)
            local_id = (tramo.entrada or tramo.salida).sucursal_id
            if local_id not in por_id:
                continue
            clave = (local_id, empleado_id)
            if clave not in filas:
                visible = empleado.empresa_id in empresas_visibles
                filas[clave] = FilaEmpleado(
                    nombre=empleado.nombre if visible else NOMBRE_OCULTO,
                    empleado_id=empleado.pk if visible else None,
                )
            fila = filas[clave]
            fila.tramos.append(tramo)
            fila.corregida = fila.corregida or tramo.corregido
            if tramo.entrada is None:
                fila.salida_sin_entrada = True
            elif tramo.salida is None:
                if es_hoy and ahora - tramo.entrada.momento < ENTRADA_ABIERTA_MAXIMA:
                    fila.trabajando_desde = tramo.entrada.momento
                    fila.en_curso = _a_minuto(ahora) - _a_minuto(tramo.entrada.momento)
                else:
                    fila.sin_salida = True
            else:
                fila.horas += _a_minuto(tramo.salida.momento) - _a_minuto(tramo.entrada.momento)

    resumenes = {s.pk: ResumenLocal(sucursal=s) for s in sucursales}
    for (local_id, _), fila in filas.items():
        resumenes[local_id].filas.append(fila)
    habituales = Empleado.objects.filter(
        activo=True, sucursal_id__in=por_id, empresa_id__in=empresas_visibles
    ).exclude(pk__in=ficharon_el_dia).order_by("nombre")
    for empleado in habituales:
        resumenes[empleado.sucursal_id].sin_marcas.append(empleado)
    for resumen in resumenes.values():
        # Primero quienes estan trabajando, despues por hora de la primera marca.
        resumen.filas.sort(key=lambda f: (f.trabajando_desde is None, f.primera_marca))
    return [resumenes[s.pk] for s in sucursales]
