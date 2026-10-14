"""Renderer generico de reportes PDF con ReportLab.

No conoce el dominio: recibe `ReportDocument`s ya armados y los dibuja en A4
con la identidad de Gerayse (la misma del presupuesto del modulo): Public Sans
para el cuerpo, Fraunces para los titulos y Spline Sans Mono para las cifras de
las tarjetas. Cada pagina lleva el encabezado del documento que la ocupa (razon
social, titulo, CUIT, alcance, periodo y emision) y al pie "Pagina X de Y".
Varios documentos en un mismo archivo (general + cada sucursal) arrancan cada
uno en pagina nueva con su propio encabezado.
"""

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    HRFlowable,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from treasury.services import formato_money

from .documento import Kpi, NoteBlock, Pct, ReportDocument, TableBlock, Value

# Tipografias del manual de identidad, embebidas (ver fuentes/README.md).
FONT_DIR = Path(__file__).resolve().parent / "fuentes"
BODY_FONT = "GerayseSans"
SEMIBOLD_FONT = "GerayseSans-SemiBold"
BOLD_FONT = "GerayseSans-Bold"
TITLE_FONT = "GerayseSerif-SemiBold"
MONO_BOLD_FONT = "GerayseMono-SemiBold"
_FUENTES = {
    BODY_FONT: "PublicSans-Regular.ttf",
    SEMIBOLD_FONT: "PublicSans-SemiBold.ttf",
    BOLD_FONT: "PublicSans-Bold.ttf",
    TITLE_FONT: "Fraunces-SemiBold.ttf",
    MONO_BOLD_FONT: "SplineSansMono-SemiBold.ttf",
}


def _registrar_fuentes():
    registradas = set(pdfmetrics.getRegisteredFontNames())
    for nombre, archivo in _FUENTES.items():
        if nombre not in registradas:
            pdfmetrics.registerFont(TTFont(nombre, str(FONT_DIR / archivo)))
    # <b> dentro de un Paragraph usa la negrita de la familia.
    pdfmetrics.registerFontFamily(BODY_FONT, normal=BODY_FONT, bold=BOLD_FONT, italic=BODY_FONT, boldItalic=BOLD_FONT)


_registrar_fuentes()

INK = colors.HexColor("#1b3a2e")
GREEN = colors.HexColor("#2f6b52")
TEXT = colors.HexColor("#23282b")
MUTED = colors.HexColor("#6b757a")
LINE = colors.HexColor("#dfe4e2")
LINE_STRONG = colors.HexColor("#c9d2ce")
PANEL = colors.HexColor("#f7f9f8")
TOTAL_BG = colors.HexColor("#e4f1e9")
GROUP_BG = colors.HexColor("#f0f3f1")

MARGIN_X = 16 * mm
HEADER_TOP = 10 * mm
MARGIN_TOP = 36 * mm
MARGIN_BOTTOM = 16 * mm
CELL_SIZE = 7.5
# Las cifras de una tabla se achican hasta que entre la mas ancha, sin bajar de aca.
MIN_CELL_SIZE = 6
HEAD_SIZE = 7.5
CELL_PADDING = 4
# Relleno de arriba y de abajo de cada fila; tiene que ser >= CELL_SIZE - MIN_CELL_SIZE.
ROW_PADDING = 3
KPI_GAP = 3.5 * mm

_SECTION_STYLE = ParagraphStyle(
    "reporte-seccion", fontName=TITLE_FONT, fontSize=12, leading=15, textColor=INK, spaceBefore=8, spaceAfter=2
)
_CELL_STYLE = ParagraphStyle(
    "reporte-celda", fontName=BODY_FONT, fontSize=CELL_SIZE, leading=CELL_SIZE + 2, textColor=TEXT
)
_CELL_BOLD_STYLE = ParagraphStyle("reporte-celda-total", parent=_CELL_STYLE, fontName=BOLD_FONT)
_HEAD_STYLE = ParagraphStyle(
    "reporte-cabecera", fontName=SEMIBOLD_FONT, fontSize=HEAD_SIZE, leading=HEAD_SIZE + 2, textColor=colors.white
)
_HEAD_RIGHT_STYLE = ParagraphStyle("reporte-cabecera-numero", parent=_HEAD_STYLE, alignment=TA_RIGHT)
_NOTE_STYLE = ParagraphStyle(
    "reporte-nota", fontName=BODY_FONT, fontSize=7.8, leading=11, textColor=MUTED, spaceBefore=4, spaceAfter=2
)
_EMPTY_STYLE = ParagraphStyle("reporte-vacio", parent=_NOTE_STYLE, spaceBefore=2)
_KPI_LABEL_STYLE = ParagraphStyle(
    "reporte-kpi-rotulo", fontName=SEMIBOLD_FONT, fontSize=6.6, leading=8.5, textColor=MUTED
)
_KPI_VALUE_STYLE = ParagraphStyle("reporte-kpi-cifra", fontName=MONO_BOLD_FONT, fontSize=11, leading=14, textColor=INK)
_KPI_HINT_STYLE = ParagraphStyle(
    "reporte-kpi-aclaracion", fontName=BODY_FONT, fontSize=7, leading=9, textColor=MUTED, spaceBefore=1
)


