from decimal import Decimal
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from users.models import PermissionModule, Role, UserPermission

from .forms import CajaAperturaForm, VentaGeneralForm
from .models import (
    AlertaOperativa,
    Caja,
    CajaCorreccion,
    CajaValidacion,
    CanalIngreso,
    CierreCaja,
    Empresa,
    LimiteRubroOperativo,
    MovimientoCaja,
    MovimientoCajaCorreccion,
    RubroOperativo,
    Sucursal,
    Transferencia,
    Turno,
)
from .permissions import (
    can_operate_box,
    can_validate_cash,
    ensure_can_operate_box,
    ensure_cash_validation,
    is_cashops_admin,
)
from .services import (
    BRANCH_TRANSFER_DISABLED_REASON,
    annul_box,
    annul_closed_box_movement,
    build_alert_panel_queryset,
    build_box_control_scope,
    build_branch_control_scope,
    build_operational_category_overview,
    build_global_control_scope,
    build_management_daily_matrix,
    build_operational_control_snapshot,
    build_operational_period_summary,
    close_box,
    get_uncategorized_operational_category,
    open_box,
    register_cash_income,
    register_card_sale,
    register_box_expense_debt,
    register_expense,
    reject_box_cash,
    transfer_between_boxes,
    transfer_between_branches,
    register_general_sale,
    update_closed_box_movement,
    validate_box_cash,
)


User = get_user_model()


class CashopsTestCase(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")

        self.admin = User.objects.create_user(username="admin", password="test", role=self.admin_role)
        self.operator = User.objects.create_user(username="operador", password="test", role=self.operator_role)
        self.operator_2 = User.objects.create_user(username="operador2", password="test", role=self.operator_role)
        self.other = User.objects.create_user(username="ajeno", password="test", role=self.operator_role)

        self.empresa_a = Empresa.objects.create(nombre="ARMADI SRL")
        self.empresa_b = Empresa.objects.create(nombre="MAPOGO SRL")
        self.branch_a = Sucursal.objects.create(codigo="SUC-A", nombre="Sucursal A", razon_social="ARMADI SRL", empresa=self.empresa_a)
        self.branch_b = Sucursal.objects.create(codigo="SUC-B", nombre="Sucursal B", razon_social="MAPOGO SRL", empresa=self.empresa_b)
        self.admin.empresas_permitidas.set([self.empresa_a, self.empresa_b])
        self.operator.empresas_permitidas.set([self.empresa_a])
        self.operator_2.empresas_permitidas.set([self.empresa_b])
        self.other.empresas_permitidas.set([self.empresa_b])
        self.rubro_insumos = RubroOperativo.objects.create(nombre="Insumos")
        self.rubro_viaticos = RubroOperativo.objects.create(nombre="Viaticos")

        self.turno_a = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.operator,
        )
        self.turno_b = Turno.objects.create(
            empresa=self.empresa_b,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.operator_2,
        )
        self.fecha_op = date(2026, 3, 27)

    def _open_form(self, actor, data):
        form = CajaAperturaForm(data=data, actor=actor, empresa=self.empresa_a)
        form.fields["usuario"].queryset = User.objects.all()
        form.fields["sucursal"].queryset = Sucursal.objects.all()
        form.fields["turno"].queryset = Turno.objects.all()
        return form

    def _grant_closed_box_fix(self, user):
        return UserPermission.objects.create(
            user=user,
            module=PermissionModule.CASHOPS_CLOSED_FIX,
            can_read=True,
            can_write=True,
        )


