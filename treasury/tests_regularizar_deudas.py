"""US-3.20, slice 2: comando `regularizar_deudas` y treasury/regularizacion.py.

Tesoreria declaro pagadas las facturas de proveedores hasta el 23/09/2026. Lo
que no esta pagado por banco se pago en EFECTIVO desde la caja fuerte, asi que
el modo general registra pagos en efectivo reales (con su EGRESO_PAGO en la
boveda de la empresa de la deuda), a la fecha de cada factura. El modo interno
cancela las facturas de la panaderia propia con INTERNO, sin plata. Nunca se
usa REGULARIZACION.
"""
import csv
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Caja, Empresa, RubroOperativo, Sucursal, Turno
from treasury.models import (
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.regularizacion import (
    MEDIOS_DEL_COMANDO,
    Accion,
    FilaDelPlan,
    Regularizacion,
    Reversion,
    leer_plan,
    token_de_pago,
)
from treasury.services import (
    annul_central_cash_movement,
    annul_payable,
    annul_payment,
    build_economic_period_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_supplier,
    get_boveda,
    register_carga_inicial_caja_central,
    register_cash_payment,
    register_central_cash_movement,
    register_egreso_tesoreria,
    register_payable,
    register_payment,
    register_special_commitment,
    register_transfer_payment,
    set_supplier_internal,
    update_payable,
)

User = get_user_model()

EFECTIVO = PagoTesoreria.MedioPago.EFECTIVO
INTERNO = PagoTesoreria.MedioPago.INTERNO
REGULARIZACION = PagoTesoreria.MedioPago.REGULARIZACION

CORTE = date(2026, 9, 23)
AGOSTO = date(2026, 8, 10)
LOTE = "REG-2026-09-23"
SIN_SUCURSAL = object()


class RegularizacionBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="tesoreria-reg", password="x", email="reg@test.com"
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI SRL")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO SRL")
        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.ec1 = Sucursal.objects.create(
            nombre="Cafe Centro", codigo="EC1", razon_social="ARMADI SRL", empresa=self.armadi
        )
        self.vivre = Sucursal.objects.create(
            nombre="Vivre", codigo="VIV-01", razon_social="MAPOGO SRL", empresa=self.mapogo
        )
        self.rubro = RubroOperativo.objects.create(nombre="Materia prima")
        self.categoria = create_payable_category(
            nombre="Materia prima", rubro_operativo=self.rubro, actor=self.admin
        )
        self.proveedor = create_supplier(razon_social="Carnes del Norte SA", actor=self.admin)
        self.verduleria = create_supplier(razon_social="Verduleria La Huerta", actor=self.admin)
        self.cuenta = create_bank_account(
            nombre="Macro ARMADI",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="300-1",
            cbu="2850590940090418135201",
            empresa=self.armadi,
            actor=self.admin,
        )
        for empresa, monto in ((self.armadi, "1000000.00"), (self.mapogo, "500000.00")):
            register_carga_inicial_caja_central(
                empresa=empresa,
                fecha=date(2026, 6, 1),
                monto=Decimal(monto),
                motivo="Saldo inicial de prueba",
                actor=self.admin,
            )

    # --- datos ----------------------------------------------------------------
    def _deuda(
        self,
        importe="1000.00",
        *,
        proveedor=None,
        sucursal=None,
        emision=AGOSTO,
        categoria=None,
        concepto="Factura",
        comprobante="",
    ):
        return register_payable(
            sucursal=None if sucursal is SIN_SUCURSAL else (sucursal or self.ec1),
            proveedor=proveedor or self.proveedor,
            categoria=categoria or self.categoria,
            concepto=concepto,
            referencia_comprobante=comprobante,
            fecha_emision=emision,
            fecha_vencimiento=emision + timedelta(days=15),
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def _proveedor(self, razon_social):
        return create_supplier(razon_social=razon_social, actor=self.admin)

    def _categoria(self, rubro_nombre):
        rubro = RubroOperativo.objects.create(nombre=rubro_nombre)
        return create_payable_category(nombre=rubro_nombre, rubro_operativo=rubro, actor=self.admin)

    def _debito(self, monto, *, fecha, proveedor=None, cuenta=None):
        return create_bank_movement(
            cuenta_bancaria=cuenta or self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=fecha,
            monto=Decimal(monto),
            concepto="Transferencia a proveedor",
            clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
            proveedor=proveedor or self.proveedor,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.ec1,
            periodo_pago=fecha.replace(day=1),
            actor=self.admin,
        )

    def _regularizacion(self, **opciones):
        opciones.setdefault("hasta", CORTE)
        return Regularizacion(actor=self.admin, **opciones)

    def _plan(self, **opciones):
        regularizacion = self._regularizacion(**opciones)
        regularizacion.planificar()
        return regularizacion

    def _aplicar(self, **opciones):
        regularizacion = self._plan(**opciones)
        regularizacion.aplicar()
        return regularizacion

    @staticmethod
    def _por_deuda(proceso):
        return {fila.deuda_id: fila for fila in proceso.filas if fila.deuda_id is not None}

    def _boveda(self, empresa):
        return get_boveda(empresa).saldo_actual


class PlanTests(RegularizacionBase):
    def test_la_simulacion_no_escribe_nada(self):
        self._deuda("1000.00")
        self._deuda("2000.00", sucursal=self.vivre)
        movimientos = MovimientoCajaCentral.objects.count()

        regularizacion = self._plan()

        self.assertEqual(PagoTesoreria.objects.count(), 0)
        self.assertEqual(MovimientoCajaCentral.objects.count(), movimientos)
        self.assertEqual(
            {f.accion for f in regularizacion.filas}, {Accion.A_REGULARIZAR}
        )
        self.assertEqual(
            CuentaPorPagar.objects.filter(estado=CuentaPorPagar.Estado.PENDIENTE).count(), 2
        )

    def test_borde_de_fecha_el_corte_entra_y_el_dia_siguiente_no(self):
        en_el_corte = self._deuda("100.00", emision=CORTE)
        despues = self._deuda("200.00", emision=CORTE + timedelta(days=1))

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[en_el_corte.pk].accion, Accion.A_REGULARIZAR)
        self.assertNotIn(despues.pk, filas)

    def test_pagadas_y_anuladas_no_aparecen(self):
        pagada = self._deuda("300.00", concepto="Ya pagada")
        register_transfer_payment(
            payable=pagada,
            bank_account=self.cuenta,
            fecha_pago=AGOSTO,
            monto=Decimal("300.00"),
            referencia="TRF-1",
            actor=self.admin,
        )
        anulada = self._deuda("400.00", concepto="Anulada")
        annul_payable(payable=anulada, motivo="Mal cargada", actor=self.admin)

        filas = self._por_deuda(self._plan())

        self.assertNotIn(pagada.pk, filas)
        self.assertNotIn(anulada.pk, filas)

    def test_parcial_toma_solo_el_saldo(self):
        parcial = self._deuda("1000.00")
        register_cash_payment(
            payable=parcial, fecha_pago=AGOSTO, monto=Decimal("400.00"), actor=self.admin
        )

        fila = self._por_deuda(self._plan())[parcial.pk]

        self.assertEqual(fila.accion, Accion.A_REGULARIZAR)
        self.assertEqual(fila.saldo, Decimal("600.00"))

    def test_impuestos_sueldos_y_tarjetas_quedan_excluidos_con_su_motivo(self):
        casos = {
            "AFIP": Accion.EXCLUIDA_IMPUESTO,
            "ARCA": Accion.EXCLUIDA_IMPUESTO,
            "JUICIOS ARCA": Accion.EXCLUIDA_IMPUESTO,
            "MUNICIPALIDAD DE SALTA": Accion.EXCLUIDA_IMPUESTO,
            "Rentas Salta DGR": Accion.EXCLUIDA_IMPUESTO,
            "SUELDOS": Accion.EXCLUIDA_SUELDO,
            "MAPOGO SRL SUELDOS": Accion.EXCLUIDA_SUELDO,
            "Sindicato UTHGRA": Accion.EXCLUIDA_SUELDO,
            "Obra Social OSUTHGRA": Accion.EXCLUIDA_SUELDO,
            "VISA": Accion.EXCLUIDA_TARJETA,
        }
        deudas = {nombre: self._deuda("100.00", proveedor=self._proveedor(nombre)) for nombre in casos}

        filas = self._por_deuda(self._plan())

        for nombre, accion in casos.items():
            with self.subTest(proveedor=nombre):
                fila = filas[deudas[nombre].pk]
                self.assertEqual(fila.accion, accion)
                self.assertTrue(fila.motivo)

    def test_los_rubros_de_personal_impuestos_y_tarjeta_quedan_excluidos(self):
        casos = {
            "PERSONAL": Accion.EXCLUIDA_SUELDO,
            "IMPUESTOS AFIP": Accion.EXCLUIDA_IMPUESTO,
            "TARJETA DE CRÉDITO": Accion.EXCLUIDA_TARJETA,
        }
        deudas = {
            rubro: self._deuda("100.00", categoria=self._categoria(rubro)) for rubro in casos
        }

        filas = self._por_deuda(self._plan())

        for rubro, accion in casos.items():
            with self.subTest(rubro=rubro):
                self.assertEqual(filas[deudas[rubro].pk].accion, accion)

    def test_compromiso_especial_queda_excluido(self):
        deuda = self._deuda("500.00", concepto="Plan de pagos")
        register_special_commitment(
            tipo=CompromisoEspecial.Tipo.IMPUESTO,
            concepto="Plan de pagos",
            sustento_referencia="VEP-1",
            monto_estimado=Decimal("500.00"),
            cuenta_por_pagar=deuda,
            organismo="ARCA",
            periodo_fiscal=date(2026, 8, 1),
            actor=self.admin,
        )

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.EXCLUIDA_COMPROMISO)

    def test_sin_sucursal_se_excluye_salvo_que_se_pida_y_se_sepa_la_boveda(self):
        sin_nada = self._deuda("100.00", sucursal=SIN_SUCURSAL, concepto="Legacy")
        de_caja = self._deuda("200.00", sucursal=SIN_SUCURSAL, concepto="Gasto de caja")
        turno = Turno.objects.create(empresa=self.mapogo, tipo=Turno.Tipo.MANANA, creado_por=self.admin)
        caja = Caja.objects.create(
            usuario=self.admin,
            turno=turno,
            sucursal=self.vivre,
            fecha_operativa=AGOSTO,
            monto_inicial=Decimal("0.00"),
            estado=Caja.Estado.CERRADA,
        )
        CuentaPorPagar.objects.filter(pk=de_caja.pk).update(caja_origen=caja)

        filas = self._por_deuda(self._plan())
        self.assertEqual(filas[sin_nada.pk].accion, Accion.EXCLUIDA_SIN_SUCURSAL)
        self.assertEqual(filas[de_caja.pk].accion, Accion.EXCLUIDA_SIN_SUCURSAL)

        filas = self._por_deuda(self._plan(incluir_sin_sucursal=True))
        self.assertEqual(filas[sin_nada.pk].accion, Accion.EXCLUIDA_SIN_SUCURSAL)
        self.assertIn("boveda", filas[sin_nada.pk].motivo)
        self.assertEqual(filas[de_caja.pk].accion, Accion.A_REGULARIZAR)
        self.assertEqual(filas[de_caja.pk].empresa_id, self.mapogo.pk)

    def test_oveja_negra_nunca_entra_al_modo_general(self):
        por_nombre = self._deuda("100.00", proveedor=self._proveedor("Oveja Negra Armadi SRL"))
        marcado = self._proveedor("Panaderia Propia")
        # Cargada ANTES de marcarlo: desde el slice 3, una factura cargada despues
        # nace saldada con INTERNO y ni siquiera queda abierta.
        por_marca = self._deuda("200.00", proveedor=marcado)
        set_supplier_internal(supplier=marcado, es_interno=True, actor=self.admin)

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[por_nombre.pk].accion, Accion.EXCLUIDA_INTERNO)
        self.assertEqual(filas[por_marca.pk].accion, Accion.EXCLUIDA_INTERNO)

    def test_excluir_proveedor_y_filtrar_sucursal(self):
        carnes = self._deuda("100.00")
        verdura = self._deuda("200.00", proveedor=self.verduleria)
        de_vivre = self._deuda("300.00", sucursal=self.vivre)

        filas = self._por_deuda(self._plan(excluir_proveedores=["verdulería"]))
        self.assertEqual(filas[verdura.pk].accion, Accion.EXCLUIDA_PROVEEDOR)
        self.assertEqual(filas[carnes.pk].accion, Accion.A_REGULARIZAR)

        filas = self._por_deuda(self._plan(sucursales=["viv-01"]))
        self.assertEqual(set(filas), {de_vivre.pk})

    def test_las_cargadas_despues_de_la_declaracion_quedan_afuera(self):
        vieja = self._deuda("100.00")
        tardia = self._deuda("200.00", concepto="Cargada tarde")
        ayer = timezone.localdate() - timedelta(days=1)
        CuentaPorPagar.objects.filter(pk=vieja.pk).update(creado_en=timezone.now() - timedelta(days=3))

        filas = self._por_deuda(self._plan(cargadas_hasta=ayer))

        self.assertEqual(filas[vieja.pk].accion, Accion.A_REGULARIZAR)
        self.assertEqual(filas[tardia.pk].accion, Accion.EXCLUIDA_CARGA_POSTERIOR)

    def test_posibles_duplicadas_incluso_contra_una_pagada(self):
        abierta = self._deuda("700.00", concepto="Factura 1")
        gemela_abierta = self._deuda("700.00", concepto="Factura 1 bis")
        pagada = self._deuda("900.00", concepto="Pagada")
        gemela_de_pagada = self._deuda("900.00", concepto="Otra vez")
        register_cash_payment(payable=pagada, fecha_pago=AGOSTO, monto=Decimal("900.00"), actor=self.admin)
        con_comprobantes = self._deuda("500.00", comprobante="A-0001")
        otra_con_comprobante = self._deuda("500.00", comprobante="A-0002")

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[abierta.pk].accion, Accion.POSIBLE_DUPLICADA)
        self.assertEqual(filas[abierta.pk].gemelos, [gemela_abierta.pk])
        self.assertEqual(filas[gemela_abierta.pk].gemelos, [abierta.pk])
        self.assertEqual(filas[gemela_de_pagada.pk].accion, Accion.POSIBLE_DUPLICADA)
        self.assertEqual(filas[gemela_de_pagada.pk].gemelos, [pagada.pk])
        # Con comprobantes distintos son dos facturas de verdad.
        self.assertEqual(filas[con_comprobantes.pk].accion, Accion.A_REGULARIZAR)
        self.assertEqual(filas[otra_con_comprobante.pk].accion, Accion.A_REGULARIZAR)

        filas = self._por_deuda(self._plan(incluir_posibles_duplicadas=True))
        self.assertEqual(filas[abierta.pk].accion, Accion.A_REGULARIZAR)
        self.assertIn("duplicada", filas[abierta.pk].aviso)

    def test_posible_pago_por_banco_en_la_ventana_queda_excluido(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=AGOSTO + timedelta(days=20))

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertTrue(fila.posibles_pagos[0].startswith(f"#{debito.pk} "))

        fila = self._por_deuda(self._plan(incluir_posibles_pagos=True))[deuda.pk]
        self.assertEqual(fila.accion, Accion.A_REGULARIZAR)
        self.assertIn("banco", fila.aviso)

    def test_debitos_fuera_de_ventana_atados_o_de_otra_empresa_no_cuentan(self):
        deuda = self._deuda("1000.00")
        self._debito("1000.00", fecha=AGOSTO + timedelta(days=76))
        self._debito("1000.00", fecha=AGOSTO - timedelta(days=31))
        # Un debito ya atado entero a otra factura no esta "sin asignar".
        otra = self._deuda("800.00", concepto="Pagada por banco")
        register_transfer_payment(
            payable=otra,
            bank_account=self.cuenta,
            fecha_pago=AGOSTO,
            monto=Decimal("800.00"),
            referencia="TRF-9",
            actor=self.admin,
        )
        cuenta_mapogo = create_bank_account(
            nombre="Macro MAPOGO",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="300-2",
            cbu="2850590940090418135202",
            empresa=self.mapogo,
            actor=self.admin,
        )
        create_bank_movement(
            cuenta_bancaria=cuenta_mapogo,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=AGOSTO,
            monto=Decimal("1000.00"),
            concepto="Transferencia de otra empresa",
            clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
            proveedor=self.proveedor,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.vivre,
            periodo_pago=date(2026, 8, 1),
            actor=self.admin,
        )

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.A_REGULARIZAR)
        self.assertEqual(fila.posibles_pagos, [])

    def test_saldo_inconsistente_no_se_toca(self):
        deuda = self._deuda("1000.00")
        CuentaPorPagar.objects.filter(pk=deuda.pk).update(
            saldo_pendiente=Decimal("900.00"), estado=CuentaPorPagar.Estado.PARCIAL
        )

        regularizacion = self._aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR_SALDO_INCONSISTENTE)
        self.assertIn(fila, regularizacion.errores())
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_mes_cerrado_de_la_empresa_queda_excluido(self):
        de_armadi = self._deuda("100.00")
        de_vivre = self._deuda("200.00", sucursal=self.vivre)
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=self.armadi, cerrado=True)

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[de_armadi.pk].accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertEqual(filas[de_vivre.pk].accion, Accion.A_REGULARIZAR)

    def test_una_cancelacion_sin_movimiento_previa_se_revisa_a_mano(self):
        deuda = self._deuda("1000.00")
        register_payment(
            payable=deuda,
            bank_account=None,
            medio_pago=INTERNO,
            fecha_pago=AGOSTO,
            monto=Decimal("100.00"),
            actor=self.admin,
        )

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.YA_PROCESADA)

    def test_avisa_las_que_vencen_despues_del_corte_y_las_categorias_sin_rubro(self):
        deuda = self._deuda("100.00", emision=date(2026, 9, 20))
        sin_rubro = self._categoria("Varios")
        otra = self._deuda("50.00", categoria=sin_rubro)
        CategoriaCuentaPagar.objects.filter(pk=sin_rubro.pk).update(rubro_operativo=None)

        regularizacion = self._plan()
        filas = self._por_deuda(regularizacion)

        self.assertIn("Vence despues del corte", filas[deuda.pk].aviso)
        self.assertIn("sin rubro", filas[otra.pk].aviso)
        self.assertEqual(regularizacion.categorias_sin_rubro, 1)

    def test_lote_y_modo_interno_validan_sus_parametros(self):
        with self.assertRaises(ValidationError):
            Regularizacion(actor=self.admin)
        with self.assertRaises(ValidationError):
            Regularizacion(actor=self.admin, hasta=CORTE, lote="x" * 81)
        with self.assertRaises(ValidationError):
            Regularizacion(actor=self.admin, proveedor_interno=self.proveedor, hasta=CORTE)
        with self.assertRaises(ValidationError):
            Regularizacion(actor=self.admin, proveedor_interno=self.proveedor, fecha_pago=CORTE)
        self.assertEqual(self._regularizacion().lote, LOTE)


