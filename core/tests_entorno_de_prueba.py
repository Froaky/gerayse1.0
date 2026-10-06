"""Cartel de entorno de prueba: solo en staging, en el ingreso y en las shells de
cajas y tesoreria, para que nadie trabaje en la copia creyendo que es el sistema real."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from cashops.models import Empresa
from users.models import Role

User = get_user_model()
MARCA = 'class="staging-banner"'


class CartelEntornoDePruebaTests(TestCase):
    def setUp(self):
        rol = Role.objects.create(code="ADMIN", name="Administrador")
        empresa = Empresa.objects.create(nombre="Empresa Prueba SA")
        self.admin = User.objects.create_user(username="admin_prueba", password="test", role=rol)
        self.admin.empresas_permitidas.set([empresa])

    def _html(self, url_name):
        respuesta = self.client.get(reverse(url_name))
        self.assertEqual(respuesta.status_code, 200, url_name)
        return respuesta.content.decode()

    @override_settings(ENTORNO_DE_PRUEBA=True)
    def test_en_staging_se_ve_al_ingresar_en_cajas_y_en_tesoreria(self):
        html = self._html("users:login")
        self.assertIn(MARCA, html)
        self.assertIn("<title>[PRUEBA]", html)
        self.client.force_login(self.admin)
        for url_name in ("cashops:dashboard", "treasury:dashboard"):
            html = self._html(url_name)
            self.assertIn(MARCA, html, url_name)
            self.assertIn("www.gerayse.com.ar", html, url_name)
            self.assertIn("<title>[PRUEBA]", html, url_name)

    def test_fuera_de_staging_no_aparece(self):
        self.assertNotIn(MARCA, self._html("users:login"))
        self.client.force_login(self.admin)
        for url_name in ("cashops:dashboard", "treasury:dashboard"):
            html = self._html(url_name)
            self.assertNotIn(MARCA, html, url_name)
            self.assertNotIn("[PRUEBA]", html, url_name)
