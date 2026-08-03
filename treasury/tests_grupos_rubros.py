"""Agrupacion de rubros en la lectura economica (US-11.11).

El grupo es un nivel de LECTURA sobre el rubro: junta varias filas del listado
economico en una sola (por ejemplo MATERIA PRIMA con almacen y verdura adentro)
y se abre para ver el desglose. No mueve plata, no reimputa nada y no puede
recibir un gasto: por eso lo que se verifica aca es que los importes agrupados
sean la suma exacta de sus rubros y que la cabecera del dashboard no cambie por
agrupar.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa, GrupoRubro, MovimientoCaja, RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_expense
from treasury.models import ObjetivoRubroEconomico
from treasury.services import build_economic_period_snapshot
from users.models import Role

User = get_user_model()


class GrupoRubroFixtureMixin:
    """Escenario comun: un mes con gasto en tres rubros y un grupo sin asignar."""

    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-grupos", password="test", role=self.admin_role)
        self.operator = User.objects.create_user(username="cajero-grupos", password="test", role=self.operator_role)
        self.empresa = Empresa.objects.create(nombre="Empresa Grupos SA")
        self.admin.empresas_permitidas.set([self.empresa])
        self.operator.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            codigo="GRP",
            nombre="Sucursal Grupos",
            razon_social="Empresa Grupos SA",
            empresa=self.empresa,
        )
        self.turno = Turno.objects.create(
            empresa=self.empresa,
            tipo=Turno.Tipo.MANANA,
            creado_por=self.admin,
        )
        self.fecha = timezone.datetime(2026, 5, 12).date()
        self.desde = timezone.datetime(2026, 5, 1).date()
        self.hasta = timezone.datetime(2026, 5, 31).date()

        self.almacen = RubroOperativo.objects.create(nombre="Almacen")
        self.verdura = RubroOperativo.objects.create(nombre="Verdura")
        self.personal = RubroOperativo.objects.create(nombre="Personal")
        self.grupo = GrupoRubro.objects.create(nombre="MATERIA PRIMA")

        self.caja = open_box(
            user=self.operator,
            turno=self.turno,
            sucursal=self.sucursal,
            fecha_operativa=self.fecha,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )
        self._venta(Decimal("1000.00"))
        self._gasto(self.almacen, Decimal("80.00"))
        self._gasto(self.verdura, Decimal("20.00"))
        self._gasto(self.personal, Decimal("50.00"))

    def _gasto(self, rubro, monto):
        return register_expense(
            caja=self.caja,
            monto=monto,
            rubro_operativo=rubro,
            categoria=f"Gasto {rubro.nombre}",
            observacion="Gasto del periodo",
            actor=self.operator,
        )

    def _venta(self, monto, rubro=None):
        return MovimientoCaja.objects.create(
            caja=self.caja,
            tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=monto,
            impacta_saldo_caja=True,
            categoria="Venta mostrador",
            rubro_operativo=rubro,
            creado_por=self.operator,
        )

    def _snapshot(self):
        return build_economic_period_snapshot(
            date_from=self.desde,
            date_to=self.hasta,
            sucursal=self.sucursal,
        )

    def _fila(self, snapshot, nombre):
        return next(item for item in snapshot["items"] if item["rubro_nombre"] == nombre)

    def _agrupar_materia_prima(self):
        RubroOperativo.objects.filter(pk__in=[self.almacen.pk, self.verdura.pk]).update(grupo=self.grupo)


class GrupoRubroLecturaTests(GrupoRubroFixtureMixin, TestCase):
    def test_sin_grupos_asignados_el_listado_no_cambia(self):
        snapshot = self._snapshot()

        nombres = [item["rubro_nombre"] for item in snapshot["items"]]
        self.assertEqual(nombres, [item["rubro_nombre"] for item in snapshot["rubro_items"]])
        self.assertIn("Almacen", nombres)
        self.assertIn("Verdura", nombres)
        self.assertNotIn("MATERIA PRIMA", nombres)

    def test_los_rubros_del_grupo_colapsan_en_una_fila_con_la_suma_exacta(self):
        self._agrupar_materia_prima()

        snapshot = self._snapshot()

        nombres = [item["rubro_nombre"] for item in snapshot["items"]]
        self.assertIn("MATERIA PRIMA", nombres)
        self.assertNotIn("Almacen", nombres)
        self.assertNotIn("Verdura", nombres)
        # El rubro sin grupo sigue suelto, como pidio la administradora.
        self.assertIn("Personal", nombres)

        fila = self._fila(snapshot, "MATERIA PRIMA")
        self.assertEqual(fila["total_expense"], Decimal("100.00"))
        self.assertEqual(fila["cash_expense_total"], Decimal("100.00"))
        self.assertEqual(fila["children_count"], 2)
        self.assertIsNone(fila["rubro"])
        self.assertEqual(fila["grupo"].pk, self.grupo.pk)

    def test_agrupar_no_toca_ningun_total_de_la_cabecera(self):
        antes = self._snapshot()
        self._agrupar_materia_prima()
        despues = self._snapshot()

        for clave in (
            "sales_total",
            "cash_expense_total",
            "treasury_expense_total",
            "debt_period_total",
            "economic_result",
            "margin_pct",
            "objective_total",
            "deviation_total",
            "objective_items_count",
        ):
            self.assertEqual(antes[clave], despues[clave], f"cambio {clave} solo por agrupar")
        self.assertEqual(len(despues["rubro_items"]), len(antes["rubro_items"]))

    def test_grupo_desactivado_devuelve_los_rubros_sueltos_sin_perder_importes(self):
        self._agrupar_materia_prima()
        self.grupo.activo = False
        self.grupo.save(update_fields=["activo"])

        snapshot = self._snapshot()

        nombres = [item["rubro_nombre"] for item in snapshot["items"]]
        self.assertNotIn("MATERIA PRIMA", nombres)
        self.assertEqual(self._fila(snapshot, "Almacen")["total_expense"], Decimal("80.00"))
        self.assertEqual(self._fila(snapshot, "Verdura")["total_expense"], Decimal("20.00"))

    def test_el_objetivo_del_grupo_avisa_cuando_no_cubre_todos_sus_rubros(self):
        # Solo Almacen tiene ventas propias y objetivo cargado: el desvio del
        # grupo tiene que medir SOLO ese rubro, no los $100 completos.
        self._venta(Decimal("400.00"), rubro=self.almacen)
        ObjetivoRubroEconomico.objects.create(
            rubro_operativo=self.almacen,
            porcentaje_objetivo=Decimal("10.00"),
            vigencia_desde=self.desde,
            creado_por=self.admin,
        )
        self._agrupar_materia_prima()

        snapshot = self._snapshot()
        fila = self._fila(snapshot, "MATERIA PRIMA")

        self.assertTrue(fila["has_objective"])
        self.assertEqual(fila["objective_children_count"], 1)
        self.assertEqual(fila["children_count"], 2)
        self.assertFalse(fila["objective_covers_all_children"])
        # Objetivo: 10% de las ventas de Almacen ($400) = $40. Gasto de Almacen
        # $80. El desvio son $40, no $60 (que saldria de sumarle Verdura).
        self.assertEqual(fila["objective_amount"], Decimal("40.00"))
        self.assertEqual(fila["deviation_amount"], Decimal("40.00"))

    def test_objetivo_en_todos_los_rubros_del_grupo_marca_cobertura_completa(self):
        self._venta(Decimal("400.00"), rubro=self.almacen)
        self._venta(Decimal("100.00"), rubro=self.verdura)
        for rubro in (self.almacen, self.verdura):
            ObjetivoRubroEconomico.objects.create(
                rubro_operativo=rubro,
                porcentaje_objetivo=Decimal("10.00"),
                vigencia_desde=self.desde,
                creado_por=self.admin,
            )
        self._agrupar_materia_prima()

        fila = self._fila(self._snapshot(), "MATERIA PRIMA")

        self.assertTrue(fila["objective_covers_all_children"])
        self.assertEqual(fila["objective_children_count"], 2)
        # 10% de $400 + 10% de $100 = $50 de objetivo contra $100 de gasto.
        self.assertEqual(fila["objective_amount"], Decimal("50.00"))
        self.assertEqual(fila["deviation_amount"], Decimal("50.00"))

    def test_un_objetivo_sobre_un_rubro_sin_ventas_propias_no_compara_nada(self):
        """Limitacion heredada del calculo de objetivos, no de la agrupacion.

        El objetivo se mide contra las ventas imputadas AL MISMO RUBRO
        (`sales_by_rubro_month`), no contra las ventas del periodo. Un rubro de
        gasto puro como Personal no recibe ventas, asi que su porcentaje nunca
        llega a comparar y la fila queda "Sin objetivo" para siempre.

        Esto bloquea el objetivo por grupo: un 35% cargado sobre MATERIA PRIMA
        daria cero mientras la base siga siendo las ventas del propio rubro.
        """
        ObjetivoRubroEconomico.objects.create(
            rubro_operativo=self.personal,
            porcentaje_objetivo=Decimal("10.00"),
            vigencia_desde=self.desde,
            creado_por=self.admin,
        )

        fila = self._fila(self._snapshot(), "Personal")

        self.assertEqual(fila["total_expense"], Decimal("50.00"))
        self.assertFalse(fila["has_objective"])
        self.assertEqual(fila["objective_amount"], Decimal("0.00"))
        self.assertIsNone(fila["deviation_amount"])

    def test_la_fila_del_grupo_reconcilia_contra_el_desglose_de_sus_rubros(self):
        self._agrupar_materia_prima()
        snapshot = self._snapshot()

        fila = self._fila(snapshot, "MATERIA PRIMA")
        del_grupo = [
            item
            for item in snapshot["rubro_items"]
            if item["rubro"] is not None and item["rubro"].grupo_id == self.grupo.pk
        ]

        self.assertEqual(len(del_grupo), fila["children_count"])
        self.assertEqual(sum(item["total_expense"] for item in del_grupo), fila["total_expense"])
        self.assertEqual(sum(item["debt_total"] for item in del_grupo), fila["debt_total"])
        self.assertEqual(sum(item["sales_total"] for item in del_grupo), fila["sales_total"])


class GrupoRubroVistasTests(GrupoRubroFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self._agrupar_materia_prima()
        self.client.force_login(self.admin)
        self.periodo = {"fecha_desde": self.desde.isoformat(), "fecha_hasta": self.hasta.isoformat()}

    def test_el_dashboard_linkea_al_desglose_del_grupo_y_no_al_rubro_agrupado(self):
        response = self.client.get(reverse("treasury:dashboard"), self.periodo)

        self.assertEqual(response.status_code, 200)
        contenido = response.content.decode()
        self.assertIn("MATERIA PRIMA", contenido)
        self.assertIn(reverse("treasury:economic_grupo_detail", args=[self.grupo.pk]), contenido)
        self.assertNotIn(reverse("treasury:economic_rubro_detail", args=[self.almacen.pk]), contenido)

    def test_el_desglose_del_grupo_lista_sus_rubros_con_su_composicion(self):
        response = self.client.get(
            reverse("treasury:economic_grupo_detail", args=[self.grupo.pk]),
            self.periodo,
        )

        self.assertEqual(response.status_code, 200)
        contenido = response.content.decode()
        self.assertIn("Almacen", contenido)
        self.assertIn("Verdura", contenido)
        self.assertNotIn("Personal", contenido)
        self.assertIn(reverse("treasury:economic_rubro_detail", args=[self.almacen.pk]), contenido)
        self.assertEqual(response.context["grupo_row"]["total_expense"], Decimal("100.00"))
        self.assertEqual(len(response.context["items"]), 2)

    def test_la_composicion_de_un_rubro_agrupado_sigue_mostrando_su_cabecera(self):
        # Sin esto, el rubro agrupado ya no tiene fila propia en `items` y la
        # cabecera de su composicion quedaria vacia.
        response = self.client.get(
            reverse("treasury:economic_rubro_detail", args=[self.almacen.pk]),
            self.periodo,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["summary_item"])
        self.assertEqual(response.context["summary_item"]["total_expense"], Decimal("80.00"))
        # Vuelve al desglose del grupo, que es de donde se entro.
        self.assertEqual(
            response.context["back_url"].split("?")[0],
            reverse("treasury:economic_grupo_detail", args=[self.grupo.pk]),
        )

    def test_config_permite_crear_un_grupo_y_asignarle_un_rubro(self):
        response = self.client.post(
            reverse("cashops:rubro_group_create"),
            {"nombre": "IMPUESTOS", "activo": "on"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        nuevo = GrupoRubro.objects.get(nombre="IMPUESTOS")

        response = self.client.post(
            reverse("cashops:operational_category_update", args=[self.personal.pk]),
            {"nombre": "Personal", "grupo": str(nuevo.pk), "activo": "on"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.personal.refresh_from_db()
        self.assertEqual(self.personal.grupo_id, nuevo.pk)