class AplicacionGeneralTests(RegularizacionBase):
    def test_registra_pagos_en_efectivo_reales_a_la_fecha_de_cada_factura(self):
        de_armadi = self._deuda("1000.00", emision=date(2026, 8, 5))
        de_vivre = self._deuda("2500.00", sucursal=self.vivre, emision=date(2026, 9, 1))
        bancos = MovimientoBancario.objects.count()

        regularizacion = self._aplicar()

        for deuda, empresa in ((de_armadi, self.armadi), (de_vivre, self.mapogo)):
            with self.subTest(deuda=deuda.concepto, empresa=empresa.nombre):
                deuda.refresh_from_db()
                self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
                pago = deuda.pagos.get()
                self.assertEqual(pago.medio_pago, EFECTIVO)
                self.assertEqual(pago.fecha_pago, deuda.fecha_emision)
                self.assertEqual(pago.monto, deuda.importe_total)
                self.assertEqual(pago.referencia, LOTE)
                self.assertEqual(pago.token_alta, token_de_pago(LOTE, EFECTIVO, deuda.pk))
                self.assertEqual(
                    pago.observaciones,
                    "Pago en efectivo segun tesoreria, regularizacion REG-2026-09-23 (corte 23/09/2026)",
                )
                egreso = MovimientoCajaCentral.objects.get(pago_tesoreria=pago)
                self.assertEqual(egreso.tipo, MovimientoCajaCentral.Tipo.EGRESO_PAGO)
                self.assertEqual(egreso.caja_central.empresa_id, empresa.pk)
                self.assertEqual(egreso.fecha, deuda.fecha_emision)
                self.assertEqual(egreso.monto, deuda.importe_total)
        self.assertEqual(self._boveda(self.armadi), Decimal("999000.00"))
        self.assertEqual(self._boveda(self.mapogo), Decimal("497500.00"))
        self.assertEqual(MovimientoBancario.objects.count(), bancos)
        self.assertEqual(regularizacion.errores(), [])

    def test_fecha_de_pago_fija(self):
        deuda = self._deuda("100.00")

        self._aplicar(fecha_pago=CORTE)

        self.assertEqual(deuda.pagos.get().fecha_pago, CORTE)

    def test_parcial_paga_solo_el_resto(self):
        deuda = self._deuda("1000.00")
        register_cash_payment(payable=deuda, fecha_pago=AGOSTO, monto=Decimal("400.00"), actor=self.admin)

        self._aplicar()

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(
            deuda.pagos.get(referencia=LOTE).monto, Decimal("600.00")
        )

    def test_el_gasto_economico_no_se_duplica(self):
        self._deuda("1000.00")
        self._deuda("3000.00", sucursal=self.vivre)
        rango = {"date_from": date(2026, 8, 1), "date_to": date(2026, 8, 31)}
        antes = build_economic_period_snapshot(**rango)

        self._aplicar()

        despues = build_economic_period_snapshot(**rango)
        self.assertEqual(despues["debt_period_total"], antes["debt_period_total"])
        self.assertEqual(despues["treasury_expense_total"], antes["treasury_expense_total"])
        self.assertEqual(despues["economic_result"], antes["economic_result"])
        self.assertEqual(despues["debt_pending_total"], Decimal("0.00"))

    def test_segunda_corrida_no_duplica(self):
        self._deuda("100.00")
        self._deuda("200.00", sucursal=self.vivre)
        self._aplicar()
        pagos = PagoTesoreria.objects.count()
        movimientos = MovimientoCajaCentral.objects.count()

        regularizacion = self._aplicar()

        self.assertEqual(PagoTesoreria.objects.count(), pagos)
        self.assertEqual(MovimientoCajaCentral.objects.count(), movimientos)
        self.assertEqual(regularizacion.filas, [])

    def test_si_la_deuda_se_reabre_con_el_pago_del_lote_vivo_queda_ya_procesada(self):
        deuda = self._deuda("1000.00")
        previo = register_cash_payment(
            payable=deuda, fecha_pago=AGOSTO, monto=Decimal("400.00"), actor=self.admin
        )
        self._aplicar()
        annul_payment(payment=previo, motivo="Estaba repetido", actor=self.admin)

        fila = self._por_deuda(self._aplicar())[deuda.pk]

        self.assertEqual(fila.accion, Accion.YA_PROCESADA)
        self.assertEqual(deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 1)

    def test_un_pago_del_lote_anulado_no_se_rehace_solo(self):
        deuda = self._deuda("500.00")
        self._aplicar()
        annul_payment(payment=deuda.pagos.get(), motivo="Se pago por banco", actor=self.admin)

        regularizacion = self._aplicar()
        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("se anulo", fila.motivo)
        self.assertEqual(deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 0)

        # Con otro lote se puede volver a registrar, a conciencia.
        self._aplicar(lote="REG-2026-09-23-B")
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)

    def test_token_del_lote_usado_por_otra_deuda_es_error(self):
        deuda = self._deuda("500.00")
        otra = self._deuda("300.00", proveedor=self.verduleria)
        register_cash_payment(
            payable=otra,
            fecha_pago=AGOSTO,
            monto=Decimal("300.00"),
            token_alta=token_de_pago(LOTE, EFECTIVO, deuda.pk),
            actor=self.admin,
        )

        regularizacion = self._aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("otra deuda", fila.motivo)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_una_deuda_que_cambia_entre_el_plan_y_la_aplicacion_no_frena_a_las_demas(self):
        cambia = self._deuda("1000.00", concepto="La pagan en el medio")
        sigue = self._deuda("200.00", proveedor=self.verduleria)
        regularizacion = self._plan()
        register_cash_payment(payable=cambia, fecha_pago=AGOSTO, monto=Decimal("100.00"), actor=self.admin)

        regularizacion.aplicar()

        filas = self._por_deuda(regularizacion)
        self.assertEqual(filas[cambia.pk].accion, Accion.ERROR)
        self.assertIn("saldo cambio", filas[cambia.pk].resultado)
        self.assertEqual(cambia.pagos.count(), 1)
        self.assertTrue(filas[sigue.pk].pago_id)
        sigue.refresh_from_db()
        self.assertEqual(sigue.estado, CuentaPorPagar.Estado.PAGADA)

    def test_saldo_desincronizado_al_aplicar_no_paga(self):
        deuda = self._deuda("1000.00")
        regularizacion = self._plan()
        CuentaPorPagar.objects.filter(pk=deuda.pk).update(
            saldo_pendiente=Decimal("900.00"), estado=CuentaPorPagar.Estado.PARCIAL
        )

        regularizacion.aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.ERROR_SALDO_INCONSISTENTE)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_compromiso_cargado_despues_del_plan_no_se_paga(self):
        deuda = self._deuda("500.00")
        regularizacion = self._plan()
        register_special_commitment(
            tipo=CompromisoEspecial.Tipo.IMPUESTO,
            concepto="Plan",
            sustento_referencia="VEP-2",
            monto_estimado=Decimal("500.00"),
            cuenta_por_pagar=deuda,
            organismo="ARCA",
            periodo_fiscal=date(2026, 8, 1),
            actor=self.admin,
        )

        regularizacion.aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.EXCLUIDA_COMPROMISO)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_cambio_de_sucursal_entre_plan_y_aplicacion_no_paga_de_otra_boveda(self):
        deuda = self._deuda("500.00")
        regularizacion = self._plan()
        CuentaPorPagar.objects.filter(pk=deuda.pk).update(sucursal=self.vivre)

        regularizacion.aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.ERROR)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_mes_cerrado_despues_del_plan_no_se_paga(self):
        deuda = self._deuda("500.00")
        regularizacion = self._plan()
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=self.armadi, cerrado=True)

        regularizacion.aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_nunca_usa_regularizacion(self):
        self.assertNotIn(REGULARIZACION, MEDIOS_DEL_COMANDO)
        self.assertEqual(set(MEDIOS_DEL_COMANDO), {EFECTIVO, INTERNO})
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        self._deuda("100.00")
        self._deuda("200.00", proveedor=panaderia)

        self._aplicar()
        interno = Regularizacion(actor=self.admin, proveedor_interno=panaderia)
        interno.planificar()
        interno.aplicar()

        self.assertEqual(
            set(PagoTesoreria.objects.values_list("medio_pago", flat=True)), {EFECTIVO, INTERNO}
        )
        self.assertFalse(PagoTesoreria.objects.filter(medio_pago=REGULARIZACION).exists())


