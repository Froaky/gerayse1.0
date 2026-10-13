"""Reporte 1 "Situacion economica" (slice S2).

El builder no calcula plata: cada KPI y cada celda tiene que ser exactamente la
clave del snapshot economico del mismo alcance. Por eso los tests comparan
Decimal contra `build_economic_period_snapshot`, en el general y en una
sucursal con datos distintos, y verifican que los datos de otra empresa
imputados a sus sucursales no cambien el documento.

Limite conocido del snapshot (no del builder): una deuda sin sucursal suma en el
general de cada empresa, porque la deuda no tiene empresa propia. Los egresos de
boveda sin imputar ya se cortan por la empresa de la boveda (ver el test de
regresion abajo).
"""

from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cashops.models import Empresa, GrupoRubro, MovimientoCaja, RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_card_sale, register_expense
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoCajaCentral,
    ObjetivoRubroEconomico,
)
from treasury.reportes.alcance import ReportScope
from treasury.reportes.documento import Pct
from treasury.reportes.economica import (
    SLUG,
    TITULO,
    build_situacion_economica,
    nombre_archivo,
)
from treasury.reportes.pdf import format_value, render_report_pdf
from treasury.services import (
    build_economic_period_snapshot,
    create_bank_account,
    create_payable_category,
    create_supplier,
    register_central_cash_movement,
    register_egreso_tesoreria,
    register_payable,
    register_transfer_payment,
)
from users.models import Role

User = get_user_model()

# Rotulo del KPI -> clave del snapshot.
KPIS_BASE = [
    ("Ventas", "sales_total"),
    ("Gasto de caja", "cash_expense_total"),
    ("Gasto de tesorería", "treasury_expense_total"),
    ("Deuda del período", "debt_period_total"),
    ("Resultado económico", "economic_result"),
]
KPIS_OBJETIVO = [
    ("Objetivo", "objective_total"),
    ("Real comparado", "objective_scope_real_total"),
    ("Desvío vs objetivo", "deviation_total"),
]
NOTA_PORCENTAJE = "El % de cada rubro es sobre el total de ventas del período."
NOTA_OBJETIVOS_GENERAL = (
    "En el reporte general solo se comparan objetivos globales. "
    "Los objetivos propios de cada sucursal se aplican en el reporte de esa sucursal."
)
NOTA_OBJETIVOS_SUCURSAL_UNICA = (
    "En este reporte solo se comparan objetivos globales; los objetivos propios de la sucursal no se aplican."
)
NOTA_SOLO_VENTAS = (
    "No se listan los rubros que en el período solo tuvieron ventas, sin gasto ni objetivo; "
    "esas ventas sí cuentan en el total de ventas."
)


def _con_gasto_u_objetivo(item):
    return bool(
        item["cash_expense_total"] or item["treasury_expense_total"] or item["debt_total"] or item["has_objective"]
    )


