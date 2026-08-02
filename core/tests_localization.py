"""Regresion de USE_THOUSAND_SEPARATOR (separador de miles en templates).

Con el separador activo, cualquier id/pk/anio interpolado A MANO en un href,
action o value se renderizaria "1.234" y romperia el link o el formulario.
Esos casos llevan |unlocalize en el template; estos tests son la red que avisa
si un template nuevo (o una edicion) vuelve a interpolar un numero sin blindar.

Los datos se crean con pk >= 1000 a proposito: el bug recien aparece cuando los
ids llegan a 4 digitos, cosa que en produccion es cuestion de tiempo. Sin estos
tests, la regresion apareceria en silencio meses despues del deploy.
"""
import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from cashops.models import (
    Caja,
    CierreCaja,
    Empresa,
    MovimientoCaja,
    RubroOperativo,
    Sucursal,
    Turno,
)
from users.models import Role

User = get_user_model()

# Un numero con separador de miles dentro de un querystring interpolado a mano
# (?box=4.321) o de un value de input/option (value="1.500"). Las fechas ISO
# (2026-07-01) no matchean porque llevan guiones, y los montos visibles no
# matchean porque no viven en esos atributos.
QUERYSTRING_LOCALIZADO = re.compile(r'(?:href|action)="[^"]*=\d{1,3}(?:\.\d{3})+')
VALUE_LOCALIZADO = re.compile(r'value="\d{1,3}(?:\.\d{3})+"')


class SeparadorDeMilesRegressionTests(TestCase):
    """Renderiza las pantallas con ids de 4 digitos y detecta numeros rotos."""

    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_user(username="admin", password="test", role=self.admin_role)

        # pks de 4 digitos: el escenario que rompe si falta un |unlocalize.
        self.empresa = Empresa.objects.create(pk=1100, nombre="Empresa Test SRL")
        self.sucursal = Sucursal.objects.create(
            pk=1500, empresa=self.empresa, codigo="SUC-T", nombre="Sucursal Test",
            razon_social="Empresa Test SRL",
        )
        self.admin.empresas_permitidas.set([self.empresa])
        self.rubro = RubroOperativo.objects.create(pk=1300, nombre="Insumos")
        self.turno = Turno.objects.create(empresa=self.empresa, tipo=Turno.Tipo.MANANA, creado_por=self.admin)
        # Fecha del dia de la corrida, no una fija: el dashboard global ventanea
        # por fecha actual, y con una fecha fija el test caduco solo al cambiar
        # el mes (paso el 2026-08: escrito en julio con date(2026, 7, 15), la
        # caja quedo fuera de la ventana y el monto desaparecio de los KPIs).
        self.fecha = timezone.localdate()

        self.caja_abierta = Caja.objects.create(
            pk=4321, sucursal=self.sucursal, turno=self.turno, usuario=self.admin,
            fecha_operativa=self.fecha, monto_inicial=Decimal("100000.00"),
            estado=Caja.Estado.ABIERTA,
        )
        MovimientoCaja.objects.create(
            caja=self.caja_abierta, tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
            sentido=MovimientoCaja.Sentido.INGRESO, monto=Decimal("1500000.00"),
            categoria="Ventas", creado_por=self.admin,
        )

        self.caja_pendiente = Caja.objects.create(
            pk=4322, sucursal=self.sucursal, turno=self.turno, usuario=self.admin,
            fecha_operativa=self.fecha, monto_inicial=Decimal("0.00"),
            estado=Caja.Estado.CERRADA,
            validacion_estado=Caja.ValidacionEstado.PENDIENTE,
        )
        CierreCaja.objects.create(
            caja=self.caja_pendiente, saldo_esperado=Decimal("1200000.00"),
            saldo_fisico=Decimal("1200000.00"), diferencia=Decimal("0.00"),
            estado=CierreCaja.Estado.AUTO, cerrado_por=self.admin,
        )

        self.client.force_login(self.admin)

    def _assert_sin_numeros_localizados_en_atributos(self, response, url):
        html = response.content.decode("utf-8")
        rotos = QUERYSTRING_LOCALIZADO.findall(html) + VALUE_LOCALIZADO.findall(html)
        self.assertEqual(
            rotos, [],
            f"{url} interpola numeros con separador de miles en href/value; "
            f"falta |unlocalize en el template: {rotos[:5]}",
        )

    def _get_ok(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{url} devolvio {response.status_code}")
        self._assert_sin_numeros_localizados_en_atributos(response, url)
        return response

    def test_dashboard_cajas_links_sanos_y_montos_con_separador(self):
        response = self._get_ok("/operacion/")
        # El link a la caja abierta conserva el id crudo...
        self.assertContains(response, "box=4321")
        self.assertNotContains(response, "box=4.321")
        # ...y el objetivo del cambio se cumple: la plata sale con separador.
        self.assertContains(response, "1.500.000,00")

    def test_dashboard_cajas_vista_de_caja_individual(self):
        response = self._get_ok("/operacion/?scope=box&box=4321")
        self.assertContains(response, "Caja #4321")

    def test_seguimiento_de_cajas(self):
        response = self._get_ok("/cajas/seguimiento/")
        self.assertContains(response, "Caja #4321")

    def test_cola_de_validaciones(self):
        response = self._get_ok("/cajas/validaciones/")
        self.assertContains(response, "Caja #4322")
        self.assertContains(response, "1.200.000,00")

    def test_detalle_de_caja(self):
        response = self._get_ok(f"/cajas/{self.caja_abierta.pk}/detalle/")
        self.assertContains(response, "Caja #4321")

    def test_matriz_diaria_y_link_de_exportacion(self):
        response = self._get_ok(
            f"/gestion/matriz/?sucursal={self.sucursal.pk}"
            f"&fecha_desde={self.fecha:%Y-%m-%d}&fecha_hasta={self.fecha:%Y-%m-%d}"
        )
        self.assertContains(response, "sucursal=1500")

    def test_panel_de_alertas(self):
        self._get_ok("/alertas/")

    def test_dashboard_tesoreria(self):
        self._get_ok("/tesoreria/dashboard/")

    def test_disponibilidades_y_form_de_cierre_de_mes(self):
        response = self._get_ok("/tesoreria/disponibilidades/?month=7&year=2026")
        # El form de cerrar mes manda year=2026: localizado ("2.026") el POST
        # no valida y el cierre de mes queda inutilizable. Rompe HOY, no en el
        # futuro: por eso este assert es el mas importante del archivo.
        self.assertContains(response, 'value="2026"')
        self.assertNotContains(response, "2.026")
