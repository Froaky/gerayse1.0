"""D3 (US-3.20, retomado el 04/11/2026): la regularizacion se reemplaza sola.

Una factura regularizada (REGULARIZACION: pagada fuera de Gerayse, sin mover
plata) figura PAGADA. Si despues tesoreria carga el pago real (transferencia,
cheque, ECHEQ, efectivo, desde un debito del banco, repartiendo una
transferencia o pagando por proveedor), Gerayse anula la regularizacion y deja
el pago real, en la misma operacion. Si el pago real cubre menos, la diferencia
se vuelve a regularizar y la factura sigue PAGADA. INTERNO no se reemplaza.
"""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from treasury.forms import CashPaymentForm, SupplierPaymentBatchForm, TransferPaymentForm, open_payables_queryset
from treasury.models import CuentaPorPagar, MovimientoBancario, MovimientoCajaCentral, PagoTesoreria
from treasury.services import (
    MOTIVO_REEMPLAZO_REGULARIZACION,
    build_economic_period_snapshot,
    get_boveda,
    importe_sin_asignar_del_movimiento,
    pay_debt_from_bank_movement,
    pay_debts_from_bank_movement,
    register_cash_payment,
    register_central_cash_movement,
    register_cheque_payment,
    register_echeq_payment,
    register_supplier_payment_batch,
    register_transfer_payment,
    saldo_para_pago_real,
)
from treasury.tests_pago_sin_movimiento import CORTE, EMISION, INTERNO, PERIODO, REGULARIZACION, PagoSinMovimientoBase


class ReemplazoDeLaRegularizacionBase(PagoSinMovimientoBase):
    def setUp(self):
        super().setUp()
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("5000.00"),
            concepto="Fondo de caja fuerte",
            fecha=EMISION,
            actor=self.admin,
        )
        self.deuda = self._payable("1000.00", concepto="Pagada fuera de Gerayse")
        self.regularizacion = self._cancelar(self.deuda)
        self.deuda.refresh_from_db()

    def _deuda(self):
        return CuentaPorPagar.objects.get(pk=self.deuda.pk)

    def _vivas(self, medio):
        return PagoTesoreria.objects.filter(
            cuenta_por_pagar=self.deuda, estado=PagoTesoreria.Estado.REGISTRADO, medio_pago=medio
        )

    def _assert_reemplazada(self):
        regularizacion = PagoTesoreria.objects.get(pk=self.regularizacion.pk)
        self.assertEqual(regularizacion.estado, PagoTesoreria.Estado.ANULADO)
        self.assertEqual(regularizacion.motivo_anulacion, MOTIVO_REEMPLAZO_REGULARIZACION)

    def _foto(self):
        deuda = self._deuda()
        return {
            "pagos": list(PagoTesoreria.objects.order_by("pk").values_list("pk", "estado")),
            "movimientos_bancarios": MovimientoBancario.objects.count(),
            "movimientos_boveda": MovimientoCajaCentral.objects.count(),
            "boveda": get_boveda(self.empresa).saldo_actual,
            "deuda": (deuda.estado, deuda.saldo_pendiente),
        }


