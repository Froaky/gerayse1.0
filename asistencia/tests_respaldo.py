"""EP-14 US-14.7: respaldo diario de la asistencia fuera del servidor.

Riesgos que cubre:
- un respaldo que no permita reconstruir empleados, carnets anulados y marcas;
- un destino mal escrito que deje el respaldo en el disco del contenedor;
- que la retencion borre de mas (otro entorno, el cron parado) o no borre;
- que una falla pase en silencio (sin error ni aviso);
- que una restauracion pise datos sin que se pida, o falle sin decir por que
  (un usuario o un local que ya no existen).
"""

import gzip
import tempfile
from datetime import date
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from .models import CarnetAnulado, Empleado, Marca
from .respaldo import (
    CarpetaLocal,
    RespaldoError,
    destino_configurado,
    generar_respaldo,
    nombre_de_respaldo,
    podar,
    restaurar_respaldo,
)
from .services import anular_carnet, anular_marca
from .tests_fichaje import FichajeTestCase, _momento


class RespaldoTestCase(FichajeTestCase):
    def setUp(self):
        super().setUp()
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.carpeta = Path(carpeta.name)
        entorno = mock.patch.dict(
            "os.environ",
            {
                "RESPALDO_DESTINO": str(self.carpeta),
                "RESPALDO_AVISO_URL": "",
                "RESPALDO_OK_URL": "",
                "RAILWAY_ENVIRONMENT_NAME": "",
            },
        )
        entorno.start()
        self.addCleanup(entorno.stop)

    def _datos(self):
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Lo perdió")
        self.empleado.refresh_from_db()
        self._fichar(_momento(2, 8))
        salida = self._fichar(_momento(2, 16)).marca
        anular_marca(actor=self.admin, marca=salida, motivo="Error")
        self._empleado(nombre="Ana Gomez", dni="32000111")

    def _vaciar(self):
        Marca.objects.all().delete()
        CarnetAnulado.objects.all().delete()
        Empleado.objects.all().delete()


class RespaldoYRestauracionTests(RespaldoTestCase):
    def test_restaurar_reconstruye_empleados_carnets_y_marcas(self):
        self._datos()
        antes = {
            "empleados": list(Empleado.objects.order_by("pk").values()),
            "carnets": list(CarnetAnulado.objects.order_by("pk").values()),
            "marcas": list(Marca.objects.order_by("pk").values()),
        }
        contenido = generar_respaldo()
        self._vaciar()

        resultado = restaurar_respaldo(contenido)

        self.assertEqual(
            resultado.cantidades,
            {"Empleado": 2, "CarnetAnulado": 1, "Marca": 2, "ReglasAsistencia": 0, "Horario": 0, "HorarioCambio": 0},
        )
        self.assertEqual(resultado.usuarios_vaciados, 0)
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
        self.assertEqual(restaurar_respaldo(contenido, reemplazar=True).cantidades["Empleado"], 2)
        self.assertFalse(Empleado.objects.filter(nombre="Nuevo").exists())

    def test_un_archivo_que_no_es_respaldo_no_toca_nada(self):
        for contenido in (
            b"basura",
            gzip.compress(b"{}"),
            gzip.compress(b'[{"pk": 1}]'),
            gzip.compress(b'[{"model": "asistencia.marca", "pk": 1}]'),
            gzip.compress(b'[{"model": "auth.user", "pk": 1, "fields": {}}]'),
        ):
            with self.subTest(contenido=contenido[:12]):
                with self.assertRaises(RespaldoError):
                    restaurar_respaldo(contenido, reemplazar=True)
        self.assertTrue(Empleado.objects.exists())

    def test_un_usuario_que_ya_no_existe_queda_vacio(self):
        self._datos()
        contenido = generar_respaldo()
        self._vaciar()
        # El cajero se borro despues del respaldo (las FK a usuario son SET_NULL).
        get_user_model().objects.filter(pk=self.cajero.pk).delete()

        resultado = restaurar_respaldo(contenido)

        self.assertEqual(resultado.cantidades["Marca"], 2)
        self.assertEqual(resultado.usuarios_vaciados, 2)
        self.assertFalse(Marca.objects.filter(registrada_por__isnull=False).exists())

    def test_un_local_que_ya_no_existe_frena_todo_y_lo_dice(self):
        self._datos()
        contenido = generar_respaldo()
        self._vaciar()
        Marca.objects.all().delete()
        terminal = self.terminal.pk
        self.terminal.delete()

        with self.assertRaisesMessage(RespaldoError, f"Sucursal {terminal}"):
            restaurar_respaldo(contenido)
        self.assertFalse(Empleado.objects.exists())

    def test_las_referencias_se_chequean_antes_de_reiniciar_los_ids(self):
        # Si algo se escapara del control previo, falla adentro de la restauracion
        # y no al confirmar (cuando el reinicio de ids ya no vuelve atras).
        self._datos()
        contenido = generar_respaldo()
        self._vaciar()
        Marca.objects.all().delete()
        self.terminal.delete()
        with mock.patch("asistencia.respaldo._completar_referencias", return_value=0):
            with mock.patch("asistencia.respaldo.connection.ops.sequence_reset_sql") as reinicio:
                with self.assertRaisesMessage(RespaldoError, "referencias rotas"):
                    restaurar_respaldo(contenido)
        reinicio.assert_not_called()


