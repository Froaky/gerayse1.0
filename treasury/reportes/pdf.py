"""Renderer generico de reportes PDF con ReportLab.

No conoce el dominio: recibe `ReportDocument`s ya armados y los dibuja en A4.
Cada pagina lleva el encabezado del documento que la ocupa (razon social,
titulo, CUIT, alcance, periodo y emision) y al pie "Pagina X de Y". Varios
documentos en un mismo archivo (general + cada sucursal) arrancan cada uno en
pagina nueva con su propio encabezado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
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

INK = colors.HexColor("#1b3a2e")
GREEN = colors.HexColor("#2f6b52")
MUTED = colors.HexColor("#6b757a")
LINE = colors.HexColor("#dfe4e2")
PANEL = colors.HexColor("#f7f9f8")
TOTAL_BG = colors.HexColor("#e4f1e9")
GROUP_BG = colors.HexColor("#f0f3f1")

MARGIN_X = 16 * mm
MARGIN_TOP = 32 * mm
MARGIN_BOTTOM = 16 * mm
BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
CELL_SIZE = 8
CELL_PADDING = 4

_SECTION_STYLE = ParagraphStyle(
    "reporte-seccion", fontName="Times-Bold", fontSize=11.5, leading=14, textColor=INK, spaceBefore=9, spaceAfter=5
)
_CELL_STYLE = ParagraphStyle("reporte-celda", fontName=BODY_FONT, fontSize=CELL_SIZE, leading=CELL_SIZE + 2)
_CELL_BOLD_STYLE = ParagraphStyle("reporte-celda-total", parent=_CELL_STYLE, fontName=BOLD_FONT)
_NOTE_STYLE = ParagraphStyle(
    "reporte-nota", fontName=BODY_FONT, fontSize=8, leading=11, textColor=MUTED, spaceBefore=5, spaceAfter=2
)
_EMPTY_STYLE = ParagraphStyle("reporte-vacio", parent=_NOTE_STYLE, spaceBefore=2)
_KPI_STYLE = ParagraphStyle("reporte-kpi", fontName=BODY_FONT, fontSize=8, leading=12)


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


HEADER_SEP = "   ·   "
TITLE_FONT = "Times-Bold"
TITLE_SIZE = 12
DETAIL_SIZE = 8


def _header_bits(doc: ReportDocument) -> tuple[list[str], list[str]]:
    """(quien, cuando) de la linea de detalle: CUIT y alcance; periodo y emision."""
    quien = [bit for bit in (f"CUIT {doc.cuit}" if doc.cuit else None, doc.alcance_label) if bit]
    cuando = [bit for bit in (doc.periodo_label or None, f"Emitido {doc.emitido_label}") if bit]
    return quien, cuando


def header_lines(doc: ReportDocument) -> tuple[str, str, str]:
    """(razon social, titulo, linea de detalle) del encabezado de cada pagina."""
    quien, cuando = _header_bits(doc)
    return doc.razon_social, doc.titulo, HEADER_SEP.join(quien + cuando)


def _clip(text: str, font: str, size: float, width: float) -> str:
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def header_layout(doc: ReportDocument, width: float) -> tuple[str, float, list[str]]:
    """Encabezado que entra en `width`: (razon social, su tamano de letra, lineas
    de detalle). La razon social se achica (y en ultimo caso se corta) para no
    pisar el titulo; el detalle pasa a dos lineas si no entra en una."""
    room = width - stringWidth(doc.titulo, TITLE_FONT, TITLE_SIZE) - 12
    size = 11
    while size > 8 and stringWidth(doc.razon_social, BOLD_FONT, size) > room:
        size -= 0.5
    razon_social = _clip(doc.razon_social, BOLD_FONT, size, room)
    quien, cuando = _header_bits(doc)
    una = HEADER_SEP.join(quien + cuando)
    if stringWidth(una, BODY_FONT, DETAIL_SIZE) <= width:
        return razon_social, size, [una]
    return razon_social, size, [_clip(HEADER_SEP.join(part), BODY_FONT, DETAIL_SIZE, width) for part in (quien, cuando)]


def footer_text(doc: ReportDocument) -> str:
    return f"Gerayse · {doc.titulo} · {doc.razon_social}"


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


def _draw_page_frame(canv, _doc):
    # Se dibuja al TERMINAR la pagina: recien ahi se sabe que documento la ocupa.
    report = getattr(canv, "_report_doc", None)
    if report is None:
        return
    width = A4[0] - 2 * MARGIN_X
    razon_social, size, detalle = header_layout(report, width)
    top = A4[1] - 14 * mm
    canv.saveState()
    canv.setFillColor(INK)
    canv.rect(MARGIN_X, top + 2 * mm, width, 1.2 * mm, stroke=0, fill=1)
    canv.setFont(BOLD_FONT, size)
    canv.drawString(MARGIN_X, top - 4 * mm, razon_social)
    canv.setFillColor(GREEN)
    canv.setFont(TITLE_FONT, TITLE_SIZE)
    canv.drawRightString(A4[0] - MARGIN_X, top - 4 * mm, report.titulo)
    canv.setFillColor(MUTED)
    canv.setFont(BODY_FONT, DETAIL_SIZE)
    y = top - 9.5 * mm
    for linea in detalle:
        canv.drawString(MARGIN_X, y, linea)
        y -= 3.8 * mm
    canv.setStrokeColor(LINE)
    canv.line(MARGIN_X, y + 1.3 * mm, A4[0] - MARGIN_X, y + 1.3 * mm)
    canv.setFont(BODY_FONT, 7.5)
    canv.drawString(MARGIN_X, 9 * mm, footer_text(report))
    canv.restoreState()


def _kpi_grid(kpis: list[Kpi], width: float) -> list:
    if not kpis:
        return []
    cells = []
    for kpi in kpis:
        parts = [
            f'<font size="6.8" color="#6b757a">{escape(kpi.label.upper())}</font>',
            f'<font name="{BOLD_FONT}" size="10.5" color="#1b3a2e">{escape(format_value(kpi.value))}</font>',
        ]
        if kpi.hint:
            parts.append(f'<font size="7" color="#6b757a">{escape(kpi.hint)}</font>')
        cells.append(Paragraph("<br/>".join(parts), _KPI_STYLE))
    rows = [cells[i : i + 3] for i in range(0, len(cells), 3)]
    rows[-1] += [""] * (3 - len(rows[-1]))
    table = Table(rows, colWidths=[width / 3] * 3)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("LINEBEFORE", (0, 0), (0, -1), 1.5, INK),
                ("LINEBELOW", (0, 0), (-1, -2), 0.6, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [table, Spacer(1, 4)]


def _cell(text: str, col_width: float, *, bold: bool, numeric: bool):
    """Texto plano si entra en la columna; Paragraph (que hace salto de linea) si no.
    Envolver solo lo que hace falta mantiene rapido un desglose de miles de filas."""
    font = BOLD_FONT if bold else BODY_FONT
    if numeric or stringWidth(text, font, CELL_SIZE) <= col_width - 2 * CELL_PADDING:
        return text
    return Paragraph(escape(text), _CELL_BOLD_STYLE if bold else _CELL_STYLE)


def _label_span_end(cells: list[str], numeric_cols: frozenset[int]) -> int:
    """Ultima columna que puede ocupar el rotulo de la fila: las vacias que le
    siguen, sin entrar en una columna numerica. 0 = no se expande."""
    last = 0
    for col in range(1, len(cells)):
        if cells[col] or col in numeric_cols:
            break
        last = col
    return last


def _table(block: TableBlock, width: float) -> list:
    flow = [Paragraph(escape(block.title), _SECTION_STYLE)]
    if not block.rows:
        flow.append(Paragraph(escape(block.empty_text), _EMPTY_STYLE))
        return flow
    col_widths = [fraction * width for fraction in block.widths]
    data = [list(block.columns)]
    label_spans = {}
    for index, row in enumerate(block.rows):
        if index in block.group_rows:
            data.append([format_value(row[0])] + [""] * (len(row) - 1))
            continue
        bold = index in block.total_rows
        cells = [format_value(value) for value in row]
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
        ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
        ("FONTNAME", (0, 1), (-1, -1), BODY_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), CELL_SIZE),
        ("LEADING", (0, 0), (-1, -1), CELL_SIZE + 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PADDING),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PANEL]),
    ]
    for col in block.numeric_cols:
        style.append(("ALIGN", (col, 0), (col, -1), "RIGHT"))
    for index in block.group_rows:
        row = index + 1
        style += [
            ("SPAN", (0, row), (-1, row)),
            ("BACKGROUND", (0, row), (-1, row), GROUP_BG),
            ("FONTNAME", (0, row), (-1, row), BOLD_FONT),
            ("TEXTCOLOR", (0, row), (-1, row), INK),
            ("ALIGN", (0, row), (-1, row), "LEFT"),
        ]
    for index in block.total_rows:
        row = index + 1
        style += [
            ("BACKGROUND", (0, row), (-1, row), TOTAL_BG),
            ("FONTNAME", (0, row), (-1, row), BOLD_FONT),
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
    frame = Frame(pdf.leftMargin, pdf.bottomMargin, pdf.width, pdf.height, id="cuerpo")
    pdf.addPageTemplates([PageTemplate(id="reporte", frames=[frame], onPageEnd=_draw_page_frame)])
    pdf.build(build_story(docs, pdf.width), canvasmaker=_NumberedCanvas)
    return buffer.getvalue()
