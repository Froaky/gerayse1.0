"""Aviso de mantenimiento programado: aparece antes y durante la ventana, en el
ingreso y en cajas y tesoreria, y desaparece solo cuando termina."""

from datetime import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa
from core.mantenimiento import aviso_de_mantenimiento
from users.models import Role

User = get_user_model()
VENTANA = {"MANTENIMIENTO_DESDE": "2026-10-07 21:15", "MANTENIMIENTO_HASTA": "2026-10-07 22:00"}
MARCA = 'class="maintenance-banner"'


def _a_las(hora, minuto):
    return timezone.make_aware(datetime(2026, 10, 7, hora, minuto))


@override_settings(**VENTANA)
class VentanaDeMantenimientoTests(SimpleTestCase):
    def test_antes_de_la_ventana_avisa_el_horario(self):
        aviso = aviso_de_mantenimiento(ahora=_a_las(20, 50))
        self.assertFalse(aviso["en_curso"])
        self.assertEqual(timezone.localtime(aviso["desde"]).hour, 21)

    def test_durante_la_ventana_esta_en_curso(self):
        self.assertTrue(aviso_de_mantenimiento(ahora=_a_las(21, 30))["en_curso"])

    def test_al_terminar_desaparece_solo(self):
        self.assertIsNone(aviso_de_mantenimiento(ahora=_a_las(22, 0)))

    @override_settings(MANTENIMIENTO_HASTA="")
    def test_sin_configurar_no_hay_cartel(self):
        self.assertIsNone(aviso_de_mantenimiento(ahora=_a_las(21, 30)))

    @override_settings(MANTENIMIENTO_HASTA="mañana")
    def test_un_valor_ilegible_no_rompe_nada(self):
        self.assertIsNone(aviso_de_mantenimiento(ahora=_a_las(21, 30)))


@override_settings(**VENTANA)
class QuienVeElAvisoDeMantenimientoTests(TestCase):
    def setUp(self):
        rol = Role.objects.create(code="ADMIN", name="Administrador")
        empresa = Empresa.objects.create(nombre="Empresa Mant SA")
        self.admin = User.objects.create_user(username="admin_mant", password="test", role=rol)
        self.admin.empresas_permitidas.set([empresa])

    def _html(self, url_name, ahora):
        with mock.patch("core.mantenimiento.timezone.now", return_value=ahora):
            respuesta = self.client.get(reverse(url_name))
        self.assertEqual(respuesta.status_code, 200, url_name)
        return respuesta.content.decode()

    def test_lo_ven_todos_antes_y_durante_y_despues_ya_no(self):
        html = self._html("users:login", _a_las(20, 50))
        self.assertIn(MARCA, html)
        self.assertIn("de 21:15 a 22:00", html)
        self.client.force_login(self.admin)
        for url_name in ("cashops:dashboard", "treasury:dashboard"):
            self.assertIn("Sistema en mantenimiento", self._html(url_name, _a_las(21, 30)), url_name)
            self.assertNotIn(MARCA, self._html(url_name, _a_las(22, 1)), url_name)
