"""Nucleo del modulo de reportes PDF: documento, alcance y renderer (slice S1)."""

import re
import time
from datetime import date, datetime
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone

from cashops.models import Empresa, Sucursal
from treasury.reportes.alcance import ReportScope, nombre_mes, rango_de_meses
from treasury.reportes.documento import Kpi, NoteBlock, Pct, ReportDocument, TableBlock
from reportlab.pdfbase.pdfmetrics import stringWidth

from treasury.reportes.pdf import (
    BODY_FONT,
    BOLD_FONT,
    CELL_SIZE,
    MIN_CELL_SIZE,
    RAZON_CHAR_SPACE,
    RAZON_SIZE,
    ROW_PADDING,
    TITLE_FONT,
    TITLE_SIZE,
    _label_span_end,
    _smaller_figures,
    build_story,
    footer_line,
    format_value,
    header_layout,
    header_lines,
    numeric_font_sizes,
    render_report_pdf,
)


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


def _doc(rows=None, **overrides) -> ReportDocument:
    rows = rows if rows is not None else [["Proveedor Uno", Decimal("100.50")]]
    data = dict(
        slug="prueba",
        titulo="Reporte de prueba",
        razon_social="ARMADI SRL",
        cuit="30-11111111-1",
        alcance_label="Alcance: General (todas las sucursales)",
        periodo_label="Período: 01/09/2026 al 30/09/2026",
        emitido_label="13/10/2026 11:40",
        kpis=[Kpi("Ventas", Decimal("1234.5"), "del período"), Kpi("Facturas", 3), Kpi("Margen", Pct(Decimal("12.345")))],
        blocks=[
            TableBlock(
                title="Detalle",
                columns=["Proveedor", "Saldo"],
                rows=rows,
                widths=[0.7, 0.3],
                numeric_cols=frozenset({1}),
            ),
            NoteBlock("Nota de prueba."),
        ],
    )
    data.update(overrides)
    return ReportDocument(**data)


class FormatValueTests(SimpleTestCase):
    def test_money_uses_the_system_format(self):
        self.assertEqual(format_value(Decimal("1209905.08")), "$ 1.209.905,08")
        self.assertEqual(format_value(Decimal("-50")), "$ -50,00")

    def test_percent_int_date_and_none(self):
        # Los snapshots ya entregan 2 decimales; el renderer no redondea distinto que ellos.
        self.assertEqual(format_value(Pct(Decimal("12.34"))), "12,34 %")
        self.assertEqual(format_value(Pct(Decimal("1234.5"))), "1.234,50 %")
        self.assertEqual(format_value(Pct(None)), "—")
        self.assertEqual(format_value(1755), "1.755")
        self.assertEqual(format_value(date(2026, 9, 5)), "05/09/2026")
        self.assertEqual(format_value(None), "—")
        self.assertEqual(format_value("Texto"), "Texto")


class TableBlockValidationTests(SimpleTestCase):
    def test_widths_must_match_columns_and_sum_one(self):
        with self.assertRaises(ValueError):
            TableBlock(title="x", columns=["a", "b"], rows=[], widths=[1.0])
        with self.assertRaises(ValueError):
            TableBlock(title="x", columns=["a", "b"], rows=[], widths=[0.5, 0.4])

    def test_rows_must_have_one_cell_per_column(self):
        with self.assertRaises(ValueError):
            TableBlock(title="x", columns=["a", "b"], rows=[["solo una"]], widths=[0.5, 0.5])

    def test_lookup_helpers(self):
        doc = _doc()
        self.assertEqual(doc.kpi("Ventas").value, Decimal("1234.5"))
        self.assertEqual(doc.table("Detalle").rows[0][0], "Proveedor Uno")
        self.assertEqual(doc.notes, ["Nota de prueba."])
        with self.assertRaises(KeyError):
            doc.kpi("No existe")


