"""EP-14 US-14.14: llegadas tarde y faltas contra el horario cargado (D18).

Riesgos que cubre:
- minutos tarde mal contados (segundos, turno cortado, la segunda entrada);
- una falta marcada hoy (todavia puede llegar), en un franco o antes del alta;
- un franco trabajado tomado como tarde o falta;
- dias sin horario contados fuera del alta o despues de la baja;
- la tolerancia del mes justa contada como pasada;
- que el Excel no muestre lo que dice el resumen.
"""

from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.utils import timezone
from openpyxl import load_workbook

from .excel import generar_excel, observacion_del_dia
from .models import Empleado, Horario, Marca
from .resumen import calcular_meses
from .services import dar_de_baja_empleado, guardar_reglas
from .tests_excel import ExcelTestCase
from .tests_fichaje import _momento

HOY = date(2026, 11, 10)


class TardanzasYFaltasTestCase(ExcelTestCase):
    def setUp(self):
        super().setUp()
        self._alta(self.empleado, date(2026, 10, 1))

    def _alta(self, empleado, dia):
        Empleado.objects.filter(pk=empleado.pk).update(creado_en=timezone.make_aware(datetime.combine(dia, time(9))))
        empleado.refresh_from_db()

    def _horario(self, dia, *entradas, franco=False, empleado=None):
        entradas = (*entradas, None, None)
        return Horario.objects.create(
            empleado=empleado or self.empleado,
            fecha=date(2026, 11, dia),
            franco=franco,
            entrada_1=None if franco else entradas[0],
            entrada_2=None if franco else entradas[1],
        )

    def _resumen(self, hoy=HOY, empleado=None):
        empleado = empleado or self.empleado
        return calcular_meses([(empleado, list(empleado.marcas.all()))], 2026, 11, hoy=hoy)[0]

    def _dia(self, resumen, dia):
        return next(d for d in resumen.dias if d.fecha == date(2026, 11, dia))


class LlegadasTardeTests(TardanzasYFaltasTestCase):
    def test_a_horario_y_un_minuto_tarde(self):
        self._horario(2, time(8))
        self._turno(2, 8, 16)
        self._horario(3, time(8))
        self._marca(_momento(3, 8, 1), Marca.Tipo.ENTRADA)
        self._horario(4, time(8))
        self._marca(_momento(4, 7, 50), Marca.Tipo.ENTRADA)
        resumen = self._resumen()
        self.assertEqual(self._dia(resumen, 2).tarde, timedelta())
        self.assertEqual(self._dia(resumen, 3).tarde, timedelta(minutes=1))
        self.assertEqual(self._dia(resumen, 4).tarde, timedelta())
        self.assertEqual((resumen.llegadas_tarde, resumen.minutos_tarde), (1, 1))

    def test_los_segundos_no_cuentan(self):
        self._horario(2, time(8))
        self._marca(_momento(2, 8) + timedelta(seconds=59), Marca.Tipo.ENTRADA)
        self.assertEqual(self._dia(self._resumen(), 2).tarde, timedelta())

    def test_turno_cortado_compara_cada_entrada_con_la_suya(self):
        self._horario(2, time(8), time(17))
        self._marca(_momento(2, 8, 5), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 12), Marca.Tipo.SALIDA)
        self._marca(_momento(2, 17, 10), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 21), Marca.Tipo.SALIDA)
        dia = self._dia(self._resumen(), 2)
        self.assertEqual(dia.tarde, timedelta(minutes=15))
        self.assertFalse(dia.turno_sin_fichar)

    def test_vino_a_un_solo_turno_no_es_falta_y_se_senala(self):
        self._horario(2, time(8), time(17))
        self._turno(2, 8, 12)
        dia = self._dia(self._resumen(), 2)
        self.assertEqual(dia.tarde, timedelta())
        self.assertFalse(dia.falta)
        self.assertTrue(dia.turno_sin_fichar)
        self.assertEqual(observacion_del_dia(dia), "No fichó el 2.º turno")

    def test_franco_trabajado_no_es_tarde_ni_falta(self):
        self._horario(2, franco=True)
        self._turno(2, 10, 14)
        dia = self._dia(self._resumen(), 2)
        self.assertEqual((dia.tarde, dia.falta), (timedelta(), False))
        self.assertEqual(observacion_del_dia(dia), "Franco")

    def test_la_tolerancia_justa_no_pasa_y_un_minuto_mas_si(self):
        guardar_reglas(actor=self.admin, empresa=self.armadi, jornada_minutos=510, tolerancia_mensual_minutos=30)
        for dia in (2, 3):
            self._horario(dia, time(8))
            self._marca(_momento(dia, 8, 15), Marca.Tipo.ENTRADA)
        resumen = self._resumen()
        self.assertEqual(resumen.minutos_tarde, 30)
        self.assertFalse(resumen.pasa_la_tolerancia)
        self._horario(4, time(8))
        self._marca(_momento(4, 8, 1), Marca.Tipo.ENTRADA)
        resumen = self._resumen()
        self.assertEqual(resumen.minutos_tarde, 31)
        self.assertTrue(resumen.pasa_la_tolerancia)


