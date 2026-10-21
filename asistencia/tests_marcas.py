"""EP-14 US-14.5: corregir marcas olvidadas o mal hechas.

Riesgos que cubre:
- una marca manual sin motivo o sin quien la cargo, o una anulacion que borre;
- que una marca anulada siga contando para el fichaje;
- cargar o anular marcas de otra empresa, o de quien solo ficha;
- marcas a futuro o duplicadas por un doble envio.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from .models import Marca
from .services import agregar_marca, anular_marca
from .tests_fichaje import FichajeTestCase, _momento


class AgregarMarcaTests(FichajeTestCase):
    # Las fechas de prueba son de noviembre de 2026: "ahora" queda fijo despues.
    AHORA = _momento(30, 23)

    def setUp(self):
        super().setUp()
        reloj = mock.patch("asistencia.services.timezone.now", return_value=self.AHORA)
        reloj.start()
        self.addCleanup(reloj.stop)

    def _agregar(self, cuando=None, tipo=Marca.Tipo.ENTRADA, motivo="Se olvidó el carnet", actor=None, sucursal=None):
        return agregar_marca(
            actor=actor or self.admin,
            empleado=self.empleado,
            momento=cuando or _momento(2, 6),
            tipo=tipo,
            sucursal=sucursal or self.centro,
            motivo=motivo,
        )

    def test_queda_manual_con_motivo_quien_y_el_local(self):
        marca = self._agregar()
        self.assertEqual(marca.origen, Marca.Origen.MANUAL)
        self.assertEqual(marca.motivo, "Se olvidó el carnet")
        self.assertEqual(marca.registrada_por, self.admin)
        self.assertEqual(marca.sucursal, self.centro)
        self.assertIsNotNone(marca.registrada_en)

    def test_exige_motivo(self):
        with self.assertRaises(ValidationError):
            self._agregar(motivo="   ")
        self.assertFalse(Marca.objects.exists())

    def test_no_acepta_una_marca_a_futuro(self):
        with self.assertRaises(ValidationError):
            self._agregar(cuando=self.AHORA + timedelta(hours=1))

    def test_no_duplica_por_un_doble_envio(self):
        self._agregar()
        with self.assertRaises(ValidationError):
            self._agregar()
        self.assertEqual(Marca.objects.count(), 1)

    def test_quien_solo_ficha_no_agrega(self):
        with self.assertRaises(PermissionDenied):
            self._agregar(actor=self.cajero)

    def test_no_agrega_en_un_local_de_una_empresa_no_permitida(self):
        with self.assertRaises(ValidationError):
            self._agregar(sucursal=self.sucursal_ajena)

    def test_llego_antes_que_el_cajero_y_el_escaneo_de_la_tarde_es_su_salida(self):
        # El caso de la respuesta 3 de Tais: el panadero entra a las 6, el cajero
        # llega a las 8. Administracion carga la entrada de las 6 y el escaneo de
        # las 14 cierra esa entrada.
        self._agregar(cuando=_momento(2, 6))
        salida = self._fichar(_momento(2, 14))
        self.assertEqual(salida.marca.tipo, Marca.Tipo.SALIDA)


class AnularMarcaTests(FichajeTestCase):
    def test_anular_no_borra_y_deja_quien_cuando_y_por_que(self):
        marca = self._fichar(_momento(2, 8)).marca
        anular_marca(actor=self.admin, marca=marca, motivo="Escaneó el carnet de un compañero")
        marca.refresh_from_db()
        self.assertTrue(marca.anulada)
        self.assertEqual(marca.anulada_por, self.admin)
        self.assertIsNotNone(marca.anulada_en)
        self.assertEqual(marca.motivo_anulacion, "Escaneó el carnet de un compañero")
        self.assertEqual(Marca.objects.count(), 1)

    def test_una_marca_anulada_no_cuenta_para_el_fichaje(self):
        marca = self._fichar(_momento(2, 8)).marca
        anular_marca(actor=self.admin, marca=marca, motivo="Error")
        # Ni como entrada abierta (el siguiente escaneo vuelve a ser entrada) ni
        # como doble escaneo (aunque pase menos de 2 minutos).
        siguiente = self._fichar(_momento(2, 8, 1))
        self.assertTrue(siguiente.registrada)
        self.assertEqual(siguiente.marca.tipo, Marca.Tipo.ENTRADA)

    def test_exige_motivo_y_no_se_anula_dos_veces(self):
        marca = self._fichar(_momento(2, 8)).marca
        with self.assertRaises(ValidationError):
            anular_marca(actor=self.admin, marca=marca, motivo="")
        anular_marca(actor=self.admin, marca=marca, motivo="Error")
        with self.assertRaises(ValidationError):
            anular_marca(actor=self.admin, marca=marca, motivo="Otra vez")

    def test_quien_solo_ficha_no_anula(self):
        marca = self._fichar(_momento(2, 8)).marca
        with self.assertRaises(PermissionDenied):
            anular_marca(actor=self.cajero, marca=marca, motivo="x")


class PantallasDeMarcasTests(FichajeTestCase):
    def _hoy(self, hora):
        return timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time()).replace(hour=hora))

    def test_agregar_por_la_pantalla(self):
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:marca_create"),
            {
                "empleado": self.empleado.pk,
                "tipo": Marca.Tipo.ENTRADA,
                # Ayer: a las 6 de hoy podria ser todavia futuro si los tests corren de madrugada.
                "fecha": (timezone.localdate() - timedelta(days=1)).isoformat(),
                "hora": "06:00",
                "sucursal": self.centro.pk,
                "motivo": "Llegó antes que el cajero",
            },
            follow=True,
        )
        self.assertContains(response, "queda señalada como corregida")
        self.assertContains(response, "Corregida por admin-padron")
        marca = Marca.objects.get()
        self.assertEqual(timezone.localtime(marca.momento).strftime("%H:%M"), "06:00")

    def test_el_formulario_solo_ofrece_empleados_y_locales_del_alcance(self):
        self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        self._login(self.admin, [self.armadi])
        form = self.client.get(reverse("asistencia:marca_create")).context["form"]
        self.assertEqual({e.nombre for e in form.fields["empleado"].queryset}, {"Juan Perez"})
        self.assertNotIn(self.vivre, set(form.fields["sucursal"].queryset))

    def test_el_listado_respeta_el_alcance_y_oculta_las_anuladas(self):
        with mock.patch("asistencia.fichaje.timezone.now", return_value=self._hoy(8)):
            propia = fichar_como(self, self.empleado)
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        with mock.patch("asistencia.fichaje.timezone.now", return_value=self._hoy(9)):
            fichar_como(self, de_mapogo)
        self._login(self.admin, [self.armadi])
        url = reverse("asistencia:marca_list")
        response = self.client.get(url)
        self.assertContains(response, f'id="marca-{propia.pk}"')
        self.assertNotContains(response, "De Mapogo")

        anular_marca(actor=self.admin, marca=propia, motivo="Error")
        self.assertNotContains(self.client.get(url), f'id="marca-{propia.pk}"')
        self.assertContains(self.client.get(url, {"anuladas": "on"}), f'id="marca-{propia.pk}"')

    def test_anular_por_la_pantalla_y_una_de_otra_empresa_da_404(self):
        marca = self._fichar(timezone.now() - timedelta(hours=1)).marca
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        ajena = self._fichar(timezone.now() - timedelta(hours=1), codigo=de_mapogo.codigo_carnet).marca
        self._login(self.admin, [self.armadi])
        self.assertEqual(
            self.client.post(reverse("asistencia:marca_anular", args=[ajena.pk]), {"motivo": "x"}).status_code, 404
        )
        url = reverse("asistencia:marca_anular", args=[marca.pk])
        self.assertEqual(self.client.post(url, {"motivo": ""}).status_code, 400)
        self.assertEqual(self.client.post(url, {"motivo": "Error"}).status_code, 302)
        marca.refresh_from_db()
        ajena.refresh_from_db()
        self.assertTrue(marca.anulada)
        self.assertFalse(ajena.anulada)

    def test_quien_solo_ficha_no_entra_a_marcas(self):
        marca = self._fichar(timezone.now() - timedelta(hours=1)).marca
        self._login(self.cajero)
        for url in (
            reverse("asistencia:marca_list"),
            reverse("asistencia:marca_create"),
            reverse("asistencia:marca_anular", args=[marca.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)


def fichar_como(caso, empleado):
    from .fichaje import fichar

    return fichar(actor=caso.cajero, codigo=empleado.codigo_carnet).marca
