from decimal import Decimal
from datetime import timedelta
from queue import Queue
from threading import Barrier, BrokenBarrierError, Thread
from unittest import skipUnless

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

from users.models import Role

from .admin import CuentaBancariaAdmin, CuentaPorPagarAdmin, PagoTesoreriaAdmin, ProveedorAdmin
from .models import CuentaBancaria, CuentaPorPagar, PagoTesoreria, Proveedor
from .permissions import is_treasury_admin
from .services import (
    annul_payment,
    create_bank_account,
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
        self.superadmin = User.objects.create_user(
            username="superadmin-treasury",
            password="test",
            role=self.admin_role,
        )
        self.superadmin.is_staff = True
        self.superadmin.is_superuser = True
        self.superadmin.save(update_fields=["is_staff", "is_superuser"])
        self.operator = User.objects.create_user(
            username="operador-treasury",
            password="test",
            role=self.operator_role,
        )
        self.supplier = create_supplier(
            razon_social="Proveedor Uno SA",
            identificador_fiscal="30-12345678-9",
            actor=self.admin,
        )
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
            CuentaBancariaAdmin(CuentaBancaria, django_admin.site),
            CuentaPorPagarAdmin(CuentaPorPagar, django_admin.site),
            PagoTesoreriaAdmin(PagoTesoreria, django_admin.site),
        )

        for model_admin in admins:
            with self.subTest(model_admin=model_admin.__class__.__name__):
                self.assertFalse(model_admin.has_delete_permission(request))
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_model(
                        request,
                        {
                            ProveedorAdmin: self.supplier,
                            CuentaBancariaAdmin: self.bank_account,
                            CuentaPorPagarAdmin: payable,
                            PagoTesoreriaAdmin: payment,
                        }[type(model_admin)],
                    )
                with self.assertRaises(PermissionDenied):
                    model_admin.delete_queryset(request, model_admin.model.objects.all())

    def test_cuenta_por_pagar_and_pago_tesoreria_are_read_only_in_admin(self):
        request = self._admin_request(self.superadmin)

        payable_admin = CuentaPorPagarAdmin(CuentaPorPagar, django_admin.site)
        payment_admin = PagoTesoreriaAdmin(PagoTesoreria, django_admin.site)

        self.assertFalse(payable_admin.has_add_permission(request))
        self.assertFalse(payable_admin.has_change_permission(request))
        self.assertFalse(payable_admin.has_delete_permission(request))
        self.assertEqual(payable_admin.inlines, [])

        self.assertFalse(payment_admin.has_add_permission(request))
        self.assertFalse(payment_admin.has_change_permission(request))
        self.assertFalse(payment_admin.has_delete_permission(request))

    def test_treasury_admin_models_keep_regular_write_access_for_masters_but_block_delete(self):
        request = self._admin_request(self.superadmin)

        supplier_admin = ProveedorAdmin(Proveedor, django_admin.site)
        bank_admin = CuentaBancariaAdmin(CuentaBancaria, django_admin.site)

        self.assertTrue(supplier_admin.has_add_permission(request))
        self.assertTrue(supplier_admin.has_change_permission(request))
        self.assertFalse(supplier_admin.has_delete_permission(request))

        self.assertTrue(bank_admin.has_add_permission(request))
        self.assertTrue(bank_admin.has_change_permission(request))
        self.assertFalse(bank_admin.has_delete_permission(request))


