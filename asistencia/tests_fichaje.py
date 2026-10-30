"""EP-14 US-14.4: fichar con el carnet (reglas y pantalla, sin la camara).

Riesgos que cubre:
- que una entrada y una salida se confundan (alternancia, 16 horas, medianoche);
- que un doble escaneo deje dos marcas;
- que un carnet anulado, desconocido o de un empleado de baja genere marca;
- que la hora o el local salgan de otro lado que no sea el servidor y el cajero;
- que alguien sin el permiso de fichar registre marcas.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.contrib.staticfiles import finders
from django.core.exceptions import PermissionDenied
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from users.models import PermissionModule, UserPermission

from .fichaje import Resultado, fichar
from .models import Marca
from .services import anular_carnet, dar_de_baja_empleado
from .tests_padron import PadronTestCase


def _momento(dia, hora, minuto=0):
    return timezone.make_aware(datetime(2026, 11, dia, hora, minuto))


class FichajeTestCase(PadronTestCase):
    def setUp(self):
        super().setUp()
        self.cajero.sucursal_base = self.terminal
        self.cajero.save(update_fields=["sucursal_base"])
        self.empleado = self._empleado(nombre="Juan Perez", dni="30123456")

    def _fichar(self, cuando, codigo=None, actor=None):
        with mock.patch("asistencia.fichaje.timezone.now", return_value=cuando):
            return fichar(actor=actor or self.cajero, codigo=codigo or self.empleado.codigo_carnet)


class ReglasDeFichajeTests(FichajeTestCase):
    def test_la_primera_es_entrada_y_la_siguiente_salida(self):
        entrada = self._fichar(_momento(2, 8))
        salida = self._fichar(_momento(2, 16))
        self.assertEqual(entrada.marca.tipo, Marca.Tipo.ENTRADA)
        self.assertEqual(salida.marca.tipo, Marca.Tipo.SALIDA)

    def test_turno_cortado_alterna_en_el_mismo_dia(self):
        tipos = [self._fichar(_momento(4, hora)).marca.tipo for hora in (9, 13, 18, 23)]
        self.assertEqual(tipos, [Marca.Tipo.ENTRADA, Marca.Tipo.SALIDA, Marca.Tipo.ENTRADA, Marca.Tipo.SALIDA])

    def test_una_salida_despues_de_medianoche_cierra_la_entrada_de_la_noche(self):
        self._fichar(_momento(5, 20))
        salida = self._fichar(_momento(6, 2))
        self.assertEqual(salida.marca.tipo, Marca.Tipo.SALIDA)

    def test_una_salida_y_una_entrada_a_mano_a_la_misma_hora_dejan_abierta_la_entrada(self):
        # Administracion partio un tramo a las 6 cargando primero la entrada: el proximo escaneo la cierra.
        self._fichar(_momento(2, 22))
        for tipo in (Marca.Tipo.ENTRADA, Marca.Tipo.SALIDA):
            Marca.objects.create(
                empleado=self.empleado, tipo=tipo, momento=_momento(3, 6), sucursal=self.terminal,
                origen=Marca.Origen.MANUAL, registrada_por=self.admin, motivo="Doble turno",
            )
        self.assertEqual(self._fichar(_momento(3, 13, 30)).marca.tipo, Marca.Tipo.SALIDA)

    def test_una_entrada_abierta_de_mas_de_16_horas_no_se_cierra_con_el_proximo_escaneo(self):
        self._fichar(_momento(2, 8))
        siguiente = self._fichar(_momento(3, 8))
        self.assertEqual(siguiente.marca.tipo, Marca.Tipo.ENTRADA)

    def test_un_doble_escaneo_dentro_de_los_2_minutos_no_genera_marca(self):
        self._fichar(_momento(2, 8))
        repetido = self._fichar(_momento(2, 8, 1))
        self.assertEqual(repetido.resultado, Resultado.REPETIDA)
        self.assertEqual(self.empleado.marcas.count(), 1)
        despues = self._fichar(_momento(2, 8, 3))
        self.assertEqual(despues.marca.tipo, Marca.Tipo.SALIDA)

    def test_la_hora_es_la_del_servidor_y_el_local_el_del_cajero(self):
        fichaje = self._fichar(_momento(2, 8, 12))
        self.assertEqual(fichaje.marca.momento, _momento(2, 8, 12))
        self.assertEqual(fichaje.marca.sucursal, self.terminal)
        self.assertEqual(fichaje.marca.registrada_por, self.cajero)
        self.assertEqual(fichaje.marca.origen, Marca.Origen.ESCANEO)

    def test_acepta_el_codigo_en_minusculas_o_con_espacios(self):
        codigo = f"  {self.empleado.codigo_carnet.lower()}\n"
        self.assertTrue(self._fichar(_momento(2, 8), codigo=codigo).registrada)

    def test_un_carnet_anulado_no_ficha_y_lo_dice(self):
        viejo = self.empleado.codigo_carnet
        anular_carnet(actor=self.admin, empleado=self.empleado, motivo="Lo perdió")
        fichaje = self._fichar(_momento(2, 8), codigo=viejo)
        self.assertEqual(fichaje.resultado, Resultado.ANULADO)
        self.assertFalse(Marca.objects.exists())

    def test_un_codigo_desconocido_o_cualquier_qr_no_ficha(self):
        for codigo in ("GA1" + "A" * 24, "https://ejemplo.com", ""):
            with self.subTest(codigo=codigo):
                self.assertEqual(self._fichar(_momento(2, 8), codigo=codigo or " ").resultado, Resultado.DESCONOCIDO)
        self.assertFalse(Marca.objects.exists())

    def test_un_empleado_de_baja_no_ficha(self):
        dar_de_baja_empleado(actor=self.admin, empleado=self.empleado)
        self.assertEqual(self._fichar(_momento(2, 8)).resultado, Resultado.DE_BAJA)
        self.assertFalse(Marca.objects.exists())

    def test_sin_local_asignado_no_se_ficha(self):
        self.cajero.sucursal_base = None
        self.cajero.save(update_fields=["sucursal_base"])
        self.assertEqual(self._fichar(_momento(2, 8)).resultado, Resultado.SIN_LOCAL)
        self.assertFalse(Marca.objects.exists())

    def test_sin_el_permiso_de_fichar_no_se_registra(self):
        UserPermission.objects.filter(user=self.cajero, module=PermissionModule.ASISTENCIA_FICHAR).update(
            can_write=False
        )
        with self.assertRaises(PermissionDenied):
            self._fichar(_momento(2, 8))
        self.assertFalse(Marca.objects.exists())

    def test_un_empleado_de_otra_empresa_puede_fichar_en_este_local(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        fichaje = self._fichar(_momento(2, 8), codigo=de_mapogo.codigo_carnet)
        self.assertTrue(fichaje.registrada)
        self.assertEqual(fichaje.marca.sucursal, self.terminal)


class PantallaDeFichajeTests(FichajeTestCase):
    def test_el_cajero_registra_y_ve_el_resultado(self):
        self._login(self.cajero)
        response = self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-resultado="registrada"')
        self.assertContains(response, "Juan Perez")
        self.assertContains(response, "Entrada")
        self.assertEqual(Marca.objects.count(), 1)

    def test_la_camara_recibe_json(self):
        self._login(self.cajero)
        response = self.client.post(
            reverse("asistencia:fichar"),
            {"codigo": self.empleado.codigo_carnet},
            HTTP_ACCEPT="application/json",
        )
        datos = response.json()
        self.assertTrue(datos["registrada"])
        self.assertEqual(datos["nombre"], "Juan Perez")
        self.assertEqual(datos["tipo"], "Entrada")
        self.assertRegex(datos["hora"], r"^\d{2}:\d{2}$")

    def test_la_pantalla_trae_la_camara_con_jsqr_servido_desde_gerayse(self):
        # Sin CDN (D7): el archivo tiene que existir en static y la pagina pedirlo de ahi.
        for archivo in ("asistencia/vendor/jsQR-1.4.0.js", "asistencia/fichar-camara.js"):
            self.assertIsNotNone(finders.find(archivo), archivo)
        self._login(self.cajero)
        response = self.client.get(reverse("asistencia:fichar"))
        self.assertContains(response, 'id="fichaje-camara" hidden')
        self.assertContains(response, static("asistencia/vendor/jsQR-1.4.0.js"))
        self.assertContains(response, static("asistencia/fichar-camara.js"))
        self.assertNotContains(response, "cdn.")

    def test_un_rechazo_se_muestra_sin_marca(self):
        self._login(self.cajero)
        response = self.client.post(reverse("asistencia:fichar"), {"codigo": "cualquier cosa"})
        self.assertContains(response, 'data-resultado="desconocido"')
        self.assertFalse(Marca.objects.exists())

    def test_quien_solo_administra_no_ficha(self):
        administra = type(self.admin).objects.create_user(username="solo-admin-asis", password="test", role=self.operator_role)
        administra.empresas_permitidas.set([self.armadi])
        administra.sucursal_base = self.centro
        administra.save(update_fields=["sucursal_base"])
        UserPermission.objects.create(
            user=administra, module=PermissionModule.ASISTENCIA_ADMIN, can_read=True, can_write=True
        )
        self._login(administra)
        self.assertEqual(self.client.get(reverse("asistencia:fichar")).status_code, 403)
        response = self.client.post(reverse("asistencia:fichar"), {"codigo": self.empleado.codigo_carnet})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Marca.objects.exists())

    def test_sin_local_la_pantalla_lo_avisa(self):
        self.cajero.sucursal_base = None
        self.cajero.save(update_fields=["sucursal_base"])
        self._login(self.cajero)
        response = self.client.get(reverse("asistencia:fichar"))
        self.assertContains(response, "no tiene un local asignado")
        self.assertNotContains(response, 'name="codigo"')

    def test_el_inicio_lleva_a_fichar(self):
        self._login(self.cajero)
        self.assertContains(self.client.get(reverse("asistencia:inicio")), reverse("asistencia:fichar"))
