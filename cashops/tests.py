from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import AlertaOperativa, Caja, CierreCaja, MovimientoCaja, Sucursal, Turno
from .services import (
    close_box,
    open_box,
    register_card_sale,
    register_expense,
    transfer_between_boxes,
    transfer_between_branches,
)


User = get_user_model()


class CashopsServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="operador", password="test")
        self.user_2 = User.objects.create_user(username="operador2", password="test")
        self.branch_a = Sucursal.objects.create(codigo="SUC-A", nombre="Sucursal A")
        self.branch_b = Sucursal.objects.create(codigo="SUC-B", nombre="Sucursal B")
        self.turno = Turno.objects.create(
            sucursal=self.branch_a,
            fecha_operativa="2026-03-27",
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.user,
        )
        self.turno_b = Turno.objects.create(
            sucursal=self.branch_b,
            fecha_operativa="2026-03-27",
            tipo=Turno.Tipo.MANANA,
            estado=Turno.Estado.ABIERTO,
            creado_por=self.user_2,
        )

    def test_open_box_registers_opening_movement(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("5000.00"))

        self.assertEqual(caja.estado, Caja.Estado.ABIERTA)
        self.assertEqual(caja.movimientos.count(), 1)
        self.assertEqual(caja.saldo_esperado, Decimal("5000.00"))
        self.assertEqual(caja.movimientos.first().tipo, MovimientoCaja.Tipo.APERTURA)

    def test_transfer_between_boxes_creates_two_movements(self):
        caja_origen = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("5000.00"))
        caja_destino = open_box(user=self.user_2, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))

        transferencia = transfer_between_boxes(
            caja_origen=caja_origen,
            caja_destino=caja_destino,
            monto=Decimal("250.00"),
            observacion="Traspaso operativo",
            creado_por=self.user,
        )

        self.assertEqual(transferencia.tipo, transferencia.Tipo.ENTRE_CAJAS)
        self.assertEqual(MovimientoCaja.objects.filter(transferencia=transferencia).count(), 2)
        self.assertEqual(caja_origen.saldo_esperado, Decimal("4750.00"))
        self.assertEqual(caja_destino.saldo_esperado, Decimal("1250.00"))

    def test_expense_and_pos_sale_update_balance(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))
        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            categoria="Insumos",
            observacion="Compra rapida",
            creado_por=self.user,
        )
        register_card_sale(
            caja=caja,
            monto=Decimal("350.00"),
            observacion="POS del dia",
            creado_por=self.user,
        )

        self.assertEqual(caja.saldo_esperado, Decimal("1250.00"))

    def test_close_box_auto_adjusts_small_difference(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))
        register_expense(
            caja=caja,
            monto=Decimal("100.00"),
            categoria="Gasto",
            observacion="test",
            creado_por=self.user,
        )

        cierre = close_box(caja=caja, saldo_fisico=Decimal("895.00"), cerrado_por=self.user)

        self.assertEqual(cierre.estado, CierreCaja.Estado.AUTO)
        self.assertEqual(caja.estado, Caja.Estado.CERRADA)
        self.assertEqual(caja.cierre.diferencia, Decimal("-5.00"))
        self.assertEqual(caja.cierre.ajuste_movimiento.tipo, MovimientoCaja.Tipo.AJUSTE_CIERRE)

    def test_close_box_requires_justification_when_difference_is_large(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))

        with self.assertRaises(ValidationError):
            close_box(caja=caja, saldo_fisico=Decimal("15050.00"), cerrado_por=self.user)

        cierre = close_box(
            caja=caja,
            saldo_fisico=Decimal("15050.00"),
            justificacion="Diferencia explicada por conteo externo",
            cerrado_por=self.user,
        )

        self.assertEqual(cierre.estado, CierreCaja.Estado.JUSTIFICADO)
        self.assertTrue(hasattr(cierre, "justificacion"))
        self.assertEqual(AlertaOperativa.objects.count(), 1)

    def test_closed_box_rejects_new_movements(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))
        close_box(caja=caja, saldo_fisico=Decimal("1000.00"), cerrado_por=self.user)

        with self.assertRaises(ValidationError):
            register_expense(
                caja=caja,
                monto=Decimal("10.00"),
                categoria="Test",
                observacion="No debe pasar",
                creado_por=self.user,
            )

    def test_branch_transfer_records_and_can_move_cash(self):
        caja_origen = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("2000.00"))
        caja_destino = open_box(user=self.user_2, turno=self.turno_b, sucursal=self.branch_b, monto_inicial=Decimal("500.00"))

        transferencia = transfer_between_branches(
            sucursal_origen=self.branch_a,
            sucursal_destino=self.branch_b,
            clase="DINERO",
            monto=Decimal("300.00"),
            observacion="Envio entre sucursales",
            caja_origen=caja_origen,
            caja_destino=caja_destino,
            creado_por=self.user,
        )

        self.assertEqual(transferencia.tipo, transferencia.Tipo.ENTRE_SUCURSALES)
        self.assertEqual(MovimientoCaja.objects.filter(transferencia=transferencia).count(), 2)
        self.assertEqual(caja_origen.saldo_esperado, Decimal("1700.00"))
        self.assertEqual(caja_destino.saldo_esperado, Decimal("800.00"))

    def test_turn_closes_after_last_box_is_closed(self):
        caja = open_box(user=self.user, turno=self.turno, sucursal=self.branch_a, monto_inicial=Decimal("1000.00"))

        close_box(caja=caja, saldo_fisico=Decimal("1000.00"), cerrado_por=self.user)

        self.turno.refresh_from_db()
        self.assertEqual(self.turno.estado, Turno.Estado.CERRADO)
