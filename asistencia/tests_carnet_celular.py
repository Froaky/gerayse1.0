"""EP-14 D16: carnet para el celular (mandar por WhatsApp y mostrar en la pantalla).

Riesgos que cubre:
- que el QR lleve otra cosa que el codigo vigente del carnet;
- que falte el codigo legible para tipear;
- que se pueda bajar el carnet de un empleado de otra empresa o dado de baja;
- que un nombre larguisimo se salga de su zona y pise el QR;
- que quien solo ficha pueda bajar carnets.

D17, varios juntos en un ZIP:
- que un archivo no lleve el carnet de la persona de su nombre;
- que dos personas con el mismo nombre se pisen dentro del ZIP;
- que un id de otra empresa o de un dado de baja se cuele en el ZIP.
"""

import re
import zipfile
from io import BytesIO
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


class CarnetsCelularZipTests(PadronTestCase):
    def setUp(self):
        super().setUp()
        self.maria = self._empleado(nombre="María Belén López", dni="")
        self.juan = self._empleado(nombre="Juan Perez", dni="30123456")
        self.ana = self._empleado(nombre="Ana Gómez", dni="30999888", sucursal=self.vivre)

    def _bajar(self, *empleados):
        return self.client.post(reverse("asistencia:carnets_celular"), {"empleado": [e.pk for e in empleados]})

    def _pdfs(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn('attachment; filename="carnets-celular.zip"', response["Content-Disposition"])
        with zipfile.ZipFile(BytesIO(response.content)) as archivo:
            return {nombre: archivo.read(nombre) for nombre in archivo.namelist()}

    def _codigo_en(self, pdf, empleado):
        codigo = empleado.codigo_carnet
        self.assertIn(f"({codigo[:3]} {codigo[3:]})", _textos_del_pdf(pdf))

    def test_un_pdf_por_marcado_con_su_nombre_y_su_carnet(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        pdfs = self._pdfs(self._bajar(self.maria, self.ana))
        self.assertEqual(sorted(pdfs), ["carnet-celular-ana-gomez.pdf", "carnet-celular-maria-belen-lopez.pdf"])
        self._codigo_en(pdfs["carnet-celular-maria-belen-lopez.pdf"], self.maria)
        self._codigo_en(pdfs["carnet-celular-ana-gomez.pdf"], self.ana)
        self.assertEqual(len(re.findall(r"/Type /Page\b", pdfs["carnet-celular-ana-gomez.pdf"].decode("latin-1"))), 1)

    def test_los_que_se_llaman_igual_no_se_pisan(self):
        de_terminal = self._empleado(nombre="Juan Perez", dni="30123457", sucursal=self.terminal)
        otro_del_centro = self._empleado(nombre="Juan Perez", dni="30123458")
        tercero_del_centro = self._empleado(nombre="Juan Perez", dni="30123459")
        self._login(self.admin, [self.armadi])
        pdfs = self._pdfs(self._bajar(self.juan, de_terminal, otro_del_centro, tercero_del_centro))
        self.assertEqual(
            sorted(pdfs),
            [
                "carnet-celular-juan-perez-cent-02-2.pdf",
                "carnet-celular-juan-perez-cent-02.pdf",
                "carnet-celular-juan-perez-term-01.pdf",
                "carnet-celular-juan-perez.pdf",
            ],
        )
        # El orden es por nombre y despues por alta: el primero cargado se queda sin sufijo.
        self._codigo_en(pdfs["carnet-celular-juan-perez.pdf"], self.juan)
        self._codigo_en(pdfs["carnet-celular-juan-perez-term-01.pdf"], de_terminal)
        self._codigo_en(pdfs["carnet-celular-juan-perez-cent-02.pdf"], otro_del_centro)
        self._codigo_en(pdfs["carnet-celular-juan-perez-cent-02-2.pdf"], tercero_del_centro)

    def test_no_se_cuelan_los_de_otra_empresa_ni_los_de_baja(self):
        dar_de_baja_empleado(actor=self.admin, empleado=self.juan)
        self._login(self.admin, [self.armadi])
        pdfs = self._pdfs(self._bajar(self.maria, self.juan, self.ana))
        self.assertEqual(list(pdfs), ["carnet-celular-maria-belen-lopez.pdf"])

    def test_sin_ninguno_valido_vuelve_al_padron_con_aviso(self):
        self._login(self.admin, [self.armadi])
        response = self._bajar(self.ana)
        self.assertRedirects(response, reverse("asistencia:empleado_list"))
        response = self.client.post(reverse("asistencia:carnets_celular"), {"empleado": ["abc", ""]}, follow=True)
        self.assertContains(response, "Marcá al menos un empleado activo")

    def test_solo_por_post_y_quien_solo_ficha_no_lo_baja(self):
        self._login(self.admin, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:carnets_celular")).status_code, 405)
        self._login(self.cajero, [self.armadi])
        self.assertEqual(self._bajar(self.maria).status_code, 403)

    def test_el_padron_trae_casillas_solo_para_los_activos(self):
        dar_de_baja_empleado(actor=self.admin, empleado=self.juan)
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:empleado_list"), {"estado": "todos"})
        self.assertContains(response, f'action="{reverse("asistencia:carnets_celular")}"')
        self.assertContains(response, f'name="empleado" value="{self.maria.pk}" form="carnets-celular"')
        self.assertNotContains(response, f'name="empleado" value="{self.juan.pk}"')
        # Solo los de baja: no hay nada para marcar.
        response = self.client.get(reverse("asistencia:empleado_list"), {"estado": "baja"})
        self.assertNotContains(response, 'id="carnets-celular"')
