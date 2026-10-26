"""EP-14 D15: asistencia de un dia por local.

Riesgos que cubre:
- que alguien trabajando aparezca como que salio, o al reves;
- horas mal sumadas (turno cortado, medianoche, marcas anuladas);
- que falte quien no ficho, o que aparezca como que no ficho alguien que si;
- fugas de alcance: locales o nombres de empleados de empresas no seleccionadas;
- que quien solo ficha entre a la pantalla.
"""

from django.urls import reverse

from .models import Marca
from .por_local import NOMBRE_OCULTO, asistencia_del_dia
from .tests_fichaje import FichajeTestCase, _momento


class PorLocalTestCase(FichajeTestCase):
    def _marca(self, cuando, tipo, empleado=None, sucursal=None, origen=Marca.Origen.ESCANEO, anulada=False):
        return Marca.objects.create(
            empleado=empleado or self.empleado,
            tipo=tipo,
            momento=cuando,
            sucursal=sucursal or self.terminal,
            origen=origen,
            registrada_por=self.cajero,
            motivo="Se olvidó el carnet" if origen == Marca.Origen.MANUAL else "",
            anulada=anulada,
        )

    def _dia(self, dia, ahora=None, sucursales=None, empresas=None):
        sucursales = sucursales or [self.terminal, self.centro]
        empresas = empresas or [self.armadi.pk]
        resumenes = asistencia_del_dia(_momento(dia, 0).date(), sucursales, empresas, ahora=ahora or _momento(30, 23))
        return {r.sucursal.codigo: r for r in resumenes}


class AsistenciaDelDiaTests(PorLocalTestCase):
    def test_quien_entro_y_salio_figura_con_sus_horas(self):
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 16, 5), Marca.Tipo.SALIDA)
        fila = self._dia(2)["TERM-01"].filas[0]
        self.assertEqual(fila.nombre, "Juan Perez")
        self.assertEqual(fila.horas_texto, "8:05")
        self.assertIsNone(fila.trabajando_desde)
        self.assertEqual(fila.ultima_salida, _momento(2, 16, 5))

    def test_quien_entro_hoy_y_no_salio_esta_trabajando(self):
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        resumen = self._dia(2, ahora=_momento(2, 11))["TERM-01"]
        self.assertEqual(resumen.filas[0].trabajando_desde, _momento(2, 8))
        self.assertEqual(resumen.filas[0].lleva_texto, "3:00")
        self.assertEqual(resumen.trabajando, 1)

    def test_un_dia_pasado_sin_salida_queda_senalado(self):
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        fila = self._dia(2, ahora=_momento(5, 10))["TERM-01"].filas[0]
        self.assertIsNone(fila.trabajando_desde)
        self.assertTrue(fila.sin_salida)

    def test_turno_cortado_suma_los_dos_tramos(self):
        for desde, hasta in ((9, 13), (18, 23)):
            self._marca(_momento(4, desde), Marca.Tipo.ENTRADA)
            self._marca(_momento(4, hasta), Marca.Tipo.SALIDA)
        fila = self._dia(4)["TERM-01"].filas[0]
        self.assertEqual(len(fila.tramos), 2)
        self.assertEqual(fila.horas_texto, "9:00")

    def test_la_salida_de_madrugada_cuenta_en_el_dia_de_la_entrada(self):
        self._marca(_momento(5, 22), Marca.Tipo.ENTRADA)
        self._marca(_momento(6, 2), Marca.Tipo.SALIDA)
        self.assertEqual(self._dia(5)["TERM-01"].filas[0].horas_texto, "4:00")
        self.assertEqual(self._dia(6)["TERM-01"].filas, [])

    def test_una_marca_anulada_no_cuenta(self):
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 9), Marca.Tipo.SALIDA, anulada=True)
        self._marca(_momento(2, 16), Marca.Tipo.SALIDA)
        self.assertEqual(self._dia(2)["TERM-01"].filas[0].horas_texto, "8:00")

    def test_cada_tramo_va_al_local_donde_se_ficho(self):
        # Juan tiene local habitual Centro pero ficho en Terminal.
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 16), Marca.Tipo.SALIDA)
        dia = self._dia(2)
        self.assertEqual(len(dia["TERM-01"].filas), 1)
        self.assertEqual(dia["CENT-02"].filas, [])
        # Y como ficho, no figura entre los que no ficharon de su local habitual.
        self.assertEqual(dia["CENT-02"].sin_marcas, [])

    def test_quien_no_ficho_figura_en_su_local_habitual(self):
        ana = self._empleado(nombre="Ana Gomez", dni="32000111", sucursal=self.centro)
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        dia = self._dia(2)
        self.assertEqual(dia["CENT-02"].sin_marcas, [ana])

    def test_la_corregida_queda_senalada(self):
        self._marca(_momento(2, 6), Marca.Tipo.ENTRADA, origen=Marca.Origen.MANUAL)
        self._marca(_momento(2, 14), Marca.Tipo.SALIDA)
        self.assertTrue(self._dia(2)["TERM-01"].filas[0].corregida)

    def test_un_empleado_de_otra_empresa_no_se_nombra(self):
        de_mapogo = self._empleado(nombre="De Mapogo", dni="31000111", sucursal=self.vivre)
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA, empleado=de_mapogo)
        fila = self._dia(2)["TERM-01"].filas[0]
        self.assertEqual(fila.nombre, NOMBRE_OCULTO)
        self.assertIsNone(fila.empleado_id)


class PantallaPorLocalTests(PorLocalTestCase):
    def test_muestra_solo_los_locales_del_alcance(self):
        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:por_local"))
        self.assertEqual(response.status_code, 200)
        codigos = {r.sucursal.codigo for r in response.context["resumenes"]}
        self.assertEqual(codigos, {"CENT-02", "TERM-01"})
        self.assertNotContains(response, "Vivré")

    def test_se_filtra_por_dia_y_local(self):
        self._marca(_momento(2, 8), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 16), Marca.Tipo.SALIDA)
        self._login(self.admin, [self.armadi])
        response = self.client.get(
            reverse("asistencia:por_local"), {"fecha": _momento(2, 0).date().isoformat(), "local": self.terminal.pk}
        )
        self.assertEqual([r.sucursal for r in response.context["resumenes"]], [self.terminal])
        self.assertContains(response, "Juan Perez")
        self.assertContains(response, "Salió 16:00")
        self.assertContains(response, "1 fichó")
        self.assertEqual(response.context["ficharon"], 1)

    def test_un_filtro_invalido_no_rompe(self):
        self._login(self.admin, [self.armadi])
        for parametros in ({"fecha": "no-es-fecha"}, {"local": "abc"}, {"local": self.vivre.pk}):
            with self.subTest(parametros=parametros):
                self.assertEqual(self.client.get(reverse("asistencia:por_local"), parametros).status_code, 200)

    def test_quien_solo_ficha_no_entra(self):
        self._login(self.cajero, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:por_local")).status_code, 403)

    def test_el_inicio_lleva_a_por_local(self):
        self._login(self.admin, [self.armadi])
        self.assertContains(self.client.get(reverse("asistencia:inicio")), reverse("asistencia:por_local"))
