"""EP-14 D16: carnet para el celular (mandar por WhatsApp y mostrar en la pantalla).

Riesgos que cubre:
- que el QR lleve otra cosa que el codigo vigente del carnet;
- que falte el codigo legible para tipear;
- que se pueda bajar el carnet de un empleado de otra empresa o dado de baja;
- que un nombre larguisimo se salga de su zona y pise el QR;
- que quien solo ficha pueda bajar carnets.
"""

import re
from unittest import mock

from django.urls import reverse
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .carnets import CELULAR_ALTO, CELULAR_ANCHO, CELULAR_BASE_QR, CELULAR_QR, generar_carnet_celular_pdf
from .services import anular_carnet, dar_de_baja_empleado
from .tests_carnets import _qr_impresos
from .tests_codigo_corto import _textos_del_pdf
from .tests_padron import PadronTestCase


class CarnetCelularTests(PadronTestCase):
    def setUp(self):
        super().setUp()
        self.empleado = self._empleado(nombre="María Belén López", dni="")

    def test_una_hoja_vertical_con_el_qr_y_el_codigo(self):
        contenido, qrs = _qr_impresos(lambda: generar_carnet_celular_pdf(self.empleado))
        self.assertEqual(qrs, [self.empleado.codigo_carnet])
        texto_pdf = contenido.decode("latin-1")
        self.assertEqual(len(re.findall(r"/Type /Page\b", texto_pdf)), 1)
        caja = re.search(r"/MediaBox \[ 0 0 ([\d.]+) ([\d.]+) \]", texto_pdf)
        ancho, alto = float(caja[1]), float(caja[2])
        self.assertAlmostEqual(ancho, CELULAR_ANCHO, places=1)
        self.assertAlmostEqual(alto, CELULAR_ALTO, places=1)
        self.assertGreater(alto, ancho)
        codigo = self.empleado.codigo_carnet
        self.assertIn(f"({codigo[:3]} {codigo[3:]})", _textos_del_pdf(contenido))

    def _textos_dibujados(self):
        original = Canvas.drawString
        dibujados = []

        def espia(lienzo, x, y, texto, *args, **kwargs):
            dibujados.append((x, y, texto, lienzo._fontsize))
            return original(lienzo, x, y, texto, *args, **kwargs)

        with mock.patch.object(Canvas, "drawString", espia):
            generar_carnet_celular_pdf(self.empleado)
        return dibujados

    def test_un_nombre_larguisimo_no_pisa_el_qr_ni_se_sale_de_la_hoja(self):
        self.empleado.nombre = "Franco Daniel Maximiliano Chauque Villarruel Miranda Herrera Ramirez"
        self.empleado.sucursal.nombre = "Panadería y Pastelería Oveja Negra del Centro Comercial"
        tope_qr = CELULAR_BASE_QR + CELULAR_QR
        for x, y, texto, tamano in self._textos_dibujados():
            with self.subTest(texto=texto):
                # La parte de abajo de la letra (un cuarto del tamano) queda arriba del QR.
                self.assertGreater(y - tamano * 0.25, tope_qr)
                self.assertLessEqual(x + stringWidth(texto, "Helvetica-Bold", tamano), CELULAR_ANCHO)

    def test_la_pantalla_lo_baja_con_el_nombre_del_empleado(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnet_celular", args=[self.empleado.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("carnet-celular-maria-belen-lopez.pdf", response["Content-Disposition"])

    def test_el_padron_trae_el_boton(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:empleado_list"))
        self.assertContains(response, reverse("asistencia:carnet_celular", args=[self.empleado.pk]))
        self.assertContains(response, "Carnet celular")

    def test_despues_de_anular_lleva_el_codigo_nuevo(self):
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Lo perdió")
        self.empleado.refresh_from_db()
        _, qrs = _qr_impresos(lambda: generar_carnet_celular_pdf(self.empleado))
        self.assertEqual(qrs, [self.empleado.codigo_carnet])

    def test_de_otra_empresa_da_404_y_de_baja_no_se_genera(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        self._login(self.admin, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:carnet_celular", args=[de_mapogo.pk])).status_code, 404)
        dar_de_baja_empleado(actor=self.admin, empleado=self.empleado)
        response = self.client.get(reverse("asistencia:carnet_celular", args=[self.empleado.pk]))
        self.assertRedirects(response, reverse("asistencia:empleado_list"))

    def test_quien_solo_ficha_no_lo_baja(self):
        self._login(self.cajero, [self.armadi])
        self.assertEqual(
            self.client.get(reverse("asistencia:carnet_celular", args=[self.empleado.pk])).status_code, 403
        )
