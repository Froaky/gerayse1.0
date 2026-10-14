"""Reporte 2: situacion financiera (slice S3).

Cada valor del documento tiene que ser igual, en Decimal, a su clave de
`build_financial_period_snapshot` para el mismo alcance. Los tests comparan
contra el snapshot y no recalculan nada.
"""

import re
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from cashops.models import Empresa, RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_card_sale, register_cash_income, register_expense
from users.models import Role

from .forms import EgresoTesoreriaForm
from .models import CuentaBancaria, DescuentoAcreditacion, MovimientoBancario, MovimientoCajaCentral
from .reportes.alcance import ReportScope
from .reportes.documento import TableBlock
from .reportes.financiera import (
    SLUG,
    TITULO,
    build_situacion_financiera,
    nombre_archivo,
)
from .reportes.pdf import render_report_pdf
from .services import (
    annul_payable,
    build_financial_period_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_supplier,
    register_card_accreditation,
    register_cash_payment,
    register_central_cash_movement,
    register_egreso_tesoreria,
    register_payable,
    register_transfer_payment,
    set_initial_bank_balance,
)

User = get_user_model()

DESDE = date(2026, 9, 1)
HASTA = date(2026, 9, 30)
PERIODO = date(2026, 9, 1)

KPIS_GENERAL = [
    ("Caja fuerte", "central_cash_total"),
    ("Bancos", "total_bank_balance"),
    ("Total disponible", "total_consolidated"),
    ("Pendiente de acreditación", "pending_accreditation_total"),
    ("Deuda pendiente", "pending_total"),
    ("Banco menos deuda", "debt_vs_bank_difference"),
]
KPIS_SUCURSAL = [
    ("Caja fuerte", "central_cash_total"),
    ("Bancos", "total_bank_balance"),
    ("Total disponible", "total_consolidated"),
    ("Deuda pendiente", "pending_total"),
]
MOVIMIENTOS_GENERAL = [
    ("Cajas de sucursal", None),
    ("Caja ingresos", "cash_income"),
    ("Caja egresos", "cash_expense"),
    ("Caja neto", "cash_net"),
    ("Caja fuerte", None),
    ("Entradas caja fuerte", "central_cash_income_period"),
    ("Salidas caja fuerte", "central_cash_expense_period"),
    ("Gastos caja fuerte (incluidos en las salidas)", "central_cash_admin_expense_period"),
    ("Neto caja fuerte", "central_cash_net_period"),
    ("Banco", None),
    ("Banco créditos", "bank_credits"),
    ("Banco débitos", "bank_debits"),
    ("Banco neto", "bank_net"),
]
NETOS = {"Caja neto", "Neto caja fuerte", "Banco neto"}
MOVIMIENTOS_SUCURSAL = [
    fila for fila in MOVIMIENTOS_GENERAL if fila[0] not in {"Banco créditos", "Banco neto"}
]
NOTA_INICIAL = (
    "Saldos de caja fuerte y bancos al 30/09/2026; la deuda pendiente es la vigente a la emisión "
    "del reporte. Los movimientos son del 01/09/2026 al 30/09/2026."
)
VENCIMIENTOS = [
    ("Crítico (vencidas y menos de 4 días)", "red_count", "red_total"),
    ("Próximo (4 a 7 días)", "yellow_count", "yellow_total"),
    ("En plazo (8 días o más)", "green_count", "green_total"),
    ("Total", "pending_count", "pending_total"),
]


