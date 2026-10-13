"""Reporte 3: desglose de deudas abiertas por mes (slice S4)."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import CuentaBancaria, CuentaPorPagar, PagoTesoreria
from treasury.reportes.alcance import ReportScope
from treasury.reportes.deudas import (
    NOTA_INTERNO,
    NOTA_PAGADO,
    NOTA_SIN_FONDOS,
    NOTA_VENCIDA,
    SIN_DEUDAS,
    TABLA,
    build_desglose_deudas,
    nombre_archivo,
)
from treasury.reportes.pdf import format_value, render_report_pdf
from treasury.services import (
    annul_payable,
    build_financial_period_snapshot,
    build_open_payables_breakdown,
    create_bank_account,
    create_payable_category,
    create_supplier,
    register_payable,
    register_payment,
    register_transfer_payment,
    set_supplier_internal,
)

User = get_user_model()

JUNIO = date(2026, 6, 1)
JULIO = date(2026, 7, 1)
AGOSTO = date(2026, 8, 1)
SEPTIEMBRE = date(2026, 9, 1)

# Columnas del general.
PROVEEDOR, COMPROBANTE, SUCURSAL, EMISION, VENCE, IMPORTE, PAGADO, SALDO = range(8)

_SUCURSAL_A = object()


class DeudasFixtureMixin:
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin-deudas", password="test", email="d@test.com")
        self.empresa = Empresa.objects.create(nombre="ARMADI Deudas", identificador_fiscal="30-11111111-1")
        self.otra_empresa = Empresa.objects.create(nombre="MAPOGO Deudas")
        self.suc_a = Sucursal.objects.create(
            codigo="DA1", nombre="Deudas Uno", razon_social="ARMADI", empresa=self.empresa
        )
        self.suc_b = Sucursal.objects.create(
            codigo="DA2", nombre="Deudas Dos", razon_social="ARMADI", empresa=self.empresa
        )
        self.suc_otra = Sucursal.objects.create(
            codigo="DM1", nombre="Deudas Mapogo", razon_social="MAPOGO", empresa=self.otra_empresa
        )
        rubro = RubroOperativo.objects.create(nombre="Mercaderia deudas")
        self.categoria = create_payable_category(nombre="Mercaderia deudas", rubro_operativo=rubro, actor=self.admin)
        self.distribuidora = create_supplier(razon_social="Distribuidora Sur", actor=self.admin)
        self.almacen = create_supplier(razon_social="Almacen Norte", actor=self.admin)
        self.panaderia = create_supplier(razon_social="Panaderia Propia", actor=self.admin)
        self.panaderia = set_supplier_internal(supplier=self.panaderia, es_interno=True, actor=self.admin)
        self.cuenta = create_bank_account(
            nombre="Cuenta deudas",
            banco="Banco Uno",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-001",
            empresa=self.empresa,
            actor=self.admin,
        )
        self.hoy = timezone.localdate()
        self._refs = 0

    def _deuda(
        self,
        importe,
        *,
        periodo=JULIO,
        sucursal=_SUCURSAL_A,
        proveedor=None,
        vence=None,
        comprobante=None,
        concepto="Factura de mercaderia",
    ):
        vence = vence or self.hoy + timedelta(days=30)
        if comprobante is None:
            self._refs += 1
            comprobante = f"FC A 0001-{self._refs:08d}"
        return register_payable(
            sucursal=self.suc_a if sucursal is _SUCURSAL_A else sucursal,
            proveedor=proveedor or self.distribuidora,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=min(periodo, vence),
            fecha_vencimiento=vence,
            periodo_referencia=periodo,
            importe_total=Decimal(importe),
            referencia_comprobante=comprobante,
            actor=self.admin,
        )

    def _pagar(self, payable, monto):
        register_transfer_payment(
            payable=payable, bank_account=self.cuenta, fecha_pago=self.hoy, monto=Decimal(monto), actor=self.admin
        )
        payable.refresh_from_db()
        return payable

    def _cancelar_sin_fondos(self, payable, monto):
        register_payment(
            payable=payable,
            bank_account=None,
            medio_pago=PagoTesoreria.MedioPago.INTERNO,
            fecha_pago=self.hoy,
            monto=Decimal(monto),
            actor=self.admin,
        )
        payable.refresh_from_db()
        return payable

    def _general(self, **kwargs):
        return ReportScope(empresa=self.empresa, **kwargs)

    def _de_sucursal(self, sucursal=None, **kwargs):
        return ReportScope(empresa=self.empresa, sucursal=sucursal or self.suc_a, **kwargs)

    @staticmethod
    def _filas_de_facturas(table):
        return [row for index, row in enumerate(table.rows) if index not in table.group_rows | table.total_rows]


class OpenPayablesBreakdownTests(DeudasFixtureMixin, TestCase):
    def test_paid_and_annulled_are_left_out_and_partial_shows_what_was_paid(self):
        """T4: pagadas y anuladas no aparecen; la parcial muestra pagado = importe - saldo."""
        abierta = self._deuda("1000.00")
        parcial = self._pagar(self._deuda("1500.00"), "400.00")
        pagada = self._pagar(self._deuda("700.00"), "700.00")
        anulada = self._deuda("900.00")
        annul_payable(payable=anulada, motivo="Cargada por error", actor=self.admin)
        self.assertEqual(parcial.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(pagada.estado, CuentaPorPagar.Estado.PAGADA)

        data = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])

        listed = [payable.pk for group in data["groups"] for payable in group["payables"]]
        self.assertCountEqual(listed, [abierta.pk, parcial.pk])
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["importe_total"], Decimal("2500.00"))
        self.assertEqual(data["saldo_total"], Decimal("2100.00"))

        table = build_desglose_deudas(self._general()).table(TABLA)
        fila = next(row for row in self._filas_de_facturas(table) if row[COMPROBANTE] == parcial.referencia_comprobante)
        self.assertEqual(fila[IMPORTE], Decimal("1500.00"))
        self.assertEqual(fila[PAGADO], Decimal("400.00"))
        self.assertEqual(fila[PAGADO], parcial.importe_total - parcial.saldo_pendiente)
        self.assertEqual(fila[SALDO], Decimal("1100.00"))

    def test_groups_by_reference_month_in_ascending_order(self):
        """T5: un grupo por periodo_referencia, ascendente; los subtotales suman el total."""
        self._deuda("300.00", periodo=SEPTIEMBRE)
        self._deuda("100.00", periodo=JULIO)
        self._deuda("200.00", periodo=AGOSTO, proveedor=self.almacen)
        self._deuda("50.00", periodo=JULIO, proveedor=self.almacen)
        self._pagar(self._deuda("80.00", periodo=AGOSTO), "30.00")

        data = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])

        self.assertEqual([group["periodo"] for group in data["groups"]], [JULIO, AGOSTO, SEPTIEMBRE])
        self.assertEqual([group["count"] for group in data["groups"]], [2, 2, 1])
        self.assertEqual(
            [group["saldo_total"] for group in data["groups"]],
            [Decimal("150.00"), Decimal("250.00"), Decimal("300.00")],
        )
        self.assertEqual(
            [group["importe_total"] for group in data["groups"]],
            [Decimal("150.00"), Decimal("280.00"), Decimal("300.00")],
        )
        self.assertEqual(sum(group["saldo_total"] for group in data["groups"]), data["saldo_total"])
        self.assertEqual(sum(group["importe_total"] for group in data["groups"]), data["importe_total"])
        self.assertEqual(sum(group["count"] for group in data["groups"]), data["count"])
        # Dentro del mes, por proveedor.
        julio = data["groups"][0]["payables"]
        self.assertEqual([payable.proveedor for payable in julio], [self.almacen, self.distribuidora])

    def test_overdue_totals_use_today_as_cutoff(self):
        self._deuda("100.00", vence=self.hoy - timedelta(days=5))
        self._deuda("40.00", vence=self.hoy - timedelta(days=1))
        self._deuda("700.00", vence=self.hoy)
        # Vencida y parcial: cuenta lo que le falta pagar, no el importe.
        self._pagar(self._deuda("500.00", vence=self.hoy - timedelta(days=3)), "200.00")

        data = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])

        self.assertEqual(data["reference_date"], self.hoy)
        self.assertEqual(data["vencido_total"], Decimal("440.00"))
        self.assertEqual(data["vencido_count"], 3)
        self.assertEqual(build_desglose_deudas(self._general()).kpi("Vencida").value, Decimal("440.00"))

    def test_reconciles_with_the_financial_snapshot(self):
        """T6: mismo saldo y cantidad que la deuda pendiente de la lectura financiera."""
        self._deuda("1000.00", sucursal=self.suc_a, vence=self.hoy - timedelta(days=3))
        self._deuda("250.00", sucursal=self.suc_b, periodo=AGOSTO)
        self._deuda("75.50", sucursal=None, periodo=JUNIO)
        self._pagar(self._deuda("600.00", sucursal=self.suc_a), "200.00")
        self._pagar(self._deuda("90.00", sucursal=self.suc_b), "90.00")
        self._deuda("5000.00", sucursal=self.suc_otra)
        desde, hasta = self.hoy.replace(day=1), self.hoy

        casos = [
            {"empresa_ids": [self.empresa.pk]},
            {"sucursal": self.suc_a, "empresa_ids": [self.empresa.pk]},
            {"sucursal": self.suc_b, "empresa_ids": [self.empresa.pk]},
            {"empresa_ids": [self.otra_empresa.pk]},
            {"empresa_ids": []},
            {},
        ]
        for alcance in casos:
            with self.subTest(alcance=alcance):
                financial = build_financial_period_snapshot(date_from=desde, date_to=hasta, **alcance)
                data = build_open_payables_breakdown(**alcance)
                self.assertEqual(data["saldo_total"], financial["pending_total"])
                self.assertEqual(data["count"], financial["pending_count"])

        general = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])
        self.assertEqual(general["saldo_total"], Decimal("1725.50"))
        self.assertEqual(general["count"], 4)

    def test_other_company_is_left_out_and_unassigned_debt_only_shows_in_the_general(self):
        """T7: la deuda de otra empresa no aparece; la sin sucursal va solo al general."""
        propia = self._deuda("100.00", sucursal=self.suc_a)
        sin_sucursal = self._deuda("60.00", sucursal=None, comprobante="LEGACY-1")
        ajena = self._deuda("999.00", sucursal=self.suc_otra)

        general = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])
        de_sucursal = build_open_payables_breakdown(sucursal=self.suc_a, empresa_ids=[self.empresa.pk])

        def pks(data):
            return {payable.pk for group in data["groups"] for payable in group["payables"]}

        self.assertEqual(pks(general), {propia.pk, sin_sucursal.pk})
        self.assertEqual(pks(de_sucursal), {propia.pk})
        self.assertNotIn(ajena.pk, pks(general) | pks(de_sucursal))
        self.assertEqual(general["saldo_total"], Decimal("160.00"))
        self.assertEqual(de_sucursal["saldo_total"], Decimal("100.00"))

        table = build_desglose_deudas(self._general()).table(TABLA)
        fila = next(row for row in self._filas_de_facturas(table) if row[COMPROBANTE] == "LEGACY-1")
        self.assertEqual(fila[SUCURSAL], "Sin sucursal")

    def test_unassigned_debt_shows_in_the_general_of_every_company(self):
        """Comportamiento fijado: la deuda sin sucursal no tiene empresa, y con el
        criterio de pending_payables entra en el general de TODAS las empresas."""
        self._deuda("60.00", sucursal=None, comprobante="LEGACY-1")
        self._deuda("999.00", sucursal=self.suc_otra, comprobante="MAPOGO-1")

        doc = build_desglose_deudas(ReportScope(empresa=self.otra_empresa))

        filas = self._filas_de_facturas(doc.table(TABLA))
        self.assertEqual(
            sorted((row[COMPROBANTE], row[SUCURSAL]) for row in filas),
            [("LEGACY-1", "Sin sucursal"), ("MAPOGO-1", "DM1")],
        )
        financial = build_financial_period_snapshot(
            date_from=self.hoy.replace(day=1), date_to=self.hoy, empresa_ids=[self.otra_empresa.pk]
        )
        self.assertEqual(doc.kpi("Deuda abierta").value, financial["pending_total"])
        self.assertEqual(doc.kpi("Deuda abierta").value, Decimal("1059.00"))

    def test_period_filter(self):
        """T8: el filtro de periodos toma meses enteros de periodo_referencia."""
        self._deuda("10.00", periodo=JUNIO)
        self._deuda("20.00", periodo=JULIO)
        self._deuda("30.00", periodo=AGOSTO)
        self._deuda("40.00", periodo=SEPTIEMBRE)

        data = build_open_payables_breakdown(
            empresa_ids=[self.empresa.pk], periodo_desde=date(2026, 7, 15), periodo_hasta=date(2026, 8, 3)
        )

        self.assertEqual([group["periodo"] for group in data["groups"]], [JULIO, AGOSTO])
        self.assertEqual(data["saldo_total"], Decimal("50.00"))
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["periodo_desde"], date(2026, 7, 15))

        solo_desde = build_open_payables_breakdown(empresa_ids=[self.empresa.pk], periodo_desde=AGOSTO)
        self.assertEqual([group["periodo"] for group in solo_desde["groups"]], [AGOSTO, SEPTIEMBRE])

        doc = build_desglose_deudas(self._general(date_from=JULIO, date_to=date(2026, 8, 31)))
        self.assertEqual(doc.kpi("Deuda abierta").value, Decimal("50.00"))
        self.assertEqual(doc.kpi("Meses").value, 2)
        self.assertTrue(doc.periodo_label.endswith(" · períodos 07/2026 a 08/2026"))


class DesgloseDeudasBuilderTests(DeudasFixtureMixin, TestCase):
    def test_groups_subtotals_and_total_are_in_place(self):
        a = self._deuda("100.00", periodo=JULIO, proveedor=self.almacen)
        b = self._deuda("200.00", periodo=JULIO)
        c = self._pagar(self._deuda("500.00", periodo=AGOSTO), "120.00")

        doc = build_desglose_deudas(self._general())
        table = doc.table(TABLA)
        data = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])

        self.assertEqual(table.group_rows, frozenset({0, 4}))
        self.assertEqual(table.total_rows, frozenset({3, 6, 7}))
        self.assertEqual(len(table.rows), 8)
        self.assertEqual(table.rows[0][PROVEEDOR], "Julio 2026 · 2 facturas")
        self.assertEqual(
            [table.rows[1][COMPROBANTE], table.rows[2][COMPROBANTE]],
            [a.referencia_comprobante, b.referencia_comprobante],
        )
        self.assertEqual(table.rows[3][PROVEEDOR], "Subtotal Julio 2026")
        self.assertEqual(table.rows[3][IMPORTE], data["groups"][0]["importe_total"])
        self.assertEqual(table.rows[3][SALDO], Decimal("300.00"))
        self.assertEqual(table.rows[3][PAGADO], "")
        self.assertEqual(table.rows[4][PROVEEDOR], "Agosto 2026 · 1 factura")
        self.assertEqual(table.rows[5][COMPROBANTE], c.referencia_comprobante)
        self.assertEqual(table.rows[6][PROVEEDOR], "Subtotal Agosto 2026")
        self.assertEqual(table.rows[6][IMPORTE], Decimal("500.00"))
        self.assertEqual(table.rows[6][SALDO], Decimal("380.00"))
        self.assertEqual(table.rows[7][PROVEEDOR], "Total")
        self.assertEqual(table.rows[7][IMPORTE], data["importe_total"])
        self.assertEqual(table.rows[7][SALDO], data["saldo_total"])
        self.assertEqual(table.rows[7][SALDO], Decimal("680.00"))

        self.assertEqual(doc.kpi("Deuda abierta").value, data["saldo_total"])
        self.assertEqual(doc.kpi("Deuda abierta").hint, "3 facturas")
        self.assertEqual(doc.kpi("Vencida").value, Decimal("0.00"))
        self.assertEqual(doc.kpi("Vencida").hint, "0 facturas")
        self.assertEqual(doc.kpi("Meses").value, 2)
        self.assertEqual(doc.kpi("Meses").hint, "Julio 2026 a Agosto 2026")
        self.assertEqual(doc.notes, [NOTA_VENCIDA, NOTA_PAGADO])

    def test_invoice_row_cells(self):
        vencida = self._deuda("100.00", vence=self.hoy - timedelta(days=2), comprobante="", concepto="Flete julio")
        al_dia = self._deuda("50.00", vence=self.hoy, comprobante="FC B 0002-00000001")

        doc = build_desglose_deudas(self._general())
        filas = self._filas_de_facturas(doc.table(TABLA))
        por_pk = dict(zip([vencida.pk, al_dia.pk], filas))

        fila = por_pk[vencida.pk]
        self.assertEqual(fila[PROVEEDOR], "Distribuidora Sur")
        self.assertEqual(fila[COMPROBANTE], "Flete julio")
        self.assertEqual(fila[SUCURSAL], "DA1")
        self.assertEqual(fila[EMISION], vencida.fecha_emision)
        self.assertEqual(fila[VENCE], f"{vencida.fecha_vencimiento:%d/%m/%Y} *")
        self.assertEqual(fila[IMPORTE], Decimal("100.00"))
        self.assertEqual(fila[PAGADO], Decimal("0.00"))
        self.assertEqual(fila[SALDO], Decimal("100.00"))

        fila = por_pk[al_dia.pk]
        self.assertEqual(fila[COMPROBANTE], "FC B 0002-00000001")
        self.assertEqual(fila[VENCE], self.hoy)

        self.assertEqual(doc.kpi("Vencida").value, Decimal("100.00"))
        self.assertEqual(doc.kpi("Vencida").hint, "1 factura")

    def test_internal_supplier_is_marked_and_explained(self):
        self._deuda("100.00")
        sin_interno = build_desglose_deudas(self._general())
        self.assertNotIn(NOTA_INTERNO, sin_interno.notes)

        # US-3.20: una factura nueva del proveedor interno nace saldada (INTERNO),
        # asi que no es deuda abierta y no aparece.
        self._deuda("80.00", proveedor=self.panaderia)
        doc = build_desglose_deudas(self._general())
        proveedores = [row[PROVEEDOR] for row in self._filas_de_facturas(doc.table(TABLA))]
        self.assertEqual(proveedores, ["Distribuidora Sur"])
        self.assertNotIn(NOTA_INTERNO, doc.notes)

        # Lo que si queda abierto: facturas cargadas antes de marcar al proveedor como
        # interno, hasta que corra `regularizar_deudas --proveedor-interno`.
        vieja = create_supplier(razon_social="Panaderia Vieja", actor=self.admin)
        self._deuda("60.00", proveedor=vieja)
        set_supplier_internal(supplier=vieja, es_interno=True, actor=self.admin)
        doc = build_desglose_deudas(self._general())
        proveedores = [row[PROVEEDOR] for row in self._filas_de_facturas(doc.table(TABLA))]
        self.assertIn("Panaderia Vieja (interno)", proveedores)
        self.assertIn("Distribuidora Sur", proveedores)
        self.assertIn(NOTA_INTERNO, doc.notes)

    def test_branch_column_only_in_the_general(self):
        self._deuda("100.00")
        general = build_desglose_deudas(self._general())
        de_sucursal = build_desglose_deudas(self._de_sucursal())

        self.assertEqual(
            general.table(TABLA).columns,
            ["Proveedor", "Comprobante", "Sucursal", "Emisión", "Vence", "Importe", "Pagado", "Saldo"],
        )
        self.assertEqual(
            de_sucursal.table(TABLA).columns,
            ["Proveedor", "Comprobante", "Emisión", "Vence", "Importe", "Pagado", "Saldo"],
        )
        self.assertEqual(de_sucursal.table(TABLA).numeric_cols, frozenset({4, 5, 6}))
        self.assertEqual(general.table(TABLA).numeric_cols, frozenset({5, 6, 7}))
        self.assertEqual(de_sucursal.alcance_label, "Sucursal Deudas Uno")
        self.assertEqual(de_sucursal.kpi("Deuda abierta").value, Decimal("100.00"))

    def test_branch_report_only_lists_that_branch(self):
        self._deuda("100.00", sucursal=self.suc_a, comprobante="SUC-A")
        self._deuda("250.00", sucursal=self.suc_b, comprobante="SUC-B")
        self._deuda("75.50", sucursal=None, comprobante="LEGACY-1")
        desde, hasta = self.hoy.replace(day=1), self.hoy

        general = build_desglose_deudas(self._general())
        de_sucursal = build_desglose_deudas(self._de_sucursal())

        financial = build_financial_period_snapshot(
            date_from=desde, date_to=hasta, sucursal=self.suc_a, empresa_ids=[self.empresa.pk]
        )
        self.assertEqual(de_sucursal.kpi("Deuda abierta").value, financial["pending_total"])
        self.assertEqual(de_sucursal.kpi("Deuda abierta").value, Decimal("100.00"))
        self.assertEqual(general.kpi("Deuda abierta").value, Decimal("425.50"))
        # Comprobante es la segunda columna tambien sin la de Sucursal.
        comprobantes = [row[COMPROBANTE] for row in self._filas_de_facturas(de_sucursal.table(TABLA))]
        self.assertEqual(comprobantes, ["SUC-A"])

    def test_cancelled_without_funds_is_explained(self):
        """US-3.20: Pagado (importe - saldo) incluye lo cancelado sin plata; una nota lo aclara."""
        parcial = self._cancelar_sin_fondos(self._deuda("500.00", comprobante="PARCIAL-INT"), "120.00")
        self._pagar(self._deuda("300.00"), "100.00")
        # Cancelada del todo sin fondos: ya no esta abierta y no cuenta.
        cerrada = self._cancelar_sin_fondos(self._deuda("200.00"), "200.00")
        self.assertEqual(parcial.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(cerrada.estado, CuentaPorPagar.Estado.PAGADA)

        data = build_open_payables_breakdown(empresa_ids=[self.empresa.pk])
        self.assertEqual(data["settled_without_funds_total"], Decimal("120.00"))

        doc = build_desglose_deudas(self._general())
        fila = next(row for row in self._filas_de_facturas(doc.table(TABLA)) if row[COMPROBANTE] == "PARCIAL-INT")
        self.assertEqual(fila[PAGADO], Decimal("120.00"))
        self.assertEqual(fila[SALDO], Decimal("380.00"))
        nota = NOTA_SIN_FONDOS.format(monto=format_value(data["settled_without_funds_total"]))
        self.assertEqual(doc.notes, [NOTA_VENCIDA, NOTA_PAGADO, nota])
        self.assertIn("$ 120,00", nota)

    def test_header_labels(self):
        doc = build_desglose_deudas(self._general())
        self.assertEqual(doc.titulo, "Desglose de deudas")
        self.assertEqual(doc.slug, "desglose-deudas")
        self.assertEqual(doc.razon_social, "ARMADI Deudas")
        self.assertEqual(doc.cuit, "30-11111111-1")
        self.assertEqual(doc.periodo_label, f"Deuda abierta al {self.hoy:%d/%m/%Y}")

    def test_no_open_debt(self):
        self._deuda("100.00")
        doc = build_desglose_deudas(ReportScope(empresa=self.otra_empresa))
        table = doc.table(TABLA)
        self.assertEqual(table.rows, [])
        self.assertEqual(table.empty_text, SIN_DEUDAS)
        self.assertEqual(doc.kpi("Deuda abierta").value, Decimal("0.00"))
        self.assertEqual(doc.kpi("Meses").value, 0)
        self.assertEqual(doc.notes, [])

    def test_renders_to_pdf(self):
        for index in range(40):
            periodo = JULIO if index % 2 else AGOSTO
            self._deuda(f"{index + 1}000.00", periodo=periodo, vence=self.hoy - timedelta(days=index))
        self._deuda("80.00", proveedor=self.panaderia, sucursal=None)

        pdf = render_report_pdf([build_desglose_deudas(self._general()), build_desglose_deudas(self._de_sucursal())])
        self.assertTrue(pdf.startswith(b"%PDF"))

        vacio = render_report_pdf([build_desglose_deudas(ReportScope(empresa=self.otra_empresa))])
        self.assertTrue(vacio.startswith(b"%PDF"))

    def test_file_name(self):
        hoy = f"{self.hoy:%Y-%m-%d}"
        self.assertEqual(nombre_archivo(self._general()), f"desglose-deudas_armadi-deudas_general_al_{hoy}.pdf")
        self.assertEqual(
            nombre_archivo(self._de_sucursal(date_from=JULIO, date_to=date(2026, 9, 30))),
            f"desglose-deudas_armadi-deudas_da1_al_{hoy}_periodos_2026-07_2026-09.pdf",
        )

    def test_period_filter_needs_both_months(self):
        with self.assertRaises(ValueError):
            build_desglose_deudas(self._general(date_from=JULIO))
        with self.assertRaises(ValueError):
            nombre_archivo(self._general(date_to=JULIO))
