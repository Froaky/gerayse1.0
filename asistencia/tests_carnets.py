"""EP-14 US-14.3: carnets con QR.

Riesgos que cubre:
- que el QR lleve datos personales o que dos empleados compartan codigo;
- que un carnet anulado siga sirviendo, o que se anule sin dejar quien, cuando
  y por que;
- imprimir o anular carnets de otra empresa, o de quien no administra;
- que la migracion deje a los empleados ya cargados sin codigo o con el mismo.
"""

import re
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from cashops.models import Empresa

from .carnets import ajustar_texto
from .forms import EmpleadoForm
from .models import CODIGO_CARNET_PATRON, CarnetAnulado, Empleado
from .services import anular_carnet, dar_de_baja_empleado
from .tests_padron import PadronTestCase

PAGINA_PDF = re.compile(rb"/Type\s*/Page(?!s)")


def _qr_impresos(response_o_funcion):
    """Corre la funcion y devuelve los textos que se pusieron en cada QR."""
    with mock.patch("asistencia.carnets.QrCodeWidget", wraps=QrCodeWidget) as espia:
        resultado = response_o_funcion()
    return resultado, [llamada.args[0] for llamada in espia.call_args_list]


class CodigoDeCarnetTests(PadronTestCase):
    def test_cada_empleado_nace_con_su_propio_codigo_sin_datos_personales(self):
        uno = self._empleado(nombre="Uno", dni="30111222")
        dos = self._empleado(nombre="Dos", dni="30333444")
        for empleado in (uno, dos):
            self.assertRegex(empleado.codigo_carnet, CODIGO_CARNET_PATRON)
            self.assertNotIn(empleado.dni, empleado.codigo_carnet)
        self.assertNotEqual(uno.codigo_carnet, dos.codigo_carnet)

    def test_el_codigo_no_se_toca_desde_el_formulario(self):
        self.assertNotIn("codigo_carnet", EmpleadoForm(empresas=Empresa.objects.filter(pk=self.armadi.pk)).fields)


