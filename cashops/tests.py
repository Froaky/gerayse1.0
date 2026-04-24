from decimal import Decimal
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import Role

from .forms import CajaAperturaForm, VentaGeneralForm
from .models import (
    AlertaOperativa,
    Caja,
    CierreCaja,
    LimiteRubroOperativo,
    MovimientoCaja,
    RubroOperativo,
    Sucursal,
    Transferencia,
    Turno,
)
from .permissions import can_operate_box, ensure_can_operate_box, is_cashops_admin
from .services import (
    BRANCH_TRANSFER_DISABLED_REASON,
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
    register_expense,
    transfer_between_boxes,
    transfer_between_branches,
    register_general_sale,
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

        self.branch_a = Sucursal.objects.create(codigo="SUC-A", nombre="Sucursal A", razon_social="ARMADI SRL")
        self.branch_b = Sucursal.objects.create(codigo="SUC-B", nombre="Sucursal B", razon_social="MAPOGO SRL")
        self.rubro_insumos = RubroOperativo.objects.create(nombre="Insumos")
        self.rubro_viaticos = RubroOperativo.objects.create(nombre="Viaticos")

        self.turno_a = Turno.objects.create(
            sucursal=self.branch_a,
            fecha_operativa="2026-03-27",
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.operator,
        )
        self.turno_b = Turno.objects.create(
            sucursal=self.branch_b,
            fecha_operativa="2026-03-27",
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.operator_2,
        )

    def _open_form(self, actor, data):
        form = CajaAperturaForm(data=data, actor=actor)
        form.fields["usuario"].queryset = User.objects.all()
        form.fields["sucursal"].queryset = Sucursal.objects.all()
        form.fields["turno"].queryset = Turno.objects.all()
        return form


class CashopsPermissionUnitTests(CashopsTestCase):
    def test_cashops_admin_helper_respects_role_and_superuser(self):
        superuser = User.objects.create_superuser(username="root", password="test", email="root@example.com")

        self.assertTrue(is_cashops_admin(self.admin))
        self.assertTrue(is_cashops_admin(superuser))
        self.assertFalse(is_cashops_admin(self.operator))

    def test_box_permission_helper_allows_owner_and_admin(self):
        caja = open_box(user=self.operator, turno=self.turno_a, sucursal=self.branch_a, monto_inicial=Decimal("100.00"), actor=self.operator)

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
                "monto_inicial": "100.00",
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
                "monto_inicial": "100.00",
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
        self.assertIn(self.turno_a, list(form.fields["turno"].queryset))

    def test_sale_form_no_longer_exposes_product_field(self):
        form = VentaGeneralForm()

        self.assertNotIn("producto", form.fields)


class CashopsServiceTests(CashopsTestCase):
    def test_admin_can_assign_box_to_another_user(self):
        caja = open_box(
            user=self.other,
            turno=self.turno_a,
            sucursal=self.branch_a,
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
            monto_inicial=Decimal("250.00"),
            actor=self.operator,
        )

        with self.assertRaises(ValidationError):
            open_box(
                user=self.operator,
                turno=self.turno_a,
                sucursal=self.branch_a,
                monto_inicial=Decimal("50.00"),
                actor=self.operator,
            )

    def test_cash_income_registers_movement_and_updates_balance(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
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
            build_branch_control_scope(fecha_operativa=self.turno_a.fecha_operativa, sucursal=self.branch_a)
        )

        self.assertEqual(snapshot["base_calculo_label"], "Egresos operativos del periodo")
        self.assertEqual(snapshot["base_calculo_total"], Decimal("60.00"))
        self.assertEqual(snapshot["total_ingresos"], Decimal("200.00"))

    def test_period_summary_aggregates_range_by_branch(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
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
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.TARDE,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        caja_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
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

    def test_management_daily_matrix_aggregates_channels_rubros_and_days(self):
        caja = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
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
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.TARDE,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        caja_next = open_box(
            user=self.operator_2,
            turno=turno_next,
            sucursal=self.branch_a,
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
            fecha_operativa=self.turno_a.fecha_operativa,
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
                periodo_fecha=self.turno_a.fecha_operativa,
                sucursal__isnull=True,
                caja__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                periodo_fecha=self.turno_a.fecha_operativa,
                sucursal=self.branch_a,
                caja__isnull=True,
            ).count(),
            1,
        )
        self.assertEqual(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                periodo_fecha=self.turno_a.fecha_operativa,
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
            periodo_fecha=self.turno_a.fecha_operativa,
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
            monto_inicial=Decimal("100.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_a,
            sucursal=self.branch_a,
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
            monto_inicial=Decimal("200.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_b,
            sucursal=self.branch_b,
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
            monto_inicial=Decimal("500.00"),
            actor=self.operator,
        )
        turno_siguiente = Turno.objects.create(
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.TARDE,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
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
        self.assertEqual(transferencia.caja_origen.turno.fecha_operativa, date(2026, 3, 27))
        self.assertEqual(transferencia.caja_destino.turno.fecha_operativa, date(2026, 3, 28))
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
            monto_inicial=Decimal("120.00"),
            actor=self.operator,
        )
        caja_destino = open_box(
            user=self.operator_2,
            turno=self.turno_b,
            sucursal=self.branch_b,
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


class CashopsViewTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.owned_box = open_box(
            user=self.operator,
            turno=self.turno_a,
            sucursal=self.branch_a,
            monto_inicial=Decimal("1000.00"),
            actor=self.operator,
        )
        self.foreign_box = open_box(
            user=self.other,
            turno=self.turno_b,
            sucursal=self.branch_b,
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

    def test_regular_dashboard_hides_foreign_box(self):
        self.client.force_login(self.operator)

        response = self.client.get(reverse("cashops:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Caja #{self.owned_box.id}")
        self.assertNotContains(response, self.other.username)

    def test_duplicate_open_box_returns_validation_feedback_without_500(self):
        self.client.force_login(self.operator)

        response = self.client.post(
            reverse("cashops:box_open"),
            {
                "usuario": self.operator.pk,
                "sucursal": self.branch_a.pk,
                "turno": self.turno_a.pk,
                "monto_inicial": "10.00",
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
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.TARDE,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        second_box = open_box(
            user=self.operator,
            turno=turno_siguiente,
            sucursal=self.branch_a,
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
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.TARDE,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
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
        self.assertContains(response, "27 Marzo 2026 a 28 Marzo 2026")
        self.assertContains(response, "$200")
        self.assertContains(response, "$50")
        self.assertContains(response, "Saldo neto")
        self.assertContains(response, "$150")

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
        self.assertContains(response, "Registrar ingreso")
        self.assertContains(response, "Registrar egreso")
        self.assertContains(response, "Arrastre entre cajas")
        self.assertNotContains(response, "Egreso por rubro")

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
                "periodo_desde": self._period(self.turno_a.fecha_operativa),
                "periodo_hasta": self._period(self.turno_a.fecha_operativa),
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
            sucursal=self.branch_a,
            fecha_operativa="2026-03-28",
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.admin,
        )
        box_siguiente = open_box(
            user=self.operator_2,
            turno=turno_siguiente,
            sucursal=self.branch_a,
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
                "periodo_desde": self._period(self.turno_a.fecha_operativa),
                "periodo_hasta": self._period(self.turno_a.fecha_operativa),
            },
        )

        self.assertEqual(response.status_code, 200)
        alertas = list(response.context["alertas"])
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0].periodo_fecha.isoformat(), self._period(self.turno_a.fecha_operativa))
        self.assertContains(response, self._period(self.turno_a.fecha_operativa))
        self.assertNotContains(response, self._period(turno_siguiente.fecha_operativa))

    def test_alert_panel_shows_complete_context_for_grave_alert(self):
        close_box(
            caja=self.owned_box,
            saldo_fisico=Decimal("13050.00"),
            justificacion="Diferencia detectada",
            cerrado_por=self.operator,
            actor=self.operator,
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "alcance": "caja",
                "sucursal": self.branch_a.pk,
                "periodo_desde": self._period(self.turno_a.fecha_operativa),
                "periodo_hasta": self._period(self.turno_a.fecha_operativa),
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
                "periodo_desde": self._period(self.turno_a.fecha_operativa),
                "periodo_hasta": self._period(self.turno_a.fecha_operativa),
            },
        )

        self.assertEqual(response.status_code, 200)
        rubro_alerts = list(
            AlertaOperativa.objects.filter(
                tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
                rubro_operativo=self.rubro_insumos,
                periodo_fecha=self.turno_a.fecha_operativa,
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
            periodo_fecha=self.turno_a.fecha_operativa,
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
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "periodo_desde": str(self.turno_a.fecha_operativa),
                "periodo_hasta": str(self.turno_a.fecha_operativa),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.branch_a.nombre)
        self.assertContains(response, f"#{self.owned_box.pk}")
        self.assertContains(response, self.turno_a.get_tipo_display())
        self.assertContains(response, str(self.turno_a.fecha_operativa))
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
            periodo_fecha=self.turno_a.fecha_operativa,
            mensaje="Alerta scope caja",
        )
        AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            sucursal=self.branch_a,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.turno_a.fecha_operativa,
            mensaje="Alerta scope sucursal",
        )
        AlertaOperativa.objects.create(
            tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            rubro_operativo=self.rubro_insumos,
            periodo_fecha=self.turno_a.fecha_operativa,
            mensaje="Alerta scope global",
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("cashops:alert_panel"),
            {
                "estado": "activas",
                "periodo_desde": str(self.turno_a.fecha_operativa),
                "periodo_hasta": str(self.turno_a.fecha_operativa),
                "rubro": self.rubro_insumos.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index("Alerta scope caja"), content.index("Alerta scope sucursal"))
        self.assertLess(content.index("Alerta scope sucursal"), content.index("Alerta scope global"))
        self.assertContains(response, "Las alertas equivalentes se muestran todas", html=False)
