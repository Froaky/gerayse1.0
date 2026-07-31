from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from treasury.models import (
    CajaCentral, MovimientoCajaCentral, CierreMensualTesoreria,
    CuentaPorPagar, Proveedor, CategoriaCuentaPagar, PagoTesoreria,
    CuentaBancaria, MovimientoBancario
)
from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.services import (
    register_cash_payment, register_central_cash_movement,
    build_disponibilidades_snapshot, close_treasury_month,
    register_arqueo, get_boveda,
    register_egreso_tesoreria,
)

User = get_user_model()

class EP05DisponibilidadesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="admin", password="password", email="admin@test.com")
        # Toda escritura de efectivo necesita empresa: la boveda es una por
        # empresa y no existe mas la caja global sin dueno.
        self.empresa = Empresa.objects.create(nombre="Empresa EP05")
        self.sucursal = Sucursal.objects.create(
            codigo="EP05",
            nombre="Sucursal EP05",
            razon_social="Empresa EP05",
            empresa=self.empresa,
        )
        self.supplier = Proveedor.objects.create(razon_social="Test Supplier", creado_por=self.user)
        self.category = CategoriaCuentaPagar.objects.create(nombre="Test Category", creado_por=self.user)
        self.bank_account = CuentaBancaria.objects.create(
            nombre="Banco Test", banco="Galicia", tipo_cuenta="CC", numero_cuenta="123", creado_por=self.user
        )

    def test_get_boveda_es_una_sola_por_empresa(self):
        caja = get_boveda(self.empresa)
        self.assertEqual(caja.empresa_id, self.empresa.pk)
        self.assertIsNone(caja.sucursal_id)
        # Pedirla de nuevo devuelve la misma: no se crea una segunda. Antes esto
        # era un get_or_create por nombre y convivia con otro resolvedor que
        # creaba una caja por sucursal; entre los dos dejaron 7 cajas en produccion.
        self.assertEqual(get_boveda(self.empresa).pk, caja.pk)
        self.assertEqual(CajaCentral.objects.filter(empresa=self.empresa, activo=True).count(), 1)

    def test_register_central_cash_movement(self):
        m = register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Aporte inicial",
            actor=self.user
        )
        self.assertEqual(m.monto, Decimal("1000.00"))
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("1000.00"))

    def test_register_cash_payment_triggers_central_movement(self):
        payable = CuentaPorPagar.objects.create(
            proveedor=self.supplier,
            categoria=self.category,
            concepto="Compra",
            fecha_emision=timezone.localdate(),
            fecha_vencimiento=timezone.localdate(),
            periodo_referencia=timezone.localdate().replace(day=1),
            importe_total=Decimal("500.00"),
            saldo_pendiente=Decimal("500.00"),
            # La deuda ahora necesita sucursal: de ahi se deduce de que boveda
            # sale el efectivo cuando se la paga en efectivo.
            sucursal=self.sucursal,
            creado_por=self.user
        )

        # Add some initial cash
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            actor=self.user
        )

        payment = register_cash_payment(
            payable=payable,
            fecha_pago=timezone.localdate(),
            monto=Decimal("500.00"),
            actor=self.user
        )

        self.assertEqual(payment.medio_pago, PagoTesoreria.MedioPago.EFECTIVO)
        self.assertEqual(get_boveda(self.empresa).saldo_actual, Decimal("500.00"))

        # Check movement exists
        move = MovimientoCajaCentral.objects.filter(pago_tesoreria=payment).first()
        self.assertIsNotNone(move)
        self.assertEqual(move.tipo, MovimientoCajaCentral.Tipo.EGRESO_PAGO)

    def test_build_disponibilidades_snapshot(self):
        today = timezone.localdate()
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            fecha=today,
            actor=self.user
        )

        snapshot = build_disponibilidades_snapshot(today.year, today.month)
        self.assertEqual(snapshot["saldo_final_efectivo"], Decimal("1000.00"))
        self.assertEqual(snapshot["total_consolidado"], Decimal("1000.00"))

    def test_snapshot_with_company_context_includes_global_central_cash_and_admin_expenses(self):
        empresa = Empresa.objects.create(nombre="Empresa Disponibilidades")
        sucursal = Sucursal.objects.create(
            codigo="DISP",
            nombre="Sucursal Disponibilidades",
            razon_social="Empresa Disponibilidades",
            empresa=empresa,
        )
        self.bank_account.sucursal = sucursal
        self.bank_account.save(update_fields=["sucursal"])
        period_day = timezone.datetime(2026, 6, 10).date()
        register_central_cash_movement(
            empresa=empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("700.00"),
            concepto="Saldo inicial junio",
            fecha=period_day,
            actor=self.user,
        )
        register_central_cash_movement(
            empresa=empresa,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("150.00"),
            concepto="Gasto administrativo",
            fecha=period_day,
            actor=self.user,
        )
        MovimientoBancario.objects.create(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.CREDITO,
            clase=MovimientoBancario.Clase.OTRO_INGRESO,
            fecha=period_day,
            monto=Decimal("300.00"),
            concepto="Ingreso banco",
            creado_por=self.user,
        )
        MovimientoBancario.objects.create(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            fecha=period_day,
            monto=Decimal("40.00"),
            concepto="Egreso banco",
            creado_por=self.user,
        )

        snapshot = build_disponibilidades_snapshot(2026, 6, empresa_ids=[empresa.pk])

        self.assertEqual(snapshot["cash_in"], Decimal("700.00"))
        self.assertEqual(snapshot["cash_out"], Decimal("150.00"))
        self.assertEqual(snapshot["saldo_final_efectivo"], Decimal("550.00"))
        self.assertEqual(snapshot["total_bancos_final"], Decimal("260.00"))
        self.assertEqual(snapshot["total_consolidado"], Decimal("810.00"))

    def test_el_efectivo_de_una_empresa_no_se_cuenta_en_la_otra(self):
        """Regresion del doble conteo que se midio en produccion.

        Habia una clausula del filtro por empresa que matcheaba cualquier
        movimiento de la caja global sin sucursal para CUALQUIER empresa. Con eso
        los mismos $21.799.835 se contaban enteros en ARMADI y enteros en MAPOGO,
        y la suma de los informes por empresa daba mas que el consolidado real.
        """
        otra = Empresa.objects.create(nombre="Empresa Sin Efectivo")
        period_day = timezone.datetime(2026, 6, 10).date()
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO,
            monto=Decimal("21799835.00"),
            concepto="Carga inicial sin sucursal",
            fecha=period_day,
            actor=self.user,
        )

        propio = build_disponibilidades_snapshot(2026, 6, empresa_ids=[self.empresa.pk])
        ajeno = build_disponibilidades_snapshot(2026, 6, empresa_ids=[otra.pk])
        consolidado = build_disponibilidades_snapshot(2026, 6)

        self.assertEqual(propio["cash_in"], Decimal("21799835.00"))
        self.assertEqual(ajeno["cash_in"], Decimal("0.00"))
        # La suma por empresa tiene que dar exactamente el consolidado: ni de mas
        # (doble conteo) ni de menos (movimiento que se pierde).
        self.assertEqual(
            propio["cash_in"] + ajeno["cash_in"],
            consolidado["cash_in"],
        )

    def test_snapshot_branch_scope_uses_admin_expense_imputation_without_bank_movements(self):
        empresa_a = Empresa.objects.create(nombre="Empresa EP05 A")
        empresa_b = Empresa.objects.create(nombre="Empresa EP05 B")
        sucursal_a = Sucursal.objects.create(
            codigo="E5A",
            nombre="Sucursal EP05 A",
            razon_social="Empresa EP05 A",
            empresa=empresa_a,
        )
        sucursal_b = Sucursal.objects.create(
            codigo="E5B",
            nombre="Sucursal EP05 B",
            razon_social="Empresa EP05 B",
            empresa=empresa_b,
        )
        rubro = RubroOperativo.objects.create(nombre="Almacen")
        period_day = timezone.datetime(2026, 6, 12).date()
        register_central_cash_movement(
            empresa=empresa_a,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Aporte sucursal",
            fecha=period_day,
            actor=self.user,
        )
        register_egreso_tesoreria(
            empresa=empresa_a,
            fuente="CAJA_CENTRAL",
            fecha=period_day,
            monto=Decimal("250.00"),
            concepto="Gasto imputado A",
            rubro=rubro,
            sucursal=sucursal_a,
            periodo=period_day.replace(day=1),
            actor=self.user,
        )
        register_egreso_tesoreria(
            empresa=empresa_b,
            fuente="CAJA_CENTRAL",
            fecha=period_day,
            monto=Decimal("90.00"),
            concepto="Gasto imputado B",
            rubro=rubro,
            sucursal=sucursal_b,
            periodo=period_day.replace(day=1),
            actor=self.user,
        )
        MovimientoBancario.objects.create(
            cuenta_bancaria=self.bank_account,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            fecha=period_day,
            monto=Decimal("80.00"),
            concepto="Egreso banco fuera libro efectivo",
            creado_por=self.user,
        )

        snapshot = build_disponibilidades_snapshot(2026, 6, sucursal=sucursal_a, empresa_ids=[empresa_a.pk])

        # El APORTE no es de ninguna sucursal (no tiene sucursal_origen), asi que
        # queda fuera del alcance por sucursal: es plata de la empresa.
        self.assertEqual(snapshot["cash_in"], Decimal("0.00"))
        self.assertEqual(snapshot["cash_out"], Decimal("250.00"))
        self.assertEqual(snapshot["saldo_final_efectivo"], Decimal("-250.00"))

    def test_egreso_no_puede_imputarse_a_sucursal_de_otra_empresa(self):
        """Un gasto de una empresa no se puede cargar contra el local de la otra."""
        from django.core.exceptions import ValidationError

        otra = Empresa.objects.create(nombre="Empresa Ajena")
        sucursal_ajena = Sucursal.objects.create(
            codigo="AJEN",
            nombre="Sucursal Ajena",
            razon_social="Empresa Ajena",
            empresa=otra,
        )
        rubro = RubroOperativo.objects.create(nombre="Servicios")
        with self.assertRaises(ValidationError):
            register_egreso_tesoreria(
                empresa=self.empresa,
                fuente="CAJA_CENTRAL",
                fecha=timezone.localdate(),
                monto=Decimal("100.00"),
                concepto="Gasto cruzado",
                rubro=rubro,
                sucursal=sucursal_ajena,
                periodo=timezone.localdate().replace(day=1),
                actor=self.user,
            )

    def test_close_treasury_month(self):
        today = timezone.localdate()
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            fecha=today,
            actor=self.user
        )

        closing = close_treasury_month(today.year, today.month, actor=self.user)
        self.assertTrue(closing.cerrado)
        self.assertEqual(closing.saldo_final_efectivo, Decimal("1000.00"))

        # Ensure it can't be closed twice
        with self.assertRaises(Exception):
            close_treasury_month(today.year, today.month, actor=self.user)

    def test_arqueo_calculates_difference(self):
        register_central_cash_movement(
            empresa=self.empresa,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Initial",
            actor=self.user
        )
        caja = get_boveda(self.empresa)

        arqueo = register_arqueo(
            caja_central=caja,
            saldo_contado=Decimal("950.00"),
            actor=self.user
        )

        self.assertEqual(arqueo.saldo_sistema_efectivo, Decimal("1000.00"))
        self.assertEqual(arqueo.diferencia, Decimal("-50.00"))
