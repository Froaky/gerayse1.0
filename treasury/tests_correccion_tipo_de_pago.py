"""US-4.11: corregir con que instrumento se pago una deuda ya registrada.

Caso real de produccion: cargaron el egreso como transferencia y era un cheque.
El detalle del movimiento esconde "Editar" apenas queda vinculado a un pago (con
razon: editar de verdad cambia monto, fecha y cuenta), asi que la unica salida
era anular los pagos y volver a cargar todo desde cero.

La correccion toca SOLO la tipificacion. Lo que estos tests cuidan es justamente
lo que NO se puede mover: importe, fecha, cuenta, deudas pagadas y saldos.
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
    annul_bank_movement,
    correct_bank_payment_method,
    create_bank_movement,
    pay_debts_from_bank_movement,
    register_payment,
)

User = get_user_model()


class CorreccionFixture(TestCase):
    """Solo fixtures: los tests viven en las dos clases de abajo."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-ctp", password="test", email="ctp@test.com"
        )
        self.empresa = Empresa.objects.create(nombre="Empresa CTP")
        self.sucursal = Sucursal.objects.create(
            codigo="CTP", nombre="Suc CTP", razon_social="CTP", empresa=self.empresa
        )
        self.rubro = RubroOperativo.objects.create(nombre="Rubro CTP")
        self.admin.empresas_permitidas.set([self.empresa])
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta CTP",
            banco="Banco CTP",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="777-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.proveedor = Proveedor.objects.create(
            razon_social="Brunetti CTP", creado_por=self.admin
        )
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Categoria CTP", rubro_operativo=self.rubro, creado_por=self.admin
        )
        self.hoy = timezone.localdate()

    def _factura(self, concepto: str, importe: str = "1000.00", proveedor=None):
        return CuentaPorPagar.objects.create(
            proveedor=proveedor or self.proveedor,
            categoria=self.categoria,
            concepto=concepto,
            fecha_emision=self.hoy,
            fecha_vencimiento=self.hoy,
            periodo_referencia=self.hoy.replace(day=1),
            importe_total=Decimal(importe),
            saldo_pendiente=Decimal(importe),
            sucursal=self.sucursal,
            creado_por=self.admin,
        )

    def _transferencia(self, monto="3000.00", referencia=""):
        return create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal(monto),
            concepto="Pago semanal cuenta corriente",
            clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
            proveedor=self.proveedor,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=self.hoy.replace(day=1),
            referencia=referencia,
            actor=self.admin,
        )

    def _transferencia_con_tres_facturas(self):
        """El escenario que reporto la usuaria: una transferencia que paga tres
        facturas del mismo proveedor y deja las tres deudas canceladas."""
        movimiento = self._transferencia("3000.00")
        facturas = [self._factura(f"Factura 00{i}") for i in (1, 2, 3)]
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[(factura, Decimal("1000.00")) for factura in facturas],
            actor=self.admin,
        )
        movimiento.refresh_from_db()
        return movimiento, facturas


