"""Pagar una deuda desde una transferencia que ya esta en el extracto.

Antes habia que cargar el pago a mano y despues vincularlo al movimiento. Ahora
se elige la factura y el pago se genera solo, por el importe exacto de la
transferencia, sin crear un segundo debito.

Una transferencia paga UNA factura: MovimientoBancario.pago_tesoreria es OneToOne
y la vinculacion exige que los importes coincidan.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
from treasury.services import create_bank_movement, pay_debt_from_bank_movement

User = get_user_model()


class PagarDeudaDesdeTransferenciaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-pdb", password="test", email="pdb@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Pago Desde Banco")
        self.sucursal = Sucursal.objects.create(
            codigo="PDB", nombre="Suc PDB", razon_social="PDB", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro PDB")
        self.admin.empresas_permitidas.set([self.empresa])
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta PDB",
            banco="Banco PDB",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="999-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.proveedor = Proveedor.objects.create(
            razon_social="Proveedor PDB", creado_por=self.admin
        )
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria PDB", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.hoy = timezone.localdate()
        self.deuda = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura 001",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("1000.00"),
            saldo_pendiente=Decimal("1000.00"),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )

    def _transferencia(self, monto="1000.00"):
        return create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal(monto),
            concepto="Transferencia al proveedor",
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=self.hoy.replace(day=1),
            actor=self.admin,
        )

    def test_paga_la_deuda_y_no_crea_un_segundo_debito(self):
        movimiento = self._transferencia("1000.00")
        debitos_antes = MovimientoBancario.objects.filter(
            tipo=MovimientoBancario.Tipo.DEBITO
        ).count()

        pago = pay_debt_from_bank_movement(
            bank_movement=movimiento, payable=self.deuda, actor=self.admin
        )

        self.deuda.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(self.deuda.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.TRANSFERENCIA)
        # Se vincula al movimiento que ya existia...
        self.assertEqual(movimiento.pago_tesoreria_id, pago.pk)
        self.assertEqual(movimiento.origen, MovimientoBancario.Origen.PAGO_TESORERIA)
        # ...y NO se genera otro debito: la plata salio del banco una sola vez.
        self.assertEqual(
            MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO).count(),
            debitos_antes,
        )

    def test_una_transferencia_mas_chica_deja_la_deuda_parcial(self):
        movimiento = self._transferencia("400.00")

        pay_debt_from_bank_movement(
            bank_movement=movimiento, payable=self.deuda, actor=self.admin
        )

        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.estado, CuentaPorPagar.Estado.PARCIAL)
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("600.00"))

    def test_no_paga_mas_de_lo_que_se_debe(self):
        movimiento = self._transferencia("1500.00")
        with self.assertRaises(ValidationError):
            pay_debt_from_bank_movement(
                bank_movement=movimiento, payable=self.deuda, actor=self.admin
            )
        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("1000.00"))

    def test_un_movimiento_ya_vinculado_no_se_puede_reusar(self):
        movimiento = self._transferencia("400.00")
        pay_debt_from_bank_movement(
            bank_movement=movimiento, payable=self.deuda, actor=self.admin
        )
        movimiento.refresh_from_db()
        with self.assertRaises(ValidationError):
            pay_debt_from_bank_movement(
                bank_movement=movimiento, payable=self.deuda, actor=self.admin
            )

    def test_un_credito_no_puede_pagar_una_deuda(self):
        credito = create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.CREDITO,
            fecha=self.hoy,
            monto=Decimal("1000.00"),
            concepto="Ingreso",
            actor=self.admin,
        )
        with self.assertRaises(ValidationError):
            pay_debt_from_bank_movement(
                bank_movement=credito, payable=self.deuda, actor=self.admin
            )


class PagarDeudaDesdeTransferenciaVistaTests(PagarDeudaDesdeTransferenciaTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()
        self.movimiento = self._transferencia("400.00")
        self.url = reverse(
            "treasury:bank_movements_pay_debt", args=[self.movimiento.pk]
        )

    def test_el_detalle_ofrece_pagar_una_deuda(self):
        response = self.client.get(
            reverse("treasury:bank_movements_detail", args=[self.movimiento.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pagar una deuda")
        self.assertContains(response, self.url)

    def test_el_paso_uno_ofrece_solo_proveedores_con_facturas_alcanzables(self):
        # Un proveedor con una factura mas chica que la transferencia no sirve.
        otro = Proveedor.objects.create(razon_social="Proveedor Chico", creado_por=self.admin)
        CuentaPorPagar.objects.create(
            proveedor=otro,
            categoria=self.categoria,
            concepto="Factura chica",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("100.00"),
            saldo_pendiente=Decimal("100.00"),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.proveedor.razon_social)
        self.assertNotContains(response, otro.razon_social)

    def test_el_paso_dos_lista_las_facturas_y_el_post_paga(self):
        listado = self.client.get(self.url, {"proveedor": self.proveedor.pk})
        self.assertEqual(listado.status_code, 200)
        self.assertContains(listado, "Factura 001")

        pago = self.client.post(
            f"{self.url}?proveedor={self.proveedor.pk}", {"payable_id": self.deuda.pk}
        )
        self.assertEqual(pago.status_code, 302)
        self.deuda.refresh_from_db()
        self.movimiento.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("600.00"))
        self.assertIsNotNone(self.movimiento.pago_tesoreria_id)
