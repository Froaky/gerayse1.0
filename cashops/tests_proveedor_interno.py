"""US-3.20, slice 3: el gasto como deuda del proveedor interno, desde la caja.

La cajera carga las facturas de la panaderia propia (Proveedor.es_interno)
como "gasto como deuda". Esas facturas nunca se pagan: la deuda nace saldada
con una cancelacion INTERNO, sin mover fondos y aunque la cajera no tenga
permiso de tesoreria. Anular la caja o eliminar ese gasto como deuda anula
tambien la cancelacion; un pago con plata sigue bloqueando como siempre.
"""
import uuid
from decimal import Decimal
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.urls import reverse

from cashops.models import Caja
from cashops.services import (
    TEXTO_DEUDA_PROVEEDOR_INTERNO,
    annul_box,
    annul_box_originated_debt,
    open_box,
    register_box_expense_debt,
)
from cashops.tests import CashopsTestCase
from treasury.models import CuentaPorPagar, MovimientoCajaCentral, PagoTesoreria
from treasury.services import (
    create_payable_category,
    create_supplier,
    register_cash_payment,
    register_central_cash_movement,
    register_payment,
    set_supplier_internal,
)
from users.models import Role

User = get_user_model()

INTERNO = PagoTesoreria.MedioPago.INTERNO