class TreasuryServiceTests(TreasuryTestCase):
    def test_supplier_duplicate_tax_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            supplier = Proveedor(
                razon_social="Proveedor Duplicado",
                identificador_fiscal="30-12345678-9",
                creado_por=self.admin,
            )
            supplier.full_clean()

    def test_register_payable_sets_full_pending_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
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
            concepto="Servicio tecnico",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("1000.00"),
            actor=self.admin,
        )

        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=timezone.localdate(),
            monto=Decimal("400.00"),
            actor=self.admin,
        )

        payable.refresh_from_db()
        self.assertEqual(payment.estado, PagoTesoreria.Estado.REGISTRADO)
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(payable.saldo_pendiente, Decimal("600.00"))

    def test_total_payment_marks_payable_as_paid(self):
        payable = register_payable(
            proveedor=self.supplier,
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

    def test_inactive_bank_account_is_rejected(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Insumos",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("200.00"),
            actor=self.admin,
        )
        self.bank_account.activa = False
        self.bank_account.save(update_fields=["activa", "actualizado_en"])

        with self.assertRaises(ValidationError):
            register_transfer_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=timezone.localdate(),
                monto=Decimal("100.00"),
                actor=self.admin,
            )

    def test_cannot_pay_annulled_payable(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Impuestos",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("500.00"),
            actor=self.admin,
        )
        payable.estado = CuentaPorPagar.Estado.ANULADA
        payable.motivo_anulacion = "Carga errada"
        payable.anulada_por = self.admin
        payable.anulada_en = timezone.now()
        payable.save(
            update_fields=["estado", "motivo_anulacion", "anulada_por", "anulada_en", "actualizado_en"]
        )

        with self.assertRaises(ValidationError):
            register_transfer_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=timezone.localdate(),
                monto=Decimal("100.00"),
                actor=self.admin,
            )

    def test_annulling_payment_restores_balance(self):
        payable = register_payable(
            proveedor=self.supplier,
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

    def test_cheque_requires_reference(self):
        payable = register_payable(
            proveedor=self.supplier,
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

    def test_payable_exposes_overdue_visible_state(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Factura vencida",
            fecha_emision=timezone.localdate() - timedelta(days=5),
            fecha_vencimiento=timezone.localdate() - timedelta(days=1),
            importe_total=Decimal("250.00"),
            actor=self.admin,
        )

        self.assertTrue(payable.esta_vencida)
        self.assertEqual(payable.estado_visible, "VENCIDA")

    def test_payment_model_rejects_overpayment_on_full_clean(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Honorarios",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        payment = PagoTesoreria(
            cuenta_por_pagar=payable,
            cuenta_bancaria=self.bank_account,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=timezone.localdate(),
            monto=Decimal("150.00"),
            creado_por=self.admin,
        )

        with self.assertRaises(ValidationError):
            payment.full_clean()

    def test_payment_model_rejects_annulled_payable_on_full_clean(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Factura anulada",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        payable.estado = CuentaPorPagar.Estado.ANULADA
        payable.saldo_pendiente = Decimal("0.00")
        payable.motivo_anulacion = "Error de carga"
        payable.anulada_por = self.admin
        payable.anulada_en = timezone.now()
        payable.save(
            update_fields=["estado", "saldo_pendiente", "motivo_anulacion", "anulada_por", "anulada_en"]
        )

        payment = PagoTesoreria(
            cuenta_por_pagar=payable,
            cuenta_bancaria=self.bank_account,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=timezone.localdate(),
            monto=Decimal("50.00"),
            creado_por=self.admin,
        )

        with self.assertRaises(ValidationError):
            payment.full_clean()

    def test_payment_model_rejects_inactive_bank_account_on_full_clean(self):
        payable = register_payable(
            proveedor=self.supplier,
            concepto="Servicios varios",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("180.00"),
            actor=self.admin,
        )
        self.bank_account.activa = False
        self.bank_account.save(update_fields=["activa", "actualizado_en"])

        payment = PagoTesoreria(
            cuenta_por_pagar=payable,
            cuenta_bancaria=self.bank_account,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=timezone.localdate(),
            monto=Decimal("50.00"),
            creado_por=self.admin,
        )

        with self.assertRaises(ValidationError):
            payment.full_clean()

    def test_payment_direct_save_requires_domain_service(self):
        payable = register_payable(
            proveedor=self.supplier,
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

        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(payable.saldo_pendiente, Decimal("100.00"))
        self.assertEqual(payable.pagos.count(), 0)


@skipUnless(connection.vendor == "postgresql", "La concurrencia con select_for_update requiere PostgreSQL.")
class TreasuryConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(
            username="admin-treasury-concurrency",
            password="test",
            role=self.admin_role,
        )
        self.supplier = create_supplier(
            razon_social="Proveedor Concurrencia SA",
            identificador_fiscal="30-99999999-9",
            actor=self.admin,
        )
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
            except Exception as exc:
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
        self.assertEqual(
            PagoTesoreria.objects.filter(
                cuenta_por_pagar=self.payable,
                estado=PagoTesoreria.Estado.REGISTRADO,
            ).count(),
            1,
        )
        self.assertEqual(self.payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(self.payable.saldo_pendiente, Decimal("40.00"))
