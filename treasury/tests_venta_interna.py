"""Venta interna: las facturas del proveedor interno son ventas de su local propio.

Pedido de tesoreria del 16/11/2026. La panaderia propia (Proveedor.es_interno)
les factura a las cafeterias; esas facturas ya contaban como gasto del local que
compra y se daban por pagadas sin mover plata (US-3.20), pero la panaderia, que
tambien es un local, no las veia como venta. Con `Proveedor.sucursal_vendedora`:

- cada factura a un local de la misma empresa es venta del local propio en la
  situacion economica, sin efectivo y sin movimiento de caja;
- entra con las reglas con que cuenta como gasto (no anulada, con rubro, importe
  total, periodo de referencia), asi que en el total de la empresa se compensa;
- no cruza empresas, no activa objetivos y se informa aparte
  (`internal_sales_total`), en pantalla y en el PDF.

Escenario base (septiembre 2026): ver `SituacionEconomicaFixtureMixin`. General:
ventas 1800, egresos 510, resultado 1290. Centro vende 1500 y Norte 300.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cashops.models import RubroOperativo, Sucursal
from treasury.models import CategoriaCuentaPagar, CuentaPorPagar, ObjetivoRubroEconomico
from treasury.reportes.economica import NOTA_VENTAS_INTERNAS
from treasury.reportes.pdf import format_value
from treasury.services import (
    annul_payable,
    build_economic_period_snapshot,
    create_payable_category,
    create_supplier,
    register_payable,
    set_supplier_internal,
    set_supplier_selling_branch,
)
from treasury.tests_reportes_economica import SituacionEconomicaFixtureMixin

OCTUBRE = date(2026, 10, 1)


class VentaInternaBase(SituacionEconomicaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.local_panaderia = Sucursal.objects.create(
            codigo="PAN", nombre="Panaderia propia", razon_social="ARMADI Reportes SRL", empresa=self.empresa
        )
        self.rubro_pan = RubroOperativo.objects.create(nombre="Pan")
        self.categoria_pan = create_payable_category(nombre="Pan", rubro_operativo=self.rubro_pan, actor=self.admin)
        self.panaderia = create_supplier(razon_social="OVEJA NEGRA ARMADI SRL", actor=self.admin)
        self.panaderia = set_supplier_internal(supplier=self.panaderia, es_interno=True, actor=self.admin)

    def _con_local_propio(self, sucursal=None):
        self.panaderia = set_supplier_selling_branch(
            supplier=self.panaderia, sucursal=sucursal or self.local_panaderia, actor=self.admin
        )

    def _factura_interna(self, sucursal, importe, *, periodo=None):
        periodo = periodo or self.desde
        return register_payable(
            sucursal=sucursal,
            proveedor=self.panaderia,
            categoria=self.categoria_pan,
            concepto="Pan de la semana",
            fecha_emision=periodo.replace(day=12),
            fecha_vencimiento=periodo.replace(day=25),
            periodo_referencia=periodo,
            importe_total=Decimal(importe),
            actor=self.admin,
        )

    def _leer_snapshot(self, sucursal=None, *, empresa_ids=None, desde=None, hasta=None):
        if sucursal is None and empresa_ids is None:
            empresa_ids = [self.empresa.pk]
        return build_economic_period_snapshot(
            date_from=desde or self.desde,
            date_to=hasta or self.hasta,
            sucursal=sucursal,
            empresa_ids=empresa_ids,
        )


class VentaInternaSnapshotTests(VentaInternaBase):
    def test_sin_local_propio_la_factura_interna_es_solo_gasto(self):
        self._factura_interna(self.centro, "100.00")

        general = self._leer_snapshot()

        self.assertEqual(general["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(general["sales_total"], Decimal("1800.00"))
        self.assertEqual(general["expense_total"], Decimal("610.00"))
        self.assertEqual(self._leer_snapshot(self.local_panaderia)["sales_total"], Decimal("0.00"))

    def test_la_factura_interna_es_venta_del_local_propio(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")
        self._factura_interna(self.norte, "60.00")

        panaderia = self._leer_snapshot(self.local_panaderia)

        self.assertEqual(panaderia["internal_sales_total"], Decimal("160.00"))
        self.assertEqual(panaderia["sales_total"], Decimal("160.00"))
        self.assertEqual(panaderia["expense_total"], Decimal("0.00"))
        self.assertEqual(panaderia["economic_result"], Decimal("160.00"))

    def test_al_local_que_compra_le_sigue_contando_solo_como_gasto(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        centro = self._leer_snapshot(self.centro)

        self.assertEqual(centro["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(centro["sales_total"], Decimal("1500.00"))
        self.assertEqual(centro["debt_period_total"], Decimal("300.00"))

    def test_en_el_total_de_la_empresa_la_venta_y_el_gasto_se_compensan(self):
        antes = self._leer_snapshot()
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")
        self._factura_interna(self.norte, "60.00")

        general = self._leer_snapshot()

        self.assertEqual(general["internal_sales_total"], Decimal("160.00"))
        self.assertEqual(general["sales_total"], antes["sales_total"] + Decimal("160.00"))
        self.assertEqual(general["expense_total"], antes["expense_total"] + Decimal("160.00"))
        self.assertEqual(general["economic_result"], antes["economic_result"])
        # El general es la suma de sus locales, tambien en ventas.
        por_local = sum(
            self._leer_snapshot(local)["sales_total"] for local in (self.centro, self.norte, self.local_panaderia)
        )
        self.assertEqual(general["sales_total"], por_local)

    def test_la_factura_anulada_deja_de_ser_venta(self):
        self._con_local_propio()
        factura = self._factura_interna(self.centro, "100.00")
        self._factura_interna(self.norte, "60.00")

        annul_payable(payable=factura, motivo="Cargada dos veces", actor=self.admin)

        self.assertEqual(self._leer_snapshot(self.local_panaderia)["internal_sales_total"], Decimal("60.00"))

    def test_va_al_periodo_de_la_factura(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00", periodo=OCTUBRE)

        septiembre = self._leer_snapshot(self.local_panaderia)
        octubre = self._leer_snapshot(self.local_panaderia, desde=OCTUBRE, hasta=date(2026, 10, 31))
        los_dos = self._leer_snapshot(self.local_panaderia, hasta=date(2026, 10, 31))

        self.assertEqual(septiembre["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(octubre["internal_sales_total"], Decimal("100.00"))
        self.assertEqual(los_dos["internal_sales_total"], Decimal("100.00"))

    def test_una_factura_sin_rubro_no_es_gasto_ni_venta(self):
        self._con_local_propio()
        legacy = CategoriaCuentaPagar.objects.create(nombre="Legacy sin rubro", activo=True, creado_por=self.admin)
        factura = self._factura_interna(self.centro, "100.00")
        CuentaPorPagar.objects.filter(pk=factura.pk).update(categoria=legacy)

        general = self._leer_snapshot()

        self.assertEqual(general["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(general["expense_total"], Decimal("510.00"))
        self.assertEqual(general["unmapped_payables_total"], Decimal("100.00"))

    def test_una_factura_sin_sucursal_no_es_venta_interna(self):
        self._con_local_propio()
        self._factura_interna(None, "100.00")

        self.assertEqual(self._leer_snapshot(self.local_panaderia)["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(self._leer_snapshot()["internal_sales_total"], Decimal("0.00"))

    def test_quitar_el_local_propio_la_saca_de_las_ventas(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        self.panaderia = set_supplier_selling_branch(supplier=self.panaderia, sucursal=None, actor=self.admin)

        self.assertEqual(self._leer_snapshot()["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(self._leer_snapshot()["sales_total"], Decimal("1800.00"))

    def test_la_venta_interna_va_sin_rubro_y_no_activa_objetivos(self):
        self._con_local_propio()
        ObjetivoRubroEconomico.objects.create(
            rubro_operativo=self.rubro_pan,
            porcentaje_objetivo=Decimal("10.00"),
            vigencia_desde=date(2026, 1, 1),
            creado_por=self.admin,
        )
        self._factura_interna(self.centro, "100.00")

        general = self._leer_snapshot()
        por_rubro = {item["rubro"]: item for item in general["rubro_items"]}

        self.assertEqual(por_rubro[None]["sales_total"], Decimal("100.00"))
        self.assertEqual(por_rubro[self.rubro_pan]["sales_total"], Decimal("0.00"))
        self.assertFalse(por_rubro[self.rubro_pan]["has_objective"])
        self.assertEqual(general["objective_total"], Decimal("0.00"))


class VentaInternaEmpresasTests(VentaInternaBase):
    """La venta interna es de la empresa del local propio y no cruza empresas."""

    def test_la_factura_a_un_local_de_otra_empresa_no_es_venta_interna(self):
        self._con_local_propio()
        self._factura_interna(self.vivre, "100.00")

        self.assertEqual(self._leer_snapshot(self.local_panaderia)["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(self._leer_snapshot()["internal_sales_total"], Decimal("0.00"))
        otra = self._leer_snapshot(empresa_ids=[self.otra_empresa.pk])
        self.assertEqual(otra["internal_sales_total"], Decimal("0.00"))
        self.assertEqual(otra["debt_period_total"], Decimal("100.00"))

    def test_la_otra_empresa_no_ve_la_venta_interna(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        self.assertEqual(
            self._leer_snapshot(empresa_ids=[self.otra_empresa.pk])["internal_sales_total"], Decimal("0.00")
        )
        self.assertEqual(self._leer_snapshot(self.vivre)["sales_total"], Decimal("0.00"))
        self.assertEqual(self._leer_snapshot(empresa_ids=[])["internal_sales_total"], Decimal("0.00"))
        todas = self._leer_snapshot(empresa_ids=[self.empresa.pk, self.otra_empresa.pk])
        self.assertEqual(todas["internal_sales_total"], Decimal("100.00"))


class LocalPropioDelProveedorTests(VentaInternaBase):
    def test_solo_un_proveedor_interno_puede_tener_local_propio(self):
        with self.assertRaises(ValidationError) as ctx:
            set_supplier_selling_branch(supplier=self.proveedor, sucursal=self.local_panaderia, actor=self.admin)
        self.assertIn("sucursal_vendedora", ctx.exception.message_dict)

        self.proveedor.sucursal_vendedora = self.local_panaderia
        with self.assertRaises(ValidationError) as ctx:
            self.proveedor.full_clean()
        self.assertIn("sucursal_vendedora", ctx.exception.message_dict)

    def test_el_proveedor_que_deja_de_ser_interno_pierde_el_local_propio(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        self.panaderia = set_supplier_internal(supplier=self.panaderia, es_interno=False, actor=self.admin)

        self.assertIsNone(self.panaderia.sucursal_vendedora_id)
        self.assertEqual(self._leer_snapshot()["internal_sales_total"], Decimal("0.00"))
        # Y se puede seguir editando desde la app, que valida el modelo entero.
        self.panaderia.full_clean()

    def test_definirlo_dos_veces_no_cambia_nada(self):
        self._con_local_propio()
        antes = self.panaderia.actualizado_en

        self._con_local_propio()

        self.assertEqual(self.panaderia.sucursal_vendedora, self.local_panaderia)
        self.assertEqual(self.panaderia.actualizado_en, antes)


class VentaInternaEnPantallaTests(VentaInternaBase):
    def _dashboard(self, sucursal):
        self.client.force_login(self.admin)
        session = self.client.session
        session["empresa_ids"] = [self.empresa.pk]
        session.save()
        return self.client.get(
            reverse("treasury:dashboard"),
            {
                "sucursal": sucursal.pk,
                "fecha_desde": self.desde.isoformat(),
                "fecha_hasta": self.hasta.isoformat(),
            },
        )

    def test_el_dashboard_del_local_propio_avisa_cuanto_es_venta_interna(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        response = self._dashboard(self.local_panaderia)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["economic_snapshot"]["internal_sales_total"], Decimal("100.00"))
        self.assertContains(response, "de ventas internas a locales propios, sin efectivo")

    def test_el_dashboard_de_un_local_sin_ventas_internas_no_dice_nada(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        response = self._dashboard(self.norte)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "ventas internas")

    def test_el_pdf_avisa_cuanto_de_las_ventas_es_interno(self):
        self._con_local_propio()
        self._factura_interna(self.centro, "100.00")

        doc, snapshot = self._leer(self.local_panaderia)

        self.assertEqual(doc.kpi("Ventas").value, Decimal("100.00"))
        self.assertIn(NOTA_VENTAS_INTERNAS.format(importe=format_value(Decimal("100.00"))), doc.notes)

    def test_el_pdf_sin_ventas_internas_no_lleva_la_nota(self):
        doc, _ = self._leer()

        self.assertFalse([nota for nota in doc.notes if "ventas internas" in nota])
