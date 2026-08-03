"""El pago tiene que bajar la disponibilidad de donde salio la plata.

Antes de este slice solo el pago en EFECTIVO movia algo (la caja fuerte).
Transferencia, cheque y ECHEQ bajaban el saldo_pendiente de la deuda pero NO
tocaban el banco, asi que el saldo bancario y el KPI de cobertura de deuda
quedaban sistematicamente optimistas por el monto de todo lo pagado.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
)
from treasury.services import (
    annul_payment,
    build_financial_period_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_supplier,
    link_payment_to_bank_movement,
    register_cash_payment,
    register_cheque_payment,
    register_echeq_payment,
    register_payable,
    register_payment,
    register_transfer_payment,
    set_initial_bank_balance,
)
from users.models import Role

User = get_user_model()


class PaymentBankImpactTests(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(
            username="admin-banco", password="test", role=self.admin_role
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Banco SA")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            nombre="Sucursal Banco",
            codigo="SB01",
            razon_social="Empresa Banco SA",
            empresa=self.empresa,
        )
        self.rubro = RubroOperativo.objects.create(nombre="Servicios banco")
        self.category = create_payable_category(
            nombre="Servicios banco", rubro_operativo=self.rubro, actor=self.admin
        )
        self.supplier = create_supplier(
            razon_social="Proveedor Banco SA",
            identificador_fiscal="30-99887766-5",
            actor=self.admin,
        )
        self.bank_account = create_bank_account(
            nombre="Cuenta operativa",
            banco="Banco Test",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-111",
            cbu="2850590940090418135201",
            empresa=self.empresa,
            actor=self.admin,
        )
        self.fecha = timezone.localdate()

    def _payable(self, importe="1000.00", *, sucursal=True):
        return register_payable(
            sucursal=self.sucursal if sucursal else None,
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura del proveedor",
            fecha_emision=self.fecha,
            fecha_vencimiento=self.fecha + timedelta(days=10),
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def test_transfer_payment_creates_the_bank_debit(self):
        payable = self._payable()

        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("1000.00"),
            referencia="TRF-1",
            actor=self.admin,
        )

        movement = MovimientoBancario.objects.get(pagos=payment)
        self.assertEqual(movement.tipo, MovimientoBancario.Tipo.DEBITO)
        self.assertEqual(movement.origen, MovimientoBancario.Origen.PAGO_TESORERIA)
        self.assertEqual(movement.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)
        self.assertTrue(movement.generado_por_pago)
        self.assertEqual(movement.monto, Decimal("1000.00"))
        self.assertEqual(movement.cuenta_bancaria, self.bank_account)
        self.assertEqual(movement.fecha, self.fecha)
        self.assertEqual(movement.estado, MovimientoBancario.Estado.REGISTRADO)
        # Imputacion heredada de la deuda: sin esto el clean() del modelo lo rechaza.
        self.assertEqual(movement.proveedor, self.supplier)
        self.assertEqual(movement.categoria, self.category)
        self.assertEqual(movement.rubro_operativo, self.rubro)
        self.assertEqual(movement.sucursal_gasto, self.sucursal)
        self.assertEqual(movement.periodo_pago, payable.periodo_referencia)
        payment.refresh_from_db()
        self.assertEqual(payment.estado_bancario, PagoTesoreria.EstadoBancario.IMPACTADO)

    def test_cheque_and_echeq_payments_use_their_own_class(self):
        cheque = register_cheque_payment(
            payable=self._payable(),
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("1000.00"),
            referencia="CH-1",
            actor=self.admin,
        )
        echeq = register_echeq_payment(
            payable=self._payable(),
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("1000.00"),
            referencia="ECH-1",
            actor=self.admin,
        )

        self.assertEqual(
            MovimientoBancario.objects.get(pagos=cheque).clase,
            MovimientoBancario.Clase.CHEQUE,
        )
        self.assertEqual(
            MovimientoBancario.objects.get(pagos=echeq).clase,
            MovimientoBancario.Clase.ECHEQ,
        )

    def test_cash_payment_does_not_touch_the_bank(self):
        """El efectivo sale de la caja fuerte, no del banco: comportamiento intacto."""
        payment = register_cash_payment(
            payable=self._payable(),
            fecha_pago=self.fecha,
            monto=Decimal("1000.00"),
            actor=self.admin,
        )

        self.assertFalse(MovimientoBancario.objects.filter(pagos=payment).exists())
        self.assertTrue(
            MovimientoCajaCentral.objects.filter(
                pago_tesoreria=payment,
                tipo=MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            ).exists()
        )

    def test_payment_still_registers_when_the_debt_has_no_branch(self):
        """No bloquear una cobranza por un dato de catalogo faltante: si la deuda no
        tiene sucursal, el clean() rechazaria el debito, asi que no se crea y el pago
        queda con estado_bancario PENDIENTE para resolverlo desde Vincular a pago."""
        payable = self._payable(sucursal=False)

        payment = register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("1000.00"),
            referencia="TRF-SIN-SUC",
            actor=self.admin,
        )

        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertFalse(MovimientoBancario.objects.filter(pagos=payment).exists())
        payment.refresh_from_db()
        self.assertEqual(payment.estado_bancario, PagoTesoreria.EstadoBancario.PENDIENTE)

    def test_bank_balance_and_debt_coverage_reflect_the_payment(self):
        """El punto del arreglo: antes la deuda bajaba y el banco no, asi que la
        cobertura de deuda quedaba optimista por el monto de todo lo pagado."""
        set_initial_bank_balance(
            cuenta_bancaria=self.bank_account,
            fecha_referencia=self.fecha,
            importe=Decimal("5000.00"),
            motivo="Base",
            actor=self.admin,
        )
        payable = self._payable("2000.00")

        antes = build_financial_period_snapshot(date_from=self.fecha, date_to=self.fecha)
        self.assertEqual(antes["total_bank_balance"], Decimal("5000.00"))
        self.assertEqual(antes["pending_total"], Decimal("2000.00"))

        register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("2000.00"),
            referencia="TRF-COBERTURA",
            actor=self.admin,
        )

        despues = build_financial_period_snapshot(date_from=self.fecha, date_to=self.fecha)
        self.assertEqual(despues["pending_total"], Decimal("0.00"))
        # La plata salio del banco de verdad: 5000 - 2000.
        self.assertEqual(despues["total_bank_balance"], Decimal("3000.00"))
        self.assertEqual(despues["bank_debits"], Decimal("2000.00"))

    def test_annulling_the_payment_annuls_the_auto_generated_debit(self):
        """El debito autogenerado nunca existio en el banco: al anular el pago se
        anula tambien, si no inflaria el egreso y contaria el gasto dos veces (la
        deuda ya lo conto al cargarse, y un debito MANUAL cuenta por si mismo)."""
        set_initial_bank_balance(
            cuenta_bancaria=self.bank_account,
            fecha_referencia=self.fecha,
            importe=Decimal("5000.00"),
            motivo="Base",
            actor=self.admin,
        )
        payment = register_transfer_payment(
            payable=self._payable("2000.00"),
            bank_account=self.bank_account,
            fecha_pago=self.fecha,
            monto=Decimal("2000.00"),
            referencia="TRF-ANULAR",
            actor=self.admin,
        )
        movement_id = MovimientoBancario.objects.get(pagos=payment).pk

        annul_payment(
            payment=payment, motivo="Se cargo el proveedor equivocado", actor=self.admin
        )

        movement = MovimientoBancario.objects.get(pk=movement_id)
        self.assertEqual(movement.estado, MovimientoBancario.Estado.ANULADO)
        self.assertIn("Se cargo el proveedor equivocado", movement.motivo_anulacion)
        self.assertEqual(movement.anulado_por, self.admin)
        self.assertFalse(movement.pagos.exists())
        self.assertFalse(movement.generado_por_pago)
        # El banco vuelve a su saldo: el egreso anulado no cuenta en ningun total.
        snapshot = build_financial_period_snapshot(date_from=self.fecha, date_to=self.fecha)
        self.assertEqual(snapshot["total_bank_balance"], Decimal("5000.00"))
        self.assertEqual(snapshot["bank_debits"], Decimal("0.00"))

    def test_annulling_a_payment_linked_by_hand_releases_without_annulling(self):
        """Contracara: si alguien cargo el debito a mano (lo vio en el resumen del
        banco) y despues lo vinculo, esa plata SI salio. Al anular el pago el
        movimiento se libera a MANUAL y sigue vigente: borrarlo es decision suya."""
        # Pago PARCIAL a proposito: link_payment_to_bank_movement re-guarda el pago
        # y el clean() de PagoTesoreria rechaza cualquier re-guardado cuando la deuda
        # quedo PAGADA ("La cuenta por pagar ya esta cancelada"). Es una limitacion
        # preexistente de esa pantalla, ajena a este slice.
        payable = self._payable("5000.00")
        manual = create_bank_movement(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.fecha,
            monto=Decimal("1500.00"),
            concepto="Debito visto en el resumen del banco",
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=self.fecha.replace(day=1),
            actor=self.admin,
        )
        self.assertFalse(manual.generado_por_pago)
        payment = register_payment(
            payable=payable,
            bank_account=self.bank_account,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=self.fecha,
            monto=Decimal("1500.00"),
            referencia="TRF-MANUAL",
            actor=self.admin,
        )
        # El pago autogenero su propio debito; para este escenario se descarta ese y
        # se usa el que la persona cargo a mano, como haria desde la pantalla.
        auto = MovimientoBancario.objects.filter(pagos=payment).first()
        self.assertIsNotNone(auto)
        payment.movimiento_bancario = None
        payment.save(skip_domain_guard=True)
        auto.origen = MovimientoBancario.Origen.MANUAL
        auto.generado_por_pago = False
        auto.estado = MovimientoBancario.Estado.ANULADO
        auto.motivo_anulacion = "Se usa el debito cargado a mano"
        auto.save()
        payment.refresh_from_db()
        link_payment_to_bank_movement(
            payment=payment, bank_movement=manual, actor=self.admin
        )

        annul_payment(payment=payment, motivo="Error de imputacion", actor=self.admin)

        manual.refresh_from_db()
        self.assertEqual(manual.estado, MovimientoBancario.Estado.REGISTRADO)
        self.assertEqual(manual.origen, MovimientoBancario.Origen.MANUAL)
        self.assertFalse(manual.pagos.exists())
        self.assertIn("anulado", manual.observaciones.lower())