class AnularCarnetTests(PadronTestCase):
    def test_anular_cambia_el_codigo_y_deja_quien_cuando_y_por_que(self):
        empleado = self._empleado()
        viejo = empleado.codigo_carnet
        anular_carnet(actor=self.admin, empleado=empleado, motivo="  Lo  perdió ")
        empleado.refresh_from_db()
        self.assertNotEqual(empleado.codigo_carnet, viejo)
        self.assertRegex(empleado.codigo_carnet, CODIGO_CARNET_PATRON)
        anulado = CarnetAnulado.objects.get(codigo=viejo)
        self.assertEqual(anulado.empleado, empleado)
        self.assertEqual(anulado.anulado_por, self.admin)
        self.assertEqual(anulado.motivo, "Lo perdió")
        self.assertIsNotNone(anulado.anulado_en)
        self.assertFalse(Empleado.objects.filter(codigo_carnet=viejo).exists())

    def test_anular_dos_veces_guarda_las_dos(self):
        empleado = self._empleado()
        anular_carnet(actor=self.admin, empleado=empleado, motivo="Se rompió")
        anular_carnet(actor=self.admin, empleado=empleado, motivo="Lo perdió")
        self.assertEqual(empleado.carnets_anulados.count(), 2)

    def test_anular_exige_motivo(self):
        empleado = self._empleado()
        viejo = empleado.codigo_carnet
        with self.assertRaises(ValidationError):
            anular_carnet(actor=self.admin, empleado=empleado, motivo="   \x00 ")
        empleado.refresh_from_db()
        self.assertEqual(empleado.codigo_carnet, viejo)

    def test_no_se_anula_el_carnet_de_un_dado_de_baja(self):
        empleado = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        with self.assertRaises(ValidationError):
            anular_carnet(actor=self.admin, empleado=empleado, motivo="Lo perdió")

    def test_sin_permiso_o_de_otra_empresa_no_se_anula(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        with self.assertRaises(PermissionDenied):
            anular_carnet(actor=self.cajero, empleado=de_mapogo, motivo="x")
        solo_armadi = type(self.admin).objects.create_user(username="solo-armadi-carnet", password="test", role=self.admin_role)
        solo_armadi.empresas_permitidas.set([self.armadi])
        with self.assertRaises(ValidationError):
            anular_carnet(actor=solo_armadi, empleado=de_mapogo, motivo="x")
        self.assertFalse(CarnetAnulado.objects.exists())

    def test_anular_por_la_pantalla(self):
        empleado = self._empleado()
        viejo = empleado.codigo_carnet
        self._login(self.admin, [self.armadi])
        url = reverse("asistencia:carnet_anular", args=[empleado.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"motivo": "", "anulaciones_previas": 0}).status_code, 400)
        response = self.client.post(url, {"motivo": "Lo perdió", "anulaciones_previas": 0}, follow=True)
        self.assertContains(response, "ya no sirve para fichar")
        self.assertContains(response, "Carnet anulado 1 vez")
        empleado.refresh_from_db()
        self.assertNotEqual(empleado.codigo_carnet, viejo)

    def test_anular_el_de_otra_empresa_por_url_da_404(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        self._login(self.admin, [self.armadi])
        url = reverse("asistencia:carnet_anular", args=[de_mapogo.pk])
        self.assertEqual(self.client.post(url, {"motivo": "x", "anulaciones_previas": 0}).status_code, 404)
        self.assertFalse(CarnetAnulado.objects.exists())


class PdfDeCarnetsTests(PadronTestCase):
    def test_el_carnet_de_un_empleado_lleva_solo_su_codigo_en_el_qr(self):
        empleado = self._empleado()
        self._login(self.admin, [self.armadi])
        response, qrs = _qr_impresos(
            lambda: self.client.get(reverse("asistencia:carnet_empleado", args=[empleado.pk]))
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(qrs, [empleado.codigo_carnet])

    def test_el_carnet_de_otra_empresa_da_404(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnet_empleado", args=[de_mapogo.pk]))
        self.assertEqual(response.status_code, 404)

    def test_no_se_imprime_el_carnet_de_un_dado_de_baja(self):
        empleado = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnet_empleado", args=[empleado.pk]))
        self.assertEqual(response.status_code, 302)

    def test_el_pdf_masivo_respeta_alcance_estado_y_filtros(self):
        centro_1 = self._empleado(nombre="Centro Uno", dni="30000001")
        centro_2 = self._empleado(nombre="Centro Dos", dni="30000002")
        terminal = self._empleado(nombre="Terminal", dni="30000003", sucursal=self.terminal)
        de_baja = self._empleado(nombre="De Baja", dni="30000004")
        dar_de_baja_empleado(actor=self.admin, empleado=de_baja)
        de_mapogo = self._empleado(nombre="De Mapogo", dni="30000005", sucursal=self.vivre)
        url = reverse("asistencia:carnets")

        self._login(self.admin, [self.armadi])
        _, qrs = _qr_impresos(lambda: self.client.get(url, {"pdf": "1"}))
        self.assertEqual(set(qrs), {centro_1.codigo_carnet, centro_2.codigo_carnet, terminal.codigo_carnet})

        _, qrs = _qr_impresos(lambda: self.client.get(url, {"pdf": "1", "sucursal": self.terminal.pk}))
        self.assertEqual(qrs, [terminal.codigo_carnet])

        self._login(self.admin, [self.armadi, self.mapogo])
        _, qrs = _qr_impresos(lambda: self.client.get(url, {"pdf": "1", "empresa": self.mapogo.pk}))
        self.assertEqual(qrs, [de_mapogo.codigo_carnet])

    def test_un_filtro_de_otra_empresa_no_imprime_todo(self):
        self._empleado()
        self._login(self.admin, [self.armadi])
        response, qrs = _qr_impresos(
            lambda: self.client.get(reverse("asistencia:carnets"), {"pdf": "1", "empresa": self.ajena.pk})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(qrs, [])

    def test_sin_empleados_no_hay_pdf(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnets"), {"pdf": "1"})
        self.assertEqual(response.status_code, 302)

    def test_diez_carnets_por_hoja(self):
        for numero in range(12):
            self._empleado(nombre=f"Empleado {numero}", dni=f"3{numero:07d}")
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnets"), {"pdf": "1"})
        self.assertEqual(len(PAGINA_PDF.findall(response.content)), 2)

    def test_la_pantalla_de_carnets_cuenta_los_activos(self):
        self._empleado()
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnets"))
        self.assertContains(response, "sale 1 empleado activo de")
        self._empleado(nombre="Otro", dni="30999888")
        response = self.client.get(reverse("asistencia:carnets"))
        self.assertContains(response, "salen 2 empleados activos de")

    def test_quien_solo_ficha_no_imprime_ni_anula(self):
        empleado = self._empleado()
        self._login(self.cajero)
        for url in (
            reverse("asistencia:carnets"),
            reverse("asistencia:carnet_empleado", args=[empleado.pk]),
            reverse("asistencia:carnet_anular", args=[empleado.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)


class MigracionCodigoDeCarnetTests(TransactionTestCase):
    """Los empleados cargados con la US-14.2 (staging) no tienen codigo: la
    migracion le tiene que dar a cada uno el suyo, distinto."""

    antes = [("asistencia", "0001_initial")]
    despues = [("asistencia", "0004_carnet_codigo_unico")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_cada_empleado_existente_recibe_un_codigo_distinto(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.antes)
        viejas = executor.loader.project_state(self.antes).apps
        Empresa = viejas.get_model("cashops", "Empresa")
        Sucursal = viejas.get_model("cashops", "Sucursal")
        Viejo = viejas.get_model("asistencia", "Empleado")
        empresa = Empresa.objects.create(nombre="Empresa Migracion")
        sucursal = Sucursal.objects.create(empresa=empresa, nombre="Local Migracion", codigo="MIG-01", razon_social="X")
        for numero in range(3):
            Viejo.objects.create(nombre=f"Viejo {numero}", dni=f"3000000{numero}", empresa=empresa, sucursal=sucursal)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.despues)
        nuevas = executor.loader.project_state(self.despues).apps
        codigos = list(nuevas.get_model("asistencia", "Empleado").objects.values_list("codigo_carnet", flat=True))
        self.assertEqual(len(codigos), 3)
        self.assertEqual(len(set(codigos)), 3)
        for codigo in codigos:
            self.assertRegex(codigo, CODIGO_CARNET_PATRON)


class CarnetsHallazgosDeRevisionTests(PadronTestCase):
    """Casos que encontro la revision adversarial de la US-14.3."""

    def test_un_nombre_con_una_palabra_larguisima_no_se_sale_de_su_zona(self):
        ancho = 40 * mm
        nombre = "ANA MARÍA DE LA CRUZ WOLKOWYSKI-SZCZEPANSKA-ETCHEVERRY"
        tamano, renglones = ajustar_texto(nombre, "Helvetica-Bold", (11, 10, 9, 8, 7), ancho, 3)
        self.assertLessEqual(len(renglones), 3)
        for renglon in renglones:
            self.assertLessEqual(stringWidth(renglon, "Helvetica-Bold", tamano), ancho)

    def test_un_nombre_largo_achica_la_letra_antes_de_perder_apellidos(self):
        tamano, renglones = ajustar_texto(
            "MARIA DE LOS ANGELES FERNANDEZ GUTIERREZ RODRIGUEZ", "Helvetica-Bold", (11, 10, 9, 8, 7), 40.6 * mm, 3
        )
        self.assertLess(tamano, 11)
        self.assertIn("RODRIGUEZ", renglones[-1])
        self.assertFalse(renglones[-1].endswith("…"))

    def test_lo_que_no_entra_ni_en_la_letra_mas_chica_se_corta_con_puntos(self):
        ancho = 40 * mm
        tamano, renglones = ajustar_texto("PALABRA " * 40, "Helvetica-Bold", (11, 7), ancho, 3)
        self.assertEqual(len(renglones), 3)
        self.assertTrue(renglones[-1].endswith("…"))
        self.assertLessEqual(stringWidth(renglones[-1], "Helvetica-Bold", tamano), ancho)

    def test_una_empresa_larga_entra_en_una_linea_sin_pisar_el_titulo(self):
        disponible = 85.6 * mm - 8 * mm - stringWidth("CARNET DE ASISTENCIA", "Helvetica-Bold", 8.5) - 3 * mm
        tamano, renglones = ajustar_texto("DISTRIBUIDORA MAYORISTA DEL NORTE S.A.", "Helvetica", (8, 7, 6), disponible, 1)
        self.assertEqual(len(renglones), 1)
        self.assertLessEqual(stringWidth(renglones[0], "Helvetica", tamano), disponible)

    def test_un_doble_envio_no_anula_el_carnet_nuevo(self):
        empleado = self._empleado()
        self._login(self.admin, [self.armadi])
        url = reverse("asistencia:carnet_anular", args=[empleado.pk])
        datos = {"motivo": "Lo perdió", "anulaciones_previas": 0}
        self.assertEqual(self.client.post(url, datos).status_code, 302)
        empleado.refresh_from_db()
        nuevo = empleado.codigo_carnet
        response = self.client.post(url, datos)
        self.assertContains(response, "ya se anuló recién", status_code=400)
        empleado.refresh_from_db()
        self.assertEqual(empleado.codigo_carnet, nuevo)
        self.assertEqual(empleado.carnets_anulados.count(), 1)

    def test_el_carnet_reimpreso_lleva_el_codigo_nuevo(self):
        empleado = self._empleado()
        viejo = empleado.codigo_carnet
        anular_carnet(actor=self.admin, empleado=empleado, motivo="Lo perdió")
        empleado.refresh_from_db()
        self._login(self.admin, [self.armadi])
        _, qrs = _qr_impresos(lambda: self.client.get(reverse("asistencia:carnet_empleado", args=[empleado.pk])))
        self.assertEqual(qrs, [empleado.codigo_carnet])
        self.assertNotEqual(qrs, [viejo])

    def test_la_base_no_deja_dos_empleados_con_el_mismo_codigo(self):
        uno = self._empleado()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Empleado.objects.create(
                nombre="Copia", dni="39999999", empresa=self.armadi, sucursal=self.centro, codigo_carnet=uno.codigo_carnet
            )
