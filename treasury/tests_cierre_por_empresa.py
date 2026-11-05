"""Cierre mensual de tesoreria por empresa.

Antes era una sola fila global. Con dos empresas eso daba dos problemas: ninguna
podia cerrar el mes hasta que la OTRA tuviera todas sus cajas validadas, y el
saldo inicial del mes siguiente mezclaba el efectivo de las dos.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from cashops.models import Caja, Empresa, Sucursal, Turno
from treasury.models import CierreMensualTesoreria, MovimientoCajaCentral
from treasury.services import (
    build_disponibilidades_snapshot,
    close_treasury_month,
    register_central_cash_movement,
)

User = get_user_model()


class CierrePorEmpresaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-cierre", password="test", email="c@test.com"
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI Cierre")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO Cierre")
        self.suc_armadi = Sucursal.objects.create(
            codigo="CA1", nombre="Suc ARMADI", razon_social="ARMADI", empresa=self.armadi
        )
        self.suc_mapogo = Sucursal.objects.create(
            codigo="CM1", nombre="Suc MAPOGO", razon_social="MAPOGO", empresa=self.mapogo
        )
        self.mes = timezone.datetime(2026, 6, 1).date()
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Efectivo ARMADI",
            fecha=self.mes,
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.mapogo,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("400.00"),
            concepto="Efectivo MAPOGO",
            fecha=self.mes,
            actor=self.admin,
        )

    def test_cada_empresa_cierra_su_propio_mes(self):
        cierre = close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)

        self.assertTrue(cierre.cerrado)
        self.assertEqual(cierre.empresa_id, self.armadi.pk)
        # Solo su propio efectivo, no el de la otra empresa.
        self.assertEqual(cierre.saldo_final_efectivo, Decimal("1000.00"))
        # MAPOGO sigue abierta.
        self.assertFalse(
            CierreMensualTesoreria.objects.filter(
                mes=self.mes, empresa=self.mapogo, cerrado=True
            ).exists()
        )

    def test_no_se_puede_cerrar_dos_veces_la_misma_empresa(self):
        close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)
        with self.assertRaises(ValidationError):
            close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)
        # Pero la otra empresa si puede cerrar el mismo mes.
        otro = close_treasury_month(2026, 6, empresa=self.mapogo, actor=self.admin)
        self.assertTrue(otro.cerrado)

    def test_una_caja_sin_validar_de_otra_empresa_no_bloquea_el_cierre(self):
        """Era el bloqueo real: ARMADI no podia cerrar por las cajas de Vivre."""
        turno = Turno.objects.create(
            empresa=self.mapogo, tipo=Turno.Tipo.MANANA, creado_por=self.admin
        )
        caja = Caja.objects.create(
            usuario=self.admin,
            turno=turno,
            sucursal=self.suc_mapogo,
            fecha_operativa=timezone.datetime(2026, 6, 15).date(),
            monto_inicial=Decimal("0.00"),
            estado=Caja.Estado.CERRADA,
            validacion_estado=Caja.ValidacionEstado.PENDIENTE,
        )

        # MAPOGO no puede cerrar: la caja pendiente es suya.
        with self.assertRaises(ValidationError):
            close_treasury_month(2026, 6, empresa=self.mapogo, actor=self.admin)

        # ARMADI si puede: esa caja no es de ARMADI.
        cierre = close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)
        self.assertTrue(cierre.cerrado)
        self.assertEqual(caja.validacion_estado, Caja.ValidacionEstado.PENDIENTE)

    def test_el_saldo_inicial_del_mes_siguiente_no_mezcla_empresas(self):
        close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)
        close_treasury_month(2026, 6, empresa=self.mapogo, actor=self.admin)

        julio_armadi = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.armadi.pk])
        julio_mapogo = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.mapogo.pk])
        julio_todo = build_disponibilidades_snapshot(2026, 7)

        self.assertEqual(julio_armadi["saldo_inicial_efectivo"], Decimal("1000.00"))
        self.assertEqual(julio_mapogo["saldo_inicial_efectivo"], Decimal("400.00"))
        # El consolidado es la suma de las dos: ni de mas ni de menos.
        self.assertEqual(julio_todo["saldo_inicial_efectivo"], Decimal("1400.00"))

    def test_cerrar_exige_empresa(self):
        with self.assertRaises(ValidationError):
            close_treasury_month(2026, 6, empresa=None, actor=self.admin)


class CierrePorEmpresaVistaTests(TestCase):
    """La pantalla ofrece un boton por empresa y deshabilita las ya cerradas."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin-vista-cierre", password="test", email="vc@test.com"
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI Vista")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO Vista")
        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.mes = timezone.datetime(2026, 6, 1).date()
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("500.00"),
            concepto="Efectivo",
            fecha=self.mes,
            actor=self.admin,
        )
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.armadi.pk, self.mapogo.pk]
        sesion.save()

    def _pantalla(self):
        from django.urls import reverse

        return self.client.get(reverse("treasury:disponibilidades"), {"year": 2026, "month": 6})

    def test_hay_un_boton_por_empresa(self):
        response = self._pantalla()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Cerrar {self.armadi.nombre}")
        self.assertContains(response, f"Cerrar {self.mapogo.nombre}")

    def test_la_empresa_ya_cerrada_queda_deshabilitada(self):
        close_treasury_month(2026, 6, empresa=self.armadi, actor=self.admin)
        response = self._pantalla()
        self.assertContains(response, f"{self.armadi.nombre}: mes cerrado")
        # La otra sigue ofreciendo el boton.
        self.assertContains(response, f"Cerrar {self.mapogo.nombre}")

    def test_el_post_cierra_la_empresa_que_manda_y_no_otra(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("treasury:close_month"),
            {"year": 2026, "month": 6, "empresa": self.mapogo.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            CierreMensualTesoreria.objects.filter(
                mes=self.mes, empresa=self.mapogo, cerrado=True
            ).exists()
        )
        self.assertFalse(
            CierreMensualTesoreria.objects.filter(
                mes=self.mes, empresa=self.armadi, cerrado=True
            ).exists()
        )


class SaldoInicialSinCierreTests(TestCase):
    """Sin ningun mes cerrado antes, el flujo arranca del saldo real, no de 0.

    En produccion nunca se habia cerrado un mes: el flujo de octubre de Vivre
    mostraba saldo inicial 0 con la caja fuerte en -11,8 M al 30/09.
    """

    def setUp(self):
        from treasury.models import CuentaBancaria, MovimientoBancario

        self.admin = User.objects.create_superuser(
            username="admin-sin-cierre", password="test", email="sc@test.com"
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI Sin Cierre")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO Sin Cierre")
        self.suc_mapogo = Sucursal.objects.create(
            codigo="SC1", nombre="Suc MAPOGO SC", razon_social="MAPOGO", empresa=self.mapogo
        )
        junio = timezone.datetime(2026, 6, 10).date()
        julio = timezone.datetime(2026, 7, 5).date()
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("1000.00"),
            concepto="Efectivo ARMADI",
            fecha=junio,
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.mapogo,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("400.00"),
            concepto="Efectivo MAPOGO",
            fecha=junio,
            actor=self.admin,
        )
        # MAPOGO queda en negativo a fin de junio, como Vivre al 30/09.
        register_central_cash_movement(
            empresa=self.mapogo,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("1000.00"),
            concepto="Pago grande",
            fecha=junio,
            actor=self.admin,
        )
        register_central_cash_movement(
            empresa=self.mapogo,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("50.00"),
            concepto="Efectivo de julio",
            fecha=julio,
            actor=self.admin,
        )
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Banco MAPOGO", banco="Galicia", tipo_cuenta="CC", numero_cuenta="99",
            empresa=self.mapogo, creado_por=self.admin,
        )
        for fecha, tipo, monto in (
            (junio, MovimientoBancario.Tipo.CREDITO, "300.00"),
            (junio, MovimientoBancario.Tipo.DEBITO, "40.00"),
            (julio, MovimientoBancario.Tipo.CREDITO, "10.00"),
        ):
            MovimientoBancario.objects.create(
                cuenta_bancaria=self.cuenta,
                tipo=tipo,
                clase=(
                    MovimientoBancario.Clase.OTRO_INGRESO
                    if tipo == MovimientoBancario.Tipo.CREDITO
                    else MovimientoBancario.Clase.OTRO_EGRESO
                ),
                fecha=fecha,
                monto=Decimal(monto),
                concepto="Banco",
                creado_por=self.admin,
            )

    def _cuenta(self, snapshot):
        return next(item for item in snapshot["accounts_info"] if item["account"].pk == self.cuenta.pk)

    def test_sin_cierre_el_efectivo_arranca_del_saldo_real_aunque_sea_negativo(self):
        julio = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.mapogo.pk])

        self.assertFalse(julio["saldo_inicial_desde_cierre"])
        self.assertEqual(julio["saldo_inicial_efectivo"], Decimal("-600.00"))
        self.assertEqual(julio["cash_in"], Decimal("50.00"))
        self.assertEqual(julio["saldo_final_efectivo"], Decimal("-550.00"))

    def test_sin_cierre_cada_empresa_y_el_consolidado_dan_su_saldo(self):
        armadi = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.armadi.pk])
        todo = build_disponibilidades_snapshot(2026, 7)

        self.assertEqual(armadi["saldo_inicial_efectivo"], Decimal("1000.00"))
        self.assertEqual(todo["saldo_inicial_efectivo"], Decimal("400.00"))

    def test_sin_cierre_el_banco_arranca_de_su_saldo_real(self):
        julio = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.mapogo.pk])

        cuenta = self._cuenta(julio)
        self.assertEqual(cuenta["initial"], Decimal("260.00"))
        self.assertEqual(cuenta["final"], Decimal("270.00"))

    def test_el_banco_respeta_el_saldo_inicial_cargado(self):
        from treasury.services import set_initial_bank_balance

        set_initial_bank_balance(
            cuenta_bancaria=self.cuenta,
            fecha_referencia=timezone.datetime(2026, 6, 1).date(),
            importe=Decimal("5000.00"),
            motivo="Extracto",
            actor=self.admin,
        )
        julio = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.mapogo.pk])

        self.assertEqual(self._cuenta(julio)["initial"], Decimal("5260.00"))

    def test_el_primer_mes_sin_historia_previa_sigue_en_cero(self):
        junio = build_disponibilidades_snapshot(2026, 6, empresa_ids=[self.mapogo.pk])

        self.assertEqual(junio["saldo_inicial_efectivo"], Decimal("0.00"))
        self.assertEqual(self._cuenta(junio)["initial"], Decimal("0.00"))

    def test_con_un_cierre_se_usa_la_foto_del_cierre(self):
        close_treasury_month(2026, 6, empresa=self.mapogo, actor=self.admin)
        # Si la historia de junio cambia despues del cierre, el arrastre no:
        # el mes cerrado es una foto congelada.
        MovimientoCajaCentral.objects.filter(
            caja_central__empresa=self.mapogo, concepto="Pago grande"
        ).update(monto=Decimal("1.00"))
        julio = build_disponibilidades_snapshot(2026, 7, empresa_ids=[self.mapogo.pk])

        self.assertTrue(julio["saldo_inicial_desde_cierre"])
        self.assertEqual(julio["saldo_inicial_efectivo"], Decimal("-600.00"))

    def test_el_primer_cierre_guarda_el_saldo_real(self):
        cierre = close_treasury_month(2026, 7, empresa=self.mapogo, actor=self.admin)

        self.assertEqual(cierre.saldo_inicial_efectivo, Decimal("-600.00"))
        self.assertEqual(cierre.saldo_final_efectivo, Decimal("-550.00"))
        self.assertEqual(cierre.saldos_bancarios_json[str(self.cuenta.pk)], "270.00")

    def test_la_pantalla_aclara_de_donde_sale_el_saldo_inicial(self):
        from django.urls import reverse

        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.client.force_login(self.admin)
        sesion = self.client.session
        sesion["empresa_ids"] = [self.mapogo.pk]
        sesion.save()

        response = self.client.get(reverse("treasury:disponibilidades"), {"year": 2026, "month": 7})

        self.assertContains(
            response,
            "Sin un mes cerrado anterior, el saldo inicial surge de los movimientos registrados hasta el 30/06/2026.",
        )
