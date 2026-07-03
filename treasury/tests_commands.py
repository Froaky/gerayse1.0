from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from users.models import Role

from cashops.models import RubroOperativo
from treasury.models import CuentaPorPagar
from treasury.services import create_payable_category, create_supplier, register_payable


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

    def test_reporta_deuda_sin_sucursal(self):
        register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura comun",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )

        output = self._run()

        self.assertIn("CuentaPorPagar (deudas) sin sucursal: 1", output)
        self.assertIn("Ningun dato fue modificado", output)

    def test_no_modifica_datos(self):
        register_payable(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Factura comun",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            importe_total=Decimal("100.00"),
            actor=self.admin,
        )
        before = list(
            CuentaPorPagar.objects.values_list("pk", "sucursal_id", "saldo_pendiente")
        )

        self._run()

        after = list(
            CuentaPorPagar.objects.values_list("pk", "sucursal_id", "saldo_pendiente")
        )
        self.assertEqual(before, after)

    def test_base_vacia_reporta_cero(self):
        output = self._run()
        self.assertIn("CuentaPorPagar (deudas) sin sucursal: 0", output)
