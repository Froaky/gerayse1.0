"""Un cajero fijo que trabaja en mas de un local (pedido de tesoreria del 13/11).

Con "Otros locales donde trabaja" tildados en su ficha, abre caja y ficha en
cualquiera de ellos ademas de su base; en un local que no tiene, no. La deuda
cargada desde una caja abierta en otro local puede ir a ese local.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from cashops.forms import BoxEditForm, CajaAperturaForm
from cashops.models import Caja, Sucursal
from cashops.services import open_box
from cashops.tests import CashopsTestCase
from users.models import Role, User


class CajeroEnOtrosLocalesTests(CashopsTestCase):
    def setUp(self):
        super().setUp()
        self.branch_a2 = Sucursal.objects.create(
            codigo="SUC-A2", nombre="Sucursal A2", razon_social="ARMADI SRL", empresa=self.empresa_a
        )
        self.cajero = User.objects.create_user(
            username="cajero-dos-locales",
            password="test",
            role=Role.objects.get(code="CAJERO"),
            usuario_fijo=True,
            sucursal_base=self.branch_a,
        )
        self.cajero.empresas_permitidas.set([self.empresa_a])

    def _abrir(self, sucursal):
        return open_box(
            user=self.cajero,
            turno=self.turno_a,
            sucursal=sucursal,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("0.00"),
            actor=self.cajero,
        )

    def test_abre_caja_en_otro_local_suyo(self):
        self.cajero.sucursales_asistencia.set([self.branch_a2])

        caja = self._abrir(self.branch_a2)

        self.assertEqual(caja.sucursal, self.branch_a2)

    def test_sigue_abriendo_en_su_base(self):
        self.cajero.sucursales_asistencia.set([self.branch_a2])

        self.assertEqual(self._abrir(self.branch_a).sucursal, self.branch_a)

    def test_no_abre_caja_en_un_local_que_no_tiene(self):
        with self.assertRaises(ValidationError) as ctx:
            self._abrir(self.branch_a2)

        self.assertIn("sucursal", ctx.exception.message_dict)
        self.assertFalse(Caja.objects.filter(sucursal=self.branch_a2).exists())

    def test_la_pantalla_ofrece_solo_sus_locales_y_arranca_en_la_base(self):
        self.client.force_login(self.cajero)

        sin_otros = self.client.get(reverse("cashops:box_open")).context["form"].fields["sucursal"]
        self.assertEqual(list(sin_otros.queryset), [self.branch_a])

        self.cajero.sucursales_asistencia.set([self.branch_a2])
        campo = self.client.get(reverse("cashops:box_open")).context["form"].fields["sucursal"]
        self.assertEqual(set(campo.queryset), {self.branch_a, self.branch_a2})
        self.assertEqual(campo.initial, self.branch_a.pk)

    def test_la_pantalla_abre_la_caja_en_el_otro_local(self):
        self.cajero.sucursales_asistencia.set([self.branch_a2])
        self.client.force_login(self.cajero)

        response = self.client.post(
            reverse("cashops:box_open"),
            {
                "usuario": self.cajero.pk,
                "sucursal": self.branch_a2.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": self.fecha_op.isoformat(),
                "efectivo_inicial": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Caja.objects.get(usuario=self.cajero).sucursal, self.branch_a2)

    def test_la_pantalla_rechaza_un_local_que_no_tiene(self):
        self.client.force_login(self.cajero)

        response = self.client.post(
            reverse("cashops:box_open"),
            {
                "usuario": self.cajero.pk,
                "sucursal": self.branch_a2.pk,
                "turno": self.turno_a.pk,
                "fecha_operativa": self.fecha_op.isoformat(),
                "efectivo_inicial": "0.00",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Caja.objects.filter(usuario=self.cajero).exists())

    def test_un_local_de_otra_empresa_no_aparece_aunque_este_tildado(self):
        self.cajero.sucursales_asistencia.set([self.branch_a2, self.branch_b])

        form = CajaAperturaForm(actor=self.cajero, empresa_ids=[self.empresa_a.pk])

        self.assertEqual(set(form.fields["sucursal"].queryset), {self.branch_a, self.branch_a2})

    def test_editar_la_caja_permite_pasarla_a_otro_local_suyo(self):
        caja = self._abrir(self.branch_a)
        datos = {
            "usuario": self.cajero.pk,
            "sucursal": self.branch_a2.pk,
            "turno": self.turno_a.pk,
            "fecha_operativa": self.fecha_op.isoformat(),
            "monto_inicial": "0.00",
            "motivo": "Se abrio en el local equivocado",
        }

        sin_otros = BoxEditForm(datos, box=caja, actor=self.cajero, empresa_ids=[self.empresa_a.pk])
        self.assertFalse(sin_otros.is_valid())
        self.assertIn("sucursal", sin_otros.errors)

        self.cajero.sucursales_asistencia.set([self.branch_a2])
        con_otros = BoxEditForm(datos, box=caja, actor=self.cajero, empresa_ids=[self.empresa_a.pk])
        self.assertTrue(con_otros.is_valid(), con_otros.errors)

    def test_la_deuda_de_una_caja_en_otro_local_ofrece_ese_local(self):
        self.cajero.sucursales_asistencia.set([self.branch_a2])
        caja = self._abrir(self.branch_a2)
        self.client.force_login(self.cajero)

        response = self.client.get(reverse("cashops:box_expense_debt", args=[caja.pk]))

        campo = response.context["form"].fields["sucursal"]
        self.assertIn(self.branch_a2, set(campo.queryset))
        self.assertEqual(response.context["form"].initial["sucursal"], self.branch_a2.pk)
