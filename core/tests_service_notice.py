"""Aviso de vencimiento del servicio: cuando sale, con que tono y quien lo ve.

El servicio vence el 9 de cada mes. Es un cartel de presentacion, sin reglas de
dinero. Lo que si se puede romper en silencio y por eso se fija aca:

1. La ventana: aparece cuando faltan 7 dias (el 2), se pone rojo cuando faltan 3
   (el 6), y desaparece pasado el 9 hasta el mes siguiente. Un error de un dia
   en el calculo no lo nota nadie hasta que el cartel sale un dia de mas o de
   menos frente al cliente.
2. Que lo vea solo el administrador: si le aparece a un operador se alarma a
   gente que no decide el pago.
3. Que el texto no diga cosas falsas ("Quedan 1 dias", un mes en mayuscula) ni
   cosas que no van (nombres propios, "base de datos", voseo).
"""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from cashops.models import Empresa
from core.service_notice import build_service_notice, proximo_vencimiento
from users.models import Role

User = get_user_model()
MARCA = "data-service-notice"


def _con_fecha(hoy):
    """Fija la fecha de hoy que ve el aviso, sin tocar el resto del sistema."""
    return mock.patch("core.service_notice.timezone.localdate", return_value=hoy)


class VentanaDelAvisoTests(SimpleTestCase):
    def _aviso(self, hoy, dia=9):
        return build_service_notice(hoy=hoy, dia_vencimiento=dia)

    def test_faltando_mas_de_siete_dias_no_hay_cartel(self):
        self.assertIsNone(self._aviso(date(2026, 9, 1)))

    def test_el_dia_2_arranca_en_advertencia(self):
        aviso = self._aviso(date(2026, 9, 2))
        self.assertEqual(aviso["nivel"], "warning")
        self.assertEqual(aviso["dias"], 7)
        self.assertEqual(
            aviso["texto"],
            "El servicio de alojamiento de Gerayse vence el 9 de septiembre de 2026. Quedan 7 días. "
            "Para evitar la interrupción del sistema, regularice el saldo del mes antes de esa fecha.",
        )

    def test_el_dia_5_sigue_en_advertencia(self):
        aviso = self._aviso(date(2026, 9, 5))
        self.assertEqual(aviso["nivel"], "warning")
        self.assertIn("Quedan 4 días", aviso["texto"])

    def test_el_dia_6_se_pone_rojo(self):
        aviso = self._aviso(date(2026, 9, 6))
        self.assertEqual(aviso["nivel"], "danger")
        self.assertIn("Quedan 3 días", aviso["texto"])

    def test_un_solo_dia_va_en_singular(self):
        self.assertIn("Queda 1 día.", self._aviso(date(2026, 9, 8))["texto"])

    def test_el_dia_del_vencimiento_es_rojo_y_dice_hoy(self):
        aviso = self._aviso(date(2026, 9, 9))
        self.assertEqual(aviso["nivel"], "danger")
        self.assertEqual(aviso["dias"], 0)
        self.assertIn("vence hoy, 9 de septiembre de 2026", aviso["texto"])
        self.assertIn("regularice el saldo del mes", aviso["texto"])

    def test_pasado_el_vencimiento_desaparece_hasta_el_mes_siguiente(self):
        self.assertIsNone(self._aviso(date(2026, 9, 10)))
        self.assertIsNone(self._aviso(date(2026, 9, 25)))
        self.assertIsNone(self._aviso(date(2026, 10, 1)))
        self.assertEqual(self._aviso(date(2026, 10, 2))["vence"], date(2026, 10, 9))

    def test_vuelve_todos_los_meses_con_la_fecha_del_mes(self):
        self.assertIn("vence el 9 de octubre de 2026", self._aviso(date(2026, 10, 4))["texto"])
        self.assertIn("vence el 9 de enero de 2027", self._aviso(date(2027, 1, 3))["texto"])

    def test_cruce_de_anio(self):
        self.assertEqual(proximo_vencimiento(date(2026, 12, 31), 9), date(2027, 1, 9))
        self.assertIsNone(self._aviso(date(2026, 12, 31)))

    def test_un_dia_que_el_mes_no_tiene_se_corre_al_ultimo_dia(self):
        # Vencimiento "el 31": en febrero cae el 28.
        self.assertEqual(proximo_vencimiento(date(2027, 2, 10), 31), date(2027, 2, 28))
        aviso = self._aviso(date(2027, 2, 25), dia=31)
        self.assertEqual(aviso["nivel"], "danger")
        self.assertIn("vence el 28 de febrero de 2027", aviso["texto"])

    def test_dia_cero_o_invalido_apaga_el_cartel(self):
        for dia in (0, -1, 32, "nueve", "  "):
            self.assertIsNone(self._aviso(date(2026, 9, 5), dia=dia), dia)

    def test_es_un_aviso_formal_sin_nombres_ni_amenazas_sobre_los_datos(self):
        for hoy in (date(2026, 9, 2), date(2026, 9, 5), date(2026, 9, 6), date(2026, 9, 8), date(2026, 9, 9)):
            texto = self._aviso(hoy)["texto"].lower()
            self.assertNotIn("base de datos", texto, hoy)
            self.assertNotIn("mateo", texto, hoy)
            self.assertNotIn("coordiná", texto, hoy)  # trato de usted, no voseo
            self.assertNotIn("venció", texto, hoy)    # nunca se afirma que vencio
            self.assertIn("regularice", texto, hoy)