class CashopsPermissionUnitTests(CashopsTestCase):
    def test_cashops_admin_helper_respects_role_and_superuser(self):
        superuser = User.objects.create_superuser(username="root", password="test", email="root@example.com")

        self.assertTrue(is_cashops_admin(self.admin))
        self.assertTrue(is_cashops_admin(superuser))
        self.assertFalse(is_cashops_admin(self.operator))

    def test_box_permission_helper_allows_owner_and_admin(self):
        caja = open_box(user=self.operator, turno=self.turno_a, sucursal=self.branch_a, fecha_operativa=self.fecha_op, monto_inicial=Decimal("100.00"), actor=self.operator)

        self.assertTrue(can_operate_box(self.operator, caja))
        self.assertTrue(can_operate_box(self.admin, caja))
        self.assertFalse(can_operate_box(self.other, caja))

        ensure_can_operate_box(self.operator, caja)
        ensure_can_operate_box(self.admin, caja)

        with self.assertRaises(PermissionDenied):
            ensure_can_operate_box(self.other, caja)

    def test_open_form_rejects_other_user_for_non_admin(self):
        form = self._open_form(
            actor=self.operator,
            data={
                "usuario": self.other.pk,
                "sucursal": self.branch_a.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": "2026-03-27",
                "efectivo_inicial": "100.00",
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn("usuario", form.errors)

    def test_open_form_allows_admin_assignment(self):
        form = self._open_form(
            actor=self.admin,
            data={
                "usuario": self.other.pk,
                "sucursal": self.branch_a.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": "2026-03-27",
                "efectivo_inicial": "100.00",
            },
        )

        self.assertTrue(form.is_valid())

    def test_open_form_prefills_fixed_user_branch(self):
        fixed_user = User.objects.create_user(
            username="fijo",
            password="test",
            role=self.operator_role,
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )

        form = CajaAperturaForm(actor=fixed_user)

        self.assertEqual(form.fields["usuario"].initial, fixed_user.pk)
        self.assertEqual(form.fields["sucursal"].initial, self.branch_a.pk)
        self.assertIn("turno", form.fields)
        self.assertQuerySetEqual(
            form.fields["sucursal"].queryset,
            Sucursal.objects.filter(pk=self.branch_a.pk, activa=True),
        )

    def test_sale_form_no_longer_exposes_product_field(self):
        form = VentaGeneralForm()

        self.assertNotIn("producto", form.fields)


class CashopsServiceTests(CashopsTestCase):
    def test_closed_box_movement_edit_requires_specific_permission(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        movimiento = register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("150.00"), cerrado_por=self.operator, actor=self.operator)

        with self.assertRaises(PermissionDenied):
            update_closed_box_movement(
                movement=movimiento,
                monto=Decimal("70.00"),
                categoria="Ingreso corregido",
                observacion="",
                motivo="Carga mal tipeada",
                actor=self.operator,
            )

    def test_closed_box_movement_edit_recalculates_closure_and_control(self):
        self._grant_closed_box_fix(self.operator)
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        movimiento = register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("150.00"), cerrado_por=self.operator, actor=self.operator)

        update_closed_box_movement(
            movement=movimiento,
            monto=Decimal("70.00"),
            categoria="Ingreso corregido",
            observacion="Correccion de importe",
            motivo="Carga mal tipeada",
            actor=self.operator,
        )

        movimiento.refresh_from_db()
        caja.refresh_from_db()
        cierre = CierreCaja.objects.get(caja=caja)
        snapshot = build_operational_control_snapshot(build_box_control_scope(caja=caja))
        self.assertEqual(movimiento.monto, Decimal("70.00"))
        self.assertEqual(caja.saldo_esperado, Decimal("170.00"))
        self.assertEqual(cierre.saldo_esperado, Decimal("170.00"))
        self.assertEqual(cierre.diferencia, Decimal("-20.00"))
        self.assertEqual(snapshot["total_ingresos"], Decimal("70.00"))
        self.assertEqual(MovimientoCajaCorreccion.objects.filter(movimiento=movimiento).count(), 1)

    def test_closed_box_movement_annul_recalculates_closure_and_excludes_totals(self):
        self._grant_closed_box_fix(self.operator)
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        movimiento = register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("150.00"), cerrado_por=self.operator, actor=self.operator)

        annul_closed_box_movement(
            movement=movimiento,
            motivo="Movimiento duplicado",
            actor=self.operator,
        )

        movimiento.refresh_from_db()
        caja.refresh_from_db()
        cierre = CierreCaja.objects.get(caja=caja)
        snapshot = build_operational_control_snapshot(build_box_control_scope(caja=caja))
        self.assertEqual(movimiento.estado, MovimientoCaja.Estado.ANULADO)
        self.assertEqual(caja.saldo_esperado, Decimal("100.00"))
        self.assertEqual(cierre.saldo_esperado, Decimal("100.00"))
        self.assertEqual(cierre.diferencia, Decimal("50.00"))
        self.assertEqual(snapshot["total_ingresos"], Decimal("0.00"))
        self.assertEqual(MovimientoCajaCorreccion.objects.get(movimiento=movimiento).accion, MovimientoCajaCorreccion.Accion.ANULACION)

    def test_admin_can_assign_box_to_another_user(self):
        caja = open_box(
            user=self.other,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("5000.00"),
            actor=self.admin,
        )

        self.assertEqual(caja.usuario, self.other)
        self.assertEqual(caja.movimientos.count(), 1)
        self.assertEqual(caja.movimientos.first().tipo, MovimientoCaja.Tipo.APERTURA)

    def test_regular_user_cannot_assign_box_to_another_user(self):
        with self.assertRaises(PermissionDenied):
            open_box(
                user=self.other,
                turno=self.turno_a,
                sucursal=self.branch_a,
                fecha_operativa=self.fecha_op,
                monto_inicial=Decimal("100.00"),
                actor=self.operator,
            )

    def test_fixed_user_cannot_open_box_outside_base_branch(self):
        fixed_user = User.objects.create_user(
            username="fijo-op",
            password="test",
            role=self.operator_role,
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )

        with self.assertRaises(ValidationError) as ctx:
            open_box(
                user=fixed_user,
                turno=self.turno_b,
                sucursal=self.branch_b,
                fecha_operativa=self.fecha_op,
                monto_inicial=Decimal("100.00"),
                actor=fixed_user,
            )

        self.assertIn("sucursal", ctx.exception.message_dict)
        self.assertIn("sucursal base", ctx.exception.message_dict["sucursal"][0])

    def test_fixed_user_can_open_box_in_base_branch(self):
        fixed_user = User.objects.create_user(
            username="fijo-ok",
            password="test",
            role=self.operator_role,
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )

        caja = open_box(
            user=fixed_user,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=fixed_user,
        )

        self.assertEqual(caja.usuario, fixed_user)
        self.assertEqual(caja.sucursal, self.branch_a)

    def test_open_box_with_zero_initial_amount_is_valid_and_creates_no_movement(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )

        self.assertEqual(caja.estado, Caja.Estado.ABIERTA)
        self.assertEqual(caja.saldo_esperado, Decimal("0.00"))
        self.assertEqual(caja.movimientos.count(), 0)

    def test_open_box_duplicate_is_rejected_with_validation_error(self):
        open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("250.00"),
            actor=self.operator,
        )

        with self.assertRaises(ValidationError):
            open_box(
                user=self.operator,
                turno=self.turno_a,
                sucursal=self.branch_a,
                fecha_operativa=self.fecha_op,
                monto_inicial=Decimal("50.00"),
                actor=self.operator,
            )

    def test_cash_income_registers_movement_and_updates_balance(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )

        movimiento = register_cash_income(
            caja=caja,
            monto=Decimal("250.00"),
            categoria="Cobro manual",
            observacion="Ingreso en efectivo",
            creado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(movimiento.tipo, MovimientoCaja.Tipo.INGRESO_EFECTIVO)
        self.assertEqual(movimiento.sentido, MovimientoCaja.Sentido.INGRESO)
        self.assertEqual(caja.saldo_esperado, Decimal("1250.00"))

    def test_card_sale_keeps_trace_but_does_not_update_cash_balance(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )

        movimiento = register_card_sale(
            caja=caja,
            monto=Decimal("300.00"),
            observacion="POS",
            creado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(movimiento.tipo, MovimientoCaja.Tipo.VENTA_TARJETA)
        self.assertFalse(movimiento.impacta_saldo_caja)
        self.assertEqual(caja.saldo_esperado, Decimal("1000.00"))

    def test_general_sale_uses_rubro_without_product(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )

        movimiento = register_general_sale(
            caja=caja,
            monto=Decimal("275.00"),
            tipo_venta=MovimientoCaja.Tipo.VENTA_QR,
            rubro=self.rubro_insumos,
            observacion="Ingreso por QR",
            creado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(movimiento.tipo, MovimientoCaja.Tipo.VENTA_QR)
        self.assertEqual(movimiento.rubro_operativo, self.rubro_insumos)
        self.assertEqual(movimiento.categoria, self.rubro_insumos.nombre)
        self.assertIsNone(movimiento.producto)

    def test_close_box_ignores_card_sale_in_expected_balance(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )

        register_card_sale(
            caja=caja,
            monto=Decimal("300.00"),
            observacion="POS",
            creado_por=self.operator,
            actor=self.operator,
        )

        cierre = close_box(
            caja=caja,
            saldo_fisico=Decimal("1000.00"),
            cerrado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(cierre.saldo_esperado, Decimal("1000.00"))
        self.assertEqual(cierre.diferencia, Decimal("0.00"))

    def test_regular_user_cannot_operate_foreign_box_in_service_layer(self):
        caja = open_box(
            user=self.other,
            turno=self.turno_b,
            sucursal=self.branch_b,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.admin,
        )

        with self.assertRaises(PermissionDenied):
            register_expense(
                caja=caja,
                monto=Decimal("10.00"),
                rubro_operativo=self.rubro_insumos,
                categoria="No autorizado",
                observacion="Caja ajena",
                creado_por=self.operator,
                actor=self.operator,
            )

    def test_register_expense_requires_operational_category(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("200.00"),
            actor=self.operator,
        )

        with self.assertRaises(ValidationError):
            register_expense(
                caja=caja,
                monto=Decimal("20.00"),
                rubro_operativo=None,
                categoria="Sin rubro",
                observacion="No deberia guardarse",
                creado_por=self.operator,
                actor=self.operator,
            )

    def test_database_rejects_direct_expense_without_operational_category(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("200.00"),
            actor=self.operator,
        )

        with self.assertRaises(IntegrityError):
            MovimientoCaja.objects.create(
                caja=caja,
                tipo=MovimientoCaja.Tipo.GASTO,
                sentido=MovimientoCaja.Sentido.EGRESO,
                monto=Decimal("20.00"),
                categoria="Carga directa",
                observacion="No deberia persistir",
                creado_por=self.operator,
            )

    def test_operational_limit_cannot_exceed_one_hundred_percent(self):
        limit = LimiteRubroOperativo(
            rubro=self.rubro_insumos,
            porcentaje_maximo=Decimal("150.00"),
        )

        with self.assertRaises(ValidationError):
            limit.full_clean()

    def test_service_uses_single_operational_base_definition_for_snapshot(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("70.00"),
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )
        register_cash_income(
            caja=caja,
            monto=Decimal("200.00"),
            categoria="Cobro extra",
            observacion="No integra la base del semaforo",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("60.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 1",
            observacion="Base operativa",
            creado_por=self.operator,
            actor=self.operator,
        )

        snapshot = build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=self.fecha_op, sucursal=self.branch_a)
        )

        self.assertEqual(snapshot["base_calculo_label"], "Egresos operativos del periodo")
        self.assertEqual(snapshot["base_calculo_total"], Decimal("60.00"))
        self.assertEqual(snapshot["total_ingresos"], Decimal("200.00"))

    def test_period_summary_aggregates_range_by_branch(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )
        register_cash_income(
            caja=caja,
            monto=Decimal("100.00"),
            categoria="Ingreso A",
            observacion="Dia 1",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("40.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra A",
            observacion="Dia 1",
            creado_por=self.operator,
            actor=self.operator,
        )
        turno_siguiente = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        caja_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_cash_income(
            caja=caja_siguiente,
            monto=Decimal("70.00"),
            categoria="Ingreso B",
            observacion="Dia 2",
            creado_por=self.admin,
            actor=self.admin,
        )
        register_expense(
            caja=caja_siguiente,
            monto=Decimal("10.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Compra B",
            observacion="Dia 2",
            creado_por=self.admin,
            actor=self.admin,
        )

        summary = build_operational_period_summary(
            date_from=date(2026, 3, 27),
            date_to=date(2026, 3, 28),
            sucursal=self.branch_a,
        )

        self.assertTrue(summary["is_period_summary"])
        self.assertEqual(summary["total_ingresos"], Decimal("170.00"))
        self.assertEqual(summary["total_egresos"], Decimal("50.00"))
        self.assertEqual(summary["saldo_neto"], Decimal("120.00"))
        self.assertEqual(summary["scope_label"], self.branch_a.nombre)
        self.assertEqual(summary["saldo_real_cajas_periodo"], Decimal("620.00"))
        self.assertEqual(summary["cajas_periodo_count"], 2)

    def test_excluded_income_channel_is_reported_separately_from_branch_totals(self):
        CanalIngreso.objects.update_or_create(
            codigo="PANIFICACION",
            defaults={
                "nombre": "PANIFICACION",
                "impacta_saldo_caja": False,
                "excluir_de_totales": True,
                "activo": True,
                "orden": 10,
            },
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_general_sale(
            caja=caja,
            monto=Decimal("100.00"),
            tipo_venta=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            rubro=self.rubro_insumos,
            observacion="Venta operativa",
            actor=self.operator,
        )
        register_general_sale(
            caja=caja,
            monto=Decimal("300.00"),
            tipo_venta="PANIFICACION",
            rubro=self.rubro_insumos,
            observacion="Facturacion panificacion",
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("40.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Viatico",
            observacion="Egreso operativo",
            actor=self.operator,
        )

        snapshot = build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=self.fecha_op, sucursal=self.branch_a)
        )
        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )

        for report in (snapshot, summary):
            self.assertEqual(report["total_ingresos"], Decimal("100.00"))
            self.assertEqual(report["total_ingresos_excluidos"], Decimal("300.00"))
            self.assertEqual(report["saldo_neto"], Decimal("60.00"))
            self.assertEqual(report["ventas_excluidas_por_canal"][0]["display_label"], "Ventas facturacion de PANIFICACION")
            self.assertEqual(report["ventas_excluidas_por_canal"][0]["total"], Decimal("300.00"))

    def test_period_summary_open_cash_balance_can_be_negative(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("90.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="Egreso sin fondos",
            actor=self.operator,
        )

        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )

        self.assertEqual(summary["saldo_neto"], Decimal("-90.00"))
        self.assertEqual(summary["saldo_real_cajas_periodo"], Decimal("-90.00"))
        self.assertEqual(summary["cajas_periodo_count"], 1)

    def test_period_summary_includes_negative_closed_box_balance(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("90.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="Egreso sin fondos",
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("-90.00"), cerrado_por=self.operator, actor=self.operator)
        validate_box_cash(caja=caja, actor=self.admin)

        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )

        self.assertEqual(summary["saldo_real_cajas_periodo"], Decimal("-90.00"))
        self.assertEqual(summary["cajas_periodo_count"], 1)

    def test_management_daily_matrix_aggregates_channels_rubros_and_days(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_general_sale(
            caja=caja,
            monto=Decimal("120.00"),
            tipo_venta=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            rubro=self.rubro_insumos,
            observacion="Venta efectivo",
            actor=self.operator,
        )
        register_card_sale(
            caja=caja,
            monto=Decimal("80.00"),
            observacion="Venta tarjeta",
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("50.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Viatico",
            observacion="Egreso del dia",
            actor=self.operator,
        )
        turno_next = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        caja_next = open_box(
            user=self.operator_2,
            turno=turno_next,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_general_sale(
            caja=caja_next,
            monto=Decimal("40.00"),
            tipo_venta=MovimientoCaja.Tipo.VENTA_TRANSFERENCIA,
            rubro=self.rubro_insumos,
            observacion="Venta transferencia",
            actor=self.admin,
        )

        matrix = build_management_daily_matrix(
            date_from=date(2026, 3, 27),
            date_to=date(2026, 3, 28),
            sucursal=self.branch_a,
        )

        first_day = matrix["days"][0]
        second_day = matrix["days"][1]
        self.assertEqual(first_day["total_income"], Decimal("200.00"))
        self.assertEqual(first_day["total_expense"], Decimal("50.00"))
        self.assertEqual(first_day["net_result"], Decimal("150.00"))
        self.assertEqual(second_day["total_income"], Decimal("40.00"))
        self.assertEqual(matrix["total_income"], Decimal("240.00"))
        self.assertEqual(matrix["total_expense"], Decimal("50.00"))
        self.assertEqual(len(list(matrix["detail_movements"])), 4)

    def test_management_daily_matrix_excludes_panificacion_from_income_totals(self):
        CanalIngreso.objects.update_or_create(
            codigo="PANIFICACION",
            defaults={
                "nombre": "PANIFICACION",
                "impacta_saldo_caja": False,
                "excluir_de_totales": True,
                "activo": True,
                "orden": 10,
            },
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_general_sale(
            caja=caja,
            monto=Decimal("100.00"),
            tipo_venta=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            rubro=self.rubro_insumos,
            observacion="Venta operativa",
            actor=self.operator,
        )
        register_general_sale(
            caja=caja,
            monto=Decimal("300.00"),
            tipo_venta="PANIFICACION",
            rubro=self.rubro_insumos,
            observacion="Facturacion panificacion",
            actor=self.operator,
        )

        matrix = build_management_daily_matrix(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )

        day = matrix["days"][0]
        self.assertEqual(day["income_by_channel"]["PANIFICACION"], Decimal("300.00"))
        self.assertEqual(day["total_income"], Decimal("100.00"))
        self.assertEqual(day["total_excluded_income"], Decimal("300.00"))
        self.assertEqual(matrix["total_income"], Decimal("100.00"))
        self.assertEqual(matrix["total_excluded_income"], Decimal("300.00"))
        self.assertEqual(matrix["excluded_channels"][0]["display_label"], "Ventas facturacion de PANIFICACION")
        self.assertEqual(matrix["excluded_channels"][0]["total"], Decimal("300.00"))

    def test_operational_overview_prefers_branch_limit_and_marks_exceeded_category(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            porcentaje_maximo=Decimal("70.00"),
        )
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("40.00"),
        )
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_viaticos,
            porcentaje_maximo=Decimal("70.00"),
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("60.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="Compra operativa",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("40.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Viaticos",
            observacion="Traslado",
            creado_por=self.operator,
            actor=self.operator,
        )

        overview = build_operational_category_overview(
            fecha_operativa=self.fecha_op,
            sucursal=self.branch_a,
        )
        insumos = next(item for item in overview["items"] if item["rubro"] == self.rubro_insumos)

        self.assertEqual(overview["total_operativo"], Decimal("100.00"))
        self.assertEqual(insumos["porcentaje_consumido"], Decimal("60.00"))
        self.assertEqual(insumos["porcentaje_maximo"], Decimal("40.00"))
        self.assertEqual(insumos["estado"], "ROJO")
        self.assertEqual(insumos["limit_scope_label"], self.branch_a.nombre)

    def test_register_expense_resyncs_global_branch_and_box_alerts(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            porcentaje_maximo=Decimal("40.00"),
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )

        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 1",
            observacion="Dispara todos los scopes",
            creado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                periodo_fecha=self.fecha_op,
                sucursal__isnull=True,
                caja__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                periodo_fecha=self.fecha_op,
                sucursal=self.branch_a,
                caja__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                periodo_fecha=self.fecha_op,
                caja=caja,
            ).count(),
            1,
        )

    def test_expense_alert_is_deduplicated_for_same_period_category_and_branch(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("40.00"),
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )

        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 1",
            observacion="Primera carga",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("25.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 2",
            observacion="Segunda carga",
            creado_por=self.operator,
            actor=self.operator,
        )

        alerts = AlertaOperativa.objects.filter(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro_insumos,
            sucursal=self.branch_a,
            periodo_fecha=self.fecha_op,
            caja__isnull=True,
        )

        self.assertEqual(alerts.count(), 1)
        self.assertFalse(alerts.first().resuelta)

    def test_expense_alert_resolves_when_mix_returns_within_limit(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("60.00"),
        )
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_viaticos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("90.00"),
        )
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )

        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 1",
            observacion="Insumos altos",
            creado_por=self.operator,
            actor=self.operator,
        )
        alert = AlertaOperativa.objects.get(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro_insumos,
            sucursal=self.branch_a,
            caja__isnull=True,
        )
        self.assertFalse(alert.resuelta)

        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Viaticos",
            observacion="Segundo rubro",
            creado_por=self.operator,
            actor=self.operator,
        )

        alert.refresh_from_db()
        self.assertTrue(alert.resuelta)

    def test_transfer_between_boxes_rejects_insufficient_funds(self):
        caja_origen = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError):
            transfer_between_boxes(
                caja_origen=caja_origen,
                caja_destino=caja_destino,
                monto=Decimal("150.00"),
                observacion="Sin fondos",
                creado_por=self.admin,
                actor=self.admin,
            )

    def test_transfer_between_boxes_rejects_different_branch(self):
        caja_origen = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("200.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_b,
            sucursal=self.branch_b,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("50.00"),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError) as ctx:
            transfer_between_boxes(
                caja_origen=caja_origen,
                caja_destino=caja_destino,
                monto=Decimal("50.00"),
                observacion="Arrastre invalido",
                creado_por=self.admin,
                actor=self.admin,
            )

        self.assertIn("caja_destino", ctx.exception.message_dict)
        self.assertIn("misma sucursal", ctx.exception.message_dict["caja_destino"][0])

    def test_transfer_between_boxes_allows_same_branch_across_turns_and_days(self):
        caja_origen = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )
        turno_siguiente = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )

        transferencia = transfer_between_boxes(
            caja_origen=caja_origen,
            caja_destino=caja_destino,
            monto=Decimal("150.00"),
            observacion="Arrastre de turno",
            creado_por=self.admin,
            actor=self.admin,
        )

        caja_origen.refresh_from_db()
        caja_destino.refresh_from_db()

        self.assertEqual(transferencia.sucursal_origen, self.branch_a)
        self.assertEqual(transferencia.sucursal_destino, self.branch_a)
        self.assertEqual(transferencia.caja_origen.fecha_operativa, date(2026, 3, 27))
        self.assertEqual(transferencia.caja_destino.fecha_operativa, date(2026, 3, 28))
        self.assertEqual(caja_origen.saldo_esperado, Decimal("350.00"))
        self.assertEqual(caja_destino.saldo_esperado, Decimal("150.00"))
        self.assertEqual(
            Transferencia.objects.filter(caja_origen=caja_origen, caja_destino=caja_destino).count(),
            1,
        )
        self.assertEqual(
            caja_origen.movimientos.filter(transferencia=transferencia, tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA).count(),
            1,
        )
        self.assertEqual(
            caja_destino.movimientos.filter(transferencia=transferencia, tipo=MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA).count(),
            1,
        )

    def test_transfer_between_branches_is_disabled(self):
        caja_origen = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("120.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_b,
            sucursal=self.branch_b,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("50.00"),
            actor=self.admin,
        )

        with self.assertRaises(ValidationError):
            transfer_between_branches(
                sucursal_origen=self.branch_a,
                sucursal_destino=self.branch_b,
                clase="DINERO",
                monto=Decimal("500.00"),
                observacion="Envio sin respaldo",
                caja_origen=caja_origen,
                caja_destino=caja_destino,
                creado_por=self.admin,
                actor=self.admin,
            )
        self.assertEqual(
            str(ValidationError({"__all__": BRANCH_TRANSFER_DISABLED_REASON}).message_dict["__all__"][0]),
            BRANCH_TRANSFER_DISABLED_REASON,
        )

    def test_close_box_still_works_with_income_and_small_difference(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )
        register_cash_income(
            caja=caja,
            monto=Decimal("200.00"),
            categoria="Ingreso manual",
            observacion="Caja chica",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("50.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Gasto",
            observacion="Compra menor",
            creado_por=self.operator,
            actor=self.operator,
        )

        cierre = close_box(caja=caja, saldo_fisico=Decimal("1148.00"), cerrado_por=self.operator, actor=self.operator)

        self.assertEqual(cierre.estado, CierreCaja.Estado.AUTO)
        self.assertEqual(caja.estado, Caja.Estado.CERRADA)
        self.assertEqual(caja.cierre.diferencia, Decimal("-2.00"))
        self.assertEqual(caja.cierre.ajuste_movimiento.tipo, MovimientoCaja.Tipo.AJUSTE_CIERRE)

    def test_close_box_large_difference_creates_alert_and_justification(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )

        cierre = close_box(
            caja=caja,
            saldo_fisico=Decimal("13050.00"),
            justificacion="Diferencia explicada",
            cerrado_por=self.operator,
            actor=self.operator,
        )

        self.assertEqual(cierre.estado, CierreCaja.Estado.JUSTIFICADO)
        self.assertEqual(AlertaOperativa.objects.count(), 1)
        self.assertTrue(hasattr(cierre, "justificacion"))

    def test_close_box_with_negative_physical_balance_reduces_central_cash(self):
        from treasury.models import CajaCentral, MovimientoCajaCentral

        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("90.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="Egreso sin fondos",
            actor=self.operator,
        )

        close_box(caja=caja, saldo_fisico=Decimal("-90.00"), cerrado_por=self.operator, actor=self.operator)
        validate_box_cash(caja=caja, actor=self.admin)

        central_box = CajaCentral.objects.get(sucursal=self.branch_a)
        central_movement = MovimientoCajaCentral.objects.get(caja_central=central_box)
        self.assertEqual(central_movement.tipo, MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO)
        self.assertEqual(central_movement.monto, Decimal("90.00"))
        self.assertEqual(central_box.saldo_actual, Decimal("-90.00"))


class CashopsViewTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.owned_box = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )
        self.foreign_box = open_box(
            user=self.other,
            turno=self.turno_b,
            sucursal=self.branch_b,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("800.00"),
            actor=self.admin,
        )

    def _period(self, raw_value):
        return raw_value.isoformat() if hasattr(raw_value, "isoformat") else str(raw_value)

    def test_regular_user_gets_403_for_foreign_box_expense(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_expense", args=[self.foreign_box.pk]))

        self.assertEqual(response.status_code, 403)

    def test_admin_can_access_foreign_box_expense(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_expense", args=[self.foreign_box.pk]))

        self.assertEqual(response.status_code, 200)

    def test_closed_box_detail_shows_correction_actions_only_with_permission(self):
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        detail_url = reverse("cashops:box_detail", args=[self.owned_box.pk])

        self.client.force_login(self.operator)
        response_without_permission = self.client.get(detail_url)
        self.assertNotContains(response_without_permission, reverse("cashops:closed_box_movement_edit", args=[movimiento.pk]))

        self._grant_closed_box_fix(self.operator)
        response_with_permission = self.client.get(detail_url)
        self.assertContains(response_with_permission, reverse("cashops:closed_box_movement_edit", args=[movimiento.pk]))
        self.assertContains(response_with_permission, reverse("cashops:closed_box_movement_delete", args=[movimiento.pk]))

    def test_closed_box_movement_edit_view_updates_movement(self):
        self._grant_closed_box_fix(self.operator)
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:closed_box_movement_edit", args=[movimiento.pk]),
            {
                "monto": "65.00",
                "categoria": "Ingreso corregido",
                "observacion": "Correccion",
                "motivo": "Importe cargado mal",
            },
        )

        self.assertEqual(response.status_code, 302)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.monto, Decimal("65.00"))
        self.assertEqual(CierreCaja.objects.get(caja=self.owned_box).saldo_esperado, Decimal("1065.00"))

    def test_closed_box_movement_delete_view_requires_specific_permission(self):
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:closed_box_movement_delete", args=[movimiento.pk]),
            {"motivo": "Duplicado"},
        )

        self.assertEqual(response.status_code, 403)

    def test_closed_box_movement_delete_confirmation_uses_plain_post(self):
        self._grant_closed_box_fix(self.operator)
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)
        next_url = f"{reverse('cashops:box_tracking')}?estado=cerradas"
        delete_url = f"{reverse('cashops:closed_box_movement_delete', args=[movimiento.pk])}?{urlencode({'next': next_url})}"

        response = self.client.get(delete_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "hx-post")
        self.assertContains(response, f'name="next" value="{next_url}"', html=False)

    def test_closed_box_movement_delete_view_annuls_movement(self):
        self._grant_closed_box_fix(self.operator)
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:closed_box_movement_delete", args=[movimiento.pk]),
            {"motivo": "Duplicado"},
        )

        self.assertEqual(response.status_code, 302)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.estado, MovimientoCaja.Estado.ANULADO)
        self.assertEqual(CierreCaja.objects.get(caja=self.owned_box).saldo_esperado, Decimal("1000.00"))

    def test_closed_box_movement_delete_view_redirects_htmx_to_box_detail(self):
        self._grant_closed_box_fix(self.operator)
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)
        next_url = f"{reverse('cashops:box_tracking')}?estado=cerradas"
        delete_url = f"{reverse('cashops:closed_box_movement_delete', args=[movimiento.pk])}?{urlencode({'next': next_url})}"

        response = self.client.post(
            delete_url,
            {"motivo": "Duplicado"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], next_url)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.estado, MovimientoCaja.Estado.ANULADO)
        redirected = self.client.get(next_url)
        self.assertContains(
            redirected,
            f"Caja #{self.owned_box.pk}: carga #{movimiento.pk} eliminada correctamente.",
            html=False,
        )

    @override_settings(ENABLE_DANGER_RESET=True)
    def test_reset_confirmation_lists_operational_and_financial_data_to_delete(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:reset_operational_data"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Se eliminaria lo siguiente")
        self.assertContains(response, "Todas las cajas y sus movimientos")
        self.assertContains(response, "Todos los movimientos bancarios")
        self.assertContains(response, "Todas las acreditaciones de tarjeta, descuentos y lotes POS")
        self.assertContains(response, "Todas las cuentas por pagar y pagos de tesoreria")
        self.assertContains(response, "Todos los cierres mensuales de tesoreria")

    @override_settings(ENABLE_DANGER_RESET=False)
    def test_reset_view_returns_404_when_disabled(self):
        self.client.force_login(self.admin)

        get_response = self.client.get(reverse("cashops:reset_operational_data"))
        post_response = self.client.post(
            reverse("cashops:reset_operational_data"), {"step": "2"}
        )

        self.assertEqual(get_response.status_code, 404)
        self.assertEqual(post_response.status_code, 404)

    @override_settings(ENABLE_DANGER_RESET=False)
    def test_empresa_list_hides_danger_zone_when_disabled(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:empresa_list"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["show_danger_zone"])
        self.assertNotContains(response, "Reiniciar datos de cajas")

    def test_regular_user_gets_403_for_foreign_box_close(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_close", args=[self.foreign_box.pk]))

        self.assertEqual(response.status_code, 403)

    def test_admin_dashboard_sees_foreign_box(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.operator.username)
        self.assertContains(response, self.other.username)
        self.assertContains(response, "Global")
        self.assertNotContains(response, "Caja activa")

    def test_tracking_view_lists_open_and_closed_boxes_for_admin(self):
        register_cash_income(
            caja=self.owned_box,
            monto=Decimal("125.00"),
            categoria="Mostrador",
            observacion="Cobro efectivo",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_general_sale(
            caja=self.owned_box,
            monto=Decimal("80.00"),
            tipo_venta=MovimientoCaja.Tipo.VENTA_QR,
            rubro=self.rubro_insumos,
            observacion="QR",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.foreign_box, saldo_fisico=Decimal("800.00"), cerrado_por=self.admin, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_tracking"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seguimiento de cajas")
        self.assertContains(response, f"Caja #{self.owned_box.pk}")
        self.assertContains(response, f"Caja #{self.foreign_box.pk}")
        self.assertContains(response, "Carga en curso")
        self.assertContains(response, "Cerrada")
        self.assertContains(response, "Efectivo")

    def test_tracking_view_status_filter_hides_closed_boxes(self):
        close_box(caja=self.foreign_box, saldo_fisico=Decimal("800.00"), cerrado_por=self.admin, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_tracking"), {"estado": "abiertas"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Caja #{self.owned_box.pk}")
        self.assertNotContains(response, f"Caja #{self.foreign_box.pk}")

    def test_regular_tracking_view_hides_foreign_boxes(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_tracking"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Caja #{self.owned_box.pk}")
        self.assertNotContains(response, f"Caja #{self.foreign_box.pk}")

    def test_tracking_view_shows_box_edit_delete_with_closed_fix_permission(self):
        self.client.force_login(self.operator)
        response_without_permission = self.client.get(reverse("cashops:box_tracking"))
        self.assertNotContains(response_without_permission, reverse("cashops:box_edit", args=[self.owned_box.pk]))
        self.assertNotContains(response_without_permission, reverse("cashops:box_delete", args=[self.owned_box.pk]))

        self._grant_closed_box_fix(self.operator)
        response_with_permission = self.client.get(reverse("cashops:box_tracking"))
        self.assertContains(response_with_permission, reverse("cashops:box_edit", args=[self.owned_box.pk]))
        self.assertContains(response_with_permission, reverse("cashops:box_delete", args=[self.owned_box.pk]))

    def test_box_edit_view_updates_whole_box_with_audit(self):
        self._grant_closed_box_fix(self.operator)
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1000.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)
        next_url = reverse("cashops:box_tracking")

        response = self.client.post(
            f"{reverse('cashops:box_edit', args=[self.owned_box.pk])}?{urlencode({'next': next_url})}",
            {
                "usuario": self.operator.pk,
                "sucursal": self.branch_a.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": "2026-03-28",
                "monto_inicial": "1200.00",
                "motivo": "Fecha e importe corregidos",
                "next": next_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], next_url)
        self.owned_box.refresh_from_db()
        self.assertEqual(self.owned_box.fecha_operativa, date(2026, 3, 28))
        self.assertEqual(self.owned_box.monto_inicial, Decimal("1200.00"))
        self.assertEqual(CierreCaja.objects.get(caja=self.owned_box).saldo_esperado, Decimal("1200.00"))
        correction = CajaCorreccion.objects.get(caja=self.owned_box)
        self.assertEqual(correction.accion, CajaCorreccion.Accion.EDICION)
        self.assertEqual(correction.motivo, "Fecha e importe corregidos")

    def test_box_delete_view_annuls_whole_box_and_hides_it_from_tracking(self):
        self._grant_closed_box_fix(self.operator)
        movimiento = register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Duplicada",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)
        next_url = reverse("cashops:box_tracking")

        response = self.client.post(
            f"{reverse('cashops:box_delete', args=[self.owned_box.pk])}?{urlencode({'next': next_url})}",
            {
                "motivo": "Caja duplicada",
                "next": next_url,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], next_url)
        self.owned_box.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(self.owned_box.estado, Caja.Estado.ANULADA)
        self.assertEqual(movimiento.estado, MovimientoCaja.Estado.ANULADO)
        correction = CajaCorreccion.objects.get(caja=self.owned_box)
        self.assertEqual(correction.accion, CajaCorreccion.Accion.ANULACION)
        self.assertEqual(correction.motivo, "Caja duplicada")
        tracking = self.client.get(next_url)
        self.assertEqual(len(tracking.context["rows"]), 0)
        self.assertContains(tracking, f"Caja #{self.owned_box.pk} eliminada correctamente.", html=False)

    def test_annul_closed_box_reverses_central_cash_closure_movement(self):
        from treasury.models import CajaCentral, MovimientoCajaCentral

        self._grant_closed_box_fix(self.operator)
        register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Ingreso a caja fuerte",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        validate_box_cash(caja=self.owned_box, actor=self.admin)
        caja_central = CajaCentral.objects.get(sucursal=self.branch_a)
        self.assertEqual(caja_central.saldo_actual, Decimal("1040.00"))

        annul_box(caja=self.owned_box, motivo="Caja duplicada", actor=self.operator)

        caja_central.refresh_from_db()
        reversal = MovimientoCajaCentral.objects.get(concepto=f"Anulacion cierre caja #{self.owned_box.pk}")
        self.assertEqual(reversal.tipo, MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO)
        self.assertEqual(reversal.monto, Decimal("1040.00"))
        self.assertEqual(caja_central.saldo_actual, Decimal("0.00"))

    def test_box_detail_shows_sales_breakdown_and_history(self):
        register_cash_income(
            caja=self.owned_box,
            monto=Decimal("125.00"),
            categoria="Mostrador",
            observacion="Cobro efectivo",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_general_sale(
            caja=self.owned_box,
            monto=Decimal("80.00"),
            tipo_venta=MovimientoCaja.Tipo.VENTA_QR,
            rubro=self.rubro_insumos,
            observacion="QR",
            creado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_detail", args=[self.owned_box.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Composicion de ventas e ingresos")
        self.assertContains(response, "Efectivo")
        self.assertContains(response, "Historial de actividad")
        self.assertContains(response, "Caja abierta")
        self.assertContains(response, "Retomar carga")

    def test_closed_box_detail_keeps_history_in_read_only_mode(self):
        register_cash_income(
            caja=self.owned_box,
            monto=Decimal("40.00"),
            categoria="Mostrador",
            observacion="Antes del cierre",
            creado_por=self.operator,
            actor=self.operator,
        )
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1040.00"), cerrado_por=self.operator, actor=self.operator)
        validate_box_cash(caja=self.owned_box, actor=self.admin)
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_detail", args=[self.owned_box.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Caja cerrada")
        self.assertContains(response, "consulta")
        self.assertContains(response, "Historial de actividad")

    def test_regular_user_gets_403_for_foreign_box_detail(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_detail", args=[self.foreign_box.pk]))

        self.assertEqual(response.status_code, 403)

    def test_regular_dashboard_hides_foreign_box(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:dashboard"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Caja activa")
        self.assertNotContains(response, self.other.username)

    def test_duplicate_open_box_returns_validation_feedback_without_500(self):
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:box_open"),
            {
                "usuario": self.operator.pk,
                "sucursal": self.branch_a.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": "2026-03-27",
                "efectivo_inicial": "10.00",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Ya existe una caja abierta", html=False, status_code=400)

    def test_closed_box_rejects_new_movements_without_500(self):
        close_box(caja=self.owned_box, saldo_fisico=Decimal("1000.00"), cerrado_por=self.operator, actor=self.operator)
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:box_expense", args=[self.owned_box.pk]),
            {
                "rubro_operativo": self.rubro_insumos.pk,
                "monto": "10.00",
                "categoria": "Gasto cerrado",
                "observacion": "No debe entrar",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "La caja esta cerrada.", html=False, status_code=400)

    def test_regular_open_box_view_only_lists_current_user(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_open"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.operator.username)
        self.assertNotContains(response, self.other.username)

    def test_admin_open_box_view_lists_other_users_for_assignment(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_open"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.other.username)

    def test_cash_income_view_registers_income_and_redirects(self):
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:box_income", args=[self.owned_box.pk]),
            {
                "monto": "250.00",
                "categoria": "Ingreso manual",
                "observacion": "Cobro en mostrador",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, f"{reverse('cashops:dashboard')}?scope=box&box={self.owned_box.pk}")
        self.owned_box.refresh_from_db()
        self.assertEqual(self.owned_box.saldo_esperado, Decimal("1250.00"))

    def test_sale_view_hides_product_field(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:register_sale", args=[self.owned_box.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar ingreso operativo")
        self.assertContains(response, "Registrar ingreso")
        self.assertNotContains(response, "Objeto / Producto")

    def test_transfer_between_boxes_without_funds_returns_error_message(self):
        turno_siguiente = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        second_box = open_box(
            user=self.operator,
            turno=turno_siguiente,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:transfer_boxes"),
            {
                "caja_origen": self.owned_box.pk,
                "caja_destino": second_box.pk,
                "monto": "1500.00",
                "observacion": "Sin respaldo",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "saldo disponible", html=False, status_code=400)

    def test_non_admin_cannot_access_admin_branch_transfer_view(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:transfer_branches"))

        self.assertEqual(response.status_code, 404)

    def test_admin_cannot_access_branch_transfer_view_when_disabled(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:transfer_branches"))

        self.assertEqual(response.status_code, 404)

    def test_expense_view_lists_only_active_operational_categories(self):
        RubroOperativo.objects.create(nombre="Mantenimiento", activo=False)
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_expense", args=[self.owned_box.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.rubro_insumos.nombre)
        self.assertNotContains(response, "Mantenimiento")

    def test_expense_view_uses_egreso_por_rubro_copy(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_expense", args=[self.owned_box.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Egreso por rubro")
        self.assertContains(response, "Guardar egreso")
        self.assertContains(response, "Observacion")
        self.assertNotContains(response, "Detalle corto")

    def test_expense_view_registers_without_short_detail_or_observation(self):
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:box_expense", args=[self.owned_box.pk]),
            {
                "rubro_operativo": self.rubro_insumos.pk,
                "monto": "90.00",
                "observacion": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        movement = MovimientoCaja.objects.get(caja=self.owned_box, monto=Decimal("90.00"))
        self.assertEqual(movement.rubro_operativo, self.rubro_insumos)
        self.assertEqual(movement.categoria, self.rubro_insumos.nombre)
        self.assertEqual(movement.observacion, "")

    def test_admin_can_manage_operational_category_list(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:operational_category_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.rubro_insumos.nombre)

    def test_admin_can_manage_branch_list_with_search_and_business_name(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:sucursal_list"), {"q": "SUC-A"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.branch_a.razon_social)
        self.assertNotContains(response, self.branch_b.nombre)

    def test_admin_can_filter_branch_list_by_business_name(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:sucursal_list"), {"q": "MAPOGO"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.branch_b.razon_social)
        self.assertNotContains(response, self.branch_a.nombre)

    def test_admin_can_create_branch_with_business_name(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("cashops:sucursal_create"),
            {
                "codigo": "EC2",
                "nombre": "Estacion Central 2",
                "razon_social": "ARMADI SRL",
                "activa": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Sucursal.objects.filter(codigo="EC2", razon_social="ARMADI SRL").exists())

    def test_admin_can_update_and_toggle_branch_status(self):
        self.client.force_login(self.admin)

        update_response = self.client.post(
            reverse("cashops:sucursal_update", args=[self.branch_a.pk]),
            {
                "codigo": "SUC-A",
                "nombre": "Sucursal A Renovada",
                "razon_social": "ARMADI OPERATIVA SRL",
            },
        )
        self.branch_a.refresh_from_db()

        self.assertEqual(update_response.status_code, 302)
        self.assertEqual(self.branch_a.nombre, "Sucursal A Renovada")
        self.assertEqual(self.branch_a.razon_social, "ARMADI OPERATIVA SRL")
        self.assertFalse(self.branch_a.activa)

        toggle_response = self.client.post(reverse("cashops:sucursal_toggle", args=[self.branch_a.pk]))
        self.branch_a.refresh_from_db()

        self.assertEqual(toggle_response.status_code, 302)
        self.assertTrue(self.branch_a.activa)

    def test_dashboard_branch_scope_supports_period_range(self):
        register_cash_income(
            caja=self.owned_box,
            monto=Decimal("120.00"),
            categoria="Ingreso A",
            observacion="Dia 1",
            creado_por=self.operator,
            actor=self.operator,
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("20.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra A",
            observacion="Dia 1",
            creado_por=self.operator,
            actor=self.operator,
        )
        turno_siguiente = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        box_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_cash_income(
            caja=box_siguiente,
            monto=Decimal("80.00"),
            categoria="Ingreso B",
            observacion="Dia 2",
            creado_por=self.admin,
            actor=self.admin,
        )
        register_expense(
            caja=box_siguiente,
            monto=Decimal("30.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Compra B",
            observacion="Dia 2",
            creado_por=self.admin,
            actor=self.admin,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:dashboard"),
            {
                "scope": "branch",
                "sucursal": self.branch_a.pk,
                "fecha_desde": "2026-03-27",
                "fecha_hasta": "2026-03-28",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "27/03/2026 a 28/03/2026")
        self.assertContains(response, "$200")
        self.assertContains(response, "$50")
        self.assertContains(response, "Saldo neto")
        self.assertContains(response, "$150")

    def test_dashboard_global_scope_shows_negative_open_cash_balance(self):
        empresa = Empresa.objects.create(nombre="NEGATIVA SRL")
        branch = Sucursal.objects.create(
            codigo="NEG",
            nombre="Sucursal Negativa",
            razon_social="NEGATIVA SRL",
            empresa=empresa,
        )
        turno = Turno.objects.create(
            empresa=empresa,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.admin,
        )
        caja = open_box(
            user=self.operator,
            turno=turno,
            sucursal=branch,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        register_expense(
            caja=caja,
            monto=Decimal("90.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="Egreso sin fondos",
            actor=self.operator,
        )
        session = self.client.session
        session["empresa_ids"] = [empresa.pk]
        session.save()
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:dashboard"),
            {
                "scope": "global",
                "fecha_desde": self.fecha_op.isoformat(),
                "fecha_hasta": self.fecha_op.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dashboard_snapshot"]["saldo_real_cajas_periodo"], Decimal("-90.00"))
        self.assertContains(response, "Resultado real de cajas")
        self.assertContains(response, "$-90,00")

    def test_dashboard_shows_panificacion_as_separate_excluded_income(self):
        CanalIngreso.objects.update_or_create(
            codigo="PANIFICACION",
            defaults={
                "nombre": "PANIFICACION",
                "impacta_saldo_caja": False,
                "excluir_de_totales": True,
                "activo": True,
                "orden": 10,
            },
        )
        register_general_sale(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            tipo_venta=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            rubro=self.rubro_insumos,
            observacion="Venta operativa",
            actor=self.operator,
        )
        register_general_sale(
            caja=self.owned_box,
            monto=Decimal("300.00"),
            tipo_venta="PANIFICACION",
            rubro=self.rubro_insumos,
            observacion="Facturacion panificacion",
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:dashboard"),
            {
                "scope": "branch",
                "sucursal": self.branch_a.pk,
                "fecha_desde": self.fecha_op.isoformat(),
                "fecha_hasta": self.fecha_op.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dashboard_snapshot"]["total_ingresos"], Decimal("100.00"))
        self.assertEqual(response.context["dashboard_snapshot"]["total_ingresos_excluidos"], Decimal("300.00"))
        self.assertContains(response, "Ventas facturacion de PANIFICACION")
        self.assertContains(response, "no suma en ingresos de sucursal")

    def test_management_matrix_view_and_export_are_admin_only_and_traceable(self):
        register_general_sale(
            caja=self.owned_box,
            monto=Decimal("90.00"),
            tipo_venta=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            rubro=self.rubro_insumos,
            observacion="Venta matriz",
            actor=self.operator,
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("35.00"),
            rubro_operativo=self.rubro_viaticos,
            categoria="Gasto matriz",
            observacion="Egreso matriz",
            actor=self.operator,
        )

        self.client.force_login(self.operator)
        forbidden = self.client.get(reverse("cashops:management_matrix"))
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("cashops:management_matrix"),
            {
                "fecha_desde": "2026-03-27",
                "fecha_hasta": "2026-03-27",
                "sucursal": self.branch_a.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matriz diaria de control")
        self.assertContains(response, "Efectivo")
        self.assertContains(response, self.rubro_viaticos.nombre)
        self.assertContains(response, "$90")
        self.assertContains(response, "$35")

        export = self.client.get(
            reverse("cashops:management_matrix_export"),
            {
                "fecha_desde": "2026-03-27",
                "fecha_hasta": "2026-03-27",
                "sucursal": self.branch_a.pk,
            },
        )

        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv")
        content = export.content.decode()
        self.assertIn("Detalle trazable", content)
        self.assertIn("Venta matriz", content)
        self.assertIn("Gasto matriz", content)

    def test_dashboard_does_not_auto_select_box_for_admin_scope(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No hay caja seleccionada", html=False)
        self.assertNotContains(response, f"Caja #{self.owned_box.id}</h2>", html=False)

    def test_dashboard_promotes_income_and_secondary_expense_access(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:dashboard") + f"?scope=box&box={self.owned_box.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar venta")
        self.assertContains(response, "Egreso operativo")
        self.assertContains(response, "Traspaso de fondos")

    def test_dashboard_box_scope_uses_explicit_scope_querystring(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra urgente",
            observacion="Se dispara el semaforo",
            creado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:dashboard") + f"?scope=box&box={self.owned_box.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Caja activa")
        self.assertContains(response, self.rubro_insumos.nombre)
        self.assertContains(response, "Excedido")

    def test_dashboard_shows_operational_semaphore_and_active_alert(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra urgente",
            observacion="Se dispara el semaforo",
            creado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:dashboard") + f"?scope=box&box={self.owned_box.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Semaforo operativo")
        self.assertContains(response, self.rubro_insumos.nombre)
        self.assertContains(response, "Excedido")
        self.assertContains(response, "supera su limite", html=False)

    def test_alert_panel_filters_active_alerts(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            sucursal=self.branch_a,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra urgente",
            observacion="Se dispara el semaforo",
            creado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "alcance": "caja",
                "periodo_desde": self._period(self.fecha_op),
                "periodo_hasta": self._period(self.fecha_op),
                "sucursal": self.branch_a.pk,
                "rubro": self.rubro_insumos.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Panel de alertas")
        self.assertContains(response, self.rubro_insumos.nombre)
        self.assertContains(response, "Activa")
        self.assertEqual(len(response.context["alertas"]), 1)

    def test_alert_panel_filters_by_periodo_operativo_real(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 27",
            observacion="Alerta del primer periodo",
            creado_por=self.operator,
            actor=self.operator,
        )
        turno_siguiente = Turno.objects.create(
            empresa=self.empresa_a,
            tipo=Turno.Tipo.TARDE,
            creado_por=self.admin,
        )
        box_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
            fecha_operativa=date(2026, 3, 28),
            monto_inicial=Decimal("500.00"),
            actor=self.admin,
        )
        register_expense(
            caja=box_siguiente,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra 28",
            observacion="Alerta del segundo periodo",
            creado_por=self.admin,
            actor=self.admin,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "alcance": "sucursal",
                "sucursal": self.branch_a.pk,
                "rubro": self.rubro_insumos.pk,
                "periodo_desde": self._period(self.fecha_op),
                "periodo_hasta": self._period(self.fecha_op),
            },
        )

        self.assertEqual(response.status_code, 200)
        alertas = list(response.context["alertas"])
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0].periodo_fecha.isoformat(), self._period(self.fecha_op))
        self.assertContains(response, self._period(self.fecha_op))
        self.assertNotContains(response, "2026-03-28")

    def test_alert_panel_shows_complete_context_for_grave_alert(self):
        close_box(
            caja=self.owned_box,
            saldo_fisico=Decimal("13050.00"),
            justificacion="Diferencia detectada",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        validate_box_cash(caja=self.owned_box, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "alcance": "caja",
                "sucursal": self.branch_a.pk,
                "periodo_desde": self._period(self.fecha_op),
                "periodo_hasta": self._period(self.fecha_op),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Diferencia grave")
        self.assertContains(response, "Turno")
        self.assertContains(response, self.turno_a.get_tipo_display())
        self.assertContains(response, "Usuario")
        self.assertContains(response, self.operator.username)
        self.assertContains(response, f"#{self.owned_box.pk}")
        self.assertContains(response, self.branch_a.nombre)

    def test_alert_panel_shows_scope_policy_for_equivalent_alerts(self):
        LimiteRubroOperativo.objects.create(
            rubro=self.rubro_insumos,
            porcentaje_maximo=Decimal("40.00"),
        )
        register_expense(
            caja=self.owned_box,
            monto=Decimal("100.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Compra urgente",
            observacion="Dispara todos los alcances",
            creado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "rubro": self.rubro_insumos.pk,
                "periodo_desde": self._period(self.fecha_op),
                "periodo_hasta": self._period(self.fecha_op),
            },
        )

        self.assertEqual(response.status_code, 200)
        rubro_alerts = list(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                rubro_operativo=self.rubro_insumos,
                periodo_fecha=self.fecha_op,
                resuelta=False,
            )
        )
        self.assertEqual(len(rubro_alerts), 3)
        self.assertEqual({alerta.alcance_tipo for alerta in rubro_alerts}, {"Caja", "Sucursal", "Global"})
        self.assertContains(response, "Politica de lectura por scope")
        self.assertContains(response, "Alcance Caja")
        self.assertContains(response, "Alcance Sucursal")
        self.assertContains(response, "Alcance Global")
        self.assertContains(response, "Las alertas equivalentes se muestran todas", html=False)

    def test_alert_panel_filters_by_operational_period_instead_of_created_date(self):
        alert_in_range = AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            sucursal=self.branch_a,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.fecha_op,
            mensaje="Periodo operativo correcto",
        )
        alert_outside_range = AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            sucursal=self.branch_a,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha="2026-03-26",
            mensaje="Periodo operativo fuera de rango",
        )
        AlertaOperativa.objects.filter(pk=alert_in_range.pk).update(
            creada_en=timezone.make_aware(datetime(2026, 3, 29, 10, 0, 0))
        )
        AlertaOperativa.objects.filter(pk=alert_outside_range.pk).update(
            creada_en=timezone.make_aware(datetime(2026, 3, 27, 10, 0, 0))
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "periodo_desde": "2026-03-27",
                "periodo_hasta": "2026-03-27",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Periodo operativo correcto")
        self.assertNotContains(response, "Periodo operativo fuera de rango")

    def test_alert_panel_shows_full_context_for_grave_alerts(self):
        close_box(
            caja=self.owned_box,
            saldo_fisico=Decimal("12050.00"),
            justificacion="Diferencia mayor detectada",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        validate_box_cash(caja=self.owned_box, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "periodo_desde": str(self.fecha_op),
                "periodo_hasta": str(self.fecha_op),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.branch_a.nombre)
        self.assertContains(response, f"#{self.owned_box.pk}")
        self.assertContains(response, self.turno_a.get_tipo_display())
        self.assertContains(response, str(self.fecha_op))
        self.assertContains(response, self.operator.username)
        self.assertContains(response, "Diferencia grave")

    def test_alert_panel_orders_equivalent_scope_alerts_from_box_to_global(self):
        AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            caja=self.owned_box,
            turno=self.turno_a,
            sucursal=self.branch_a,
            usuario=self.operator,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.fecha_op,
            mensaje="Alerta scope caja",
        )
        AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            sucursal=self.branch_a,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.fecha_op,
            mensaje="Alerta scope sucursal",
        )
        AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.fecha_op,
            mensaje="Alerta scope global",
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "periodo_desde": str(self.fecha_op),
                "periodo_hasta": str(self.fecha_op),
                "rubro": self.rubro_insumos.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("Alerta scope caja"), content.index("Alerta scope sucursal"))
        self.assertLess(content.index("Alerta scope sucursal"), content.index("Alerta scope global"))
        self.assertContains(response, "Las alertas equivalentes se muestran todas", html=False)


class EP12EmpresasTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.admin.empresas_permitidas.set([self.empresa_a, self.empresa_b])

    def test_empresa_list_requires_admin(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("cashops:empresa_list"))
        self.assertEqual(response.status_code, 403)

    def test_empresa_list_shows_all_empresas(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("cashops:empresa_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ARMADI SRL")
        self.assertContains(response, "MAPOGO SRL")

    def test_empresa_create_persists_record(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("cashops:empresa_create"),
            {"nombre": "NUEVA SRL", "activa": True},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Empresa.objects.filter(nombre="NUEVA SRL").exists())

    def test_set_empresa_activa_stores_in_session(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("cashops:set_empresa_activa"),
            {"empresa_id": self.empresa_a.pk, "next": reverse("cashops:dashboard")},
        )
        self.assertEqual(self.client.session.get("empresa_ids"), [self.empresa_a.pk])

    def test_set_empresa_activa_ignores_unassigned_company(self):
        self.admin.empresas_permitidas.clear()
        self.client.force_login(self.admin)
        self.client.post(
            reverse("cashops:set_empresa_activa"),
            {"empresa_id": self.empresa_a.pk, "next": reverse("cashops:dashboard")},
        )
        self.assertEqual(self.client.session.get("empresa_ids"), [])

    def test_dashboard_filters_sucursales_by_empresa_activa(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["empresa_activa_id"] = self.empresa_a.pk
        session.save()
        response = self.client.get(reverse("cashops:dashboard"))
        self.assertEqual(response.status_code, 200)
        sucursales = response.context["sucursales"]
        self.assertIn(self.branch_a, sucursales)
        self.assertNotIn(self.branch_b, sucursales)

    def test_backfill_migration_creates_empresas_from_razon_social(self):
        sucursal = Sucursal.objects.create(
            codigo="SUC-X", nombre="Sucursal X", razon_social="NUEVA CORP SA"
        )
        self.assertIsNone(sucursal.empresa)
        empresa = Empresa.objects.create(nombre="NUEVA CORP SA")
        sucursal.empresa = empresa
        sucursal.save(update_fields=["empresa"])
        sucursal.refresh_from_db()
        self.assertEqual(sucursal.empresa.nombre, "NUEVA CORP SA")


class EP12DashboardCanalTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.caja = open_box(
            user=self.operator,
            sucursal=self.branch_a,
            turno=self.turno_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )

    def test_snapshot_discriminates_efectivo_from_canal_sales(self):
        register_card_sale(
            caja=self.caja,
            monto=Decimal("200.00"),
            actor=self.operator,
        )
        register_cash_income(
            caja=self.caja,
            monto=Decimal("100.00"),
            categoria="Cobro mostrador",
            observacion="cobro efectivo",
            actor=self.operator,
        )
        scope = build_box_control_scope(caja=self.caja)
        snapshot = build_operational_control_snapshot(scope)
        self.assertEqual(snapshot["total_ventas_digitales"], Decimal("200.00"))
        self.assertEqual(snapshot["ingreso_efectivo_total"], Decimal("100.00"))
        self.assertIsNotNone(snapshot["saldo_efectivo_caja"])
        self.assertEqual(snapshot["saldo_efectivo_caja"], self.caja.saldo_esperado)
        self.assertEqual(len(snapshot["ventas_por_canal"]), 1)
        self.assertEqual(snapshot["ventas_por_canal"][0]["tipo"], MovimientoCaja.Tipo.VENTA_TARJETA)

    def test_period_summary_includes_canal_breakdown(self):
        register_card_sale(
            caja=self.caja,
            monto=Decimal("150.00"),
            actor=self.operator,
        )
        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
        )
        self.assertEqual(summary["total_ventas_digitales"], Decimal("150.00"))
        self.assertIsNone(summary["saldo_efectivo_caja"])


class EP13CajeroScopeTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.cajero_role = Role.objects.get(code="CAJERO")
        self.cajero = User.objects.create_user(
            username="cajero",
            password="test",
            role=self.cajero_role,
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )
        self.cajero.empresas_permitidas.set([self.empresa_a])

    def test_cajero_opens_and_loads_own_box_in_base_branch(self):
        caja = open_box(
            user=self.cajero,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.cajero,
        )
        register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Venta mostrador",
            observacion="Carga del cajero",
            actor=self.cajero,
        )

        self.assertEqual(caja.saldo_esperado, Decimal("150.00"))

    def test_cajero_cannot_open_box_outside_base_branch(self):
        with self.assertRaises(ValidationError):
            open_box(
                user=self.cajero,
                turno=self.turno_b,
                sucursal=self.branch_b,
                fecha_operativa=self.fecha_op,
                monto_inicial=Decimal("0.00"),
                actor=self.cajero,
            )

    def test_cajero_cannot_open_box_for_another_user(self):
        with self.assertRaises(PermissionDenied):
            open_box(
                user=self.operator,
                turno=self.turno_a,
                sucursal=self.branch_a,
                fecha_operativa=self.fecha_op,
                monto_inicial=Decimal("0.00"),
                actor=self.cajero,
            )

    def test_cajero_cannot_operate_foreign_box(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )

        with self.assertRaises(PermissionDenied):
            register_cash_income(
                caja=caja,
                monto=Decimal("10.00"),
                categoria="Intento ajeno",
                observacion="",
                actor=self.cajero,
            )

    def test_cajero_has_no_treasury_config_users_access(self):
        self.client.force_login(self.cajero)

        self.assertEqual(self.client.get(reverse("cashops:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("treasury:dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("users:user_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("cashops:sucursal_create")).status_code, 403)

    def test_cajero_cannot_validate_cash_by_default(self):
        self.assertFalse(can_validate_cash(self.cajero))
        self.assertTrue(can_validate_cash(self.admin))
        with self.assertRaises(PermissionDenied):
            ensure_cash_validation(self.cajero)


class EP13CashValidationTests(CashopsTestCase):
    def _open_operator_box(self, monto_inicial="100.00"):
        return open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal(monto_inicial),
            actor=self.operator,
        )

    def test_open_box_with_cash_counts_normally_until_close(self):
        caja = self._open_operator_box()
        register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Mostrador",
            observacion="",
            actor=self.operator,
        )

        caja.refresh_from_db()
        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )

        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.NO_REQUERIDA)
        self.assertEqual(summary["total_ingresos"], Decimal("50.00"))
        self.assertEqual(summary["cajas_periodo_count"], 1)

    def test_close_with_cash_leaves_box_pending_and_out_of_every_total(self):
        from treasury.models import MovimientoCajaCentral
        from treasury.services import build_economic_period_snapshot, build_financial_period_snapshot

        caja = self._open_operator_box()
        register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Mostrador",
            observacion="",
            actor=self.operator,
        )
        register_card_sale(caja=caja, monto=Decimal("60.00"), actor=self.operator)
        register_general_sale(
            caja=caja,
            monto=Decimal("80.00"),
            tipo_venta=MovimientoCaja.Tipo.VENTA_QR,
            rubro=self.rubro_insumos,
            observacion="",
            actor=self.operator,
        )
        register_expense(
            caja=caja,
            monto=Decimal("30.00"),
            rubro_operativo=self.rubro_insumos,
            categoria="Insumos",
            observacion="",
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("120.00"), cerrado_por=self.operator, actor=self.operator)

        caja.refresh_from_db()
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.PENDIENTE)
        self.assertFalse(MovimientoCajaCentral.objects.exists())

        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(summary["total_ingresos"], Decimal("0.00"))
        self.assertEqual(summary["saldo_real_cajas_periodo"], Decimal("0.00"))
        self.assertEqual(summary["cajas_periodo_count"], 0)

        matrix = build_management_daily_matrix(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(matrix["total_income"], Decimal("0.00"))
        self.assertEqual(matrix["total_expense"], Decimal("0.00"))

        financial = build_financial_period_snapshot(date_from=self.fecha_op, date_to=self.fecha_op)
        self.assertEqual(financial["cash_income"], Decimal("0.00"))
        self.assertEqual(financial["cash_expense"], Decimal("0.00"))
        self.assertEqual(financial["digital_sales_total"], Decimal("0.00"))
        self.assertEqual(financial["central_cash_total"], Decimal("0.00"))

        economic = build_economic_period_snapshot(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(economic["sales_total"], Decimal("0.00"))
        self.assertEqual(economic["cash_expense_total"], Decimal("0.00"))

        branch_snapshot = build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=self.fecha_op, sucursal=self.branch_a)
        )
        self.assertEqual(branch_snapshot["total_ingresos"], Decimal("0.00"))
        self.assertEqual(branch_snapshot["total_egresos"], Decimal("0.00"))
        global_snapshot = build_operational_control_snapshot(
            build_global_control_scope(fecha_operativa=self.fecha_op)
        )
        self.assertEqual(global_snapshot["total_ingresos"], Decimal("0.00"))

        from treasury.services import build_economic_rubro_detail

        rubro_detail = build_economic_rubro_detail(
            rubro_id=self.rubro_insumos.pk,
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(rubro_detail["cash_expense_total"], Decimal("0.00"))

        from treasury.models import CuentaBancaria
        from treasury.services import build_bank_reconciliation_snapshot, create_bank_account

        account = create_bank_account(
            nombre="Cuenta conciliacion",
            banco="Banco Test",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-111",
            cbu="2850590940090418135201",
            empresa=self.empresa_a,
            actor=self.admin,
        )
        reconciliation = build_bank_reconciliation_snapshot(
            cuenta_bancaria=account,
            date_from=timezone.localdate() - timedelta(days=1),
            date_to=timezone.localdate() + timedelta(days=1),
        )
        self.assertEqual(reconciliation["total_sales"], Decimal("0.00"))

    def test_validate_box_restores_totals_and_pushes_central_cash_once(self):
        from treasury.models import MovimientoCajaCentral

        caja = self._open_operator_box()
        register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Mostrador",
            observacion="",
            actor=self.operator,
        )
        close_box(caja=caja, saldo_fisico=Decimal("150.00"), cerrado_por=self.operator, actor=self.operator)

        validate_box_cash(caja=caja, actor=self.admin)

        caja.refresh_from_db()
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.VALIDADA)
        self.assertEqual(caja.validada_por, self.admin)
        self.assertIsNotNone(caja.validada_en)
        evento = CajaValidacion.objects.get(caja=caja)
        self.assertEqual(evento.accion, CajaValidacion.Accion.VALIDACION)
        self.assertEqual(evento.efectivo_esperado, Decimal("150.00"))
        push = MovimientoCajaCentral.objects.get(concepto=f"Cierre caja #{caja.pk}")
        self.assertEqual(push.monto, Decimal("150.00"))

        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(summary["total_ingresos"], Decimal("50.00"))
        self.assertEqual(summary["saldo_real_cajas_periodo"], Decimal("150.00"))
        self.assertEqual(summary["cajas_periodo_count"], 1)
        branch_snapshot = build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=self.fecha_op, sucursal=self.branch_a)
        )
        self.assertEqual(branch_snapshot["total_ingresos"], Decimal("50.00"))

        with self.assertRaises(ValidationError):
            validate_box_cash(caja=caja, actor=self.admin)
        self.assertEqual(MovimientoCajaCentral.objects.count(), 1)

    def test_validation_requires_permission_and_closed_box(self):
        caja = self._open_operator_box()

        with self.assertRaises(PermissionDenied):
            validate_box_cash(caja=caja, actor=self.operator)
        with self.assertRaises(ValidationError):
            validate_box_cash(caja=caja, actor=self.admin)

    def test_reject_requires_motivo_and_box_can_be_validated_later(self):
        caja = self._open_operator_box()
        close_box(caja=caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.operator, actor=self.operator)

        with self.assertRaises(ValidationError):
            reject_box_cash(caja=caja, motivo="  ", actor=self.admin)

        reject_box_cash(caja=caja, motivo="Faltan 100 pesos contra lo entregado", actor=self.admin)

        caja.refresh_from_db()
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.RECHAZADA)
        evento = CajaValidacion.objects.get(caja=caja, accion=CajaValidacion.Accion.RECHAZO)
        self.assertEqual(evento.motivo, "Faltan 100 pesos contra lo entregado")
        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(summary["cajas_periodo_count"], 0)

        validate_box_cash(caja=caja, actor=self.admin)

        caja.refresh_from_db()
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.VALIDADA)

    def test_box_without_cash_counts_immediately_without_validation(self):
        from treasury.models import MovimientoCajaCentral

        caja = self._open_operator_box(monto_inicial="0.00")
        register_card_sale(caja=caja, monto=Decimal("150.00"), actor=self.operator)
        close_box(caja=caja, saldo_fisico=Decimal("0.00"), cerrado_por=self.operator, actor=self.operator)

        caja.refresh_from_db()
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.NO_REQUERIDA)
        self.assertFalse(MovimientoCajaCentral.objects.exists())
        summary = build_operational_period_summary(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(summary["total_ventas_digitales"], Decimal("150.00"))
        self.assertEqual(summary["cajas_periodo_count"], 1)


class EP13ReviewFixTests(CashopsTestCase):
    def _pending_box(self, monto_inicial="100.00", income="50.00", fisico="150.00"):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal(monto_inicial),
            actor=self.operator,
        )
        if income:
            register_cash_income(
                caja=caja,
                monto=Decimal(income),
                categoria="Mostrador",
                observacion="",
                actor=self.operator,
            )
        close_box(caja=caja, saldo_fisico=Decimal(fisico), cerrado_por=self.operator, actor=self.operator)
        caja.refresh_from_db()
        return caja

    def test_month_close_blocked_while_boxes_pending_validation(self):
        from treasury.services import close_treasury_month

        caja = self._pending_box()

        with self.assertRaises(ValidationError):
            close_treasury_month(2026, 3, actor=self.admin)

        validate_box_cash(caja=caja, actor=self.admin)
        closing = close_treasury_month(2026, 3, actor=self.admin)
        self.assertTrue(closing.cerrado)

    def test_validation_after_month_close_redates_central_push(self):
        from treasury.models import MovimientoCajaCentral
        from treasury.services import close_treasury_month

        close_treasury_month(2026, 3, actor=self.admin)
        caja = self._pending_box()

        validate_box_cash(caja=caja, actor=self.admin)

        push = MovimientoCajaCentral.objects.get(caja_cierre=caja)
        self.assertEqual(push.monto, Decimal("150.00"))
        self.assertEqual(push.fecha, timezone.localdate())
        self.assertIn("mes de tesoreria ya cerrado", push.observaciones)

    def test_forged_concept_cannot_suppress_validation_push(self):
        from treasury.models import MovimientoCajaCentral
        from treasury.services import register_central_cash_movement

        caja = self._pending_box()
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1.00"),
            concepto=f"Cierre caja #{caja.pk}",
            actor=self.admin,
        )

        validate_box_cash(caja=caja, actor=self.admin)

        push = MovimientoCajaCentral.objects.get(caja_cierre=caja)
        self.assertEqual(push.tipo, MovimientoCajaCentral.Tipo.INGRESO_CAJA)
        self.assertEqual(push.monto, Decimal("150.00"))

    def test_pending_box_alert_stays_out_of_panel_until_validated(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        register_cash_income(
            caja=caja,
            monto=Decimal("50.00"),
            categoria="Mostrador",
            observacion="",
            actor=self.operator,
        )
        close_box(
            caja=caja,
            saldo_fisico=Decimal("20050.00"),
            justificacion="Sobrante grande a revisar",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        alerta = AlertaOperativa.objects.get(tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE, caja=caja)
        self.assertFalse(alerta.resuelta)

        panel = build_alert_panel_queryset(estado="activas")
        self.assertFalse(panel.filter(pk=alerta.pk).exists())

        validate_box_cash(caja=caja, actor=self.admin)

        panel = build_alert_panel_queryset(estado="activas")
        self.assertTrue(panel.filter(pk=alerta.pk).exists())

    def test_annul_pending_box_without_push_leaves_central_cash_untouched(self):
        from treasury.models import MovimientoCajaCentral

        caja = self._pending_box()
        self._grant_closed_box_fix(self.operator)

        annul_box(caja=caja, motivo="Caja duplicada", actor=self.operator)

        caja.refresh_from_db()
        self.assertEqual(caja.estado, Caja.Estado.ANULADA)
        self.assertFalse(MovimientoCajaCentral.objects.exists())

    @override_settings(ENABLE_DANGER_RESET=True)
    def test_reset_survives_boxes_with_audited_corrections(self):
        from .services import update_box_metadata

        caja = self._pending_box()
        self._grant_closed_box_fix(self.admin)
        update_box_metadata(
            caja=caja,
            usuario=caja.usuario,
            sucursal=caja.sucursal,
            turno=caja.turno,
            fecha_operativa=caja.fecha_operativa,
            monto_inicial=caja.monto_inicial,
            motivo="Ajuste auditado de prueba",
            actor=self.admin,
        )
        self.assertTrue(CajaCorreccion.objects.exists())
        self.client.force_login(self.admin)

        response = self.client.post(reverse("cashops:reset_operational_data"), {"step": "2"})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Caja.objects.exists())
        self.assertFalse(CajaCorreccion.objects.exists())


class EP13CashValidationViewTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.pending_box = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        close_box(caja=self.pending_box, saldo_fisico=Decimal("100.00"), cerrado_por=self.operator, actor=self.operator)

    def test_queue_requires_validation_permission(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:box_validation_queue"))

        self.assertEqual(response.status_code, 403)

    def test_queue_lists_pending_box_with_actions_for_validator(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_validation_queue"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Caja #{self.pending_box.pk}")
        self.assertContains(response, "Pendiente de validación")
        self.assertContains(response, reverse("cashops:box_validate", args=[self.pending_box.pk]))
        self.assertContains(response, reverse("cashops:box_reject", args=[self.pending_box.pk]))

    def test_validate_action_marks_box_and_redirects(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("cashops:box_validate", args=[self.pending_box.pk]))

        self.assertEqual(response.status_code, 302)
        self.pending_box.refresh_from_db()
        self.assertEqual(self.pending_box.validacion_estado, Caja.ValidacionEstado.VALIDADA)

    def test_validate_action_requires_permission(self):
        self.client.force_login(self.operator)

        response = self.client.post(reverse("cashops:box_validate", args=[self.pending_box.pk]))

        self.assertEqual(response.status_code, 403)
        self.pending_box.refresh_from_db()
        self.assertEqual(self.pending_box.validacion_estado, Caja.ValidacionEstado.PENDIENTE)

    def test_reject_flow_requires_motivo_and_marks_box(self):
        self.client.force_login(self.admin)
        url = reverse("cashops:box_reject", args=[self.pending_box.pk])

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)

        empty_post = self.client.post(url, {"motivo": ""})
        self.assertEqual(empty_post.status_code, 200)
        self.pending_box.refresh_from_db()
        self.assertEqual(self.pending_box.validacion_estado, Caja.ValidacionEstado.PENDIENTE)

        response = self.client.post(url, {"motivo": "No coincide el efectivo entregado"})
        self.assertEqual(response.status_code, 302)
        self.pending_box.refresh_from_db()
        self.assertEqual(self.pending_box.validacion_estado, Caja.ValidacionEstado.RECHAZADA)

    def test_nav_shows_validaciones_only_with_permission(self):
        self.client.force_login(self.admin)
        admin_page = self.client.get(reverse("cashops:box_tracking"))
        self.assertContains(admin_page, reverse("cashops:box_validation_queue"))

        self.client.force_login(self.operator)
        operator_page = self.client.get(reverse("cashops:box_tracking"))
        self.assertNotContains(operator_page, reverse("cashops:box_validation_queue"))

    def test_tracking_shows_pending_validation_badge(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_tracking"))

        self.assertContains(response, "Pendiente de validación")

    def test_tracking_shows_validated_badge_after_validation(self):
        validate_box_cash(caja=self.pending_box, actor=self.admin)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("cashops:box_tracking"))

        self.assertContains(response, "Efectivo validado")

    def test_non_admin_validator_can_open_detail_of_foreign_pending_box(self):
        validator = User.objects.create_user(
            username="validadora",
            password="test",
            role=self.operator_role,
        )
        validator.empresas_permitidas.set([self.empresa_a])
        UserPermission.objects.create(
            user=validator,
            module=PermissionModule.CASHOPS_VALIDATE,
            can_read=True,
            can_write=True,
        )
        self.client.force_login(validator)

        queue_response = self.client.get(reverse("cashops:box_validation_queue"))
        detail_response = self.client.get(reverse("cashops:box_detail", args=[self.pending_box.pk]))

        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, f"Caja #{self.pending_box.pk}")
        self.assertEqual(detail_response.status_code, 200)


class EP13BoxExpenseDebtTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        from treasury.services import create_payable_category, create_supplier

        self.cajero_role = Role.objects.get(code="CAJERO")
        self.cajero = User.objects.create_user(
            username="cajero-deuda",
            password="test",
            role=self.cajero_role,
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )
        self.cajero.empresas_permitidas.set([self.empresa_a])
        self.supplier = create_supplier(razon_social="Proveedor Caja SA", actor=self.admin)
        self.payable_category = create_payable_category(
            nombre="Insumos de caja",
            rubro_operativo=self.rubro_insumos,
            actor=self.admin,
        )
        self.caja = open_box(
            user=self.cajero,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.cajero,
        )

    def _register_debt(self, monto="80.00", concepto="Harina para el turno", caja=None, actor=None):
        return register_box_expense_debt(
            caja=caja or self.caja,
            proveedor=self.supplier,
            categoria=self.payable_category,
            monto=Decimal(monto),
            concepto=concepto,
            actor=actor or self.cajero,
        )

    def test_cajero_registers_expense_as_pending_debt_without_cash_out(self):
        from treasury.models import CuentaPorPagar

        saldo_antes = self.caja.saldo_esperado

        deuda = self._register_debt()

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(deuda.saldo_pendiente, Decimal("80.00"))
        self.assertEqual(deuda.sucursal, self.branch_a)
        self.assertEqual(deuda.caja_origen, self.caja)
        self.assertEqual(deuda.periodo_referencia, date(2026, 3, 1))
        self.assertEqual(deuda.creado_por, self.cajero)
        self.assertEqual(self.caja.saldo_esperado, saldo_antes)
        self.assertEqual(self.caja.movimientos.filter(tipo=MovimientoCaja.Tipo.GASTO).count(), 0)

    def test_debt_enters_economic_once_and_financial_only_when_paid(self):
        from treasury.models import MovimientoCajaCentral
        from treasury.services import (
            build_economic_period_snapshot,
            build_financial_period_snapshot,
            register_cash_payment,
            register_central_cash_movement,
        )

        deuda = self._register_debt()

        economic = build_economic_period_snapshot(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(economic["debt_period_total"], Decimal("80.00"))
        self.assertEqual(economic["cash_expense_total"], Decimal("0.00"))

        financial = build_financial_period_snapshot(date_from=self.fecha_op, date_to=self.fecha_op)
        self.assertEqual(financial["cash_expense"], Decimal("0.00"))
        self.assertEqual(financial["pending_total"], Decimal("80.00"))

        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("500.00"),
            concepto="Fondo central",
            fecha=self.fecha_op,
            actor=self.admin,
        )
        register_cash_payment(
            payable=deuda,
            fecha_pago=self.fecha_op,
            monto=Decimal("80.00"),
            actor=self.admin,
        )

        financial_after = build_financial_period_snapshot(date_from=self.fecha_op, date_to=self.fecha_op)
        self.assertEqual(financial_after["central_cash_expense_period"], Decimal("80.00"))
        self.assertEqual(financial_after["pending_total"], Decimal("0.00"))
        economic_after = build_economic_period_snapshot(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(economic_after["debt_period_total"], Decimal("80.00"))

    def test_debt_requires_open_own_box_and_valid_data(self):
        foreign = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        with self.assertRaises(PermissionDenied):
            self._register_debt(caja=foreign)

        with self.assertRaises(ValidationError):
            self._register_debt(monto="0.00")

        with self.assertRaises(ValidationError):
            self._register_debt(concepto="   ")

        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        with self.assertRaises(ValidationError):
            self._register_debt()

    def test_categoria_sin_rubro_is_rejected(self):
        from treasury.models import CategoriaCuentaPagar

        legacy_cat = CategoriaCuentaPagar.objects.create(nombre="Legacy sin rubro", creado_por=self.admin)

        with self.assertRaises(ValidationError):
            register_box_expense_debt(
                caja=self.caja,
                proveedor=self.supplier,
                categoria=legacy_cat,
                monto=Decimal("10.00"),
                concepto="Gasto legacy",
                actor=self.cajero,
            )

    def test_view_creates_debt_for_cajero(self):
        from treasury.models import CuentaPorPagar

        self.client.force_login(self.cajero)
        url = reverse("cashops:box_expense_debt", args=[self.caja.pk])

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)

        response = self.client.post(
            url,
            {
                "proveedor": self.supplier.pk,
                "rubro": self.rubro_insumos.pk,
                "fecha_factura": self.fecha_op.isoformat(),
                "monto": "45.50",
                "concepto": "Velas y descartables",
                "referencia_comprobante": "",
                "observacion": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        deuda = CuentaPorPagar.objects.get(caja_origen=self.caja)
        self.assertEqual(deuda.importe_total, Decimal("45.50"))
        self.assertEqual(deuda.creado_por, self.cajero)

    def test_view_rejects_foreign_box(self):
        foreign = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.operator,
        )
        self.client.force_login(self.cajero)

        response = self.client.post(
            reverse("cashops:box_expense_debt", args=[foreign.pk]),
            {
                "proveedor": self.supplier.pk,
                "rubro": self.rubro_insumos.pk,
                "monto": "10.00",
                "concepto": "Intento ajeno",
            },
        )

        self.assertEqual(response.status_code, 403)

    def _grant_closed_debt_permission(self, user=None):
        return UserPermission.objects.create(
            user=user or self.cajero,
            module=PermissionModule.CASHOPS_DEBT_CLOSED,
            can_read=True,
            can_write=True,
        )

    def test_debt_uses_fecha_factura_for_emision_and_period(self):
        deuda = self._register_debt()  # sin fecha_factura -> usa la fecha operativa de la caja
        self.assertEqual(deuda.fecha_emision, self.fecha_op)
        self.assertEqual(deuda.periodo_referencia, self.fecha_op.replace(day=1))

        otra = date(2026, 5, 9)
        deuda2 = register_box_expense_debt(
            caja=self.caja,
            proveedor=self.supplier,
            categoria=self.payable_category,
            monto=Decimal("30.00"),
            concepto="Con fecha de factura propia",
            fecha_factura=otra,
            actor=self.cajero,
        )
        self.assertEqual(deuda2.fecha_emision, otra)
        self.assertEqual(deuda2.periodo_referencia, date(2026, 5, 1))

    def test_debt_on_closed_box_blocked_without_permission(self):
        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        with self.assertRaises(ValidationError):
            register_box_expense_debt(
                caja=self.caja,
                proveedor=self.supplier,
                categoria=self.payable_category,
                monto=Decimal("40.00"),
                concepto="Intento sin permiso",
                permitir_caja_cerrada=False,
                actor=self.cajero,
            )

    def test_debt_on_closed_box_allowed_with_permission_without_touching_box(self):
        from treasury.models import CuentaPorPagar

        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        self.caja.refresh_from_db()
        estado_antes = self.caja.estado
        validacion_antes = self.caja.validacion_estado
        saldo_antes = self.caja.saldo_esperado

        deuda = register_box_expense_debt(
            caja=self.caja,
            proveedor=self.supplier,
            categoria=self.payable_category,
            monto=Decimal("55.00"),
            concepto="Backfill julio",
            fecha_factura=date(2026, 3, 20),
            permitir_caja_cerrada=True,
            actor=self.cajero,
        )

        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PENDIENTE)
        self.assertEqual(deuda.caja_origen, self.caja)
        self.assertEqual(deuda.fecha_emision, date(2026, 3, 20))
        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, estado_antes)  # sigue cerrada, no se reabre
        self.assertEqual(self.caja.validacion_estado, validacion_antes)  # no toca la validacion
        self.assertEqual(self.caja.saldo_esperado, saldo_antes)  # no toca el efectivo
        self.assertEqual(self.caja.movimientos.filter(tipo=MovimientoCaja.Tipo.GASTO).count(), 0)

    def test_debt_never_allowed_on_annulled_box(self):
        from cashops.services import annul_box

        annul_box(caja=self.caja, motivo="prueba", actor=self.admin)
        with self.assertRaises(ValidationError):
            register_box_expense_debt(
                caja=self.caja,
                proveedor=self.supplier,
                categoria=self.payable_category,
                monto=Decimal("10.00"),
                concepto="Sobre anulada",
                permitir_caja_cerrada=True,
                actor=self.cajero,
            )

    def test_cash_movements_still_blocked_on_closed_box(self):
        from cashops.services import register_cash_income, register_expense

        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        with self.assertRaises(ValidationError):
            register_cash_income(caja=self.caja, monto=Decimal("10.00"), categoria="INGRESO", actor=self.cajero)
        with self.assertRaises(ValidationError):
            register_expense(
                caja=self.caja,
                monto=Decimal("10.00"),
                rubro_operativo=self.rubro_insumos,
                categoria="GASTO",
                actor=self.cajero,
            )

    def test_view_loads_debt_on_closed_box_when_permitted(self):
        from treasury.models import CuentaPorPagar

        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        self._grant_closed_debt_permission()
        self.client.force_login(self.cajero)
        url = reverse("cashops:box_expense_debt", args=[self.caja.pk])

        response = self.client.post(
            url,
            {
                "proveedor": self.supplier.pk,
                "rubro": self.rubro_insumos.pk,
                "fecha_factura": "2026-03-18",
                "monto": "33.00",
                "concepto": "Pollo cuenta corriente",
                "referencia_comprobante": "",
                "observacion": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        deuda = CuentaPorPagar.objects.get(caja_origen=self.caja)
        self.assertEqual(deuda.importe_total, Decimal("33.00"))
        self.assertEqual(deuda.fecha_emision, date(2026, 3, 18))

    def test_view_blocks_debt_on_closed_box_without_permission(self):
        from treasury.models import CuentaPorPagar

        close_box(caja=self.caja, saldo_fisico=Decimal("100.00"), cerrado_por=self.cajero, actor=self.cajero)
        self.client.force_login(self.cajero)
        url = reverse("cashops:box_expense_debt", args=[self.caja.pk])

        response = self.client.post(
            url,
            {
                "proveedor": self.supplier.pk,
                "rubro": self.rubro_insumos.pk,
                "fecha_factura": "2026-03-18",
                "monto": "33.00",
                "concepto": "Intento sin permiso",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(CuentaPorPagar.objects.filter(caja_origen=self.caja).exists())

    def _extra_branch_same_empresa(self):
        return Sucursal.objects.create(
            codigo="SUC-ON", nombre="Oveja Negra", razon_social="ARMADI SRL", empresa=self.empresa_a
        )

    def test_sucursales_para_deuda_includes_base_and_extras(self):
        self.assertEqual(set(self.cajero.sucursales_para_deuda()), {self.branch_a})
        extra = self._extra_branch_same_empresa()
        self.cajero.sucursales_deuda.add(extra)
        self.assertEqual(set(self.cajero.sucursales_para_deuda()), {self.branch_a, extra})

    def test_debt_imputes_to_selected_extra_branch(self):
        extra = self._extra_branch_same_empresa()
        self.cajero.sucursales_deuda.add(extra)
        deuda = register_box_expense_debt(
            caja=self.caja,
            proveedor=self.supplier,
            categoria=self.payable_category,
            monto=Decimal("70.00"),
            concepto="Huevos Oveja Negra",
            sucursal=extra,
            actor=self.cajero,
        )
        self.assertEqual(deuda.sucursal, extra)          # imputa a la sucursal elegida
        self.assertEqual(deuda.caja_origen, self.caja)   # provenance: la caja de Belgrano

    def test_debt_rejects_branch_not_allowed(self):
        extra = self._extra_branch_same_empresa()  # NO se agrega a sucursales_deuda
        with self.assertRaises(ValidationError):
            register_box_expense_debt(
                caja=self.caja, proveedor=self.supplier, categoria=self.payable_category,
                monto=Decimal("70.00"), concepto="No permitida", sucursal=extra, actor=self.cajero,
            )

    def test_debt_rejects_cross_empresa_branch(self):
        self.cajero.sucursales_deuda.add(self.branch_b)  # empresa_b, distinta a la caja (empresa_a)
        with self.assertRaises(ValidationError):
            register_box_expense_debt(
                caja=self.caja, proveedor=self.supplier, categoria=self.payable_category,
                monto=Decimal("70.00"), concepto="Otra empresa", sucursal=self.branch_b, actor=self.cajero,
            )

    def test_view_shows_branch_selector_and_imputes_to_extra(self):
        from treasury.models import CuentaPorPagar

        extra = self._extra_branch_same_empresa()
        self.cajero.sucursales_deuda.add(extra)
        self.client.force_login(self.cajero)
        url = reverse("cashops:box_expense_debt", args=[self.caja.pk])

        get_response = self.client.get(url)
        self.assertContains(get_response, "Sucursal de la deuda")

        response = self.client.post(url, {
            "proveedor": self.supplier.pk,
            "rubro": self.rubro_insumos.pk,
            "sucursal": extra.pk,
            "fecha_factura": self.fecha_op.isoformat(),
            "monto": "70.00",
            "concepto": "Huevos Oveja Negra",
        })
        self.assertEqual(response.status_code, 302)
        deuda = CuentaPorPagar.objects.get(caja_origen=self.caja)
        self.assertEqual(deuda.sucursal, extra)

    def test_view_hides_branch_selector_without_extras(self):
        self.client.force_login(self.cajero)
        get_response = self.client.get(reverse("cashops:box_expense_debt", args=[self.caja.pk]))
        self.assertNotContains(get_response, "Sucursal de la deuda")

    def test_debt_service_accepts_rubro_and_maps_to_category(self):
        deuda = register_box_expense_debt(
            caja=self.caja,
            proveedor=self.supplier,
            rubro=self.rubro_insumos,
            monto=Decimal("25.00"),
            concepto="Con rubro directo",
            actor=self.cajero,
        )
        self.assertEqual(deuda.categoria.rubro_operativo, self.rubro_insumos)
        # reusa la categoria activa que ya existe para ese rubro (no crea otra)
        self.assertEqual(deuda.categoria, self.payable_category)

    def test_debt_view_pide_rubro_no_categoria(self):
        from treasury.models import CuentaPorPagar

        self.client.force_login(self.cajero)
        url = reverse("cashops:box_expense_debt", args=[self.caja.pk])
        get_response = self.client.get(url)
        self.assertContains(get_response, "Rubro")
        self.assertNotContains(get_response, "Categoría del gasto")

        response = self.client.post(url, {
            "proveedor": self.supplier.pk,
            "rubro": self.rubro_insumos.pk,
            "fecha_factura": self.fecha_op.isoformat(),
            "monto": "12.00",
            "concepto": "Compra por rubro",
        })
        self.assertEqual(response.status_code, 302)
        deuda = CuentaPorPagar.objects.get(caja_origen=self.caja)
        self.assertEqual(deuda.categoria.rubro_operativo, self.rubro_insumos)

    def test_debt_rubro_sin_categoria_crea_una(self):
        from cashops.models import RubroOperativo
        from treasury.models import CategoriaCuentaPagar

        nuevo = RubroOperativo.objects.create(nombre="Cerveza")
        self.assertFalse(CategoriaCuentaPagar.objects.filter(rubro_operativo=nuevo).exists())
        deuda = register_box_expense_debt(
            caja=self.caja,
            proveedor=self.supplier,
            rubro=nuevo,
            monto=Decimal("30.00"),
            concepto="Cerveza cuenta corriente",
            actor=self.cajero,
        )
        self.assertEqual(deuda.categoria.rubro_operativo, nuevo)
        self.assertTrue(CategoriaCuentaPagar.objects.filter(rubro_operativo=nuevo, activo=True).exists())

    def test_box_detail_timeline_shows_debt_event(self):
        self._register_debt()
        self.client.force_login(self.cajero)

        response = self.client.get(reverse("cashops:box_detail", args=[self.caja.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gasto registrado como deuda")

    def test_annul_box_annuls_pending_originated_debts(self):
        from treasury.models import CuentaPorPagar
        from treasury.services import build_economic_period_snapshot

        deuda = self._register_debt()
        self._grant_closed_box_fix(self.admin)

        annul_box(caja=self.caja, motivo="Caja duplicada", actor=self.admin)

        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.ANULADA)
        self.assertEqual(deuda.saldo_pendiente, Decimal("0.00"))
        self.assertIn(f"Anulacion de caja origen #{self.caja.pk}", deuda.motivo_anulacion)
        economic = build_economic_period_snapshot(
            date_from=self.fecha_op,
            date_to=self.fecha_op,
            sucursal=self.branch_a,
        )
        self.assertEqual(economic["debt_period_total"], Decimal("0.00"))

    def test_annul_box_blocked_when_originated_debt_has_payments(self):
        from treasury.models import MovimientoCajaCentral
        from treasury.services import register_cash_payment, register_central_cash_movement

        deuda = self._register_debt()
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("500.00"),
            concepto="Fondo central",
            fecha=self.fecha_op,
            actor=self.admin,
        )
        register_cash_payment(
            payable=deuda,
            fecha_pago=self.fecha_op,
            monto=Decimal("80.00"),
            actor=self.admin,
        )
        self._grant_closed_box_fix(self.admin)

        with self.assertRaises(ValidationError):
            annul_box(caja=self.caja, motivo="Caja duplicada", actor=self.admin)

        self.caja.refresh_from_db()
        self.assertEqual(self.caja.estado, Caja.Estado.ABIERTA)

    def test_annulled_debt_is_marked_in_box_timeline(self):
        from treasury.services import annul_payable

        deuda = self._register_debt()
        annul_payable(payable=deuda, motivo="Carga duplicada", actor=self.admin)
        self.client.force_login(self.cajero)

        response = self.client.get(reverse("cashops:box_detail", args=[self.caja.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deuda anulada")
        self.assertContains(response, "Carga duplicada")
