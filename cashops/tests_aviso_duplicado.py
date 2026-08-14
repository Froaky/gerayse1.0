"""US-3.15: avisar cuando la deuda que se esta cargando ya parece estar cargada.

Origen: en produccion se encontraron 19 facturas cargadas dos veces, y 10 de
ellas se pagaron dos veces ($2.013.126,48) porque al pagar por lote aparecian
como dos lineas identicas y se tildaron las dos.

La regla la definio tesoreria: buscar coincidencia en proveedor + sucursal +
fecha de factura + importe, avisar, y DEJAR GUARDAR IGUAL. Sus palabras: "que
busque coincidencias en monto, fecha y sucursal y ahi largue el aviso y un
cartel 'continua de todos modos o guardar de todos modos' pero deje continuar la
carga". Dos facturas reales pueden coincidir en las cuatro cosas.

El numero de comprobante NO participa: el 82% de las deudas abiertas no lo tiene
y ademas algunos proveedores repiten numeracion cuando usan remiteros.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cashops.models import Caja, Empresa, RubroOperativo, Sucursal, Turno
from cashops.services import open_box, register_box_expense_debt
from treasury.models import CategoriaCuentaPagar, CuentaPorPagar, Proveedor
from treasury.services import deudas_posiblemente_duplicadas
from users.models import Role

User = get_user_model()


class AvisoDuplicadoFixture(TestCase):
    def setUp(self):
        self.role = Role.objects.create(code="ADMIN", name="Administrador")
        self.admin = User.objects.create_superuser(
            username="admin-dup", password="test", email="dup@test.com", role=self.role
        )
        self.empresa = Empresa.objects.create(nombre="Empresa DUP")
        self.admin.empresas_permitidas.set([self.empresa])
        self.suc_a = Sucursal.objects.create(
            codigo="EC1", nombre="Estacion Central 1", razon_social="DUP", empresa=self.empresa
        )
        self.suc_b = Sucursal.objects.create(
            codigo="EB2", nombre="Estacion Belgrano 2", razon_social="DUP", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro DUP")
        self.turno = Turno.objects.create(
            empresa=self.empresa, tipo=Turno.Tipo.MANANA, creado_por=self.admin
        )
        self.proveedor = Proveedor.objects.create(
            razon_social="Pare Carrito DUP", creado_por=self.admin
        )
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria DUP", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.fecha_op = date(2026, 3, 27)
        self.caja = open_box(
            user=self.admin,
            turno=self.turno,
            sucursal=self.suc_a,
            fecha_operativa=self.fecha_op,
            monto_inicial=Decimal("100.00"),
            actor=self.admin,
        )

    def _deuda(self, sucursal=None, importe="120000.00", fecha=None, concepto="frutas"):
        fecha = fecha or self.fecha_op
        return CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=fecha,
            fecha_vencimiento=fecha,
            periodo_referencia=fecha.replace(day=1),
            importe_total=Decimal(importe),
            saldo_pendiente=Decimal(importe),
            sucursal=sucursal if sucursal is not None else self.suc_a,
            caja_origen=self.caja,
            creado_por=self.admin,
        )

    # Centinela: hace falta poder pedir explicitamente "sin sucursal", que es
    # distinto de "no me pasaron sucursal".
    SIN_PASAR = object()

    def _buscar(self, sucursal=SIN_PASAR, importe="120000.00", fecha=None, **extra):
        return list(
            deudas_posiblemente_duplicadas(
                proveedor=self.proveedor,
                sucursal=self.suc_a if sucursal is self.SIN_PASAR else sucursal,
                fecha_emision=fecha or self.fecha_op,
                importe=Decimal(importe),
                **extra,
            )
        )


class DeteccionDeDuplicadosTests(AvisoDuplicadoFixture):
    def test_encuentra_la_deuda_igual(self):
        ya_cargada = self._deuda()

        self.assertEqual(self._buscar(), [ya_cargada])

    def test_no_marca_si_cambia_la_sucursal(self):
        """El caso que tesoreria pidio respetar: el mismo remito por el mismo
        importe en dos sucursales distintas son dos facturas de verdad."""
        self._deuda(sucursal=self.suc_b)

        self.assertEqual(self._buscar(sucursal=self.suc_a), [])

    def test_no_marca_si_cambia_el_importe(self):
        self._deuda(importe="120000.00")

        self.assertEqual(self._buscar(importe="120000.01"), [])

    def test_no_marca_si_cambia_la_fecha_de_factura(self):
        self._deuda(fecha=date(2026, 3, 27))

        self.assertEqual(self._buscar(fecha=date(2026, 3, 28)), [])

    def test_ignora_las_anuladas(self):
        anulada = self._deuda()
        anulada.estado = CuentaPorPagar.Estado.ANULADA
        anulada.save(update_fields=["estado"])

        self.assertEqual(self._buscar(), [])

    def test_marca_igual_una_deuda_ya_pagada(self):
        """El duplicado tambien importa si la primera ya se pago: es justamente
        el caso que termino en pagar dos veces la misma factura."""
        pagada = self._deuda()
        pagada.estado = CuentaPorPagar.Estado.PAGADA
        pagada.saldo_pendiente = Decimal("0.00")
        pagada.save(update_fields=["estado", "saldo_pendiente"])

        self.assertEqual(self._buscar(), [pagada])

    def test_puede_excluirse_a_si_misma(self):
        propia = self._deuda()

        self.assertEqual(self._buscar(excluir_pk=propia.pk), [])

    def test_una_deuda_sin_sucursal_solo_se_parece_a_otra_sin_sucursal(self):
        sin_sucursal = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="legacy",
            fecha_emision=self.fecha_op,
            fecha_vencimiento=self.fecha_op,
            periodo_referencia=self.fecha_op.replace(day=1),
            importe_total=Decimal("120000.00"),
            saldo_pendiente=Decimal("120000.00"),
            sucursal=None,
            creado_por=self.admin,
        )

        self.assertEqual(self._buscar(sucursal=self.suc_a), [])
        self.assertEqual(self._buscar(sucursal=None), [sin_sucursal])


class AvisoEnLaCargaDeDeudaTests(AvisoDuplicadoFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()
        self.url = reverse("cashops:box_expense_debt", args=[self.caja.pk])

    def _payload(self, **extra):
        datos = {
            "proveedor": self.proveedor.pk,
            "rubro": self.rubro.pk,
            "monto": "120000.00",
            "concepto": "frutas y verduras",
            "fecha_factura": self.fecha_op.isoformat(),
            "referencia_comprobante": "",
            "observacion": "",
        }
        datos.update(extra)
        return datos

    def test_sin_duplicado_guarda_directo(self):
        response = self.client.post(self.url, self._payload())

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CuentaPorPagar.objects.count(), 1)

    def test_con_duplicado_avisa_y_no_guarda_todavia(self):
        ya_cargada = self._deuda()

        response = self.client.post(self.url, self._payload())

        self.assertEqual(response.status_code, 200)
        # No se grabo nada: sigue estando solo la original.
        self.assertEqual(CuentaPorPagar.objects.count(), 1)
        self.assertContains(response, "Ojo: ya hay una deuda igual")
        self.assertContains(response, f"#{ya_cargada.pk}")
        # El aviso trae la caja de origen, que es lo que permite reconocerla.
        self.assertContains(response, f"Caja #{self.caja.pk}")
        self.assertContains(response, "Guardar de todos modos")

    def test_el_segundo_envio_guarda_igual(self):
        self._deuda()

        primera = self.client.post(self.url, self._payload())
        self.assertEqual(CuentaPorPagar.objects.count(), 1)
        # El formulario vuelve con el flag puesto; el usuario aprieta de nuevo.
        self.assertContains(primera, 'name="confirmar_duplicado"')

        segunda = self.client.post(self.url, self._payload(confirmar_duplicado="1"))

        self.assertEqual(segunda.status_code, 302)
        self.assertEqual(CuentaPorPagar.objects.count(), 2)

    def test_el_aviso_mira_la_sucursal_destino_y_no_la_de_la_caja(self):
        """Si el usuario imputa la deuda a otra sucursal habilitada, el aviso
        tiene que comparar contra ESA, no contra la sucursal de la caja."""
        self.admin.sucursales_deuda.set([self.suc_a, self.suc_b])
        self._deuda(sucursal=self.suc_b)

        response = self.client.post(
            self.url, self._payload(sucursal=self.suc_b.pk)
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ojo: ya hay una deuda igual")
        self.assertEqual(CuentaPorPagar.objects.count(), 1)

    def test_dos_deudas_iguales_avisan_en_plural(self):
        self._deuda(concepto="frutas uno")
        register_box_expense_debt(
            caja=self.caja,
            proveedor=self.proveedor,
            categoria=self.categoria,
            monto=Decimal("120000.00"),
            concepto="frutas dos",
            fecha_factura=self.fecha_op,
            actor=self.admin,
        )

        response = self.client.post(self.url, self._payload())

        self.assertContains(response, "ya hay 2 deudas iguales")

    def test_un_comprobante_distinto_no_dispara_el_aviso(self):
        """Si la ya cargada tiene otro numero de comprobante, es otra factura."""
        ya_cargada = self._deuda()
        ya_cargada.referencia_comprobante = "F-0001"
        ya_cargada.save(update_fields=["referencia_comprobante"])

        response = self.client.post(
            self.url, self._payload(referencia_comprobante="F-0002")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CuentaPorPagar.objects.count(), 2)
