"""US-3.20, slice 1: medios de pago que cancelan una deuda SIN mover fondos.

La tesorera declaro pagadas fuera de Gerayse las facturas hasta el 23/09/2026
(REGULARIZACION), y las de la panaderia propia nunca se pagan (INTERNO). Los dos
medios dejan la deuda PAGADA con un PagoTesoreria real, para reusar todas las
invariantes de deuda (saldo derivado, sobrepago, token, anulacion), pero:

- no llevan cuenta, movimiento ni fecha diferida, y quedan con estado bancario
  NO_APLICA;
- nunca crean MovimientoBancario ni MovimientoCajaCentral, asi que banco, caja
  fuerte y resultado economico quedan exactamente igual;
- las lecturas que presentan "plata que salio" (pagos recientes, contador de
  pagos, pagado historico, listado de pagos) los dejan afuera o los separan.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django import forms as django_forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops import forms as cashops_forms
from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury import forms as treasury_forms
from treasury.admin import ProveedorAdmin
from treasury.forms import BankPaymentMethodCorrectionForm, PaymentFilterForm, SupplierPaymentBatchForm
from treasury.importacion_planillas import Importador
from treasury.models import (
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import (
    CLASE_POR_MEDIO_DE_PAGO,
    _bank_balance_until,
    annul_payable,
    annul_payment,
    build_disponibilidades_snapshot,
    build_economic_period_snapshot,
    build_financial_period_snapshot,
    build_payable_settlement_breakdown,
    build_supplier_history_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_supplier,
    get_boveda,
    importe_sin_asignar_del_movimiento,
    link_payment_to_bank_movement,
    pay_debt_from_bank_movement,
    pay_debts_from_bank_movement,
    register_cash_payment,
    register_central_cash_movement,
    register_cheque_payment,
    register_echeq_payment,
    register_payable,
    register_payment,
    register_special_commitment,
    register_supplier_payment_batch,
    register_transfer_payment,
    set_initial_bank_balance,
)

User = get_user_model()

REGULARIZACION = PagoTesoreria.MedioPago.REGULARIZACION
INTERNO = PagoTesoreria.MedioPago.INTERNO

EMISION = date(2026, 5, 10)
VENCIMIENTO = date(2026, 5, 25)
PERIODO = date(2026, 5, 1)
CORTE = date(2026, 5, 31)


class PagoSinMovimientoBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-sin-mov", password="x", email="sinmov@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Sin Movimiento SA")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            nombre="Sucursal Uno",
            codigo="SM01",
            razon_social="Empresa Sin Movimiento SA",
            empresa=self.empresa,
        )
        self.rubro = RubroOperativo.objects.create(nombre="Materia prima SM")
        self.category = create_payable_category(
            nombre="Materia prima SM", rubro_operativo=self.rubro, actor=self.admin
        )
        self.supplier = create_supplier(
            razon_social="Proveedor Real SA", identificador_fiscal="30-11111111-1", actor=self.admin
        )
        self.panaderia = create_supplier(
            razon_social="Panaderia Propia SRL", identificador_fiscal="30-22222222-2", actor=self.admin
        )
        self.bank_account = create_bank_account(
            nombre="Cuenta SM",
            banco="Banco SM",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="555-1",
            cbu="2850590940090418135299",
            empresa=self.empresa,
            actor=self.admin,
        )

    def _payable(self, importe="1000.00", *, proveedor=None, concepto="Factura de mayo"):
        return register_payable(
            sucursal=self.sucursal,
            proveedor=proveedor or self.supplier,
            categoria=self.category,
            concepto=concepto,
            fecha_emision=EMISION,
            fecha_vencimiento=VENCIMIENTO,
            periodo_referencia=PERIODO,
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def _cancelar(self, payable, *, medio=REGULARIZACION, monto=None, referencia="REG-2026-05-31", **extra):
        payable.refresh_from_db()
        return register_payment(
            payable=payable,
            bank_account=None,
            medio_pago=medio,
            fecha_pago=CORTE,
            monto=payable.saldo_pendiente if monto is None else Decimal(monto),
            referencia=referencia,
            observaciones="Regularizacion de prueba",
            actor=self.admin,
            **extra,
        )

    def _transferir(self, payable, monto, referencia="TRF-1", fecha=EMISION):
        return register_transfer_payment(
            payable=payable,
            bank_account=self.bank_account,
            fecha_pago=fecha,
            monto=Decimal(monto),
            referencia=referencia,
            actor=self.admin,
        )

    def _debito(self, monto, concepto="Debito suelto"):
        return create_bank_movement(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=CORTE,
            monto=Decimal(monto),
            concepto=concepto,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=PERIODO,
            actor=self.admin,
        )

    def _login(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["empresa_ids"] = [self.empresa.pk]
        session.save()


class AltaDePagoSinMovimientoTests(PagoSinMovimientoBase):
    def test_regularizacion_deja_la_deuda_pagada_sin_crear_movimientos(self):
        payable = self._payable("1000.00")
        bancarios_antes = MovimientoBancario.objects.count()
        boveda_antes = MovimientoCajaCentral.objects.count()

        payment = self._cancelar(payable)

        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(payable.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(payment.medio_pago, REGULARIZACION)
        self.assertEqual(payment.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
        self.assertIsNone(payment.cuenta_bancaria_id)
        self.assertIsNone(payment.movimiento_bancario_id)
        self.assertFalse(payment.mueve_fondos)
        self.assertEqual(MovimientoBancario.objects.count(), bancarios_antes)
        self.assertEqual(MovimientoCajaCentral.objects.count(), boveda_antes)

    def test_interno_tambien_cancela_sin_mover_fondos(self):
        payable = self._payable("800.00", proveedor=self.panaderia)

        payment = self._cancelar(payable, medio=INTERNO, referencia="")

        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(payment.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
        self.assertEqual(MovimientoBancario.objects.count(), 0)
        self.assertEqual(MovimientoCajaCentral.objects.count(), 0)

    def test_con_cuenta_bancaria_se_rechaza_y_no_crea_ningun_debito(self):
        """Protege la rama del servicio que crea el debito PAGO_TESORERIA."""
        payable = self._payable("1000.00")

        with self.assertRaises(ValidationError) as error:
            register_payment(
                payable=payable,
                bank_account=self.bank_account,
                medio_pago=REGULARIZACION,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                actor=self.admin,
            )

        self.assertIn("no mueve fondos", str(error.exception))
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        self.assertEqual(MovimientoBancario.objects.count(), 0)
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_con_movimiento_bancario_se_rechaza_y_no_lo_ata(self):
        payable = self._payable("1000.00")
        debito = self._debito("1000.00")

        with self.assertRaises(ValidationError):
            register_payment(
                payable=payable,
                bank_account=None,
                medio_pago=INTERNO,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                bank_movement=debito,
                actor=self.admin,
            )

        self.assertEqual(PagoTesoreria.objects.count(), 0)
        debito.refresh_from_db()
        self.assertEqual(debito.origen, MovimientoBancario.Origen.MANUAL)

    def test_con_fecha_diferida_se_rechaza(self):
        payable = self._payable("1000.00")

        with self.assertRaises(ValidationError):
            self._cancelar(payable, fecha_diferida=CORTE + timedelta(days=10))

        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_sobre_una_deuda_parcial_cancela_solo_el_resto(self):
        payable = self._payable("1000.00")
        self._transferir(payable, "400.00")
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)

        payment = self._cancelar(payable)

        self.assertEqual(payment.monto, Decimal("600.00"))
        payable.refresh_from_db()
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PAGADA)
        breakdown = build_payable_settlement_breakdown(payable)
        self.assertEqual(breakdown["paid_with_funds"], Decimal("400.00"))
        self.assertEqual(breakdown["settled_without_funds"], Decimal("600.00"))
        self.assertFalse(breakdown["settled_only_without_funds"])

    def test_no_puede_pasarse_del_saldo(self):
        payable = self._payable("1000.00")

        with self.assertRaises(ValidationError):
            self._cancelar(payable, monto="1000.01")

        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_deuda_pagada_o_anulada_se_rechaza(self):
        pagada = self._payable("100.00")
        self._cancelar(pagada)
        anulada = self._payable("200.00", concepto="Factura anulada")
        annul_payable(payable=anulada, motivo="Carga duplicada", actor=self.admin)

        with self.assertRaises(ValidationError):
            self._cancelar(CuentaPorPagar.objects.get(pk=pagada.pk), monto="100.00", referencia="OTRO")
        with self.assertRaises(ValidationError):
            self._cancelar(CuentaPorPagar.objects.get(pk=anulada.pk), monto="200.00")

    def test_compromiso_especial_se_rechaza_y_no_queda_ejecutado(self):
        """Un impuesto o plan no puede quedar EJECUTADO sin que se haya pagado."""
        payable = self._payable("500.00", concepto="Impuesto del mes")
        compromiso = register_special_commitment(
            tipo=CompromisoEspecial.Tipo.IMPUESTO,
            concepto="Impuesto del mes",
            sustento_referencia="VEP-1",
            monto_estimado=Decimal("500.00"),
            cuenta_por_pagar=payable,
            organismo="ARCA",
            periodo_fiscal=PERIODO,
            actor=self.admin,
        )

        with self.assertRaises(ValidationError) as error:
            self._cancelar(payable)

        self.assertIn("compromiso especial", str(error.exception))
        compromiso.refresh_from_db()
        self.assertNotEqual(compromiso.estado, CompromisoEspecial.Estado.EJECUTADO)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_mismo_token_devuelve_el_mismo_pago(self):
        payable = self._payable("1000.00")
        token = uuid.uuid4()

        primero = self._cancelar(payable, token_alta=token)
        segundo = self._cancelar(payable, token_alta=token)

        self.assertEqual(primero.pk, segundo.pk)
        self.assertEqual(PagoTesoreria.objects.count(), 1)

    def test_la_misma_referencia_de_lote_no_choca_entre_deudas(self):
        """La unicidad (cuenta, medio, referencia) no aplica con la cuenta en NULL."""
        una = self._payable("100.00", concepto="Factura A")
        otra = self._payable("200.00", concepto="Factura B")

        self._cancelar(una, referencia="REG-2026-05-31")
        self._cancelar(otra, referencia="REG-2026-05-31")

        self.assertEqual(
            PagoTesoreria.objects.filter(referencia="REG-2026-05-31").count(), 2
        )

    def test_anular_la_regularizacion_reabre_la_deuda_sin_tocar_movimientos(self):
        payable = self._payable("1000.00")
        self._transferir(payable, "300.00")
        payment = self._cancelar(payable)
        bancarios = list(MovimientoBancario.objects.values_list("pk", "estado"))
        boveda = list(MovimientoCajaCentral.objects.values_list("pk", "estado"))

        annul_payment(payment=payment, motivo="Se pago con un debito real", actor=self.admin)

        payment.refresh_from_db()
        payable.refresh_from_db()
        self.assertEqual(payment.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(payment.estado_bancario, PagoTesoreria.EstadoBancario.ANULADO)
        self.assertEqual(payable.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(payable.saldo_pendiente, Decimal("700.00"))
        self.assertEqual(list(MovimientoBancario.objects.values_list("pk", "estado")), bancarios)
        self.assertEqual(list(MovimientoCajaCentral.objects.values_list("pk", "estado")), boveda)

    def test_no_se_vincula_a_un_movimiento_bancario(self):
        payable = self._payable("1000.00")
        payment = self._cancelar(payable)
        debito = self._debito("1000.00", concepto="Debito real")

        with self.assertRaises(ValidationError) as error:
            link_payment_to_bank_movement(payment=payment, bank_movement=debito, actor=self.admin)

        self.assertIn("Anule la cancelacion y pague la deuda desde el movimiento", str(error.exception))
        payment.refresh_from_db()
        self.assertIsNone(payment.movimiento_bancario_id)


class InvariantesDelModeloTests(PagoSinMovimientoBase):
    def _pago(self, **campos):
        payable = self._payable("1000.00")
        valores = {
            "cuenta_por_pagar": payable,
            "medio_pago": REGULARIZACION,
            "fecha_pago": CORTE,
            "monto": Decimal("100.00"),
            "estado_bancario": PagoTesoreria.EstadoBancario.NO_APLICA,
        }
        valores.update(campos)
        return PagoTesoreria(**valores)

    def test_sin_movimiento_y_sin_cuenta_es_valido(self):
        self._pago().full_clean()

    def test_sin_movimiento_con_cuenta_es_invalido(self):
        with self.assertRaises(ValidationError) as error:
            self._pago(cuenta_bancaria=self.bank_account).full_clean()
        self.assertIn("cuenta_bancaria", error.exception.message_dict)

    def test_sin_movimiento_con_movimiento_bancario_es_invalido(self):
        debito = self._debito("100.00")
        with self.assertRaises(ValidationError) as error:
            self._pago(medio_pago=INTERNO, movimiento_bancario=debito).full_clean()
        self.assertIn("movimiento_bancario", error.exception.message_dict)

    def test_sin_movimiento_con_fecha_diferida_es_invalido(self):
        with self.assertRaises(ValidationError) as error:
            self._pago(fecha_diferida=CORTE + timedelta(days=5)).full_clean()
        self.assertIn("fecha_diferida", error.exception.message_dict)

    def test_sin_movimiento_registrado_exige_estado_bancario_no_aplica(self):
        with self.assertRaises(ValidationError) as error:
            self._pago(estado_bancario=PagoTesoreria.EstadoBancario.PENDIENTE).full_clean()
        self.assertIn("estado_bancario", error.exception.message_dict)

    def test_no_aplica_es_solo_para_los_medios_sin_movimiento(self):
        with self.assertRaises(ValidationError) as error:
            self._pago(
                medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
                cuenta_bancaria=self.bank_account,
            ).full_clean()
        self.assertIn("estado_bancario", error.exception.message_dict)
        with self.assertRaises(ValidationError) as error:
            self._pago(medio_pago=PagoTesoreria.MedioPago.EFECTIVO).full_clean()
        self.assertIn("estado_bancario", error.exception.message_dict)

    def test_efectivo_sin_cuenta_sigue_siendo_valido(self):
        self._pago(
            medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
            estado_bancario=PagoTesoreria.EstadoBancario.PENDIENTE,
        ).full_clean()

    def test_transferencia_sin_cuenta_sigue_siendo_invalida(self):
        with self.assertRaises(ValidationError) as error:
            self._pago(
                medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
                estado_bancario=PagoTesoreria.EstadoBancario.PENDIENTE,
            ).full_clean()
        self.assertIn("cuenta_bancaria", error.exception.message_dict)

    def test_la_caja_fuerte_no_acepta_un_movimiento_de_un_pago_sin_movimiento(self):
        payment = self._cancelar(self._payable("100.00"))
        movimiento = MovimientoCajaCentral(
            caja_central=get_boveda(self.empresa),
            tipo=MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            monto=Decimal("100.00"),
            concepto="No deberia existir",
            pago_tesoreria=payment,
        )
        with self.assertRaises(ValidationError) as error:
            movimiento.full_clean()
        self.assertIn("pago_tesoreria", error.exception.message_dict)

    def test_queryset_separa_plata_real_de_cancelaciones(self):
        real = self._transferir(self._payable("100.00", concepto="Real"), "100.00")
        regularizada = self._cancelar(self._payable("200.00", concepto="Regularizada"))
        interna = self._cancelar(
            self._payable("300.00", proveedor=self.panaderia, concepto="Pan"), medio=INTERNO, referencia=""
        )

        self.assertEqual(
            set(PagoTesoreria.objects.con_movimiento_de_fondos().values_list("pk", flat=True)), {real.pk}
        )
        self.assertEqual(
            set(PagoTesoreria.objects.sin_movimiento_de_fondos().values_list("pk", flat=True)),
            {regularizada.pk, interna.pk},
        )

    def test_proveedor_nace_no_interno(self):
        self.assertFalse(Proveedor.objects.get(pk=self.supplier.pk).es_interno)


class FormulariosNoOfrecenLosMediosNuevosTests(TestCase):
    def test_correccion_de_tipo_y_pago_por_proveedor_no_los_ofrecen(self):
        for medio in PagoTesoreria.MEDIOS_SIN_MOVIMIENTO:
            self.assertNotIn(medio, CLASE_POR_MEDIO_DE_PAGO)
            self.assertNotIn(medio, dict(BankPaymentMethodCorrectionForm().fields["medio_pago"].choices))
            self.assertNotIn(medio, dict(SupplierPaymentBatchForm.MEDIOS_HABILITADOS))

    def test_el_filtro_de_pagos_ofrece_interno_para_auditar_y_no_regularizacion(self):
        """US-3.20, slice 4: REGULARIZACION quedo sin uso (lo que no se pago por
        banco se registra en efectivo), asi que el filtro no lo sugiere."""
        choices = dict(PaymentFilterForm().fields["medio_pago"].choices)
        self.assertIn(INTERNO, choices)
        self.assertNotIn(REGULARIZACION, choices)
        self.assertEqual(choices[""], "Todos los medios con movimiento de fondos")
        for medio in (
            PagoTesoreria.MedioPago.TRANSFERENCIA,
            PagoTesoreria.MedioPago.CHEQUE,
            PagoTesoreria.MedioPago.ECHEQ,
            PagoTesoreria.MedioPago.EFECTIVO,
        ):
            self.assertIn(medio, choices)

    def test_ningun_formulario_ofrece_regularizacion(self):
        """Barre las opciones fijas de todos los formularios de tesoreria y caja,
        para que uno nuevo con MedioPago.choices no lo vuelva a ofrecer."""
        revisados = 0
        for modulo in (treasury_forms, cashops_forms):
            for nombre, clase in vars(modulo).items():
                if not (
                    isinstance(clase, type)
                    and issubclass(clase, django_forms.BaseForm)
                    and clase.__module__ == modulo.__name__
                ):
                    continue
                for campo_nombre, campo in clase.base_fields.items():
                    if isinstance(campo, django_forms.ModelChoiceField) or not hasattr(campo, "choices"):
                        continue
                    revisados += 1
                    valores = {str(valor) for valor, _ in campo.choices}
                    with self.subTest(formulario=nombre, campo=campo_nombre):
                        self.assertNotIn(REGULARIZACION, valores)
        self.assertGreater(revisados, 0)
        # Los que arman sus opciones al instanciarse.
        self.assertNotIn(REGULARIZACION, dict(PaymentFilterForm().fields["medio_pago"].choices))
        self.assertNotIn(REGULARIZACION, dict(BankPaymentMethodCorrectionForm().fields["medio_pago"].choices))
        self.assertNotIn(REGULARIZACION, dict(SupplierPaymentBatchForm().fields["medio_pago"].choices))


class RegresionDeDineroTests(PagoSinMovimientoBase):
    """Banco, caja fuerte, disponibilidades y resultado economico no se mueven."""

    def setUp(self):
        super().setUp()
        set_initial_bank_balance(
            cuenta_bancaria=self.bank_account,
            fecha_referencia=date(2026, 4, 30),
            importe=Decimal("5000.00"),
            motivo="Saldo inicial de prueba",
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("2000.00"),
            concepto="Fondo de caja fuerte",
            fecha=date(2026, 5, 1),
            actor=self.admin,
        )
        # Plata real: una transferencia y un pago en efectivo.
        self._transferir(self._payable("700.00", concepto="Pagada por banco"), "700.00", fecha=date(2026, 5, 12))
        register_cash_payment(
            payable=self._payable("300.00", concepto="Pagada en efectivo"),
            fecha_pago=date(2026, 5, 13),
            monto=Decimal("300.00"),
            actor=self.admin,
        )
        # Un egreso de tesoreria suelto con imputacion: cuenta como gasto.
        create_bank_movement(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=date(2026, 5, 14),
            monto=Decimal("50.00"),
            concepto="Comision",
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=PERIODO,
            actor=self.admin,
        )
        self.a_regularizar = self._payable("1000.00", concepto="Pagada fuera de Gerayse")
        self.interna = self._payable("400.00", proveedor=self.panaderia, concepto="Pan de mayo")
        self.queda_abierta = self._payable("250.00", concepto="Sigue abierta")

    def _foto_de_dinero(self):
        financiero = build_financial_period_snapshot(
            date_from=PERIODO, date_to=CORTE, empresa_ids=[self.empresa.pk]
        )
        disponibilidades = build_disponibilidades_snapshot(2026, 5, empresa_ids=[self.empresa.pk])
        return {
            "banco": _bank_balance_until(self.bank_account, CORTE),
            "boveda": get_boveda(self.empresa).saldo_actual,
            "total_bank_balance": financiero["total_bank_balance"],
            "central_cash_total": financiero["central_cash_total"],
            "total_consolidated": financiero["total_consolidated"],
            "bank_debits": financiero["bank_debits"],
            "bank_credits": financiero["bank_credits"],
            "central_cash_expense_period": financiero["central_cash_expense_period"],
            "disponibilidades": {
                clave: disponibilidades[clave]
                for clave in ("cash_in", "cash_out", "saldo_final_efectivo", "total_bancos_final", "total_consolidado")
            },
            "movimientos_bancarios": MovimientoBancario.objects.count(),
            "movimientos_boveda": MovimientoCajaCentral.objects.count(),
        }

    def _economico(self):
        return build_economic_period_snapshot(
            date_from=PERIODO, date_to=CORTE, empresa_ids=[self.empresa.pk]
        )

    def _regularizar(self):
        self._cancelar(self.a_regularizar)
        self._cancelar(self.interna, medio=INTERNO, referencia="")

    def test_banco_caja_fuerte_y_disponibilidades_quedan_iguales(self):
        antes = self._foto_de_dinero()

        self._regularizar()

        self.assertEqual(self._foto_de_dinero(), antes)

    def test_resultado_economico_igual_y_solo_baja_lo_que_falta_pagar(self):
        antes = self._economico()
        self.assertEqual(antes["debt_pending_total"], Decimal("1650.00"))

        self._regularizar()

        despues = self._economico()
        self.assertEqual(despues["debt_period_total"], antes["debt_period_total"])
        self.assertEqual(despues["treasury_expense_total"], antes["treasury_expense_total"])
        self.assertEqual(despues["economic_result"], antes["economic_result"])
        # Lo unico que baja: "De eso, falta pagar" queda en lo no regularizado.
        self.assertEqual(despues["debt_pending_total"], Decimal("250.00"))

    def test_anular_la_deuda_la_sigue_sacando_del_gasto(self):
        antes = self._economico()

        annul_payable(payable=self.queda_abierta, motivo="Mal cargada", actor=self.admin)

        despues = self._economico()
        self.assertEqual(despues["debt_period_total"], antes["debt_period_total"] - Decimal("250.00"))

    def test_deuda_viva_baja_y_pagos_recientes_no_muestran_cancelaciones(self):
        antes = build_financial_period_snapshot(
            date_from=PERIODO, date_to=CORTE, empresa_ids=[self.empresa.pk]
        )

        self._regularizar()

        despues = build_financial_period_snapshot(
            date_from=PERIODO, date_to=CORTE, empresa_ids=[self.empresa.pk]
        )
        self.assertEqual(antes["pending_total"], Decimal("1650.00"))
        self.assertEqual(despues["pending_total"], Decimal("250.00"))
        self.assertEqual(despues["pending_count"], 1)
        self.assertEqual(despues["red_count"], antes["red_count"] - 2)
        self.assertEqual(despues["debt_vs_bank_difference"], antes["debt_vs_bank_difference"] + Decimal("1400.00"))
        medios = {payment.medio_pago for payment in despues["recent_payments"]}
        self.assertEqual(medios, {PagoTesoreria.MedioPago.TRANSFERENCIA, PagoTesoreria.MedioPago.EFECTIVO})

    def test_historial_del_proveedor_separa_pagado_de_cancelado(self):
        self._transferir(self.queda_abierta, "50.00", referencia="TRF-2", fecha=date(2026, 5, 20))
        self._regularizar()

        real = build_supplier_history_snapshot(supplier=self.supplier)
        panaderia = build_supplier_history_snapshot(supplier=self.panaderia)

        self.assertEqual(real["historical_paid"], Decimal("1050.00"))
        self.assertEqual(real["historical_settled_without_funds"], Decimal("1000.00"))
        # La panaderia nunca cobro: no puede figurar como pagada.
        self.assertEqual(panaderia["historical_paid"], Decimal("0.00"))
        self.assertEqual(panaderia["historical_settled_without_funds"], Decimal("400.00"))

    def test_el_importador_no_toma_las_cancelaciones_como_efectivo_ya_cargado(self):
        self._regularizar()

        importador = Importador(actor=self.admin)
        importador.planificar()

        # Solo el pago en efectivo real cuenta como "ya en Gerayse".
        self.assertEqual(sum(importador.efectivo_registrado.values()), Decimal("300.00"))


class PantallasTests(PagoSinMovimientoBase):
    def setUp(self):
        super().setUp()
        self.real = self._transferir(self._payable("700.00", concepto="Factura con plata"), "700.00")
        self.deuda_regularizada = self._payable("1000.00", proveedor=self.panaderia, concepto="Pan de mayo")
        self.regularizacion = self._cancelar(self.deuda_regularizada)
        self._login()

    def _field(self, response, label):
        return next((f["value"] for f in response.context["fields"] if f["label"] == label), None)

    def test_detalle_del_pago_dice_sin_movimiento_y_no_aplica(self):
        response = self.client.get(reverse("treasury:pagos_detail", args=[self.regularizacion.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._field(response, "Cuenta de registro"), "Sin movimiento de fondos")
        self.assertEqual(self._field(response, "Estado bancario"), "No aplica")
        self.assertNotContains(response, "Caja central")
        self.assertNotContains(response, "Corregir tipo de pago")

    def test_el_pago_en_efectivo_sigue_diciendo_caja_central(self):
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("500.00"),
            concepto="Fondo",
            fecha=EMISION,
            actor=self.admin,
        )
        efectivo = register_cash_payment(
            payable=self._payable("100.00", concepto="En efectivo"),
            fecha_pago=EMISION,
            monto=Decimal("100.00"),
            actor=self.admin,
        )

        response = self.client.get(reverse("treasury:pagos_detail", args=[efectivo.pk]))

        self.assertEqual(self._field(response, "Cuenta de registro"), "Caja central")

    def test_detalle_de_la_deuda_separa_pagado_y_cancelado_sin_movimiento(self):
        response = self.client.get(
            reverse("treasury:cuentas_por_pagar_detail", args=[self.deuda_regularizada.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._field(response, "Pagado"), "$ 0,00")
        self.assertEqual(self._field(response, "Cancelado sin movimiento"), "$ 1.000,00")
        self.assertEqual(self._field(response, "Estado"), "Pagada (sin movimiento)")
        self.assertEqual(response.context["section_label"], "Pagada (sin movimiento)")
        # PAGADA: sin botones de pago, edicion ni anulacion.
        self.assertEqual(
            [action["label"] for action in response.context["actions"]], []
        )
        self.assertContains(response, "Sin movimiento de fondos")

    def test_detalle_de_una_deuda_pagada_con_plata_queda_como_antes(self):
        response = self.client.get(
            reverse("treasury:cuentas_por_pagar_detail", args=[self.real.cuenta_por_pagar_id])
        )

        self.assertEqual(self._field(response, "Pagado"), "$ 700,00")
        self.assertIsNone(self._field(response, "Cancelado sin movimiento"))
        self.assertEqual(self._field(response, "Estado"), "Pagada")

    def test_detalle_del_proveedor_muestra_lo_cancelado_aparte(self):
        response = self.client.get(reverse("treasury:proveedores_detail", args=[self.panaderia.pk]))

        self.assertEqual(self._field(response, "Pagado historico"), "$ 0,00")
        self.assertEqual(self._field(response, "Cancelado sin movimiento"), "$ 1.000,00")

        response = self.client.get(reverse("treasury:proveedores_detail", args=[self.supplier.pk]))
        self.assertEqual(self._field(response, "Pagado historico"), "$ 700,00")
        self.assertIsNone(self._field(response, "Cancelado sin movimiento"))

    def test_dashboard_no_cuenta_ni_lista_las_cancelaciones(self):
        response = self.client.get(
            reverse("treasury:dashboard"),
            {"fecha_desde": PERIODO.isoformat(), "fecha_hasta": CORTE.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        pagos = next(s for s in response.context["sections"] if s["label"] == "Pagos")
        self.assertEqual(pagos["count"], 1)
        self.assertEqual([p.pk for p in response.context["recent_payments"]], [self.real.pk])
        self.assertNotContains(response, "Panaderia Propia SRL")

    def test_listado_de_pagos_oculta_las_cancelaciones_por_defecto(self):
        response = self.client.get(reverse("treasury:pagos_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pagos y cancelaciones de deudas")
        self.assertContains(response, "Proveedor Real SA")
        self.assertNotContains(response, "Panaderia Propia SRL")
        self.assertNotContains(response, "Trazabilidad completa de egresos administrativos")

    def test_listado_de_pagos_muestra_las_cancelaciones_al_elegir_su_medio(self):
        response = self.client.get(reverse("treasury:pagos_list"), {"medio_pago": REGULARIZACION})

        self.assertContains(response, "Panaderia Propia SRL")
        self.assertContains(response, "Sin movimiento de fondos")
        self.assertNotContains(response, "Proveedor Real SA")

        response = self.client.get(reverse("treasury:pagos_list"), {"medio_pago": INTERNO})
        self.assertNotContains(response, "Panaderia Propia SRL")

    def test_listado_de_pagos_pagina_y_conserva_el_filtro(self):
        for numero in range(100):
            self._cancelar(self._payable("10.00", concepto=f"Relleno {numero}"))

        response = self.client.get(reverse("treasury:pagos_list"), {"medio_pago": REGULARIZACION})

        self.assertEqual(len(response.context["items"]), 100)
        pagination = response.context["pagination"]
        self.assertEqual(pagination["label"], "Pagina 1 de 2 (101 pagos)")
        self.assertEqual(pagination["prev_url"], "")
        self.assertIn("medio_pago=REGULARIZACION", pagination["next_url"])
        self.assertIn("page=2", pagination["next_url"])
        self.assertContains(response, "Siguiente")

        response = self.client.get(
            reverse("treasury:pagos_list"), {"medio_pago": REGULARIZACION, "page": 2}
        )
        self.assertEqual(len(response.context["items"]), 1)
        self.assertIn("page=1", response.context["pagination"]["prev_url"])

    def test_listado_de_pagos_sin_pagina_extra_no_muestra_paginador(self):
        response = self.client.get(reverse("treasury:pagos_list"))

        self.assertIsNone(response.context["pagination"])
        self.assertNotContains(response, "Siguiente")


class AdminProveedorInternoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-prov-interno", password="x", email="interno@test.com"
        )
        self.panaderia = Proveedor.objects.create(razon_social="Panaderia Propia SRL", es_interno=True)
        Proveedor.objects.create(razon_social="Proveedor Comun SA")
        self.client.force_login(self.admin)

    def test_lista_y_filtra_por_proveedor_interno(self):
        self.assertIn("es_interno", ProveedorAdmin.list_display)
        self.assertIn("es_interno", ProveedorAdmin.list_filter)

        response = self.client.get(
            reverse("admin:treasury_proveedor_changelist"), {"es_interno__exact": "1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panaderia Propia SRL")
        self.assertNotContains(response, "Proveedor Comun SA")

    def test_no_se_edita_a_mano(self):
        """Lo marca el comando, que deja informe y lote."""
        self.assertIn("es_interno", ProveedorAdmin.readonly_fields)
        response = self.client.get(
            reverse("admin:treasury_proveedor_change", args=[self.panaderia.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("es_interno", response.context["adminform"].form.fields)


class PagoRealSobreDeudaCanceladaSinMovimientoTests(PagoSinMovimientoBase):
    """US-3.20, slice 4 reducido: la cancelacion sin movimiento NO se reemplaza
    sola por el pago real (D3 quedo cancelado). Un pago con plata sobre una deuda
    ya saldada asi tiene que cortarse con un error que diga por que y que hacer,
    en todos los caminos, y sin dejar nada a medias."""

    def setUp(self):
        super().setUp()
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("5000.00"),
            concepto="Fondo de caja fuerte",
            fecha=date(2026, 5, 1),
            actor=self.admin,
        )
        self.deuda = self._payable("1000.00", proveedor=self.panaderia, concepto="Pan de mayo")
        self.cancelacion = self._cancelar(self.deuda, medio=INTERNO, referencia="")
        self.deuda.refresh_from_db()

    def _foto(self):
        deuda = CuentaPorPagar.objects.get(pk=self.deuda.pk)
        return {
            "pagos": PagoTesoreria.objects.count(),
            "movimientos_bancarios": MovimientoBancario.objects.count(),
            "movimientos_boveda": MovimientoCajaCentral.objects.count(),
            "boveda": get_boveda(self.empresa).saldo_actual,
            "deuda": (deuda.estado, deuda.saldo_pendiente),
        }

    def _assert_rechazo_claro(self, error, detalle="proveedor interno"):
        mensaje = " ".join(error.exception.messages)
        self.assertIn("ya esta cancelada sin movimiento de fondos", mensaje)
        self.assertIn(f"({detalle})", mensaje)
        self.assertIn("no admite un pago real", mensaje)
        self.assertIn("anule primero esa cancelacion", mensaje)
        self.assertIn("cuenta_por_pagar", error.exception.message_dict)

    def _pagos_reales(self, payable):
        return {
            "transferencia": lambda: register_transfer_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                referencia="TRF-REAL",
                actor=self.admin,
            ),
            "cheque": lambda: register_cheque_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                referencia="CH-1",
                actor=self.admin,
            ),
            "echeq": lambda: register_echeq_payment(
                payable=payable,
                bank_account=self.bank_account,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                referencia="ECH-1",
                actor=self.admin,
            ),
            "efectivo": lambda: register_cash_payment(
                payable=payable,
                fecha_pago=CORTE,
                monto=Decimal("1000.00"),
                actor=self.admin,
            ),
        }

    def test_ningun_medio_con_fondos_la_paga_y_el_error_dice_que_hacer(self):
        antes = self._foto()

        for medio, pagar in self._pagos_reales(self.deuda).items():
            with self.subTest(medio=medio):
                with self.assertRaises(ValidationError) as error:
                    pagar()
                self._assert_rechazo_claro(error)
                self.assertEqual(self._foto(), antes)

    def test_una_regularizacion_tambien_se_explica(self):
        deuda = self._payable("500.00", concepto="Regularizada")
        self._cancelar(deuda)
        deuda.refresh_from_db()

        with self.assertRaises(ValidationError) as error:
            register_transfer_payment(
                payable=deuda,
                bank_account=self.bank_account,
                fecha_pago=CORTE,
                monto=Decimal("500.00"),
                actor=self.admin,
            )

        self._assert_rechazo_claro(error, detalle="regularizacion")

    def test_la_deuda_leida_antes_de_cancelarla_igual_se_rechaza(self):
        """Pantalla abierta o reparto armado antes de la cancelacion: la deuda en
        memoria dice PENDIENTE con saldo; el control vale porque se hace con la
        deuda bloqueada."""
        vieja = self._payable("800.00", proveedor=self.panaderia, concepto="Pan de junio")
        self._cancelar(CuentaPorPagar.objects.get(pk=vieja.pk), medio=INTERNO, referencia="")
        self.assertEqual(vieja.estado, CuentaPorPagar.Estado.PENDIENTE)
        debito = self._debito("800.00", concepto="Debito real")
        antes = self._foto()

        with self.assertRaises(ValidationError) as error:
            register_transfer_payment(
                payable=vieja,
                bank_account=self.bank_account,
                fecha_pago=CORTE,
                monto=Decimal("800.00"),
                actor=self.admin,
            )
        self._assert_rechazo_claro(error)

        with self.assertRaises(ValidationError) as error:
            pay_debt_from_bank_movement(bank_movement=debito, payable=vieja, actor=self.admin)
        self._assert_rechazo_claro(error)

        self.assertEqual(self._foto(), antes)
        debito.refresh_from_db()
        self.assertEqual(importe_sin_asignar_del_movimiento(debito), Decimal("800.00"))

    def test_desde_un_debito_explica_en_lugar_de_decir_que_le_quedan_cero(self):
        debito = self._debito("1000.00", concepto="Debito real")

        with self.assertRaises(ValidationError) as error:
            pay_debt_from_bank_movement(bank_movement=debito, payable=self.deuda, actor=self.admin)

        self._assert_rechazo_claro(error)
        self.assertNotIn("le quedan", " ".join(error.exception.messages))
        debito.refresh_from_db()
        self.assertEqual(debito.origen, MovimientoBancario.Origen.MANUAL)
        self.assertFalse(debito.pagos.exists())

    def test_el_reparto_de_una_transferencia_no_paga_ninguna(self):
        abierta = self._payable("300.00", concepto="Factura abierta")
        debito = self._debito("1300.00", concepto="Pago semanal")

        with self.assertRaises(ValidationError) as error:
            pay_debts_from_bank_movement(
                bank_movement=debito,
                asignaciones=[(abierta, Decimal("300.00")), (self.deuda, Decimal("1000.00"))],
                actor=self.admin,
            )

        self._assert_rechazo_claro(error)
        abierta.refresh_from_db()
        self.assertEqual(abierta.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(debito.pagos.count(), 0)
        self.assertEqual(PagoTesoreria.objects.count(), 1)

    def test_el_pago_por_proveedor_no_paga_ninguna(self):
        abierta = self._payable("300.00", proveedor=self.panaderia, concepto="Pan abierto")
        antes = self._foto()

        with self.assertRaises(ValidationError) as error:
            register_supplier_payment_batch(
                proveedor=self.panaderia,
                lineas=[(abierta, Decimal("300.00")), (self.deuda, Decimal("1000.00"))],
                medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
                fecha_pago=CORTE,
                actor=self.admin,
            )

        self._assert_rechazo_claro(error)
        self.assertEqual(self._foto(), antes)
        abierta.refresh_from_db()
        self.assertEqual(abierta.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_saldada_en_parte_con_plata_y_en_parte_sin_movimiento_tambien_se_explica(self):
        deuda = self._payable("1000.00", proveedor=self.panaderia, concepto="Pan mixto")
        self._transferir(deuda, "400.00", referencia="TRF-PARTE")
        self._cancelar(deuda, medio=INTERNO, referencia="")
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)

        with self.assertRaises(ValidationError) as error:
            register_cash_payment(
                payable=deuda, fecha_pago=CORTE, monto=Decimal("100.00"), actor=self.admin
            )

        self._assert_rechazo_claro(error)

    def test_una_deuda_pagada_con_plata_sigue_con_el_mensaje_de_siempre(self):
        deuda = self._payable("200.00", concepto="Pagada por banco")
        self._transferir(deuda, "200.00", referencia="TRF-TODO")
        deuda.refresh_from_db()

        with self.assertRaises(ValidationError) as error:
            register_cash_payment(
                payable=deuda, fecha_pago=CORTE, monto=Decimal("200.00"), actor=self.admin
            )

        mensaje = " ".join(error.exception.messages)
        self.assertIn("ya esta cancelada", mensaje)
        self.assertNotIn("sin movimiento", mensaje)

    def test_anulando_la_cancelacion_se_puede_pagar_de_verdad(self):
        """Lo que indica el mensaje funciona: anular y despues pagar."""
        annul_payment(payment=self.cancelacion, motivo="Se pago por banco", actor=self.admin)
        self.deuda.refresh_from_db()

        pago = register_transfer_payment(
            payable=self.deuda,
            bank_account=self.bank_account,
            fecha_pago=CORTE,
            monto=Decimal("1000.00"),
            referencia="TRF-REAL",
            actor=self.admin,
        )

        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertTrue(pago.mueve_fondos)
        breakdown = build_payable_settlement_breakdown(self.deuda)
        self.assertEqual(breakdown["paid_with_funds"], Decimal("1000.00"))
        self.assertEqual(breakdown["settled_without_funds"], Decimal("0.00"))


class PantallasDePagoSobreDeudaCanceladaTests(PagoSinMovimientoBase):
    """Las pantallas de pago no ofrecen la deuda saldada sin movimiento, y si el
    servicio la rechaza (carrera), el mensaje claro llega a la persona."""

    def setUp(self):
        super().setUp()
        self.deuda = self._payable("1000.00", proveedor=self.panaderia, concepto="Pan cancelado")
        self._cancelar(self.deuda, medio=INTERNO, referencia="")
        self._login()

    def _post_transferencia(self):
        return self.client.post(
            reverse("treasury:pagos_transferencia_create"),
            {
                "cuenta_por_pagar": self.deuda.pk,
                "cuenta_bancaria": self.bank_account.pk,
                "fecha_pago": CORTE.isoformat(),
                "monto": "1000.00",
                "referencia": "TRF-PANTALLA",
            },
        )

    def test_el_formulario_de_pago_no_la_ofrece_ni_la_acepta(self):
        response = self.client.get(reverse("treasury:pagos_transferencia_create"))
        self.assertNotIn(
            self.deuda.pk,
            response.context["form"].fields["cuenta_por_pagar"].queryset.values_list("pk", flat=True),
        )

        response = self._post_transferencia()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PagoTesoreria.objects.count(), 1)
        self.assertEqual(MovimientoBancario.objects.count(), 0)

    def test_si_la_cancelan_con_la_pantalla_abierta_el_error_se_ve(self):
        """Simula la carrera: el desplegable todavia la ofrece cuando se envia."""
        with mock.patch(
            "treasury.forms.open_payables_queryset",
            side_effect=lambda empresa_ids=None: CuentaPorPagar.objects.all(),
        ):
            response = self._post_transferencia()

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "ya esta cancelada sin movimiento de fondos (proveedor interno)",
            status_code=400,
        )
        self.assertEqual(PagoTesoreria.objects.count(), 1)
        self.assertEqual(MovimientoBancario.objects.count(), 0)

    def test_el_reparto_de_un_debito_no_la_ofrece_ni_la_paga(self):
        debito = self._debito("1000.00", concepto="Debito real")
        url = reverse("treasury:bank_movements_pay_debt", args=[debito.pk])

        response = self.client.get(url)
        self.assertNotIn(self.deuda.pk, [fila["payable"].pk for fila in response.context["facturas"]])

        response = self.client.post(
            url, {"payable_id": [str(self.deuda.pk)], f"monto_{self.deuda.pk}": "1000.00"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(debito.pagos.count(), 0)
        self.assertEqual(PagoTesoreria.objects.count(), 1)

    def test_el_listado_de_pagos_no_ofrece_regularizacion_si_no_hay_ninguna(self):
        response = self.client.get(reverse("treasury:pagos_list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'value="{REGULARIZACION}"')
        self.assertContains(response, f'value="{INTERNO}"')

    def test_si_hubiera_una_regularizacion_el_filtro_la_ofrece_para_no_esconderla(self):
        self._cancelar(self._payable("100.00", concepto="Regularizada a mano"))

        response = self.client.get(reverse("treasury:pagos_list"))
        self.assertContains(response, f'value="{REGULARIZACION}"')

        response = self.client.get(reverse("treasury:pagos_list"), {"medio_pago": REGULARIZACION})
        self.assertContains(response, "Regularizada a mano")
