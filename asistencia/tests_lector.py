"""EP-14 US-14.8: fichar con un lector QR USB.

El lector se comporta como un teclado: escribe el codigo del carnet y Enter en
el campo que tenga el foco. Riesgos que cubre:
- que el Enter o el salto de linea del lector, o el Bloq Mayus, rompan el codigo;
- que despues de una lectura el campo no quede listo para el siguiente carnet;
- que el codigo use caracteres que cambian segun la distribucion del teclado.
"""

import re

from django.urls import reverse

from .models import CODIGO_CARNET_PATRON, Marca, nuevo_codigo_carnet
from .tests_fichaje import FichajeTestCase


class LectorUSBTests(FichajeTestCase):
    def test_el_codigo_con_el_enter_del_lector_registra(self):
        self._login(self.cajero)
        for sufijo in ("\r", "\n", "\r\n", "\t"):
            with self.subTest(sufijo=repr(sufijo)):
                Marca.objects.all().delete()
                response = self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet + sufijo})
                self.assertContains(response, 'data-resultado="registrada"')

    def test_con_bloq_mayus_el_lector_escribe_en_minusculas_y_registra(self):
        self._login(self.cajero)
        response = self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet.lower()})
        self.assertContains(response, 'data-resultado="registrada"')

    def test_despues_de_cada_lectura_el_campo_queda_vacio_y_con_el_foco(self):
        self._login(self.cajero)
        response = self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet})
        campo = re.search(r'<input id="id_codigo"[^>]*>', response.content.decode())
        self.assertIsNotNone(campo)
        self.assertIn("autofocus", campo.group(0))
        self.assertNotIn("value=", campo.group(0))

    def test_el_codigo_solo_usa_digitos_que_no_cambian_con_el_teclado(self):
        # Solo 0-9: iguales en teclado espanol, latinoamericano e ingles.
        for _ in range(50):
            codigo = nuevo_codigo_carnet()
            self.assertIsNotNone(CODIGO_CARNET_PATRON.fullmatch(codigo), codigo)
            self.assertTrue(set(codigo) <= set("0123456789"))
