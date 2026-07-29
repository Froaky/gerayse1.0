from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from users.models import Role

from cashops.models import RubroOperativo
from treasury.models import CuentaBancaria, CuentaPorPagar, MovimientoBancario
from treasury.services import (
    create_payable_category,
    create_supplier,
    register_payable,
    register_transfer_payment,
)


User = get_user_model()


class ReporteSinSucursalCommandTests(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(
            username="admin-reporte", password="test", role=self.admin_role
        )
        self.rubro = RubroOperativo.objects.create(nombre="Servicios")
        self.category = create_payable_category(
            nombre="Servicios", rubro_operativo=self.rubro, actor=self.admin
        )
        self.supplier = create_supplier(razon_social="Proveedor Sin Sucursal", actor=self.admin)

    def _run(self, **options):
        out = StringIO()
        call_command("reporte_sin_sucursal", stdout=out, **options)
        return out.getvalue()

    def _crear_deuda_sin_sucursal(self):
        return register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura comun",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )

    def _cuenta(self):
        if not hasattr(self, "_cuenta_cache"):
            self._cuenta_cache = CuentaBancaria.objects.create(
                nombre="Cuenta Test", banco="Banco Test", tipo_cuenta="CC", numero_cuenta="123"
            )
        return self._cuenta_cache

    def _crear_gasto_banco_sin_sucursal(self, monto):
        return MovimientoBancario.objects.create(
            cuenta_bancaria=self._cuenta(),
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.IMPUESTO,
            estado=MovimientoBancario.Estado.REGISTRADO,
            fecha=timezone.localdate(),
            monto=monto,
            concepto="AFIP",
            sucursal_gasto=None,
        )

    def test_base_vacia_reporta_cero_y_sin_modificar(self):
        output = self._run()
        self.assertIn("Deudas sin sucursal: 0", output)
        self.assertIn("CUENTAS DE BANCO SIN EMPRESA ASIGNADA: 0", output)
        self.assertIn("GASTOS DEL BANCO QUE NO SE CUENTAN EN LA RENTABILIDAD: 0", output)
        self.assertIn("Ningun dato fue modificado", output)

    def test_reporta_deuda_sin_sucursal(self):
        self._crear_deuda_sin_sucursal()
        output = self._run()
        self.assertIn("Deudas sin sucursal: 1", output)

    def test_reporta_gastos_banco_con_total_e_importe_formateado(self):
        self._crear_gasto_banco_sin_sucursal(Decimal("64500.00"))
        self._crear_gasto_banco_sin_sucursal(Decimal("500.00"))

        output = self._run()

        self.assertIn("GASTOS DEL BANCO QUE NO SE CUENTAN EN LA RENTABILIDAD: 2", output)
        # Total y montos en formato argentino ($ 65.000,00 / $ 64.500,00).
        self.assertIn("$ 65.000,00", output)
        self.assertIn("$ 64.500,00", output)
        # Les falta todo (rubro, sucursal y periodo).
        self.assertIn("los 3", output)
        # El cuadre debe cerrar.
        self.assertIn("CUADRA", output)

    def test_muestra_todas_las_filas_por_defecto(self):
        for i in range(25):
            self._crear_gasto_banco_sin_sucursal(Decimal("10.00") + i)

        output = self._run()

        self.assertIn("GASTOS DEL BANCO QUE NO SE CUENTAN EN LA RENTABILIDAD: 25", output)
        # Sin --max no debe truncar.
        self.assertNotIn("fila(s) mas", output)

    def test_no_modifica_datos(self):
        self._crear_deuda_sin_sucursal()
        before = list(
            CuentaPorPagar.objects.values_list("pk", "sucursal_id", "saldo_pendiente")
        )

        self._run()

        after = list(
            CuentaPorPagar.objects.values_list("pk", "sucursal_id", "saldo_pendiente")
        )
        self.assertEqual(before, after)


class DesgloseDeudaCommandTests(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(
            username="admin-desglose", password="test", role=self.admin_role
        )
        self.rubro = RubroOperativo.objects.create(nombre="Insumos")
        self.category = create_payable_category(
            nombre="Insumos", rubro_operativo=self.rubro, actor=self.admin
        )
        self.supplier = create_supplier(razon_social="Proveedor Desglose", actor=self.admin)
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta Test", banco="Banco Test", tipo_cuenta="CC", numero_cuenta="999"
        )

    def _run(self, **options):
        out = StringIO()
        call_command("desglose_deuda", stdout=out, **options)
        return out.getvalue()

    def _crear_deuda(self, importe="100.00"):
        return register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def test_base_vacia_muestra_titulares_y_no_modifica(self):
        output = self._run()
        self.assertIn("DEUDA VIVA", output)
        self.assertIn("DEVENGADO", output)
        self.assertIn("Ningun dato fue modificado", output)

    def test_deuda_pendiente_completa_suma_viva_y_devengado(self):
        self._crear_deuda("100.00")

        output = self._run()

        # deuda viva == importe == devengado == 100 (nada pagado)
        self.assertIn("$100,00", output)
        self.assertIn("Proveedor Desglose", output)
        self.assertIn("Insumos", output)

    def test_deuda_parcial_separa_viva_de_devengado(self):
        deuda = self._crear_deuda("100.00")
        register_transfer_payment(
            payable=deuda,
            bank_account=self.cuenta,
            fecha_pago=timezone.localdate(),
            monto=Decimal("30.00"),
            actor=self.admin,
        )
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(deuda.saldo_pendiente, Decimal("70.00"))

        output = self._run()

        # deuda viva = 70 (lo que falta), devengado / importe factura = 100
        self.assertIn("$70,00", output)
        self.assertIn("$100,00", output)
        # la diferencia ya pagada (30) se explicita
        self.assertIn("$30,00", output)

    def test_deuda_anulada_no_cuenta_en_viva(self):
        from treasury.services import annul_payable

        deuda = self._crear_deuda("500.00")
        annul_payable(payable=deuda, motivo="Cargada por error", actor=self.admin)

        output = self._run()

        # No debe figurar como deuda viva; el diagnostico la lista como anulada.
        self.assertIn("Deudas ANULADAS", output)

    def test_no_modifica_datos(self):
        self._crear_deuda("100.00")
        before = list(
            CuentaPorPagar.objects.values_list("pk", "estado", "saldo_pendiente")
        )

        self._run()

        after = list(
            CuentaPorPagar.objects.values_list("pk", "estado", "saldo_pendiente")
        )
        self.assertEqual(before, after)