class ConsolaTests(RegularizacionBase):
    def test_boveda_proyectada_por_empresa_y_la_real_despues(self):
        self._deuda("1000.00")
        self._deuda("250.00", proveedor=self.verduleria)
        self._deuda("4000.00", sucursal=self.vivre)

        regularizacion = self._plan()
        proyectadas = {fila["empresa"]: fila for fila in regularizacion.bovedas()}

        self.assertEqual(proyectadas["ARMADI SRL"]["antes"], Decimal("1000000.00"))
        self.assertEqual(proyectadas["ARMADI SRL"]["efectivo"], Decimal("1250.00"))
        self.assertEqual(proyectadas["ARMADI SRL"]["despues"], Decimal("998750.00"))
        self.assertEqual(proyectadas["MAPOGO SRL"]["despues"], Decimal("496000.00"))

        regularizacion.aplicar()
        reales = {fila["empresa"]: fila for fila in regularizacion.bovedas()}
        self.assertEqual(reales["ARMADI SRL"]["despues"], self._boveda(self.armadi))
        self.assertEqual(reales["MAPOGO SRL"]["despues"], self._boveda(self.mapogo))
        self.assertEqual(reales["ARMADI SRL"]["despues"], proyectadas["ARMADI SRL"]["despues"])

    def test_aviso_por_mes_con_debitos_sin_atar_y_egresos_de_boveda(self):
        self._deuda("1000.00", proveedor=self.verduleria)
        create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=date(2026, 8, 20),
            monto=Decimal("700.00"),
            concepto="Transferencia sin factura",
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.ec1,
            periodo_pago=date(2026, 8, 1),
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("300.00"),
            concepto="Pago a proveedor sin factura",
            fecha=date(2026, 8, 21),
            actor=self.admin,
        )

        avisos = self._plan().avisos_por_mes()

        self.assertEqual(
            avisos,
            [
                {
                    "empresa": "ARMADI SRL",
                    "mes": date(2026, 8, 1),
                    "a_registrar": Decimal("1000.00"),
                    "debitos_sin_atar": Decimal("700.00"),
                    "egresos_admin": Decimal("300.00"),
                }
            ],
        )

    def test_top_de_proveedores_por_importe(self):
        self._deuda("100.00")
        self._deuda("150.00", concepto="Otra", emision=date(2026, 8, 11))
        self._deuda("5000.00", proveedor=self.verduleria)

        top = self._plan().top_proveedores()

        self.assertEqual(
            top,
            [
                ("Verduleria La Huerta", 1, Decimal("5000.00")),
                ("Carnes del Norte SA", 2, Decimal("250.00")),
            ],
        )