class CorregirTipoDePagoTests(CorreccionFixture):
    def test_corrige_transferencia_a_cheque_en_el_movimiento_y_en_los_pagos(self):
        movimiento, facturas = self._transferencia_con_tres_facturas()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)

        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.CHEQUE,
            referencia="CH-1001",
            actor=self.admin,
        )

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.CHEQUE)
        self.assertEqual(movimiento.referencia, "CH-1001")
        self.assertEqual(movimiento.actualizado_por, self.admin)
        pagos = list(movimiento.pagos.order_by("pk"))
        self.assertEqual(len(pagos), 3)
        for pago in pagos:
            self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.CHEQUE)
        # PagoTesoreria tiene unicidad por (cuenta, medio, referencia): el mismo
        # numero de cheque en tres facturas se sufija por linea, igual que en el
        # pago por proveedor.
        self.assertEqual(
            [pago.referencia for pago in pagos],
            ["CH-1001 (1/3)", "CH-1001 (2/3)", "CH-1001 (3/3)"],
        )
        # Y queda dicho en el movimiento que la tipificacion se corrigio.
        self.assertIn("Tipo financiero corregido", movimiento.observaciones)

    def test_la_correccion_no_mueve_ni_un_peso(self):
        movimiento, facturas = self._transferencia_con_tres_facturas()
        antes = {
            "monto": movimiento.monto,
            "fecha": movimiento.fecha,
            "cuenta": movimiento.cuenta_bancaria_id,
            "tipo": movimiento.tipo,
            "estado": movimiento.estado,
        }
        montos_de_pago = sorted(movimiento.pagos.values_list("monto", flat=True))

        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.ECHEQ,
            referencia="ECHEQ-55",
            actor=self.admin,
        )

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.monto, antes["monto"])
        self.assertEqual(movimiento.fecha, antes["fecha"])
        self.assertEqual(movimiento.cuenta_bancaria_id, antes["cuenta"])
        self.assertEqual(movimiento.tipo, antes["tipo"])
        self.assertEqual(movimiento.estado, antes["estado"])
        self.assertEqual(
            sorted(movimiento.pagos.values_list("monto", flat=True)), montos_de_pago
        )
        for factura in facturas:
            factura.refresh_from_db()
            self.assertEqual(factura.estado, CuentaPorPagar.Estado.PAGADA)
            self.assertEqual(factura.saldo_pendiente, Decimal("0.00"))

    def test_se_puede_corregir_aunque_la_deuda_ya_quedo_cancelada(self):
        """Regresion del bloqueo real: el clean de PagoTesoreria rechazaba
        cualquier re-guardado cuando la deuda quedaba PAGADA ("La cuenta por
        pagar ya esta cancelada"), que es justo el caso normal de un pago total.
        Esa guarda ahora aplica solo al alta de un pago nuevo."""
        movimiento = self._transferencia("1000.00")
        factura = self._factura("Factura total")
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[(factura, Decimal("1000.00"))],
            actor=self.admin,
        )
        factura.refresh_from_db()
        self.assertEqual(factura.estado, CuentaPorPagar.Estado.PAGADA)

        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.CHEQUE,
            referencia="CH-UNICO",
            actor=self.admin,
        )

        pago = movimiento.pagos.get()
        self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.CHEQUE)
        # Sin sufijo: una sola factura, un solo cheque.
        self.assertEqual(pago.referencia, "CH-UNICO")

    def test_cheque_sin_referencia_no_pasa(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()

        with self.assertRaises(ValidationError) as ctx:
            correct_bank_payment_method(
                bank_movement=movimiento,
                medio_pago=PagoTesoreria.MedioPago.CHEQUE,
                referencia="   ",
                actor=self.admin,
            )

        self.assertIn("referencia", ctx.exception.message_dict)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)

    def test_una_transferencia_a_varios_proveedores_no_puede_ser_un_cheque(self):
        """Un cheque tiene un solo beneficiario. La transferencia repartida entre
        proveedores distintos no tiene proveedor unico y no se puede re-tipificar
        asi; el rechazo no deja nada a medio cambiar."""
        movimiento = create_bank_movement(
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal("2000.00"),
            concepto="Pago semanal a varios",
            # Sin proveedor todavia: un debito manual no puede nacer como
            # "transferencia a terceros" sin beneficiario. Al repartirlo entre
            # facturas de proveedores distintos, la vinculacion lo deja como
            # transferencia justamente sin proveedor unico.
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            rubro_operativo=self.rubro,
            sucursal_gasto=self.sucursal,
            periodo_pago=self.hoy.replace(day=1),
            actor=self.admin,
        )
        otro = Proveedor.objects.create(razon_social="Otro CTP", creado_por=self.admin)
        una = self._factura("Factura A", "1000.00")
        otra = self._factura("Factura B", "1000.00", proveedor=otro)
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[(una, Decimal("1000.00")), (otra, Decimal("1000.00"))],
            actor=self.admin,
        )

        with self.assertRaises(ValidationError) as ctx:
            correct_bank_payment_method(
                bank_movement=movimiento,
                medio_pago=PagoTesoreria.MedioPago.CHEQUE,
                referencia="CH-IMPOSIBLE",
                actor=self.admin,
            )

        self.assertIn("un solo beneficiario", str(ctx.exception))
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)
        self.assertEqual(
            set(movimiento.pagos.values_list("medio_pago", flat=True)),
            {PagoTesoreria.MedioPago.TRANSFERENCIA},
        )

    def test_volver_de_cheque_a_transferencia_limpia_la_fecha_diferida(self):
        movimiento = self._transferencia("1000.00")
        factura = self._factura("Factura diferida")
        pay_debts_from_bank_movement(
            bank_movement=movimiento,
            asignaciones=[(factura, Decimal("1000.00"))],
            actor=self.admin,
        )
        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.CHEQUE,
            referencia="CH-DIFERIDO",
            actor=self.admin,
        )
        pago = movimiento.pagos.get()
        pago.fecha_diferida = self.hoy + timezone.timedelta(days=30)
        pago.save(skip_domain_guard=True)

        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            referencia="TRF-9",
            actor=self.admin,
        )

        pago.refresh_from_db()
        self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.TRANSFERENCIA)
        self.assertIsNone(pago.fecha_diferida)

    def test_si_no_se_toca_la_referencia_el_pago_conserva_la_suya(self):
        """Un pago cargado a mano y despues vinculado puede tener referencia
        propia, distinta de la del movimiento. Corregir solo el tipo financiero
        no debe pisarsela."""
        movimiento = self._transferencia("400.00", referencia="TRF-77")
        factura = self._factura("Factura vinculada a mano")
        pago = register_payment(
            payable=factura,
            bank_account=self.cuenta,
            medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
            fecha_pago=self.hoy,
            monto=Decimal("400.00"),
            referencia="PAGO-99",
            bank_movement=movimiento,
            actor=self.admin,
        )

        correct_bank_payment_method(
            bank_movement=movimiento,
            medio_pago=PagoTesoreria.MedioPago.ECHEQ,
            referencia="TRF-77",
            actor=self.admin,
        )

        pago.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(pago.medio_pago, PagoTesoreria.MedioPago.ECHEQ)
        self.assertEqual(pago.referencia, "PAGO-99")
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.ECHEQ)
        self.assertEqual(movimiento.referencia, "TRF-77")

    def test_un_movimiento_sin_pagos_no_se_corrige_por_aca(self):
        movimiento = self._transferencia("1000.00")

        with self.assertRaises(ValidationError) as ctx:
            correct_bank_payment_method(
                bank_movement=movimiento,
                medio_pago=PagoTesoreria.MedioPago.CHEQUE,
                referencia="CH-1",
                actor=self.admin,
            )

        self.assertIn("no paga ninguna factura", str(ctx.exception))

    def test_un_movimiento_eliminado_no_se_corrige(self):
        movimiento = self._transferencia("1000.00")
        annul_bank_movement(
            movement=movimiento, motivo="Cargado por error", actor=self.admin
        )

        with self.assertRaises(ValidationError):
            correct_bank_payment_method(
                bank_movement=movimiento,
                medio_pago=PagoTesoreria.MedioPago.CHEQUE,
                referencia="CH-1",
                actor=self.admin,
            )

    def test_el_efectivo_no_es_un_medio_bancario(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()

        with self.assertRaises(ValidationError) as ctx:
            correct_bank_payment_method(
                bank_movement=movimiento,
                medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
                referencia="",
                actor=self.admin,
            )

        self.assertIn("medio_pago", ctx.exception.message_dict)


class CorregirTipoDePagoVistaTests(CorreccionFixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.empresa.pk]
        sesion.save()

    def test_el_detalle_del_movimiento_ofrece_corregir_el_tipo_de_pago(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()
        url = reverse("treasury:bank_movements_correct_method", args=[movimiento.pk])

        response = self.client.get(
            reverse("treasury:bank_movements_detail", args=[movimiento.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Corregir tipo de pago")
        self.assertContains(response, url)
        # El egreso vinculado sigue sin poder editarse de verdad.
        self.assertNotContains(
            response, reverse("treasury:bank_movements_edit_confirm", args=[movimiento.pk])
        )

    def test_el_detalle_del_pago_abre_y_ofrece_la_correccion(self):
        """Regresion: la URL declaraba <int:pk> y la vista esperaba payment_id,
        asi que el detalle de cualquier pago reventaba con TypeError."""
        movimiento, _facturas = self._transferencia_con_tres_facturas()
        pago = movimiento.pagos.order_by("pk").first()

        response = self.client.get(reverse("treasury:pagos_detail", args=[pago.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("treasury:bank_movements_correct_method", args=[movimiento.pk]),
        )

    def test_el_formulario_ofrece_los_tres_instrumentos_con_el_texto_de_la_pantalla(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()

        response = self.client.get(
            reverse("treasury:bank_movements_correct_method", args=[movimiento.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tipo financiero")
        for etiqueta in (
            "Egreso por cheque",
            "Egreso por ECHEQ",
            "Egreso por transferencia a terceros",
        ):
            self.assertContains(response, etiqueta)
        # Y avisa que la plata no se toca.
        self.assertContains(response, "No cambia el importe")

    def test_el_post_corrige_y_vuelve_al_detalle(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()
        url = reverse("treasury:bank_movements_correct_method", args=[movimiento.pk])

        response = self.client.post(
            url,
            {"medio_pago": PagoTesoreria.MedioPago.CHEQUE, "referencia": "CH-2002"},
        )

        self.assertRedirects(
            response, reverse("treasury:bank_movements_detail", args=[movimiento.pk])
        )
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.CHEQUE)
        self.assertEqual(
            set(movimiento.pagos.values_list("medio_pago", flat=True)),
            {PagoTesoreria.MedioPago.CHEQUE},
        )

    def test_cheque_sin_referencia_vuelve_al_formulario_con_error(self):
        movimiento, _facturas = self._transferencia_con_tres_facturas()
        url = reverse("treasury:bank_movements_correct_method", args=[movimiento.pk])

        response = self.client.post(
            url, {"medio_pago": PagoTesoreria.MedioPago.CHEQUE, "referencia": ""}
        )

        self.assertEqual(response.status_code, 400)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.clase, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)

    def test_un_movimiento_de_otra_empresa_no_se_corrige_por_url(self):
        otra_empresa = Empresa.objects.create(nombre="Empresa ajena CTP")
        otra_cuenta = CuentaBancaria.objects.create(
            nombre="Cuenta ajena",
            banco="Banco ajeno",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="888-1",
            empresa=otra_empresa,
            creado_por=self.admin,
        )
        otra_sucursal = Sucursal.objects.create(
            codigo="AJE", nombre="Suc ajena", razon_social="AJE", empresa=otra_empresa
        )
        ajeno = create_bank_movement(
            cuenta_bancaria=otra_cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            fecha=self.hoy,
            monto=Decimal("500.00"),
            concepto="Egreso de la otra empresa",
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            rubro_operativo=self.rubro,
            sucursal_gasto=otra_sucursal,
            periodo_pago=self.hoy.replace(day=1),
            actor=self.admin,
        )

        response = self.client.get(
            reverse("treasury:bank_movements_correct_method", args=[ajeno.pk])
        )

        self.assertEqual(response.status_code, 404)