class FaltasYSinHorarioTests(TardanzasYFaltasTestCase):
    def test_falta_solo_hasta_ayer(self):
        for dia in (8, 9, 10, 11):
            self._horario(dia, time(8))
        resumen = self._resumen(hoy=date(2026, 11, 10))
        self.assertEqual([self._dia(resumen, d).falta for d in (8, 9, 10, 11)], [True, True, False, False])
        self.assertEqual(resumen.faltas, 2)
        self.assertEqual(observacion_del_dia(self._dia(resumen, 9)), "Falta")

    def test_una_salida_sin_entrada_no_es_falta(self):
        self._horario(2, time(8))
        self._marca(_momento(2, 16), Marca.Tipo.SALIDA)
        self.assertFalse(self._dia(self._resumen(), 2).falta)

    def test_un_franco_sin_marcas_no_es_falta(self):
        self._horario(2, franco=True)
        dia = self._dia(self._resumen(), 2)
        self.assertFalse(dia.falta)
        self.assertEqual(observacion_del_dia(dia), "Franco")

    def test_dias_sin_horario_desde_el_alta_hasta_ayer(self):
        self._alta(self.empleado, date(2026, 11, 5))
        self._horario(6, time(8))
        self._turno(6, 8, 16)
        resumen = self._resumen(hoy=date(2026, 11, 10))
        # 5, 7, 8 y 9 sin horario; el 6 tiene; antes del 5 no estaba; el 10 es hoy.
        self.assertEqual(resumen.dias_sin_horario, 4)
        self.assertFalse(self._dia(resumen, 4).sin_horario)
        self.assertEqual(observacion_del_dia(self._dia(resumen, 4)), "Sin marcas")
        self.assertEqual(observacion_del_dia(self._dia(resumen, 7)), "Sin horario")
        # Un horario cargado antes del alta tampoco es falta.
        self._horario(3, time(8))
        self.assertFalse(self._dia(self._resumen(hoy=date(2026, 11, 10)), 3).falta)

    def test_despues_de_la_baja_no_cuenta(self):
        self._horario(9, time(8))
        dar_de_baja_empleado(actor=self.admin, empleado=self.empleado)
        Empleado.objects.filter(pk=self.empleado.pk).update(
            dado_de_baja_en=timezone.make_aware(datetime(2026, 11, 7, 12))
        )
        self.empleado.refresh_from_db()
        resumen = self._resumen(hoy=date(2026, 11, 20))
        self.assertEqual(resumen.dias_sin_horario, 7)
        self.assertFalse(self._dia(resumen, 9).falta)


class ExcelConTardanzasTests(TardanzasYFaltasTestCase):
    def test_la_hoja_y_el_resumen_muestran_horario_tarde_y_faltas(self):
        guardar_reglas(actor=self.admin, empresa=self.armadi, jornada_minutos=510, tolerancia_mensual_minutos=10)
        self._horario(2, time(8))
        self._marca(_momento(2, 8, 20), Marca.Tipo.ENTRADA)
        self._marca(_momento(2, 16), Marca.Tipo.SALIDA)
        self._horario(3, time(8), time(17))
        self._horario(4, franco=True)
        libro = load_workbook(BytesIO(generar_excel([(self.empleado, list(self.empleado.marcas.all()))], 2026, 11, hoy=date(2026, 11, 5))))
        hoja = libro["Juan Perez"]
        encabezados = [hoja.cell(row=5, column=c).value for c in range(1, 11)]
        self.assertEqual(
            encabezados,
            ["Día", "Horario", "Entrada 1", "Salida 1", "Entrada 2", "Salida 2", "Horas del día", "Horas extra", "Tarde (min)", "Observación"],
        )
        dia_2, dia_3, dia_4 = (hoja[f"{columna}{fila}"] for columna, fila in (("B", 7), ("B", 8), ("B", 9)))
        self.assertEqual((dia_2.value, dia_3.value, dia_4.value), ("8:00", "8:00 y 17:00", "Franco"))
        self.assertEqual(hoja["I7"].value, 20)
        self.assertEqual(hoja["J8"].value, "Falta")
        self.assertEqual(hoja["J9"].value, "Franco")
        self.assertEqual(hoja["J6"].value, "Sin horario")
        self.assertEqual(hoja["I36"].value, 20)

        resumen = libro["Resumen"]
        fila = [resumen.cell(row=6, column=c).value for c in range(7, 12)]
        # Llegadas tarde, minutos, pasa la tolerancia, faltas, dias sin horario (el 1).
        self.assertEqual(fila, [1, 20, "Sí", 1, 1])
        self.assertIn("ARMADI 10 min", resumen["A3"].value)