class PagoRealReemplazaTests(ReemplazoDeLaRegularizacionBase):
    def test_la_regularizada_admite_pago_real_por_lo_regularizado(self):
        self.assertEqual(self.deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(saldo_para_pago_real(self.deuda), Decimal("1000.00"))

    def test_cada_medio_con_fondos_reemplaza_la_regularizacion(self):
        medios = {
            "transferencia": lambda deuda: register_transfer_payment(
                payable=deuda, bank_account=self.bank_account, fecha_pago=CORTE,
                monto=Decimal("1000.00"), referencia="TRF-REAL", actor=self.admin,
            ),
            "cheque": lambda deuda: register_cheque_payment(
                payable=deuda, bank_account=self.bank_account, fecha_pago=CORTE,
                monto=Decimal("1000.00"), referencia="CH-REAL", actor=self.admin,
            ),
            "echeq": lambda deuda: register_echeq_payment(
                payable=deuda, bank_account=self.bank_account, fecha_pago=CORTE,
                monto=Decimal("1000.00"), referencia="ECH-REAL", actor=self.admin,
            ),
            "efectivo": lambda deuda: register_cash_payment(
                payable=deuda, fecha_pago=CORTE, monto=Decimal("1000.00"), actor=self.admin,
            ),
        }
        for medio, pagar in medios.items():
            with self.subTest(medio=medio):
                deuda = self._payable("1000.00", concepto=f"Regularizada {medio}")
                regularizacion = self._cancelar(deuda)
                deuda.refresh_from_db()

                pago = pagar(deuda)

                deuda.refresh_from_db()
                regularizacion.refresh_from_db()
                self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)
                self.assertTrue(pago.mueve_fondos)
                self.assertEqual(regularizacion.estado, PagoTesoreria.Estado.ANULADO)
                self.assertEqual(regularizacion.motivo_anulacion, MOTIVO_REEMPLAZO_REGULARIZACION)
                self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
                self.assertEqual(deuda.saldo_pendiente, Decimal("0.00"))
                self.assertFalse(
                    deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO, medio_pago=REGULARIZACION).exists()
                )

    def test_el_efectivo_real_baja_la_caja_fuerte_y_la_transferencia_el_banco(self):
        boveda_antes = get_boveda(self.empresa).saldo_actual

        register_cash_payment(payable=self.deuda, fecha_pago=CORTE, monto=Decimal("1000.00"), actor=self.admin)

        self.assertEqual(get_boveda(self.empresa).saldo_actual, boveda_antes - Decimal("1000.00"))
        self._assert_reemplazada()

        otra = self._payable("700.00", concepto="Otra regularizada")
        self._cancelar(otra)
        debitos_antes = MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO).count()
        register_transfer_payment(
            payable=otra, bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("700.00"), referencia="TRF-OTRA", actor=self.admin,
        )
        self.assertEqual(
            MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO).count(), debitos_antes + 1
        )

    def test_pago_parcial_vuelve_a_regularizar_solo_el_resto(self):
        pago = register_transfer_payment(
            payable=self.deuda, bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("400.00"), referencia="TRF-400", actor=self.admin,
        )

        self._assert_reemplazada()
        resto = self._vivas(REGULARIZACION).get()
        self.assertEqual(resto.monto, Decimal("600.00"))
        self.assertEqual(resto.fecha_pago, self.regularizacion.fecha_pago)
        self.assertEqual(resto.referencia, "REG-2026-05-31 (resto)")
        self.assertIn(f"#{pago.pk}", resto.observaciones)
        self.assertEqual(resto.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
        deuda = self._deuda()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(saldo_para_pago_real(deuda), Decimal("600.00"))

    def test_dos_pagos_parciales_reemplazan_todo(self):
        register_transfer_payment(
            payable=self.deuda, bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("400.00"), referencia="TRF-400", actor=self.admin,
        )
        register_cash_payment(payable=self._deuda(), fecha_pago=CORTE, monto=Decimal("600.00"), actor=self.admin)

        self.assertFalse(self._vivas(REGULARIZACION).exists())
        reales = PagoTesoreria.objects.filter(
            cuenta_por_pagar=self.deuda, estado=PagoTesoreria.Estado.REGISTRADO
        ).con_movimiento_de_fondos()
        self.assertEqual(sum(p.monto for p in reales), Decimal("1000.00"))
        self.assertEqual(self._deuda().estado, CuentaPorPagar.Estado.PAGADA)

    def test_pagar_mas_de_lo_regularizado_falla_sin_tocar_nada(self):
        antes = self._foto()

        with self.assertRaises(ValidationError):
            register_transfer_payment(
                payable=self.deuda, bank_account=self.bank_account, fecha_pago=CORTE,
                monto=Decimal("1200.00"), referencia="TRF-1200", actor=self.admin,
            )

        self.assertEqual(self._foto(), antes)
        self.assertEqual(self._vivas(REGULARIZACION).get().pk, self.regularizacion.pk)

    def test_regularizacion_parcial_y_saldo_abierto(self):
        # 1000 de factura: 300 regularizados y 700 abiertos. Un pago de 900
        # reemplaza los 300 y paga 600 del saldo: no queda nada para regularizar.
        deuda = self._payable("1000.00", concepto="Parcialmente regularizada")
        self._cancelar(deuda, monto="300.00")
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PARCIAL)

        register_transfer_payment(
            payable=deuda, bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("900.00"), referencia="TRF-900", actor=self.admin,
        )

        deuda.refresh_from_db()
        self.assertEqual(deuda.saldo_pendiente, Decimal("100.00"))
        self.assertFalse(
            deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO, medio_pago=REGULARIZACION).exists()
        )

    def test_el_resultado_economico_no_cambia(self):
        antes = build_economic_period_snapshot(date_from=PERIODO, date_to=CORTE)

        register_cash_payment(payable=self.deuda, fecha_pago=CORTE, monto=Decimal("1000.00"), actor=self.admin)

        despues = build_economic_period_snapshot(date_from=PERIODO, date_to=CORTE)
        for clave in ("debt_period_total", "treasury_expense_total", "expense_total", "economic_result"):
            self.assertEqual(despues[clave], antes[clave], clave)

    def test_el_mismo_formulario_dos_veces_no_reemplaza_dos_veces(self):
        token = uuid.uuid4()
        primero = register_transfer_payment(
            payable=self.deuda, bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("400.00"), referencia="TRF-400", token_alta=token, actor=self.admin,
        )
        resto = self._vivas(REGULARIZACION).get()

        segundo = register_transfer_payment(
            payable=self._deuda(), bank_account=self.bank_account, fecha_pago=CORTE,
            monto=Decimal("400.00"), referencia="TRF-400", token_alta=token, actor=self.admin,
        )

        self.assertEqual(segundo.pk, primero.pk)
        self.assertEqual(self._vivas(REGULARIZACION).get().pk, resto.pk)

    def test_interno_sigue_sin_reemplazarse(self):
        pan = self._payable("500.00", proveedor=self.panaderia, concepto="Pan")
        self._cancelar(pan, medio=INTERNO, referencia="")
        pan.refresh_from_db()

        with self.assertRaises(ValidationError) as error:
            register_cash_payment(payable=pan, fecha_pago=CORTE, monto=Decimal("500.00"), actor=self.admin)

        self.assertIn("proveedor interno", " ".join(error.exception.messages))