class PlanRevisadoTests(RegularizacionBase):
    def _informe(self, regularizacion, carpeta):
        ruta = Path(carpeta) / "plan.csv"
        regularizacion.escribir_informe(ruta)
        return ruta

    @staticmethod
    def _leer_csv(ruta):
        with Path(ruta).open(encoding="utf-8-sig", newline="") as archivo:
            return list(csv.DictReader(archivo, delimiter=";"))

    def test_el_informe_tiene_una_fila_por_deuda_con_su_accion(self):
        ok = self._deuda("1000.00")
        afip = self._deuda("50.00", proveedor=self._proveedor("AFIP"))
        with TemporaryDirectory() as carpeta:
            filas = self._leer_csv(self._informe(self._plan(), carpeta))

        self.assertEqual(
            list(filas[0].keys()),
            [
                "deuda_id", "empresa", "sucursal", "proveedor", "rubro", "concepto", "comprobante",
                "fecha_emision", "fecha_vencimiento", "creado_en", "importe_total", "saldo", "medio",
                "fecha_pago", "accion", "motivo", "gemelos", "posibles_pagos", "posibles_pagos_efectivo",
                "aviso", "pago_id", "resultado", "empresa_id", "proveedor_id",
            ],
        )
        por_id = {int(fila["deuda_id"]): fila for fila in filas}
        self.assertEqual(por_id[ok.pk]["accion"], Accion.A_REGULARIZAR)
        self.assertEqual(por_id[ok.pk]["saldo"], "1.000,00")
        self.assertEqual(por_id[ok.pk]["empresa"], "ARMADI SRL")
        self.assertEqual(por_id[ok.pk]["empresa_id"], str(self.armadi.pk))
        self.assertEqual(por_id[ok.pk]["proveedor_id"], str(self.proveedor.pk))
        self.assertEqual(por_id[ok.pk]["sucursal"], "EC1")
        self.assertEqual(por_id[ok.pk]["medio"], EFECTIVO)
        self.assertEqual(por_id[ok.pk]["fecha_pago"], "10/08/2026")
        self.assertEqual(por_id[afip.pk]["accion"], Accion.EXCLUIDA_IMPUESTO)

    def test_aplica_solo_lo_aprobado_con_el_mismo_saldo(self):
        aprobada = self._deuda("1000.00")
        sacada = self._deuda("200.00", proveedor=self.verduleria)
        cambia = self._deuda("300.00", concepto="Cambia de saldo")
        se_paga = self._deuda("400.00", concepto="Se paga en el medio")
        with TemporaryDirectory() as carpeta:
            ruta = self._informe(self._plan(), carpeta)
            filas = [f for f in self._leer_csv(ruta) if int(f["deuda_id"]) != sacada.pk]
            with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
                escritor = csv.DictWriter(archivo, fieldnames=list(filas[0].keys()), delimiter=";")
                escritor.writeheader()
                escritor.writerows(filas)
            aprobadas = leer_plan(ruta, EFECTIVO)
        register_cash_payment(payable=cambia, fecha_pago=AGOSTO, monto=Decimal("50.00"), actor=self.admin)
        register_cash_payment(payable=se_paga, fecha_pago=AGOSTO, monto=Decimal("400.00"), actor=self.admin)
        nueva = self._deuda("999.00", concepto="Cargada despues de la revision", emision=date(2026, 9, 1))

        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas)
        regularizacion.aplicar()

        filas = self._por_deuda(regularizacion)
        self.assertTrue(filas[aprobada.pk].pago_id)
        self.assertEqual(filas[sacada.pk].accion, Accion.FUERA_DEL_PLAN)
        self.assertEqual(filas[nueva.pk].accion, Accion.FUERA_DEL_PLAN)
        self.assertEqual(filas[cambia.pk].accion, Accion.DISTINTA_AL_PLAN)
        self.assertEqual(filas[se_paga.pk].accion, Accion.NO_DISPONIBLE)
        self.assertEqual(
            list(PagoTesoreria.objects.filter(referencia=LOTE).values_list("cuenta_por_pagar_id", flat=True)),
            [aprobada.pk],
        )

    def test_lo_aprobado_que_hoy_quedo_excluido_no_se_aplica(self):
        deuda = self._deuda("1000.00")
        with TemporaryDirectory() as carpeta:
            aprobadas = leer_plan(self._informe(self._plan(), carpeta), EFECTIVO)
        self._debito("1000.00", fecha=AGOSTO)

        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas)
        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertIn("plan revisado la aprobaba", fila.aviso)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_leer_plan_valida_columnas_medio_y_acepta_excel_ansi(self):
        cabecera = "deuda_id;accion;saldo;medio;importe_total;sucursal;fecha_pago;empresa_id;proveedor_id"
        with TemporaryDirectory() as carpeta:
            sin_medio = Path(carpeta) / "sin_medio.csv"
            sin_medio.write_text("deuda_id;accion;saldo\n1;A_REGULARIZAR;10,00\n", encoding="utf-8")
            with self.assertRaisesMessage(ValidationError, "medio"):
                leer_plan(sin_medio, EFECTIVO)

            # Un plan viejo, solo con el saldo, no ata la boveda ni la fecha: se rechaza.
            sin_boveda = Path(carpeta) / "sin_boveda.csv"
            sin_boveda.write_text("deuda_id;accion;saldo;medio\n1;A_REGULARIZAR;10,00;EFECTIVO\n", encoding="utf-8")
            with self.assertRaisesMessage(ValidationError, "empresa_id, fecha_pago"):
                leer_plan(sin_boveda, EFECTIVO)

            interno = Path(carpeta) / "interno.csv"
            interno.write_text(
                f"{cabecera}\n1;A_REGULARIZAR;10,00;INTERNO;10,00;EC1;10/08/2026;1;1\n", encoding="utf-8"
            )
            with self.assertRaises(ValidationError):
                leer_plan(interno, EFECTIVO)

            mala_fecha = Path(carpeta) / "mala_fecha.csv"
            mala_fecha.write_text(
                f"{cabecera}\n1;A_REGULARIZAR;10,00;EFECTIVO;10,00;EC1;2026-08-10;1;1\n", encoding="utf-8"
            )
            with self.assertRaisesMessage(ValidationError, "fecha_pago invalida"):
                leer_plan(mala_fecha, EFECTIVO)

            repetida = Path(carpeta) / "repetida.csv"
            repetida.write_text(
                f"{cabecera}\n"
                "1;A_REGULARIZAR;10,00;EFECTIVO;10,00;EC1;10/08/2026;1;1\n"
                "1;A_REGULARIZAR;10,00;EFECTIVO;10,00;VIV-01;10/08/2026;2;1\n",
                encoding="utf-8",
            )
            with self.assertRaisesMessage(ValidationError, "dos veces con datos distintos"):
                leer_plan(repetida, EFECTIVO)

            # Excel guarda en ANSI, con coma, y saca los ceros de la fecha.
            ansi = Path(carpeta) / "ansi.csv"
            ansi.write_bytes(
                "deuda_id,proveedor,accion,saldo,medio,importe_total,sucursal,fecha_pago,empresa_id,proveedor_id\n"
                "7,Panader\xeda,A_REGULARIZAR,\"1.234,56\",EFECTIVO,\"2.000,00\",ec1,1/8/2026,3,9\n"
                "8,Otra,EXCLUIDA_IMPUESTO,10,EFECTIVO,10,EC1,10/08/2026,3,9\n".encode("cp1252")
            )
            self.assertEqual(
                leer_plan(ansi, EFECTIVO),
                {
                    7: FilaDelPlan(
                        saldo=Decimal("1234.56"),
                        importe_total=Decimal("2000.00"),
                        empresa_id=3,
                        sucursal="ec1",
                        proveedor_id=9,
                        fecha_pago=date(2026, 8, 1),
                    )
                },
            )

            with self.assertRaises(ValidationError):
                leer_plan(Path(carpeta) / "no_existe.csv", EFECTIVO)


