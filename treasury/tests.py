from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, BrokenBarrierError, Thread
from unittest import skipUnless

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_card_sale, register_cash_income, register_expense
from users.models import Role

from .admin import (
    CategoriaCuentaPagarAdmin,
    CuentaBancariaAdmin,
    CuentaPorPagarAdmin,
    PagoTesoreriaAdmin,
    ProveedorAdmin,
)
from .models import (
    AcreditacionTarjeta,
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    CajaCentral,
    DescuentoAcreditacion,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from .permissions import is_treasury_admin
from .services import (
    annul_payment,
    build_economic_period_snapshot,
    build_financial_period_snapshot,
    build_supplier_history_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_supplier,
    link_payment_to_bank_movement,
    register_card_accreditation,
    register_central_cash_movement,
    register_cheque_payment,
    register_echeq_payment,
    register_payable,
    register_transfer_payment,
)


User = get_user_model()


class TreasuryTestCase(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-treasury", password="test", role=self.admin_role)
        self.superadmin = User.objects.create_user(username="superadmin-treasury", password="test", role=self.admin_role)
        self.superadmin.is_staff = True
        self.superadmin.is_superuser = True
        self.superadmin.save(update_fields=["is_staff", "is_superuser"])
        self.operator = User.objects.create_user(username="operador-treasury", password="test", role=self.operator_role)
        self.category = create_payable_category(nombre="Servicios", actor=self.admin)
        self.supplier = create_supplier(razon_social="Proveedor Uno SA", identificador_fiscal="30-12345678-9", actor=self.admin)
        self.bank_account = create_bank_account(
            nombre="Cuenta principal",
            banco="Banco Uno",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="123-456",
            cbu="2850590940090418135201",
            actor=self.admin,
        )

    def _admin_request(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        return request


class TreasuryPermissionTests(TreasuryTestCase):
    def test_admin_role_is_treasury_admin(self):
        self.assertTrue(is_treasury_admin(self.admin))
        self.assertFalse(is_treasury_admin(self.operator))

    def test_non_admin_cannot_register_supplier(self):
        with self.assertRaises(PermissionDenied):
            create_supplier(razon_social="Proveedor Dos", actor=self.operator)


class TreasuryAdminProtectionTests(TreasuryTestCase):
    def test_treasury_admin_disables_delete_for_all_models(self):
        request = self._admin_request(self.superadmin)
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura administrativa",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("50.00"),
            actor=self.admin,
        )
        admins = (
            ProveedorAdmin(Proveedor, django_admin.site),
            CategoriaCuentaPagarAdmin(CategoriaCuentaPagar, django_admin.site),
            CuentaBancariaAdmin(CuentaBancaria, django_admin.site),
            CuentaPorPagarAdmin(CuentaPorPagar, django_admin.site),
            PagoTesoreriaAdmin(PagoTesoreria, django_admin.site),
        )
        sample_objects = {
            ProveedorAdmin: self.supplier,
            CategoriaCuentaPagarAdmin: self.category,
            CuentaBancariaAdmin: self.bank_account,
            CuentaPorPagarAdmin: payable,
            PagoTesoreriaAdmin: payment,
        }
        for model_admin in admins:
            with self.subTest(model_admin=model_admin.__class__.__name__):
                self.assertFalse(model_admin.has_delete_permission(request))
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_model(request, sample_objects[type(model_admin)])
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_queryset(request, model_admin.model.objects.all())

    def test_read_only_admin_is_kept_for_payables_and_payments(self):
        request = self._admin_request(self.superadmin)
        self.assertFalse(CuentaPorPagarAdmin(CuentaPorPagar, django_admin.site).has_add_permission(request))
        self.assertFalse(PagoTesoreriaAdmin(PagoTesoreria, django_admin.site).has_change_permission(request))


class TreasuryServiceTests(TreasuryTestCase):
    def test_register_payable_sets_full_pending_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura de mercaderia",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("1000.00"),
            actor=self.admin,
        )
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(payable.saldo_pendiente, Decimal("1000.00"))

    def test_register_payable_defaults_period_reference_to_issue_month(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura periodo",
            fecha_emision=timezone.datetime(2026, 4, 18).date(),
            fecha_vencimiento=timezone.datetime(2026, 4, 30).date(),
            importe_total=Decimal("300.00"),
            actor=self.admin,
        )

        self.assertEqual(payable.periodo_referencia.isoformat(), "2026-04-01")

    def test_create_payable_category_can_link_operational_rubro(self):
        rubro = RubroOperativo.objects.create(nombre="Administracion")

        category = create_payable_category(
            nombre="Servicios administrativos",
            rubro_operativo=rubro,
            actor=self.admin,
        )

        self.assertEqual(category.rubro_operativo, rubro)
        self.assertEqual(category.rubro_label, "Administracion")

    def test_partial_payment_recalculates_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Servicio tecnico",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("1000.00"),
            actor=self.admin,
        )
        register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("400.00"),
            actor=self.admin,
        )
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(payable.saldo_pendiente, Decimal("600.00"))

    def test_total_payment_marks_payable_as_paid(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Alquiler",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("800.00"),
            actor=self.admin,
        )
        register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("800.00"),
            actor=self.admin,
        )
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(payable.saldo_pendiente, Decimal("0.00"))

    def test_overpayment_is_rejected(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Mantenimiento",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("300.00"),
            actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            register_transfer_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=timezone.localdate(),
                monto=Decimal("301.00"),
                actor=self.admin,
            )

    def test_cheque_requires_reference(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Equipamiento",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("1500.00"),
            actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            register_cheque_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=timezone.localdate(),
                monto=Decimal("200.00"),
                referencia="",
                actor=self.admin,
            )

    def test_echeq_payment_is_registered_with_bank_account(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Servicio diferido",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("1200.00"),
            actor=self.admin,
        )
        payment = register_echeq_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            fecha_diferida=timezone.localdate() + timedelta(days=3),
            monto=Decimal("200.00"),
            referencia="ECHEQ-200",
            actor=self.admin,
        )
        self.assertEqual(payment.medio_pago, PagoTesoreria.MedioPago.ECHEQ)
        self.assertEqual(payment.cuenta_bancaria, self.bank_account)

    def test_annulling_payment_restores_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Servicios",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("900.00"),
            actor=self.admin,
        )
        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("300.00"),
            actor=self.admin,
        )
        annul_payment(payment=payment, motivo="Pago duplicado", actor=self.admin)
        payable.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual(payment.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(payable.saldo_pendiente, Decimal("900.00"))

    def test_payable_exposes_overdue_visible_state(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura vencida",
            fecha_emision=timezone.localdate() - timedelta(days=5),
            fecha_vencimiento=timezone.localdate() - timedelta(days=1),
            importe_total=Decimal("250.00"),
            actor=self.admin,
        )
        self.assertTrue(payable.esta_vencida)
        self.assertEqual(payable.estado_visible, "VENCIDA")

    def test_payment_direct_save_requires_domain_service(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura directa",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            PagoTesoreria.objects.create(
                cuenta_por_pagar=payable,
                cuenta_bancaria=self.bank_account,
                medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
                fecha_pago=timezone.localdate(),
                monto=Decimal("40.00"),
                creado_por=self.admin,
            )

    def test_bank_movement_requires_category_for_tax_debit(self):
        with self.assertRaises(ValidationError):
            create_bank_movement(
                cuenta_bancaria=self.bank_account,
                tipo=MovimientoBancario.Tipo.DEBITO,
                clase=MovimientoBancario.Clase.IMPUESTO,
                fecha=timezone.localdate(),
                monto=Decimal("45.00"),
                concepto="ARCA",
                actor=self.admin,
            )

    def test_link_payment_to_bank_movement_sets_financial_class_supplier_and_category(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura por transferencia",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("500.00"),
            actor=self.admin,
        )
        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("120.00"),
            referencia="TRF-120",
            actor=self.admin,
        )
        movement = create_bank_movement(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            categoria=self.category,
            fecha=timezone.localdate(),
            monto=Decimal("120.00"),
            concepto="Debito banco",
            referencia="TRF-120",
            actor=self.admin,
        )

        link_payment_to_bank_movement(payment=payment, bank_movement=movement, actor=self.admin)

        movement.refresh_from_db()
        self.assertEqual(movement.origen, MovimientoBancario.Origen.PAGO_TESORERIA)
        self.assertEqual(movement.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)
        self.assertEqual(movement.proveedor, self.supplier)
        self.assertEqual(movement.categoria, self.category)

    def test_register_grouped_card_accreditation_persists_period_and_blocks_obvious_duplicate(self):
        grouped = register_card_accreditation(
            cuenta_bancaria=self.bank_account,
            fecha_acreditacion=timezone.localdate(),
            monto_neto=Decimal("170.00"),
            canal="Payway",
            referencia_externa="LIQ-170",
            modo_registro=AcreditacionTarjeta.ModoRegistro.PERIODO,
            periodo_desde=timezone.localdate() - timedelta(days=6),
            periodo_hasta=timezone.localdate(),
            descuentos=[
                {
                    "tipo": DescuentoAcreditacion.Tipo.COMISION,
                    "monto": Decimal("10.00"),
                    "descripcion": "Comision de servicio",
                }
            ],
            actor=self.admin,
        )

        self.assertEqual(grouped.modo_registro, AcreditacionTarjeta.ModoRegistro.PERIODO)
        self.assertEqual(grouped.periodo_desde, timezone.localdate() - timedelta(days=6))
        self.assertEqual(grouped.periodo_hasta, timezone.localdate())
        self.assertEqual(grouped.movimiento_bancario.clase, MovimientoBancario.Clase.ACREDITACION)

        with self.assertRaises(ValidationError):
            register_card_accreditation(
                cuenta_bancaria=self.bank_account,
                fecha_acreditacion=timezone.localdate(),
                monto_neto=Decimal("170.00"),
                canal="Payway",
                referencia_externa="LIQ-170",
                modo_registro=AcreditacionTarjeta.ModoRegistro.PERIODO,
                periodo_desde=timezone.localdate() - timedelta(days=6),
                periodo_hasta=timezone.localdate(),
                actor=self.admin,
            )

    def test_supplier_history_snapshot_aggregates_payables_and_payments(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura historial",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("500.00"),
            actor=self.admin,
        )
        register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("200.00"),
            actor=self.admin,
        )
        snapshot = build_supplier_history_snapshot(supplier=self.supplier)
        self.assertEqual(snapshot["historical_total"], Decimal("500.00"))
        self.assertEqual(snapshot["historical_pending"], Decimal("300.00"))
        self.assertEqual(snapshot["historical_paid"], Decimal("200.00"))

    def test_financial_period_snapshot_aggregates_cash_bank_accreditations_and_due_buckets(self):
        branch = Sucursal.objects.create(codigo="SUC-T", nombre="Sucursal Test", razon_social="Test SRL")
        branch_account = create_bank_account(
            nombre="Cuenta Sucursal Test",
            banco="Banco Test",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-888",
            sucursal=branch,
            actor=self.admin,
        )
        turno = Turno.objects.create(
            sucursal=branch,
            fecha_operativa=timezone.localdate(),
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box = open_box(
            user=self.operator,
            turno=turno,
            sucursal=branch,
            monto_inicial=Decimal("100.00"),
            actor=self.admin,
        )
        rubro = RubroOperativo.objects.create(nombre="Insumos Tesoreria")
        register_cash_income(
            caja=box,
            monto=Decimal("50.00"),
            categoria="Cobro manual",
            observacion="Ingreso del dia",
            actor=self.operator,
        )
        register_expense(
            caja=box,
            monto=Decimal("20.00"),
            rubro_operativo=rubro,
            categoria="Compra",
            observacion="Egreso del dia",
            actor=self.operator,
        )
        register_card_sale(
            caja=box,
            monto=Decimal("200.00"),
            observacion="Ventas tarjeta",
            actor=self.operator,
        )
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("400.00"),
            concepto="Aporte inicial",
            fecha=timezone.localdate(),
            actor=self.admin,
        )
        create_bank_movement(
            cuenta_bancaria=branch_account,
            tipo=MovimientoBancario.Tipo.CREDITO,
            fecha=timezone.localdate(),
            monto=Decimal("300.00"),
            concepto="Ingreso bancario",
            actor=self.admin,
        )
        create_bank_movement(
            cuenta_bancaria=branch_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=timezone.localdate(),
            monto=Decimal("40.00"),
            concepto="Debito bancario",
            actor=self.admin,
        )
        register_card_accreditation(
            cuenta_bancaria=branch_account,
            fecha_acreditacion=timezone.localdate(),
            monto_neto=Decimal("170.00"),
            canal="Payway",
            referencia_externa="ACC-1",
            descuentos=[
                {"tipo": "COMISION", "monto": Decimal("20.00"), "descripcion": "Comision"},
                {"tipo": "IIBB", "monto": Decimal("10.00"), "descripcion": "IIBB"},
            ],
            actor=self.admin,
        )
        register_payable(
            sucursal=branch,
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura vencida",
            fecha_emision=timezone.localdate() - timedelta(days=3),
            fecha_vencimiento=timezone.localdate() - timedelta(days=1),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        register_payable(
            sucursal=branch,
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura hoy",
            fecha_emision=timezone.localdate() - timedelta(days=1),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("80.00"),
            actor=self.admin,
        )
        register_payable(
            sucursal=branch,
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura proxima",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate() + timedelta(days=3),
            importe_total=Decimal("60.00"),
            actor=self.admin,
        )

        snapshot = build_financial_period_snapshot(
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
        )
        branch_snapshot = build_financial_period_snapshot(
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
            sucursal=branch,
        )

        self.assertEqual(snapshot["cash_income"], Decimal("50.00"))
        self.assertEqual(snapshot["cash_expense"], Decimal("20.00"))
        self.assertEqual(snapshot["cash_net"], Decimal("30.00"))
        self.assertEqual(snapshot["bank_credits"], Decimal("470.00"))
        self.assertEqual(snapshot["bank_debits"], Decimal("40.00"))
        self.assertEqual(snapshot["central_cash_total"], Decimal("400.00"))
        self.assertEqual(snapshot["total_bank_balance"], Decimal("430.00"))
        self.assertEqual(snapshot["total_consolidated"], Decimal("830.00"))
        self.assertEqual(snapshot["digital_sales_total"], Decimal("200.00"))
        self.assertEqual(snapshot["accredited_net"], Decimal("170.00"))
        self.assertEqual(snapshot["accredited_gross"], Decimal("200.00"))
        self.assertEqual(snapshot["pending_accreditation_total"], Decimal("0.00"))
        self.assertEqual(snapshot["overdue_count"], 1)
        self.assertEqual(snapshot["due_today_count"], 1)
        self.assertEqual(snapshot["upcoming_count"], 1)
        self.assertEqual(branch_snapshot["total_bank_balance"], Decimal("430.00"))
        self.assertEqual(branch_snapshot["pending_total"], Decimal("240.00"))

    def test_financial_period_snapshot_uses_grouped_accreditation_coverage_period(self):
        branch = Sucursal.objects.create(codigo="SUC-P", nombre="Sucursal Periodo", razon_social="Periodo SRL")
        branch_account = create_bank_account(
            nombre="Cuenta Periodo",
            banco="Banco Periodo",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="321-654",
            sucursal=branch,
            actor=self.admin,
        )
        turno = Turno.objects.create(
            sucursal=branch,
            fecha_operativa=timezone.localdate(),
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box = open_box(
            user=self.operator,
            turno=turno,
            sucursal=branch,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_card_sale(
            caja=box,
            monto=Decimal("100.00"),
            observacion="Venta con cobertura agrupada",
            actor=self.operator,
        )
        register_card_accreditation(
            cuenta_bancaria=branch_account,
            fecha_acreditacion=timezone.localdate() + timedelta(days=2),
            monto_neto=Decimal("100.00"),
            canal="Payway",
            referencia_externa="PER-100",
            modo_registro=AcreditacionTarjeta.ModoRegistro.PERIODO,
            periodo_desde=timezone.localdate(),
            periodo_hasta=timezone.localdate(),
            actor=self.admin,
        )

        snapshot = build_financial_period_snapshot(
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
            sucursal=branch,
        )

        self.assertEqual(snapshot["digital_sales_total"], Decimal("100.00"))
        self.assertEqual(snapshot["accredited_net"], Decimal("100.00"))
        self.assertEqual(snapshot["pending_accreditation_total"], Decimal("0.00"))

    def test_economic_period_snapshot_groups_sales_cash_expense_and_period_debt_by_rubro(self):
        branch = Sucursal.objects.create(codigo="SUC-E", nombre="Sucursal Economica", razon_social="Economica SRL")
        rubro_admin = RubroOperativo.objects.create(nombre="Administracion")
        rubro_ventas = RubroOperativo.objects.create(nombre="Ventas")
        category_admin = create_payable_category(
            nombre="Servicios administrativos",
            rubro_operativo=rubro_admin,
            actor=self.admin,
        )
        turno = Turno.objects.create(
            sucursal=branch,
            fecha_operativa=timezone.datetime(2026, 4, 20).date(),
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box = open_box(
            user=self.operator,
            turno=turno,
            sucursal=branch,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_cash_income(
            caja=box,
            monto=Decimal("500.00"),
            categoria="Venta mostrador",
            observacion="Ingreso sin rubro",
            actor=self.operator,
        )
        from cashops.models import MovimientoCaja

        MovimientoCaja.objects.create(
            caja=box,
            tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=Decimal("500.00"),
            impacta_saldo_caja=True,
            categoria="Venta rubro",
            rubro_operativo=rubro_ventas,
            creado_por=self.operator,
        )
        register_card_sale(
            caja=box,
            monto=Decimal("250.00"),
            observacion="Tarjeta del periodo",
            actor=self.operator,
        )
        register_expense(
            caja=box,
            monto=Decimal("80.00"),
            rubro_operativo=rubro_admin,
            categoria="Gasto administrativo",
            observacion="Caja del periodo",
            actor=self.operator,
        )
        register_payable(
            sucursal=branch,
            proveedor=self.supplier,
            categoria=category_admin,
            concepto="Factura administrativa",
            fecha_emision=timezone.datetime(2026, 4, 10).date(),
            fecha_vencimiento=timezone.datetime(2026, 4, 25).date(),
            periodo_referencia=timezone.datetime(2026, 4, 1).date(),
            importe_total=Decimal("120.00"),
            actor=self.admin,
        )

        snapshot = build_economic_period_snapshot(
            date_from=timezone.datetime(2026, 4, 1).date(),
            date_to=timezone.datetime(2026, 4, 30).date(),
            sucursal=branch,
        )

        admin_item = next(item for item in snapshot["items"] if item["rubro_nombre"] == "Administracion")
        ventas_item = next(item for item in snapshot["items"] if item["rubro_nombre"] == "Ventas")
        self.assertEqual(snapshot["sales_total"], Decimal("750.00"))
        self.assertEqual(snapshot["cash_expense_total"], Decimal("80.00"))
        self.assertEqual(snapshot["debt_period_total"], Decimal("120.00"))
        self.assertEqual(snapshot["economic_result"], Decimal("550.00"))
        self.assertEqual(snapshot["margin_pct"], Decimal("73.33"))
        self.assertEqual(admin_item["total_expense"], Decimal("200.00"))
        self.assertEqual(admin_item["expense_ratio_over_sales"], Decimal("26.67"))
        self.assertEqual(ventas_item["sales_total"], Decimal("500.00"))


@skipUnless(connection.vendor == "postgresql", "La concurrencia con select_for_update requiere PostgreSQL.")
class TreasuryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(username="admin-treasury-concurrency", password="test", role=self.admin_role)
        self.category = create_payable_category(nombre="Mercaderia", actor=self.admin)
        self.supplier = create_supplier(razon_social="Proveedor Concurrencia SA", identificador_fiscal="30-99999999-9", actor=self.admin)
        self.bank_account = create_bank_account(
            nombre="Cuenta concurrencia",
            banco="Banco Dos",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-123",
            cbu="2850590940090418135210",
            actor=self.admin,
        )
        self.payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Pago concurrente",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )

    def test_concurrent_payments_do_not_overpay_same_payable(self):
        barrier = Barrier(2)
        results = Queue()

        def attempt_payment():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                payment = register_transfer_payment(
                    payable=CuentaPorPagar.objects.get(pk=self.payable.pk),
                    bank_account=CuentaBancaria.objects.get(pk=self.bank_account.pk),
                    fecha_pago=timezone.localdate(),
                    monto=Decimal("60.00"),
                    actor=self.admin,
                )
                results.put(("ok", payment.pk))
            except (ValidationError, BrokenBarrierError) as exc:
                results.put(("error", exc))
            finally:
                close_old_connections()

        threads = [Thread(target=attempt_payment), Thread(target=attempt_payment)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        outcomes = [results.get_nowait() for _ in range(results.qsize())]
        success_count = sum(1 for status, _ in outcomes if status == "ok")
        error_count = sum(1 for status, _ in outcomes if status == "error")
        self.payable.refresh_from_db()
        self.assertEqual(success_count, 1)
        self.assertEqual(error_count, 1)
        self.assertEqual(self.payable.saldo_pendiente, Decimal("40.00"))


class TreasuryViewTests(TreasuryTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_supplier_list_filters_by_text_query(self):
        create_supplier(razon_social="Otro proveedor", actor=self.admin)
        response = self.client.get(reverse("treasury:proveedores_list"), {"q": "Uno"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Proveedor Uno SA")
        self.assertNotContains(response, "Otro proveedor")

    def test_supplier_create_persists_record(self):
        response = self.client.post(
            reverse("treasury:proveedores_create"),
            {
                "razon_social": "Proveedor Nuevo SRL",
                "identificador_fiscal": "30-22222222-2",
                "contacto": "Compras",
                "telefono": "555-1234",
                "email": "compras@proveedornuevo.com",
                "alias_bancario": "",
                "cbu": "",
                "observaciones": "Alta desde vista",
                "activo": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Proveedor.objects.filter(razon_social="Proveedor Nuevo SRL").exists())

    def test_payable_create_starts_with_full_balance(self):
        response = self.client.post(
            reverse("treasury:cuentas_por_pagar_create"),
            {
                "proveedor": self.supplier.pk,
                "categoria": self.category.pk,
                "concepto": "Factura mensual",
                "referencia_comprobante": "F-0001",
                "fecha_emision": timezone.localdate(),
                "fecha_vencimiento": timezone.localdate(),
                "importe_total": "250.00",
                "observaciones": "Carga manual",
            },
        )
        self.assertEqual(response.status_code, 302)
        payable = CuentaPorPagar.objects.get(concepto="Factura mensual")
        self.assertEqual(payable.saldo_pendiente, Decimal("250.00"))
        self.assertEqual(payable.periodo_referencia, timezone.localdate().replace(day=1))

    def test_transfer_payment_create_reduces_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura de servicio",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("400.00"),
            actor=self.admin,
        )
        response = self.client.post(
            reverse("treasury:pagos_transferencia_create"),
            {
                "cuenta_por_pagar": payable.pk,
                "cuenta_bancaria": self.bank_account.pk,
                "fecha_pago": timezone.localdate(),
                "monto": "150.00",
                "referencia": "TRF-150",
                "observaciones": "Primer pago",
            },
        )
        self.assertEqual(response.status_code, 302)
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(payable.saldo_pendiente, Decimal("250.00"))

    def test_cash_payment_create_reduces_balance_and_creates_central_movement(self):
        payable = register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura en efectivo",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("400.00"),
            actor=self.admin,
        )
        response = self.client.post(
            reverse("treasury:pagos_efectivo_create"),
            {
                "cuenta_por_pagar": payable.pk,
                "fecha_pago": timezone.localdate(),
                "monto": "150.00",
                "observaciones": "Pago interno en caja",
            },
        )
        self.assertEqual(response.status_code, 302)
        payable.refresh_from_db()
        payment = PagoTesoreria.objects.get(cuenta_por_pagar=payable, medio_pago=PagoTesoreria.MedioPago.EFECTIVO)
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(payable.saldo_pendiente, Decimal("250.00"))
        self.assertIsNone(payment.cuenta_bancaria)
        self.assertTrue(MovimientoCajaCentral.objects.filter(pago_tesoreria=payment).exists())

    def test_bank_movement_create_persists_financial_taxonomy(self):
        response = self.client.post(
            reverse("treasury:bank_movements_create"),
            {
                "cuenta_bancaria": self.bank_account.pk,
                "tipo": MovimientoBancario.Tipo.DEBITO,
                "clase": MovimientoBancario.Clase.IMPUESTO,
                "categoria": self.category.pk,
                "proveedor": "",
                "fecha": timezone.localdate(),
                "monto": "85.00",
                "concepto": "ARCA abril",
                "referencia": "IMP-85",
                "observaciones": "Carga tributaria",
            },
        )

        self.assertEqual(response.status_code, 302)
        movement = MovimientoBancario.objects.get(referencia="IMP-85")
        self.assertEqual(movement.clase, MovimientoBancario.Clase.IMPUESTO)
        self.assertEqual(movement.categoria, self.category)

    def test_grouped_accreditation_create_persists_period_metadata(self):
        response = self.client.post(
            reverse("treasury:card_accreditations_register"),
            {
                "modo_registro": AcreditacionTarjeta.ModoRegistro.PERIODO,
                "cuenta_bancaria": self.bank_account.pk,
                "fecha_acreditacion": timezone.localdate(),
                "periodo_desde": (timezone.localdate() - timedelta(days=6)).isoformat(),
                "periodo_hasta": timezone.localdate().isoformat(),
                "monto_neto": "210.00",
                "canal": "Payway",
                "referencia_externa": "AGR-210",
                "lote_pos": "",
                "monto_descuentos": "10.00",
                "descripcion_descuentos": "Comision agrupada",
            },
        )

        self.assertEqual(response.status_code, 302)
        accreditation = AcreditacionTarjeta.objects.get(referencia_externa="AGR-210")
        self.assertEqual(accreditation.modo_registro, AcreditacionTarjeta.ModoRegistro.PERIODO)
        self.assertEqual(accreditation.periodo_desde, timezone.localdate() - timedelta(days=6))
        self.assertEqual(accreditation.periodo_hasta, timezone.localdate())

    def test_non_admin_is_blocked_from_treasury_dashboard(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("treasury:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_supports_period_and_branch_financial_view(self):
        branch = Sucursal.objects.create(codigo="SUC-D", nombre="Sucursal Dashboard", razon_social="Dashboard SRL")
        branch_account = create_bank_account(
            nombre="Cuenta Dashboard",
            banco="Banco Dashboard",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="456-789",
            sucursal=branch,
            actor=self.admin,
        )
        turno = Turno.objects.create(
            sucursal=branch,
            fecha_operativa=timezone.localdate(),
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box = open_box(
            user=self.operator,
            turno=turno,
            sucursal=branch,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        rubro = RubroOperativo.objects.create(nombre="Dashboard Rubro")
        category = create_payable_category(
            nombre="Servicios Dashboard",
            rubro_operativo=rubro,
            actor=self.admin,
        )
        register_cash_income(
            caja=box,
            monto=Decimal("120.00"),
            categoria="Ingreso caja",
            observacion="Caja del periodo",
            actor=self.operator,
        )
        register_expense(
            caja=box,
            monto=Decimal("30.00"),
            rubro_operativo=rubro,
            categoria="Compra caja",
            observacion="Caja del periodo",
            actor=self.operator,
        )
        register_card_sale(
            caja=box,
            monto=Decimal("150.00"),
            observacion="Tarjeta dashboard",
            actor=self.operator,
        )
        create_bank_movement(
            cuenta_bancaria=branch_account,
            tipo=MovimientoBancario.Tipo.CREDITO,
            fecha=timezone.localdate(),
            monto=Decimal("90.00"),
            concepto="Credito dashboard",
            actor=self.admin,
        )
        register_card_accreditation(
            cuenta_bancaria=branch_account,
            fecha_acreditacion=timezone.localdate(),
            monto_neto=Decimal("120.00"),
            canal="Payway",
            referencia_externa="ACC-DASH",
            descuentos=[
                {"tipo": "COMISION", "monto": Decimal("10.00"), "descripcion": "Comision dashboard"},
            ],
            actor=self.admin,
        )
        register_payable(
            sucursal=branch,
            proveedor=self.supplier,
            categoria=category,
            concepto="Vence hoy dashboard",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("70.00"),
            actor=self.admin,
        )

        response = self.client.get(
            reverse("treasury:dashboard"),
            {
                "sucursal": branch.pk,
                "fecha_desde": timezone.localdate().isoformat(),
                "fecha_hasta": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Situacion financiera por periodo")
        self.assertContains(response, "Situacion economica y rentabilidad")
        self.assertContains(response, "Resultado economico")
        self.assertContains(response, "Dashboard Rubro")
        self.assertContains(response, "Caja fuerte general")
        self.assertContains(response, "Pendiente de acreditacion")
        self.assertContains(response, "Vence hoy")
        self.assertContains(response, "$ 120,00")
        self.assertContains(response, "$ 30,00")
        self.assertContains(response, "$ 150,00")
