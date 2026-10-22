"""Excel del mes por empleado (EP-14 US-14.6).

Una hoja por empleado con todos los dias del mes: entradas y salidas en pares,
horas del dia y una observacion. Reglas (epica EP-14):
- las marcas anuladas no cuentan;
- una entrada se cierra con la siguiente salida si pasaron menos de 16 horas
  (la misma regla del fichaje), asi una salida despues de medianoche cuenta en
  el dia de la entrada;
- las horas del dia suman solo los tramos con entrada y salida; un tramo sin
  salida (o una salida sin entrada) no suma y queda senalado;
- una marca cargada a mano sale como "Corregida".
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .fichaje import ENTRADA_ABIERTA_MAXIMA
from .models import Marca

MESES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
        return _a_minuto(self.salida.momento) - _a_minuto(self.entrada.momento)

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


def armar_tramos(marcas) -> list[Tramo]:
    """`marcas` vigentes (no anuladas) de un empleado, en orden de momento."""
    tramos, abierta = [], None
    for marca in marcas:
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
    vigentes = sorted((m for m in marcas if not m.anulada), key=lambda m: (m.momento, m.pk))
    for tramo in armar_tramos(vigentes):
        if tramo.dia in dias:
            dias[tramo.dia].append(tramo)
    return dias


def observacion(tramos: list[Tramo]) -> str:
    if not tramos:
        return "Sin marcas"
    notas = []
    if any(t.corregido for t in tramos):
        notas.append("Corregida")
    if any(t.entrada is not None and t.salida is None for t in tramos):
        notas.append("Sin salida")
    if any(t.entrada is None for t in tramos):
        notas.append("Salida sin entrada")
    return ", ".join(notas)


def horas_del_dia(tramos: list[Tramo]) -> timedelta:
    return sum((t.horas for t in tramos if t.horas is not None), timedelta())


def _a_minuto(momento: datetime) -> datetime:
    return momento.replace(second=0, microsecond=0)


def _recortar(texto: str, maximo: int) -> str:
    # Excel cuenta el largo en unidades UTF-16: un emoji ocupa dos. Tampoco
    # acepta un apostrofo al principio o al final.
    while len(texto.encode("utf-16-le")) // 2 > maximo:
        texto = texto[:-1]
    return texto.strip(" '")


def _titulo_hoja(nombre: str, usados: set) -> str:
    # Excel: hasta 31 caracteres, sin []:*?/\ y sin repetir. Un nombre de hoja
    # invalido hace que Excel no abra el libro entero.
    base = _recortar(re.sub(r"[\[\]:*?/\\]", " ", ILLEGAL_CHARACTERS_RE.sub("", nombre)).strip(" '"), 31) or "Empleado"
    titulo, numero = base, 2
    while titulo.lower() in usados:
        sufijo = f" ({numero})"
        titulo = (_recortar(base, 31 - len(sufijo)) or "Empleado") + sufijo
        numero += 1
    usados.add(titulo.lower())
    return titulo


def _texto(hoja, coordenada: str, valor: str):
    # Un nombre que empieza con "=" no tiene que volverse una formula.
    celda = hoja[coordenada]
    celda.value = ILLEGAL_CHARACTERS_RE.sub("", valor)
    celda.data_type = "s"
    return celda


def _hora(marca) -> object:
    return timezone.localtime(marca.momento).time().replace(second=0, microsecond=0) if marca else None


def generar_excel(empleados_y_marcas, anio: int, mes: int) -> bytes:
    """`empleados_y_marcas`: [(empleado, marcas del mes y alrededores)]."""
    libro = Workbook()
    libro.remove(libro.active)
    usados = set()
    negrita = Font(bold=True)
    encabezado = PatternFill("solid", fgColor="1B3A2E")
    for empleado, marcas in empleados_y_marcas:
        hoja = libro.create_sheet(_titulo_hoja(empleado.nombre, usados))
        jornadas = jornadas_del_mes(marcas, anio, mes)
        pares = max([2, *(len(tramos) for tramos in jornadas.values())])
        _texto(hoja, "A1", empleado.nombre).font = Font(bold=True, size=14)
        local = empleado.sucursal.nombre if empleado.sucursal.empresa_id == empleado.empresa_id else "local de otra empresa"
        _texto(hoja, "A2", f"{empleado.empresa.nombre} · {local}" + ("" if empleado.activo else " · dado de baja"))
        hoja["A3"] = f"Asistencia de {MESES[mes]} de {anio}"

        titulos = ["Día"]
        for numero in range(1, pares + 1):
            titulos += [f"Entrada {numero}", f"Salida {numero}"]
        titulos += ["Horas del día", "Observación"]
        for columna, titulo in enumerate(titulos, start=1):
            celda = hoja.cell(row=5, column=columna, value=titulo)
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = encabezado
            celda.alignment = Alignment(horizontal="center")

        total = timedelta()
        fila = 6
        for dia, tramos in jornadas.items():
            hoja.cell(row=fila, column=1, value=dia).number_format = "ddd dd/mm"
            for indice, tramo in enumerate(tramos):
                for desplazamiento, marca in ((0, tramo.entrada), (1, tramo.salida)):
                    celda = hoja.cell(row=fila, column=2 + indice * 2 + desplazamiento, value=_hora(marca))
                    celda.number_format = "hh:mm"
                    celda.alignment = Alignment(horizontal="center")
            horas = horas_del_dia(tramos)
            total += horas
            celda = hoja.cell(row=fila, column=2 + pares * 2, value=horas if tramos else None)
            celda.number_format = "[h]:mm"
            celda.alignment = Alignment(horizontal="center")
            hoja.cell(row=fila, column=3 + pares * 2, value=observacion(tramos))
            fila += 1
        hoja.cell(row=fila, column=1, value="Total del mes").font = negrita
        celda = hoja.cell(row=fila, column=2 + pares * 2, value=total)
        celda.number_format = "[h]:mm"
        celda.font = negrita
        celda.alignment = Alignment(horizontal="center")

        hoja.column_dimensions["A"].width = 14
        for columna in range(2, 2 + pares * 2):
            hoja.column_dimensions[get_column_letter(columna)].width = 11
        hoja.column_dimensions[get_column_letter(2 + pares * 2)].width = 14
        hoja.column_dimensions[get_column_letter(3 + pares * 2)].width = 28
        hoja.freeze_panes = "B6"
    if not libro.sheetnames:
        libro.create_sheet("Sin empleados")["A1"] = "No hay empleados para ese mes."
    buffer = BytesIO()
    libro.save(buffer)
    return buffer.getvalue()