class DestinoTests(RespaldoTestCase):
    def test_un_destino_mal_escrito_es_un_error(self):
        for valor in (
            "s3:/gerayse/asistencia",
            '"s3://gerayse/asistencia"',
            "r2://gerayse/asistencia",
            "gerayse-respaldos/asistencia",
            "s3://",
        ):
            with self.subTest(valor=valor):
                with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": valor}):
                    with self.assertRaises(RespaldoError):
                        destino_configurado()

    def test_s3_en_mayusculas_se_entiende(self):
        with mock.patch.dict("sys.modules", {"boto3": mock.MagicMock()}):
            with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": "S3://gerayse/asistencia"}):
                self.assertEqual(destino_configurado().bucket, "gerayse")


class RetencionTests(RespaldoTestCase):
    def _subir(self, destino, entorno, dia):
        destino.subir(f"asistencia-{entorno}-{dia:%Y%m%d}-033000.json.gz", b"x")

    def test_borra_los_de_mas_de_30_dias_y_solo_de_este_entorno(self):
        destino = CarpetaLocal(self.carpeta)
        hoy = date(2026, 11, 20)
        for atras in range(40):
            self._subir(destino, "production", date.fromordinal(hoy.toordinal() - atras))
        for atras in range(40):
            self._subir(destino, "staging", date.fromordinal(hoy.toordinal() - atras))
        (self.carpeta / "otra-cosa.txt").write_bytes(b"no tocar")

        borrados = podar(destino, "production", hoy=hoy)

        self.assertEqual(len(borrados), 9)
        self.assertTrue(all("-production-" in nombre for nombre in borrados))
        self.assertEqual(len([n for n in destino.listar() if "-staging-" in n]), 40)
        self.assertTrue((self.carpeta / "otra-cosa.txt").exists())

    def test_con_el_cron_parado_quedan_los_ultimos_30_aunque_sean_viejos(self):
        destino = CarpetaLocal(self.carpeta)
        for atras in range(35):
            self._subir(destino, "production", date.fromordinal(date(2026, 1, 1).toordinal() - atras))
        borrados = podar(destino, "production", hoy=date(2026, 11, 20))
        self.assertEqual(len(borrados), 5)
        self.assertEqual(len(destino.listar()), 30)

    def test_varias_corridas_en_un_dia_no_acortan_los_30_dias(self):
        destino = CarpetaLocal(self.carpeta)
        hoy = date(2026, 11, 20)
        for atras in range(30):
            self._subir(destino, "production", date.fromordinal(hoy.toordinal() - atras))
        for hora in range(10):
            destino.subir(f"asistencia-production-20261120-1{hora}0000.json.gz", b"x")
        self.assertEqual(podar(destino, "production", hoy=hoy), [])

    def test_el_nombre_lleva_el_entorno(self):
        with mock.patch.dict("os.environ", {"RAILWAY_ENVIRONMENT_NAME": "production"}):
            call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
        self.assertRegex(CarpetaLocal(self.carpeta).listar()[0], r"^asistencia-production-\d{8}-\d{6}\.json\.gz$")
        self.assertTrue(nombre_de_respaldo("local").startswith("asistencia-local-"))