class QuienVeElAvisoTests(TestCase):
    def setUp(self):
        admin_rol = Role.objects.create(code="ADMIN", name="Administrador")
        operador_rol = Role.objects.create(code="OPERADOR", name="Operador")
        self.empresa = Empresa.objects.create(nombre="Empresa Aviso SA")
        self.admin = User.objects.create_user(username="admin_aviso", password="test", role=admin_rol)
        self.operador = User.objects.create_user(username="oper_aviso", password="test", role=operador_rol)
        for usuario in (self.admin, self.operador):
            usuario.empresas_permitidas.set([self.empresa])

    def _html(self, url_name):
        respuesta = self.client.get(reverse(url_name))
        self.assertEqual(respuesta.status_code, 200, url_name)
        return respuesta.content.decode()

    def test_el_administrador_lo_ve_en_cajas_y_en_tesoreria(self):
        self.client.force_login(self.admin)
        with _con_fecha(date(2026, 9, 3)):
            for url_name in ("cashops:dashboard", "treasury:dashboard"):
                html = self._html(url_name)
                self.assertIn(MARCA, html, url_name)
                self.assertIn("service-notice--warning", html, url_name)
                self.assertIn("Quedan 6 días", html, url_name)
                self.assertIn("regularice el saldo del mes", html, url_name)

    def test_en_los_ultimos_tres_dias_sale_en_rojo(self):
        self.client.force_login(self.admin)
        with _con_fecha(date(2026, 9, 7)):
            html = self._html("cashops:dashboard")
        self.assertIn("service-notice--danger", html)
        self.assertIn("Quedan 2 días", html)

    def test_fuera_de_la_ventana_no_lo_ve_ni_el_administrador(self):
        self.client.force_login(self.admin)
        with _con_fecha(date(2026, 9, 15)):
            self.assertNotIn(MARCA, self._html("cashops:dashboard"))

    def test_un_superusuario_sin_rol_tambien_lo_ve(self):
        root = User.objects.create_superuser(username="root_aviso", password="test", email="root@example.com")
        root.empresas_permitidas.set([self.empresa])
        self.client.force_login(root)
        with _con_fecha(date(2026, 9, 3)):
            self.assertIn(MARCA, self._html("cashops:dashboard"))

    def test_un_operador_no_lo_ve(self):
        self.client.force_login(self.operador)
        with _con_fecha(date(2026, 9, 3)):
            self.assertNotIn(MARCA, self._html("cashops:dashboard"))

    def test_la_pantalla_publica_no_lo_muestra(self):
        with _con_fecha(date(2026, 9, 3)):
            self.assertNotIn(MARCA, self._html("home"))

    def test_se_puede_apagar_por_entorno(self):
        self.client.force_login(self.admin)
        with _con_fecha(date(2026, 9, 3)), override_settings(SERVICE_NOTICE_DUE_DAY=0):
            self.assertNotIn(MARCA, self._html("cashops:dashboard"))
