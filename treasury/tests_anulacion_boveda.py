"""Anulacion de movimientos de la boveda de efectivo.

Reemplaza la practica de compensar a mano: en produccion 7 de los 8
AJUSTE_POSITIVO son parches de gastos cargados dos veces, cargados asi porque el
modelo no tenia forma de anular nada.

Cubre tambien un agujero que existia antes de este slice: anular un pago en
efectivo dejaba vivo su EGRESO_PAGO, o sea que la deuda volvia a quedar
pendiente pero la plata nunca volvia a la caja fuerte.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CuentaPorPagar,
    MovimientoCajaCentral,
    Proveedor,
)
from treasury.services import (
    annul_central_cash_movement,
    annul_payment,
    build_economic_period_snapshot,
    get_boveda,
    is_central_cash_movement_annullable,
    register_cash_payment,
    register_central_cash_movement,
    register_egreso_tesoreria,
)
from users.models import PermissionModule, UserPermission

User = get_user_model()


class AnulacionBovedaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-boveda", password="test", email="a@test.com"
        )
        # Tiene tesoreria (puede cargar) pero NO el permiso de anular: es el caso
        # que interesa, porque cargar y sacarle plata a la boveda van separados.
        self.sin_permiso = User.objects.create_user(username="carga-nomas", password="test")
        UserPermission.objects.create(
            user=self.sin_permiso,
            module=PermissionModule.TREASURY,
            can_read=True,
            can_write=True,
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Anulacion")
        self.sucursal = Sucursal.objects.create(
            codigo="ANU", nombre="Sucursal Anulacion", razon_social="Anulacion", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Servicios Anulacion")
        self.proveedor = Proveedor.objects.create(razon_social="Proveedor Anulacion", creado_por=self.admin)
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria Anulacion", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.hoy = timezone.localdate()

    def _fondear(self, monto="1000.00"):
        return register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal(monto),
            concepto="Fondeo",
            fecha=self.hoy,
            actor=self.admin,
        )

    def _egreso(self, monto="250.00"):
        return register_egreso_tesoreria(
            empresa=self.empresa,
            fuente="CAJA_CENTRAL",
            fecha=self.hoy,
            monto=Decimal(monto),
            concepto="Gasto cargado de mas",
            rubro=self.rubro,
            sucursal=self.sucursal,
            periodo=self.hoy.replace(day=1),
            actor=self.admin,
        )

    # --- lo esencial: la plata vuelve ------------------------------------

    def test_anular_devuelve_el_saldo_y_deja_auditoria(self):
        self._fondear("1000.00")
        egreso = self._egreso("250.00")
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("750.00"))

        annul_central_cash_movement(movement=egreso, motivo="Se cargo dos veces", actor=self.admin)

        egreso.refresh_from_db()
        self.assertEqual(egreso.estado, MovimientoCajaCentral.Estado.ANULADO)
        self.assertEqual(egreso.motivo_anulacion, "Se cargo dos veces")
        self.assertEqual(egreso.anulado_por_id, self.admin.pk)
        self.assertIsNotNone(egreso.anulado_en)
        # El saldo vuelve al valor previo: es el punto de todo el slice.
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("1000.00"))

    def test_anular_exige_motivo(self):
        egreso = self._egreso()
        with self.assertRaises(ValidationError):
            annul_central_cash_movement(movement=egreso, motivo="   ", actor=self.admin)
        egreso.refresh_from_db()
        self.assertEqual(egreso.estado, MovimientoCajaCentral.Estado.REGISTRADO)

    def test_no_se_puede_anular_dos_veces(self):
        egreso = self._egreso()
        annul_central_cash_movement(movement=egreso, motivo="Primera", actor=self.admin)
        with self.assertRaises(ValidationError):
            annul_central_cash_movement(movement=egreso, motivo="Segunda", actor=self.admin)

    # --- que NO se puede anular a mano -----------------------------------

    def test_no_se_anula_lo_que_genero_un_pago(self):
        self._fondear()
        payable = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("300.00"),
            saldo_pendiente=Decimal("300.00"),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )
        pago = register_cash_payment(
            payable=payable, fecha_pago=self.hoy, monto=Decimal("300.00"), actor=self.admin
        )
        egreso_pago = MovimientoCajaCentral.objects.get(pago_tesoreria=pago)
        self.assertFalse(is_central_cash_movement_annullable(egreso_pago))
        with self.assertRaises(ValidationError):
            annul_central_cash_movement(movement=egreso_pago, motivo="No deberia", actor=self.admin)

    def test_no_se_anula_en_un_mes_cerrado_todavia(self):
        """Falta definir con administracion como contra-asentar en el mes abierto.

        El saldo inicial de cada mes sale del valor GUARDADO en el cierre, asi que
        anular hacia atras no devuelve la plata a ningun lado. Hasta que este
        definido, se bloquea con un mensaje claro en vez de mover mal la plata.
        """
        egreso = self._egreso()
        CierreMensualTesoreria.objects.create(
            mes=self.hoy.replace(day=1),
            saldo_inicial_efectivo=Decimal("0.00"),
            saldo_final_efectivo=Decimal("0.00"),
            cerrado=True,
        )
        with self.assertRaises(ValidationError):
            annul_central_cash_movement(movement=egreso, motivo="Mes cerrado", actor=self.admin)

    # --- permiso ---------------------------------------------------------

    def test_anular_requiere_permiso_propio(self):
        egreso = self._egreso()
        with self.assertRaises(PermissionDenied):
            annul_central_cash_movement(movement=egreso, motivo="Sin permiso", actor=self.sin_permiso)

        UserPermission.objects.create(
            user=self.sin_permiso,
            module=PermissionModule.TREASURY_MOV_DELETE,
            can_read=True,
            can_write=True,
        )
        annul_central_cash_movement(movement=egreso, motivo="Con permiso", actor=self.sin_permiso)
        egreso.refresh_from_db()
        self.assertEqual(egreso.estado, MovimientoCajaCentral.Estado.ANULADO)

    # --- que el anulado salga de los reportes ----------------------------

    def test_el_gasto_anulado_sale_de_la_lectura_economica(self):
        self._fondear()
        egreso = self._egreso("250.00")
        primer_mes = self.hoy.replace(day=1)

        antes = build_economic_period_snapshot(date_from=primer_mes, date_to=self.hoy)
        annul_central_cash_movement(movement=egreso, motivo="Duplicado", actor=self.admin)
        despues = build_economic_period_snapshot(date_from=primer_mes, date_to=self.hoy)

        self.assertEqual(
            antes["treasury_expense_total"] - despues["treasury_expense_total"],
            Decimal("250.00"),
        )

    def test_el_gasto_anulado_sale_de_los_pendientes_de_imputar(self):
        """Un egreso sin imputar que se anula no puede seguir pidiendo accion."""
        self._fondear()
        sin_imputar = MovimientoCajaCentral.objects.create(
            caja_central=get_boveda(self.empresa),
            fecha=self.hoy,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("55.00"),
            concepto="Egreso sin imputar",
            creado_por=self.admin,
        )
        primer_mes = self.hoy.replace(day=1)
        antes = build_economic_period_snapshot(date_from=primer_mes, date_to=self.hoy)
        self.assertEqual(antes["treasury_unmapped_expenses_total"], Decimal("55.00"))

        annul_central_cash_movement(movement=sin_imputar, motivo="Nunca existio", actor=self.admin)

        despues = build_economic_period_snapshot(date_from=primer_mes, date_to=self.hoy)
        self.assertEqual(despues["treasury_unmapped_expenses_total"], Decimal("0.00"))


class AnularPagoDevuelveElEfectivoTests(TestCase):
    """El agujero que existia antes de este slice.

    annul_payment liberaba el movimiento BANCARIO del pago pero no tocaba el
    EGRESO_PAGO de la boveda: la deuda volvia a quedar pendiente y la plata no
    volvia a la caja fuerte. No habia ningun test que lo cubriera porque el unico
    test de anulacion de pago usaba TRANSFERENCIA.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-pago", password="test", email="p@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Pago Efectivo")
        self.sucursal = Sucursal.objects.create(
            codigo="PEF", nombre="Sucursal Pago", razon_social="Pago", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro Pago")
        self.proveedor = Proveedor.objects.create(razon_social="Proveedor Pago", creado_por=self.admin)
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria Pago", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.hoy = timezone.localdate()

    def test_anular_un_pago_en_efectivo_devuelve_la_plata_a_la_boveda(self):
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Fondeo",
            fecha=self.hoy,
            actor=self.admin,
        )
        payable = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura a anular",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("400.00"),
            saldo_pendiente=Decimal("400.00"),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )
        pago = register_cash_payment(
            payable=payable, fecha_pago=self.hoy, monto=Decimal("400.00"), actor=self.admin
        )
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("600.00"))

        annul_payment(payment=pago, motivo="Pago mal cargado", actor=self.admin)

        # La deuda vuelve a estar pendiente...
        payable.refresh_from_db()
        self.assertEqual(payable.saldo_pendiente, Decimal("400.00"))
        # ...y la plata vuelve a la boveda. Esto fallaba antes del slice.
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("1000.00"))
        egreso = MovimientoCajaCentral.objects.get(pago_tesoreria=pago)
        self.assertEqual(egreso.estado, MovimientoCajaCentral.Estado.ANULADO)
        self.assertIn(f"Anulacion del pago #{pago.pk}", egreso.motivo_anulacion)