class ComandosTests(RespaldoTestCase):
    def test_el_comando_sube_el_respaldo_y_se_puede_restaurar_por_nombre(self):
        self._datos()
        salida = StringIO()
        call_command("respaldar_asistencia", stdout=salida, stderr=StringIO())
        nombres = CarpetaLocal(self.carpeta).listar()
        self.assertEqual(len(nombres), 1)
        self.assertIn(str(self.carpeta / nombres[0]), salida.getvalue())

        listado = StringIO()
        call_command("restaurar_asistencia", stdout=listado)
        self.assertIn(nombres[0], listado.getvalue())

        salida = StringIO()
        call_command("restaurar_asistencia", nombres[0], "--reemplazar", stdout=salida)
        self.assertIn("Marca: 2", salida.getvalue())

    def test_si_falla_termina_con_error_y_avisa(self):
        with mock.patch.dict("os.environ", {"RESPALDO_AVISO_URL": "https://avisos.example/hook"}):
            with mock.patch("asistencia.respaldo.CarpetaLocal.subir", side_effect=OSError("disco lleno")):
                with mock.patch("asistencia.respaldo.urllib.request.urlopen") as urlopen:
                    with self.assertRaisesMessage(CommandError, "disco lleno"):
                        call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
        pedido = urlopen.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://avisos.example/hook")
        self.assertIn(b"disco lleno", pedido.data)

    def test_si_falla_la_limpieza_dice_que_el_respaldo_se_subio(self):
        with mock.patch("asistencia.management.commands.respaldar_asistencia.podar", side_effect=OSError("sin permiso")):
            with self.assertRaisesMessage(CommandError, "se subio, pero fallo la limpieza"):
                call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(CarpetaLocal(self.carpeta).listar()), 1)

    def test_si_sale_bien_avisa_al_monitor_y_si_falla_no(self):
        with mock.patch.dict("os.environ", {"RESPALDO_OK_URL": "https://monitor.example/ping"}):
            with mock.patch("asistencia.respaldo.urllib.request.urlopen") as urlopen:
                call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
                self.assertEqual(urlopen.call_args.args[0].full_url, "https://monitor.example/ping")
                urlopen.reset_mock()
                with mock.patch("asistencia.respaldo.CarpetaLocal.subir", side_effect=OSError("disco lleno")):
                    with self.assertRaises(CommandError):
                        call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
                urlopen.assert_not_called()

    def test_sin_aviso_configurado_lo_advierte(self):
        errores = StringIO()
        call_command("respaldar_asistencia", stdout=StringIO(), stderr=errores)
        self.assertIn("RESPALDO_AVISO_URL", errores.getvalue())

    def test_sin_destino_configurado_es_un_error(self):
        with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": ""}):
            with self.assertRaisesMessage(CommandError, "RESPALDO_DESTINO"):
                call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())

    def test_restaurar_un_nombre_que_no_existe_es_un_error_claro(self):
        with self.assertRaisesMessage(CommandError, "No se restauro nada"):
            call_command("restaurar_asistencia", "asistencia-local-20260101-033000.json.gz", stdout=StringIO())

    def test_s3_usa_bucket_y_prefijo(self):
        cliente = mock.MagicMock()
        cliente.get_paginator.return_value.paginate.return_value = []
        boto3 = mock.MagicMock(client=mock.MagicMock(return_value=cliente))
        with mock.patch.dict("sys.modules", {"boto3": boto3}):
            with mock.patch.dict("os.environ", {"RESPALDO_DESTINO": "s3://gerayse-respaldos/asistencia"}):
                call_command("respaldar_asistencia", stdout=StringIO(), stderr=StringIO())
        argumentos = cliente.put_object.call_args.kwargs
        self.assertEqual(argumentos["Bucket"], "gerayse-respaldos")
        self.assertTrue(argumentos["Key"].startswith("asistencia/asistencia-local-"))
