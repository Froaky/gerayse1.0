"""US-3.20: el comando `regularizar_deudas` ata facturas a debitos del banco.

El ensayo en staging (copia de prod del 13/10) dejo 661 facturas como
POSIBLE_PAGO_EN_GERAYSE: habia debitos del mismo proveedor y empresa sin
asignar, cargados como movimiento suelto (junio sin imputar, julio y agosto
como gasto con rubro). Esas facturas se pagaron por banco. Registrarles
efectivo sacaria de la caja fuerte plata que nunca salio, y dejarlas como
estan cuenta el gasto dos veces (factura + debito imputado). El comando las
ata a su debito antes del efectivo: el banco no se mueve, la caja fuerte solo
baja por el resto, y el debito atado deja de contar como gasto.
"""
import csv
import uuid
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError

from cashops.models import RubroOperativo, Sucursal
from treasury.models import (
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    PagoTesoreria,
)
from treasury.regularizacion import (
    NAMESPACE_REGULARIZACION,
    Accion,
    Reversion,
    leer_ataduras,
    leer_plan,
    token_de_atadura,
)
from treasury.services import (
    _bank_balance_until,
    annul_payment,
    build_economic_period_snapshot,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    importe_sin_asignar_del_movimiento,
    pay_debt_from_bank_movement,
    register_cash_payment,
)
from treasury.tests_regularizar_deudas import AGOSTO, EFECTIVO, LOTE, RegularizacionBase

TRANSFERENCIA = PagoTesoreria.MedioPago.TRANSFERENCIA
MANUAL = MovimientoBancario.Origen.MANUAL
EGRESO_TESORERIA = MovimientoBancario.Origen.EGRESO_TESORERIA
PAGO_TESORERIA = MovimientoBancario.Origen.PAGO_TESORERIA
TRANSFERENCIA_TERCEROS = MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS
OTRO_EGRESO = MovimientoBancario.Clase.OTRO_EGRESO
AGOSTO_COMPLETO = {"date_from": date(2026, 8, 1), "date_to": date(2026, 8, 31)}


def _dia(dia, mes=8):
    return date(2026, mes, dia)


class AtarBancoBase(RegularizacionBase):
    def setUp(self):
        super().setUp()
        self.ec2 = Sucursal.objects.create(
            nombre="Cafe Norte", codigo="EC2", razon_social="ARMADI SRL", empresa=self.armadi
        )
        self.limpieza = RubroOperativo.objects.create(nombre="Limpieza")

    def _debito(
        self,
        monto,
        *,
        fecha,
        proveedor=None,
        cuenta=None,
        sucursal=None,
        clase=TRANSFERENCIA_TERCEROS,
        origen=MANUAL,
        rubro=None,
        categoria=None,
        referencia="",
    ):
        return create_bank_movement(
            cuenta_bancaria=cuenta or self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=fecha,
            monto=Decimal(monto),
            concepto="Transferencia a proveedor",
            clase=clase,
            proveedor=proveedor or self.proveedor,
            rubro_operativo=rubro or self.rubro,
            sucursal_gasto=sucursal or self.ec1,
            periodo_pago=fecha.replace(day=1),
            origen=origen,
            categoria=categoria,
            referencia=referencia,
            actor=self.admin,
        )

    def _banco(self):
        return _bank_balance_until(self.cuenta, date(2026, 12, 31))["balance"]

    @staticmethod
    def _sin_asignar(debito):
        return importe_sin_asignar_del_movimiento(MovimientoBancario.objects.get(pk=debito.pk))

    @staticmethod
    def _leer_csv(ruta):
        with Path(ruta).open(encoding="utf-8-sig", newline="") as archivo:
            return list(csv.DictReader(archivo, delimiter=";"))

    @staticmethod
    def _escribir_csv(ruta, filas):
        with Path(ruta).open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=list(filas[0].keys()), delimiter=";")
            escritor.writeheader()
            escritor.writerows(filas)