class SituacionFinancieraFixtureMixin:
    """Septiembre 2026 de ARMADI con dos sucursales: Belgrano (sin cuenta propia)
    y Palermo (con cuenta propia). La cuenta principal es de la empresa."""

    def setUp(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-financiera", password="test", role=admin_role)
        self.operator = User.objects.create_user(username="cajero-financiera", password="test", role=operator_role)
        self.empresa = Empresa.objects.create(nombre="ARMADI SRL", identificador_fiscal="30-11111111-1")
        self.otra = Empresa.objects.create(nombre="MAPOGO SRL")
        self.admin.empresas_permitidas.set([self.empresa, self.otra])
        self.operator.empresas_permitidas.set([self.empresa, self.otra])
        self.belgrano = Sucursal.objects.create(
            codigo="EB", nombre="Estación Belgrano", razon_social="ARMADI SRL", empresa=self.empresa
        )
        self.palermo = Sucursal.objects.create(
            codigo="PA", nombre="Palermo", razon_social="ARMADI SRL", empresa=self.empresa
        )
        self.vivre = Sucursal.objects.create(
            codigo="VIVRE", nombre="Vivre", razon_social="MAPOGO SRL", empresa=self.otra
        )
        self.rubro = RubroOperativo.objects.create(nombre="Servicios")
        self.categoria = create_payable_category(nombre="Servicios", rubro_operativo=self.rubro, actor=self.admin)
        self.proveedor = create_supplier(
            razon_social="Proveedor Uno SA", identificador_fiscal="30-12345678-9", actor=self.admin
        )

        # Banco: cuenta de la empresa con saldo inicial y cuenta propia de Palermo.
        self.cuenta = create_bank_account(
            nombre="Cuenta corriente",
            banco="Banco Nación",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="111-1",
            empresa=self.empresa,
            actor=self.admin,
        )
        set_initial_bank_balance(
            cuenta_bancaria=self.cuenta,
            fecha_referencia=date(2026, 8, 31),
            importe=Decimal("1000.00"),
            motivo="Saldo de apertura",
            actor=self.admin,
        )
        self._credito(self.cuenta, date(2026, 9, 5), Decimal("500.00"))
        self.cuenta_palermo = create_bank_account(
            nombre="Caja de ahorro Palermo",
            banco="Banco Galicia",
            tipo_cuenta=CuentaBancaria.Tipo.CAJA_AHORRO,
            numero_cuenta="222-2",
            empresa=self.empresa,
            sucursal=self.palermo,
            actor=self.admin,
        )
        self._credito(self.cuenta_palermo, date(2026, 9, 8), Decimal("300.00"))
        create_bank_movement(
            cuenta_bancaria=self.cuenta_palermo,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=date(2026, 9, 9),
            monto=Decimal("40.00"),
            concepto="Mantenimiento de cuenta",
            rubro_operativo=self.rubro,
            sucursal_gasto=self.palermo,
            periodo_pago=PERIODO,
            actor=self.admin,
        )
        # Movimiento posterior al corte: no entra ni en saldo ni en flujo.
        self._credito(self.cuenta, date(2026, 10, 3), Decimal("9999.00"))

        # Caja de Belgrano.
        turno = Turno.objects.create(empresa=self.empresa, tipo=Turno.Tipo.MANANA, creado_por=self.admin)
        caja = open_box(
            user=self.operator,
            turno=turno,
            sucursal=self.belgrano,
            fecha_operativa=date(2026, 9, 10),
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_cash_income(caja=caja, monto=Decimal("150.00"), categoria="Cobro", actor=self.operator)
        register_expense(
            caja=caja, monto=Decimal("45.00"), rubro_operativo=self.rubro, categoria="Compra", actor=self.operator
        )
        register_card_sale(caja=caja, monto=Decimal("250.00"), actor=self.operator)
        # Acreditacion parcial con descuento: lo pendiente no es igual a lo vendido con tarjeta.
        register_card_accreditation(
            cuenta_bancaria=self.cuenta,
            fecha_acreditacion=date(2026, 9, 15),
            monto_neto=Decimal("100.00"),
            canal="Visa",
            referencia_externa="LIQ-0915",
            descuentos=[
                {"tipo": DescuentoAcreditacion.Tipo.COMISION, "monto": Decimal("10.00"), "descripcion": "Comision"}
            ],
            actor=self.admin,
        )

        # Caja fuerte: un ingreso de Belgrano anterior al periodo (el saldo al corte no es
        # el neto del periodo), aporte de la empresa, ingreso desde Belgrano y gasto imputado.
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.INGRESO_CAJA,
            monto=Decimal("500.00"),
            concepto="Retiro de caja Belgrano de agosto",
            fecha=date(2026, 8, 20),
            sucursal_origen=self.belgrano,
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("2000.00"),
            concepto="Aporte de socios",
            fecha=date(2026, 9, 2),
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.INGRESO_CAJA,
            monto=Decimal("120.00"),
            concepto="Retiro de caja Belgrano",
            fecha=date(2026, 9, 10),
            sucursal_origen=self.belgrano,
            actor=self.admin,
        )
        register_egreso_tesoreria(
            empresa=self.empresa,
            fuente=EgresoTesoreriaForm.FUENTE_CAJA,
            fecha=date(2026, 9, 12),
            monto=Decimal("70.00"),
            concepto="Limpieza",
            rubro=self.rubro,
            sucursal=self.belgrano,
            periodo=PERIODO,
            actor=self.admin,
        )

        # Deudas al 30/09: critico < 04/10, proximo 04/10 a 07/10, en plazo desde 08/10.
        self._deuda(self.belgrano, date(2026, 9, 20), Decimal("100.00"))
        parcial = self._deuda(self.belgrano, date(2026, 10, 2), Decimal("80.00"))
        register_transfer_payment(
            payable=parcial,
            bank_account=self.cuenta,
            fecha_pago=date(2026, 9, 25),
            monto=Decimal("30.00"),
            actor=self.admin,
        )
        self._deuda(self.belgrano, date(2026, 10, 5), Decimal("60.00"))
        self._deuda(self.palermo, date(2026, 10, 15), Decimal("200.00"))
        anulada = self._deuda(self.belgrano, date(2026, 9, 15), Decimal("999.00"))
        annul_payable(payable=anulada, motivo="Cargada dos veces", actor=self.admin)
        pagada = self._deuda(self.palermo, date(2026, 9, 18), Decimal("150.00"))
        register_transfer_payment(
            payable=pagada,
            bank_account=self.cuenta_palermo,
            fecha_pago=date(2026, 9, 18),
            monto=Decimal("150.00"),
            actor=self.admin,
        )
        # Pagada en efectivo: una salida de caja fuerte que no es gasto (EGRESO_PAGO).
        pagada_efectivo = self._deuda(self.belgrano, date(2026, 9, 14), Decimal("90.00"))
        register_cash_payment(
            payable=pagada_efectivo, fecha_pago=date(2026, 9, 14), monto=Decimal("90.00"), actor=self.admin
        )

    def _credito(self, cuenta, fecha, monto):
        return create_bank_movement(
            cuenta_bancaria=cuenta,
            tipo=MovimientoBancario.Tipo.CREDITO,
            fecha=fecha,
            monto=monto,
            concepto="Transferencia recibida",
            actor=self.admin,
        )

    def _deuda(self, sucursal, vencimiento, importe):
        return register_payable(
            sucursal=sucursal,
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto=f"Factura {importe}",
            fecha_emision=date(2026, 9, 1),
            fecha_vencimiento=vencimiento,
            importe_total=importe,
            actor=self.admin,
        )

    def _scope(self, sucursal=None, empresa=None):
        return ReportScope(empresa=empresa or self.empresa, sucursal=sucursal, date_from=DESDE, date_to=HASTA)

    def _snapshot(self, sucursal=None, empresa=None):
        return build_financial_period_snapshot(
            date_from=DESDE,
            date_to=HASTA,
            sucursal=sucursal,
            empresa_ids=[(empresa or self.empresa).pk],
        )

    def _assert_movements(self, table: TableBlock, expected, snapshot):
        self.assertEqual([row[0] for row in table.rows], [label for label, _ in expected])
        grupos = {index for index, (_, key) in enumerate(expected) if key is None}
        netos = {index for index, (label, _) in enumerate(expected) if label in NETOS}
        self.assertEqual(table.group_rows, frozenset(grupos))
        self.assertEqual(table.total_rows, frozenset(netos))
        for index, (label, key) in enumerate(expected):
            if key is None:
                continue
            with self.subTest(concepto=label):
                self.assertIsInstance(table.rows[index][1], Decimal)
                self.assertEqual(table.rows[index][1], snapshot[key])


class SituacionFinancieraGeneralTests(SituacionFinancieraFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.doc = build_situacion_financiera(self._scope())
        self.snapshot = self._snapshot()

    def test_header_comes_from_the_scope(self):
        self.assertEqual(self.doc.slug, SLUG)
        self.assertEqual(self.doc.titulo, TITULO)
        self.assertEqual(self.doc.razon_social, "ARMADI SRL")
        self.assertEqual(self.doc.cuit, "30-11111111-1")
        self.assertEqual(self.doc.alcance_label, "Alcance: General (todas las sucursales)")
        self.assertEqual(self.doc.periodo_label, "Período: 01/09/2026 al 30/09/2026")

    def test_kpis_equal_the_snapshot(self):
        self.assertEqual([kpi.label for kpi in self.doc.kpis], [label for label, _ in KPIS_GENERAL])
        for label, key in KPIS_GENERAL:
            with self.subTest(kpi=label):
                self.assertIsInstance(self.doc.kpi(label).value, Decimal)
                self.assertEqual(self.doc.kpi(label).value, self.snapshot[key])

    def test_fixture_moves_every_number(self):
        # Sanidad del escenario: ningun valor comparado es un cero trivial.
        for key in [
            "central_cash_total",
            "total_bank_balance",
            "pending_accreditation_total",
            "pending_total",
            "debt_vs_bank_difference",
            "cash_income",
            "cash_expense",
            "central_cash_income_period",
            "central_cash_expense_period",
            "central_cash_admin_expense_period",
            "bank_credits",
            "bank_debits",
            "red_total",
            "yellow_total",
            "green_total",
        ]:
            with self.subTest(key=key):
                self.assertNotEqual(self.snapshot[key], Decimal("0.00"))
        # Claves que el escenario separa para que un cruce entre ellas no pase inadvertido.
        for key, otra in [
            ("central_cash_total", "central_cash_net_period"),
            ("central_cash_expense_period", "central_cash_admin_expense_period"),
            ("pending_accreditation_total", "digital_sales_total"),
        ]:
            with self.subTest(key=key, otra=otra):
                self.assertNotEqual(self.snapshot[key], self.snapshot[otra])

    def test_debt_hint_and_coverage_hint(self):
        self.assertEqual(self.snapshot["pending_count"], 4)
        self.assertEqual(self.doc.kpi("Deuda pendiente").hint, "4 facturas")
        self.assertTrue(self.snapshot["debt_vs_bank_covered"])
        self.assertEqual(
            self.doc.kpi("Banco menos deuda").hint,
            "Saldo bancario al 30/09/2026 menos la deuda a la emisión: el banco cubre la deuda pendiente.",
        )

    def test_cash_hints_in_the_general(self):
        self.assertEqual(self.doc.kpi("Caja fuerte").hint, "Efectivo al 30/09/2026")
        self.assertEqual(self.doc.kpi("Total disponible").hint, "Caja fuerte más bancos")

    def test_coverage_hint_when_the_bank_does_not_cover_the_debt(self):
        self._deuda(self.belgrano, date(2026, 10, 20), Decimal("50000.00"))
        doc = build_situacion_financiera(self._scope())
        snapshot = self._snapshot()
        self.assertFalse(snapshot["debt_vs_bank_covered"])
        self.assertEqual(doc.kpi("Banco menos deuda").value, snapshot["debt_vs_bank_difference"])
        self.assertEqual(
            doc.kpi("Banco menos deuda").hint,
            "Saldo bancario al 30/09/2026 menos la deuda a la emisión: "
            "el banco no alcanza para cubrir la deuda pendiente.",
        )

    def test_first_block_states_the_cut_date_and_the_period(self):
        self.assertEqual(self.doc.notes[0], NOTA_INICIAL)

    def test_bank_balances_rows_equal_the_snapshot(self):
        table = self.doc.table("Saldos bancarios")
        self.assertEqual(table.columns, ["Cuenta", "Saldo inicial", "Créditos", "Débitos", "Saldo"])
        balances = self.snapshot["bank_balances"]
        self.assertEqual(len(balances), 2)
        self.assertEqual(len(table.rows), len(balances) + 1)
        for row, item in zip(table.rows, balances):
            with self.subTest(cuenta=str(item["account"])):
                self.assertEqual(row[0], str(item["account"]))
                self.assertEqual(row[1:], [item["initial_amount"], item["credits"], item["debits"], item["balance"]])
        self.assertEqual(table.rows[-1], ["Total", "", "", "", self.snapshot["total_bank_balance"]])
        self.assertEqual(table.total_rows, frozenset({len(balances)}))
        self.assertIn("Cuenta corriente - Banco Nación", [row[0] for row in table.rows])

    def test_period_movements_equal_the_snapshot(self):
        table = self.doc.table("Movimientos del período")
        self.assertEqual(table.columns, ["Concepto", "Importe"])
        self._assert_movements(table, MOVIMIENTOS_GENERAL, self.snapshot)

    def test_due_dates_rows_equal_the_snapshot(self):
        table = self.doc.table("Vencimientos de deuda")
        self.assertEqual(table.columns, ["Plazo", "Facturas", "Importe"])
        self.assertEqual(
            table.rows,
            [[label, self.snapshot[count], self.snapshot[total]] for label, count, total in VENCIMIENTOS],
        )
        self.assertEqual(table.total_rows, frozenset({3}))

    def test_due_dates_buckets_add_up_to_what_the_snapshot_says(self):
        # Escenario: critico = vencida 100 + parcial (80 - 30); proximo 60; en plazo 200.
        # Anulada y pagadas (transferencia y efectivo) no figuran.
        table = self.doc.table("Vencimientos de deuda")
        self.assertEqual([row[1] for row in table.rows], [2, 1, 1, 4])
        self.assertEqual(
            [row[2] for row in table.rows],
            [Decimal("150.00"), Decimal("60.00"), Decimal("200.00"), Decimal("410.00")],
        )
        self.assertEqual(table.rows[-1][2], self.snapshot["pending_total"])

    def test_general_notes_explain_balance_versus_flow(self):
        notes = " ".join(self.doc.notes)
        self.assertIn("no los del período", notes)
        self.assertIn("no solo las del período", notes)
        self.assertIn("Banco menos deuda usa solo el saldo bancario", notes)

    def test_renders_a_pdf(self):
        pdf = render_report_pdf([self.doc])
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page(?!s)", pdf)), 1)

    def test_file_name(self):
        self.assertEqual(
            nombre_archivo(self._scope()), "situacion-financiera_armadi-srl_general_2026-09_2026-09.pdf"
        )
        self.assertEqual(
            nombre_archivo(self._scope(sucursal=self.belgrano)),
            "situacion-financiera_armadi-srl_eb_2026-09_2026-09.pdf",
        )


class SituacionFinancieraSucursalTests(SituacionFinancieraFixtureMixin, TestCase):
    def test_branch_without_own_account_equals_its_snapshot(self):
        doc = build_situacion_financiera(self._scope(sucursal=self.belgrano))
        snapshot = self._snapshot(sucursal=self.belgrano)

        self.assertEqual(doc.alcance_label, "Sucursal: Estación Belgrano")
        self.assertEqual([kpi.label for kpi in doc.kpis], [label for label, _ in KPIS_SUCURSAL])
        for label, key in KPIS_SUCURSAL:
            with self.subTest(kpi=label):
                self.assertEqual(doc.kpi(label).value, snapshot[key])
        self.assertEqual(doc.kpi("Deuda pendiente").hint, "3 facturas")
        # El ingreso de agosto separa el saldo imputado al corte del neto del periodo.
        self.assertNotEqual(snapshot["central_cash_total"], snapshot["central_cash_net_period"])
        self.assertEqual(
            doc.kpi("Caja fuerte").hint, "Neto de movimientos imputados a la sucursal al 30/09/2026"
        )
        self.assertEqual(
            doc.kpi("Total disponible").hint, "Caja fuerte imputada más cuentas propias de la sucursal"
        )
        for oculto in ["Pendiente de acreditación", "Banco menos deuda"]:
            with self.assertRaises(KeyError):
                doc.kpi(oculto)

        bancos = doc.table("Saldos bancarios")
        self.assertEqual(snapshot["bank_balances"], [])
        self.assertEqual(bancos.rows, [])
        self.assertEqual(
            bancos.empty_text, "La cuenta bancaria es de la empresa: su saldo figura en el reporte General."
        )

        movimientos = doc.table("Movimientos del período")
        self._assert_movements(movimientos, MOVIMIENTOS_SUCURSAL, snapshot)
        conceptos = [row[0] for row in movimientos.rows]
        self.assertNotIn("Banco créditos", conceptos)
        self.assertNotIn("Banco neto", conceptos)
        # El debito automatico del pago parcial esta imputado a Belgrano.
        self.assertEqual(snapshot["bank_debits"], Decimal("30.00"))

        self.assertEqual(
            doc.table("Vencimientos de deuda").rows,
            [[label, snapshot[count], snapshot[total]] for label, count, total in VENCIMIENTOS],
        )
        self.assertEqual(doc.notes[0], NOTA_INICIAL)
        self.assertIn("Por sucursal, el banco muestra solo los débitos imputados", " ".join(doc.notes))

    def test_branch_with_own_account_lists_it(self):
        doc = build_situacion_financiera(self._scope(sucursal=self.palermo))
        snapshot = self._snapshot(sucursal=self.palermo)

        for label, key in KPIS_SUCURSAL:
            with self.subTest(kpi=label):
                self.assertEqual(doc.kpi(label).value, snapshot[key])
        self.assertEqual(doc.kpi("Deuda pendiente").hint, "1 factura")
        bancos = doc.table("Saldos bancarios")
        self.assertEqual(len(snapshot["bank_balances"]), 1)
        item = snapshot["bank_balances"][0]
        self.assertEqual(
            bancos.rows,
            [
                [str(item["account"]), item["initial_amount"], item["credits"], item["debits"], item["balance"]],
                ["Total", "", "", "", snapshot["total_bank_balance"]],
            ],
        )
        self._assert_movements(doc.table("Movimientos del período"), MOVIMIENTOS_SUCURSAL, snapshot)

    def test_general_and_branches_render_in_one_pdf(self):
        docs = [
            build_situacion_financiera(self._scope()),
            build_situacion_financiera(self._scope(sucursal=self.belgrano)),
            build_situacion_financiera(self._scope(sucursal=self.palermo)),
        ]
        pdf = render_report_pdf(docs)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page(?!s)", pdf)), 3)


class SituacionFinancieraAislamientoTests(SituacionFinancieraFixtureMixin, TestCase):
    def _valores(self, doc):
        return (
            [(kpi.label, kpi.value, kpi.hint) for kpi in doc.kpis],
            [block.rows for block in doc.blocks if isinstance(block, TableBlock)],
        )

    def test_other_company_debt_bank_and_cash_do_not_change_the_general(self):
        antes = self._valores(build_situacion_financiera(self._scope()))

        cuenta_mapogo = create_bank_account(
            nombre="Cuenta Mapogo",
            banco="Banco Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="333-3",
            empresa=self.otra,
            actor=self.admin,
        )
        self._credito(cuenta_mapogo, date(2026, 9, 6), Decimal("7000.00"))
        deuda_mapogo = self._deuda(self.vivre, date(2026, 9, 21), Decimal("5000.00"))
        register_transfer_payment(
            payable=deuda_mapogo,
            bank_account=cuenta_mapogo,
            fecha_pago=date(2026, 9, 22),
            monto=Decimal("1000.00"),
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.otra,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("800.00"),
            concepto="Aporte Mapogo",
            fecha=date(2026, 9, 3),
            actor=self.admin,
        )

        self.assertEqual(self._valores(build_situacion_financiera(self._scope())), antes)
        # Y el general de la otra empresa si lo ve.
        doc_mapogo = build_situacion_financiera(self._scope(empresa=self.otra))
        snapshot_mapogo = self._snapshot(empresa=self.otra)
        self.assertEqual(doc_mapogo.razon_social, "MAPOGO SRL")
        self.assertEqual(doc_mapogo.kpi("Deuda pendiente").value, snapshot_mapogo["pending_total"])
        self.assertEqual(doc_mapogo.kpi("Deuda pendiente").value, Decimal("4000.00"))
        self.assertEqual(doc_mapogo.kpi("Bancos").value, snapshot_mapogo["total_bank_balance"])
        self.assertEqual(doc_mapogo.kpi("Caja fuerte").value, snapshot_mapogo["central_cash_total"])
        self.assertEqual(
            [row[0] for row in doc_mapogo.table("Saldos bancarios").rows], ["Cuenta Mapogo - Banco Macro", "Total"]
        )


class SituacionFinancieraVaciaTests(TestCase):
    def test_company_without_movements_renders_zeros_and_empty_tables(self):
        empresa = Empresa.objects.create(nombre="Empresa Nueva SA")
        doc = build_situacion_financiera(ReportScope(empresa=empresa, date_from=DESDE, date_to=HASTA))
        self.assertEqual(doc.kpi("Total disponible").value, Decimal("0.00"))
        self.assertEqual(doc.kpi("Deuda pendiente").hint, "0 facturas")
        self.assertEqual(doc.table("Saldos bancarios").rows, [])
        self.assertEqual(
            doc.table("Saldos bancarios").empty_text, "No hay cuentas bancarias activas para este alcance."
        )
        self.assertTrue(render_report_pdf([doc]).startswith(b"%PDF"))