class PagoDesdeElBancoReemplazaTests(ReemplazoDeLaRegularizacionBase):
    def test_atar_un_debito_a_la_regularizada(self):
        debito = self._debito("1000.00", concepto="Transferencia real")

        pago = pay_debt_from_bank_movement(bank_movement=debito, payable=self.deuda, actor=self.admin)

        self._assert_reemplazada()
        debito.refresh_from_db()
        self.assertEqual(pago.movimiento_bancario_id, debito.pk)
        self.assertEqual(debito.origen, MovimientoBancario.Origen.PAGO_TESORERIA)
        self.assertEqual(importe_sin_asignar_del_movimiento(debito), Decimal("0.00"))
        self.assertEqual(self._deuda().estado, CuentaPorPagar.Estado.PAGADA)

    def test_repartir_una_transferencia_entre_una_regularizada_y_una_abierta(self):
        abierta = self._payable("500.00", concepto="Abierta")
        debito = self._debito("1500.00", concepto="Pago semanal")

        pagos = pay_debts_from_bank_movement(
            bank_movement=debito,
            asignaciones=[(self.deuda, Decimal("1000.00")), (abierta, Decimal("500.00"))],
            actor=self.admin,
        )

        self.assertEqual(len(pagos), 2)
        self._assert_reemplazada()
        abierta.refresh_from_db()
        self.assertEqual(abierta.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(importe_sin_asignar_del_movimiento(debito), Decimal("0.00"))

    def test_pagar_por_proveedor_incluye_la_regularizada(self):
        abierta = self._payable("500.00", concepto="Abierta")

        pagos = register_supplier_payment_batch(
            proveedor=self.supplier,
            lineas=[(self.deuda, Decimal("1000.00")), (abierta, Decimal("500.00"))],
            bank_account=self.bank_account,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=CORTE,
            referencia="TRF-LOTE",
            actor=self.admin,
        )

        self.assertEqual(len(pagos), 2)
        self._assert_reemplazada()


class SelectoresOfrecenLaRegularizadaTests(ReemplazoDeLaRegularizacionBase):
    def test_open_payables_la_trae_despues_de_las_abiertas(self):
        abierta = self._payable("500.00", concepto="Abierta")
        pagada_en_serio = self._payable("200.00", concepto="Pagada con plata")
        self._transferir(pagada_en_serio, "200.00", referencia="TRF-200")
        pan = self._payable("300.00", proveedor=self.panaderia, concepto="Pan")
        self._cancelar(pan, medio=INTERNO, referencia="")

        filas = list(open_payables_queryset([self.empresa.pk]))

        self.assertEqual([fila.pk for fila in filas], [abierta.pk, self.deuda.pk])
        regularizada = filas[1]
        self.assertEqual(regularizada.es_regularizada, 1)
        self.assertEqual(regularizada.monto_regularizado, Decimal("1000.00"))
        self.assertEqual(regularizada.saldo_a_pagar, Decimal("1000.00"))
        self.assertEqual(filas[0].saldo_a_pagar, Decimal("500.00"))

    def test_los_formularios_de_pago_la_ofrecen_rotulada(self):
        for form_class in (TransferPaymentForm, CashPaymentForm):
            with self.subTest(form=form_class.__name__):
                campo = form_class(empresa_ids=[self.empresa.pk]).fields["cuenta_por_pagar"]
                opcion = campo.queryset.get(pk=self.deuda.pk)
                self.assertTrue(campo.label_from_instance(opcion).endswith(" - regularizada $1000.00"))

    def test_pago_por_proveedor_la_precarga_con_lo_regularizado(self):
        form = SupplierPaymentBatchForm(proveedor=self.supplier, empresa_ids=[self.empresa.pk])

        self.assertEqual(form.fields[f"monto_{self.deuda.pk}"].initial, Decimal("1000.00"))

    def test_el_detalle_de_la_deuda_explica_y_ofrece_pagarla(self):
        self._login()

        response = self.client.get(reverse("treasury:cuentas_por_pagar_detail", args=[self.deuda.pk]))

        self.assertContains(response, "Figura pagada fuera de Gerayse")
        self.assertContains(response, reverse("treasury:pagos_transferencia_create") + f"?payable={self.deuda.pk}")
        self.assertContains(response, reverse("treasury:pagos_efectivo_create") + f"?payable={self.deuda.pk}")

    def test_repartir_transferencia_la_lista_marcada(self):
        self._login()
        debito = self._debito("1000.00", concepto="Transferencia real")

        response = self.client.get(reverse("treasury:bank_movements_pay_debt", args=[debito.pk]))

        self.assertContains(response, "Regularizada")
        self.assertContains(response, "reemplaza a la regularización")
