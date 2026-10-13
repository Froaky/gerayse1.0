"""Vistas del modulo de reportes PDF (slice S5): permisos, aislamiento por empresa,
formulario de alcance y periodo, y respuesta PDF."""

import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.forms import ReportFilterForm
from treasury.reportes.registro import REGISTRO
from treasury.services import create_payable_category, create_supplier, register_payable
from users.models import PermissionModule, Role, RolePermission

User = get_user_model()


def _pages(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", pdf))


class ReportesVistasBase(TestCase):
    def setUp(self):
        self.hoy = timezone.localdate()
        self.mes = self.hoy.strftime("%Y-%m")
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.armadi = Empresa.objects.create(nombre="ARMADI SRL", identificador_fiscal="30-71111111-1")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO SRL")
        self.ajena = Empresa.objects.create(nombre="AJENA SA")
        self.belgrano = Sucursal.objects.create(nombre="Estación Belgrano", codigo="EB", razon_social="ARMADI SRL", empresa=self.armadi)
        self.palermo = Sucursal.objects.create(nombre="Palermo", codigo="PAL", razon_social="ARMADI SRL", empresa=self.armadi)
        self.cerrada = Sucursal.objects.create(
            nombre="Cerrada", codigo="CER", razon_social="ARMADI SRL", empresa=self.armadi, activa=False
        )
        self.vivre = Sucursal.objects.create(nombre="Vivre", codigo="VIVRE", razon_social="MAPOGO SRL", empresa=self.mapogo)
        self.ajena_suc = Sucursal.objects.create(nombre="Ajena", codigo="AJ", razon_social="AJENA SA", empresa=self.ajena)
        self.admin = User.objects.create_user(username="admin-reportes", password="test", role=admin_role)
        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.client.force_login(self.admin)
        self._seleccionar_empresas([self.armadi.pk, self.mapogo.pk])

    def _seleccionar_empresas(self, ids):
        session = self.client.session
        session["empresa_ids"] = ids
        session.save()

    def _pdf_url(self, slug):
        return reverse("treasury:reporte_pdf", args=[slug])

    def _get_pdf(self, slug, **params):
        datos = {"alcance": f"e{self.armadi.pk}", "mes_desde": self.mes, "mes_hasta": self.mes}
        datos.update(params)
        return self.client.get(self._pdf_url(slug), {k: v for k, v in datos.items() if v is not None})


class ReportesPermisosTests(ReportesVistasBase):
    def test_anonimo_va_al_login(self):
        self.client.logout()
        for url in (reverse("treasury:reportes"), self._pdf_url("situacion-economica")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])

    def test_usuario_sin_tesoreria_recibe_403(self):
        operador = User.objects.create_user(username="operador-reportes", password="test", role=self.operator_role)
        operador.empresas_permitidas.set([self.armadi])
        self.client.force_login(operador)
        self._seleccionar_empresas([self.armadi.pk])
        self.assertEqual(self.client.get(reverse("treasury:reportes")).status_code, 403)
        self.assertEqual(self._get_pdf("situacion-economica").status_code, 403)

    def test_rol_de_solo_lectura_de_tesoreria_descarga(self):
        read_role = Role.objects.create(code="TESORERIA_LECTURA", name="Tesorería lectura")
        RolePermission.objects.create(role=read_role, module=PermissionModule.TREASURY, can_read=True, can_write=False)
        lector = User.objects.create_user(username="lector-reportes", password="test", role=read_role)
        lector.empresas_permitidas.set([self.armadi])
        self.client.force_login(lector)
        self._seleccionar_empresas([self.armadi.pk])
        self.assertEqual(self.client.get(reverse("treasury:reportes")).status_code, 200)
        response = self._get_pdf("situacion-financiera")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")


