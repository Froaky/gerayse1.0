"""Pagar deudas desde una transferencia que ya esta en el extracto.

Antes habia que cargar el pago a mano y despues vincularlo al movimiento. Ahora
se eligen las facturas y los pagos se generan solos, sin crear un segundo debito:
la transferencia sigue siendo un unico hecho del extracto.

US-4.10: una transferencia se puede repartir entre varias facturas, incluso de
proveedores distintos. El limite es su importe: la suma de los pagos vinculados
no puede pasarlo.
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
from treasury.services import (
    annul_payment,
    create_bank_movement,
    importe_sin_asignar_del_movimiento,
    pay_debt_from_bank_movement,
    pay_debts_from_bank_movement,
    update_bank_movement,
)

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
        self.assertEqual(movimiento.pagos.get().pk, pago.pk)
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

    def _empresa_ajena_con_factura(self):
        """Otra empresa, con su propia sucursal y su propia factura impaga."""
        otra_empresa = Empresa.objects.create(nombre="Empresa Ajena SA")
        otra_sucursal = Sucursal.objects.create(
            codigo="AJE", nombre="Suc Ajena", razon_social="Ajena", empresa=otra_empresa
        )
        factura_ajena = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura de la otra empresa",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("200.00"),
            saldo_pendiente=Decimal("200.00"),
            sucursal=otra_sucursal,
            creado_por=self.admin,
        )
        return otra_empresa, factura_ajena

    def test_no_se_paga_una_factura_de_otra_empresa_con_esta_cuenta(self):
        """ARMADI y MAPOGO son empresas distintas.

        La plata de la cuenta de una no puede pagar la factura de la otra: seria
        que una empresa le pague las deudas a la otra sin que quede registrado
        como tal.
        """
        _otra, factura_ajena = self._empresa_ajena_con_factura()
        movimiento = self._transferencia("500.00")

        with self.assertRaises(ValidationError) as capturado:
            pay_debt_from_bank_movement(
                bank_movement=movimiento,
                payable=factura_ajena,
                monto=Decimal("200.00"),
                actor=self.admin,
            )

        self.assertIn("otra empresa", " ".join(capturado.exception.messages))
        self.assertEqual(movimiento.pagos.count(), 0)
        factura_ajena.refresh_from_db()
        self.assertEqual(factura_ajena.saldo_pendiente, Decimal("200.00"))

    def test_una_factura_legacy_sin_sucursal_sigue_siendo_pagable(self):
        """No romper lo historico: si no se sabe de que empresa es, no se bloquea."""
        legacy = CuentaPorPagar.objects.create(
            proveedor=self.proveedor,
            categoria=self.categoria,
            concepto="Factura vieja sin sucursal",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("150.00"),
            saldo_pendiente=Decimal("150.00"),
            sucursal=None,
            creado_por=self.admin,
        )
        movimiento = self._transferencia("500.00")

        pago = pay_debt_from_bank_movement(
            bank_movement=movimiento,
            payable=legacy,
            monto=Decimal("150.00"),
            actor=self.admin,
        )

        self.assertEqual(pago.monto, Decimal("150.00"))
        legacy.refresh_from_db()
        self.assertEqual(legacy.saldo_pendiente, Decimal("0.00"))


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

    def _factura_chica(self):
        otro = Proveedor.objects.create(razon_social="Proveedor Chico", creado_por=self.admin)
        chica = CuentaPorPagar.objects.create(
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
        return otro, chica

    def test_se_listan_todos_los_proveedores_no_solo_los_que_cubren_el_importe(self):
        """US-4.10: lo pidio la administradora.

        Antes solo aparecian los proveedores con una factura de al menos el
        importe de la transferencia, porque una transferencia pagaba una sola
        factura entera. Ahora se reparte, asi que toda factura impaga sirve.
        """
        otro, _chica = self._factura_chica()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.proveedor.razon_social)
        self.assertContains(response, otro.razon_social)

    def test_el_filtro_por_proveedor_deja_solo_sus_facturas(self):
        otro, _chica = self._factura_chica()

        response = self.client.get(self.url, {"proveedor": otro.pk})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Factura chica")
        self.assertNotContains(response, "Factura 001")

    def test_el_post_paga_la_factura_marcada(self):
        pago = self.client.post(
            self.url,
            {"payable_id": [str(self.deuda.pk)], f"monto_{self.deuda.pk}": "400.00"},
        )

        self.assertEqual(pago.status_code, 302)
        self.deuda.refresh_from_db()
        self.movimiento.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("600.00"))
        self.assertTrue(self.movimiento.pagos.exists())

    def test_el_post_reparte_entre_dos_proveedores_en_una_sola_operacion(self):
        otro, chica = self._factura_chica()

        response = self.client.post(
            self.url,
            {
                "payable_id": [str(self.deuda.pk), str(chica.pk)],
                f"monto_{self.deuda.pk}": "300.00",
                f"monto_{chica.pk}": "100.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.deuda.refresh_from_db()
        chica.refresh_from_db()
        self.movimiento.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("700.00"))
        self.assertEqual(chica.saldo_pendiente, Decimal("0.00"))
        self.assertEqual(self.movimiento.pagos.count(), 2)
        # Un solo movimiento en el extracto para los dos pagos.
        self.assertEqual(
            MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO).count(), 1
        )

    def test_un_importe_que_no_es_numero_no_paga_nada(self):
        response = self.client.post(
            self.url,
            {"payable_id": [str(self.deuda.pk)], f"monto_{self.deuda.pk}": "cien pesos"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.movimiento.refresh_from_db()
        self.assertEqual(self.movimiento.pagos.count(), 0)
        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("1000.00"))

    def test_si_se_asigna_de_menos_avisa_cuanto_queda_sin_asignar(self):
        response = self.client.post(
            self.url,
            {"payable_id": [str(self.deuda.pk)], f"monto_{self.deuda.pk}": "150.00"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        mensajes = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("sin asignar", mensajes)
        self.movimiento.refresh_from_db()
        self.assertEqual(importe_sin_asignar_del_movimiento(self.movimiento), Decimal("250.00"))


class RepartirTransferenciaEntreFacturasTests(PagarDeudaDesdeTransferenciaTests):
    """US-4.10: una transferencia sola cubre varias facturas.

    El caso real: el pago semanal de cuenta corriente sale en un solo monto y
    cubre facturas de proveedores distintos. Lo que se cuida es que la suma de los
    pagos nunca pase el importe de la transferencia (seria sacar del banco mas
    plata de la que salio) y que anular uno no desarme los otros.
    """

    def setUp(self):
        super().setUp()
        self.otro_proveedor = Proveedor.objects.create(
            razon_social="Proveedor Dos", creado_por=self.admin
        )
        self.deuda_otro = CuentaPorPagar.objects.create(
            proveedor=self.otro_proveedor,
            categoria=self.categoria,
            concepto="Factura del otro proveedor",
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal("300.00"),
            saldo_pendiente=Decimal("300.00"),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )

    def test_reparte_una_transferencia_entre_facturas_de_proveedores_distintos(self):
        movimiento = self._transferencia("500.00")
        debitos_antes = MovimientoBancario.objects.filter(
            tipo=MovimientoBancario.Tipo.DEBITO
        ).count()

        pagos = pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[
                (self.deuda, Decimal("200.00")),
                (self.deuda_otro, Decimal("300.00")),
            ],
            actor=self.admin,
        )

        self.assertEqual(len(pagos), 2)
        self.deuda.refresh_from_db()
        self.deuda_otro.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("800.00"))
        self.assertEqual(self.deuda_otro.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(movimiento.pagos.count(), 2)
        # Un solo hecho del extracto: no aparecen debitos nuevos.
        self.assertEqual(
            MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO).count(),
            debitos_antes,
        )
        self.assertEqual(importe_sin_asignar_del_movimiento(movimiento), Decimal("0.00"))

    def test_con_proveedores_distintos_el_movimiento_no_se_queda_con_uno_solo(self):
        movimiento = self._transferencia("500.00")

        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[
                (self.deuda, Decimal("200.00")),
                (self.deuda_otro, Decimal("300.00")),
            ],
            actor=self.admin,
        )

        movimiento.refresh_from_db()
        # Poner el proveedor de la primera factura seria mentir: la transferencia
        # pago a dos. Los proveedores se leen de los pagos.
        self.assertIsNone(movimiento.proveedor_id)
        self.assertEqual(
            {p.cuenta_por_pagar.proveedor_id for p in movimiento.pagos.all()},
            {self.proveedor.pk, self.otro_proveedor.pk},
        )

    def test_no_se_puede_repartir_mas_que_el_importe_de_la_transferencia(self):
        movimiento = self._transferencia("400.00")

        with self.assertRaises(ValidationError) as capturado:
            pay_debts_from_bank_movement(
                bank_movement=movimiento,
                asignaciones=[
                    (self.deuda, Decimal("200.00")),
                    (self.deuda_otro, Decimal("300.00")),
                ],
                actor=self.admin,
            )

        self.assertIn("sin asignar", " ".join(capturado.exception.messages))
        # Todo o nada: no quedo ningun pago a medio hacer.
        self.assertEqual(movimiento.pagos.count(), 0)
        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("1000.00"))

    def test_no_se_puede_asignar_a_una_factura_mas_que_su_saldo(self):
        movimiento = self._transferencia("1000.00")

        with self.assertRaises(ValidationError) as capturado:
            pay_debts_from_bank_movement(
                bank_movement=movimiento,
                asignaciones=[(self.deuda_otro, Decimal("500.00"))],
                actor=self.admin,
            )

        self.assertIn("le quedan", " ".join(capturado.exception.messages))
        self.assertEqual(movimiento.pagos.count(), 0)

    def test_no_se_puede_elegir_dos_veces_la_misma_factura(self):
        movimiento = self._transferencia("500.00")

        with self.assertRaises(ValidationError) as capturado:
            pay_debts_from_bank_movement(
                bank_movement=movimiento,
                asignaciones=[
                    (self.deuda, Decimal("100.00")),
                    (self.deuda, Decimal("100.00")),
                ],
                actor=self.admin,
            )

        self.assertIn("dos veces la misma factura", " ".join(capturado.exception.messages))
        self.assertEqual(movimiento.pagos.count(), 0)

    def test_lo_que_sobra_queda_sin_asignar_y_se_puede_usar_despues(self):
        movimiento = self._transferencia("500.00")

        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[(self.deuda, Decimal("200.00"))],
            actor=self.admin,
        )
        movimiento.refresh_from_db()
        self.assertEqual(importe_sin_asignar_del_movimiento(movimiento), Decimal("300.00"))

        # Y esos $300 se pueden asignar en un segundo momento.
        pay_debt_from_bank_movement(
            bank_movement=movimiento,
            payable=self.deuda_otro,
            monto=Decimal("300.00"),
            actor=self.admin,
        )
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.pagos.count(), 2)
        self.assertEqual(importe_sin_asignar_del_movimiento(movimiento), Decimal("0.00"))

    def test_una_transferencia_ya_repartida_no_acepta_un_peso_mas(self):
        movimiento = self._transferencia("500.00")
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[
                (self.deuda, Decimal("200.00")),
                (self.deuda_otro, Decimal("300.00")),
            ],
            actor=self.admin,
        )
        movimiento.refresh_from_db()

        with self.assertRaises(ValidationError) as capturado:
            pay_debt_from_bank_movement(
                bank_movement=movimiento,
                payable=self.deuda,
                monto=Decimal("50.00"),
                actor=self.admin,
            )

        self.assertIn("ya esta asignada", " ".join(capturado.exception.messages))

    def test_anular_un_pago_libera_su_importe_y_no_toca_los_otros(self):
        movimiento = self._transferencia("500.00")
        primero, segundo = pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[
                (self.deuda, Decimal("200.00")),
                (self.deuda_otro, Decimal("300.00")),
            ],
            actor=self.admin,
        )

        annul_payment(payment=primero, motivo="Se imputo mal la factura", actor=self.admin)

        movimiento.refresh_from_db()
        self.deuda.refresh_from_db()
        self.deuda_otro.refresh_from_db()
        # Vuelve el saldo a SU factura...
        self.assertEqual(self.deuda.saldo_pendiente, Decimal("1000.00"))
        # ...el otro pago sigue en pie...
        self.assertEqual(self.deuda_otro.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(movimiento.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 1)
        self.assertEqual(
            movimiento.pagos.get(estado=PagoTesoreria.Estado.REGISTRADO).pk, segundo.pk
        )
        # ...la transferencia sigue vigente en el extracto y con origen de pago...
        self.assertEqual(movimiento.estado, MovimientoBancario.Estado.REGISTRADO)
        self.assertEqual(movimiento.origen, MovimientoBancario.Origen.PAGO_TESORERIA)
        # ...y los $200 quedan libres para reasignar.
        self.assertEqual(importe_sin_asignar_del_movimiento(movimiento), Decimal("200.00"))

    def test_anular_el_ultimo_pago_devuelve_la_transferencia_a_manual(self):
        movimiento = self._transferencia("500.00")
        pago = pay_debt_from_bank_movement(
            bank_movement=movimiento,
            payable=self.deuda,
            monto=Decimal("500.00"),
            actor=self.admin,
        )

        annul_payment(payment=pago, motivo="Error de carga", actor=self.admin)

        movimiento.refresh_from_db()
        # Se cargo a mano, asi que esa plata SI salio del banco: sigue vigente,
        # pero vuelve a ser un movimiento manual sin pago.
        self.assertEqual(movimiento.estado, MovimientoBancario.Estado.REGISTRADO)
        self.assertEqual(movimiento.origen, MovimientoBancario.Origen.MANUAL)
        self.assertEqual(movimiento.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count(), 0)

    def test_un_movimiento_repartido_no_se_puede_editar_a_mano(self):
        movimiento = self._transferencia("500.00")
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[
                (self.deuda, Decimal("200.00")),
                (self.deuda_otro, Decimal("300.00")),
            ],
            actor=self.admin,
        )
        movimiento.refresh_from_db()

        with self.assertRaises(ValidationError):
            update_bank_movement(
                movement=movimiento,
                cuenta_bancaria=self.cuenta,
                tipo=MovimientoBancario.Tipo.DEBITO,
                fecha=self.hoy,
                monto=Decimal("500.00"),
                concepto="Cambiado a mano",
                actor=self.admin,
            )
