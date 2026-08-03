"""La migracion 0036 no puede perder ningun vinculo pago <-> extracto.

En produccion cada pago por transferencia, cheque o ECHEQ ya tiene su movimiento
bancario. La 0036 da vuelta la relacion (de `MovimientoBancario.pago_tesoreria`
OneToOne a `PagoTesoreria.movimiento_bancario` FK) y copia los vinculos con un
RunPython. Si ese backfill fallara, los pagos historicos quedarian sin reflejo
bancario y la conciliacion mostraria plata sin explicar.

Se corre la migracion de verdad: se vuelve a 0035, se crean filas vinculadas con
los modelos de ESE estado y se migra hacia adelante para ver que llegaron.
"""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

ANTES = [("treasury", "0035_movimientobancario_token_alta_and_more")]
DESPUES = [("treasury", "0036_pago_movimiento_bancario")]


class BackfillDelVinculoPagoTests(TransactionTestCase):
    # La migracion toca el esquema, asi que no puede correr dentro de la
    # transaccion de un TestCase comun.
    available_apps = None

    def tearDown(self):
        # Deja la base como la esperan los demas tests.
        MigrationExecutor(connection).migrate(DESPUES)
        super().tearDown()

    def _volver_a_0035(self):
        executor = MigrationExecutor(connection)
        executor.migrate(ANTES)
        executor.loader.build_graph()
        return executor.loader.project_state(ANTES).apps

    def _crear_escenario(self, apps):
        """Un pago vinculado a un debito, con el minimo de filas necesario."""
        CuentaBancaria = apps.get_model("treasury", "CuentaBancaria")
        Proveedor = apps.get_model("treasury", "Proveedor")
        CategoriaCuentaPagar = apps.get_model("treasury", "CategoriaCuentaPagar")
        CuentaPorPagar = apps.get_model("treasury", "CuentaPorPagar")
        PagoTesoreria = apps.get_model("treasury", "PagoTesoreria")
        MovimientoBancario = apps.get_model("treasury", "MovimientoBancario")

        cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta migracion",
            banco="Banco migracion",
            tipo_cuenta="CUENTA_CORRIENTE",
            numero_cuenta="777-1",
        )
        proveedor = Proveedor.objects.create(razon_social="Proveedor migracion")
        categoria = CategoriaCuentaPagar.objects.create(nombre="Categoria migracion")
        deuda = CuentaPorPagar.objects.create(
            proveedor=proveedor,
            categoria=categoria,
            concepto="Factura migracion",
            fecha_emision=date(2026, 7, 1),
            fecha_vencimiento=date(2026, 7, 20),
            periodo_referencia=date(2026, 7, 1),
            importe_total=Decimal("500.00"),
            saldo_pendiente=Decimal("0.00"),
        )
        pago = PagoTesoreria.objects.create(
            cuenta_por_pagar=deuda,
            cuenta_bancaria=cuenta,
            medio_pago="TRANSFERENCIA",
            fecha_pago=date(2026, 7, 15),
            monto=Decimal("500.00"),
        )
        movimiento = MovimientoBancario.objects.create(
            cuenta_bancaria=cuenta,
            tipo="DEBITO",
            clase="TRANSFERENCIA_TERCEROS",
            fecha=date(2026, 7, 15),
            monto=Decimal("500.00"),
            concepto="Transferencia migracion",
            origen="PAGO_TESORERIA",
            proveedor=proveedor,
            categoria=categoria,
            pago_tesoreria=pago,
        )
        suelto = MovimientoBancario.objects.create(
            cuenta_bancaria=cuenta,
            tipo="DEBITO",
            clase="OTRO_EGRESO",
            fecha=date(2026, 7, 16),
            monto=Decimal("100.00"),
            concepto="Debito sin pago",
            origen="MANUAL",
        )
        return {"pago": pago.pk, "movimiento": movimiento.pk, "suelto": suelto.pk}

    def test_el_vinculo_existente_queda_del_lado_del_pago(self):
        ids = self._crear_escenario(self._volver_a_0035())

        MigrationExecutor(connection).migrate(DESPUES)

        from treasury.models import MovimientoBancario, PagoTesoreria

        pago = PagoTesoreria.objects.get(pk=ids["pago"])
        self.assertEqual(pago.movimiento_bancario_id, ids["movimiento"])
        # Y se sigue llegando al pago desde el movimiento, ahora en plural.
        movimiento = MovimientoBancario.objects.get(pk=ids["movimiento"])
        self.assertEqual([p.pk for p in movimiento.pagos.all()], [ids["pago"]])

    def test_un_debito_sin_pago_no_se_inventa_un_vinculo(self):
        ids = self._crear_escenario(self._volver_a_0035())

        MigrationExecutor(connection).migrate(DESPUES)

        from treasury.models import MovimientoBancario

        suelto = MovimientoBancario.objects.get(pk=ids["suelto"])
        self.assertEqual(suelto.pagos.count(), 0)

    def test_la_vuelta_atras_devuelve_el_vinculo_al_movimiento(self):
        ids = self._crear_escenario(self._volver_a_0035())
        MigrationExecutor(connection).migrate(DESPUES)

        apps_viejas = self._volver_a_0035()

        MovimientoBancario = apps_viejas.get_model("treasury", "MovimientoBancario")
        movimiento = MovimientoBancario.objects.get(pk=ids["movimiento"])
        self.assertEqual(movimiento.pago_tesoreria_id, ids["pago"])