class ModoInternoTests(RegularizacionBase):
    def setUp(self):
        super().setUp()
        self.panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")

    def _interno(self, **opciones):
        regularizacion = Regularizacion(actor=self.admin, proveedor_interno=self.panaderia, **opciones)
        regularizacion.planificar()
        return regularizacion

    def test_cancela_todas_las_abiertas_sin_plata_y_marca_al_proveedor(self):
        vieja = self._deuda("1000.00", proveedor=self.panaderia, emision=date(2026, 6, 3))
        nueva = self._deuda("2000.00", proveedor=self.panaderia, emision=date(2026, 10, 1), sucursal=self.vivre)
        sin_sucursal = self._deuda("300.00", proveedor=self.panaderia, sucursal=SIN_SUCURSAL)
        ajena = self._deuda("500.00")
        movimientos = (MovimientoCajaCentral.objects.count(), MovimientoBancario.objects.count())
        bovedas = (self._boveda(self.armadi), self._boveda(self.mapogo))

        regularizacion = self._interno()
        regularizacion.aplicar()

        for deuda in (vieja, nueva, sin_sucursal):
            with self.subTest(deuda=deuda.pk):
                deuda.refresh_from_db()
                self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
                pago = deuda.pagos.get()
                self.assertEqual(pago.medio_pago, INTERNO)
                self.assertEqual(pago.fecha_pago, deuda.fecha_emision)
                self.assertEqual(pago.estado_bancario, PagoTesoreria.EstadoBancario.NO_APLICA)
                self.assertEqual(pago.referencia, f"INTERNO-{self.panaderia.pk}")
                self.assertEqual(pago.token_alta, token_de_pago(pago.referencia, INTERNO, deuda.pk))
        ajena.refresh_from_db()
        self.assertEqual(ajena.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(
            (MovimientoCajaCentral.objects.count(), MovimientoBancario.objects.count()), movimientos
        )
        self.assertEqual((self._boveda(self.armadi), self._boveda(self.mapogo)), bovedas)
        self.panaderia.refresh_from_db()
        self.assertTrue(self.panaderia.es_interno)
        self.assertTrue(regularizacion.proveedor_marcado)
        self.assertEqual(regularizacion.bovedas(), [])

    def test_la_simulacion_no_marca_al_proveedor(self):
        self._deuda("1000.00", proveedor=self.panaderia)

        self._interno()

        self.panaderia.refresh_from_db()
        self.assertFalse(self.panaderia.es_interno)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_lista_pagos_reales_otros_proveedores_parecidos_y_duplicados(self):
        pagada_en_efectivo = self._deuda("400.00", proveedor=self.panaderia, concepto="Pagada con plata")
        pago = register_cash_payment(
            payable=pagada_en_efectivo, fecha_pago=AGOSTO, monto=Decimal("150.00"), actor=self.admin
        )
        duplicada = self._deuda("700.00", proveedor=self.panaderia, concepto="Pan")
        gemela = self._deuda("700.00", proveedor=self.panaderia, concepto="Pan otra vez")
        parecido = self._proveedor("Panaderia La Oveja")
        self._deuda("80.00", proveedor=parecido)

        regularizacion = self._interno()
        filas = regularizacion.filas

        aviso_pago = next(f for f in filas if f.accion == Accion.AVISO_PAGO_REAL)
        self.assertEqual(aviso_pago.pago_id, pago.pk)
        self.assertEqual(aviso_pago.medio, EFECTIVO)
        otro = next(f for f in filas if f.accion == Accion.AVISO_OTRO_PROVEEDOR)
        self.assertEqual(otro.proveedor, "Panaderia La Oveja")
        self.assertEqual(otro.saldo, Decimal("80.00"))
        por_deuda = {
            f.deuda_id: f for f in filas if f.accion not in (Accion.AVISO_PAGO_REAL, Accion.AVISO_OTRO_PROVEEDOR)
        }
        self.assertEqual(por_deuda[duplicada.pk].accion, Accion.POSIBLE_DUPLICADA)
        self.assertEqual(por_deuda[gemela.pk].accion, Accion.POSIBLE_DUPLICADA)
        parcial = por_deuda[pagada_en_efectivo.pk]
        self.assertEqual(parcial.accion, Accion.A_REGULARIZAR)
        self.assertEqual(parcial.saldo, Decimal("250.00"))
        self.assertIn("Ya tiene pagos", parcial.aviso)

        regularizacion.aplicar()
        self.assertEqual(
            PagoTesoreria.objects.get(pk=pago.pk).estado, PagoTesoreria.Estado.REGISTRADO
        )
        self.assertEqual(duplicada.pagos.count(), 0)


class ReversionTests(RegularizacionBase):
    def test_simular_no_anula_y_aplicar_devuelve_el_efectivo(self):
        de_armadi = self._deuda("1000.00")
        de_vivre = self._deuda("2000.00", sucursal=self.vivre)
        self._aplicar()
        bovedas_aplicadas = (self._boveda(self.armadi), self._boveda(self.mapogo))

        simulacion = Reversion(actor=self.admin, lote=LOTE)
        simulacion.planificar()
        self.assertEqual({f.accion for f in simulacion.filas}, {Accion.A_REVERTIR})
        self.assertEqual((self._boveda(self.armadi), self._boveda(self.mapogo)), bovedas_aplicadas)
        proyectadas = {fila["empresa"]: fila for fila in simulacion.bovedas()}
        self.assertEqual(proyectadas["ARMADI SRL"]["despues"], Decimal("1000000.00"))

        reversion = Reversion(actor=self.admin, lote=LOTE, motivo="Corrida de prueba")
        reversion.planificar()
        reversion.aplicar()

        for deuda in (de_armadi, de_vivre):
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
            pago = deuda.pagos.get()
            self.assertEqual(pago.estado, PagoTesoreria.Estado.ANULADO)
            self.assertEqual(pago.motivo_anulacion, "Corrida de prueba")
            self.assertEqual(
                MovimientoCajaCentral.objects.get(pago_tesoreria=pago).estado,
                MovimientoCajaCentral.Estado.ANULADO,
            )
        self.assertEqual(self._boveda(self.armadi), Decimal("1000000.00"))
        self.assertEqual(self._boveda(self.mapogo), Decimal("500000.00"))

        # Revertir dos veces no hace nada nuevo.
        otra = Reversion(actor=self.admin, lote=LOTE)
        otra.planificar()
        self.assertEqual({f.accion for f in otra.filas}, {Accion.YA_ANULADO})

    def test_no_toca_pagos_cargados_a_mano_con_la_misma_referencia(self):
        deuda = self._deuda("500.00")
        manual = register_payment(
            payable=deuda,
            bank_account=None,
            medio_pago=EFECTIVO,
            fecha_pago=AGOSTO,
            monto=Decimal("500.00"),
            referencia=LOTE,
            actor=self.admin,
        )

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        self.assertEqual(reversion.filas[0].accion, Accion.NO_ES_DEL_LOTE)
        manual.refresh_from_db()
        self.assertEqual(manual.estado, PagoTesoreria.Estado.REGISTRADO)

    def test_mes_cerrado_no_se_revierte(self):
        deuda = self._deuda("500.00")
        self._aplicar()
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=self.armadi, cerrado=True)

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        self.assertEqual(reversion.filas[0].accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.REGISTRADO)

    def test_revertir_el_modo_interno_y_desmarcar(self):
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        deuda = self._deuda("700.00", proveedor=panaderia)
        interno = Regularizacion(actor=self.admin, proveedor_interno=panaderia)
        interno.planificar()
        interno.aplicar()
        lote = interno.lote

        sin_desmarcar = Reversion(actor=self.admin, lote=lote)
        sin_desmarcar.planificar()
        self.assertEqual(sin_desmarcar.proveedores_internos(), ["OVEJA NEGRA ARMADI SRL"])

        reversion = Reversion(actor=self.admin, lote=lote, desmarcar_interno=True)
        reversion.planificar()
        reversion.aplicar()

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        panaderia.refresh_from_db()
        self.assertFalse(panaderia.es_interno)
        self.assertEqual(reversion.proveedores_desmarcados, ["OVEJA NEGRA ARMADI SRL"])

    def test_lote_vacio_es_error(self):
        with self.assertRaises(ValidationError):
            Reversion(actor=self.admin, lote="  ")


class ProveedorInternoServicioTests(RegularizacionBase):
    def test_marcar_exige_tesoreria_y_es_idempotente(self):
        cajera = User.objects.create_user(username="cajera-reg", password="x")
        with self.assertRaises(PermissionDenied):
            set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=cajera)

        marcado = set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=self.admin)
        self.assertTrue(marcado.es_interno)
        self.assertEqual(marcado.actualizado_por, self.admin)
        otra_vez = set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=self.admin)
        self.assertTrue(otra_vez.es_interno)

    def test_un_dato_legacy_invalido_no_impide_marcarlo(self):
        Proveedor.objects.filter(pk=self.proveedor.pk).update(email="no-es-un-email")

        marcado = set_supplier_internal(supplier=self.proveedor, es_interno=True, actor=self.admin)

        self.assertTrue(Proveedor.objects.get(pk=marcado.pk).es_interno)