class SituacionEconomicaFixtureMixin:
    """Septiembre 2026 de una empresa con dos sucursales y datos distintos en cada una.

    Centro: ventas Almacen 1000 y Personal 500; gasto de caja Almacen 80, Verdura 20
    y Personal 50; egreso de tesoreria Personal 120; deuda Servicios 200 con 50 pagos.
    Norte: venta Almacen 300 y gasto de caja Limpieza 40.
    Almacen y Verdura forman el grupo MATERIA PRIMA.
    """

    def setUp(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-rep-eco", password="test", role=admin_role)
        self.operator = User.objects.create_user(username="cajero-rep-eco", password="test", role=operator_role)
        self.empresa = Empresa.objects.create(nombre="ARMADI Reportes SRL", identificador_fiscal="30-11111111-1")
        self.otra_empresa = Empresa.objects.create(nombre="MAPOGO Reportes SRL")
        for user in (self.admin, self.operator):
            user.empresas_permitidas.set([self.empresa, self.otra_empresa])
        self.centro = Sucursal.objects.create(
            codigo="CEN", nombre="Centro", razon_social="ARMADI Reportes SRL", empresa=self.empresa
        )
        self.norte = Sucursal.objects.create(
            codigo="NOR", nombre="Norte", razon_social="ARMADI Reportes SRL", empresa=self.empresa
        )
        self.vivre = Sucursal.objects.create(
            codigo="VIV", nombre="Vivre", razon_social="MAPOGO Reportes SRL", empresa=self.otra_empresa
        )
        self.desde = date(2026, 9, 1)
        self.hasta = date(2026, 9, 30)
        self.dia = date(2026, 9, 12)
        self.emitido = timezone.make_aware(datetime(2026, 10, 13, 11, 40))

        self.almacen = RubroOperativo.objects.create(nombre="Almacen")
        self.verdura = RubroOperativo.objects.create(nombre="Verdura")
        self.personal = RubroOperativo.objects.create(nombre="Personal")
        self.limpieza = RubroOperativo.objects.create(nombre="Limpieza")
        self.servicios = RubroOperativo.objects.create(nombre="Servicios")
        self.materia_prima = GrupoRubro.objects.create(nombre="MATERIA PRIMA")
        RubroOperativo.objects.filter(pk__in=[self.almacen.pk, self.verdura.pk]).update(grupo=self.materia_prima)

        self.categoria = create_payable_category(
            nombre="Servicios", rubro_operativo=self.servicios, actor=self.admin
        )
        self.proveedor = create_supplier(razon_social="Proveedor Luz SA", actor=self.admin)

        self.caja_centro = self._caja(self.centro)
        self._venta(self.caja_centro, Decimal("1000.00"), self.almacen)
        self._venta(self.caja_centro, Decimal("500.00"), self.personal)
        self._gasto(self.caja_centro, Decimal("80.00"), self.almacen)
        self._gasto(self.caja_centro, Decimal("20.00"), self.verdura)
        self._gasto(self.caja_centro, Decimal("50.00"), self.personal)

        caja_norte = self._caja(self.norte)
        self._venta(caja_norte, Decimal("300.00"), self.almacen)
        self._gasto(caja_norte, Decimal("40.00"), self.limpieza)

        register_egreso_tesoreria(
            empresa=self.empresa,
            fuente="CAJA_CENTRAL",
            fecha=self.dia,
            monto=Decimal("120.00"),
            concepto="Honorarios",
            rubro=self.personal,
            sucursal=self.centro,
            periodo=self.desde,
            actor=self.admin,
        )
        deuda = self._deuda(self.centro, Decimal("200.00"))
        cuenta = create_bank_account(
            nombre="Cuenta ARMADI",
            banco="Banco Uno",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="111-222",
            cbu="2850590940090418135201",
            empresa=self.empresa,
            actor=self.admin,
        )
        register_transfer_payment(
            payable=deuda,
            bank_account=cuenta,
            fecha_pago=date(2026, 9, 20),
            monto=Decimal("50.00"),
            actor=self.admin,
        )

    # --- armado de datos ---

    def _caja(self, sucursal):
        turno, _ = Turno.objects.get_or_create(
            empresa=sucursal.empresa, tipo=Turno.Tipo.MANANA, defaults={"creado_por": self.admin}
        )
        return open_box(
            user=self.operator,
            turno=turno,
            sucursal=sucursal,
            fecha_operativa=self.dia,
            monto_inicial=Decimal("0.00"),
            actor=self.admin,
        )

    def _venta(self, caja, monto, rubro):
        return MovimientoCaja.objects.create(
            caja=caja,
            tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=monto,
            impacta_saldo_caja=True,
            categoria="Venta mostrador",
            rubro_operativo=rubro,
            creado_por=self.operator,
        )

    def _gasto(self, caja, monto, rubro):
        return register_expense(
            caja=caja,
            monto=monto,
            rubro_operativo=rubro,
            categoria=f"Gasto {rubro.nombre}",
            observacion="Gasto del periodo",
            actor=self.operator,
        )

    def _deuda(self, sucursal, importe):
        return register_payable(
            sucursal=sucursal,
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura de luz",
            fecha_emision=self.dia,
            fecha_vencimiento=date(2026, 9, 25),
            periodo_referencia=self.desde,
            importe_total=importe,
            actor=self.admin,
        )

    def _deuda_legacy(self, importe):
        # Categoria sin rubro, cargada antes de que fuera obligatorio.
        legacy, _ = CategoriaCuentaPagar.objects.get_or_create(
            nombre="Legacy sin rubro", defaults={"activo": True, "creado_por": self.admin}
        )
        return CuentaPorPagar.objects.create(
            sucursal=self.centro,
            proveedor=self.proveedor,
            categoria=legacy,
            concepto="Deuda legacy",
            fecha_emision=self.dia,
            fecha_vencimiento=date(2026, 9, 25),
            periodo_referencia=self.desde,
            importe_total=importe,
            saldo_pendiente=importe,
            creado_por=self.admin,
        )

    def _egreso_sin_imputar(self, monto, empresa=None):
        return register_central_cash_movement(
            empresa=empresa or self.empresa,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=monto,
            concepto="Gasto sin imputar",
            fecha=self.dia,
            actor=self.admin,
        )

    def _objetivo(self, rubro):
        # Objetivo global del 10 %: aplica al general y a cada sucursal.
        return ObjetivoRubroEconomico.objects.create(
            rubro_operativo=rubro,
            porcentaje_objetivo=Decimal("10.00"),
            vigencia_desde=date(2026, 1, 1),
            creado_por=self.admin,
        )

    def _objetivo_almacen(self):
        return self._objetivo(self.almacen)

    # --- lectura ---

    def _scope(self, sucursal=None, *, empresa=None, desde=None, hasta=None, sucursal_unica=None):
        return ReportScope(
            empresa=empresa or self.empresa,
            sucursal=sucursal,
            date_from=desde or self.desde,
            date_to=hasta or self.hasta,
            sucursal_unica=sucursal_unica,
            emitido=self.emitido,
        )

    def _snapshot(self, scope):
        return build_economic_period_snapshot(
            date_from=scope.date_from,
            date_to=scope.date_to,
            sucursal=scope.sucursal,
            empresa_ids=scope.empresa_ids,
        )

    def _leer(self, sucursal=None, **kwargs):
        scope = self._scope(sucursal, **kwargs)
        return build_situacion_economica(scope), self._snapshot(scope)


class SituacionEconomicaKpiTests(SituacionEconomicaFixtureMixin, TestCase):
    def _assert_kpis_del_snapshot(self, doc, snapshot):
        for label, clave in KPIS_BASE:
            self.assertEqual(doc.kpi(label).value, snapshot[clave], label)
        self.assertEqual(doc.kpi("Margen").value, Pct(snapshot["margin_pct"]))
        self.assertEqual(
            doc.kpi("Deuda del período").hint,
            "De eso falta pagar " + format_value(snapshot["debt_pending_total"]),
        )

    def test_kpis_del_general_son_las_claves_del_snapshot(self):
        doc, snapshot = self._leer()

        self._assert_kpis_del_snapshot(doc, snapshot)
        self.assertEqual(
            [kpi.label for kpi in doc.kpis],
            ["Ventas", "Gasto de caja", "Gasto de tesorería", "Deuda del período", "Resultado económico", "Margen"],
        )
        # El escenario suma las dos sucursales: si no, el test no prueba nada.
        self.assertEqual(doc.kpi("Ventas").value, Decimal("1800.00"))
        self.assertEqual(doc.kpi("Gasto de caja").value, Decimal("190.00"))
        self.assertEqual(doc.kpi("Gasto de tesorería").value, Decimal("120.00"))
        self.assertEqual(doc.kpi("Deuda del período").value, Decimal("200.00"))
        self.assertEqual(doc.kpi("Deuda del período").hint, "De eso falta pagar $ 150,00")
        self.assertEqual(doc.kpi("Resultado económico").value, Decimal("1290.00"))

    def test_kpis_de_una_sucursal_son_las_claves_de_su_snapshot(self):
        general, _ = self._leer()
        doc, snapshot = self._leer(self.centro)

        self._assert_kpis_del_snapshot(doc, snapshot)
        self.assertEqual(doc.kpi("Ventas").value, Decimal("1500.00"))
        self.assertEqual(doc.kpi("Gasto de caja").value, Decimal("150.00"))
        self.assertNotEqual(doc.kpi("Ventas").value, general.kpi("Ventas").value)
        self.assertNotEqual(doc.kpi("Resultado económico").value, general.kpi("Resultado económico").value)

        norte, snapshot_norte = self._leer(self.norte)
        self._assert_kpis_del_snapshot(norte, snapshot_norte)
        self.assertEqual(norte.kpi("Ventas").value, Decimal("300.00"))
        self.assertEqual(norte.kpi("Deuda del período").value, Decimal("0.00"))

    def test_encabezado_sale_del_alcance(self):
        doc, _ = self._leer(self.centro)

        self.assertEqual(doc.slug, SLUG)
        self.assertEqual(doc.titulo, TITULO)
        self.assertEqual(doc.razon_social, "ARMADI Reportes SRL")
        self.assertEqual(doc.cuit, "30-11111111-1")
        self.assertEqual(doc.alcance_label, "Sucursal Centro")
        self.assertEqual(doc.periodo_label, "Período 01/09/2026 al 30/09/2026")
        self.assertEqual(doc.emitido_label, "13/10/2026 11:40")

    def test_sin_objetivo_no_aparecen_los_kpis_de_objetivo(self):
        doc, snapshot = self._leer()

        self.assertEqual(snapshot["objective_items_count"], 0)
        for label, _ in KPIS_OBJETIVO:
            with self.assertRaises(KeyError):
                doc.kpi(label)

    def test_con_objetivo_aparecen_los_kpis_de_objetivo(self):
        self._objetivo_almacen()

        for sucursal in (None, self.centro):
            doc, snapshot = self._leer(sucursal)
            self.assertEqual(snapshot["objective_items_count"], 1)
            self._assert_kpis_del_snapshot(doc, snapshot)
            for label, clave in KPIS_OBJETIVO:
                self.assertEqual(doc.kpi(label).value, snapshot[clave], label)
            self.assertEqual(doc.kpi("Objetivo").hint, "1 rubro con objetivo")
            self.assertEqual([kpi.label for kpi in doc.kpis][6:], [label for label, _ in KPIS_OBJETIVO])

        # General: 10 % de las ventas de Almacen de las dos sucursales (1300).
        general, _ = self._leer()
        self.assertEqual(general.kpi("Objetivo").value, Decimal("130.00"))
        self.assertEqual(general.kpi("Real comparado").value, Decimal("80.00"))
        self.assertEqual(general.kpi("Desvío vs objetivo").value, Decimal("-50.00"))

    def test_con_varios_objetivos_el_hint_va_en_plural(self):
        self._objetivo_almacen()
        self._objetivo(self.personal)

        doc, snapshot = self._leer()

        self.assertEqual(snapshot["objective_items_count"], 2)
        self.assertEqual(doc.kpi("Objetivo").hint, "2 rubros con objetivo")
        for label, clave in KPIS_OBJETIVO:
            self.assertEqual(doc.kpi(label).value, snapshot[clave], label)

    def test_sin_ventas_el_margen_y_los_porcentajes_no_aplican(self):
        sur = Sucursal.objects.create(
            codigo="SUR", nombre="Sur", razon_social="ARMADI Reportes SRL", empresa=self.empresa
        )
        self._gasto(self._caja(sur), Decimal("40.00"), self.limpieza)

        doc, snapshot = self._leer(sur)

        # El snapshot devuelve 0,00 % sin ventas; el reporte muestra que no aplica.
        self.assertEqual(snapshot["sales_total"], Decimal("0.00"))
        self.assertEqual(snapshot["margin_pct"], Decimal("0.00"))
        self.assertEqual(doc.kpi("Margen").value, Pct(None))
        for label, clave in KPIS_BASE:
            self.assertEqual(doc.kpi(label).value, snapshot[clave], label)
        self.assertEqual(doc.kpi("Resultado económico").value, Decimal("-40.00"))
        self.assertEqual(
            doc.table("Gasto por rubro").rows,
            [
                [
                    "Limpieza",
                    Decimal("40.00"),
                    Decimal("0.00"),
                    Decimal("0.00"),
                    Decimal("40.00"),
                    Pct(None),
                    "Sin objetivo",
                ]
            ],
        )


class SituacionEconomicaTablaTests(SituacionEconomicaFixtureMixin, TestCase):
    def _fila(self, doc, nombre):
        return next(row for row in doc.table("Gasto por rubro").rows if row[0] == nombre)

    def test_filas_siguen_los_items_del_snapshot(self):
        for sucursal in (None, self.centro, self.norte):
            doc, snapshot = self._leer(sucursal)
            tabla = doc.table("Gasto por rubro")
            items = [item for item in snapshot["items"] if _con_gasto_u_objetivo(item)]
            self.assertEqual(tabla.columns, ["Rubro", "Caja", "Tesorería", "Deuda", "Total", "% s/ventas", "Desvío"])
            self.assertEqual(tabla.numeric_cols, frozenset({1, 2, 3, 4, 5, 6}))
            self.assertEqual(len(tabla.rows), len(items))
            for row, item in zip(tabla.rows, items):
                self.assertTrue(row[0].startswith(item["rubro_nombre"]))
                self.assertEqual(row[1], item["cash_expense_total"])
                self.assertEqual(row[2], item["treasury_expense_total"])
                self.assertEqual(row[3], item["debt_total"])
                self.assertEqual(row[4], item["total_expense"])
                self.assertEqual(row[5], Pct(item["expense_ratio_over_sales"]))

        # En el general todos los items tienen gasto: no se omite ninguno.
        general, snapshot = self._leer()
        self.assertEqual(len(general.table("Gasto por rubro").rows), len(snapshot["items"]))
        self.assertEqual(
            [row[0] for row in general.table("Gasto por rubro").rows],
            ["Servicios", "Personal", "MATERIA PRIMA · 2 rubros", "Limpieza"],
        )
        self.assertEqual(
            self._fila(general, "Personal")[1:5],
            [Decimal("50.00"), Decimal("120.00"), Decimal("0.00"), Decimal("170.00")],
        )

    def test_fila_de_grupo_muestra_la_cantidad_de_rubros(self):
        doc, snapshot = self._leer()

        grupo = next(item for item in snapshot["items"] if item.get("grupo"))
        fila = self._fila(doc, "MATERIA PRIMA · 2 rubros")
        self.assertEqual(grupo["children_count"], 2)
        self.assertEqual(fila[4], grupo["total_expense"])
        self.assertEqual(fila[4], Decimal("100.00"))

    def test_sin_objetivo_cada_fila_lo_dice(self):
        doc, _ = self._leer()

        self.assertEqual({row[6] for row in doc.table("Gasto por rubro").rows}, {"Sin objetivo"})

    def test_con_objetivo_la_fila_muestra_el_desvio_del_snapshot(self):
        self._objetivo_almacen()

        doc, snapshot = self._leer()
        tabla = doc.table("Gasto por rubro")
        for row, item in zip(tabla.rows, snapshot["items"]):
            esperado = item["deviation_amount"] if item["has_objective"] else "Sin objetivo"
            self.assertEqual(row[6], esperado)
        # El grupo compara solo Almacen: 80 de gasto contra 130 de objetivo.
        self.assertEqual(self._fila(doc, "MATERIA PRIMA · 2 rubros")[6], Decimal("-50.00"))
        self.assertEqual(self._fila(doc, "Personal")[6], "Sin objetivo")

    def test_rubro_suelto_con_objetivo_muestra_su_propio_desvio(self):
        self._objetivo_almacen()
        self._objetivo(self.personal)

        doc, snapshot = self._leer()

        personal = next(item for item in snapshot["items"] if item["rubro_nombre"] == "Personal")
        self.assertIsNone(personal.get("grupo"))
        self.assertEqual(self._fila(doc, "Personal")[6], personal["deviation_amount"])
        # 170 de gasto contra el 10 % de 500 de ventas.
        self.assertEqual(self._fila(doc, "Personal")[6], Decimal("120.00"))
        # La cabecera suma Almacen (-50) y Personal (120): la celda no es el total.
        self.assertEqual(doc.kpi("Desvío vs objetivo").value, Decimal("70.00"))

    def test_filas_solo_de_ventas_no_se_listan(self):
        # Las ventas con tarjeta no tienen rubro: el snapshot arma "Sin rubro" sin gasto.
        register_card_sale(caja=self.caja_centro, monto=Decimal("900.00"), actor=self.operator)

        doc, snapshot = self._leer()

        sin_rubro = next(item for item in snapshot["items"] if item["rubro_nombre"] == "Sin rubro")
        self.assertEqual(sin_rubro["sales_total"], Decimal("900.00"))
        self.assertEqual(sin_rubro["total_expense"], Decimal("0.00"))
        tabla = doc.table("Gasto por rubro")
        self.assertEqual(len(tabla.rows), len(snapshot["items"]) - 1)
        self.assertEqual(
            [row[0] for row in tabla.rows],
            ["Servicios", "Personal", "MATERIA PRIMA · 2 rubros", "Limpieza"],
        )
        self.assertIn(NOTA_SOLO_VENTAS, doc.notes)
        # La venta igual suma en el KPI.
        self.assertEqual(doc.kpi("Ventas").value, snapshot["sales_total"])
        self.assertEqual(doc.kpi("Ventas").value, Decimal("2700.00"))

    def test_rubro_solo_de_ventas_se_lista_si_tiene_objetivo(self):
        # Norte: Almacen vende 300 y no tiene gasto.
        sin_objetivo, _ = self._leer(self.norte)
        self.assertEqual([row[0] for row in sin_objetivo.table("Gasto por rubro").rows], ["Limpieza"])
        self.assertIn(NOTA_SOLO_VENTAS, sin_objetivo.notes)

        self._objetivo_almacen()
        doc, snapshot = self._leer(self.norte)

        grupo = next(item for item in snapshot["items"] if item.get("grupo"))
        fila = self._fila(doc, "MATERIA PRIMA · 1 rubro")
        self.assertEqual(fila[4], Decimal("0.00"))
        self.assertEqual(fila[6], grupo["deviation_amount"])
        self.assertEqual(fila[6], Decimal("-30.00"))
        self.assertEqual(len(doc.table("Gasto por rubro").rows), len(snapshot["items"]))
        self.assertNotIn(NOTA_SOLO_VENTAS, doc.notes)

    def test_periodo_sin_datos_deja_la_tabla_vacia(self):
        doc, snapshot = self._leer(desde=date(2026, 8, 1), hasta=date(2026, 8, 31))

        tabla = doc.table("Gasto por rubro")
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(tabla.rows, [])
        self.assertEqual(tabla.empty_text, "No hay gastos imputados a rubros en el período.")
        self.assertEqual(doc.kpi("Ventas").value, Decimal("0.00"))


class SituacionEconomicaNotasTests(SituacionEconomicaFixtureMixin, TestCase):
    def test_notas_base_del_general_y_de_la_sucursal(self):
        general, _ = self._leer()
        sucursal, _ = self._leer(self.centro)

        self.assertEqual(general.notes, [NOTA_PORCENTAJE, NOTA_OBJETIVOS_GENERAL])
        # En una sucursal se aplican sus objetivos: la aclaracion no corresponde.
        self.assertEqual(sucursal.notes, [NOTA_PORCENTAJE])

    def test_egresos_y_deudas_sin_imputar_se_avisan(self):
        self._egreso_sin_imputar(Decimal("70.00"))
        self._deuda_legacy(Decimal("250.00"))

        doc, snapshot = self._leer()

        self.assertEqual(snapshot["treasury_unmapped_expenses_count"], 1)
        self.assertEqual(snapshot["unmapped_payables_count"], 1)
        self.assertIn(
            "1 egreso de tesorería sin imputar (sucursal, rubro o período) por $ 70,00 "
            "no suma en los rubros ni en el resultado económico.",
            doc.notes,
        )
        self.assertIn(
            "1 deuda del período sin rubro operativo por $ 250,00 no suma en los rubros ni en el resultado económico.",
            doc.notes,
        )
        # Lo sin imputar no entra en los KPIs: siguen siendo las claves del snapshot.
        self.assertEqual(doc.kpi("Gasto de tesorería").value, snapshot["treasury_expense_total"])
        self.assertEqual(doc.kpi("Deuda del período").value, Decimal("200.00"))

    def test_grupo_con_objetivo_parcial_lo_aclara(self):
        self._objetivo_almacen()

        doc, _ = self._leer()

        self.assertIn(
            "MATERIA PRIMA: el desvío compara 1 de 2 rubros del grupo; los demás no tienen objetivo cargado.",
            doc.notes,
        )

    def test_grupo_con_objetivo_en_todos_sus_rubros_no_lleva_aclaracion(self):
        # Verdura necesita ventas para que su objetivo aplique.
        self._venta(self.caja_centro, Decimal("200.00"), self.verdura)
        self._objetivo_almacen()
        self._objetivo(self.verdura)

        doc, snapshot = self._leer()

        grupo = next(item for item in snapshot["items"] if item.get("grupo"))
        fila = next(row for row in doc.table("Gasto por rubro").rows if row[0] == "MATERIA PRIMA · 2 rubros")
        self.assertTrue(grupo["objective_covers_all_children"])
        self.assertEqual(fila[6], grupo["deviation_amount"])
        self.assertEqual([nota for nota in doc.notes if nota.startswith("MATERIA PRIMA:")], [])

    def test_egresos_y_deudas_sin_imputar_en_plural(self):
        self._egreso_sin_imputar(Decimal("70.00"))
        self._egreso_sin_imputar(Decimal("30.00"))
        self._deuda_legacy(Decimal("250.00"))
        self._deuda_legacy(Decimal("150.00"))

        doc, snapshot = self._leer()

        self.assertEqual(snapshot["treasury_unmapped_expenses_count"], 2)
        self.assertEqual(snapshot["unmapped_payables_count"], 2)
        self.assertIn(
            "2 egresos de tesorería sin imputar (sucursal, rubro o período) por $ 100,00 "
            "no suman en los rubros ni en el resultado económico.",
            doc.notes,
        )
        self.assertIn(
            "2 deudas del período sin rubro operativo por $ 400,00 "
            "no suman en los rubros ni en el resultado económico.",
            doc.notes,
        )

    def test_empresa_de_una_sola_sucursal_no_remite_a_otro_reporte(self):
        # Con una sola sucursal activa el form solo ofrece el general.
        self._venta(self._caja(self.vivre), Decimal("400.00"), self.almacen)

        doc, _ = self._leer(empresa=self.otra_empresa, sucursal_unica=self.vivre)

        self.assertEqual(doc.alcance_label, "Sucursal Vivre (única de la empresa)")
        self.assertIn(NOTA_OBJETIVOS_SUCURSAL_UNICA, doc.notes)
        self.assertNotIn(NOTA_OBJETIVOS_GENERAL, doc.notes)


class SituacionEconomicaAlcanceTests(SituacionEconomicaFixtureMixin, TestCase):
    def _datos_de_la_otra_empresa(self):
        caja = self._caja(self.vivre)
        self._venta(caja, Decimal("5000.00"), self.almacen)
        self._gasto(caja, Decimal("999.00"), self.almacen)
        self._deuda(self.vivre, Decimal("777.00"))
        register_egreso_tesoreria(
            empresa=self.otra_empresa,
            fuente="CAJA_CENTRAL",
            fecha=self.dia,
            monto=Decimal("333.00"),
            concepto="Alquiler",
            rubro=self.personal,
            sucursal=self.vivre,
            periodo=self.desde,
            actor=self.admin,
        )

    def test_la_otra_empresa_no_cambia_el_general(self):
        # Datos de la otra empresa imputados a su sucursal. Una deuda sin sucursal
        # si sumaria en las dos (limite conocido, ver docstring del modulo).
        self._objetivo_almacen()
        antes, _ = self._leer()

        self._datos_de_la_otra_empresa()
        despues, _ = self._leer()
        otra, snapshot_otra = self._leer(empresa=self.otra_empresa)

        self.assertEqual(despues.kpis, antes.kpis)
        self.assertEqual(despues.blocks, antes.blocks)
        # Los datos existen: en el general de la otra empresa si suman.
        self.assertEqual(otra.kpi("Ventas").value, Decimal("5000.00"))
        self.assertEqual(otra.kpi("Ventas").value, snapshot_otra["sales_total"])
        self.assertEqual(otra.razon_social, "MAPOGO Reportes SRL")
        self.assertIsNone(otra.cuit)

    def test_egreso_de_boveda_sin_imputar_queda_en_su_empresa(self):
        # Regresion del snapshot: los egresos de boveda pendientes de imputar se
        # cortan por la empresa de la boveda, igual que los del banco por la de
        # la cuenta. Antes el de otra empresa aparecia en la nota de esta.
        antes_general, snapshot_general = self._leer()
        antes_centro, snapshot_centro = self._leer(self.centro)

        self._egreso_sin_imputar(Decimal("70.00"), empresa=self.otra_empresa)
        despues_general, _ = self._leer()
        despues_centro, _ = self._leer(self.centro)
        self.assertEqual(despues_general.notes, antes_general.notes)
        self.assertEqual(despues_centro.notes, antes_centro.notes)

        # El de la propia boveda sigue apareciendo, en el general y en la sucursal.
        self._egreso_sin_imputar(Decimal("30.00"))
        _, propio_general = self._leer()
        _, propio_centro = self._leer(self.centro)
        self.assertEqual(
            propio_general["treasury_unmapped_expenses_total"],
            snapshot_general["treasury_unmapped_expenses_total"] + Decimal("30.00"),
        )
        self.assertEqual(
            propio_centro["treasury_unmapped_expenses_total"],
            snapshot_centro["treasury_unmapped_expenses_total"] + Decimal("30.00"),
        )

    def test_renderiza_a_pdf(self):
        self._objetivo_almacen()
        general, _ = self._leer()
        centro, _ = self._leer(self.centro)
        vacio, _ = self._leer(desde=date(2026, 8, 1), hasta=date(2026, 8, 31))

        pdf = render_report_pdf([general, centro, vacio])

        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_nombre_de_archivo(self):
        self.assertEqual(
            nombre_archivo(self._scope()),
            "situacion-economica_armadi-reportes-srl_general_2026-09_2026-09.pdf",
        )
        self.assertEqual(
            nombre_archivo(self._scope(self.centro)),
            "situacion-economica_armadi-reportes-srl_cen_2026-09_2026-09.pdf",
        )
