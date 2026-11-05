"""Egresos en efectivo, uno por uno (pedido de tesoreria del 05/11/2026).

Tesoreria quiere conciliar lo que salio en efectivo por sucursal y rubro, de la
caja fuerte y de las cajas de los locales, y no tenia una lista que lo junte.
Lo que se protege aca:
- el pago de una factura toma sucursal, rubro y proveedor de la factura, y el
  filtro por sucursal o rubro no lo pierde (el libro de efectivo central si);
- no entran anulados, ajustes de saldo ni aportes;
- las empresas no se mezclan, ni por filtro ni por URL;
- el Excel trae lo mismo que la pantalla.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from cashops.models import Caja, Empresa, RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_expense
from treasury.egresos_efectivo import CAJA_FUERTE, LOCAL, leer_egresos_efectivo
from treasury.models import MovimientoCajaCentral
from treasury.services import (
    annul_payment,
    create_payable_category,
    create_supplier,
    register_cash_payment,
    register_central_cash_movement,
    register_egreso_tesoreria,
    register_payable,
)
from users.models import Role

User = get_user_model()
DESDE = date(2026, 10, 1)
HASTA = date(2026, 10, 31)


class EgresosEfectivoBase(TestCase):
    """Octubre de ARMADI (Centro y Norte) y de MAPOGO (Vivre).

    ARMADI: pago en efectivo de una factura de verdura de Centro (300), un egreso
    sin factura de personal de Norte (120) y un gasto de verdura en una caja de
    Centro (80). MAPOGO: un egreso sin factura de Vivre (999).
    """

    def setUp(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-egresos", password="test", role=admin_role)
        self.operator = User.objects.create_user(username="cajero-egresos", password="test", role=operator_role)
        self.armadi = Empresa.objects.create(nombre="ARMADI Egresos SRL")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO Egresos SRL")
        for user in (self.admin, self.operator):
            user.empresas_permitidas.set([self.armadi, self.mapogo])
        self.centro = Sucursal.objects.create(codigo="CEN", nombre="Centro", razon_social="ARMADI", empresa=self.armadi)
        self.norte = Sucursal.objects.create(codigo="NOR", nombre="Norte", razon_social="ARMADI", empresa=self.armadi)
        self.vivre = Sucursal.objects.create(codigo="VIV", nombre="Vivre", razon_social="MAPOGO", empresa=self.mapogo)
        self.verdura = RubroOperativo.objects.create(nombre="Verdura")
        self.personal = RubroOperativo.objects.create(nombre="Personal")
        self.categoria = create_payable_category(nombre="Verduleria", rubro_operativo=self.verdura, actor=self.admin)
        self.proveedor = create_supplier(razon_social="Pare Carrito", actor=self.admin)
        for empresa in (self.armadi, self.mapogo):
            register_central_cash_movement(
                empresa=empresa,
                tipo=MovimientoCajaCentral.Tipo.APORTE,
                monto=Decimal("100000.00"),
                concepto="Efectivo inicial",
                fecha=DESDE,
                actor=self.admin,
            )

        self.factura = self._factura(self.centro, Decimal("300.00"), "A-0001")
        self.pago = register_cash_payment(
            payable=self.factura, fecha_pago=date(2026, 10, 5), monto=Decimal("300.00"),
            empresa=self.armadi, actor=self.admin,
        )
        self._egreso(self.armadi, self.norte, Decimal("120.00"), self.personal, "Sueldo Juan")
        self._egreso(self.mapogo, self.vivre, Decimal("999.00"), self.personal, "Sueldo Vivre")
        self.caja = self._caja(self.centro, date(2026, 10, 7))
        register_expense(
            caja=self.caja, monto=Decimal("80.00"), rubro_operativo=self.verdura,
            categoria="Verduleria del barrio", observacion="Tomates", actor=self.operator,
        )

    def _factura(self, sucursal, importe, comprobante):
        return register_payable(
            sucursal=sucursal,
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Verdura",
            fecha_emision=date(2026, 10, 3),
            fecha_vencimiento=date(2026, 10, 10),
            periodo_referencia=DESDE,
            importe_total=importe,
            referencia_comprobante=comprobante,
            actor=self.admin,
        )

    def _egreso(self, empresa, sucursal, monto, rubro, concepto, fecha=date(2026, 10, 6)):
        return register_egreso_tesoreria(
            empresa=empresa, fuente="CAJA_CENTRAL", fecha=fecha, monto=monto, concepto=concepto,
            rubro=rubro, sucursal=sucursal, periodo=DESDE, actor=self.admin,
        )

    def _caja(self, sucursal, fecha):
        turno, _ = Turno.objects.get_or_create(
            empresa=sucursal.empresa, tipo=Turno.Tipo.MANANA, defaults={"creado_por": self.admin}
        )
        return open_box(
            user=self.operator, turno=turno, sucursal=sucursal, fecha_operativa=fecha,
            monto_inicial=Decimal("0.00"), actor=self.admin,
        )

    def _leer(self, **filtros):
        filtros.setdefault("empresa_ids", [self.armadi.pk])
        return leer_egresos_efectivo(date_from=DESDE, date_to=HASTA, **filtros)

    def _filas(self, lectura):
        return [
            (e.origen, e.sucursal_label, e.rubro_label, e.importe) for e in lectura["egresos"]
        ]


class LecturaEgresosEfectivoTests(EgresosEfectivoBase):
    def test_junta_caja_fuerte_y_locales_ordenados_por_fecha(self):
        lectura = self._leer()

        self.assertEqual(
            self._filas(lectura),
            [
                (CAJA_FUERTE, "Centro", "Verdura", Decimal("300.00")),
                (CAJA_FUERTE, "Norte", "Personal", Decimal("120.00")),
                (LOCAL, "Centro", "Verdura", Decimal("80.00")),
            ],
        )
        self.assertEqual(lectura["total_caja_fuerte"], Decimal("420.00"))
        self.assertEqual(lectura["total_locales"], Decimal("80.00"))
        self.assertEqual(lectura["total"], Decimal("500.00"))
        self.assertEqual(lectura["cantidad"], 3)

    def test_el_pago_de_una_factura_muestra_proveedor_y_comprobante(self):
        pago = self._leer()["egresos"][0]

        self.assertEqual(pago.proveedor_o_categoria, "Pare Carrito")
        self.assertEqual(pago.referencia, "A-0001")
        self.assertEqual(pago.cuenta_por_pagar_id, self.factura.pk)

    def test_el_gasto_de_un_local_muestra_categoria_detalle_y_caja(self):
        gasto = self._leer(origen=LOCAL)["egresos"][0]

        self.assertEqual(gasto.proveedor_o_categoria, "Verduleria del barrio")
        self.assertEqual(gasto.detalle, "Tomates")
        self.assertEqual(gasto.referencia, f"Caja #{self.caja.pk}")
        self.assertFalse(gasto.caja_sin_validar)

    def test_filtrar_por_sucursal_no_pierde_los_pagos_de_facturas(self):
        # Regresion del libro de efectivo central: el pago no guarda sucursal y
        # al filtrar por sucursal desaparecia.
        lectura = self._leer(sucursal=self.centro)

        self.assertEqual(
            self._filas(lectura),
            [
                (CAJA_FUERTE, "Centro", "Verdura", Decimal("300.00")),
                (LOCAL, "Centro", "Verdura", Decimal("80.00")),
            ],
        )

    def test_filtrar_por_rubro_mira_el_rubro_de_la_factura(self):
        lectura = self._leer(rubro=self.verdura)

        self.assertEqual(lectura["total"], Decimal("380.00"))
        self.assertEqual({e.rubro_label for e in lectura["egresos"]}, {"Verdura"})

    def test_filtrar_por_origen(self):
        self.assertEqual(self._leer(origen=CAJA_FUERTE)["total"], Decimal("420.00"))
        self.assertEqual(self._leer(origen=LOCAL)["total"], Decimal("80.00"))

    def test_no_mezcla_empresas(self):
        self.assertNotIn(Decimal("999.00"), [e.importe for e in self._leer()["egresos"]])
        self.assertEqual(self._leer(empresa_ids=[self.mapogo.pk])["total"], Decimal("999.00"))
        # Una sucursal de otra empresa no trae datos ajenos aunque se la pida.
        self.assertEqual(self._leer(sucursal=self.vivre)["cantidad"], 0)
        # Sin empresa seleccionada no se ve nada.
        self.assertEqual(self._leer(empresa_ids=[])["cantidad"], 0)

    def test_no_entran_anulados_ajustes_aportes_ni_fuera_de_fecha(self):
        annul_payment(payment=self.pago, motivo="Cargado dos veces", actor=self.admin)
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO,
            monto=Decimal("5000.00"),
            concepto="Ajuste por arqueo",
            fecha=date(2026, 10, 15),
            actor=self.admin,
        )
        self._egreso(self.armadi, self.norte, Decimal("77.00"), self.personal, "Noviembre", fecha=date(2026, 11, 2))

        lectura = self._leer()

        self.assertEqual(
            self._filas(lectura),
            [
                (CAJA_FUERTE, "Norte", "Personal", Decimal("120.00")),
                (LOCAL, "Centro", "Verdura", Decimal("80.00")),
            ],
        )

    def test_el_gasto_de_una_caja_sin_validar_entra_marcado(self):
        Caja.objects.filter(pk=self.caja.pk).update(validacion_estado=Caja.ValidacionEstado.PENDIENTE)

        lectura = self._leer(origen=LOCAL)

        gasto = lectura["egresos"][0]
        self.assertTrue(gasto.caja_sin_validar)
        self.assertEqual(gasto.estado_caja, "Pendiente de validación")
        self.assertEqual(lectura["sin_validar_cantidad"], 1)
        self.assertEqual(lectura["sin_validar_total"], Decimal("80.00"))

    def test_una_caja_anulada_no_entra(self):
        Caja.objects.filter(pk=self.caja.pk).update(estado=Caja.Estado.ANULADA)

        self.assertEqual(self._leer(origen=LOCAL)["cantidad"], 0)

    def test_el_resumen_por_rubro_suma_lo_mismo_que_el_total(self):
        lectura = self._leer()

        resumen = {fila["rubro"]: (fila["caja_fuerte"], fila["locales"], fila["total"]) for fila in lectura["resumen_rubros"]}
        self.assertEqual(
            resumen,
            {
                "Personal": (Decimal("120.00"), Decimal("0.00"), Decimal("120.00")),
                "Verdura": (Decimal("300.00"), Decimal("80.00"), Decimal("380.00")),
            },
        )
        self.assertEqual(sum(fila["total"] for fila in lectura["resumen_rubros"]), lectura["total"])


class PantallaEgresosEfectivoTests(EgresosEfectivoBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.armadi.pk]
        sesion.save()
        self.url = reverse("treasury:egresos_efectivo")
        self.octubre = {"fecha_desde": "2026-10-01", "fecha_hasta": "2026-10-31"}

    def test_lista_los_egresos_de_la_empresa_seleccionada(self):
        response = self.client.get(self.url, self.octubre)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pare Carrito")
        self.assertContains(response, "Sueldo Juan")
        self.assertContains(response, "Tomates")
        self.assertNotContains(response, "Sueldo Vivre")
        self.assertEqual(response.context["lectura"]["total"], Decimal("500.00"))
        self.assertContains(response, reverse("treasury:cuentas_por_pagar_detail", args=[self.factura.pk]))
        self.assertContains(response, reverse("cashops:box_detail", args=[self.caja.pk]))

    def test_filtra_por_sucursal_rubro_y_origen(self):
        response = self.client.get(
            self.url, {**self.octubre, "sucursal": self.centro.pk, "rubro": self.verdura.pk, "origen": "caja_fuerte"}
        )

        self.assertEqual(response.context["lectura"]["total"], Decimal("300.00"))
        self.assertNotContains(response, "Tomates")

    def test_una_sucursal_de_otra_empresa_por_url_no_muestra_datos_ajenos(self):
        response = self.client.get(self.url, {**self.octubre, "sucursal": self.vivre.pk})

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sueldo Vivre")
        self.assertNotIn(self.vivre, response.context["filter_form"].fields["sucursal"].queryset)

    def test_sin_fechas_toma_el_mes_en_curso(self):
        response = self.client.get(self.url)

        filtros = response.context["filtros"]
        self.assertEqual(filtros["date_from"].day, 1)
        self.assertEqual(filtros["date_to"].month, filtros["date_from"].month)

    def test_sin_permiso_de_tesoreria_no_entra(self):
        self.client.force_login(self.operator)

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(reverse("treasury:egresos_efectivo_excel")).status_code, 403)

    def test_el_menu_de_tesoreria_tiene_el_acceso(self):
        response = self.client.get(reverse("treasury:dashboard"))

        self.assertContains(response, self.url)

    def test_el_excel_trae_lo_mismo_con_columna_para_tildar(self):
        response = self.client.get(reverse("treasury:egresos_efectivo_excel"), self.octubre)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.assertIn("egresos-efectivo-20261001-20261031.xlsx", response["Content-Disposition"])
        hoja = load_workbook(BytesIO(response.content)).active
        self.assertEqual(hoja.cell(row=4, column=1).value, "Conciliado")
        filas = [
            (hoja.cell(row=r, column=3).value, hoja.cell(row=r, column=4).value, hoja.cell(row=r, column=8).value)
            for r in range(5, 8)
        ]
        self.assertEqual(
            filas,
            [("Caja fuerte", "Centro", 300), ("Caja fuerte", "Norte", 120), ("Caja del local", "Centro", 80)],
        )
        self.assertEqual(hoja.cell(row=9, column=8).value, "=SUBTOTAL(109,H5:H7)")
        self.assertIn("ARMADI Egresos SRL", hoja["A2"].value)
        self.assertNotIn("Sueldo Vivre", [hoja.cell(row=r, column=7).value for r in range(5, 9)])