class ComandoTests(RegularizacionBase):
    def _run(self, *args, **opciones):
        salida = StringIO()
        call_command("regularizar_deudas", *args, stdout=salida, **opciones)
        return salida.getvalue()

    def test_usuario_inexistente_o_sin_permiso(self):
        with self.assertRaisesMessage(CommandError, "No existe el usuario"):
            self._run(usuario="nadie", hasta="2026-09-23")
        User.objects.create_user(username="cajera-cmd", password="x")
        with self.assertRaisesMessage(CommandError, "no puede operar tesoreria"):
            self._run(usuario="cajera-cmd", hasta="2026-09-23")

    def test_simulacion_por_consola_con_informe(self):
        self._deuda("1000.00")
        self._deuda("4000.00", sucursal=self.vivre)
        self._deuda("50.00", proveedor=self._proveedor("AFIP"))
        with TemporaryDirectory() as carpeta:
            informe = Path(carpeta) / "plan.csv"
            salida = self._run(usuario=self.admin.username, hasta="23/09/2026", informe=str(informe))
            with informe.open(encoding="utf-8-sig", newline="") as archivo:
                filas = list(csv.DictReader(archivo, delimiter=";"))

        self.assertIn("SIMULACION", salida)
        self.assertIn("Corte 23/09/2026", salida)
        self.assertIn("fecha de pago: la de cada factura", salida)
        self.assertIn("A_REGULARIZAR", salida)
        self.assertIn("EXCLUIDA_IMPUESTO", salida)
        self.assertIn("Caja fuerte por empresa", salida)
        self.assertIn("1.000.000,00", salida)
        self.assertIn("999.000,00", salida)
        self.assertIn("496.000,00", salida)
        self.assertIn("Carnes del Norte SA", salida)
        self.assertEqual(len(filas), 3)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_aplicar_el_modo_general_exige_el_plan_revisado(self):
        self._deuda("1000.00")

        with self.assertRaisesMessage(CommandError, "--plan"):
            self._run(usuario=self.admin.username, hasta="2026-09-23", apply=True)

        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_simular_revisar_y_aplicar(self):
        aprobada = self._deuda("1000.00")
        sacada = self._deuda("200.00", proveedor=self.verduleria)
        with TemporaryDirectory() as carpeta:
            plan = Path(carpeta) / "plan.csv"
            self._run(usuario=self.admin.username, hasta="2026-09-23", informe=str(plan))
            texto = plan.read_text(encoding="utf-8-sig")
            plan.write_text(
                "\n".join(linea for linea in texto.splitlines() if not linea.startswith(f"{sacada.pk};")),
                encoding="utf-8-sig",
            )
            aplicado = Path(carpeta) / "aplicado.csv"
            salida = self._run(
                usuario=self.admin.username,
                hasta="2026-09-23",
                plan=str(plan),
                informe=str(aplicado),
                apply=True,
            )
            with aplicado.open(encoding="utf-8-sig", newline="") as archivo:
                resultado = {int(f["deuda_id"]): f for f in csv.DictReader(archivo, delimiter=";")}

        self.assertIn("APLICANDO", salida)
        self.assertIn("1 deudas aprobadas", salida)
        aprobada.refresh_from_db()
        sacada.refresh_from_db()
        self.assertEqual(aprobada.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(sacada.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertTrue(resultado[aprobada.pk]["pago_id"])
        self.assertEqual(resultado[sacada.pk]["accion"], Accion.FUERA_DEL_PLAN)
        self.assertEqual(self._boveda(self.armadi), Decimal("999000.00"))

    def test_modo_interno_por_nombre_normalizado_o_id(self):
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        deuda = self._deuda("700.00", proveedor=panaderia)

        with TemporaryDirectory() as carpeta:
            plan = Path(carpeta) / "interno.csv"
            salida = self._run(
                usuario=self.admin.username, proveedor_interno="  Oveja Négra Armadi SRL ", informe=str(plan)
            )
            self.assertIn("Modo interno: OVEJA NEGRA ARMADI SRL", salida)
            self.assertIn("Con --apply el proveedor queda marcado", salida)
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

            salida = self._run(
                usuario=self.admin.username, proveedor_interno=str(panaderia.pk), plan=str(plan), apply=True
            )
        self.assertIn("Proveedor marcado como interno", salida)
        self.assertIn("No quedan deudas abiertas de OVEJA NEGRA ARMADI SRL", salida)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda.pagos.get().medio_pago, INTERNO)

    def test_modo_interno_rechaza_corte_y_proveedor_desconocido(self):
        self._proveedor("OVEJA NEGRA ARMADI SRL")
        with self.assertRaisesMessage(CommandError, "no lleva --hasta"):
            self._run(usuario=self.admin.username, proveedor_interno="OVEJA NEGRA ARMADI SRL", hasta="2026-09-23")
        with self.assertRaisesMessage(CommandError, "Parecidos: #"):
            self._run(usuario=self.admin.username, proveedor_interno="OVEJA")
        with self.assertRaisesMessage(CommandError, "No existe el proveedor #999999"):
            self._run(usuario=self.admin.username, proveedor_interno="999999")

    def test_validaciones_de_opciones(self):
        with self.assertRaisesMessage(CommandError, "Indique --hasta"):
            self._run(usuario=self.admin.username)
        with self.assertRaisesMessage(CommandError, "fecha invalida"):
            self._run(usuario=self.admin.username, hasta="23-09-2026x")
        with self.assertRaisesMessage(CommandError, "No existen las sucursales: ZZZ"):
            self._run(usuario=self.admin.username, hasta="2026-09-23", sucursal=["ZZZ"])
        with self.assertRaisesMessage(CommandError, "--revertir no se combina con --hasta"):
            self._run(usuario=self.admin.username, revertir=LOTE, hasta="2026-09-23")
        with self.assertRaisesMessage(CommandError, "solo con --revertir"):
            self._run(usuario=self.admin.username, hasta="2026-09-23", motivo="x")
        with TemporaryDirectory() as carpeta:
            plan = Path(carpeta) / "plan.csv"
            plan.write_text("deuda_id;accion\n", encoding="utf-8")
            with self.assertRaisesMessage(CommandError, "no tiene las columnas"):
                self._run(usuario=self.admin.username, hasta="2026-09-23", plan=str(plan), apply=True)

    def test_revertir_por_consola(self):
        deuda = self._deuda("1000.00")
        self._aplicar()

        salida = self._run(usuario=self.admin.username, revertir=LOTE)
        self.assertIn("SIMULACION", salida)
        self.assertIn("A_REVERTIR", salida)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)

        salida = self._run(usuario=self.admin.username, revertir=LOTE, motivo="Ensayo", apply=True)
        self.assertIn("APLICANDO", salida)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(self._boveda(self.armadi), Decimal("1000000.00"))

    def test_los_errores_salen_por_consola(self):
        deuda = self._deuda("500.00")
        self._aplicar()
        annul_payment(payment=deuda.pagos.get(), motivo="Se pago por banco", actor=self.admin)

        salida = self._run(usuario=self.admin.username, hasta="2026-09-23")

        self.assertIn(f"ERROR deuda #{deuda.pk}", salida)