class ReportScopeTests(SimpleTestCase):
    def setUp(self):
        self.empresa = Empresa(pk=7, nombre="ARMADI SRL", identificador_fiscal="30-11111111-1")
        self.sucursal = Sucursal(pk=3, nombre="Estación Belgrano", codigo="EB", empresa=self.empresa)

    def test_general_scope_labels(self):
        scope = ReportScope(empresa=self.empresa, date_from=date(2026, 9, 1), date_to=date(2026, 9, 30))
        self.assertEqual(scope.empresa_ids, [7])
        self.assertEqual(scope.razon_social, "ARMADI SRL")
        self.assertEqual(scope.cuit, "30-11111111-1")
        self.assertTrue(scope.es_general)
        self.assertEqual(scope.alcance_label, "Alcance: General (todas las sucursales)")
        self.assertEqual(scope.periodo_label, "Período: 01/09/2026 al 30/09/2026")
        self.assertEqual(
            scope.filename("situacion-economica"), "situacion-economica_armadi-srl_general_2026-09_2026-09.pdf"
        )

    def test_sucursal_scope_labels(self):
        scope = ReportScope(empresa=self.empresa, sucursal=self.sucursal, date_from=date(2026, 7, 1), date_to=date(2026, 9, 30))
        self.assertFalse(scope.es_general)
        self.assertEqual(scope.alcance_label, "Sucursal: Estación Belgrano")
        self.assertEqual(scope.filename("situacion-financiera"), "situacion-financiera_armadi-srl_eb_2026-07_2026-09.pdf")
        self.assertEqual(scope.filename("desglose-deudas", sufijo="al_2026-10-13"), "desglose-deudas_armadi-srl_eb_al_2026-10-13.pdf")

    def test_single_branch_company_labels_the_general_with_the_branch(self):
        mapogo = Empresa(pk=8, nombre="MAPOGO SRL", identificador_fiscal=None)
        vivre = Sucursal(pk=9, nombre="Vivre", codigo="VIVRE", empresa=mapogo)
        scope = ReportScope(empresa=mapogo, sucursal_unica=vivre)
        self.assertTrue(scope.es_general)
        self.assertIsNone(scope.cuit)
        self.assertEqual(scope.alcance_label, "Sucursal: Vivre (única de la empresa)")
        self.assertEqual(scope.alcance_slug, "general")

    def test_emitido_label_uses_local_time(self):
        emitido = timezone.make_aware(datetime(2026, 10, 13, 11, 40))
        scope = ReportScope(empresa=self.empresa, emitido=emitido)
        self.assertEqual(scope.emitido_label, "13/10/2026 11:40")

    def test_month_range_caps_current_month_at_today(self):
        hoy = date(2026, 10, 13)
        self.assertEqual(rango_de_meses(date(2026, 9, 1), date(2026, 9, 1), hoy=hoy), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(rango_de_meses(date(2026, 8, 15), date(2026, 10, 1), hoy=hoy), (date(2026, 8, 1), date(2026, 10, 13)))
        self.assertEqual(rango_de_meses(date(2026, 2, 1), date(2026, 2, 1), hoy=hoy), (date(2026, 2, 1), date(2026, 2, 28)))

    def test_month_range_never_ends_in_the_future(self):
        # El form ya rechaza meses futuros; el rango igual nunca da un corte futuro.
        hoy = date(2026, 10, 13)
        self.assertEqual(rango_de_meses(date(2026, 10, 1), date(2026, 12, 1), hoy=hoy), (date(2026, 10, 1), hoy))

    def test_month_name(self):
        self.assertEqual(nombre_mes(date(2026, 9, 1)), "Septiembre 2026")


class RendererTests(SimpleTestCase):
    def test_renders_a_pdf(self):
        pdf = render_report_pdf([_doc()])
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(_pages(pdf), 1)

    def test_long_table_spans_several_pages(self):
        rows = [[f"Proveedor {i} con un nombre bastante largo para forzar salto", Decimal(i)] for i in range(300)]
        pdf = render_report_pdf([_doc(rows=rows)])
        self.assertGreater(_pages(pdf), 1)

    def test_empty_table_shows_its_empty_text(self):
        story = build_story([_doc(rows=[])], 500)
        texts = [getattr(flowable, "text", "") for flowable in story]
        self.assertIn("Sin datos para el alcance elegido.", texts)

    def test_several_documents_start_each_on_a_new_page(self):
        pdf = render_report_pdf([_doc(), _doc(alcance_label="Sucursal: Estación Belgrano")])
        self.assertEqual(_pages(pdf), 2)

    def test_group_and_total_rows_render(self):
        block = TableBlock(
            title="Por mes",
            columns=["Proveedor", "Saldo"],
            rows=[["Septiembre 2026 · 1 factura", None], ["Proveedor Uno", Decimal("10")], ["Subtotal", Decimal("10")]],
            widths=[0.7, 0.3],
            numeric_cols=frozenset({1}),
            group_rows=frozenset({0}),
            total_rows=frozenset({2}),
        )
        pdf = render_report_pdf([_doc(blocks=[block])])
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_header_lines_skip_missing_cuit_and_period(self):
        razon, titulo, detalle = header_lines(_doc(cuit=None, periodo_label=""))
        self.assertEqual(razon, "ARMADI SRL")
        self.assertEqual(titulo, "Reporte de prueba")
        self.assertNotIn("CUIT", detalle)
        self.assertNotIn("Período", detalle)
        _, _, con_periodo = header_lines(_doc())
        self.assertIn("Período: 01/09/2026 al 30/09/2026", con_periodo)
        self.assertIn("Emitido: 13/10/2026 11:40", detalle)

    def test_requires_at_least_one_document(self):
        with self.assertRaises(ValueError):
            render_report_pdf([])

    def test_two_thousand_rows_render_fast_enough(self):
        rows = [[f"Proveedor {i}", Decimal(i)] for i in range(2000)]
        start = time.perf_counter()
        pdf = render_report_pdf([_doc(rows=rows)])
        elapsed = time.perf_counter() - start
        self.assertGreater(_pages(pdf), 20)
        self.assertLess(elapsed, 10, f"2000 filas tardaron {elapsed:.1f}s")


class LabelSpanTests(SimpleTestCase):
    def test_subtotal_label_takes_the_empty_cells_up_to_the_first_number(self):
        cells = ["Subtotal Septiembre 2026", "", "", "", "", "$ 10,00", "", "$ 10,00"]
        self.assertEqual(_label_span_end(cells, frozenset({5, 6, 7})), 4)

    def test_label_never_spans_into_a_numeric_column(self):
        # "Pagado" vacio en el subtotal: es numerica, el rotulo no la ocupa.
        self.assertEqual(_label_span_end(["Total", "", "$ 5,00"], frozenset({1, 2})), 0)
        self.assertEqual(_label_span_end(["Total", "$ 5,00"], frozenset({1})), 0)


class HeaderLayoutTests(SimpleTestCase):
    WIDTH = 505

    def test_short_header_stays_in_one_line(self):
        razon, size, lineas = header_layout(_doc(), self.WIDTH)
        self.assertEqual((razon, size), ("ARMADI SRL", RAZON_SIZE))
        self.assertEqual(len(lineas), 1)

    def test_long_detail_splits_in_two_lines_that_fit(self):
        # Peor caso real: desglose con filtro de meses, CUIT y empresa de sucursal unica.
        doc = _doc(
            titulo="Desglose de deudas",
            alcance_label="Sucursal: Estación Belgrano Norte (única de la empresa)",
            periodo_label="Deuda abierta al 13/10/2026 · períodos 07/2026 a 09/2026",
        )
        _, _, lineas = header_layout(doc, self.WIDTH)
        self.assertEqual(len(lineas), 2)
        self.assertTrue(lineas[0].startswith("CUIT: 30-11111111-1"))
        self.assertIn("Emitido: 13/10/2026 11:40", lineas[1])
        for linea in lineas:
            self.assertLessEqual(stringWidth(linea, BODY_FONT, 8), self.WIDTH)

    def test_long_company_name_never_overlaps_the_title(self):
        doc = _doc(razon_social="SOCIEDAD ANÓNIMA DE GASTRONOMÍA Y PANADERÍA DEL CENTRO Y ALREDEDORES SRL")
        razon, size, _ = header_layout(doc, self.WIDTH)
        room = self.WIDTH - stringWidth(doc.titulo, TITLE_FONT, TITLE_SIZE) - 12
        self.assertLess(size, RAZON_SIZE)
        self.assertLessEqual(stringWidth(razon, BOLD_FONT, size) + RAZON_CHAR_SPACE * (len(razon) - 1), room)


class NumericFontSizeTests(SimpleTestCase):
    """Una cifra no hace salto de linea: si no entra, la tabla achica sus cifras."""

    WIDTH = 505
    WIDTHS = [0.66, 0.2, 0.14]

    def _sizes(self, rows, total_rows=frozenset()):
        block = TableBlock(
            title="Cifras",
            columns=["Concepto", "Uno", "Dos"],
            rows=rows,
            widths=self.WIDTHS,
            numeric_cols=frozenset({1, 2}),
            total_rows=total_rows,
        )
        texts = [[format_value(value) for value in row] for row in rows]
        col_widths = [fraction * self.WIDTH for fraction in self.WIDTHS]
        return texts, col_widths, numeric_font_sizes(texts, block, col_widths)

    def test_numbers_that_fit_keep_the_cell_size(self):
        *_, sizes = self._sizes([["Uno", Decimal("1.00"), Decimal("100.50")]])
        self.assertEqual(sizes, (CELL_SIZE, CELL_SIZE))

    def test_a_wide_number_shrinks_the_whole_body_until_it_fits(self):
        rows = [["Uno", Decimal("1.00"), Decimal("1234567890.12")], ["Total", Decimal("1.00"), Decimal("1.00")]]
        texts, col_widths, (body, total) = self._sizes(rows, frozenset({1}))
        self.assertLess(body, CELL_SIZE)
        self.assertGreaterEqual(body, MIN_CELL_SIZE)
        self.assertLessEqual(stringWidth(texts[0][2], BODY_FONT, body), col_widths[2] - 8)
        # Un total nunca queda mas grande que el cuerpo.
        self.assertEqual(total, body)

    def test_a_wide_total_only_shrinks_the_totals(self):
        rows = [["Uno", Decimal("1.00"), Decimal("1.00")], ["Total", Decimal("1.00"), Decimal("1234567890.12")]]
        texts, col_widths, (body, total) = self._sizes(rows, frozenset({1}))
        self.assertEqual(body, CELL_SIZE)
        self.assertLess(total, CELL_SIZE)
        self.assertLessEqual(stringWidth(texts[1][2], BOLD_FONT, total), col_widths[2] - 8)

    def test_never_goes_below_the_minimum(self):
        *_, sizes = self._sizes([["Uno", Decimal("1.00"), Decimal("123456789012.34")]])
        self.assertEqual(sizes, (MIN_CELL_SIZE, MIN_CELL_SIZE))


class BrandFontsTests(SimpleTestCase):
    def test_pdf_embeds_the_brand_fonts(self):
        pdf = render_report_pdf([_doc()])
        for font in (b"PublicSans-Regular", b"PublicSans-SemiBold", b"PublicSans-Bold", b"Fraunces-SemiBold", b"SplineSansMono-SemiBold"):
            self.assertIn(font, pdf)

    def test_header_detail_uses_label_and_value(self):
        _, _, detalle = header_lines(_doc())
        self.assertTrue(detalle.startswith("CUIT: 30-11111111-1"))
        self.assertIn("Alcance: General (todas las sucursales)", detalle)


class FooterAndFigureAlignmentTests(SimpleTestCase):
    WIDTH = 505

    def test_long_company_name_is_cut_before_the_page_number(self):
        doc = _doc(razon_social="SOCIEDAD ANÓNIMA DE GASTRONOMÍA " * 5)
        linea = footer_line(doc, self.WIDTH)
        self.assertTrue(linea.endswith("…"))
        room = self.WIDTH - stringWidth("Página 999 de 999", BODY_FONT, 7.5) - 12
        self.assertLessEqual(stringWidth(linea, BODY_FONT, 7.5), room)

    def test_short_footer_is_left_as_is(self):
        self.assertEqual(footer_line(_doc(), self.WIDTH), "Gerayse — Reporte de prueba · ARMADI SRL")

    def test_smaller_figures_keep_the_row_height_and_the_baseline(self):
        for size in (MIN_CELL_SIZE, 6.5, 7.25):
            comandos = {cmd[0]: cmd[3] for cmd in _smaller_figures((1, 1), (1, -1), size)}
            self.assertEqual(comandos["FONTSIZE"], size)
            # Lo que baja la letra sube el relleno de arriba: misma linea base que el rotulo.
            self.assertEqual(comandos["TOPPADDING"] + size, ROW_PADDING + CELL_SIZE)
            # Y sale del de abajo: la fila no crece.
            self.assertEqual(comandos["TOPPADDING"] + comandos["BOTTOMPADDING"], 2 * ROW_PADDING)
            self.assertGreaterEqual(comandos["BOTTOMPADDING"], 0)