class ReportesDescargaTests(ReportesVistasBase):
    def test_cada_reporte_descarga_un_pdf_con_nombre_esperado(self):
        esperado = {
            "situacion-economica": f"situacion-economica_armadi-srl_general_{self.mes}_{self.mes}.pdf",
            "situacion-financiera": f"situacion-financiera_armadi-srl_general_{self.mes}_{self.mes}.pdf",
            "desglose-deudas": f"desglose-deudas_armadi-srl_general_al_{self.hoy:%Y-%m-%d}.pdf",
        }
        self.assertEqual(set(REGISTRO), set(esperado))
        for slug, nombre in esperado.items():
            with self.subTest(slug=slug):
                params = {"mes_desde": None, "mes_hasta": None} if slug == "desglose-deudas" else {}
                response = self._get_pdf(slug, **params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertEqual(response["Content-Disposition"], f'attachment; filename="{nombre}"')
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_por_sucursal_usa_el_codigo_en_el_nombre(self):
        response = self._get_pdf("situacion-economica", alcance=f"s{self.belgrano.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"_eb_{self.mes}_{self.mes}.pdf", response["Content-Disposition"])

    def test_general_y_cada_sucursal_en_un_solo_pdf(self):
        response = self._get_pdf("situacion-economica", alcance=f"t{self.armadi.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("_general-y-sucursales_", response["Content-Disposition"])
        # General + Estacion Belgrano + Palermo (la inactiva no): al menos una pagina cada uno.
        self.assertGreaterEqual(_pages(response.content), 3)

    def test_desglose_con_filtro_de_periodos(self):
        response = self._get_pdf("desglose-deudas")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"_periodos_{self.mes}_{self.mes}", response["Content-Disposition"])

    def test_slug_inexistente_da_404(self):
        self.assertEqual(self._get_pdf("no-existe").status_code, 404)


class ReportesAislamientoTests(ReportesVistasBase):
    def test_sucursal_de_empresa_no_permitida_da_404(self):
        self.assertEqual(self._get_pdf("situacion-economica", alcance=f"s{self.ajena_suc.pk}").status_code, 404)

    def test_empresa_no_permitida_da_404(self):
        self.assertEqual(self._get_pdf("situacion-financiera", alcance=f"e{self.ajena.pk}").status_code, 404)

    def test_empresa_permitida_pero_no_seleccionada_da_404(self):
        self._seleccionar_empresas([self.armadi.pk])
        self.assertEqual(self._get_pdf("situacion-economica", alcance=f"e{self.mapogo.pk}").status_code, 404)
        self.assertEqual(self._get_pdf("situacion-economica", alcance=f"s{self.vivre.pk}").status_code, 404)

    def test_sucursal_inactiva_da_404(self):
        self.assertEqual(self._get_pdf("situacion-economica", alcance=f"s{self.cerrada.pk}").status_code, 404)

    def test_alcance_mal_formado_da_404(self):
        for valor in ("x1", "e", "eabc", "s999999"):
            with self.subTest(valor=valor):
                self.assertEqual(self._get_pdf("situacion-economica", alcance=valor).status_code, 404)

    def test_deuda_de_otra_empresa_no_entra_en_el_pdf(self):
        rubro = RubroOperativo.objects.create(nombre="Servicios")
        categoria = create_payable_category(nombre="Servicios", rubro_operativo=rubro, actor=self.admin)
        proveedor = create_supplier(razon_social="Proveedor Ajeno SA", actor=self.admin)
        register_payable(
            proveedor=proveedor,
            categoria=categoria,
            sucursal=self.ajena_suc,
            concepto="Factura ajena",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            importe_total=Decimal("999.00"),
            actor=self.admin,
        )
        form = ReportFilterForm({"alcance": f"e{self.armadi.pk}"}, empresas=[self.armadi], usa_periodo=False)
        self.assertTrue(form.is_valid(), form.errors)
        documento = REGISTRO["desglose-deudas"].builder(form.build_scopes()[0])
        self.assertEqual(documento.kpi("Deuda abierta").value, Decimal("0.00"))


class ReportesFormularioTests(ReportesVistasBase):
    def test_sin_alcance_vuelve_a_la_pagina_con_error(self):
        response = self._get_pdf("situacion-economica", alcance="")
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_mes_hasta_anterior_vuelve_con_error(self):
        anterior = (self.hoy.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        response = self._get_pdf("situacion-economica", mes_desde=self.mes, mes_hasta=anterior)
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_mes_futuro_vuelve_con_error(self):
        siguiente = date(self.hoy.year + 1, 1, 1).strftime("%Y-%m")
        response = self._get_pdf("situacion-economica", mes_desde=siguiente, mes_hasta=siguiente)
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_periodo_es_obligatorio_en_los_reportes_por_periodo(self):
        response = self._get_pdf("situacion-economica", mes_desde=None, mes_hasta=None)
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_desglose_con_un_solo_mes_vuelve_con_error(self):
        response = self._get_pdf("desglose-deudas", mes_hasta=None)
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def _mes(self, meses):
        anio, mes = divmod(self.hoy.year * 12 + self.hoy.month - 1 + meses, 12)
        return f"{anio:04d}-{mes + 1:02d}"

    def test_mes_hasta_futuro_vuelve_con_error(self):
        # Sin tope, la situacion financiera tomaba de corte una fecha futura.
        for slug in ("situacion-economica", "situacion-financiera"):
            with self.subTest(slug=slug):
                response = self._get_pdf(slug, mes_desde=self.mes, mes_hasta=self._mes(1))
                self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_anios_fuera_de_rango_no_rompen(self):
        casos = (
            ("situacion-economica", {"mes_hasta": "9999-12"}),
            ("situacion-financiera", {"mes_hasta": "9999-12"}),
            ("desglose-deudas", {"mes_desde": self.mes, "mes_hasta": "9999-12"}),
            ("situacion-economica", {"mes_desde": "1999-12"}),
            ("desglose-deudas", {"mes_desde": "0001-01", "mes_hasta": self.mes}),
        )
        for slug, params in casos:
            with self.subTest(slug=slug, params=params):
                response = self._get_pdf(slug, **params)
                self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_desglose_deja_filtrar_hasta_el_anio_que_viene(self):
        response = self._get_pdf("desglose-deudas", mes_desde=self.mes, mes_hasta=f"{self.hoy.year + 1}-12")
        self.assertEqual(response.status_code, 200)
        response = self._get_pdf("desglose-deudas", mes_desde=self.mes, mes_hasta=f"{self.hoy.year + 2}-01")
        self.assertRedirects(response, reverse("treasury:reportes"), fetch_redirect_response=False)

    def test_los_meses_tienen_tope_en_el_selector(self):
        form = ReportFilterForm(empresas=[self.armadi])
        self.assertEqual(form.fields["mes_hasta"].widget.attrs["max"], self.mes)
        self.assertEqual(form.fields["mes_desde"].widget.attrs["min"], "2000-01")
        desglose = ReportFilterForm(empresas=[self.armadi], usa_periodo=False)
        self.assertEqual(desglose.fields["mes_hasta"].widget.attrs["max"], f"{self.hoy.year + 1}-12")

    def test_sucursal_cerrada_impide_rotular_como_unica(self):
        # El general suma tambien la sucursal cerrada: no puede decir "unica".
        Sucursal.objects.create(
            nombre="Vivre vieja", codigo="VIV2", razon_social="MAPOGO SRL", empresa=self.mapogo, activa=False
        )
        form = ReportFilterForm(empresas=[self.mapogo])
        self.assertEqual(
            form.fields["alcance"].choices,
            [
                (f"e{self.mapogo.pk}", "General (todas las sucursales)"),
                (f"t{self.mapogo.pk}", "General y cada sucursal (un solo PDF)"),
                (f"s{self.vivre.pk}", "Sucursal Vivre"),
            ],
        )
        form = ReportFilterForm(
            {"alcance": f"e{self.mapogo.pk}", "mes_desde": self.mes, "mes_hasta": self.mes}, empresas=[self.mapogo]
        )
        self.assertTrue(form.is_valid(), form.errors)
        (scope,) = form.build_scopes()
        self.assertIsNone(scope.sucursal_unica)
        self.assertEqual(scope.alcance_label, "General (todas las sucursales)")

    def test_opciones_de_alcance_por_empresa(self):
        form = ReportFilterForm(empresas=[self.armadi, self.mapogo])
        grupos = dict(form.fields["alcance"].choices)
        self.assertEqual(
            [valor for valor, _ in grupos["ARMADI SRL"]],
            [f"e{self.armadi.pk}", f"t{self.armadi.pk}", f"s{self.belgrano.pk}", f"s{self.palermo.pk}"],
        )
        # Mapogo tiene una sola sucursal: solo el general, rotulado con ella.
        self.assertEqual(grupos["MAPOGO SRL"], [(f"e{self.mapogo.pk}", "General · Vivre (única sucursal)")])

    def test_una_sola_empresa_no_agrupa_las_opciones(self):
        form = ReportFilterForm(empresas=[self.armadi])
        self.assertEqual(form.fields["alcance"].choices[0], (f"e{self.armadi.pk}", "General (todas las sucursales)"))

    def test_general_de_empresa_con_una_sucursal_se_rotula_con_ella(self):
        form = ReportFilterForm(
            {"alcance": f"e{self.mapogo.pk}", "mes_desde": self.mes, "mes_hasta": self.mes}, empresas=[self.mapogo]
        )
        self.assertTrue(form.is_valid(), form.errors)
        (scope,) = form.build_scopes()
        self.assertIsNone(scope.sucursal)
        self.assertEqual(scope.alcance_label, "Sucursal Vivre (única de la empresa)")

    def test_todas_arma_el_general_y_cada_sucursal_activa(self):
        form = ReportFilterForm(
            {"alcance": f"t{self.armadi.pk}", "mes_desde": self.mes, "mes_hasta": self.mes}, empresas=[self.armadi]
        )
        self.assertTrue(form.is_valid(), form.errors)
        scopes = form.build_scopes()
        self.assertEqual([scope.sucursal for scope in scopes], [None, self.belgrano, self.palermo])
        self.assertTrue(scopes[0].todas_las_sucursales)

    def test_periodo_va_del_primer_dia_al_ultimo_o_hasta_hoy(self):
        form = ReportFilterForm(
            {"alcance": f"e{self.armadi.pk}", "mes_desde": "2026-07", "mes_hasta": "2026-08"},
            empresas=[self.armadi],
            hoy=date(2026, 10, 13),
        )
        self.assertTrue(form.is_valid(), form.errors)
        (scope,) = form.build_scopes()
        self.assertEqual((scope.date_from, scope.date_to), (date(2026, 7, 1), date(2026, 8, 31)))


class ReportesPaginaTests(ReportesVistasBase):
    def test_pagina_lista_los_tres_reportes(self):
        response = self.client.get(reverse("treasury:reportes"))
        self.assertEqual(response.status_code, 200)
        for reporte in REGISTRO.values():
            self.assertContains(response, reporte.titulo)
            self.assertContains(response, self._pdf_url(reporte.slug))
        self.assertContains(response, "Descargar PDF", count=len(REGISTRO))

    def test_pagina_no_ofrece_empresas_no_seleccionadas(self):
        self._seleccionar_empresas([self.armadi.pk])
        response = self.client.get(reverse("treasury:reportes"))
        self.assertContains(response, f'value="e{self.armadi.pk}"')
        self.assertNotContains(response, f'value="e{self.mapogo.pk}"')
        self.assertNotContains(response, f'value="e{self.ajena.pk}"')

    def test_menu_de_tesoreria_tiene_el_acceso(self):
        response = self.client.get(reverse("treasury:dashboard"))
        self.assertContains(response, reverse("treasury:reportes"))
        self.assertContains(response, "Reportes PDF")
