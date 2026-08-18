"""US-3.18: poder llegar a todos los movimientos del mes en el libro de efectivo.

Caso real: tesoreria tenia que anular 7 egresos administrativos del 02, 03 y 05
de junio. Filtro junio y solo le aparecieron los del 26 en adelante. El listado
cortaba en 100 y ordena por fecha descendente, asi que en un mes con mas de 100
movimientos los primeros dias eran INALCANZABLES desde la pantalla: no hay
paginacion, y el boton de anular vive en cada fila del listado.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import CajaCentral, MovimientoCajaCentral
from users.models import PermissionModule, UserPermission

User = get_user_model()


class AlcanceDelLibroDeEfectivoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-libro", password="test", email="libro@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa LIBRO")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            codigo="LB1", nombre="Sucursal LIBRO", razon_social="LIBRO", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro LIBRO")
        self.boveda = CajaCentral.objects.create(
            nombre="Boveda LIBRO", empresa=self.empresa
        )
        UserPermission.objects.create(
            user=self.admin,
            module=PermissionModule.TREASURY_MOV_DELETE,
            can_read=True,
            can_write=True,
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def _egreso(self, dia, concepto):
        return MovimientoCajaCentral.objects.create(
            caja_central=self.boveda,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            fecha=date(2026, 6, dia),
            monto=Decimal("1000.00"),
            concepto=concepto,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=date(2026, 6, 1),
            creado_por=self.admin,
        )

    def test_el_movimiento_del_primer_dia_del_mes_se_alcanza(self):
        """Con mas de 100 movimientos en el mes, el del dia 2 tiene que estar
        igual: es el que tesoreria necesitaba anular y no encontraba."""
        viejo = self._egreso(2, "EGRESO DEL DOS DE JUNIO")
        for i in range(120):
            self._egreso(26, f"relleno {i}")

        response = self.client.get(
            reverse("treasury:central_cash_list"), {"year": 2026, "month": 6}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EGRESO DEL DOS DE JUNIO")
        # Y con su boton de anular, que es a lo que no podia llegar.
        self.assertContains(
            response, reverse("treasury:central_cash_annul_confirm", args=[viejo.pk])
        )

    def test_avisa_si_el_tope_de_seguridad_actua(self):
        """El tope alto sigue existiendo como freno, pero cuando corta lo dice."""
        self._egreso(2, "EGRESO DEL DOS DE JUNIO")
        for i in range(120):
            self._egreso(26, f"relleno {i}")

        response = self.client.get(
            reverse("treasury:central_cash_list"), {"year": 2026, "month": 6}
        )

        # Con 121 movimientos no corta nada, asi que no debe aparecer el aviso.
        self.assertNotContains(response, "se muestran los")
