"""Token de alta en las altas de plata de tesoreria.

Mismo contrato que en cashops: cada render de formulario lleva un token oculto
unico; si vuelve el mismo token (doble click, reintento tras un timeout, volver
atras y reenviar), el servicio devuelve lo ya creado en lugar de mover plata de
nuevo. Tres capas: short-circuit en el servicio ANTES de cualquier lock,
savepoint sobre la carrera, y constraint parcial unica en la base con mensaje
humano.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import (
    create_bank_movement,
    register_carga_inicial_caja_central,
    register_cash_payment,
    register_central_cash_movement,
    register_egreso_tesoreria,
    register_payable,
    register_supplier_payment_batch,
    register_transfer_payment,
)

User = get_user_model()


class TreasuryTokenAltaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-token", password="test", email="t@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Token")
        self.sucursal = Sucursal.objects.create(
            codigo="TOK", nombre="Sucursal Token", razon_social="Token SRL", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Servicios Token")
        self.proveedor = Proveedor.objects.create(razon_social="Proveedor Token SA", creado_por=self.admin)
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria Token", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta Token", banco="Banco Token", empresa=self.empresa, creado_por=self.admin
        )
        self.hoy = timezone.localdate()

    def _deuda(self, importe="1000.00", referencia=""):
        return register_payable(
            sucursal=self.sucursal,
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Insumos",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy + timedelta(days=30),
            importe_total=Decimal(importe),
            referencia_comprobante=referencia,
            actor=self.admin,
        )

    def test_resend_transfer_payment_does_not_duplicate(self):
        deuda = self._deuda()
        token = uuid4()
        kwargs = dict(
            payable=deuda,
            bank_account=self.cuenta,
            fecha_pago=self.hoy,
            monto=Decimal("400.00"),
            referencia="TRF-1",
            token_alta=token,
            actor=self.admin,
        )
        primero = register_transfer_payment(**kwargs)
        segundo = register_transfer_payment(**kwargs)

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(PagoTesoreria.objects.count(), 1)
        deuda.refresh_from_db()
        self.assertEqual(deuda.saldo_pendiente, Decimal("600.00"))
        # El debito bancario del pago tambien quedo uno solo.
        self.assertEqual(
            MovimientoBancario.objects.filter(pago_tesoreria=primero).count(), 1
        )

    def test_new_token_creates_second_identical_payment(self):
        deuda = self._deuda()
        base = dict(
            payable=deuda,
            bank_account=self.cuenta,
            fecha_pago=self.hoy,
            monto=Decimal("100.00"),
            actor=self.admin,
        )
        register_transfer_payment(token_alta=uuid4(), **base)
        register_transfer_payment(token_alta=uuid4(), **base)
        self.assertEqual(PagoTesoreria.objects.count(), 2)

    def test_resend_cash_payment_hits_boveda_once(self):
        deuda = self._deuda()
        register_carga_inicial_caja_central(
            empresa=self.empresa, fecha=self.hoy, monto=Decimal("5000.00"),
            motivo="Fondeo inicial", actor=self.admin,
        )
        token = uuid4()
        kwargs = dict(
            payable=deuda,
            fecha_pago=self.hoy,
            monto=Decimal("300.00"),
            empresa=self.empresa,
            token_alta=token,
            actor=self.admin,
        )
        primero = register_cash_payment(**kwargs)
        segundo = register_cash_payment(**kwargs)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(
            MovimientoCajaCentral.objects.filter(
                tipo=MovimientoCajaCentral.Tipo.EGRESO_PAGO, pago_tesoreria=primero
            ).count(),
            1,
        )

    def test_resend_batch_does_not_pay_again(self):
        deuda_1 = self._deuda(referencia="F-1")
        deuda_2 = self._deuda(referencia="F-2")
        token = uuid4()
        kwargs = dict(
            proveedor=self.proveedor,
            lineas=[(deuda_1, Decimal("200.00")), (deuda_2, Decimal("300.00"))],
            bank_account=self.cuenta,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=self.hoy,
            referencia="LOTE-1",
            token_alta=token,
            actor=self.admin,
        )
        pagos = register_supplier_payment_batch(**kwargs)
        self.assertEqual(len(pagos), 2)

        register_supplier_payment_batch(**kwargs)
        self.assertEqual(PagoTesoreria.objects.count(), 2)
        deuda_1.refresh_from_db()
        deuda_2.refresh_from_db()
        self.assertEqual(deuda_1.saldo_pendiente, Decimal("800.00"))
        self.assertEqual(deuda_2.saldo_pendiente, Decimal("700.00"))

    def test_resend_manual_bank_movement(self):
        token = uuid4()
        kwargs = dict(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.CREDITO,
            fecha=self.hoy,
            monto=Decimal("150.00"),
            concepto="Acreditacion manual",
            token_alta=token,
            actor=self.admin,
        )
        primero = create_bank_movement(**kwargs)
        segundo = create_bank_movement(**kwargs)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(MovimientoBancario.objects.count(), 1)

    def test_resend_egreso_tesoreria_both_sources(self):
        register_carga_inicial_caja_central(
            empresa=self.empresa, fecha=self.hoy, monto=Decimal("5000.00"),
            motivo="Fondeo", actor=self.admin,
        )
        base = dict(
            empresa=self.empresa,
            fecha=self.hoy,
            monto=Decimal("120.00"),
            concepto="Libreria",
            rubro=self.rubro,
            sucursal=self.sucursal,
            periodo=self.hoy,
            actor=self.admin,
        )
        token_caja = uuid4()
        a = register_egreso_tesoreria(fuente="CAJA_CENTRAL", token_alta=token_caja, **base)
        b = register_egreso_tesoreria(fuente="CAJA_CENTRAL", token_alta=token_caja, **base)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(
            MovimientoCajaCentral.objects.filter(tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN).count(), 1
        )

        token_banco = uuid4()
        c = register_egreso_tesoreria(
            fuente="BANCO", cuenta_bancaria=self.cuenta, token_alta=token_banco, **base
        )
        d = register_egreso_tesoreria(
            fuente="BANCO", cuenta_bancaria=self.cuenta, token_alta=token_banco, **base
        )
        self.assertEqual(c.pk, d.pk)
        self.assertEqual(
            MovimientoBancario.objects.filter(origen=MovimientoBancario.Origen.EGRESO_TESORERIA).count(), 1
        )

    def test_resend_carga_inicial(self):
        token = uuid4()
        kwargs = dict(
            empresa=self.empresa, fecha=self.hoy, monto=Decimal("9000.00"),
            motivo="Apertura de boveda", token_alta=token, actor=self.admin,
        )
        primero = register_carga_inicial_caja_central(**kwargs)
        segundo = register_carga_inicial_caja_central(**kwargs)
        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(MovimientoCajaCentral.objects.count(), 1)

    def test_db_rejects_two_payments_with_same_token(self):
        deuda = self._deuda()
        token = uuid4()
        register_transfer_payment(
            payable=deuda, bank_account=self.cuenta, fecha_pago=self.hoy,
            monto=Decimal("100.00"), token_alta=token, actor=self.admin,
        )
        clon = PagoTesoreria(
            cuenta_por_pagar=deuda,
            cuenta_bancaria=self.cuenta,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=self.hoy,
            monto=Decimal("100.00"),
            token_alta=token,
            creado_por=self.admin,
        )
        # El save de PagoTesoreria pasa por full_clean: el duplicado sale como
        # ValidationError con el mensaje humano de la constraint.
        from django.core.exceptions import ValidationError

        with self.assertRaisesMessage(ValidationError, "Este pago ya fue registrado."):
            with transaction.atomic():
                clon.save(skip_domain_guard=True)

    def test_payment_form_renders_hidden_token(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("treasury:pagos_transferencia_create"))
        self.assertContains(response, 'name="token_alta"')
