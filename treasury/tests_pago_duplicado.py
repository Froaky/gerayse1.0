"""US-3.16: no pagar dos veces la misma factura dentro de un mismo lote.

Los 10 pagos dobles de produccion ($2.013.126,48) salieron todos asi: las dos
copias de la misma factura aparecian como dos lineas identicas y se tildaron las
dos en la misma operacion. Las referencias lo delatan: "sistema (6/26)" y
"sistema (8/26)", dos lineas del mismo lote.

Regla que pidio tesoreria: "si realmente esta duplicado el pago que te deje
elegir solo uno". Se corta el envio y se pide un tilde aparte, porque dos
facturas reales pueden coincidir en todo.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import create_bank_movement, lineas_que_parecen_la_misma_factura

User = get_user_model()


class PagoDuplicadoFixture(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-pd", password="test", email="pd@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa PD")
        self.admin.empresas_permitidas.set([self.empresa])
        self.suc_a = Sucursal.objects.create(
            codigo="EC1", nombre="Estacion Central 1", razon_social="PD", empresa=self.empresa
        )
        self.suc_b = Sucursal.objects.create(
            codigo="EB2", nombre="Estacion Belgrano 2", razon_social="PD", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro PD")
        self.proveedor = Proveedor.objects.create(
            razon_social="Crash Pollo PD", creado_por=self.admin
        )
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria PD", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta PD",
            banco="Banco PD",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="800-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.hoy = timezone.localdate()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def _factura(self, sucursal=None, importe="120000.00", fecha=None, concepto="1 caja de pollo"):
        fecha = fecha or self.hoy
        return CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=fecha,
            fecha_vencimiento=fecha,
            periodo_referencia=fecha.replace(day=1),
            importe_total=Decimal(importe),
            saldo_pendiente=Decimal(importe),
            sucursal=sucursal or self.suc_a,
            creado_por=self.admin,
        )

    def _dos_copias(self):
        """El caso de produccion: dos deudas identicas de la misma factura."""
        return self._factura(concepto="1 caja de pollo"), self._factura(concepto="1 caja de pollo")


class DeteccionEnElLoteTests(PagoDuplicadoFixture):
    def test_agrupa_dos_lineas_que_son_la_misma_factura(self):
        una, otra = self._dos_copias()

        grupos = lineas_que_parecen_la_misma_factura([una, otra])

        self.assertEqual(grupos, [[una, otra]])

    def test_no_agrupa_si_cambia_la_sucursal(self):
        una = self._factura(sucursal=self.suc_a)
        otra = self._factura(sucursal=self.suc_b)

        self.assertEqual(lineas_que_parecen_la_misma_factura([una, otra]), [])

    def test_no_agrupa_si_cambia_el_importe(self):
        una = self._factura(importe="120000.00")
        otra = self._factura(importe="120000.01")

        self.assertEqual(lineas_que_parecen_la_misma_factura([una, otra]), [])

    def test_no_agrupa_si_cambia_la_fecha_de_factura(self):
        una = self._factura(fecha=self.hoy)
        otra = self._factura(fecha=self.hoy - timezone.timedelta(days=1))

        self.assertEqual(lineas_que_parecen_la_misma_factura([una, otra]), [])

    def test_comprobantes_distintos_prueban_que_son_dos_facturas(self):
        """Cuando las dos tienen numero de comprobante cargado y distinto no hay
        nada que preguntar: dentro de un proveedor el numero es unico."""
        una = self._factura()
        una.referencia_comprobante = "F-0001"
        una.save(update_fields=["referencia_comprobante"])
        otra = self._factura()
        otra.referencia_comprobante = "F-0002"
        otra.save(update_fields=["referencia_comprobante"])

        self.assertEqual(lineas_que_parecen_la_misma_factura([una, otra]), [])

    def test_si_una_sola_tiene_comprobante_igual_se_pregunta(self):
        una = self._factura()
        una.referencia_comprobante = "F-0001"
        una.save(update_fields=["referencia_comprobante"])
        otra = self._factura()

        self.assertEqual(lineas_que_parecen_la_misma_factura([una, otra]), [[una, otra]])


class PagoPorProveedorTests(PagoDuplicadoFixture):
    def setUp(self):
        super().setUp()
        self.url = f"{reverse('treasury:pagos_proveedor_create')}?proveedor={self.proveedor.pk}"

    def _payload(self, facturas, **extra):
        datos = {
            "medio_pago": PagoTesoreria.MedioPago.TRANSFERENCIA,
            "cuenta_bancaria": self.cuenta.pk,
            "fecha_pago": self.hoy.isoformat(),
            "referencia": "",
            "observaciones": "",
        }
        for factura in facturas:
            datos[f"pagar_{factura.pk}"] = "on"
            datos[f"monto_{factura.pk}"] = str(factura.saldo_pendiente)
        datos.update(extra)
        return datos

    def test_tildar_las_dos_copias_no_paga_y_avisa(self):
        una, otra = self._dos_copias()

        response = self.client.post(self.url, self._payload([una, otra]))

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "parecen la misma", status_code=400)
        self.assertContains(response, f"#{una.pk}", status_code=400)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        # Y aparece el tilde para insistir si de verdad son dos.
        self.assertContains(response, "Son facturas distintas", status_code=400)

    def test_con_el_tilde_de_confirmacion_paga_las_dos(self):
        una, otra = self._dos_copias()

        response = self.client.post(
            self.url, self._payload([una, otra], confirmar_duplicado="on")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PagoTesoreria.objects.count(), 2)

    def test_dos_facturas_distintas_se_pagan_sin_molestar(self):
        una = self._factura(sucursal=self.suc_a)
        otra = self._factura(sucursal=self.suc_b)

        response = self.client.post(self.url, self._payload([una, otra]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PagoTesoreria.objects.count(), 2)

    def test_una_sola_factura_no_dispara_nada(self):
        una, _otra = self._dos_copias()

        response = self.client.post(self.url, self._payload([una]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PagoTesoreria.objects.count(), 1)


class RepartirTransferenciaTests(PagoDuplicadoFixture):
    def setUp(self):
        super().setUp()
        self.movimiento = create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal("240000.00"),
            concepto="Pago semanal cuenta corriente",
            clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
            proveedor=self.proveedor,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.suc_a,
            periodo_pago=self.hoy.replace(day=1),
            actor=self.admin,
        )
        self.url = reverse("treasury:bank_movements_pay_debt", args=[self.movimiento.pk])

    def _payload(self, facturas, **extra):
        datos = {
            "payable_id": [str(f.pk) for f in facturas],
        }
        for factura in facturas:
            datos[f"monto_{factura.pk}"] = str(factura.saldo_pendiente)
        datos.update(extra)
        return datos

    def test_marcar_las_dos_copias_no_paga_y_avisa(self):
        una, otra = self._dos_copias()

        response = self.client.post(self.url, self._payload([una, otra]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(PagoTesoreria.objects.count(), 0)
        mensajes = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("parecen la misma" in m for m in mensajes), mensajes)
        self.assertContains(response, "Son facturas distintas, pagar las dos igual")

    def test_las_marcadas_siguen_marcadas_despues_del_aviso(self):
        """Si se perdiera la seleccion habria que volver a tildar todo para
        poder confirmar, y nadie lo haria."""
        una, otra = self._dos_copias()

        response = self.client.post(self.url, self._payload([una, otra]))

        elegidas = {fila["payable"].pk for fila in response.context["facturas"] if fila["elegida"]}
        self.assertEqual(elegidas, {una.pk, otra.pk})

    def test_con_el_tilde_de_confirmacion_paga_las_dos(self):
        una, otra = self._dos_copias()

        response = self.client.post(
            self.url, self._payload([una, otra], confirmar_duplicado="1")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PagoTesoreria.objects.count(), 2)

    def test_dos_facturas_distintas_se_reparten_sin_molestar(self):
        una = self._factura(sucursal=self.suc_a)
        otra = self._factura(sucursal=self.suc_b)

        response = self.client.post(self.url, self._payload([una, otra]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PagoTesoreria.objects.count(), 2)