class AsignacionTests(AtarBancoBase):
    def test_exacta_ata_la_factura_del_mismo_importe_aunque_haya_otras_mas_viejas(self):
        vieja = self._deuda("1000.00", emision=_dia(1))
        exacta = self._deuda("700.00", emision=_dia(5))
        mismo_importe_mas_nueva = self._deuda("700.00", emision=_dia(7))
        debito = self._debito("700.00", fecha=_dia(20))

        filas = self._por_deuda(self._plan())

        fila = filas[exacta.pk]
        self.assertEqual(fila.accion, Accion.A_ATAR_BANCO)
        self.assertEqual(fila.debito_id, debito.pk)
        self.assertEqual(fila.importe_debito, Decimal("700.00"))
        self.assertEqual(fila.medio, TRANSFERENCIA)
        self.assertEqual(fila.fecha_pago, _dia(20))
        self.assertIn("mismo importe", fila.motivo)
        self.assertIn("imputado como gasto", fila.aviso)
        # Las demas quedan para revisar: el debito ya no les alcanza.
        for otra in (vieja, mismo_importe_mas_nueva):
            self.assertEqual(filas[otra.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
            self.assertIn("se atan a otras facturas", filas[otra.pk].aviso)

    def test_combinacion_exacta_gana_al_reparto_por_antiguedad(self):
        a = self._deuda("100.00", emision=_dia(1))
        b = self._deuda("250.00", emision=_dia(2))
        c = self._deuda("400.00", emision=_dia(3))
        d = self._deuda("900.00", emision=_dia(4))
        debito = self._debito("650.00", fecha=_dia(20))

        filas = self._por_deuda(self._plan())

        self.assertEqual({filas[x.pk].accion for x in (b, c)}, {Accion.A_ATAR_BANCO})
        self.assertEqual({filas[x.pk].debito_id for x in (b, c)}, {debito.pk})
        self.assertIn("combinacion exacta de 2 facturas", filas[b.pk].motivo)
        self.assertEqual(filas[a.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertEqual(filas[d.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)

    def test_la_combinacion_es_de_hasta_seis_facturas_consecutivas(self):
        seis = [self._deuda("100.00", emision=_dia(dia), concepto=f"F{dia}") for dia in range(1, 7)]
        siete = [self._deuda("10.00", emision=_dia(dia, 9), concepto=f"G{dia}") for dia in range(1, 8)]
        para_seis = self._debito("600.00", fecha=_dia(20))
        para_siete = self._debito("70.00", fecha=_dia(20, 9))

        filas = self._por_deuda(self._plan())

        self.assertEqual({filas[x.pk].debito_id for x in seis}, {para_seis.pk})
        self.assertIn("combinacion exacta de 6 facturas", filas[seis[0].pk].motivo)
        # Siete no entran en una combinacion: van por antiguedad y suman igual.
        self.assertEqual({filas[x.pk].debito_id for x in siete}, {para_siete.pk})
        self.assertIn("reparto por antiguedad entre 7", filas[siete[0].pk].motivo)

    def test_antiguedad_solo_con_facturas_enteras_y_el_sobrante_queda_sin_asignar(self):
        # Un debito ya atado a otra factura (PAGO_TESORERIA) no cuenta como
        # gasto: puede quedar con sobrante, que sigue disponible para atar.
        pagada_a_mano = self._deuda("400.00", emision=_dia(1), concepto="Pagada a mano desde el debito")
        debito = self._debito("1400.00", fecha=_dia(20))
        pay_debt_from_bank_movement(
            bank_movement=debito, payable=pagada_a_mano, monto=Decimal("400.00"), actor=self.admin
        )
        a = self._deuda("300.00", emision=_dia(1))
        b = self._deuda("500.00", emision=_dia(2))
        grande = self._deuda("2000.00", emision=_dia(3))
        c = self._deuda("150.00", emision=_dia(4))

        regularizacion = self._plan()
        filas = self._por_deuda(regularizacion)

        for atada in (a, b, c):
            self.assertEqual(filas[atada.pk].accion, Accion.A_ATAR_BANCO)
            self.assertEqual(filas[atada.pk].debito_id, debito.pk)
        self.assertIn("reparto por antiguedad entre 3 factura(s); le quedan 50,00 sin asignar", filas[a.pk].motivo)
        self.assertNotIn("que le sobran", filas[a.pk].aviso)
        self.assertEqual(filas[grande.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertEqual(
            regularizacion.sobrantes_de_debitos(),
            {
                "debitos": 1,
                "sobrante": Decimal("50.00"),
                "debitos_de_gasto": 0,
                "sobrante_de_gasto": Decimal("0.00"),
            },
        )

        regularizacion.aplicar()

        self.assertEqual(self._sin_asignar(debito), Decimal("50.00"))
        grande.refresh_from_db()
        self.assertEqual(grande.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(grande.pagos.count(), 0)

    def test_nunca_paga_parcial_una_factura(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("600.00", fecha=_dia(20))

        regularizacion = self._aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        self.assertEqual(self._sin_asignar(debito), Decimal("600.00"))

    def test_el_debito_con_sucursal_solo_paga_facturas_de_esa_sucursal(self):
        de_ec2 = self._deuda("1000.00", sucursal=self.ec2, emision=_dia(1))
        de_ec1 = self._deuda("1000.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20), sucursal=self.ec1)

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[de_ec1.pk].accion, Accion.A_ATAR_BANCO)
        self.assertEqual(filas[de_ec1.pk].debito_id, debito.pk)
        self.assertEqual(filas[de_ec2.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)

    def test_el_debito_sin_sucursal_paga_cualquiera_de_su_empresa(self):
        de_ec2 = self._deuda("1000.00", sucursal=self.ec2, emision=_dia(1))
        de_vivre = self._deuda("1000.00", sucursal=self.vivre, emision=_dia(1))
        debito = self._debito("1000.00", fecha=_dia(20))
        MovimientoBancario.objects.filter(pk=debito.pk).update(sucursal_gasto=None)

        regularizacion = self._aplicar()
        filas = self._por_deuda(regularizacion)

        self.assertEqual(filas[de_ec2.pk].debito_id, debito.pk)
        self.assertTrue(filas[de_ec2.pk].pago_id)
        # La factura de MAPOGO no se paga con la cuenta de ARMADI: va en
        # efectivo (registrada, queda APLICADA).
        self.assertEqual(filas[de_vivre.pk].accion, Accion.APLICADA)
        self.assertIsNone(filas[de_vivre.pk].debito_id)
        debito.refresh_from_db()
        self.assertEqual(debito.sucursal_gasto_id, self.ec2.pk)

    def test_ventana_de_menos_30_a_mas_75_dias(self):
        emision = date(2026, 8, 10)
        borde_antes = self._deuda("100.00", emision=emision, concepto="Borde antes")
        borde_despues = self._deuda("200.00", emision=emision, concepto="Borde despues")
        d1 = self._debito("100.00", fecha=date(2026, 7, 11))
        d2 = self._debito("200.00", fecha=date(2026, 10, 24))
        afuera = self._deuda("300.00", proveedor=self.verduleria, emision=emision)
        self._debito("300.00", fecha=date(2026, 7, 10), proveedor=self.verduleria)
        self._debito("300.00", fecha=date(2026, 10, 25), proveedor=self.verduleria)

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[borde_antes.pk].debito_id, d1.pk)
        self.assertEqual(filas[borde_despues.pk].debito_id, d2.pk)
        self.assertEqual(filas[afuera.pk].accion, Accion.A_REGULARIZAR)
        self.assertIsNone(filas[afuera.pk].debito_id)

    def test_cheque_se_ata_como_cheque_y_un_impuesto_no_paga_facturas(self):
        con_cheque = self._deuda("800.00", emision=_dia(1))
        cheque = self._debito("800.00", fecha=_dia(20), clase=MovimientoBancario.Clase.CHEQUE, referencia="CH-77")
        otra = self._deuda("500.00", proveedor=self.verduleria)
        self._debito("500.00", fecha=_dia(20), proveedor=self.verduleria, clase=MovimientoBancario.Clase.IMPUESTO)

        regularizacion = self._aplicar()
        filas = self._por_deuda(regularizacion)

        self.assertEqual(filas[con_cheque.pk].medio, PagoTesoreria.MedioPago.CHEQUE)
        pago = con_cheque.pagos.get()
        self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.CHEQUE)
        cheque.refresh_from_db()
        self.assertEqual(cheque.clase, MovimientoBancario.Clase.CHEQUE)
        self.assertEqual(cheque.origen, PAGO_TESORERIA)
        # El impuesto la deja como posible pago, pero no la paga.
        self.assertEqual(filas[otra.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertEqual(otra.pagos.count(), 0)

    def test_debito_de_una_cuenta_sin_empresa_ni_sucursal_no_se_ata(self):
        cuenta_legacy = create_bank_account(
            nombre="Cuenta vieja",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="300-9",
            cbu="2850590940090418135209",
            empresa=self.armadi,
            actor=self.admin,
        )
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=_dia(20), cuenta=cuenta_legacy)
        # Legacy: ni la cuenta ni el debito dicen de que empresa son.
        CuentaBancaria.objects.filter(pk=cuenta_legacy.pk).update(empresa=None, sucursal=None)
        MovimientoBancario.objects.filter(pk=debito.pk).update(sucursal_gasto=None)

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_GERAYSE)

    def test_las_duplicadas_y_las_excluidas_no_se_atan(self):
        duplicada = self._deuda("1000.00", concepto="Factura")
        self._deuda("1000.00", concepto="Factura otra vez")
        afip = self._proveedor("AFIP")
        impuesto = self._deuda("1000.00", proveedor=afip)
        self._debito("1000.00", fecha=_dia(20))
        self._debito("1000.00", fecha=_dia(20), proveedor=afip)

        filas = self._por_deuda(self._plan())

        self.assertEqual(filas[duplicada.pk].accion, Accion.POSIBLE_DUPLICADA)
        self.assertEqual(filas[impuesto.pk].accion, Accion.EXCLUIDA_IMPUESTO)
        self.assertFalse(any(f.accion == Accion.A_ATAR_BANCO for f in filas.values()))

    def test_sin_atar_banco_queda_el_comportamiento_anterior(self):
        deuda = self._deuda("1000.00")
        self._debito("1000.00", fecha=_dia(20))

        fila = self._por_deuda(self._plan(atar_banco=False))[deuda.pk]

        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertIsNone(fila.debito_id)

    def test_incluir_posibles_pagos_manda_a_efectivo_solo_las_que_no_se_atan(self):
        atada = self._deuda("1000.00", emision=_dia(1))
        sin_debito = self._deuda("500.00", emision=_dia(2))
        self._debito("1000.00", fecha=_dia(20))

        filas = self._por_deuda(self._plan(incluir_posibles_pagos=True))

        self.assertEqual(filas[atada.pk].accion, Accion.A_ATAR_BANCO)
        self.assertNotIn("incluida con --incluir-posibles-pagos", filas[atada.pk].aviso)
        self.assertEqual(filas[sin_debito.pk].accion, Accion.A_REGULARIZAR)
        self.assertIn("incluida con --incluir-posibles-pagos", filas[sin_debito.pk].aviso)

    def test_un_debito_ya_atado_en_parte_paga_con_lo_que_le_queda(self):
        otra = self._deuda("400.00", emision=_dia(1), concepto="Pagada a mano desde el debito")
        deuda = self._deuda("600.00", emision=_dia(2))
        debito = self._debito("1000.00", fecha=_dia(20))
        pay_debt_from_bank_movement(
            bank_movement=debito, payable=otra, monto=Decimal("400.00"), actor=self.admin
        )

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.A_ATAR_BANCO)
        self.assertIn("Ya tenia 400,00 asignados", fila.motivo)

    def test_token_de_atadura_por_lote_deuda_y_debito(self):
        self.assertEqual(
            token_de_atadura(LOTE, 12, 34), uuid.uuid5(NAMESPACE_REGULARIZACION, f"{LOTE}|ATAR|12|34")
        )


class AplicacionAtarTests(AtarBancoBase):
    def test_ata_al_debito_sin_mover_el_banco_y_el_resto_va_a_efectivo(self):
        atada = self._deuda("1000.00", emision=_dia(5))
        en_efectivo = self._deuda("500.00", proveedor=self.verduleria, emision=_dia(6))
        debito = self._debito("1000.00", fecha=_dia(20), referencia="OP-123")
        banco = self._banco()
        movimientos = MovimientoBancario.objects.count()
        boveda = self._boveda(self.armadi)

        regularizacion = self._aplicar()

        atada.refresh_from_db()
        self.assertEqual(atada.estado, CuentaPorPagar.Estado.PAGADA)
        pago = atada.pagos.get()
        self.assertEqual(pago.medio_pago, TRANSFERENCIA)
        self.assertEqual(pago.movimiento_bancario_id, debito.pk)
        self.assertEqual(pago.cuenta_bancaria_id, self.cuenta.pk)
        self.assertEqual(pago.monto, Decimal("1000.00"))
        self.assertEqual(pago.fecha_pago, _dia(20))
        self.assertEqual(pago.referencia, f"{LOTE} #{atada.pk}")
        self.assertEqual(pago.token_alta, token_de_atadura(LOTE, atada.pk, debito.pk))
        self.assertEqual(
            pago.observaciones,
            f"Atado al debito #{debito.pk} del 20/08/2026 por la regularizacion {LOTE} (corte 23/09/2026)",
        )
        self.assertIn("origen=MANUAL", pago.observacion_bancaria)
        self.assertIn("pagos=0", pago.observacion_bancaria)
        debito.refresh_from_db()
        self.assertEqual(debito.origen, PAGO_TESORERIA)
        self.assertEqual(debito.estado, MovimientoBancario.Estado.REGISTRADO)
        self.assertEqual(self._sin_asignar(debito), Decimal("0.00"))
        # El banco no se mueve: no hay debito nuevo y el saldo es el mismo.
        self.assertEqual(MovimientoBancario.objects.count(), movimientos)
        self.assertEqual(self._banco(), banco)
        # La caja fuerte solo baja por lo que fue en efectivo.
        self.assertEqual(self._boveda(self.armadi), boveda - Decimal("500.00"))
        self.assertEqual(en_efectivo.pagos.get().medio_pago, EFECTIVO)
        # Primero las ataduras, despues el efectivo.
        self.assertLess(pago.pk, en_efectivo.pagos.get().pk)
        self.assertEqual(regularizacion.errores(), [])

    def test_el_gasto_del_debito_atado_no_queda_contado_dos_veces(self):
        materia_prima = self._deuda("1000.00", emision=_dia(5))
        limpieza = create_payable_category(nombre="Limpieza", rubro_operativo=self.limpieza, actor=self.admin)
        de_limpieza = self._deuda("400.00", proveedor=self.verduleria, categoria=limpieza, emision=_dia(6))
        # Julio/agosto: la transferencia se cargo como gasto con rubro (MANUAL)
        # o como egreso de tesoreria, y la factura igual quedo abierta.
        self._debito("1000.00", fecha=_dia(20))
        self._debito(
            "400.00",
            fecha=_dia(21),
            proveedor=self.verduleria,
            clase=OTRO_EGRESO,
            origen=EGRESO_TESORERIA,
            rubro=self.limpieza,
        )
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(antes["treasury_expense_total"], Decimal("1400.00"))
        self.assertEqual(antes["debt_period_total"], Decimal("1400.00"))
        banco = self._banco()

        self._aplicar()

        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        # Lo atado ya no es gasto de tesoreria: lo cuenta la factura, una vez.
        self.assertEqual(despues["treasury_expense_total"], Decimal("0.00"))
        self.assertEqual(despues["debt_period_total"], Decimal("1400.00"))
        self.assertEqual(despues["economic_result"], antes["economic_result"] + Decimal("1400.00"))
        por_rubro = {item["rubro"].pk: item for item in despues["rubro_items"] if item["rubro"] is not None}
        self.assertEqual(por_rubro[self.limpieza.pk]["treasury_expense_total"], Decimal("0.00"))
        self.assertEqual(por_rubro[self.limpieza.pk]["total_expense"], Decimal("400.00"))
        self.assertEqual(por_rubro[self.rubro.pk]["total_expense"], Decimal("1000.00"))
        self.assertEqual(despues["debt_pending_total"], Decimal("0.00"))
        for deuda in (materia_prima, de_limpieza):
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(self._banco(), banco)

    def test_segunda_corrida_no_duplica_y_una_atadura_anulada_no_se_rehace_sola(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=_dia(20))
        self._aplicar()
        pagos = PagoTesoreria.objects.count()

        otra_corrida = self._aplicar()

        self.assertEqual(PagoTesoreria.objects.count(), pagos)
        self.assertEqual(otra_corrida.filas, [])

        annul_payment(payment=deuda.pagos.get(), motivo="Atada a mano por error", actor=self.admin)
        regularizacion = self._aplicar()
        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("se anulo", fila.motivo)
        self.assertEqual(deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 0)
        self.assertEqual(self._sin_asignar(debito), Decimal("1000.00"))

    def test_el_debito_que_se_ata_en_el_medio_no_se_paga_parcial(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=_dia(20))
        regularizacion = self._plan()
        otra = self._deuda("300.00", concepto="Pagada a mano desde el debito")
        pay_debt_from_bank_movement(bank_movement=debito, payable=otra, monto=Decimal("300.00"), actor=self.admin)

        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("le quedan 700,00 sin asignar", fila.resultado)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_una_factura_excluida_por_mes_cerrado_tampoco_se_ata(self):
        # Mismas exclusiones que el efectivo: una factura cuyo pago caeria en
        # un mes cerrado queda excluida y no es candidata a atarse.
        deuda = self._deuda("1000.00")
        self._debito("1000.00", fecha=date(2026, 9, 5))
        CierreMensualTesoreria.objects.create(mes=date(2026, 8, 1), empresa=self.armadi, cerrado=True)

        fila = self._por_deuda(self._plan())[deuda.pk]

        self.assertEqual(fila.accion, Accion.EXCLUIDA_MES_CERRADO)


def _gasto_total(snapshot):
    """Gasto de tesoreria (debitos imputados) mas deuda del periodo."""
    return snapshot["treasury_expense_total"] + snapshot["debt_period_total"]


class DebitoDeGastoTests(AtarBancoBase):
    """Al atarlo, el debito pasa ENTERO a PAGO_TESORERIA y sale de la lectura
    economica (como gasto o como pendiente de imputar). Un debito que hoy
    cuenta solo se ata si sus facturas lo consumen entero y tienen rubro: el
    gasto total no puede bajar mas que lo atado."""

    def _sin_rubro(self, deuda):
        legacy, _ = CategoriaCuentaPagar.objects.get_or_create(nombre="Legacy sin rubro", defaults={"activo": True})
        CuentaPorPagar.objects.filter(pk=deuda.pk).update(categoria=legacy)
        return legacy

    def test_un_debito_imputado_como_gasto_no_se_ata_si_le_sobra(self):
        deuda = self._deuda("600.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)

        regularizacion = self._plan()
        fila = self._por_deuda(regularizacion)[deuda.pk]

        self.assertEqual(fila.accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertIsNone(fila.debito_id)
        self.assertIn(f"No se ata al debito #{debito.pk}", fila.aviso)
        self.assertIn("le sobrarian 400,00 sin asignar", fila.aviso)
        self.assertEqual(
            regularizacion.debitos_no_atados,
            [{"debito_id": debito.pk, "sobrante": Decimal("400.00"), "facturas": 1, "importe": Decimal("600.00")}],
        )

        regularizacion.aplicar()

        self.assertEqual(PagoTesoreria.objects.count(), 0)
        debito.refresh_from_db()
        self.assertEqual(debito.origen, MANUAL)
        self.assertEqual(self._sin_asignar(debito), Decimal("1000.00"))
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(despues["treasury_expense_total"], antes["treasury_expense_total"])
        self.assertEqual(despues["treasury_expense_total"], Decimal("1000.00"))

    def test_el_gasto_total_no_baja_mas_que_lo_atado(self):
        # Debito de gasto con sobrante (300 + 500 de 1000): no se ata.
        self._deuda("300.00", emision=_dia(1))
        self._deuda("500.00", emision=_dia(2))
        self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        # Egreso de tesoreria que la factura consume entero: se ata.
        exacta = self._deuda("400.00", proveedor=self.verduleria, emision=_dia(3))
        self._debito(
            "400.00",
            fecha=_dia(21),
            proveedor=self.verduleria,
            clase=OTRO_EGRESO,
            origen=EGRESO_TESORERIA,
            rubro=self.limpieza,
        )
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(_gasto_total(antes), Decimal("2600.00"))
        banco = self._banco()

        regularizacion = self._aplicar()

        atado = sum(
            (f.saldo for f in regularizacion.filas if f.accion == Accion.A_ATAR_BANCO and f.pago_id), Decimal("0.00")
        )
        self.assertEqual(atado, Decimal("400.00"))
        self.assertEqual(exacta.pagos.get().estado, PagoTesoreria.Estado.REGISTRADO)
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        # Lo atado deja de contarse dos veces; nada mas.
        self.assertEqual(_gasto_total(antes) - _gasto_total(despues), atado)
        self.assertEqual(despues["treasury_expense_total"], Decimal("1000.00"))
        self.assertEqual(despues["debt_period_total"], Decimal("1200.00"))
        self.assertEqual(self._banco(), banco)

    def test_un_debito_pendiente_de_imputar_no_se_ata_si_le_sobra(self):
        deuda = self._deuda("600.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20))
        # Junio: cargado suelto, sin rubro, sucursal ni periodo.
        MovimientoBancario.objects.filter(pk=debito.pk).update(
            rubro_operativo=None, sucursal_gasto=None, periodo_pago=None
        )
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(antes["treasury_unmapped_expenses_total"], Decimal("1000.00"))

        regularizacion = self._aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        # Sigue en la lista de pendientes de imputar, entero.
        self.assertEqual(despues["treasury_unmapped_expenses_total"], Decimal("1000.00"))

    def test_atar_con_sobrante_lo_acepta_y_lo_avisa(self):
        deuda = self._deuda("600.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)

        regularizacion = self._plan(atar_con_sobrante=True)
        fila = self._por_deuda(regularizacion)[deuda.pk]

        self.assertEqual(fila.accion, Accion.A_ATAR_BANCO)
        self.assertIn("Con --atar-con-sobrante: los 400,00 que le sobran", fila.aviso)
        self.assertEqual(
            regularizacion.sobrantes_de_debitos(),
            {
                "debitos": 1,
                "sobrante": Decimal("400.00"),
                "debitos_de_gasto": 1,
                "sobrante_de_gasto": Decimal("400.00"),
            },
        )
        self.assertEqual(regularizacion.debitos_no_atados, [])

        regularizacion.aplicar()

        self.assertEqual(deuda.pagos.get().movimiento_bancario_id, debito.pk)
        self.assertEqual(self._sin_asignar(debito), Decimal("400.00"))
        # Es lo que se acepta con la opcion: el sobrante deja de contar.
        self.assertEqual(build_economic_period_snapshot(**AGOSTO_COMPLETO)["treasury_expense_total"], Decimal("0.00"))
        with self.assertRaisesMessage(Exception, "--atar-con-sobrante"):
            self._regularizacion(atar_banco=False, atar_con_sobrante=True)

    def test_si_una_factura_del_debito_falla_al_aplicar_no_se_ata_ninguna(self):
        primera = self._deuda("600.00", emision=_dia(1))
        segunda = self._deuda("400.00", emision=_dia(2))
        debito = self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        regularizacion = self._plan()
        filas = self._por_deuda(regularizacion)
        self.assertEqual({filas[d.pk].accion for d in (primera, segunda)}, {Accion.A_ATAR_BANCO})
        # Entre el plan y la aplicacion le pagan una parte de la segunda.
        register_cash_payment(payable=segunda, fecha_pago=AGOSTO, monto=Decimal("50.00"), actor=self.admin)
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)

        regularizacion.aplicar()

        self.assertEqual(filas[segunda.pk].accion, Accion.ERROR)
        self.assertIn("El saldo cambio", filas[segunda.pk].resultado)
        self.assertEqual(filas[primera.pk].accion, Accion.ERROR)
        self.assertIn(f"la factura #{segunda.pk} del mismo debito no se pudo atar", filas[primera.pk].resultado)
        self.assertIsNone(filas[primera.pk].pago_id)
        self.assertEqual(primera.pagos.count(), 0)
        debito.refresh_from_db()
        self.assertEqual(debito.origen, MANUAL)
        self.assertEqual(debito.rubro_operativo_id, self.limpieza.pk)
        self.assertEqual(self._sin_asignar(debito), Decimal("1000.00"))
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(despues["treasury_expense_total"], antes["treasury_expense_total"])

    def test_al_aplicar_el_sobrante_se_verifica_con_el_debito_bloqueado(self):
        deuda = self._deuda("1000.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20))
        regularizacion = self._plan()
        # El debito se corrigio despues del plan (por ejemplo, el importe).
        MovimientoBancario.objects.filter(pk=debito.pk).update(monto=Decimal("1100.00"))

        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("le quedarian 100,00 sin asignar", fila.resultado)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_plan_revisado_sin_una_fila_de_la_combinacion_no_ata_el_debito(self):
        primera = self._deuda("600.00", emision=_dia(1))
        segunda = self._deuda("400.00", emision=_dia(2))
        debito = self._debito("1000.00", fecha=_dia(20))
        sin_rubro = self._deuda("700.00", proveedor=self.verduleria, emision=_dia(3))
        otro = self._debito("700.00", fecha=_dia(21), proveedor=self.verduleria)
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "plan.csv"
            self._plan().escribir_informe(ruta)
            filas = [f for f in self._leer_csv(ruta) if int(f["deuda_id"]) != segunda.pk]
            self._escribir_csv(ruta, filas)
            aprobadas, ataduras = leer_plan(ruta, EFECTIVO), leer_ataduras(ruta)
        self.assertEqual(ataduras[primera.pk], (Decimal("600.00"), debito.pk))
        self.assertEqual(ataduras[sin_rubro.pk], (Decimal("700.00"), otro.pk))
        # Despues de la revision, la categoria de una factura queda sin rubro.
        self._sin_rubro(sin_rubro)

        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas, ataduras)
        filas = self._por_deuda(regularizacion)

        self.assertEqual(filas[primera.pk].accion, Accion.ERROR)
        self.assertIn("le quedarian 400,00 sin asignar", filas[primera.pk].motivo)
        self.assertEqual(filas[sin_rubro.pk].accion, Accion.ERROR)
        self.assertIn("no tiene rubro", filas[sin_rubro.pk].motivo)
        regularizacion.aplicar()
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        # Con --atar-con-sobrante el plan sin la fila se acepta (la sin rubro no).
        regularizacion = self._plan(atar_con_sobrante=True)
        regularizacion.restringir_a_plan(aprobadas, ataduras)
        regularizacion.aplicar()
        filas = self._por_deuda(regularizacion)
        self.assertEqual(primera.pagos.get().movimiento_bancario_id, debito.pk)
        self.assertEqual(filas[sin_rubro.pk].accion, Accion.ERROR)
        self.assertEqual(sin_rubro.pagos.count(), 0)

    def test_categoria_sin_rubro_no_se_ata_a_un_debito_de_gasto(self):
        deuda = self._deuda("1000.00", emision=_dia(5))
        self._sin_rubro(deuda)
        debito = self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        # Y un debito pendiente de imputar con otra factura sin rubro.
        pendiente_deuda = self._deuda("500.00", proveedor=self.verduleria, emision=_dia(6))
        self._sin_rubro(pendiente_deuda)
        pendiente = self._debito("500.00", fecha=_dia(21), proveedor=self.verduleria)
        MovimientoBancario.objects.filter(pk=pendiente.pk).update(
            rubro_operativo=None, sucursal_gasto=None, periodo_pago=None
        )
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(_gasto_total(antes), Decimal("1000.00"))

        regularizacion = self._aplicar()
        filas = self._por_deuda(regularizacion)

        for sin_rubro, su_debito in ((deuda, debito), (pendiente_deuda, pendiente)):
            self.assertEqual(filas[sin_rubro.pk].accion, Accion.POSIBLE_PAGO_EN_GERAYSE)
            self.assertIn(f"el debito #{su_debito.pk} cuenta como gasto", filas[sin_rubro.pk].aviso)
        self.assertEqual(regularizacion.sin_rubro_sin_atar, 2)
        self.assertEqual(regularizacion.errores(), [])
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        self.assertEqual(_gasto_total(despues), _gasto_total(antes))
        self.assertEqual(despues["treasury_unmapped_expenses_total"], antes["treasury_unmapped_expenses_total"])

    def test_categoria_sin_rubro_se_ata_a_un_debito_que_ya_no_cuenta_como_gasto(self):
        con_rubro = self._deuda("600.00", emision=_dia(1), concepto="Pagada a mano desde el debito")
        debito = self._debito("1600.00", fecha=_dia(20), rubro=self.limpieza)
        pay_debt_from_bank_movement(bank_movement=debito, payable=con_rubro, monto=Decimal("600.00"), actor=self.admin)
        deuda = self._deuda("1000.00", emision=_dia(5))
        self._sin_rubro(deuda)
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)

        regularizacion = self._aplicar()

        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.A_ATAR_BANCO)
        self.assertEqual(deuda.pagos.get().movimiento_bancario_id, debito.pk)
        # El debito ya no contaba como gasto: la lectura no cambia.
        self.assertEqual(_gasto_total(build_economic_period_snapshot(**AGOSTO_COMPLETO)), _gasto_total(antes))

    def test_categoria_sin_rubro_al_aplicar_no_se_ata(self):
        deuda = self._deuda("1000.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        regularizacion = self._plan()
        self.assertEqual(self._por_deuda(regularizacion)[deuda.pk].accion, Accion.A_ATAR_BANCO)
        self._sin_rubro(deuda)
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)

        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.ERROR)
        self.assertIn("no tiene rubro", fila.resultado)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        debito.refresh_from_db()
        self.assertEqual(debito.origen, MANUAL)
        self.assertEqual(_gasto_total(build_economic_period_snapshot(**AGOSTO_COMPLETO)), _gasto_total(antes))


class PlanRevisadoAtarTests(AtarBancoBase):
    def test_aplica_exactamente_las_ataduras_del_plan_y_verifica_bajo_lock(self):
        aprobada = self._deuda("1000.00", emision=_dia(1), concepto="Aprobada")
        d1 = self._debito("1000.00", fecha=_dia(20))
        sacada = self._deuda("300.00", emision=_dia(2), concepto="Sacada del plan")
        self._debito("300.00", fecha=_dia(21))
        efectivo = self._deuda("200.00", proveedor=self.verduleria, concepto="Efectivo")
        sin_lugar = self._deuda("450.00", emision=_dia(3), concepto="El debito se usa en el medio")
        d3 = self._debito("450.00", fecha=_dia(22))
        cambia = self._deuda("600.00", emision=_dia(4), concepto="Cambia de saldo")
        self._debito("600.00", fecha=_dia(23))
        con_otro_debito = self._deuda("800.00", emision=_dia(5), concepto="Tesoreria elige el debito")
        d4 = self._debito("800.00", fecha=_dia(24))
        d5 = self._debito("800.00", fecha=_dia(25))

        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "plan.csv"
            self._plan().escribir_informe(ruta)
            filas = self._leer_csv(ruta)
            por_id = {int(f["deuda_id"]): f for f in filas}
            self.assertEqual(por_id[aprobada.pk]["accion"], Accion.A_ATAR_BANCO)
            self.assertEqual(por_id[aprobada.pk]["debito_id"], str(d1.pk))
            self.assertEqual(por_id[aprobada.pk]["importe_debito"], "1.000,00")
            self.assertEqual(por_id[con_otro_debito.pk]["debito_id"], str(d4.pk))
            # Tesoreria saca una fila y cambia el debito de otra.
            por_id[con_otro_debito.pk]["debito_id"] = str(d5.pk)
            filas = [f for f in filas if int(f["deuda_id"]) != sacada.pk]
            self._escribir_csv(ruta, filas)
            aprobadas = leer_plan(ruta, EFECTIVO)
            ataduras = leer_ataduras(ruta)
        self.assertEqual(set(aprobadas), {efectivo.pk})
        self.assertEqual(ataduras[con_otro_debito.pk], (Decimal("800.00"), d5.pk))
        # Entre la revision y la aplicacion: alguien usa parte de d3 para otra
        # factura y le paga una parte de otra en efectivo.
        de_otro = self._deuda("100.00", emision=_dia(9), concepto="Otra")
        pay_debt_from_bank_movement(bank_movement=d3, payable=de_otro, monto=Decimal("100.00"), actor=self.admin)
        register_cash_payment(payable=cambia, fecha_pago=AGOSTO, monto=Decimal("50.00"), actor=self.admin)

        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas, ataduras)
        regularizacion.aplicar()

        filas = self._por_deuda(regularizacion)
        self.assertEqual(aprobada.pagos.get().movimiento_bancario_id, d1.pk)
        self.assertEqual(filas[sacada.pk].accion, Accion.FUERA_DEL_PLAN)
        self.assertEqual(sacada.pagos.count(), 0)
        self.assertEqual(efectivo.pagos.get().medio_pago, EFECTIVO)
        self.assertEqual(filas[sin_lugar.pk].accion, Accion.ERROR)
        self.assertIn("le quedan 350,00 sin asignar", filas[sin_lugar.pk].resultado)
        self.assertEqual(sin_lugar.pagos.count(), 0)
        self.assertEqual(filas[cambia.pk].accion, Accion.DISTINTA_AL_PLAN)
        self.assertEqual(con_otro_debito.pagos.get().movimiento_bancario_id, d5.pk)
        self.assertIn(f"ataria al debito #{d4.pk}", filas[con_otro_debito.pk].aviso)
        self.assertEqual(self._sin_asignar(d4), Decimal("800.00"))

    def test_lo_aprobado_en_efectivo_que_hoy_tiene_debito_no_se_paga(self):
        deuda = self._deuda("1000.00")
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "plan.csv"
            self._plan().escribir_informe(ruta)
            aprobadas, ataduras = leer_plan(ruta, EFECTIVO), leer_ataduras(ruta)
        self._debito("1000.00", fecha=_dia(20))

        regularizacion = self._plan()
        regularizacion.restringir_a_plan(aprobadas, ataduras)
        regularizacion.aplicar()

        fila = self._por_deuda(regularizacion)[deuda.pk]
        self.assertEqual(fila.accion, Accion.FUERA_DEL_PLAN)
        self.assertIn("pagaba en efectivo", fila.aviso)
        self.assertEqual(PagoTesoreria.objects.count(), 0)

    def test_leer_ataduras_valida_el_plan(self):
        with TemporaryDirectory() as carpeta:
            sin_columna = Path(carpeta) / "sin_columna.csv"
            sin_columna.write_text(
                "deuda_id;accion;saldo;medio\n1;A_ATAR_BANCO;10,00;TRANSFERENCIA\n", encoding="utf-8"
            )
            with self.assertRaisesMessage(Exception, "debito_id"):
                leer_ataduras(sin_columna)
            doble = Path(carpeta) / "doble.csv"
            doble.write_text(
                "deuda_id;accion;saldo;medio;debito_id\n"
                "1;A_ATAR_BANCO;10,00;TRANSFERENCIA;5\n"
                "1;A_ATAR_BANCO;10,00;TRANSFERENCIA;6\n",
                encoding="utf-8",
            )
            with self.assertRaisesMessage(Exception, "dos veces"):
                leer_ataduras(doble)
            viejo = Path(carpeta) / "viejo.csv"
            viejo.write_text("deuda_id;accion;saldo;medio\n1;A_REGULARIZAR;10,00;EFECTIVO\n", encoding="utf-8")
            self.assertEqual(leer_ataduras(viejo), {})

    def test_un_plan_con_ataduras_no_se_aplica_sin_atar_banco(self):
        regularizacion = self._plan(atar_banco=False)
        with self.assertRaisesMessage(Exception, "A_ATAR_BANCO"):
            regularizacion.restringir_a_plan({}, {1: (Decimal("10.00"), 2)})


class ReversionAtarTests(AtarBancoBase):
    def test_revertir_desata_y_devuelve_la_imputacion_que_tenia_el_debito(self):
        x = self._deuda("1000.00", emision=_dia(1))
        y = self._deuda("500.00", emision=_dia(2))
        en_efectivo = self._deuda("200.00", proveedor=self.verduleria)
        # Un egreso de tesoreria imputado a Limpieza, sin categoria.
        debito = self._debito(
            "1500.00", fecha=_dia(20), clase=OTRO_EGRESO, origen=EGRESO_TESORERIA, rubro=self.limpieza
        )
        antes = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        banco = self._banco()
        boveda = self._boveda(self.armadi)
        self._aplicar()
        debito.refresh_from_db()
        self.assertEqual(debito.origen, PAGO_TESORERIA)
        self.assertEqual(debito.clase, TRANSFERENCIA_TERCEROS)
        self.assertEqual(debito.rubro_operativo_id, self.rubro.pk)
        self.assertEqual(debito.categoria_id, self.categoria.pk)

        simulacion = Reversion(actor=self.admin, lote=LOTE)
        simulacion.planificar()
        atadas = {f.deuda_id: f for f in simulacion.filas if f.debito_id}
        self.assertEqual(set(atadas), {x.pk, y.pk})
        self.assertEqual({f.accion for f in simulacion.filas}, {Accion.A_REVERTIR})
        self.assertEqual(debito.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 2)

        reversion = Reversion(actor=self.admin, lote=LOTE, motivo="Ensayo")
        reversion.planificar()
        reversion.aplicar()

        for deuda in (x, y, en_efectivo):
            deuda.refresh_from_db()
            self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
            self.assertEqual(deuda.pagos.get().estado, PagoTesoreria.Estado.ANULADO)
        debito.refresh_from_db()
        self.assertEqual(debito.estado, MovimientoBancario.Estado.REGISTRADO)
        self.assertEqual(debito.origen, EGRESO_TESORERIA)
        self.assertEqual(debito.clase, OTRO_EGRESO)
        self.assertEqual(debito.rubro_operativo_id, self.limpieza.pk)
        self.assertIsNone(debito.categoria_id)
        self.assertEqual(debito.proveedor_id, self.proveedor.pk)
        self.assertIn(f"Imputacion restaurada al revertir el lote {LOTE}", debito.observaciones)
        self.assertEqual(self._sin_asignar(debito), Decimal("1500.00"))
        self.assertEqual(reversion.debitos_restaurados, [debito.pk])
        self.assertEqual(self._banco(), banco)
        self.assertEqual(self._boveda(self.armadi), boveda)
        despues = build_economic_period_snapshot(**AGOSTO_COMPLETO)
        for clave in ("treasury_expense_total", "debt_period_total", "economic_result"):
            self.assertEqual(despues[clave], antes[clave], clave)

        otra = Reversion(actor=self.admin, lote=LOTE)
        otra.planificar()
        self.assertEqual({f.accion for f in otra.filas}, {Accion.YA_ANULADO})

    def test_revertir_un_debito_historico_sin_imputar_lo_deja_sin_imputar(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=_dia(20))
        # Junio: el debito se cargo suelto, sin rubro, sucursal ni periodo.
        MovimientoBancario.objects.filter(pk=debito.pk).update(
            rubro_operativo=None, sucursal_gasto=None, periodo_pago=None
        )
        self._aplicar()
        debito.refresh_from_db()
        self.assertEqual(debito.sucursal_gasto_id, self.ec1.pk)

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        debito.refresh_from_db()
        self.assertEqual(debito.origen, MANUAL)
        self.assertIsNone(debito.rubro_operativo_id)
        self.assertIsNone(debito.sucursal_gasto_id)
        self.assertIsNone(debito.periodo_pago)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_un_debito_con_pagos_de_antes_vuelve_a_quedar_como_estaba(self):
        limpieza = create_payable_category(nombre="Limpieza", rubro_operativo=self.limpieza, actor=self.admin)
        otra = self._deuda("400.00", categoria=limpieza, emision=_dia(1), concepto="Pagada a mano")
        deuda = self._deuda("600.00", emision=_dia(2))
        debito = self._debito("1000.00", fecha=_dia(20))
        previo = pay_debt_from_bank_movement(
            bank_movement=debito, payable=otra, monto=Decimal("400.00"), actor=self.admin
        )
        debito.refresh_from_db()
        categoria_antes = debito.categoria_id
        self.assertEqual(categoria_antes, limpieza.pk)
        self._aplicar()
        debito.refresh_from_db()
        self.assertEqual(debito.categoria_id, self.categoria.pk)

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        debito.refresh_from_db()
        self.assertEqual(debito.origen, PAGO_TESORERIA)
        self.assertEqual(debito.categoria_id, categoria_antes)
        self.assertEqual(self._sin_asignar(debito), Decimal("600.00"))
        previo.refresh_from_db()
        self.assertEqual(previo.estado, PagoTesoreria.Estado.REGISTRADO)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)

    def test_si_el_debito_tiene_pagos_nuevos_se_desata_pero_no_se_toca_la_imputacion(self):
        deuda = self._deuda("1000.00", emision=_dia(1))
        debito = self._debito("1500.00", fecha=_dia(20), clase=OTRO_EGRESO, rubro=self.limpieza)
        self._aplicar(atar_con_sobrante=True)
        nueva = self._deuda("500.00", proveedor=self.verduleria, emision=_dia(9))
        pay_debt_from_bank_movement(bank_movement=debito, payable=nueva, monto=Decimal("500.00"), actor=self.admin)

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        fila = next(f for f in reversion.filas if f.debito_id)
        self.assertEqual(fila.resultado, "Pago anulado")
        self.assertIn("antes del lote tenia 0", fila.aviso)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        debito.refresh_from_db()
        # Sigue pagando la factura nueva: queda atado, no vuelve a ser gasto.
        self.assertEqual(debito.origen, PAGO_TESORERIA)
        self.assertEqual(self._sin_asignar(debito), Decimal("1000.00"))
        self.assertEqual(reversion.debitos_restaurados, [])

    def test_no_toca_ataduras_hechas_a_mano_con_un_texto_parecido(self):
        deuda = self._deuda("1000.00")
        debito = self._debito("1000.00", fecha=_dia(20))
        pago = pay_debt_from_bank_movement(
            bank_movement=debito,
            payable=deuda,
            observaciones=f"Atado al debito #{debito.pk} del 20/08/2026 por la regularizacion {LOTE} (corte x)",
            actor=self.admin,
        )

        reversion = Reversion(actor=self.admin, lote=LOTE)
        reversion.planificar()
        reversion.aplicar()

        self.assertEqual([f.accion for f in reversion.filas], [Accion.NO_ES_DEL_LOTE])
        pago.refresh_from_db()
        self.assertEqual(pago.estado, PagoTesoreria.Estado.REGISTRADO)


class ConsolaAtarTests(AtarBancoBase):
    def _run(self, *args, **opciones):
        salida = StringIO()
        call_command("regularizar_deudas", *args, stdout=salida, **opciones)
        return salida.getvalue()

    def test_por_empresa_y_mes_atado_efectivo_y_caja_fuerte_proyectada(self):
        self._deuda("1000.00", emision=_dia(5))
        self._debito("1000.00", fecha=_dia(20))
        self._deuda("300.00", proveedor=self.verduleria, emision=_dia(6))
        self._deuda("200.00", proveedor=self.verduleria, emision=_dia(5, 9))
        self._deuda("4000.00", sucursal=self.vivre, emision=_dia(7))

        regularizacion = self._plan()
        por_mes = {(f["mes"], f["empresa"]): f for f in regularizacion.por_mes()}

        agosto_armadi = por_mes[(date(2026, 8, 1), "ARMADI SRL")]
        self.assertEqual(agosto_armadi["atado_banco"], Decimal("1000.00"))
        self.assertEqual(agosto_armadi["efectivo"], Decimal("300.00"))
        self.assertEqual(agosto_armadi["boveda_hoy"], Decimal("1000000.00"))
        self.assertEqual(agosto_armadi["boveda_proyectada"], Decimal("999700.00"))
        septiembre_armadi = por_mes[(date(2026, 9, 1), "ARMADI SRL")]
        self.assertEqual(septiembre_armadi["atado_banco"], Decimal("0.00"))
        self.assertEqual(septiembre_armadi["efectivo"], Decimal("200.00"))
        # El efectivo de agosto tambien baja la caja de septiembre.
        self.assertEqual(septiembre_armadi["boveda_proyectada"], Decimal("999500.00"))
        agosto_mapogo = por_mes[(date(2026, 8, 1), "MAPOGO SRL")]
        self.assertEqual(agosto_mapogo["atado_banco"], Decimal("0.00"))
        self.assertEqual(agosto_mapogo["boveda_proyectada"], Decimal("496000.00"))

        regularizacion.aplicar()
        reales = {(f["mes"], f["empresa"]): f for f in regularizacion.por_mes()}
        for clave, fila in reales.items():
            with self.subTest(clave=clave):
                self.assertEqual(fila["boveda_proyectada"], por_mes[clave]["boveda_proyectada"])
                self.assertEqual(fila["boveda_hoy"], fila["boveda_proyectada"])

    def test_simular_revisar_aplicar_y_revertir_por_consola(self):
        atada = self._deuda("1000.00", emision=_dia(5))
        debito = self._debito("1000.00", fecha=_dia(20))
        en_efectivo = self._deuda("300.00", proveedor=self.verduleria)
        with TemporaryDirectory() as carpeta:
            plan = Path(carpeta) / "plan.csv"
            salida = self._run(usuario=self.admin.username, hasta="2026-09-23", informe=str(plan))
            filas = {int(f["deuda_id"]): f for f in self._leer_csv(plan)}

            self.assertIn("A_ATAR_BANCO", salida)
            self.assertIn("Antes del efectivo se atan al banco", salida)
            self.assertIn("Por empresa y mes del pago", salida)
            self.assertIn("a atar al banco", salida)
            self.assertIn("999.700,00", salida)
            self.assertEqual(filas[atada.pk]["accion"], Accion.A_ATAR_BANCO)
            self.assertEqual(filas[atada.pk]["debito_id"], str(debito.pk))
            self.assertEqual(filas[atada.pk]["importe_debito"], "1.000,00")
            self.assertEqual(filas[atada.pk]["medio"], TRANSFERENCIA)
            self.assertEqual(filas[en_efectivo.pk]["accion"], Accion.A_REGULARIZAR)

            with self.assertRaisesMessage(CommandError, "A_ATAR_BANCO"):
                self._run(
                    usuario=self.admin.username, hasta="2026-09-23", plan=str(plan), sin_atar_banco=True, apply=True
                )
            self.assertEqual(PagoTesoreria.objects.count(), 0)

            salida = self._run(usuario=self.admin.username, hasta="2026-09-23", plan=str(plan), apply=True)

        self.assertIn("1 deudas aprobadas, 1 ataduras al banco", salida)
        self.assertIn("atado al banco", salida)
        atada.refresh_from_db()
        self.assertEqual(atada.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(atada.pagos.get().movimiento_bancario_id, debito.pk)
        self.assertEqual(self._boveda(self.armadi), Decimal("999700.00"))

        salida = self._run(usuario=self.admin.username, revertir=LOTE)
        self.assertIn("Ataduras al banco a anular: 1 pagos en 1 debitos", salida)
        salida = self._run(usuario=self.admin.username, revertir=LOTE, motivo="Ensayo", apply=True)
        self.assertIn("Ataduras al banco anuladas: 1 pagos en 1 debitos; 1 debitos volvieron", salida)
        atada.refresh_from_db()
        self.assertEqual(atada.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(self._sin_asignar(debito), Decimal("1000.00"))
        self.assertEqual(self._boveda(self.armadi), Decimal("1000000.00"))

    def test_consola_avisa_los_debitos_de_gasto_que_no_se_atan(self):
        self._deuda("600.00", emision=_dia(5))
        self._debito("1000.00", fecha=_dia(20), rubro=self.limpieza)
        sin_rubro = self._deuda("500.00", proveedor=self.verduleria, emision=_dia(6))
        legacy = CategoriaCuentaPagar.objects.create(nombre="Legacy sin rubro", activo=True)
        CuentaPorPagar.objects.filter(pk=sin_rubro.pk).update(categoria=legacy)
        self._debito("500.00", fecha=_dia(21), proveedor=self.verduleria)

        salida = self._run(usuario=self.admin.username, hasta="2026-09-23")

        self.assertIn(
            "1 debitos que cuentan como gasto (o estan pendientes de imputar) no se atan: les sobrarian 400,00",
            salida,
        )
        self.assertIn("Sus 1 facturas (600,00) quedan como POSIBLE_PAGO_EN_GERAYSE", salida)
        self.assertIn("1 facturas con categoria sin rubro no se atan a su debito de gasto", salida)
        self.assertIn("POSIBLE_PAGO_EN_GERAYSE          2           1.100,00", salida)

        salida = self._run(usuario=self.admin.username, hasta="2026-09-23", atar_con_sobrante=True)

        self.assertIn("Con --atar-con-sobrante: un debito que cuenta como gasto se ata", salida)
        self.assertIn("1 debitos atados quedan con importe sin asignar: 400,00", salida)
        self.assertIn("sus 400,00 sobrantes dejan de contar como gasto", salida)
        # La factura sin rubro tampoco se ata con la opcion.
        self.assertIn("1 facturas con categoria sin rubro no se atan", salida)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        for opciones in ({"hasta": "2026-09-23", "sin_atar_banco": True}, {"revertir": LOTE}):
            with self.assertRaisesMessage(CommandError, "--atar-con-sobrante"):
                self._run(usuario=self.admin.username, atar_con_sobrante=True, **opciones)

    def test_sin_atar_banco_por_consola_y_sus_incompatibles(self):
        deuda = self._deuda("1000.00")
        self._debito("1000.00", fecha=_dia(20))

        salida = self._run(usuario=self.admin.username, hasta="2026-09-23", sin_atar_banco=True)

        self.assertIn("Sin atar al banco (--sin-atar-banco)", salida)
        self.assertIn("POSIBLE_PAGO_EN_GERAYSE", salida)
        self.assertNotIn("A_ATAR_BANCO", salida)
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        panaderia = self._proveedor("OVEJA NEGRA ARMADI SRL")
        with self.assertRaisesMessage(CommandError, "--sin-atar-banco es solo del modo general"):
            self._run(usuario=self.admin.username, proveedor_interno=str(panaderia.pk), sin_atar_banco=True)
        with self.assertRaisesMessage(CommandError, "--sin-atar-banco"):
            self._run(usuario=self.admin.username, revertir=LOTE, sin_atar_banco=True)
