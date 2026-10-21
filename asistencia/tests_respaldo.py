"""EP-14 US-14.7: respaldo diario de la asistencia fuera del servidor.

Riesgos que cubre:
- un respaldo que no permita reconstruir empleados, carnets anulados y marcas;
- que la retencion borre de mas o no borre;
- que una falla pase en silencio (sin error ni aviso);
- que una restauracion pise datos sin que se pida.
"""

import gzip
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from .models import CarnetAnulado, Empleado, Marca
from .respaldo import CarpetaLocal, RespaldoError, generar_respaldo, podar, restaurar_respaldo
from .services import anular_carnet, anular_marca
from .tests_fichaje import FichajeTestCase, _momento


class RespaldoTestCase(FichajeTestCase):
    def setUp(self):
        super().setUp()
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.carpeta = Path(carpeta.name)
        entorno = mock.patch.dict("os.environ", {"RESPALDO_DESTINO": str(self.carpeta), "RESPALDO_AVISO_URL": ""})
        entorno.start()
        self.addCleanup(entorno.stop)

    def _datos(self):
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Lo perdió")
        self.empleado.refresh_from_db()
        self._fichar(_momento(2, 8))
        salida = self._fichar(_momento(2, 16)).marca
        anular_marca(actor=self.admin, marca=salida, motivo="Error")
        self._empleado(nombre="Ana Gomez", dni="32000111")


class RespaldoYRestauracionTests(RespaldoTestCase):
    def test_restaurar_reconstruye_empleados_carnets_y_marcas(self):
        self._datos()
        antes = {
            "empleados": list(Empleado.objects.order_by("pk").values()),
            "carnets": list(CarnetAnulado.objects.order_by("pk").values()),
            "marcas": list(Marca.objects.order_by("pk").values()),
        }
        contenido = generar_respaldo()
        Marca.objects.all().delete()
        CarnetAnulado.objects.all().delete()
        Empleado.objects.all().delete()

        cantidades = restaurar_respaldo(contenido)

        self.assertEqual(cantidades, {"Empleado": 2, "CarnetAnulado": 1, "Marca": 2})
        self.assertEqual(list(Empleado.objects.order_by("pk").values()), antes["empleados"])
        self.assertEqual(list(CarnetAnulado.objects.order_by("pk").values()), antes["carnets"])
        self.assertEqual(list(Marca.objects.order_by("pk").values()), antes["marcas"])
        # Despues de restaurar se puede seguir cargando sin chocar ids.
        self.assertTrue(self._fichar(_momento(3, 8)).registrada)

    def test_no_pisa_datos_salvo_que_se_pida(self):
        self._datos()
        contenido = generar_respaldo()
        with self.assertRaises(RespaldoError):
            restaurar_respaldo(contenido)
        self._empleado(nombre="Nuevo", dni="35000111")
        self.assertEqual(restaurar_respaldo(contenido, reemplazar=True)["Empleado"], 2)
        self.assertFalse(Empleado.objects.filter(nombre="Nuevo").exists())

    def test_un_archivo_que_no_es_respaldo_no_toca_nada(self):
        for contenido in (b"basura", gzip.compress(b"[{\"model\": \"auth.user\", \"pk\": 1, \"fields\": {}}]")):
            with self.subTest(contenido=contenido[:10]):
                with self.assertRaises(RespaldoError):
                    restaurar_respaldo(contenido, reemplazar=True)
        self.assertTrue(Empleado.objects.exists())


class ComandosTests(RespaldoTestCase):
    def test_el_comando_sube_el_respaldo_y_se_puede_restaurar_por_nombre(self):
        self._datos()
        call_command("respaldar_asistencia", stdout=StringIO())
        nombres = CarpetaLocal(self.carpeta).listar()
        self.assertEqual(len(nombres), 1)

        listado = StringIO()
        call_command("restaurar_asistencia", stdout=listado)
        self.assertIn(nombres[0], listado.getvalue())

        salida = StringIO()
        call_command("restaurar_asistencia", nombres[0], "--reemplazar", stdout=salida)
        self.assertIn("Marca: 2", salida.getvalue())

    def test_quedan_los_ultimos_30(self):
        destino = CarpetaLocal(self.carpeta)
        for dia in range(1, 36):
            destino.subir(f"asistencia-202610{dia:02d}-030000.json.gz" if dia <= 31 else f"asistencia-202611{dia - 31:02d}-030000.json.gz", b"x")
        (self.carpeta / "otra-cosa.txt").write_bytes(b"no tocar")
        borrados = podar(destino)
        self.assertEqual(len(borrados), 5)
        self.assertEqual(destino.listar()[0], "asistencia-20261006-030000.json.gz")
        self.assertEqual(len(destino.listar()), 30)
        self.assertTrue((self.carpeta / "otra-cosa.txt").exists())

    def test_si_falla_termina_con_error_y_avisa(self):
        with mock.patch.dict("os.environ", {"RESPALDO_AVISO_URL": "https://avisos.example/hook"}):
            with mock.patch("asistencia.respaldo.CarpetaLocal.subir", side_effect=OSError("disco lleno")):
                with mock.patch("asistencia.respaldo.urllib.request.urlopen") as urlopen:
                    with self.assertRaisesMessage(CommandError, "disco lleno"):
                        call_command("respaldar_asistencia", stdout=StringIO())
        pedido = urlopen.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://avisos.example/hook")
        self.assertIn(b"disco lleno", pedido.data)

    def test_sin_destino_configurado_es_un_error(self):
        with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": ""}):
            with self.assertRaisesMessage(CommandError, "RESPALDO_DESTINO"):
                call_command("respaldar_asistencia", stdout=StringIO())

    def test_s3_usa_bucket_y_prefijo(self):
        cliente = mock.MagicMock()
        boto3 = mock.MagicMock(client=mock.MagicMock(return_value=cliente))
        with mock.patch.dict("sys.modules", {"boto3": boto3}):
            with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": "s3://gerayse-respaldos/asistencia"}):
                call_command("respaldar_asistencia", stdout=StringIO())
        argumentos = cliente.put_object.call_args.kwargs
        self.assertEqual(argumentos["Bucket"], "gerayse-respaldos")
        self.assertTrue(argumentos["Key"].startswith("asistencia/asistencia-"))
