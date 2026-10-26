"""EP-14 D11: quien administra asistencia elige el local al fichar.

Un admin sin sucursal base no podia fichar ("no tenes un local asignado").
Riesgos que cubre:
- que un admin no pueda fichar, o que la marca quede en otro local que el elegido;
- que se elija un local de una empresa no permitida, no seleccionada o inactivo;
- que un cajero cambie su local (tiene que seguir fijo en su sucursal base).
"""

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from .fichaje import Resultado, fichar
from .models import Marca
from .permissions import can_fichar
from .tests_fichaje import FichajeTestCase
from .views import SESION_LOCAL_FICHAJE


class ElegirLocalAlFicharTests(FichajeTestCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(can_fichar(self.admin))
        self.assertIsNone(self.admin.sucursal_base)

    def _elegir(self, local):
        return self.client.post(reverse("asistencia:fichar"), {"elegir_local": "1", "local": local})

    def _fichar_por_pantalla(self, **extra):
        return self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet}, **extra)

    def test_un_admin_sin_sucursal_base_ve_el_selector_y_no_el_formulario(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:fichar"))
        self.assertContains(response, 'id="fichar-local"')
        self.assertEqual(set(response.context["locales"]), {self.centro, self.terminal})
        self.assertNotContains(response, 'name="codigo"')
        self.assertContains(response, "Elegí arriba el local")

    def test_elige_el_local_y_la_marca_queda_ahi(self):
        self._login(self.admin, [self.armadi])
        self.assertRedirects(self._elegir(self.terminal.pk), reverse("asistencia:fichar"))
        self.assertEqual(self.client.session[SESION_LOCAL_FICHAJE], self.terminal.pk)
        response = self._fichar_por_pantalla()
        self.assertContains(response, 'data-resultado="registrada"')
        marca = Marca.objects.get()
        self.assertEqual(marca.sucursal, self.terminal)
        self.assertEqual(marca.registrada_por, self.admin)

    def test_la_camara_usa_el_local_elegido(self):
        self._login(self.admin, [self.armadi])
        self._elegir(self.centro.pk)
        datos = self._fichar_por_pantalla(HTTP_ACCEPT="application/json").json()
        self.assertTrue(datos["registrada"])
        self.assertEqual(Marca.objects.get().sucursal, self.centro)

    def test_no_se_puede_elegir_un_local_fuera_del_alcance(self):
        self._login(self.admin, [self.armadi])
        for local in (self.vivre.pk, self.sucursal_ajena.pk, self.sin_empresa.pk, "abc", "", "1²"):
            with self.subTest(local=local):
                self._elegir(local)
                self.assertNotIn(SESION_LOCAL_FICHAJE, self.client.session)

    def test_sin_local_elegido_no_registra(self):
        self._login(self.admin, [self.armadi])
        response = self._fichar_por_pantalla()
        self.assertContains(response, 'data-resultado="sin_local"')
        self.assertFalse(Marca.objects.exists())

    def test_un_admin_con_sucursal_base_arranca_en_esa(self):
        self.admin.sucursal_base = self.centro
        self.admin.save(update_fields=["sucursal_base"])
        self._login(self.admin, [self.armadi])
        self._fichar_por_pantalla()
        self.assertEqual(Marca.objects.get().sucursal, self.centro)

    def test_si_el_local_elegido_deja_de_estar_en_el_alcance_no_se_usa(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        self._elegir(self.vivre.pk)
        session = self.client.session
        session["empresa_ids"] = [self.armadi.pk]
        session.save()
        self._fichar_por_pantalla()
        self.assertFalse(Marca.objects.exists())

    def test_el_cajero_sigue_fijo_en_su_sucursal_base(self):
        self._login(self.cajero, [self.armadi])
        response = self.client.get(reverse("asistencia:fichar"))
        self.assertNotContains(response, 'id="fichar-local"')
        self.assertEqual(self._elegir(self.centro.pk).status_code, 403)
        self._fichar_por_pantalla()
        self.assertEqual(Marca.objects.get().sucursal, self.terminal)

    def test_el_servicio_rechaza_un_local_elegido_sin_permiso(self):
        inactiva = type(self.centro).objects.create(
            empresa=self.armadi, nombre="Cerrada", codigo="CER-09", razon_social="Armadi SRL", activa=False
        )
        casos = (
            (self.cajero, self.centro),  # un cajero no elige
            (self.admin, self.sucursal_ajena),  # empresa no permitida
            (self.admin, inactiva),  # local inactivo
        )
        for actor, sucursal in casos:
            with self.subTest(actor=actor.username, sucursal=sucursal.codigo):
                with self.assertRaises(PermissionDenied):
                    fichar(actor=actor, codigo=self.empleado.codigo_carnet, sucursal=sucursal)
        self.assertFalse(Marca.objects.exists())

    def test_el_servicio_acepta_el_local_elegido_por_quien_administra(self):
        fichaje = fichar(actor=self.admin, codigo=self.empleado.codigo_carnet, sucursal=self.vivre)
        self.assertEqual(fichaje.resultado, Resultado.REGISTRADA)
        self.assertEqual(fichaje.marca.sucursal, self.vivre)
