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

from users.models import Role

from .admin import (
    CategoriaCuentaPagarAdmin,
    CuentaBancariaAdmin,
    CuentaPorPagarAdmin,
    PagoTesoreriaAdmin,
    ProveedorAdmin,
)
from .models import CategoriaCuentaPagar, CuentaBancaria, CuentaPorPagar, PagoTesoreria, Proveedor
from .permissions import is_treasury_admin
from .services import (
    annul_payment,
    build_supplier_history_snapshot,
    create_bank_account,
    create_payable_category,
    create_supplier,
    register_cheque_payment,
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

    def test_non_admin_is_blocked_from_treasury_dashboard(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("treasury:dashboard"))
        self.assertEqual(response.status_code, 403)
