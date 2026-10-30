"""EP-14 US-14.10: reglas de asistencia por empresa (D18).

Riesgos que cubre:
- que sin reglas guardadas no haya con que calcular (valen las iniciales, sin escribir);
- que una jornada mal escrita o fuera de rango se guarde;
- que se puedan ver o guardar las reglas de una empresa fuera del alcance;
- que quien solo ficha cambie las reglas;
- que no quede registrado quien las cambio.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from .models import JORNADA_INICIAL_MINUTOS, TOLERANCIA_INICIAL_MINUTOS, ReglasAsistencia
from .services import guardar_reglas
from .tests_padron import PadronTestCase


class ReglasTests(PadronTestCase):
    def _guardar(self, empresa, jornada="9:00", tolerancia="15"):
        return self.client.post(
            reverse("asistencia:reglas"),
            {
                "empresa": empresa.pk,
                f"empresa-{empresa.pk}-jornada": jornada,
                f"empresa-{empresa.pk}-tolerancia": tolerancia,
            },
        )

    def test_sin_guardar_valen_las_iniciales_y_no_se_escribe_nada(self):
        reglas = ReglasAsistencia.de_empresas([self.armadi, self.mapogo])
        self.assertEqual(reglas[self.armadi.pk].jornada_minutos, JORNADA_INICIAL_MINUTOS)
        self.assertEqual(reglas[self.mapogo.pk].tolerancia_mensual_minutos, TOLERANCIA_INICIAL_MINUTOS)
        self.assertEqual(JORNADA_INICIAL_MINUTOS, 8 * 60 + 30)
        self.assertFalse(ReglasAsistencia.objects.exists())

        self._login(self.admin, [self.armadi, self.mapogo])
        response = self.client.get(reverse("asistencia:reglas"))
        self.assertContains(response, 'value="8:30"', count=2)
        self.assertContains(response, "Todavía nadie las cambió")
        self.assertFalse(ReglasAsistencia.objects.exists())

    def test_guardar_cambia_solo_esa_empresa_y_registra_quien(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        response = self._guardar(self.armadi, jornada="9", tolerancia="15")
        self.assertRedirects(response, reverse("asistencia:reglas"))
        reglas = ReglasAsistencia.objects.get(empresa=self.armadi)
        self.assertEqual((reglas.jornada_minutos, reglas.tolerancia_mensual_minutos), (540, 15))
        self.assertEqual(reglas.actualizado_por, self.admin)
        self.assertIsNotNone(reglas.actualizado_en)
        self.assertFalse(ReglasAsistencia.objects.filter(empresa=self.mapogo).exists())

        # Guardar otra vez actualiza la misma fila.
        self._guardar(self.armadi, jornada="8.45", tolerancia="0")
        reglas.refresh_from_db()
        self.assertEqual((reglas.jornada_minutos, reglas.tolerancia_mensual_minutos), (525, 0))
        self.assertEqual(ReglasAsistencia.objects.count(), 1)
        response = self.client.get(reverse("asistencia:reglas"))
        self.assertContains(response, 'value="8:45"')
        self.assertContains(response, "Último cambio: admin-padron")

    def test_jornada_mal_escrita_o_fuera_de_rango_no_se_guarda(self):
        self._login(self.admin, [self.armadi])
        for jornada in ("", "abc", "8:60", "0", "0:00", "24:01", "25", "8:3", "-8", "８:30"):
            with self.subTest(jornada=jornada):
                response = self._guardar(self.armadi, jornada=jornada)
                self.assertEqual(response.status_code, 400)
        for tolerancia in ("-1", "abc", "1441"):
            with self.subTest(tolerancia=tolerancia):
                self.assertEqual(self._guardar(self.armadi, tolerancia=tolerancia).status_code, 400)
        self.assertFalse(ReglasAsistencia.objects.exists())
        self.assertRedirects(self._guardar(self.armadi, jornada="24:00"), reverse("asistencia:reglas"))

    def test_empresa_fuera_del_alcance_no_se_ve_ni_se_guarda(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:reglas"))
        # MAPOGO aparece en el selector de empresas de la barra, pero no tiene seccion de reglas.
        self.assertContains(response, f'id="reglas-{self.armadi.pk}"')
        self.assertNotContains(response, f'id="reglas-{self.mapogo.pk}"')
        # MAPOGO esta permitida pero no seleccionada; la ajena ni siquiera permitida.
        for empresa in (self.mapogo, self.ajena):
            with self.subTest(empresa=empresa.nombre):
                self.assertEqual(self._guardar(empresa).status_code, 404)
        for valor in ("", "abc", "²"):
            with self.subTest(valor=valor):
                response = self.client.post(reverse("asistencia:reglas"), {"empresa": valor})
                self.assertEqual(response.status_code, 404)
        self.assertFalse(ReglasAsistencia.objects.exists())

    def test_quien_solo_ficha_no_las_ve_ni_las_cambia(self):
        self._login(self.cajero, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:reglas")).status_code, 403)
        self.assertEqual(self._guardar(self.armadi).status_code, 403)
        with self.assertRaises(PermissionDenied):
            guardar_reglas(actor=self.cajero, empresa=self.armadi, jornada_minutos=480, tolerancia_mensual_minutos=10)

    def test_el_servicio_valida_aunque_no_pase_por_el_form(self):
        with self.assertRaises(ValidationError):
            guardar_reglas(actor=self.admin, empresa=self.armadi, jornada_minutos=0, tolerancia_mensual_minutos=10)
        with self.assertRaises(ValidationError):
            guardar_reglas(actor=self.admin, empresa=self.ajena, jornada_minutos=480, tolerancia_mensual_minutos=10)
        self.assertFalse(ReglasAsistencia.objects.exists())

    def test_el_inicio_lleva_a_reglas(self):
        self._login(self.admin, [self.armadi])
        self.assertContains(self.client.get(reverse("asistencia:inicio")), reverse("asistencia:reglas"))
