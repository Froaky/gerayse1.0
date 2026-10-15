"""US-3.18: poder llegar a todos los movimientos del mes en el libro de efectivo.

Caso real: tesoreria tenia que anular 7 egresos administrativos del 02, 03 y 05
de junio. Filtro junio y solo le aparecieron los del 26 en adelante. El listado
cortaba en 100 y ordena por fecha descendente, asi que en un mes con mas de 100
movimientos los primeros dias eran INALCANZABLES desde la pantalla: no hay
paginacion, y el boton de anular vive en cada fila del listado.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CajaCentral,
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    Proveedor,
)
from treasury.services import register_cash_payment, register_central_cash_movement
from users.models import PermissionModule, UserPermission

User = get_user_model()


class AlcanceDelLibroDeEfectivoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-libro", password="test", email="libro@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa LIBRO")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            codigo="LB1", nombre="Sucursal LIBRO", razon_social="LIBRO", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro LIBRO")
        self.boveda = CajaCentral.objects.create(
            nombre="Boveda LIBRO", empresa=self.empresa
        )
        UserPermission.objects.create(
            user=self.admin,
            module=PermissionModule.TREASURY_MOV_DELETE,
            can_read=True,
            can_write=True,
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def _egreso(self, dia, concepto):
        return MovimientoCajaCentral.objects.create(
            caja_central=self.boveda,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            fecha=date(2026, 6, dia),
            monto=Decimal("1000.00"),
            concepto=concepto,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=date(2026, 6, 1),
            creado_por=self.admin,
        )

    def test_el_movimiento_del_primer_dia_del_mes_se_alcanza(self):
        """Con mas de 100 movimientos en el mes, el del dia 2 tiene que estar
        igual: es el que tesoreria necesitaba anular y no encontraba."""
        viejo = self._egreso(2, "EGRESO DEL DOS DE JUNIO")
        for i in range(120):
            self._egreso(26, f"relleno {i}")

        response = self.client.get(
            reverse("treasury:central_cash_list"), {"year": 2026, "month": 6}
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EGRESO DEL DOS DE JUNIO")
        # Y con su boton de anular, que es a lo que no podia llegar.
        self.assertContains(
            response, reverse("treasury:central_cash_annul_confirm", args=[viejo.pk])
        )

    def test_avisa_si_el_tope_de_seguridad_actua(self):
        """El tope alto sigue existiendo como freno, pero cuando corta lo dice."""
        self._egreso(2, "EGRESO DEL DOS DE JUNIO")
        for i in range(120):
            self._egreso(26, f"relleno {i}")

        response = self.client.get(
            reverse("treasury:central_cash_list"), {"year": 2026, "month": 6}
        )

        # Con 121 movimientos no corta nada, asi que no debe aparecer el aviso.
        self.assertNotContains(response, "se muestran los")


class PagoDeFacturaEnElLibroTests(TestCase):
    """Caso real (15/10): tesoreria pago en efectivo facturas de verduras y en el
    libro todos los pagos salian "sin sucursal imputada". El EGRESO_PAGO no
    guarda sucursal, rubro ni periodo: los tiene la factura que pago. Como no se
    podian editar, creia que los tenia que anular para imputarlos."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-pago-libro", password="test", email="pagolibro@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa PAGO LIBRO")
        self.admin.empresas_permitidas.set([self.empresa])
        self.sucursal = Sucursal.objects.create(
            codigo="PL1", nombre="Sucursal PAGO LIBRO", razon_social="PAGO LIBRO", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro VERDURA")
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria VERDURA", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.proveedor = Proveedor.objects.create(razon_social="Verduleria TEST", creado_por=self.admin)
        CajaCentral.objects.create(nombre="Boveda PAGO LIBRO", empresa=self.empresa)
        # El fondeo va en mayo: un aporte es de la empresa y no tiene sucursal,
        # asi que en el libro de junio ensuciaria el "sin sucursal imputada".
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("10000.00"),
            concepto="Fondeo",
            fecha=date(2026, 5, 1),
            actor=self.admin,
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def _pagar_en_efectivo(self, *, sucursal, concepto):
        factura = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=date(2026, 5, 20),
            fecha_vencimiento=date(2026, 5, 30),
            periodo_referencia=date(2026, 5, 1),
            importe_total=Decimal("300.00"),
            saldo_pendiente=Decimal("300.00"),
            sucursal=sucursal,
            creado_por=self.admin,
        )
        register_cash_payment(
            payable=factura,
            fecha_pago=date(2026, 6, 10),
            monto=Decimal("300.00"),
            empresa=self.empresa,
            actor=self.admin,
        )
        return MovimientoCajaCentral.objects.get(pago_tesoreria__cuenta_por_pagar=factura)

    def _libro_de_junio(self):
        return self.client.get(reverse("treasury:central_cash_list"), {"year": 2026, "month": 6})

    def test_el_pago_muestra_la_sucursal_el_rubro_y_el_periodo_de_la_factura(self):
        self._pagar_en_efectivo(sucursal=self.sucursal, concepto="verduras de mayo")

        response = self._libro_de_junio()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "verduras de mayo")
        self.assertContains(response, "Sucursal: Sucursal PAGO LIBRO")
        self.assertContains(response, "Rubro: Rubro VERDURA")
        self.assertContains(response, "Periodo: 05/2026 de la factura")
        self.assertNotContains(response, "sin sucursal imputada")

    def test_solo_cambia_lo_que_se_muestra(self):
        """El movimiento sigue sin sucursal propia: el saldo por sucursal de la
        boveda y las lecturas que lo usan quedan exactamente como estaban."""
        egreso = self._pagar_en_efectivo(sucursal=self.sucursal, concepto="verduras de mayo")

        self.assertIsNone(egreso.sucursal_gasto_id)
        self.assertIsNone(egreso.rubro_operativo_id)
        self.assertIsNone(egreso.periodo_pago)

    def test_si_la_factura_no_tiene_sucursal_lo_sigue_diciendo(self):
        self._pagar_en_efectivo(sucursal=None, concepto="factura sin sucursal")

        response = self._libro_de_junio()

        self.assertContains(response, "factura sin sucursal")
        self.assertContains(response, "sin sucursal imputada")


