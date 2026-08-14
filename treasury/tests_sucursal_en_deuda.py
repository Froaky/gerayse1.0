"""US-4.12: que la sucursal y la caja de origen se vean en las pantallas de deuda.

Caso real de produccion: hay 233 deudas abiertas donde el mismo proveedor tiene
varias facturas por el mismo importe en sucursales distintas. El peor caso es un
proveedor con 33 facturas de $27.500 repartidas en 5 sucursales. Ninguna de las
cuatro pantallas de deuda mostraba la sucursal, asi que las lineas quedaban
literalmente identicas y tesoreria no tenia con que elegir. De ahi salieron 10
pagos dobles dentro de un mismo lote.

Tesoreria trabaja con una planilla por proveedor / sucursal / fecha, asi que la
sucursal se muestra con su codigo ademas del nombre.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone

from cashops.models import Caja, Empresa, RubroOperativo, Sucursal, Turno
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    Proveedor,
)
from treasury.services import create_bank_movement

User = get_user_model()


class SucursalEnDeudaFixture(TestCase):
    """Solo fixtures: los tests viven en las clases de abajo."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-suc", password="test", email="suc@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa SUC")
        self.admin.empresas_permitidas.set([self.empresa])
        # Dos sucursales de la misma empresa: es el escenario que rompe hoy,
        # porque el mismo proveedor factura lo mismo en las dos.
        self.suc_a = Sucursal.objects.create(
            codigo="EC1", nombre="Estacion Central 1", razon_social="SUC", empresa=self.empresa
        )
        self.suc_b = Sucursal.objects.create(
            codigo="EB2", nombre="Estacion Belgrano 2", razon_social="SUC", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro SUC")
        self.turno = Turno.objects.create(
            empresa=self.empresa, tipo=Turno.Tipo.MANANA, creado_por=self.admin
        )
        self.proveedor = Proveedor.objects.create(
            razon_social="La Canada SUC", creado_por=self.admin
        )
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria SUC", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta SUC",
            banco="Banco SUC",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="900-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.hoy = timezone.localdate()

    def _caja(self, sucursal, fecha=None):
        return Caja.objects.create(
            sucursal=sucursal,
            turno=self.turno,
            fecha_operativa=fecha or self.hoy,
            usuario=self.admin,
            monto_inicial=Decimal("0.00"),
            estado=Caja.Estado.CERRADA,
        )

    def _factura(self, sucursal=None, importe="27500.00", caja=None, concepto="huevos"):
        return CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal(importe),
            saldo_pendiente=Decimal(importe),
            sucursal=sucursal,
            caja_origen=caja,
            creado_por=self.admin,
        )

    def _dos_facturas_iguales_de_distinta_sucursal(self):
        """El caso exacto que reporto tesoreria: mismo proveedor, mismo importe,
        sucursales distintas. Antes de este cambio las dos lineas eran iguales."""
        caja_a = self._caja(self.suc_a)
        caja_b = self._caja(self.suc_b)
        return (
            self._factura(sucursal=self.suc_a, caja=caja_a),
            self._factura(sucursal=self.suc_b, caja=caja_b),
        )


class EtiquetasDeDeudaTests(SucursalEnDeudaFixture):
    def test_sucursal_label_trae_codigo_y_nombre(self):
        factura = self._factura(sucursal=self.suc_a)

        self.assertEqual(factura.sucursal_label, "EC1 - Estacion Central 1")

    def test_sucursal_label_avisa_cuando_la_deuda_no_tiene_sucursal(self):
        # Las deudas legacy sin sucursal existen y se pagan; no pueden romper.
        factura = self._factura(sucursal=None)

        self.assertEqual(factura.sucursal_label, "Sin sucursal")

    def test_origen_label_identifica_la_caja_y_su_dia(self):
        caja = self._caja(self.suc_a, fecha=self.hoy)
        factura = self._factura(sucursal=self.suc_a, caja=caja)

        self.assertEqual(
            factura.origen_label,
            f"Caja #{caja.pk} del {self.hoy:%d/%m/%Y}",
        )

    def test_origen_label_distingue_la_carga_directa(self):
        factura = self._factura(sucursal=self.suc_a, caja=None)

        self.assertEqual(factura.origen_label, "Carga directa")

    def test_dos_facturas_iguales_dejan_de_ser_indistinguibles(self):
        una, otra = self._dos_facturas_iguales_de_distinta_sucursal()

        self.assertEqual(una.importe_total, otra.importe_total)
        self.assertEqual(una.proveedor_id, otra.proveedor_id)
        # Mismo proveedor y mismo importe, pero ya no dicen lo mismo.
        self.assertNotEqual(una.sucursal_label, otra.sucursal_label)
        self.assertNotEqual(una.origen_label, otra.origen_label)


class SucursalEnLasPantallasTests(SucursalEnDeudaFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def test_el_listado_de_cuentas_por_pagar_muestra_la_sucursal(self):
        self._dos_facturas_iguales_de_distinta_sucursal()

        response = self.client.get(reverse("treasury:cuentas_por_pagar_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EC1 - Estacion Central 1")
        self.assertContains(response, "EB2 - Estacion Belgrano 2")

    def test_el_detalle_de_la_deuda_muestra_sucursal_y_origen(self):
        caja = self._caja(self.suc_a)
        factura = self._factura(sucursal=self.suc_a, caja=caja)

        response = self.client.get(
            reverse("treasury:cuentas_por_pagar_detail", args=[factura.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sucursal")
        self.assertContains(response, "EC1 - Estacion Central 1")
        self.assertContains(response, f"Caja #{caja.pk} del {self.hoy:%d/%m/%Y}")

    def test_pagar_por_proveedor_distingue_las_dos_facturas(self):
        self._dos_facturas_iguales_de_distinta_sucursal()

        response = self.client.get(
            reverse("treasury:pagos_proveedor_create"),
            {"proveedor": self.proveedor.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EC1 - Estacion Central 1")
        self.assertContains(response, "EB2 - Estacion Belgrano 2")

    def test_repartir_transferencia_distingue_las_dos_facturas(self):
        self._dos_facturas_iguales_de_distinta_sucursal()
        movimiento = create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal("55000.00"),
            concepto="Pago semanal cuenta corriente",
            clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
            proveedor=self.proveedor,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.suc_a,
            periodo_pago=self.hoy.replace(day=1),
            actor=self.admin,
        )

        response = self.client.get(
            reverse("treasury:bank_movements_pay_debt", args=[movimiento.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EC1 - Estacion Central 1")
        self.assertContains(response, "EB2 - Estacion Belgrano 2")

    def test_el_listado_no_consulta_una_vez_por_deuda(self):
        """Las etiquetas nuevas leen sucursal y caja_origen. Sin select_related
        eso es una consulta por fila, y en produccion el listado trae 1.292."""
        caja = self._caja(self.suc_a)
        self._factura(sucursal=self.suc_a, caja=caja)
        url = reverse("treasury:cuentas_por_pagar_list")

        with CaptureQueriesContext(connection) as con_una:
            self.client.get(url)

        for _ in range(6):
            self._factura(sucursal=self.suc_b, caja=self._caja(self.suc_b))

        with CaptureQueriesContext(connection) as con_siete:
            self.client.get(url)

        self.assertEqual(len(con_siete), len(con_una))