class PantallasTests(RegularizacionBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        session = self.client.session
        session["empresa_ids"] = [self.armadi.pk, self.mapogo.pk]
        session.save()

    def test_los_pagos_del_comando_son_plata_real_en_el_listado_y_el_detalle(self):
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        deuda = self._deuda("1000.00")
        self._deuda("700.00", proveedor=panaderia)
        self._aplicar()
        interno = Regularizacion(actor=self.admin, proveedor_interno=panaderia)
        interno.planificar()
        interno.aplicar()

        response = self.client.get(reverse("treasury:pagos_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Carnes del Norte SA")
        self.assertNotContains(response, "OVEJA NEGRA ARMADI SRL")

        pago = deuda.pagos.get()
        response = self.client.get(reverse("treasury:pagos_detail", args=[pago.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pago en efectivo segun tesoreria, regularizacion REG-2026-09-23")
        self.assertContains(response, LOTE)


class PlanAtaBovedaYFechaTests(RegularizacionBase):
    """El plan revisado ata lo que tesoreria vio: de que boveda sale el efectivo
    (empresa y sucursal), a quien se le paga y con que fecha, no solo el saldo."""

    def _aprobadas(self, **opciones):
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "plan.csv"
            self._plan(**opciones).escribir_informe(ruta)
            return leer_plan(ruta, EFECTIVO)

    def _aplicar_plan(self, aprobadas, **opciones):
        regularizacion = self._plan(**opciones)
        regularizacion.restringir_a_plan(aprobadas)
        regularizacion.aplicar()
        return regularizacion

    def _editar(self, deuda, **cambios):
        datos = {
            "sucursal": deuda.sucursal,
            "proveedor": deuda.proveedor,
            "categoria": deuda.categoria,
            "concepto": deuda.concepto,
            "fecha_emision": deuda.fecha_emision,
            "fecha_vencimiento": deuda.fecha_vencimiento,
            "importe_total": deuda.importe_total,
        }
        datos.update(cambios)
        return update_payable(payable=deuda, actor=self.admin, **datos)

    def test_cambio_de_sucursal_entre_la_revision_y_el_apply_no_paga_de_otra_boveda(self):
        deuda = self._deuda("500.00")
        aprobadas = self._aprobadas()
        self.assertEqual(aprobadas[deuda.pk].empresa_id, self.armadi.pk)
        # Mientras tesoreria revisa, alguien corrige la sucursal (mismo importe).
        self._editar(deuda, sucursal=self.vivre)
        bovedas = (self._boveda(self.armadi), self._boveda(self.mapogo))

        regularizacion = self._aplicar_plan(aprobadas)

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn(
            "empresa (boveda de la que sale el efectivo): en el plan ARMADI SRL, hoy MAPOGO SRL", fila.motivo
        )
        self.assertIn("sucursal: en el plan EC1, hoy VIV-01", fila.motivo)
        self.assertIsNone(fila.pago_id)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        self.assertEqual((self._boveda(self.armadi), self._boveda(self.mapogo)), bovedas)

    def test_otra_sucursal_de_la_misma_empresa_tampoco_se_aplica(self):
        ec2 = Sucursal.objects.create(
            nombre="Cafe Norte", codigo="EC2", razon_social="ARMADI SRL", empresa=self.armadi
        )
        deuda = self._deuda("500.00")
        aprobadas = self._aprobadas()
        self._editar(deuda, sucursal=ec2)

        fila = self._por_deuda(self._aplicar_plan(aprobadas))[deuda.pk]

        self.assertEqual(fila.accion, Accion.DISTINTA_AL_PLAN)
        self.assertNotIn("empresa", fila.motivo)
        self.assertIn("sucursal: en el plan EC1, hoy EC2", fila.motivo)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_fecha_de_pago_distinta_a_la_revisada_no_se_aplica(self):
        deuda = self._deuda("500.00")

        # Simulo con la fecha de cada factura y aplico con --fecha-pago.
        regularizacion = self._aplicar_plan(self._aprobadas(), fecha_pago=CORTE)
        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn("fecha de pago: en el plan 10/08/2026, hoy 23/09/2026", fila.motivo)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

        # Al reves: simulo con --fecha-pago y aplico sin la opcion.
        regularizacion = self._aplicar_plan(self._aprobadas(fecha_pago=CORTE))
        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn("fecha de pago: en el plan 23/09/2026, hoy 10/08/2026", fila.motivo)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

        # Con la misma fecha que se reviso, se registra.
        regularizacion = self._aplicar_plan(self._aprobadas(fecha_pago=CORTE), fecha_pago=CORTE)
        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.APLICADA)
        self.assertEqual(deuda.pagos.get().fecha_pago, CORTE)

    def test_fecha_de_la_factura_corregida_cambia_la_fecha_de_pago(self):
        deuda = self._deuda("500.00")
        aprobadas = self._aprobadas()
        self._editar(deuda, fecha_emision=date(2026, 9, 20), fecha_vencimiento=date(2026, 10, 5))

        fila = self._por_deuda(self._aplicar_plan(aprobadas))[deuda.pk]

        self.assertEqual(fila.accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn("fecha de pago: en el plan 10/08/2026, hoy 20/09/2026", fila.motivo)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_cambio_de_proveedor_o_de_importe_con_el_mismo_saldo(self):
        de_proveedor = self._deuda("500.00", concepto="Cambia de proveedor")
        de_importe = self._deuda("1000.00", concepto="Cambia de importe")
        aprobadas = self._aprobadas()
        self._editar(de_proveedor, proveedor=self.verduleria)
        self._editar(de_importe, importe_total=Decimal("1100.00"))
        register_cash_payment(payable=de_importe, fecha_pago=AGOSTO, monto=Decimal("100.00"), actor=self.admin)

        filas = self._por_deuda(self._aplicar_plan(aprobadas))

        self.assertEqual(filas[de_proveedor.pk].accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn(
            f"proveedor: en el plan #{self.proveedor.pk}, hoy #{self.verduleria.pk}", filas[de_proveedor.pk].motivo
        )
        self.assertEqual(filas[de_importe.pk].accion, Accion.DISTINTA_AL_PLAN)
        self.assertIn("importe: en el plan 1.000,00, hoy 1.100,00", filas[de_importe.pk].motivo)
        self.assertNotIn("saldo", filas[de_importe.pk].motivo)
        self.assertFalse(PagoTesoreria.objects.filter(referencia=LOTE).exists())

    def test_cambio_de_sucursal_despues_de_restringir_tampoco_se_paga(self):
        deuda = self._deuda("500.00")
        aprobadas = self._aprobadas()
        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas)
        CuentaPorPagar.objects.filter(pk=deuda.pk).update(sucursal=self.vivre)

        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("otra boveda", fila.resultado)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_volver_a_correr_el_mismo_plan_distingue_lo_ya_pagado_por_el_lote(self):
        aplicada = self._deuda("1000.00", concepto="La paga el lote")
        otro_camino = self._deuda("200.00", proveedor=self.verduleria, concepto="La pagan por otro lado")
        de_vivre = self._deuda("300.00", sucursal=self.vivre, concepto="Queda afuera del filtro")
        aprobadas = self._aprobadas()
        primera = self._aplicar_plan(aprobadas, sucursales=["EC1"], excluir_proveedores=["verduleria"])
        self.assertEqual(self._por_deuda(primera)[aplicada.pk].accion, Accion.APLICADA)
        register_cash_payment(payable=otro_camino, fecha_pago=AGOSTO, monto=Decimal("200.00"), actor=self.admin)

        segunda = self._aplicar_plan(aprobadas, sucursales=["EC1"])

        filas = self._por_deuda(segunda)
        pago = aplicada.pagos.get()
        self.assertEqual(filas[aplicada.pk].accion, Accion.YA_PROCESADA)
        self.assertEqual(filas[aplicada.pk].pago_id, pago.pk)
        self.assertEqual(filas[aplicada.pk].motivo, f"Ya pagada por este lote: pago #{pago.pk}.")
        self.assertEqual(filas[otro_camino.pk].accion, Accion.NO_DISPONIBLE)
        self.assertIn("pagada por otro camino", filas[otro_camino.pk].motivo)
        self.assertEqual(filas[de_vivre.pk].accion, Accion.NO_DISPONIBLE)
        self.assertIn("sigue abierta, pero quedo fuera de esta seleccion", filas[de_vivre.pk].motivo)
        self.assertEqual(aplicada.pagos.count(), 1)

    def test_despues_de_aplicar_las_filas_dicen_aplicada_y_no_sirven_de_plan(self):
        deuda = self._deuda("1000.00")
        regularizacion = self._aplicar_plan(self._aprobadas())
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "aplicado.csv"
            regularizacion.escribir_informe(ruta)
            with ruta.open(encoding="utf-8-sig", newline="") as archivo:
                [fila] = list(csv.DictReader(archivo, delimiter=";"))
            self.assertEqual(leer_plan(ruta, EFECTIVO), {})

        self.assertEqual(fila["accion"], Accion.APLICADA)
        self.assertEqual(fila["pago_id"], str(deuda.pagos.get().pk))
        self.assertEqual(regularizacion.resumen()[Accion.APLICADA], (1, Decimal("1000.00")))


class PosiblePagoEnEfectivoTests(RegularizacionBase):
    """Un egreso de la boveda sin factura (EGRESO_ADMIN) de la misma sucursal e
    importe pudo ser el pago en efectivo: registrar otro bajaria dos veces la
    caja fuerte por la misma plata."""

    def _egreso(self, monto, *, fecha, sucursal=None, empresa=None, concepto="S/E"):
        return register_egreso_tesoreria(
            empresa=empresa or self.armadi,
            fuente="CAJA_CENTRAL",
            fecha=fecha,
            monto=Decimal(monto),
            concepto=concepto,
            rubro=self.rubro,
            sucursal=sucursal or self.ec1,
            periodo=fecha.replace(day=1),
            actor=self.admin,
        )

    def test_egreso_de_la_misma_sucursal_e_importe_excluye_la_factura(self):
        deuda = self._deuda("31480.00", emision=date(2026, 8, 8))
        egreso = self._egreso("31480.00", fecha=date(2026, 8, 12), concepto="PARE CARRITO S/E")
        boveda = self._boveda(self.armadi)

        regularizacion = self._aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        esperado = f"#{egreso.pk} 12/08/2026 31.480,00 PARE CARRITO S/E"
        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_EFECTIVO)
        self.assertIn("anular ese egreso y registrar el pago", fila.motivo)
        self.assertEqual(fila.posibles_pagos_efectivo, [esperado])
        self.assertEqual(fila.posibles_pagos, [])
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        self.assertEqual(self._boveda(self.armadi), boveda)
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "plan.csv"
            regularizacion.escribir_informe(ruta)
            with ruta.open(encoding="utf-8-sig", newline="") as archivo:
                [csv_fila] = list(csv.DictReader(archivo, delimiter=";"))
        self.assertEqual(csv_fila["posibles_pagos_efectivo"], esperado)

    def test_se_incluye_con_la_opcion_y_queda_con_aviso(self):
        deuda = self._deuda("75000.00")
        self._egreso("75000.00", fecha=AGOSTO + timedelta(days=3), concepto="Pago Carnes Del Norte")

        fila = self._por_deuda(self._plan(incluir_posibles_pagos_en_efectivo=True))[deuda.pk]

        self.assertEqual(fila.accion, Accion.A_REGULARIZAR)
        self.assertIn("Posible pago en efectivo ya cargado", fila.aviso)

    def test_cada_egreso_se_asigna_a_una_sola_factura(self):
        primera = self._deuda("31480.00", emision=date(2026, 8, 8), concepto="Semana 1")
        segunda = self._deuda("31480.00", emision=date(2026, 8, 15), concepto="Semana 2")
        egreso = self._egreso("31480.00", fecha=date(2026, 8, 18))

        filas = self._por_deuda(self._plan())
        self.assertEqual(filas[primera.pk].accion, Accion.POSIBLE_PAGO_EN_EFECTIVO)
        self.assertTrue(filas[primera.pk].posibles_pagos_efectivo[0].startswith(f"#{egreso.pk} "))
        self.assertEqual(filas[segunda.pk].accion, Accion.A_REGULARIZAR)

        otro = self._egreso("31480.00", fecha=date(2026, 8, 12))
        filas = self._por_deuda(self._plan())
        self.assertTrue(filas[primera.pk].posibles_pagos_efectivo[0].startswith(f"#{otro.pk} "))
        self.assertTrue(filas[segunda.pk].posibles_pagos_efectivo[0].startswith(f"#{egreso.pk} "))

    def test_tolerancia_de_importe_y_egresos_sin_sucursal(self):
        redondeada = self._deuda("31478.50", concepto="Pagada redondeando")
        sin_sucursal = self._deuda("5000.00", proveedor=self.verduleria)
        self._egreso("31500.00", fecha=AGOSTO + timedelta(days=1))
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("5000.00"),
            concepto="Pago sin sucursal",
            fecha=AGOSTO + timedelta(days=2),
            actor=self.admin,
        )

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[redondeada.pk].accion, Accion.POSIBLE_PAGO_EN_EFECTIVO)
        self.assertEqual(filas[sin_sucursal.pk].accion, Accion.POSIBLE_PAGO_EN_EFECTIVO)

    def test_no_cuentan_fuera_de_ventana_otra_sucursal_otra_empresa_anulados_ni_otro_importe(self):
        ec2 = Sucursal.objects.create(
            nombre="Cafe Norte", codigo="EC2", razon_social="ARMADI SRL", empresa=self.armadi
        )
        deuda = self._deuda("10000.00")
        self._egreso("10000.00", fecha=AGOSTO - timedelta(days=6))
        self._egreso("10000.00", fecha=AGOSTO + timedelta(days=61))
        self._egreso("10000.00", fecha=AGOSTO, sucursal=ec2)
        self._egreso("10000.00", fecha=AGOSTO, empresa=self.mapogo, sucursal=self.vivre)
        self._egreso("9999.99", fecha=AGOSTO)
        self._egreso("10100.01", fecha=AGOSTO)
        anulado = self._egreso("10000.00", fecha=AGOSTO + timedelta(days=1))
        annul_central_cash_movement(movement=anulado, motivo="Cargado dos veces", actor=self.admin)

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.A_REGULARIZAR)
        self.assertEqual(fila.posibles_pagos_efectivo, [])

    def test_el_modo_interno_no_lo_mira(self):
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        deuda = self._deuda("700.00", proveedor=panaderia)
        self._egreso("700.00", fecha=AGOSTO)

        interno = Regularizacion(actor=self.admin, proveedor_interno=panaderia)
        interno.planificar()

        self.assertEqual(self._por_deuda(interno)[deuda.pk].accion, Accion.A_REGULARIZAR)


class ModoInternoSeguroTests(RegularizacionBase):
    """El modo interno saca deudas de la deuda viva y marca al proveedor para
    siempre: exige el plan, no acepta filtros, pide confirmar un nombre que no
    es el de la panaderia y no marca si no cancelo nada o hubo errores."""

    def setUp(self):
        super().setUp()
        self.panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")

    def _run(self, **opciones):
        salida = StringIO()
        call_command("regularizar_deudas", usuario=self.admin.username, stdout=salida, **opciones)
        return salida.getvalue()

    def _simular(self, carpeta, proveedor, **opciones):
        plan = Path(carpeta) / "interno.csv"
        self._run(proveedor_interno=str(proveedor.pk), informe=str(plan), **opciones)
        return plan

    def test_aplicar_el_modo_interno_exige_el_plan(self):
        deuda = self._deuda("700.00", proveedor=self.panaderia)

        with self.assertRaisesMessage(CommandError, "--plan"):
            self._run(proveedor_interno=str(self.panaderia.pk), apply=True)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.panaderia.refresh_from_db()
        self.assertFalse(self.panaderia.es_interno)

    def test_el_modo_interno_no_acepta_filtros(self):
        casos = {
            "--sucursal": {"sucursal": ["EC1"]},
            "--excluir-proveedor": {"excluir_proveedor": ["x"]},
            "--incluir-sin-sucursal": {"incluir_sin_sucursal": True},
            "--incluir-posibles-pagos": {"incluir_posibles_pagos": True},
            "--incluir-posibles-pagos-en-efectivo": {"incluir_posibles_pagos_en_efectivo": True},
            "--cargadas-hasta": {"cargadas_hasta": "2026-10-01"},
            "--fecha-pago": {"fecha_pago": "2026-09-23"},
        }
        for opcion, opciones in casos.items():
            with self.subTest(opcion=opcion):
                with self.assertRaisesMessage(CommandError, f"no lleva {opcion}"):
                    self._run(proveedor_interno=str(self.panaderia.pk), **opciones)
        with self.assertRaisesMessage(ValidationError, "no lleva --sucursal"):
            Regularizacion(actor=self.admin, proveedor_interno=self.panaderia, sucursales=["EC1"])

    def test_un_plan_que_no_aprueba_ninguna_deuda_no_marca_al_proveedor(self):
        deuda = self._deuda("700.00", proveedor=self.panaderia)
        with TemporaryDirectory() as carpeta:
            plan = self._simular(carpeta, self.panaderia)
            texto = plan.read_text(encoding="utf-8-sig")
            plan.write_text(texto.replace(Accion.A_REGULARIZAR, "NO_VA"), encoding="utf-8-sig")

            salida = self._run(proveedor_interno=str(self.panaderia.pk), plan=str(plan), apply=True)

        self.assertIn("(0 deudas aprobadas)", salida)
        self.assertIn("No se marco al proveedor OVEJA NEGRA ARMADI SRL", salida)
        self.assertIn("Queda SIN marcar como interno", salida)
        self.assertIn("Siguen abiertas 1 deudas de OVEJA NEGRA ARMADI SRL por 700,00", salida)
        self.panaderia.refresh_from_db()
        self.assertFalse(self.panaderia.es_interno)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_un_proveedor_que_no_parece_la_panaderia_pide_confirmacion(self):
        deuda = self._deuda("500.00")
        with TemporaryDirectory() as carpeta:
            plan = self._simular(carpeta, self.proveedor)

            with self.assertRaisesMessage(CommandError, '--confirmar "Carnes del Norte SA"'):
                self._run(proveedor_interno=str(self.proveedor.pk), plan=str(plan), apply=True)
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
            with self.assertRaisesMessage(CommandError, "--confirmar"):
                self._run(
                    proveedor_interno=str(self.proveedor.pk), plan=str(plan), apply=True, confirmar="Carnes"
                )

            salida = self._run(
                proveedor_interno=str(self.proveedor.pk),
                plan=str(plan),
                apply=True,
                confirmar="carnes del norte sa",
            )

        self.assertIn("Proveedor marcado como interno: Carnes del Norte SA", salida)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        with self.assertRaisesMessage(CommandError, "--confirmar va solo con --proveedor-interno"):
            self._run(hasta="2026-09-23", confirmar="x")

    def test_con_una_deuda_en_error_no_se_marca(self):
        buena = self._deuda("700.00", proveedor=self.panaderia, concepto="Pan")
        rota = self._deuda("300.00", proveedor=self.panaderia, concepto="Facturas", emision=date(2026, 8, 11))
        CuentaPorPagar.objects.filter(pk=rota.pk).update(
            saldo_pendiente=Decimal("200.00"), estado=CuentaPorPagar.Estado.PARCIAL
        )
        with TemporaryDirectory() as carpeta:
            plan = self._simular(carpeta, self.panaderia)
            salida = self._run(proveedor_interno=str(self.panaderia.pk), plan=str(plan), apply=True)

        buena.refresh_from_db()
        self.assertEqual(buena.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertIn("1 deudas quedaron con error", salida)
        self.assertIn("Siguen abiertas 1 deudas", salida)
        self.panaderia.refresh_from_db()
        self.assertFalse(self.panaderia.es_interno)

    def test_si_se_corta_antes_de_marcar_la_re_corrida_con_el_mismo_plan_marca(self):
        deuda = self._deuda("700.00", proveedor=self.panaderia)
        with TemporaryDirectory() as carpeta:
            aprobadas = leer_plan(self._simular(carpeta, self.panaderia), INTERNO)
        primera = Regularizacion(actor=self.admin, proveedor_interno=self.panaderia)
        primera.planificar()
        primera.restringir_a_plan(aprobadas)
        with mock.patch(
            "treasury.regularizacion.set_supplier_internal", side_effect=RuntimeError("se corto la conexion")
        ):
            with self.assertRaises(RuntimeError):
                primera.aplicar()
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.panaderia.refresh_from_db()
        self.assertFalse(self.panaderia.es_interno)

        segunda = Regularizacion(actor=self.admin, proveedor_interno=self.panaderia)
        segunda.planificar()
        segunda.restringir_a_plan(aprobadas)
        segunda.aplicar()

        self.assertEqual(self._por_deuda(segunda)[deuda.pk].accion, Accion.YA_PROCESADA)
        self.assertTrue(segunda.proveedor_marcado)
        self.panaderia.refresh_from_db()
        self.assertTrue(self.panaderia.es_interno)
        self.assertEqual(segunda.abiertas_despues, (0, Decimal("0.00")))

    def test_avisa_las_deudas_que_siguen_abiertas(self):
        self._deuda("700.00", proveedor=self.panaderia, concepto="Pan")
        self._deuda("700.00", proveedor=self.panaderia, concepto="Pan repetido")
        self._deuda("100.00", proveedor=self.panaderia, concepto="Medialunas", emision=date(2026, 8, 11))
        with TemporaryDirectory() as carpeta:
            plan = self._simular(carpeta, self.panaderia)
            salida = self._run(proveedor_interno=str(self.panaderia.pk), plan=str(plan), apply=True)

        self.assertIn("Proveedor marcado como interno", salida)
        self.assertIn("Siguen abiertas 2 deudas de OVEJA NEGRA ARMADI SRL por 1.400,00", salida)


class MesCerradoPosteriorTests(RegularizacionBase):
    """Disponibilidades encadena cada mes desde el ultimo cierre: un pago fechado
    en un mes abierto ANTERIOR a un mes cerrado no llegaria nunca a los meses
    siguientes, asi que tambien se excluye."""

    def test_un_cierre_posterior_excluye_el_pago_fechado_antes(self):
        de_julio = self._deuda("100.00", emision=date(2026, 7, 15))
        de_vivre = self._deuda("200.00", sucursal=self.vivre, emision=date(2026, 7, 15))
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=self.armadi, cerrado=True)

        filas = self._por_deuda(self._plan())
        self.assertEqual(filas[de_julio.pk].accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertIn("Hay un mes cerrado posterior (08/2026)", filas[de_julio.pk].motivo)
        self.assertIn("--fecha-pago", filas[de_julio.pk].motivo)
        self.assertEqual(filas[de_vivre.pk].accion, Accion.A_REGULARIZAR)

        # En el primer mes abierto despues del cierre si se puede.
        filas = self._por_deuda(self._plan(fecha_pago=date(2026, 9, 23)))
        self.assertEqual(filas[de_julio.pk].accion, Accion.A_REGULARIZAR)

    def test_un_cierre_legacy_global_bloquea_a_todas_las_empresas(self):
        de_vivre = self._deuda("200.00", sucursal=self.vivre, emision=date(2026, 7, 15))
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=None, cerrado=True)

        self.assertEqual(self._por_deuda(self._plan())[de_vivre.pk].accion, Accion.EXCLUIDA_MES_CERRADO)

    def test_un_cierre_posterior_hecho_despues_del_plan_frena_la_aplicacion(self):
        deuda = self._deuda("500.00")
        regularizacion = self._plan()
        CierreMensualTesoreria.objects.create(mes=date(2026, 9, 1), empresa=self.armadi, cerrado=True)

        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertIn("09/2026", fila.resultado)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_la_reversion_no_anula_un_egreso_anterior_a_un_mes_cerrado(self):
        deuda = self._deuda("500.00")
        self._aplicar()
        CierreMensualTesoreria.objects.create(mes=date(2026, 9, 1), empresa=self.armadi, cerrado=True)

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        self.assertEqual(reversion.filas[0].accion, Accion.EXCLUIDA_MES_CERRADO)
        self.assertIn("Hay un mes cerrado posterior (09/2026)", reversion.filas[0].motivo)
        self.assertNotIn("--fecha-pago", reversion.filas[0].motivo)
        self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.REGISTRADO)