class AlcanceDeMovimientosBancariosTests(TestCase):
    """Mismo corte silencioso en el listado de banco, con dos diferencias: el
    tope era de 50 y esta pantalla NO acota por mes, asi que sin filtros lista
    todo el historico. El tope se queda; lo que se arregla es que sea usable y
    que el aviso diga como llegar al resto."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-banco", password="test", email="banco@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa BANCO")
        self.admin.empresas_permitidas.set([self.empresa])
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta BANCO",
            banco="Banco BANCO",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="700-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def _credito(self, dia, concepto):
        return MovimientoBancario.objects.create(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.CREDITO,
            clase=MovimientoBancario.Clase.OTRO_INGRESO,
            fecha=date(2026, 6, dia),
            monto=Decimal("1000.00"),
            concepto=concepto,
            creado_por=self.admin,
        )

    def test_pasa_el_tope_viejo_de_cincuenta(self):
        buscado = self._credito(1, "MOVIMIENTO NUMERO UNO")
        for i in range(60):
            self._credito(20, f"relleno {i}")

        response = self.client.get(reverse("treasury:bank_movements_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "MOVIMIENTO NUMERO UNO")
        self.assertContains(
            response, reverse("treasury:bank_movements_detail", args=[buscado.pk])
        )

    def test_si_corta_dice_como_llegar_al_resto(self):
        """El aviso viejo decia cuantos quedaban afuera pero no que hacer."""
        for i in range(61):
            self._credito(20, f"relleno {i}")

        response = self.client.get(reverse("treasury:bank_movements_list"))

        # Con 61 movimientos y tope 300 no corta, asi que no debe avisar.
        self.assertNotContains(response, "se muestran los")
