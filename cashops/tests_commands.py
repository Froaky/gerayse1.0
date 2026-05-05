from datetime import date
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from users.models import Role

from cashops.models import AlertaOperativa, Caja, Empresa, LimiteRubroOperativo, MovimientoCaja, RubroOperativo, Sucursal, Turno
from cashops.services import close_box, open_box, register_expense


User = get_user_model()


class ResyncOperationalEngineCommandTests(TestCase):
    def setUp(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin", password="test", role=admin_role)
        self.operator = User.objects.create_user(username="operador", password="test", role=operator_role)

        self.empresa = Empresa.objects.create(nombre="Empresa 01 SRL")
        self.branch = Sucursal.objects.create(
            codigo="SUC-01",
            nombre="Sucursal 01",
            razon_social="Sucursal 01 SRL",
            empresa=self.empresa,
        )
        self.turno = Turno.objects.create(
            empresa=self.empresa,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.admin,
        )
        self.fecha_op = date(2026, 3, 27)
        self.box = open_box(
            user=self.operator,
            turno=self.turno,
            sucursal=self.branch,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.admin,
        )
        self.rubro = RubroOperativo.objects.create(nombre="Insumos")
        self.rubro_soporte = RubroOperativo.objects.create(nombre="Soporte")

    def _create_legacy_expense_without_category(self):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA ignore_check_constraints = 1;")
            cursor.execute(
                """
                INSERT INTO cashops_movimientocaja
                (
                    tipo,
                    sentido,
                    monto,
                    impacta_saldo_caja,
                    categoria,
                    observacion,
                    creado_en,
                    caja_id,
                    transferencia_id,
                    creado_por_id,
                    rubro_operativo_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    MovimientoCaja.Tipo.GASTO,
                    MovimientoCaja.Sentido.EGRESO,
                    Decimal("50.00"),
                    True,
                    "Legacy",
                    "Gasto antiguo",
                    timezone.now(),
                    self.box.pk,
                    None,
                    self.admin.pk,
                    None,
                ],
            )
            cursor.execute("PRAGMA ignore_check_constraints = 0;")

    def test_backfill_dry_run_does_not_persist_changes(self):
        self._create_legacy_expense_without_category()
        system_category = RubroOperativo.objects.get(nombre__iexact="Sin clasificar")
        out = StringIO()

        call_command(
            "resync_operational_engine",
            skip_resync=True,
            stdout=out,
        )

        self.assertContainsText(out.getvalue(), "Modo dry-run")
        self.assertContainsText(out.getvalue(), "rubro de sistema 'Sin clasificar'")
        self.assertTrue(system_category.es_sistema)
        self.assertFalse(system_category.activo)
        self.assertEqual(
            MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.GASTO, rubro_operativo__isnull=True).count(),
            1,
        )

    def test_backfill_apply_assigns_default_rubro_to_null_expenses(self):
        self._create_legacy_expense_without_category()
        gasto = MovimientoCaja.objects.filter(
            tipo=MovimientoCaja.Tipo.GASTO,
            rubro_operativo__isnull=True,
        ).latest("id")
        out = StringIO()

        call_command(
            "resync_operational_engine",
            apply=True,
            skip_resync=True,
            stdout=out,
        )

        gasto.refresh_from_db()
        self.assertEqual(gasto.rubro_operativo.nombre, "Sin clasificar")
        self.assertTrue(
            RubroOperativo.objects.filter(nombre__iexact="Sin clasificar", es_sistema=True, activo=False).exists()
        )
        self.assertEqual(
            MovimientoCaja.objects.filter(tipo=MovimientoCaja.Tipo.GASTO, rubro_operativo__isnull=True).count(),
            0,
        )

    def test_resync_dry_run_reports_alerts_without_creating_them(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro,
            sucursal=self.branch,
            porcentaje_maximo=Decimal("80.00"),
        )
        register_expense(
            caja=self.box,
            monto=Decimal("30.00"),
            rubro_operativo=self.rubro,
            categoria="Insumos",
            observacion="Carga operativa",
            creado_por=self.admin,
            actor=self.admin,
        )
        register_expense(
            caja=self.box,
            monto=Decimal("70.00"),
            rubro_operativo=self.rubro_soporte,
            categoria="Soporte",
            observacion="Base de comparacion",
            creado_por=self.admin,
            actor=self.admin,
        )
        LimiteRubroOperativo.objects.filter(rubro=self.rubro, sucursal=self.branch).update(porcentaje_maximo=Decimal("5.00"))
        out = StringIO()

        call_command(
            "resync_operational_engine",
            skip_backfill=True,
            scope="sucursal",
            sucursal=self.branch.pk,
            fecha_operativa="2026-03-27",
            stdout=out,
        )

        self.assertContainsText(out.getvalue(), "Modo dry-run")
        self.assertContainsText(out.getvalue(), "abrir=1")
        self.assertEqual(
            AlertaOperativa.objects.filter(tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO, resuelta=False).count(),
            0,
        )

    def test_resync_apply_creates_and_keeps_alerts_in_sync(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro,
            sucursal=self.branch,
            porcentaje_maximo=Decimal("80.00"),
        )
        register_expense(
            caja=self.box,
            monto=Decimal("30.00"),
            rubro_operativo=self.rubro,
            categoria="Insumos",
            observacion="Carga operativa",
            creado_por=self.admin,
            actor=self.admin,
        )
        register_expense(
            caja=self.box,
            monto=Decimal("70.00"),
            rubro_operativo=self.rubro_soporte,
            categoria="Soporte",
            observacion="Base de comparacion",
            creado_por=self.admin,
            actor=self.admin,
        )
        LimiteRubroOperativo.objects.filter(rubro=self.rubro, sucursal=self.branch).update(porcentaje_maximo=Decimal("5.00"))
        out = StringIO()

        call_command(
            "resync_operational_engine",
            apply=True,
            skip_backfill=True,
            scope="sucursal",
            sucursal=self.branch.pk,
            fecha_operativa="2026-03-27",
            stdout=out,
        )

        alert = AlertaOperativa.objects.get(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro,
            sucursal=self.branch,
            periodo_fecha=self.fecha_op,
            caja__isnull=True,
            resuelta=False,
        )
        self.assertFalse(alert.resuelta)
        self.assertContainsText(out.getvalue(), "abrir=1")
        self.assertContainsText(out.getvalue(), "reabrir=1")

    def assertContainsText(self, haystack: str, needle: str):
        self.assertIn(needle, haystack)


class SanitizeLegacyAlertsCommandTests(TestCase):
    def setUp(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin_sanitize", password="test", role=admin_role)
        self.operator = User.objects.create_user(username="operador_sanitize", password="test", role=operator_role)

        self.empresa = Empresa.objects.create(nombre="Empresa Sanitize SRL")
        self.branch = Sucursal.objects.create(
            codigo="SUC-02",
            nombre="Sucursal 02",
            razon_social="Sucursal 02 SRL",
            empresa=self.empresa,
        )
        self.turno = Turno.objects.create(
            empresa=self.empresa,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.admin,
        )
        self.fecha_op = date(2026, 3, 27)
        self.box = open_box(
            user=self.operator,
            turno=self.turno,
            sucursal=self.branch,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.admin,
        )
        self.rubro = RubroOperativo.objects.create(nombre="Auditoria")

    def test_sanitize_legacy_alerts_dry_run_reports_without_persisting(self):
        close_box(
            caja=self.box,
            saldo_fisico=Decimal("13050.00"),
            justificacion="Diferencia legacy",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        alert = AlertaOperativa.objects.get(tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE)
        AlertaOperativa.objects.filter(pk=alert.pk).update(
            periodo_fecha=None,
            turno=None,
            usuario=None,
        )
        out = StringIO()

        call_command("sanitize_legacy_alerts", stdout=out)

        alert.refresh_from_db()
        self.assertIsNone(alert.periodo_fecha)
        self.assertIsNone(alert.turno)
        self.assertIsNone(alert.usuario)
        self.assertContainsText(out.getvalue(), "Modo dry-run")
        self.assertContainsText(out.getvalue(), "Alertas legacy incompletas: 1")
        self.assertContainsText(out.getvalue(), "actualizables=1")

    def test_sanitize_legacy_alerts_apply_backfills_grave_alert_from_closing(self):
        close_box(
            caja=self.box,
            saldo_fisico=Decimal("13050.00"),
            justificacion="Diferencia legacy",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        alert = AlertaOperativa.objects.get(tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE)
        AlertaOperativa.objects.filter(pk=alert.pk).update(
            periodo_fecha=None,
            turno=None,
            usuario=None,
            sucursal=None,
        )
        out = StringIO()

        call_command("sanitize_legacy_alerts", apply=True, stdout=out)

        alert.refresh_from_db()
        self.assertEqual(alert.periodo_fecha.isoformat(), "2026-03-27")
        self.assertEqual(alert.turno_id, self.turno.id)
        self.assertEqual(alert.usuario_id, self.operator.id)
        self.assertEqual(alert.sucursal_id, self.branch.id)
        self.assertContainsText(out.getvalue(), "Modo apply")
        self.assertContainsText(out.getvalue(), "actualizadas=1")

    def test_sanitize_legacy_alerts_backfills_period_for_branch_expense_alert(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro,
            categoria="Compra legacy",
            observacion="Genera alerta de rubro",
            creado_por=self.admin,
            actor=self.admin,
        )
        alert = AlertaOperativa.objects.get(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            sucursal=self.branch,
            caja__isnull=True,
        )
        AlertaOperativa.objects.filter(pk=alert.pk).update(periodo_fecha=None)
        out = StringIO()

        call_command("sanitize_legacy_alerts", apply=True, stdout=out)

        alert.refresh_from_db()
        self.assertEqual(alert.periodo_fecha.isoformat(), "2026-03-27")
        self.assertIsNone(alert.turno)
        self.assertIsNone(alert.usuario)
        self.assertContainsText(out.getvalue(), "actualizadas=1")

    def test_sanitize_legacy_alerts_backfills_box_scope_alert_from_dedupe_key(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro,
            categoria="Compra legacy caja",
            observacion="Genera alerta de caja",
            creado_por=self.admin,
            actor=self.admin,
        )
        alert = AlertaOperativa.objects.get(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro,
            caja=self.box,
        )
        AlertaOperativa.objects.filter(pk=alert.pk).update(
            caja=None,
            sucursal=None,
            turno=None,
            usuario=None,
            periodo_fecha=None,
        )
        out = StringIO()

        call_command("sanitize_legacy_alerts", apply=True, stdout=out)

        alert.refresh_from_db()
        self.assertEqual(alert.caja_id, self.box.id)
        self.assertEqual(alert.sucursal_id, self.branch.id)
        self.assertEqual(alert.turno_id, self.turno.id)
        self.assertEqual(alert.usuario_id, self.operator.id)
        self.assertEqual(alert.periodo_fecha.isoformat(), "2026-03-27")
        self.assertContainsText(out.getvalue(), "actualizadas=1")

    def test_sanitize_legacy_alerts_reports_conflict_without_overwriting(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro,
            categoria="Compra conflictiva",
            observacion="Genera alerta conflictiva",
            creado_por=self.admin,
            actor=self.admin,
        )
        alert = AlertaOperativa.objects.get(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro,
            caja=self.box,
        )
        AlertaOperativa.objects.filter(pk=alert.pk).update(
            turno=None,
            usuario=None,
            periodo_fecha=None,
            dedupe_key=f"RUBRO_EXCEDIDO:caja-{self.box.id}:2026-03-29:{self.rubro.id}",
        )
        out = StringIO()

        call_command("sanitize_legacy_alerts", apply=True, stdout=out)

        alert.refresh_from_db()
        self.assertIsNone(alert.turno_id)
        self.assertIsNone(alert.usuario_id)
        self.assertIsNone(alert.periodo_fecha)
        self.assertContainsText(out.getvalue(), "conflictos=1")
        self.assertContainsText(out.getvalue(), "conflicto_periodo_fecha")

    def test_sanitize_legacy_alerts_keeps_unreconstructable_alert_pending_review(self):
        alert = AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro,
            sucursal=self.branch,
            mensaje="Alerta legacy sin fuente canonica",
            periodo_fecha=None,
            dedupe_key=None,
            resuelta=False,
        )
        out = StringIO()

        call_command("sanitize_legacy_alerts", apply=True, stdout=out)

        alert.refresh_from_db()
        self.assertIsNone(alert.periodo_fecha)
        self.assertContainsText(out.getvalue(), "pendientes_revision=1")
        self.assertContainsText(out.getvalue(), "sin_fuente_periodo_fecha")

    def assertContainsText(self, haystack: str, needle: str):
        self.assertIn(needle, haystack)
