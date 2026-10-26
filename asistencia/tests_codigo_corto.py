"""EP-14 D12: codigo de carnet de 6 digitos (26/10/2026).

El codigo largo era imposible de tipear a mano. Riesgos que cubre:
- un codigo que no tenga 6 digitos, que empiece con 0 o que se repita;
- que un codigo anulado (carnet perdido) se le asigne a otra persona;
- que el codigo tipeado con espacio o guion no se reconozca;
- que un carnet del formato anterior diga "desconocido" en vez de "anulado";
- que la migracion deje empleados sin codigo nuevo o pierda los viejos.
"""

import base64
import re
import zlib
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse

from cashops.models import Empresa, Sucursal

from .fichaje import Resultado
from .models import CODIGO_CARNET_PATRON, CarnetAnulado, nuevo_codigo_carnet
from .services import anular_carnet
from .tests_fichaje import FichajeTestCase, _momento


def _textos_del_pdf(contenido: bytes) -> str:
    texto = contenido.decode("latin-1")
    salida = []
    for crudo in re.findall(r"stream\r?\n(.*?)endstream", texto, re.S):
        datos = base64.a85decode(crudo.strip().removesuffix("~>").encode("latin-1"))
        salida.append(zlib.decompress(datos).decode("latin-1"))
    return "\n".join(salida)


class CodigoCortoTests(FichajeTestCase):
    def test_el_codigo_tiene_6_digitos_y_no_empieza_con_cero(self):
        for _ in range(200):
            codigo = nuevo_codigo_carnet()
            self.assertRegex(codigo, r"^[1-9][0-9]{5}$")
        self.assertRegex(self.empleado.codigo_carnet, CODIGO_CARNET_PATRON)

    def test_no_repite_un_codigo_vigente_ni_uno_anulado(self):
        viejo = self.empleado.codigo_carnet
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Lo perdió")
        self.empleado.refresh_from_db()
        vigente = self.empleado.codigo_carnet
        libre = 654321
        # El azar devuelve primero el anulado, despues el vigente y recien despues uno libre.
        valores = [int(viejo) - 100000, int(vigente) - 100000, libre - 100000]
        with mock.patch("asistencia.models.secrets.randbelow", side_effect=valores):
            self.assertEqual(nuevo_codigo_carnet(), str(libre))

    def test_anular_da_otro_codigo_corto_y_el_viejo_queda_anulado(self):
        viejo = self.empleado.codigo_carnet
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Se rompió")
        self.empleado.refresh_from_db()
        self.assertRegex(self.empleado.codigo_carnet, CODIGO_CARNET_PATRON)
        self.assertNotEqual(self.empleado.codigo_carnet, viejo)
        self.assertEqual(self._fichar(_momento(2, 8), codigo=viejo).resultado, Resultado.ANULADO)

    def test_se_puede_tipear_con_espacio_o_guion(self):
        codigo = self.empleado.codigo_carnet
        for tipeado in (f"{codigo[:3]} {codigo[3:]}", f"{codigo[:3]}-{codigo[3:]}", f" {codigo}\n"):
            with self.subTest(tipeado=tipeado):
                self.empleado.marcas.all().delete()
                self.assertTrue(self._fichar(_momento(2, 8), codigo=tipeado).registrada)

    def test_un_carnet_del_formato_anterior_anulado_avisa_que_esta_anulado(self):
        anterior = "GA1" + "B" * 24
        CarnetAnulado.objects.create(empleado=self.empleado, codigo=anterior, motivo="Cambio a código de 6 dígitos")
        fichaje = self._fichar(_momento(2, 8), codigo=anterior)
        self.assertEqual(fichaje.resultado, Resultado.ANULADO)
        self.assertIn("carnet nuevo", fichaje.mensaje)

    def test_otros_numeros_no_son_carnets(self):
        for codigo in ("012345", "12345", "1234567", "abcdef"):
            with self.subTest(codigo=codigo):
                self.assertEqual(self._fichar(_momento(2, 8), codigo=codigo).resultado, Resultado.DESCONOCIDO)

    def test_el_carnet_lleva_el_codigo_escrito_grande_y_en_dos_grupos(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:carnet_empleado", args=[self.empleado.pk]))
        texto = _textos_del_pdf(response.content)
        codigo = self.empleado.codigo_carnet
        self.assertIn(f"({codigo[:3]} {codigo[3:]}) Tj", texto)
        self.assertIn("Courier-Bold", response.content.decode("latin-1"))


class MigracionCodigoCortoTests(TransactionTestCase):
    antes = [("asistencia", "0007_dni_opcional")]
    despues = [("asistencia", "0008_carnet_codigo_corto")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_los_codigos_anteriores_pasan_a_6_digitos_y_quedan_anulados(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.antes)
        viejas = executor.loader.project_state(self.antes).apps
        Viejo = viejas.get_model("asistencia", "Empleado")
        empresa = Empresa.objects.create(nombre="Empresa Migracion")
        sucursal = Sucursal.objects.create(empresa=empresa, nombre="Local Migracion", codigo="MIG-01", razon_social="X")
        anteriores = [f"GA1{letra * 24}" for letra in "BCD"]
        for numero, codigo in enumerate(anteriores):
            Viejo.objects.create(
                nombre=f"Viejo {numero}", dni="", empresa_id=empresa.pk, sucursal_id=sucursal.pk, codigo_carnet=codigo
            )
        Viejo.objects.create(nombre="Ya corto", dni="", empresa_id=empresa.pk, sucursal_id=sucursal.pk, codigo_carnet="123456")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.despues)
        nuevas = executor.loader.project_state(self.despues).apps
        Empleado_ = nuevas.get_model("asistencia", "Empleado")
        Anulado = nuevas.get_model("asistencia", "CarnetAnulado")

        codigos = list(Empleado_.objects.exclude(nombre="Ya corto").values_list("codigo_carnet", flat=True))
        self.assertEqual(len(set(codigos)), 3)
        for codigo in codigos:
            self.assertRegex(codigo, CODIGO_CARNET_PATRON)
        self.assertEqual(Empleado_.objects.get(nombre="Ya corto").codigo_carnet, "123456")
        self.assertEqual(set(Anulado.objects.values_list("codigo", flat=True)), set(anteriores))
        self.assertEqual(set(Anulado.objects.values_list("motivo", flat=True)), {"Cambio a código de 6 dígitos"})