def format_value(value: Value) -> str:
    """Texto de una celda o KPI. Plata con el formato unico del sistema."""
    if value is None:
        return "—"
    if isinstance(value, Pct):
        if value.value is None:
            return "—"
        number = Decimal(value.value).quantize(Decimal("0.01"))
        return f"{number:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".") + " %"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, Decimal):
        return formato_money(value)
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)


# Encabezado, como el ejemplo del presupuesto: barra verde, razon social a la
# izquierda, titulo a la derecha y debajo "Rotulo: valor" con el rotulo en gris.
HEADER_SEP = " " * 6
HEADER_PAD = 5 * mm
TITLE_SIZE = 13
DETAIL_SIZE = 8
RAZON_SIZE = 10.5
RAZON_CHAR_SPACE = 0.6


def _header_bits(doc: ReportDocument) -> tuple[list[str], list[str]]:
    """(quien, cuando) de la linea de detalle: CUIT y alcance; periodo y emision."""
    quien = [bit for bit in (f"CUIT: {doc.cuit}" if doc.cuit else None, doc.alcance_label) if bit]
    cuando = [bit for bit in (doc.periodo_label or None, f"Emitido: {doc.emitido_label}") if bit]
    return quien, cuando


def header_lines(doc: ReportDocument) -> tuple[str, str, str]:
    """(razon social, titulo, linea de detalle) del encabezado de cada pagina."""
    quien, cuando = _header_bits(doc)
    return doc.razon_social.upper(), doc.titulo, HEADER_SEP.join(quien + cuando)


def _detail_width(items: list[str]) -> float:
    return stringWidth(HEADER_SEP.join(items), BODY_FONT, DETAIL_SIZE)


def _razon_width(text: str, size: float) -> float:
    return stringWidth(text, BOLD_FONT, size) + RAZON_CHAR_SPACE * max(len(text) - 1, 0)


def _clip(text: str, fits) -> str:
    if fits(text):
        return text
    while text and not fits(text + "…"):
        text = text[:-1]
    return text.rstrip() + "…"


def _detail_lines(doc: ReportDocument, width: float) -> list[list[str]]:
    """Items de la linea de detalle; pasa a dos lineas (quien / cuando) si no entra
    en una, y en ultimo caso se corta el ultimo item de la linea."""
    quien, cuando = _header_bits(doc)
    if _detail_width(quien + cuando) <= width:
        return [quien + cuando]
    lineas = []
    for items in (quien, cuando):
        if items:
            *previos, ultimo = items
            ultimo = _clip(ultimo, lambda texto: _detail_width([*previos, texto]) <= width)
            lineas.append([*previos, ultimo])
    return lineas


def header_layout(doc: ReportDocument, width: float) -> tuple[str, float, list[str]]:
    """Encabezado que entra en `width`: (razon social, su tamano de letra, lineas
    de detalle). La razon social se achica (y en ultimo caso se corta) para no
    pisar el titulo; el detalle pasa a dos lineas si no entra en una."""
    razon_social = doc.razon_social.upper()
    room = width - stringWidth(doc.titulo, TITLE_FONT, TITLE_SIZE) - 12
    size = RAZON_SIZE
    while size > 7.5 and _razon_width(razon_social, size) > room:
        size -= 0.5
    razon_social = _clip(razon_social, lambda texto: _razon_width(texto, size) <= room)
    return razon_social, size, [HEADER_SEP.join(items) for items in _detail_lines(doc, width)]


def footer_text(doc: ReportDocument) -> str:
    return f"Gerayse — {doc.titulo} · {doc.razon_social}"


