from datetime import date
from decimal import Decimal

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class Cashops0006MigrationSafetyTests(TransactionTestCase):
    reset_sequences = True

    migrate_from = [("cashops", "0005_rubrooperativo_es_sistema_and_more")]
    migrate_to = [("cashops", "0006_movimientocaja_impacta_saldo_caja_and_more")]

    def test_0006_applies_with_existing_legacy_card_sales(self):
        executor = MigrationExecutor(connection)

        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Role = old_apps.get_model("users", "Role")
        User = old_apps.get_model("users", "User")
        Sucursal = old_apps.get_model("cashops", "Sucursal")
        Turno = old_apps.get_model("cashops", "Turno")
        Caja = old_apps.get_model("cashops", "Caja")
        MovimientoCaja = old_apps.get_model("cashops", "MovimientoCaja")

        role = Role.objects.create(code="ADMIN", name="Administrador")
        user = User.objects.create(
            username="legacy-admin",
            password="test",
            role=role,
            is_active=True,
            is_staff=True,
        )
        sucursal = Sucursal.objects.create(codigo="LEG-01", nombre="Legacy 01")
        turno = Turno.objects.create(
            sucursal=sucursal,
            fecha_operativa=date(2026, 3, 27),
            tipo="TM",
            estado="ABIERTO",
            creado_por=user,
        )
        caja = Caja.objects.create(
            sucursal=sucursal,
            turno=turno,
            usuario=user,
            monto_inicial=Decimal("1000.00"),
            estado="ABIERTA",
        )
        MovimientoCaja.objects.create(
            caja=caja,
            tipo="VENTA_TARJETA",
            sentido="INGRESO",
            monto=Decimal("125.00"),
            categoria="POS legado",
            observacion="Venta por tarjeta previa a 0006",
            creado_por=user,
        )

        executor = MigrationExecutor(connection)
        call_command("migrate", "cashops", "0006_movimientocaja_impacta_saldo_caja_and_more", verbosity=0)

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tipo, monto, impacta_saldo_caja
                FROM cashops_movimientocaja
                WHERE caja_id = %s AND tipo = %s
                """,
                [caja.pk, "VENTA_TARJETA"],
            )
            tipo, monto, impacta_saldo_caja = cursor.fetchone()

        self.assertEqual(tipo, "VENTA_TARJETA")
        self.assertEqual(Decimal(str(monto)), Decimal("125.00"))
        self.assertTrue(bool(impacta_saldo_caja))
