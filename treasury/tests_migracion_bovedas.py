"""Verifica la consolidacion de cajas de efectivo en una boveda por empresa.

Reproduce la forma real que tenia produccion antes de migrar: una caja global sin
empresa de la que salian todos los egresos, y varias cajas por sucursal creadas
solas por el cierre de caja, con toda la recaudacion adentro.

El caso que mas importa es el de los ingresos sin `caja_cierre`: ese campo se
agrego el 14/07/2026 sin backfill, asi que la mayor parte de la historia lo tiene
en NULL y su sucursal solo se podia deducir de la caja. Si la migracion no la
rescata antes de mover los movimientos, se pierde la contabilidad por local.
"""

from decimal import Decimal
from importlib import import_module

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cashops.models import Caja, Empresa, Sucursal, Turno
from treasury.models import CajaCentral, MovimientoCajaCentral
from treasury.services import scope_central_cash_movements

# El modulo arranca con un numero, asi que no se puede importar con `from ... import`.
consolidar = import_module(
    "treasury.migrations.0031_consolidar_bovedas_por_empresa"
).consolidar

User = get_user_model()


class ConsolidacionBovedasTests(TestCase):
    """Corre la funcion de la migracion sobre datos con la forma de produccion."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username="migracion", password="test", email="m@test.com"
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI SRL")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO SRL")
        self.term = Sucursal.objects.create(
            codigo="TERM-01", nombre="Cafeteria Terminal", razon_social="ARMADI", empresa=self.armadi
        )
        self.belg = Sucursal.objects.create(
            codigo="EB1-03", nombre="Estacion Belgrano 1", razon_social="ARMADI", empresa=self.armadi
        )
        self.viv = Sucursal.objects.create(
            codigo="VIV-01", nombre="Vivre", razon_social="MAPOGO", empresa=self.mapogo
        )
        self.turno = Turno.objects.create(
            empresa=self.armadi, tipo=Turno.Tipo.MANANA, creado_por=self.user
        )

        # La caja global: sin empresa y sin sucursal, con los egresos imputados.
        self.global_ = CajaCentral.objects.create(nombre="Efectivo Central")
        MovimientoCajaCentral.objects.create(
            caja_central=self.global_,
            fecha=timezone.datetime(2026, 5, 30).date(),
            tipo=MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO,
            monto=Decimal("16984545.00"),
            concepto="Carga inicial: SALDO AL CIERRE DEL 30.05",
            creado_por=self.user,
        )
        MovimientoCajaCentral.objects.create(
            caja_central=self.global_,
            fecha=timezone.datetime(2026, 6, 10).date(),
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("500000.00"),
            concepto="Gasto imputado a la terminal",
            sucursal_gasto=self.term,
            creado_por=self.user,
        )

        # Dos cajas por sucursal, como las que creaba el cierre de caja.
        self.caja_term = CajaCentral.objects.create(
            nombre="Caja Central Cafeteria Terminal", sucursal=self.term
        )
        self.caja_belg = CajaCentral.objects.create(
            nombre="Caja Central Estacion Belgrano 1", sucursal=self.belg
        )

        # Ingreso VIEJO: sin caja_cierre, igual que todo lo anterior al 14/07.
        # Su sucursal solo se puede deducir de la caja.
        self.viejo = MovimientoCajaCentral.objects.create(
            caja_central=self.caja_term,
            fecha=timezone.datetime(2026, 6, 15).date(),
            tipo=MovimientoCajaCentral.Tipo.INGRESO_CAJA,
            monto=Decimal("2000000.00"),
            concepto="Cierre caja #7",
            creado_por=self.user,
        )
        # Ingreso NUEVO: con caja_cierre, posterior a la migracion 0025.
        caja_op = Caja.objects.create(
            usuario=self.user,
            turno=self.turno,
            sucursal=self.belg,
            fecha_operativa=timezone.datetime(2026, 7, 20).date(),
            monto_inicial=Decimal("0.00"),
        )
        self.nuevo = MovimientoCajaCentral.objects.create(
            caja_central=self.caja_belg,
            fecha=timezone.datetime(2026, 7, 20).date(),
            tipo=MovimientoCajaCentral.Tipo.INGRESO_CAJA,
            monto=Decimal("800000.00"),
            concepto="Cierre caja #21",
            caja_cierre=caja_op,
            creado_por=self.user,
        )

    def _saldo_total(self):
        return sum(
            (caja.saldo_actual for caja in CajaCentral.objects.all()), Decimal("0.00")
        )

    def test_consolida_en_una_boveda_por_empresa_sin_perder_plata(self):
        saldo_antes = self._saldo_total()
        movimientos_antes = MovimientoCajaCentral.objects.count()

        consolidar(self._apps(), None)

        activas = CajaCentral.objects.filter(activo=True)
        self.assertEqual(activas.count(), 2)
        self.assertEqual(
            set(activas.values_list("empresa_id", flat=True)),
            {self.armadi.pk, self.mapogo.pk},
        )
        # Ninguna boveda activa cuelga de una sucursal.
        self.assertFalse(activas.filter(sucursal__isnull=False).exists())
        # Ni un movimiento se borro ni se perdio plata.
        self.assertEqual(MovimientoCajaCentral.objects.count(), movimientos_antes)
        self.assertEqual(self._saldo_total(), saldo_antes)
        # Las cajas de sucursal quedan como historia, vacias e inactivas.
        for caja in CajaCentral.objects.filter(sucursal__isnull=False):
            self.assertFalse(caja.activo)
            self.assertTrue(caja.nombre.startswith("[Consolidada] "))
            self.assertEqual(caja.movimientos.count(), 0)

    def test_rescata_la_sucursal_de_los_ingresos_sin_caja_cierre(self):
        consolidar(self._apps(), None)

        self.viejo.refresh_from_db()
        self.nuevo.refresh_from_db()
        # El viejo no tenia caja_cierre: la sucursal se rescato de la caja.
        self.assertEqual(self.viejo.sucursal_origen_id, self.term.pk)
        # El nuevo la toma de su caja de cierre.
        self.assertEqual(self.nuevo.sucursal_origen_id, self.belg.pk)

    def test_el_filtro_por_sucursal_sigue_viendo_los_ingresos_despues_de_consolidar(self):
        consolidar(self._apps(), None)

        de_la_terminal = scope_central_cash_movements(
            MovimientoCajaCentral.objects.all(), sucursal=self.term
        )
        conceptos = set(de_la_terminal.values_list("concepto", flat=True))
        # El ingreso de la terminal y el gasto imputado a la terminal.
        self.assertIn("Cierre caja #7", conceptos)
        self.assertIn("Gasto imputado a la terminal", conceptos)
        # El ingreso de Belgrano no es de la terminal.
        self.assertNotIn("Cierre caja #21", conceptos)

    def test_la_suma_por_empresa_da_exactamente_el_consolidado(self):
        consolidar(self._apps(), None)

        def entradas(empresa_ids=None):
            qs = scope_central_cash_movements(
                MovimientoCajaCentral.objects.all(), empresa_ids=empresa_ids
            )
            return sum((m.monto for m in qs), Decimal("0.00"))

        total = entradas()
        por_empresa = entradas([self.armadi.pk]) + entradas([self.mapogo.pk])
        # Ni de mas (el doble conteo que se midio en produccion) ni de menos.
        self.assertEqual(por_empresa, total)

    def test_correrla_dos_veces_no_cambia_nada(self):
        consolidar(self._apps(), None)
        saldo = self._saldo_total()
        cajas = CajaCentral.objects.count()
        movimientos = MovimientoCajaCentral.objects.count()

        consolidar(self._apps(), None)

        self.assertEqual(self._saldo_total(), saldo)
        self.assertEqual(CajaCentral.objects.count(), cajas)
        self.assertEqual(MovimientoCajaCentral.objects.count(), movimientos)
        self.assertEqual(CajaCentral.objects.filter(activo=True).count(), 2)

    def _apps(self):
        """La migracion usa apps.get_model; el registro real sirve igual."""
        from django.apps import apps

        return apps