def footer_line(doc: ReportDocument, width: float) -> str:
    """Pie que entra a la izquierda de "Pagina X de Y": una razon social larga se corta."""
    room = width - stringWidth("Página 999 de 999", BODY_FONT, 7.5) - 12
    return _clip(footer_text(doc), lambda texto: stringWidth(texto, BODY_FONT, 7.5) <= room)


class _DocMarker(Flowable):
    """Invisible: avisa al canvas que documento se esta dibujando."""

    def __init__(self, doc: ReportDocument):
        super().__init__()
        self.doc = doc

    def wrap(self, *args):
        return (0, 0)

    def draw(self):
        self.canv._report_doc = self.doc


class _NumberedCanvas(rl_canvas.Canvas):
    """Guarda las paginas y al final escribe "Pagina X de Y"."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self.setFont(BODY_FONT, 7.5)
            self.setFillColor(MUTED)
            self.drawRightString(A4[0] - MARGIN_X, 9 * mm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def _draw_detail_item(canv, x: float, y: float, item: str) -> float:
    """Dibuja "Rotulo: valor" (rotulo en gris, valor en tinta) y devuelve el x final."""
    rotulo, sep, valor = item.partition(": ")
    partes = [(rotulo + ":", MUTED), (" " + valor, TEXT)] if sep else [(item, TEXT)]
    for texto, color in partes:
        canv.setFillColor(color)
        canv.drawString(x, y, texto)
        x += stringWidth(texto, BODY_FONT, DETAIL_SIZE)
    return x


def _draw_page_frame(canv, _doc):
    # Se dibuja al TERMINAR la pagina: recien ahi se sabe que documento la ocupa.
    report = getattr(canv, "_report_doc", None)
    if report is None:
        return
    width = A4[0] - 2 * MARGIN_X
    inner = width - 2 * HEADER_PAD
    left, right = MARGIN_X + HEADER_PAD, MARGIN_X + width - HEADER_PAD
    razon_social, size, _ = header_layout(report, inner)
    top = A4[1] - HEADER_TOP
    bar = 1.3 * mm
    baseline = top - bar - 6.8 * mm
    separator = baseline - 3.4 * mm
    canv.saveState()
    canv.setFillColor(INK)
    canv.setFont(BOLD_FONT, size)
    canv.drawString(left, baseline, razon_social, charSpace=RAZON_CHAR_SPACE)
    canv.setFillColor(GREEN)
    canv.setFont(TITLE_FONT, TITLE_SIZE)
    canv.drawRightString(right, baseline, report.titulo)
    canv.setStrokeColor(LINE)
    canv.setLineWidth(0.6)
    canv.line(left, separator, right, separator)
    canv.setFont(BODY_FONT, DETAIL_SIZE)
    y = separator - 4.6 * mm
    for items in _detail_lines(report, inner):
        x = left
        for item in items:
            x = _draw_detail_item(canv, x, y, item) + stringWidth(HEADER_SEP, BODY_FONT, DETAIL_SIZE)
        y -= 3.9 * mm
    bottom = y + 3.9 * mm - 3.3 * mm
    canv.setStrokeColor(LINE_STRONG)
    canv.rect(MARGIN_X, bottom, width, top - bottom, stroke=1, fill=0)
    canv.setFillColor(INK)
    canv.rect(MARGIN_X, top - bar, width, bar, stroke=0, fill=1)
    canv.setStrokeColor(LINE)
    canv.line(MARGIN_X, 13 * mm, A4[0] - MARGIN_X, 13 * mm)
    canv.setFillColor(MUTED)
    canv.setFont(BODY_FONT, 7.5)
    canv.drawString(MARGIN_X, 9 * mm, footer_line(report, width))
    canv.restoreState()


def _kpi_cell(kpi: Kpi) -> list[Paragraph]:
    # Un parrafo por linea: cada una con su interlineado (la aclaracion va apretada).
    cell = [
        Paragraph(escape(kpi.label.upper()), _KPI_LABEL_STYLE),
        Paragraph(escape(format_value(kpi.value)), _KPI_VALUE_STYLE),
    ]
    if kpi.hint:
        cell.append(Paragraph(escape(kpi.hint), _KPI_HINT_STYLE))
    return cell


def _kpi_grid(kpis: list[Kpi], width: float) -> list:
    """Tarjetas de a tres por fila, cada una con su filete verde a la izquierda."""
    card = (width - 2 * KPI_GAP) / 3
    flow = []
    for start in range(0, len(kpis), 3):
        chunk = kpis[start : start + 3]
        cells, widths = [], []
        style = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]
        for position in range(3):
            if position:
                cells.append("")
                widths.append(KPI_GAP)
            col = len(cells)
            widths.append(card)
            if position < len(chunk):
                cells.append(_kpi_cell(chunk[position]))
                style += [("BACKGROUND", (col, 0), (col, 0), PANEL), ("LINEBEFORE", (col, 0), (col, 0), 1.6, INK)]
            else:
                cells.append("")
        table = Table([cells], colWidths=widths)
        table.setStyle(TableStyle(style))
        flow += [table, Spacer(1, KPI_GAP * 0.7)]
    return flow


def _cell(text: str, col_width: float, *, bold: bool, numeric: bool):
    """Texto plano si entra en la columna; Paragraph (que hace salto de linea) si no.
    Envolver solo lo que hace falta mantiene rapido un desglose de miles de filas."""
    font = BOLD_FONT if bold else BODY_FONT
    if numeric or stringWidth(text, font, CELL_SIZE) <= col_width - 2 * CELL_PADDING:
        return text
    return Paragraph(escape(text), _CELL_BOLD_STYLE if bold else _CELL_STYLE)


def _head_cell(text: str, col_width: float, *, numeric: bool):
    if stringWidth(text, SEMIBOLD_FONT, HEAD_SIZE) <= col_width - 2 * CELL_PADDING:
        return text
    return Paragraph(escape(text), _HEAD_RIGHT_STYLE if numeric else _HEAD_STYLE)


def numeric_font_sizes(texts: list[list[str]], block: TableBlock, col_widths: list[float]) -> tuple[float, float]:
    """(cuerpo, totales): tamano de letra de las cifras de la tabla. Una cifra no
    hace salto de linea: si alguna no entra en su columna a CELL_SIZE, las cifras
    bajan (de a 0,25) hasta que entre la mas ajustada, sin pasar de MIN_CELL_SIZE.
    Las filas comunes van todas al mismo tamano; los subtotales y totales se miden
    aparte (en negrita, y un total grande no achica todo el cuerpo) y nunca quedan
    mas grandes que el cuerpo. `texts` son las filas ya formateadas."""
    body = total = CELL_SIZE
    for col in block.numeric_cols:
        available = col_widths[col] - 2 * CELL_PADDING
        for index, cells in enumerate(texts):
            if index in block.group_rows:
                continue
            is_total = index in block.total_rows
            width = stringWidth(cells[col], BOLD_FONT if is_total else BODY_FONT, CELL_SIZE)
            if width > available:
                fit = math.floor(CELL_SIZE * available / width * 4) / 4
                if is_total:
                    total = min(total, fit)
                else:
                    body = min(body, fit)
    body = max(MIN_CELL_SIZE, body)
    return body, max(MIN_CELL_SIZE, min(total, body))


def _smaller_figures(start: tuple[int, int], end: tuple[int, int], size: float) -> list:
    """Cifras a `size` en la misma linea base que el texto de la fila: con VALIGN TOP
    la base queda en tope - relleno - tamano, asi que lo que se achica la letra pasa
    al relleno de arriba (y sale del de abajo, para que la fila no crezca)."""
    shift = CELL_SIZE - size
    return [
        ("FONTSIZE", start, end, size),
        ("TOPPADDING", start, end, ROW_PADDING + shift),
        ("BOTTOMPADDING", start, end, ROW_PADDING - shift),
    ]


def _label_span_end(cells: list[str], numeric_cols: frozenset[int]) -> int:
    """Ultima columna que puede ocupar el rotulo de la fila: las vacias que le
    siguen, sin entrar en una columna numerica. 0 = no se expande."""
    last = 0
    for col in range(1, len(cells)):
        if cells[col] or col in numeric_cols:
            break
        last = col
    return last


def _section_title(title: str) -> list:
    # Sin lugar para el titulo y unas filas, la seccion arranca en la pagina siguiente.
    return [
        CondPageBreak(28 * mm),
        Paragraph(escape(title), _SECTION_STYLE),
        HRFlowable(width="100%", thickness=0.6, color=LINE_STRONG, spaceBefore=0, spaceAfter=4),
    ]


def _table(block: TableBlock, width: float) -> list:
    flow = _section_title(block.title)
    if not block.rows:
        flow.append(Paragraph(escape(block.empty_text), _EMPTY_STYLE))
        return flow
    col_widths = [fraction * width for fraction in block.widths]
    texts = [[format_value(value) for value in row] for row in block.rows]
    data = [[_head_cell(text, col_widths[col], numeric=col in block.numeric_cols) for col, text in enumerate(block.columns)]]
    label_spans = {}
    for index, cells in enumerate(texts):
        if index in block.group_rows:
            data.append([cells[0]] + [""] * (len(cells) - 1))
            continue
        bold = index in block.total_rows
        widths = list(col_widths)
        if bold:
            # El rotulo de un subtotal ocupa las celdas vacias que le siguen, asi
            # "Subtotal Septiembre 2026" no se parte en una columna angosta.
            last = _label_span_end(cells, block.numeric_cols)
            if last:
                label_spans[index] = last
                widths[0] = sum(col_widths[: last + 1])
        data.append(
            [
                _cell(text, widths[col], bold=bold, numeric=col in block.numeric_cols)
                for col, text in enumerate(cells)
            ]
        )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), SEMIBOLD_FONT),
        ("FONTSIZE", (0, 0), (-1, 0), HEAD_SIZE),
        ("TOPPADDING", (0, 0), (-1, 0), 4),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ("FONTNAME", (0, 1), (-1, -1), BODY_FONT),
        ("FONTSIZE", (0, 1), (-1, -1), CELL_SIZE),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT),
        ("LEADING", (0, 0), (-1, -1), CELL_SIZE + 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 1), (-1, -1), ROW_PADDING),
        ("BOTTOMPADDING", (0, 1), (-1, -1), ROW_PADDING),
        ("LEFTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
    ]
    for col in block.numeric_cols:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    body_size, total_size = numeric_font_sizes(texts, block, col_widths)
    for col in block.numeric_cols:
        if body_size < CELL_SIZE:
            style += _smaller_figures((col, 1), (col, -1), body_size)
        if total_size < body_size:
            for index in block.total_rows:
                style += _smaller_figures((col, index + 1), (col, index + 1), total_size)
    for index in block.group_rows:
        row = index + 1
        style += [
            ("SPAN", (0, row), (-1, row)),
            ("BACKGROUND", (0, row), (-1, row), GROUP_BG),
            ("FONTNAME", (0, row), (-1, row), SEMIBOLD_FONT),
            ("TEXTCOLOR", (0, row), (-1, row), INK),
            ("ALIGN", (0, row), (-1, row), "LEFT"),
        ]
    for index in block.total_rows:
        row = index + 1
        style += [
            ("BACKGROUND", (0, row), (-1, row), TOTAL_BG),
            ("FONTNAME", (0, row), (-1, row), BOLD_FONT),
            ("TEXTCOLOR", (0, row), (-1, row), INK),
        ]
        if index in label_spans:
            style.append(("SPAN", (0, row), (label_spans[index], row)))
    table = LongTable(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle(style))
    flow.append(table)
    return flow


def build_story(docs: list[ReportDocument], width: float) -> list:
    story = []
    for index, report in enumerate(docs):
        if index:
            story.append(PageBreak())
        story.append(_DocMarker(report))
        story += _kpi_grid(report.kpis, width)
        for block in report.blocks:
            if isinstance(block, TableBlock):
                story += _table(block, width)
            elif isinstance(block, NoteBlock):
                story.append(Paragraph(escape(block.text), _NOTE_STYLE))
    return story


def render_report_pdf(docs: list[ReportDocument]) -> bytes:
    if not docs:
        raise ValueError("render_report_pdf necesita al menos un documento.")
    buffer = BytesIO()
    pdf = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN_X,
        rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP,
        bottomMargin=MARGIN_BOTTOM,
        title=f"{docs[0].titulo} - {docs[0].razon_social}",
        author="Gerayse",
        creator="Gerayse",
    )
    # Sin relleno interno: titulos, notas y tablas alinean con el encabezado.
    frame = Frame(
        pdf.leftMargin,
        pdf.bottomMargin,
        pdf.width,
        pdf.height,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
        id="cuerpo",
    )
    pdf.addPageTemplates([PageTemplate(id="reporte", frames=[frame], onPageEnd=_draw_page_frame)])
    pdf.build(build_story(docs, pdf.width), canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