class InternoCajaBase(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.cajero = User.objects.create_user(
            username="cajera-panaderia",
            password="test",
            role=Role.objects.get(code="CAJERO"),
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )
        self.cajero.empresas_permitidas.set([self.empresa_a])
        self.panaderia = create_supplier(razon_social="OVEJA NEGRA ARMADI SRL", actor=self.admin)
        self.panaderia = set_supplier_internal(supplier=self.panaderia, es_interno=True, actor=self.admin)
        self.proveedor = create_supplier(razon_social="Verduleria La Huerta", actor=self.admin)
        self.categoria = create_payable_category(
            nombre="Insumos de caja", rubro_operativo=self.rubro_insumos, actor=self.admin
        )
        self.caja = open_box(
            user=self.cajero,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.cajero,
        )

    def _deuda(self, monto="80.00", *, proveedor=None, concepto="Pan del turno", token_alta=None):
        return register_box_expense_debt(
            caja=self.caja,
            proveedor=proveedor or self.panaderia,
            categoria=self.categoria,
            monto=Decimal(monto),
            concepto=concepto,
            actor=self.cajero,
            token_alta=token_alta,
        )

    # --- helpers --------------------------------------------------------------
    def _pagar_parte_y_cancelar_el_resto(self, deuda):
        """Una deuda con plata y con INTERNO a la vez: el INTERNO no la libera."""
        register_central_cash_movement(
            empresa=self.empresa_a,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("500.00"),
            concepto="Fondo central",
            fecha=self.fecha_op,
            actor=self.admin,
        )
        register_cash_payment(payable=deuda, fecha_pago=self.fecha_op, monto=Decimal("30.00"), actor=self.admin)
        register_payment(
            payable=deuda,
            bank_account=None,
            medio_pago=INTERNO,
            fecha_pago=self.fecha_op,
            monto=Decimal("50.00"),
            actor=self.admin,
        )


class GastoComoDeudaInternoTests(InternoCajaBase):
    def test_la_cajera_la_carga_y_nace_saldada_sin_mover_fondos(self):
        saldo_caja = self.caja.saldo_esperado
        boveda = MovimientoCajaCentral.objects.count()
        # La cajera no puede registrar pagos de tesoreria por su cuenta.
        with self.assertRaises(PermissionDenied):
            register_payment(
                payable=self._deuda("10.00", proveedor=self.proveedor),
                bank_account=None,
                medio_pago=INTERNO,
                fecha_pago=self.fecha_op,
                monto=Decimal("10.00"),
                actor=self.cajero,
            )

        deuda = self._deuda("80.00")

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        pago = deuda.pagos.get()
        self.assertEqual(pago.medio_pago, INTERNO)
        self.assertEqual(pago.creado_por, self.cajero)
        self.assertEqual(pago.fecha_pago, self.fecha_op)
        self.assertEqual(pago.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.saldo_esperado, saldo_caja)
        self.assertEqual(MovimientoCajaCentral.objects.count(), boveda)

    def test_reenviar_el_mismo_formulario_no_duplica_deuda_ni_cancelacion(self):
        token = uuid.uuid4()

        primera = self._deuda(token_alta=token)
        segunda = self._deuda(token_alta=token)

        self.assertEqual(primera.pk, segunda.pk)
        self.assertEqual(CuentaPorPagar.objects.filter(caja_origen=self.caja).count(), 1)
        self.assertEqual(PagoTesoreria.objects.filter(cuenta_por_pagar=primera).count(), 1)

    def test_un_proveedor_comun_sigue_quedando_pendiente(self):
        deuda = self._deuda(proveedor=self.proveedor)

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertFalse(deuda.pagos.exists())

    def test_anular_la_caja_anula_la_cancelacion_y_la_deuda(self):
        deuda = self._deuda()
        pago = deuda.pagos.get()
        self._grant_closed_box_fix(self.admin)

        annul_box(caja=self.caja, motivo="Caja duplicada", actor=self.admin)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.saldo_pendiente, Decimal("0.00"))
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.ANULADO)
        self.assertIn(f"Anulacion de caja origen #{self.caja.pk}", pago.motivo_anulacion)
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, Caja.Estado.ANULADA)

    def test_anular_la_caja_con_plata_e_interno_sigue_bloqueado(self):
        deuda = self._deuda("80.00", proveedor=self.proveedor)
        self._pagar_parte_y_cancelar_el_resto(deuda)
        self._grant_closed_box_fix(self.admin)

        with self.assertRaises(ValidationError):
            annul_box(caja=self.caja, motivo="Caja duplicada", actor=self.admin)

        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, Caja.Estado.ABIERTA)
        self.assertEqual(
            deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 2
        )

    def test_eliminar_el_gasto_como_deuda_interno(self):
        self._grant_movement_delete(self.cajero)
        deuda = self._deuda()
        pago = deuda.pagos.get()

        annul_box_originated_debt(deuda=deuda, motivo="Cargada de mas", actor=self.cajero)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.anulada_por, self.cajero)
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(pago.anulado_por, self.cajero)
        self.assertEqual(pago.motivo_anulacion, "Cargada de mas")

    def test_eliminar_con_plata_e_interno_sigue_bloqueado(self):
        self._grant_movement_delete(self.cajero)
        deuda = self._deuda("80.00", proveedor=self.proveedor)
        self._pagar_parte_y_cancelar_el_resto(deuda)

        with self.assertRaises(ValidationError):
            annul_box_originated_debt(deuda=deuda, motivo="Cargada de mas", actor=self.cajero)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(
            deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 2
        )

    def test_eliminar_sin_permiso_no_toca_la_cancelacion(self):
        deuda = self._deuda()

        with self.assertRaises(PermissionDenied):
            annul_box_originated_debt(deuda=deuda, motivo="Cargada de mas", actor=self.cajero)

        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.REGISTRADO)

    def test_la_linea_de_tiempo_de_la_caja_dice_proveedor_interno(self):
        self._deuda(concepto="Pan del turno")
        self._deuda(proveedor=self.proveedor, concepto="Verdura del turno")
        self.client.force_login(self.cajero)

        response = self.client.get(reverse("cashops:box_detail", args=[self.caja.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Pan del turno. {TEXTO_DEUDA_PROVEEDOR_INTERNO}.")
        self.assertNotContains(response, "Pan del turno. No salio efectivo de la caja; tesoreria paga despues.")
        self.assertContains(response, "Verdura del turno. No salio efectivo de la caja; tesoreria paga despues.")

    def test_la_pantalla_de_carga_avisa_que_queda_saldada(self):
        self.client.force_login(self.cajero)
        datos = {
            "rubro": self.rubro_insumos.pk,
            "fecha_factura": self.fecha_op.isoformat(),
            "monto": "45.50",
            "referencia_comprobante": "",
            "observacion": "",
        }

        interna = self.client.post(
            reverse("cashops:box_expense_debt", args=[self.caja.pk]),
            {**datos, "proveedor": self.panaderia.pk, "concepto": "Medialunas"},
            follow=True,
        )
        comun = self.client.post(
            reverse("cashops:box_expense_debt", args=[self.caja.pk]),
            {**datos, "proveedor": self.proveedor.pk, "concepto": "Lechuga"},
            follow=True,
        )

        self.assertContains(interna, "se da por pagada sin mover fondos")
        self.assertEqual(
            CuentaPorPagar.objects.get(concepto="Medialunas").estado, CuentaPorPagar.Estado.PAGADA
        )
        self.assertContains(comun, "tesorería lo paga después")
        self.assertEqual(
            CuentaPorPagar.objects.get(concepto="Lechuga").estado, CuentaPorPagar.Estado.PENDIENTE
        )


@skipUnless(connection.vendor == "postgresql", "Los bloqueos con select_for_update solo se prueban en PostgreSQL.")
class BloqueosEnPostgresTests(InternoCajaBase):
    """Anular la caja bloquea sus deudas con of=("self",): en Postgres un FOR
    UPDATE sobre un JOIN de FK nullable revienta (ver PostgresRowLockingTests).
    SQLite ignora FOR UPDATE, asi que esto solo protege corriendo en Postgres."""

    def test_anular_la_caja_con_deuda_interna(self):
        deuda = self._deuda()
        self._grant_closed_box_fix(self.admin)

        annul_box(caja=self.caja, motivo="Caja duplicada", actor=self.admin)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.ANULADO)

    def test_eliminar_el_gasto_como_deuda_interno(self):
        self._grant_movement_delete(self.cajero)
        deuda = self._deuda()

        annul_box_originated_debt(deuda=deuda, motivo="Cargada de mas", actor=self.cajero)

        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.ANULADO)
