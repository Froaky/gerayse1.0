"""US-3.20, slice 3: las facturas del proveedor interno nacen saldadas.

La panaderia propia (Proveedor.es_interno, la marca el comando
regularizar_deudas) nunca se paga, pero su factura cuenta como materia prima.
Desde este slice:

- `register_payable` (y el alta de deuda desde la caja, en cashops) deja la
  deuda PAGADA con una cancelacion INTERNO a la fecha de la factura, sin mover
  fondos y en la misma transaccion que la crea;
- la cancelacion INTERNO no bloquea editar ni anular la deuda: con la deuda
  bloqueada se valida todo, se anula esa cancelacion y, al editar, se vuelve a
  saldar si el proveedor sigue o pasa a ser interno. Cualquier otro pago sigue
  bloqueando como siempre;
- el detalle de la deuda ofrece Editar y Anular cuando solo tiene INTERNO;
- revertir el lote interno con --desmarcar-interno anula tambien las
  cancelaciones automaticas.
"""
import re
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
)
from treasury.regularizacion import Accion, Regularizacion, Reversion, token_de_pago
from treasury.services import (
    OBSERVACIONES_INTERNO_AUTOMATICO,
    annul_payable,
    annul_payment,
    build_economic_period_snapshot,
    create_bank_account,
    create_payable_category,
    create_supplier,
    get_boveda,
    payable_blocked_by_payments,
    register_carga_inicial_caja_central,
    register_cash_payment,
    register_payable,
    register_payment,
    register_special_commitment,
    set_supplier_internal,
    token_interno_automatico,
    toggle_payable_category,
    update_payable,
)

User = get_user_model()

INTERNO = PagoTesoreria.MedioPago.INTERNO
EFECTIVO = PagoTesoreria.MedioPago.EFECTIVO
REGULARIZACION = PagoTesoreria.MedioPago.REGULARIZACION

EMISION = date(2026, 10, 5)
OCTUBRE = date(2026, 10, 1)
FIN_DE_OCTUBRE = date(2026, 10, 31)


class ProveedorInternoBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="tesoreria-interno", password="x", email="interno@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="ARMADI SRL")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            nombre="Cafe Centro", codigo="EC1", razon_social="ARMADI SRL", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Materia prima")
        self.categoria = create_payable_category(
            nombre="Materia prima", rubro_operativo=self.rubro, actor=self.admin
        )
        self.panaderia = create_supplier(razon_social="OVEJA NEGRA ARMADI SRL", actor=self.admin)
        self.panaderia = set_supplier_internal(supplier=self.panaderia, es_interno=True, actor=self.admin)
        self.proveedor = create_supplier(razon_social="Carnes del Norte SA", actor=self.admin)
        self.cuenta = create_bank_account(
            nombre="Macro ARMADI",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="300-9",
            cbu="2850590940090418135209",
            empresa=self.empresa,
            actor=self.admin,
        )
        register_carga_inicial_caja_central(
            empresa=self.empresa,
            fecha=date(2026, 9, 1),
            monto=Decimal("100000.00"),
            motivo="Saldo inicial de prueba",
            actor=self.admin,
        )

    def _deuda(self, importe="1000.00", *, proveedor=None, emision=EMISION, concepto="Pan de la semana"):
        return register_payable(
            sucursal=self.sucursal,
            proveedor=proveedor or self.panaderia,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=emision,
            fecha_vencimiento=emision + timedelta(days=15),
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def _editar(self, deuda, **cambios):
        deuda = CuentaPorPagar.objects.get(pk=deuda.pk)
        datos = {
            "sucursal": deuda.sucursal,
            "proveedor": deuda.proveedor,
            "categoria": deuda.categoria,
            "concepto": deuda.concepto,
            "fecha_emision": deuda.fecha_emision,
            "fecha_vencimiento": deuda.fecha_vencimiento,
            "periodo_referencia": deuda.periodo_referencia,
            "importe_total": deuda.importe_total,
            "referencia_comprobante": deuda.referencia_comprobante,
            "observaciones": deuda.observaciones,
        }
        datos.update(cambios)
        return update_payable(payable=deuda, actor=self.admin, **datos)

    def _movimientos(self):
        return (MovimientoBancario.objects.count(), MovimientoCajaCentral.objects.count())

    def _boveda(self):
        return get_boveda(self.empresa).saldo_actual

    def _registrados(self, deuda):
        return list(deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).order_by("pk"))


