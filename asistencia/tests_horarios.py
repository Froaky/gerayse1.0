"""EP-14 US-14.12 y US-14.13: permiso de cargar horarios y grilla semanal (D18).

Riesgos que cubre:
- una casilla mal leida (un "8-16" tomado como dos entradas, una hora imposible);
- que el encargado cambie hoy o un dia pasado (borrar una llegada tarde), ni por URL;
- que el encargado vea o cargue otro local, o un empleado de otro local;
- que una casilla con error deje la semana guardada a medias;
- que copiar la semana anterior pise lo ya cargado;
- que un cambio no quede en el historial;
- que el permiso nuevo abra padron, marcas o Excel;
- que los horarios y las reglas no entren al respaldo.
"""

from datetime import date, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from users.models import PermissionModule, UserPermission

from .alcance import locales_de_horarios
from .horarios import Celda, CeldaInvalida, leer_celda, lunes_de
from .models import Horario, HorarioCambio
from .respaldo import generar_respaldo, restaurar_respaldo
from .services import copiar_semana_anterior, guardar_reglas, guardar_semana
from .tests_padron import PadronTestCase


class LeerCeldaTests(PadronTestCase):
    def test_formatos_que_entiende(self):
        casos = {
            "": Celda(),
            "   ": Celda(),
            "F": Celda(franco=True),
            " Franco ": Celda(franco=True),
            "8": Celda(entradas=(time(8),)),
            "8:30": Celda(entradas=(time(8, 30),)),
            "08.30": Celda(entradas=(time(8, 30),)),
            "8hs": Celda(entradas=(time(8),)),
            "8h30": Celda(entradas=(time(8, 30),)),
            "0:15": Celda(entradas=(time(0, 15),)),
            "8 17": Celda(entradas=(time(8), time(17))),
            "8:00 y 17:00": Celda(entradas=(time(8), time(17))),
            "8/17:30": Celda(entradas=(time(8), time(17, 30))),
            "8, 17": Celda(entradas=(time(8), time(17))),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(leer_celda(texto), esperado)

    def test_lo_que_no_entiende_lo_dice(self):
        # Una casilla con solo separadores no es "vacia": si no, borraria el horario sin aviso.
        # "8,15" puede ser 8:15 o dos entradas: se pide que lo aclare.
        for texto in ("8-16", "8 a 16", "25", "8:61", "8:5", "abc", "17 8", "8 8", "8 12 17", "８", "F 8",
                      ",", "/", "y", ", /", "8,15", "8,30", "13,15", "8,5"):
            with self.subTest(texto=texto):
                with self.assertRaises(CeldaInvalida):
                    leer_celda(texto)

    def test_la_coma_propone_lo_que_escribio(self):
        for texto, mensaje in (
            ("8,15", "escribí 8:15 si es una hora, o 8 15 si tiene turno cortado"),
            ("13,30", "escribí 13:30 si es una hora, o 13 30 si tiene turno cortado"),
            ("8,5", "escribí 8:05 si es una hora, o 8 5 si tiene turno cortado"),
        ):
            with self.subTest(texto=texto):
                with self.assertRaisesMessage(CeldaInvalida, mensaje):
                    leer_celda(texto)
        # Con minutos o con espacio no hay duda: son dos entradas.
        self.assertEqual(leer_celda("8:00,17:00"), Celda(entradas=(time(8), time(17))))

    def test_el_texto_de_la_celda_se_vuelve_a_leer_igual(self):
        for celda in (Celda(), Celda(franco=True), Celda(entradas=(time(8, 5),)), Celda(entradas=(time(8), time(17)))):
            with self.subTest(celda=celda):
                self.assertEqual(leer_celda(celda.texto), celda)


class GrillaTestCase(PadronTestCase):
    def setUp(self):
        super().setUp()
        self.encargado = self.cajero  # rol comun, sucursal base Centro, ARMADI
        self.encargado.sucursal_base = self.centro
        self.encargado.save(update_fields=["sucursal_base"])
        UserPermission.objects.create(
            user=self.encargado, module=PermissionModule.ASISTENCIA_HORARIOS, can_read=True, can_write=True
        )
        self.juan = self._empleado(nombre="Juan Perez", dni="30123456")
        self.ana = self._empleado(nombre="Ana Gomez", dni="30999888")
        self.de_terminal = self._empleado(nombre="Beto Terminal", dni="31000111", sucursal=self.terminal)
        self.hoy = timezone.localdate()
        self.proxima = lunes_de(self.hoy) + timedelta(days=7)
        self.pasada = lunes_de(self.hoy) - timedelta(days=7)

    def _celda(self, empleado, dia):
        return f"c-{empleado.pk}-{dia:%Y%m%d}"

    def _post(self, lunes, celdas, local=None, originales=None, **extra):
        """Como la pantalla: cada fila viaja con lo que mostraban sus 7 casillas al
        abrirse ("o-<empleado>", separadas por "|"). Sin `originales`, es una
        pantalla recien abierta (lo que hay en la base)."""
        originales = originales or {}
        datos = {"local": (local or self.centro).pk, "semana": lunes.isoformat(), **extra}
        for (empleado, dia), texto in celdas.items():
            datos[self._celda(empleado, dia)] = texto
        for empleado in {empleado for empleado, _ in celdas}:
            fila = []
            for n in range(7):
                dia = lunes + timedelta(days=n)
                fila.append(originales.get((empleado, dia), Celda.de_horario(self._horario(empleado, dia)).texto))
            datos[f"o-{empleado.pk}"] = "|".join(fila)
        return self.client.post(reverse("asistencia:horarios"), datos)

    def _get(self, lunes, local=None):
        return self.client.get(
            reverse("asistencia:horarios"), {"local": (local or self.centro).pk, "semana": lunes.isoformat()}
        )

    def _horario(self, empleado, dia):
        return Horario.objects.filter(empleado=empleado, fecha=dia).first()


class GrillaDelEncargadoTests(GrillaTestCase):
    def test_carga_la_semana_que_viene_y_queda_en_el_historial(self):
        self._login(self.encargado, [self.armadi])
        lunes = self.proxima
        response = self._post(
            lunes,
            {
                (self.juan, lunes): "8",
                (self.juan, lunes + timedelta(days=1)): "8 17",
                (self.juan, lunes + timedelta(days=2)): "F",
                (self.ana, lunes): "",
            },
        )
        self.assertRedirects(response, f"{reverse('asistencia:horarios')}?local={self.centro.pk}&semana={lunes.isoformat()}")
        self.assertEqual(self._horario(self.juan, lunes).entradas, (time(8),))
        martes = self._horario(self.juan, lunes + timedelta(days=1))
        self.assertEqual((martes.entrada_1, martes.entrada_2, martes.cargado_por), (time(8), time(17), self.encargado))
        self.assertTrue(self._horario(self.juan, lunes + timedelta(days=2)).franco)
        self.assertIsNone(self._horario(self.ana, lunes))
        self.assertEqual(
            sorted(HorarioCambio.objects.values_list("antes", "despues")),
            [("", "8:00"), ("", "8:00 y 17:00"), ("", "Franco")],
        )
        pagina = self._get(lunes)
        for valor in ("8:00", "8:00 y 17:00", "Franco"):
            self.assertContains(pagina, f'value="{valor}"')

    def test_cambiar_y_borrar_guarda_el_antes(self):
        self._login(self.encargado, [self.armadi])
        lunes = self.proxima
        self._post(lunes, {(self.juan, lunes): "8", (self.ana, lunes): "F"})
        self._post(lunes, {(self.juan, lunes): "9:30", (self.ana, lunes): ""})
        self.assertEqual(self._horario(self.juan, lunes).entradas, (time(9, 30),))
        self.assertIsNone(self._horario(self.ana, lunes))
        ultimos = list(HorarioCambio.objects.order_by("pk").values_list("antes", "despues"))[-2:]
        self.assertCountEqual(ultimos, [("8:00", "9:30"), ("Franco", "")])
        # Mandar lo mismo no suma historial.
        antes = HorarioCambio.objects.count()
        self._post(lunes, {(self.juan, lunes): "9:30"})
        self.assertEqual(HorarioCambio.objects.count(), antes)

    def test_una_casilla_con_error_no_guarda_nada_y_conserva_lo_escrito(self):
        self._login(self.encargado, [self.armadi])
        lunes = self.proxima
        response = self._post(lunes, {(self.juan, lunes): "8", (self.ana, lunes): "8-16"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Horario.objects.exists())
        self.assertContains(response, "No se guardó nada", status_code=400)
        self.assertContains(response, 'value="8-16"', status_code=400)
        self.assertContains(response, "celda--error", status_code=400)

    def test_hoy_y_los_dias_pasados_estan_cerrados_para_el_encargado(self):
        self._login(self.encargado, [self.armadi])
        pagina = self._get(lunes_de(self.hoy))
        self.assertContains(pagina, f'name="{self._celda(self.juan, self.hoy)}"')
        self.assertContains(pagina, "pedíselo a administración")
        # Por URL tampoco: un dia pasado o de hoy da 403 y no guarda.
        for dia in (self.hoy, self.pasada):
            with self.subTest(dia=dia):
                response = self._post(lunes_de(dia), {(self.juan, dia): "8"})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(Horario.objects.exists())
        with self.assertRaises(PermissionDenied):
            guardar_semana(actor=self.encargado, sucursal=self.centro, lunes=self.pasada, celdas={(self.juan.pk, self.pasada): Celda(franco=True)})

    def test_una_pantalla_vieja_no_pisa_lo_que_otro_guardo(self):
        lunes = self.proxima
        miercoles = lunes + timedelta(days=2)
        # El encargado abrio la semana vacia; mientras tanto administracion le pone franco a Juan el miercoles.
        guardar_semana(actor=self.admin, sucursal=self.centro, lunes=lunes, celdas={(self.juan.pk, miercoles): Celda(franco=True)})
        self._login(self.encargado, [self.armadi])
        # Su pantalla manda todas las casillas como las vio (vacias) y solo cambio el lunes de Ana.
        response = self._post(
            lunes,
            {(self.juan, miercoles): "", (self.ana, lunes): "9"},
            originales={(self.juan, miercoles): "", (self.ana, lunes): ""},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self._horario(self.juan, miercoles).franco)
        self.assertEqual(self._horario(self.ana, lunes).entradas, (time(9),))
        self.assertFalse(HorarioCambio.objects.filter(empleado=self.juan, cambiado_por=self.encargado).exists())

    def test_si_otro_cambio_la_misma_casilla_avisa_y_no_pisa(self):
        lunes = self.proxima
        guardar_semana(actor=self.admin, sucursal=self.centro, lunes=lunes, celdas={(self.juan.pk, lunes): Celda(entradas=(time(8),))})
        self._login(self.encargado, [self.armadi])
        response = self._post(lunes, {(self.juan, lunes): "10"}, originales={(self.juan, lunes): ""})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._horario(self.juan, lunes).entradas, (time(8),))
        self.assertContains(response, "ahora dice «8:00»", status_code=400)
        self.assertContains(response, 'value="10"', status_code=400)
        # La pantalla ya trae lo que hay ahora: volver a guardar deja lo suyo, a sabiendas.
        self.assertContains(response, f'name="o-{self.juan.pk}" value="8:00||||||"', status_code=400)
        self.assertContains(response, "let cambiada = true;", status_code=400)
        response = self._post(lunes, {(self.juan, lunes): "10"}, originales={(self.juan, lunes): "8:00"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._horario(self.juan, lunes).entradas, (time(10),))

    def test_una_grilla_abierta_ayer_guarda_aunque_hoy_haya_quedado_cerrado(self):
        # La casilla de hoy estaba abierta cuando se dibujo la pagina y viaja sin cambios.
        manana = self.hoy + timedelta(days=1)
        celdas = {(self.juan, self.hoy): "", (self.ana, manana): "8"}
        self._login(self.encargado, [self.armadi])
        response = self._post(lunes_de(manana), celdas) if lunes_de(manana) == lunes_de(self.hoy) else None
        if response is None:
            # Hoy es domingo: la de manana es de otra semana. Se prueba con la de hoy sola.
            response = self._post(lunes_de(self.hoy), {(self.juan, self.hoy): ""})
            self.assertEqual(response.status_code, 302)
            return
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._horario(self.ana, manana).entradas, (time(8),))

    def test_cambiar_un_dia_cerrado_muestra_la_grilla_con_el_aviso(self):
        self._login(self.encargado, [self.armadi])
        response = self._post(lunes_de(self.hoy), {(self.juan, self.hoy): "8"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Hoy y los días anteriores solo los cambia administración.", status_code=403)
        self.assertContains(response, 'id="grilla-horarios"', status_code=403)
        self.assertFalse(Horario.objects.exists())

    def test_una_pantalla_sin_los_valores_originales_se_reabre_sin_guardar(self):
        guardar_semana(actor=self.admin, sucursal=self.centro, lunes=self.proxima, celdas={(self.ana.pk, self.proxima): Celda(franco=True)})
        self._login(self.encargado, [self.armadi])
        datos = {
            "local": self.centro.pk,
            "semana": self.proxima.isoformat(),
            self._celda(self.juan, self.proxima): "8",
            self._celda(self.ana, self.proxima): "",
        }
        for fila in (None, "8:00|F"):  # sin el campo, o con una cantidad de dias que no es 7
            with self.subTest(fila=fila):
                if fila is not None:
                    datos[f"o-{self.juan.pk}"] = fila
                response = self.client.post(reverse("asistencia:horarios"), datos, follow=True)
                self.assertContains(response, "Esta pantalla había quedado vieja")
                # Se reabre con lo guardado: reenviarla no puede borrar el franco de Ana.
                self.assertNotContains(response, 'value="8"')
                self.assertContains(response, 'value="Franco"')
        self.assertTrue(self._horario(self.ana, self.proxima).franco)
        self.assertIsNone(self._horario(self.juan, self.proxima))

    def test_un_dia_que_cerro_mientras_cargaba_frena_antes_que_el_choque(self):
        # Administracion cargo 9 en el dia de hoy; el encargado tenia la casilla abierta
        # desde ayer, vacia, y escribio 10.
        guardar_semana(actor=self.admin, sucursal=self.centro, lunes=lunes_de(self.hoy), celdas={(self.juan.pk, self.hoy): Celda(entradas=(time(9),))})
        self._login(self.encargado, [self.armadi])
        response = self._post(lunes_de(self.hoy), {(self.juan, self.hoy): "10"}, originales={(self.juan, self.hoy): ""})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "volvé a guardar", status_code=403)
        self.assertContains(response, "Este día ya cerró: no se guardó lo que escribiste", status_code=403)
        # La casilla cerrada muestra lo guardado, no lo escrito.
        self.assertNotContains(response, 'value="10"', status_code=403)
        self.assertEqual(self._horario(self.juan, self.hoy).entradas, (time(9),))

    def test_despues_de_un_choque_lo_que_no_toco_muestra_lo_de_ahora(self):
        lunes = self.proxima
        martes = lunes + timedelta(days=1)
        # Mientras el encargado tenia la semana vacia abierta, administracion cargo el lunes y el martes de Juan.
        guardar_semana(
            actor=self.admin, sucursal=self.centro, lunes=lunes,
            celdas={(self.juan.pk, lunes): Celda(entradas=(time(8),)), (self.juan.pk, martes): Celda(franco=True)},
        )
        self._login(self.encargado, [self.armadi])
        response = self._post(
            lunes, {(self.juan, lunes): "10", (self.juan, martes): ""}, originales={(self.juan, lunes): "", (self.juan, martes): ""}
        )
        self.assertEqual(response.status_code, 400)
        # El martes no lo toco: se ve el franco que cargo administracion, no la casilla vacia de su pantalla.
        self.assertContains(response, f'name="{self._celda(self.juan, martes)}" value="Franco"', status_code=400)
        self.assertTrue(self._horario(self.juan, martes).franco)

    def test_un_campo_por_fila_para_los_valores_originales(self):
        self._login(self.encargado, [self.armadi])
        html = self._get(self.proxima).content.decode()
        self.assertEqual(html.count('name="o-'), 2)

    def test_una_semana_o_un_id_tocados_a_mano_no_dan_error(self):
        self._login(self.encargado, [self.armadi])
        for semana in ("0001-01-01", "9999-12-31", "2026-02-30", "abc"):
            with self.subTest(semana=semana):
                response = self.client.get(reverse("asistencia:horarios"), {"semana": semana})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["lunes"], lunes_de(self.hoy))
        self.assertEqual(self.client.get(reverse("asistencia:horarios"), {"local": "9" * 5000}).status_code, 404)
        self._login(self.admin, [self.armadi])
        self.assertEqual(self.client.post(reverse("asistencia:reglas"), {"empresa": "9" * 5000}).status_code, 404)
        self.assertEqual(self.client.post(reverse("asistencia:horarios"), {"semana": "0001-01-01", "accion": "copiar"}).status_code, 302)

    def test_las_casillas_cerradas_salen_deshabilitadas(self):
        self._login(self.encargado, [self.armadi])
        pagina = self._get(self.pasada).content.decode()
        casilla = pagina[pagina.index(f'name="{self._celda(self.juan, self.pasada)}"'):]
        self.assertIn("disabled", casilla[: casilla.index(">")])

    def test_administracion_corrige_cualquier_dia(self):
        self._login(self.admin, [self.armadi])
        response = self._post(self.pasada, {(self.juan, self.pasada): "7"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._horario(self.juan, self.pasada).cargado_por, self.admin)

    def test_solo_sus_locales_y_sus_empleados(self):
        self._login(self.encargado, [self.armadi])
        self.assertEqual(self._get(self.proxima, local=self.terminal).status_code, 404)
        self.assertEqual(self._post(self.proxima, {(self.de_terminal, self.proxima): "8"}, local=self.terminal).status_code, 404)
        for valor in ("abc", "²", "999999"):
            with self.subTest(valor=valor):
                response = self.client.get(reverse("asistencia:horarios"), {"local": valor})
                self.assertEqual(response.status_code, 404)
        # Un empleado de otro local metido en la grilla del suyo: no se guarda nada.
        with self.assertRaises(ValidationError):
            guardar_semana(
                actor=self.encargado,
                sucursal=self.centro,
                lunes=self.proxima,
                celdas={(self.juan.pk, self.proxima): Celda(franco=True), (self.de_terminal.pk, self.proxima): Celda(franco=True)},
            )
        with self.assertRaises(PermissionDenied):
            guardar_semana(actor=self.encargado, sucursal=self.terminal, lunes=self.proxima, celdas={})
        # Un dia de otra semana tampoco.
        with self.assertRaises(ValidationError):
            guardar_semana(
                actor=self.encargado, sucursal=self.centro, lunes=self.proxima,
                celdas={(self.juan.pk, self.proxima + timedelta(days=7)): Celda(franco=True)},
            )
        self.assertFalse(Horario.objects.exists())
        pagina = self._get(self.proxima)
        self.assertContains(pagina, "Juan Perez")
        self.assertNotContains(pagina, "Beto Terminal")

    def test_copiar_la_semana_anterior_no_pisa_lo_cargado(self):
        self._login(self.admin, [self.armadi])
        anterior = self.proxima - timedelta(days=7)
        self._post(anterior, {(self.juan, anterior): "8", (self.juan, anterior + timedelta(days=1)): "F", (self.ana, anterior): "14"})
        self._post(self.proxima, {(self.juan, self.proxima + timedelta(days=1)): "10"})
        self._login(self.encargado, [self.armadi])
        response = self._post(self.proxima, {}, accion="copiar")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._horario(self.juan, self.proxima).entradas, (time(8),))
        self.assertEqual(self._horario(self.juan, self.proxima + timedelta(days=1)).entradas, (time(10),))
        self.assertEqual(self._horario(self.ana, self.proxima).entradas, (time(14),))
        # Copiar de nuevo no hace nada.
        self.assertEqual(copiar_semana_anterior(actor=self.encargado, sucursal=self.centro, lunes=self.proxima), 0)

    def test_copiar_en_la_semana_actual_solo_llena_los_dias_abiertos(self):
        actual = lunes_de(self.hoy)
        anterior = actual - timedelta(days=7)
        guardar_semana(
            actor=self.admin, sucursal=self.centro, lunes=anterior,
            celdas={(self.juan.pk, anterior + timedelta(days=n)): Celda(entradas=(time(8),)) for n in range(7)},
        )
        copiadas = copiar_semana_anterior(actor=self.encargado, sucursal=self.centro, lunes=actual)
        abiertos = [actual + timedelta(days=n) for n in range(7) if actual + timedelta(days=n) > self.hoy]
        self.assertEqual(copiadas, len(abiertos))
        self.assertEqual(sorted(Horario.objects.filter(fecha__gte=actual).values_list("fecha", flat=True)), abiertos)

    def test_el_modelo_no_acepta_franco_con_hora_ni_segunda_antes(self):
        for datos in (
            {"franco": True, "entrada_1": time(8)},
            {"franco": False},
            {"franco": False, "entrada_1": time(17), "entrada_2": time(8)},
        ):
            with self.subTest(datos=datos):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Horario.objects.create(empleado=self.juan, fecha=date(2026, 11, 2), **datos)


class PermisoDeHorariosTests(GrillaTestCase):
    def test_el_permiso_abre_horarios_y_nada_mas(self):
        UserPermission.objects.filter(user=self.encargado, module=PermissionModule.ASISTENCIA_FICHAR).delete()
        self._login(self.encargado, [self.armadi])
        inicio = self.client.get(reverse("asistencia:inicio"))
        self.assertContains(inicio, 'id="asistencia-horarios"')
        self.assertNotContains(inicio, 'id="asistencia-administrar"')
        self.assertNotContains(inicio, 'id="asistencia-fichar"')
        self.assertTrue(self.encargado.can_access_asistencia())
        for nombre in ("empleado_list", "marca_list", "excel", "reglas", "carnets", "por_local"):
            with self.subTest(pantalla=nombre):
                self.assertEqual(self.client.get(reverse(f"asistencia:{nombre}")).status_code, 403)

    def test_sin_permiso_no_entra(self):
        UserPermission.objects.filter(user=self.encargado, module=PermissionModule.ASISTENCIA_HORARIOS).delete()
        self._login(self.encargado, [self.armadi])
        self.assertEqual(self._get(self.proxima).status_code, 403)
        self.assertEqual(self._post(self.proxima, {(self.juan, self.proxima): "8"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            guardar_semana(actor=self.encargado, sucursal=self.centro, lunes=self.proxima, celdas={})

    def test_los_locales_del_encargado_y_los_de_administracion(self):
        factory = RequestFactory()

        def locales(user, empresas):
            request = factory.get("/")
            request.user = user
            request.session = {"empresa_ids": [e.pk for e in empresas]}
            return list(locales_de_horarios(request))

        self.assertEqual(locales(self.encargado, [self.armadi]), [self.centro])
        self.encargado.sucursales_asistencia.set([self.terminal, self.vivre])
        # Vivre es de MAPOGO, que el encargado no tiene permitida.
        self.assertEqual(locales(self.encargado, [self.armadi, self.mapogo]), [self.centro, self.terminal])
        self.assertEqual(locales(self.encargado, [self.mapogo]), [])
        self.assertEqual(locales(self.admin, [self.armadi, self.mapogo]), [self.centro, self.terminal, self.vivre])

    def test_sin_locales_lo_dice(self):
        self.encargado.sucursal_base = None
        self.encargado.save(update_fields=["sucursal_base"])
        self._login(self.encargado, [self.armadi])
        response = self.client.get(reverse("asistencia:horarios"))
        self.assertContains(response, "no tiene ningún local para cargar horarios")


class RespaldoDeHorariosTests(GrillaTestCase):
    def test_reglas_horarios_e_historial_entran_al_respaldo(self):
        guardar_reglas(actor=self.admin, empresa=self.armadi, jornada_minutos=480, tolerancia_mensual_minutos=20)
        guardar_semana(
            actor=self.admin, sucursal=self.centro, lunes=self.proxima,
            celdas={(self.juan.pk, self.proxima): Celda(entradas=(time(8), time(17))), (self.ana.pk, self.proxima): Celda(franco=True)},
        )
        contenido = generar_respaldo()
        resultado = restaurar_respaldo(contenido, reemplazar=True)
        self.assertEqual(resultado.cantidades["ReglasAsistencia"], 1)
        self.assertEqual(resultado.cantidades["Horario"], 2)
        self.assertEqual(resultado.cantidades["HorarioCambio"], 2)
        self.assertEqual(self._horario(self.juan, self.proxima).entradas, (time(8), time(17)))