class ConceptoLargoYBovedaNegativaTests(RegularizacionBase):
    def test_razon_social_y_concepto_largos_no_rompen_el_pago_en_efectivo(self):
        proveedor = self._proveedor("DISTRIBUIDORA DE ALIMENTOS Y BEBIDAS DEL NORTE ARGENTINO SRL")
        deuda = self._deuda("500.00", proveedor=proveedor, concepto="F" * 120)

        fila = self._por_deuda(self._aplicar())[deuda.pk]

        self.assertEqual(fila.accion, Accion.APLICADA)
        egreso = MovimientoCajaCentral.objects.get(pago_tesoreria_id=fila.pago_id)
        self.assertEqual(len(egreso.concepto), 160)
        self.assertTrue(egreso.concepto.startswith(f"Pago a {proveedor.razon_social}: FFF"))

    def test_la_pantalla_de_pago_en_efectivo_tambien_recorta(self):
        proveedor = self._proveedor("P" * 160)
        deuda = self._deuda("300.00", proveedor=proveedor, concepto="G" * 160)

        pago = register_cash_payment(payable=deuda, fecha_pago=AGOSTO, monto=Decimal("300.00"), actor=self.admin)

        egreso = MovimientoCajaCentral.objects.get(pago_tesoreria=pago)
        self.assertEqual(len(egreso.concepto), 160)
        self.assertEqual(egreso.monto, Decimal("300.00"))

    def test_la_consola_avisa_si_una_boveda_quedaria_negativa(self):
        self._deuda("600000.00", sucursal=self.vivre)
        salida = StringIO()

        call_command("regularizar_deudas", usuario=self.admin.username, hasta="2026-09-23", stdout=salida)

        texto = salida.getvalue()
        self.assertIn("-100.000,00", texto)
        self.assertIn("Caja fuerte en negativo: MAPOGO SRL", texto)
        self.assertIn("no deja revertir la validacion", texto)
        self.assertNotIn("Caja fuerte en negativo: ARMADI SRL", texto)