class AltaAutomaticaTests(ProveedorInternoBase):
    def test_la_factura_del_proveedor_interno_nace_saldada_sin_mover_fondos(self):
        movimientos = self._movimientos()
        boveda = self._boveda()

        deuda = self._deuda("1500.00")

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda.saldo_pendiente, Decimal("0.00"))
        pago = deuda.pagos.get()
        self.assertEqual(pago.medio_pago, INTERNO)
        self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)
        self.assertEqual(pago.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
        self.assertEqual(pago.monto, Decimal("1500.00"))
        self.assertEqual(pago.fecha_pago, EMISION)
        self.assertIsNone(pago.cuenta_bancaria)
        self.assertIsNone(pago.movimiento_bancario)
        self.assertEqual(pago.creado_por, self.admin)
        self.assertEqual(pago.observaciones, OBSERVACIONES_INTERNO_AUTOMATICO)
        self.assertEqual(pago.token_alta, token_interno_automatico(deuda.pk, 0))
        self.assertEqual(self._movimientos(), movimientos)
        self.assertEqual(self._boveda(), boveda)

    def test_el_token_automatico_no_choca_con_el_del_comando(self):
        deuda = self._deuda()

        pago = deuda.pagos.get()

        self.assertNotEqual(pago.token_alta, token_de_pago(f"INTERNO-{self.panaderia.pk}", INTERNO, deuda.pk))
        self.assertNotEqual(token_interno_automatico(deuda.pk, 0), token_interno_automatico(deuda.pk, 1))

    def test_un_proveedor_comun_sigue_naciendo_pendiente(self):
        deuda = self._deuda(proveedor=self.proveedor)

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertFalse(deuda.pagos.exists())

    def test_el_gasto_sigue_contando_y_no_queda_deuda_viva(self):
        self._deuda("1500.00")

        economico = build_economic_period_snapshot(date_from=OCTUBRE, date_to=FIN_DE_OCTUBRE)

        self.assertEqual(economico["debt_period_total"], Decimal("1500.00"))
        self.assertEqual(economico["debt_pending_total"], Decimal("0.00"))

    def test_si_el_saldado_falla_la_deuda_no_queda_creada(self):
        with mock.patch(
            "treasury.services._register_payment", side_effect=ValidationError("falla de prueba")
        ):
            with self.assertRaises(ValidationError):
                self._deuda()

        self.assertFalse(CuentaPorPagar.objects.exists())
        self.assertFalse(PagoTesoreria.objects.exists())

    def test_marcar_al_proveedor_no_salda_lo_viejo_pero_editarlo_si(self):
        vieja = self._deuda(proveedor=self.proveedor)
        set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=self.admin)

        vieja.refresh_from_db()
        self.assertEqual(vieja.estado, CuentaPorPagar.Estado.PENDIENTE)

        editada = self._editar(vieja, concepto="Corregida")
        self.assertEqual(editada.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(editada.pagos.get().medio_pago, INTERNO)

    def test_no_salda_una_deuda_con_compromiso_especial(self):
        deuda = self._deuda(proveedor=self.proveedor)
        register_special_commitment(
            tipo=CompromisoEspecial.Tipo.IMPUESTO,
            concepto="Plan de pagos",
            sustento_referencia="PLAN-1",
            monto_estimado=Decimal("1000.00"),
            cuenta_por_pagar=deuda,
            organismo="ARCA",
            periodo_fiscal=OCTUBRE,
            actor=self.admin,
        )
        set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=self.admin)

        editada = self._editar(deuda, concepto="Con compromiso")

        self.assertEqual(editada.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertFalse(editada.pagos.exists())
        self.assertNotEqual(editada.compromiso_especial.estado, CompromisoEspecial.Estado.EJECUTADO)


class EdicionTests(ProveedorInternoBase):
    def test_editar_la_vuelve_a_saldar_por_el_importe_nuevo(self):
        deuda = self._deuda("1000.00")
        anterior = deuda.pagos.get()
        movimientos = self._movimientos()

        editada = self._editar(deuda, importe_total=Decimal("1200.00"), concepto="Pan y facturas")

        self.assertEqual(editada.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(editada.importe_total, Decimal("1200.00"))
        self.assertEqual(editada.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(editada.concepto, "Pan y facturas")
        self.assertEqual(editada.actualizado_por, self.admin)
        anterior.refresh_from_db()
        self.assertEqual(anterior.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(anterior.estado_bancario, PagoTesoreria.EstadoBancario.ANULADO)
        self.assertEqual(anterior.motivo_anulacion, "Edicion de deuda de proveedor interno")
        self.assertEqual(anterior.anulado_por, self.admin)
        [nuevo] = self._registrados(editada)
        self.assertEqual(nuevo.medio_pago, INTERNO)
        self.assertEqual(nuevo.monto, Decimal("1200.00"))
        self.assertEqual(nuevo.token_alta, token_interno_automatico(deuda.pk, 1))
        self.assertEqual(self._movimientos(), movimientos)

    def test_cada_edicion_usa_un_token_nuevo(self):
        deuda = self._deuda("1000.00")
        self._editar(deuda, importe_total=Decimal("1100.00"))
        self._editar(deuda, importe_total=Decimal("1300.00"))

        pagos = list(deuda.pagos.order_by("pk"))
        self.assertEqual(len(pagos), 3)
        self.assertEqual(len({pago.token_alta for pago in pagos}), 3)
        self.assertEqual(
            [pago.estado for pago in pagos],
            [PagoTesoreria.Estado.ANULADO, PagoTesoreria.Estado.ANULADO, PagoTesoreria.Estado.REGISTRADO],
        )
        self.assertEqual(pagos[-1].monto, Decimal("1300.00"))

    def test_si_pasa_a_un_proveedor_comun_queda_pendiente(self):
        deuda = self._deuda("1000.00")

        editada = self._editar(deuda, proveedor=self.proveedor)

        self.assertEqual(editada.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(editada.saldo_pendiente, Decimal("1000.00"))
        self.assertEqual(self._registrados(editada), [])

    def test_si_pasa_a_ser_del_proveedor_interno_se_salda(self):
        deuda = self._deuda("800.00", proveedor=self.proveedor)

        editada = self._editar(deuda, proveedor=self.panaderia)

        self.assertEqual(editada.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(self._registrados(editada)[0].medio_pago, INTERNO)

    def test_si_la_validacion_falla_la_cancelacion_sigue_viva(self):
        deuda = self._deuda("1000.00")
        pago = deuda.pagos.get()
        inactiva = create_payable_category(nombre="Vieja", rubro_operativo=self.rubro, actor=self.admin)
        toggle_payable_category(category=inactiva, actor=self.admin)

        casos = {
            "categoria inactiva": {"categoria": inactiva, "importe_total": Decimal("900.00")},
            "vencimiento antes de la factura": {
                "fecha_vencimiento": EMISION - timedelta(days=1),
                "importe_total": Decimal("900.00"),
            },
        }
        for nombre, cambios in casos.items():
            with self.subTest(nombre):
                with self.assertRaises(ValidationError):
                    self._editar(deuda, **cambios)
                pago.refresh_from_db()
                self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)
                actual = CuentaPorPagar.objects.get(pk=deuda.pk)
                self.assertEqual(actual.estado, CuentaPorPagar.Estado.PAGADA)
                self.assertEqual(actual.importe_total, Decimal("1000.00"))

    def test_si_el_nuevo_saldado_falla_no_queda_nada_a_medias(self):
        deuda = self._deuda("1000.00")
        pago = deuda.pagos.get()

        with mock.patch(
            "treasury.services._register_payment", side_effect=ValidationError("falla de prueba")
        ):
            with self.assertRaises(ValidationError):
                self._editar(deuda, importe_total=Decimal("1200.00"))

        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)
        actual = CuentaPorPagar.objects.get(pk=deuda.pk)
        self.assertEqual(actual.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(actual.importe_total, Decimal("1000.00"))

    def test_la_cancelada_por_el_comando_tambien_se_edita(self):
        deuda = self._deuda("700.00", proveedor=self.proveedor)
        interno = Regularizacion(actor=self.admin, proveedor_interno=self.proveedor)
        interno.planificar()
        interno.aplicar()
        del_lote = deuda.pagos.get()

        editada = self._editar(deuda, importe_total=Decimal("750.00"))

        del_lote.refresh_from_db()
        self.assertEqual(del_lote.estado, PagoTesoreria.Estado.ANULADO)
        [nuevo] = self._registrados(editada)
        self.assertEqual(nuevo.monto, Decimal("750.00"))
        self.assertEqual(nuevo.token_alta, token_interno_automatico(deuda.pk, 1))
        reversion = Reversion(actor=self.admin, lote=interno.lote)
        reversion.planificar()
        self.assertEqual([f.accion for f in reversion.filas], [Accion.YA_ANULADO])


class BloqueosTests(ProveedorInternoBase):
    def _pagar_en_efectivo(self, deuda, monto):
        return register_cash_payment(payable=deuda, fecha_pago=EMISION, monto=Decimal(monto), actor=self.admin)

    def test_un_pago_con_plata_sigue_bloqueando(self):
        deuda = self._deuda("1000.00", proveedor=self.proveedor)
        self._pagar_en_efectivo(deuda, "400.00")

        with self.assertRaisesMessage(ValidationError, "pagos registrados"):
            self._editar(deuda, concepto="Otra cosa")
        with self.assertRaisesMessage(ValidationError, "pagos registrados"):
            annul_payable(payable=deuda, motivo="Mal cargada", actor=self.admin)
        self.assertTrue(payable_blocked_by_payments(deuda))

    def test_plata_mas_interno_bloquea_y_no_toca_la_cancelacion(self):
        deuda = self._deuda("1000.00", proveedor=self.proveedor)
        self._pagar_en_efectivo(deuda, "400.00")
        interno = register_payment(
            payable=deuda,
            bank_account=None,
            medio_pago=INTERNO,
            fecha_pago=EMISION,
            monto=Decimal("600.00"),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError):
            self._editar(deuda, concepto="Otra cosa")
        with self.assertRaises(ValidationError):
            annul_payable(payable=deuda, motivo="Mal cargada", actor=self.admin)

        interno.refresh_from_db()
        self.assertEqual(interno.estado, PagoTesoreria.Estado.REGISTRADO)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)

    def test_regularizacion_tambien_bloquea(self):
        deuda = self._deuda("1000.00", proveedor=self.proveedor)
        register_payment(
            payable=deuda,
            bank_account=None,
            medio_pago=REGULARIZACION,
            fecha_pago=EMISION,
            monto=Decimal("1000.00"),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError):
            self._editar(deuda, concepto="Otra cosa")
        with self.assertRaises(ValidationError):
            annul_payable(payable=deuda, motivo="Mal cargada", actor=self.admin)
        self.assertTrue(payable_blocked_by_payments(deuda))

    def test_solo_interno_no_bloquea(self):
        deuda = self._deuda("1000.00")

        self.assertFalse(payable_blocked_by_payments(deuda))

    def test_una_deuda_anulada_no_se_edita_ni_se_anula_de_nuevo(self):
        deuda = self._deuda("1000.00", proveedor=self.proveedor)
        annul_payable(payable=deuda, motivo="Cargada dos veces", actor=self.admin)

        with self.assertRaisesMessage(ValidationError, "anulada"):
            self._editar(deuda, concepto="Revivida")
        with self.assertRaisesMessage(ValidationError, "ya esta anulada"):
            annul_payable(payable=deuda, motivo="Otra vez", actor=self.admin)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.motivo_anulacion, "Cargada dos veces")


class AnulacionTests(ProveedorInternoBase):
    def test_anular_la_deuda_anula_su_cancelacion(self):
        deuda = self._deuda("1500.00")
        pago = deuda.pagos.get()
        movimientos = self._movimientos()

        anulada = annul_payable(payable=deuda, motivo="Cargada dos veces", actor=self.admin)

        self.assertEqual(anulada.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(anulada.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(anulada.motivo_anulacion, "Cargada dos veces")
        self.assertEqual(anulada.anulada_por, self.admin)
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(pago.motivo_anulacion, "Anulacion de la deuda: Cargada dos veces")
        self.assertEqual(self._movimientos(), movimientos)
        economico = build_economic_period_snapshot(date_from=OCTUBRE, date_to=FIN_DE_OCTUBRE)
        self.assertEqual(economico["debt_period_total"], Decimal("0.00"))

    def test_sin_motivo_no_toca_la_cancelacion(self):
        deuda = self._deuda("1500.00")
        pago = deuda.pagos.get()

        with self.assertRaises(ValidationError):
            annul_payable(payable=deuda, motivo="  ", actor=self.admin)

        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)

    def test_anular_a_mano_la_cancelacion_deja_la_deuda_abierta_para_el_pago_real(self):
        deuda = self._deuda("1500.00")
        pago = deuda.pagos.get()

        with self.assertRaisesMessage(ValidationError, "anule primero esa cancelacion"):
            register_cash_payment(payable=deuda, fecha_pago=EMISION, monto=Decimal("1500.00"), actor=self.admin)

        annul_payment(payment=pago, motivo="Se le pago de verdad", actor=self.admin)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(self._registrados(deuda), [])

        register_cash_payment(payable=deuda, fecha_pago=EMISION, monto=Decimal("1500.00"), actor=self.admin)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual([p.medio_pago for p in self._registrados(deuda)], [EFECTIVO])


class ReversionConDesmarcarTests(ProveedorInternoBase):
    def setUp(self):
        super().setUp()
        self.vieja = self._deuda("700.00", proveedor=self.proveedor, concepto="Cargada antes de marcarlo")
        self.interno = Regularizacion(actor=self.admin, proveedor_interno=self.proveedor)
        self.interno.planificar()
        self.interno.aplicar()
        self.nueva = self._deuda("300.00", proveedor=self.proveedor, concepto="Cargada despues")

    def test_sin_desmarcar_no_toca_las_automaticas(self):
        reversion = Reversion(actor=self.admin, lote=self.interno.lote)
        reversion.planificar()
        reversion.aplicar()

        self.assertEqual([f.deuda_id for f in reversion.filas], [self.vieja.pk])
        self.nueva.refresh_from_db()
        self.assertEqual(self.nueva.estado, CuentaPorPagar.Estado.PAGADA)

    def test_desmarcar_anula_tambien_las_cancelaciones_automaticas(self):
        simulacion = Reversion(actor=self.admin, lote=self.interno.lote, desmarcar_interno=True)
        simulacion.planificar()
        acciones = {f.deuda_id: f.accion for f in simulacion.filas}
        self.assertEqual(
            acciones,
            {self.vieja.pk: Accion.A_REVERTIR, self.nueva.pk: Accion.A_REVERTIR_AUTOMATICA},
        )
        self.nueva.refresh_from_db()
        self.assertEqual(self.nueva.estado, CuentaPorPagar.Estado.PAGADA)

        reversion = Reversion(actor=self.admin, lote=self.interno.lote, desmarcar_interno=True)
        reversion.planificar()
        reversion.aplicar()

        for deuda in (self.vieja, self.nueva):
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.proveedor.refresh_from_db()
        self.assertFalse(self.proveedor.es_interno)
        # Ya desmarcado, la factura siguiente nace pendiente.
        self.assertEqual(self._deuda("50.00", proveedor=self.proveedor).estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_no_toca_cancelaciones_internas_de_otro_lote(self):
        otra = self._deuda("200.00", proveedor=self.proveedor, concepto="Saldada a mano")
        annul_payment(payment=otra.pagos.get(), motivo="Se rehace a mano", actor=self.admin)
        a_mano = register_payment(
            payable=otra,
            bank_account=None,
            medio_pago=INTERNO,
            fecha_pago=EMISION,
            monto=Decimal("200.00"),
            referencia="OTRO-LOTE",
            actor=self.admin,
        )

        reversion = Reversion(actor=self.admin, lote=self.interno.lote, desmarcar_interno=True)
        reversion.planificar()

        self.assertNotIn(a_mano.pk, {f.pago_id for f in reversion.filas})

    def test_por_consola(self):
        salida = StringIO()

        call_command(
            "regularizar_deudas",
            usuario=self.admin.username,
            revertir=self.interno.lote,
            desmarcar_interno=True,
            apply=True,
            stdout=salida,
        )

        texto = salida.getvalue()
        self.assertIn("A_REVERTIR_AUTOMATICA", texto)
        self.assertIn("Proveedores desmarcados como internos: Carnes del Norte SA", texto)
        self.nueva.refresh_from_db()
        self.assertEqual(self.nueva.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_un_pago_ajeno_con_la_referencia_del_lote_no_desmarca_a_su_proveedor(self):
        otro = create_supplier(razon_social="Fabrica de Pastas Propia", actor=self.admin)
        abierta = self._deuda("120.00", proveedor=otro, concepto="Cargada antes")
        register_payment(
            payable=abierta,
            bank_account=None,
            medio_pago=INTERNO,
            fecha_pago=EMISION,
            monto=Decimal("120.00"),
            referencia=self.interno.lote,
            actor=self.admin,
        )
        set_supplier_internal(supplier=otro, es_interno=True, actor=self.admin)
        automatica = self._deuda("90.00", proveedor=otro, concepto="Cargada despues")

        reversion = Reversion(actor=self.admin, lote=self.interno.lote, desmarcar_interno=True)
        reversion.planificar()
        reversion.aplicar()

        acciones = {f.deuda_id: f.accion for f in reversion.filas}
        self.assertEqual(acciones[abierta.pk], Accion.NO_ES_DEL_LOTE)
        self.assertNotIn(automatica.pk, acciones)
        otro.refresh_from_db()
        self.assertTrue(otro.es_interno)
        self.proveedor.refresh_from_db()
        self.assertFalse(self.proveedor.es_interno)


class PantallasTests(ProveedorInternoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def _detalle(self, deuda):
        return self.client.get(reverse("treasury:cuentas_por_pagar_detail", args=[deuda.pk]))

    def test_deuda_interna_ofrece_editar_y_anular_pero_no_pagar(self):
        deuda = self._deuda()

        response = self._detalle(deuda)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("treasury:cuentas_por_pagar_update", args=[deuda.pk]))
        self.assertContains(response, reverse("treasury:cuentas_por_pagar_annul", args=[deuda.pk]))
        self.assertNotContains(response, f"?payable={deuda.pk}")

    def test_deuda_pagada_con_plata_no_ofrece_editar_ni_anular(self):
        deuda = self._deuda("500.00", proveedor=self.proveedor)
        register_cash_payment(payable=deuda, fecha_pago=EMISION, monto=Decimal("500.00"), actor=self.admin)

        response = self._detalle(deuda)

        self.assertNotContains(response, reverse("treasury:cuentas_por_pagar_update", args=[deuda.pk]))
        self.assertNotContains(response, reverse("treasury:cuentas_por_pagar_annul", args=[deuda.pk]))

    def test_deuda_parcial_con_plata_sigue_sin_anular(self):
        deuda = self._deuda("500.00", proveedor=self.proveedor)
        register_cash_payment(payable=deuda, fecha_pago=EMISION, monto=Decimal("200.00"), actor=self.admin)

        response = self._detalle(deuda)

        self.assertContains(response, f"?payable={deuda.pk}")
        self.assertNotContains(response, reverse("treasury:cuentas_por_pagar_annul", args=[deuda.pk]))

    def test_alta_por_pantalla_nace_saldada(self):
        response = self.client.post(
            reverse("treasury:cuentas_por_pagar_create"),
            {
                "proveedor": self.panaderia.pk,
                "categoria": self.categoria.pk,
                "concepto": "Pan del lunes",
                "referencia_comprobante": "",
                "fecha_emision": EMISION.isoformat(),
                "fecha_vencimiento": EMISION.isoformat(),
                "periodo_referencia": "",
                "importe_total": "450.00",
                "sucursal": self.sucursal.pk,
                "observaciones": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        deuda = CuentaPorPagar.objects.get(concepto="Pan del lunes")
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda.pagos.get().medio_pago, INTERNO)

    def test_editar_por_pantalla_la_vuelve_a_saldar(self):
        deuda = self._deuda("1000.00")
        url = reverse("treasury:cuentas_por_pagar_update", args=[deuda.pk])

        pagina = self.client.get(url)
        self.assertContains(pagina, "cancelacion sin movimiento se anula")

        response = self.client.post(
            url,
            {
                "proveedor": self.panaderia.pk,
                "categoria": self.categoria.pk,
                "concepto": "Pan corregido",
                "referencia_comprobante": "",
                "fecha_emision": EMISION.isoformat(),
                "fecha_vencimiento": (EMISION + timedelta(days=15)).isoformat(),
                "periodo_referencia": OCTUBRE.isoformat(),
                "importe_total": "1250.00",
                "sucursal": self.sucursal.pk,
                "observaciones": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda.importe_total, Decimal("1250.00"))
        self.assertEqual([p.monto for p in self._registrados(deuda)], [Decimal("1250.00")])
        self.assertEqual(deuda.pagos.count(), 2)

    def test_anular_por_pantalla(self):
        deuda = self._deuda("1000.00")
        url = reverse("treasury:cuentas_por_pagar_annul", args=[deuda.pk])

        pagina = self.client.get(url)
        self.assertContains(pagina, "Tambien se anula su cancelacion sin movimiento")

        response = self.client.post(url, {"motivo": "Cargada dos veces"})

        self.assertEqual(response.status_code, 302)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.ANULADO)

    def test_las_pantallas_de_una_deuda_comun_no_cambian(self):
        deuda = self._deuda("1000.00", proveedor=self.proveedor)

        self.assertContains(
            self.client.get(reverse("treasury:cuentas_por_pagar_update", args=[deuda.pk])),
            "Solo se permite mientras no tenga pagos registrados.",
        )
        self.assertNotContains(
            self.client.get(reverse("treasury:cuentas_por_pagar_annul", args=[deuda.pk])),
            "cancelacion sin movimiento",
        )

    def test_el_detalle_del_proveedor_dice_que_es_interno(self):
        interno = self.client.get(reverse("treasury:proveedores_detail", args=[self.panaderia.pk]))
        comun = self.client.get(reverse("treasury:proveedores_detail", args=[self.proveedor.pk]))

        self.assertContains(interno, "Sus facturas se dan por pagadas sin mover fondos")
        self.assertNotContains(comun, "Sus facturas se dan por pagadas sin mover fondos")


class SinPermisoSoloDesdeServiciosTests(TestCase):
    """Las funciones que saltean el permiso de tesoreria no las llama ninguna vista."""

    PRIVADAS = (
        "_register_payment",
        "_annul_payment",
        "_saldar_si_proveedor_interno",
        "_liberar_cancelaciones_internas",
    )

    def test_ninguna_vista_ni_formulario_las_usa(self):
        raiz = Path(settings.BASE_DIR)
        archivos = [
            ruta
            for app in ("treasury", "cashops", "core", "users")
            for nombre in ("views.py", "forms.py", "urls.py")
            if (ruta := raiz / app / nombre).exists()
        ]
        self.assertTrue(archivos)
        for ruta in archivos:
            texto = ruta.read_text(encoding="utf-8")
            for nombre in self.PRIVADAS:
                with self.subTest(archivo=ruta.name, app=ruta.parent.name, funcion=nombre):
                    self.assertIsNone(re.search(rf"\b{nombre}\b", texto))
