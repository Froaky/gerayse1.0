from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from treasury.models import (
    CajaCentral, MovimientoCajaCentral, CierreMensualTesoreria,
    CuentaPorPagar, Proveedor, CategoriaCuentaPagar, PagoTesoreria,
    CuentaBancaria
)
from treasury.services import (
    register_cash_payment, register_central_cash_movement,
    build_disponibilidades_snapshot, close_treasury_month,
    register_arqueo, get_or_create_default_caja_central
)

User = get_user_model()

class EP05DisponibilidadesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin", password="password", email="admin@test.com")
        self.supplier = Proveedor.objects.create(razon_social="Test Supplier", creado_por=self.user)
        self.category = CategoriaCuentaPagar.objects.create(nombre="Test Category", creado_por=self.user)
        self.bank_account = CuentaBancaria.objects.create(
            nombre="Banco Test", banco="Galicia", tipo_cuenta="CC", numero_cuenta="123", creado_por=self.user
        )

    def test_get_or_create_default_caja_central(self):
        caja = get_or_create_default_caja_central()
        self.assertEqual(caja.nombre, "Efectivo Central")
        self.assertEqual(CajaCentral.objects.count(), 1)

    def test_register_central_cash_movement(self):
        m = register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Aporte inicial",
            actor=self.user
        )
        self.assertEqual(m.monto, Decimal("1000.00"))
        self.assertEqual(CajaCentral.objects.first().saldo_actual, Decimal("1000.00"))

    def test_register_cash_payment_triggers_central_movement(self):
        payable = CuentaPorPagar.objects.create(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Compra",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            periodo_referencia=timezone.localdate().replace(day=1),
            importe_total=Decimal("500.00"),
            saldo_pendiente=Decimal("500.00"),
            creado_por=self.user
        )
        
        # Add some initial cash
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            actor=self.user
        )
        
        payment = register_cash_payment(
            payable=payable,
            fecha_pago=timezone.localdate(),
            monto=Decimal("500.00"),
            actor=self.user
        )
        
        self.assertEqual(payment.medio_pago, PagoTesoreria.MedioPago.EFECTIVO)
        self.assertEqual(CajaCentral.objects.first().saldo_actual, Decimal("500.00"))
        
        # Check movement exists
        move = MovimientoCajaCentral.objects.filter(pago_tesoreria=payment).first()
        self.assertIsNotNone(move)
        self.assertEqual(move.tipo, MovimientoCajaCentral.Tipo.EGRESO_PAGO)

    def test_build_disponibilidades_snapshot(self):
        today = timezone.localdate()
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            fecha=today,
            actor=self.user
        )
        
        snapshot = build_disponibilidades_snapshot(today.year, today.month)
        self.assertEqual(snapshot["saldo_final_efectivo"], Decimal("1000.00"))
        self.assertEqual(snapshot["total_consolidado"], Decimal("1000.00"))

    def test_close_treasury_month(self):
        today = timezone.localdate()
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            fecha=today,
            actor=self.user
        )
        
        closing = close_treasury_month(today.year, today.month, actor=self.user)
        self.assertTrue(closing.cerrado)
        self.assertEqual(closing.saldo_final_efectivo, Decimal("1000.00"))
        
        # Ensure it can't be closed twice
        with self.assertRaises(Exception):
            close_treasury_month(today.year, today.month, actor=self.user)

    def test_arqueo_calculates_difference(self):
        caja = get_or_create_default_caja_central()
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            actor=self.user
        )
        
        arqueo = register_arqueo(
            caja_central=caja,
            saldo_contado=Decimal("950.00"),
            actor=self.user
        )
        
        self.assertEqual(arqueo.saldo_sistema_efectivo, Decimal("1000.00"))
        self.assertEqual(arqueo.diferencia, Decimal("-50.00"))
