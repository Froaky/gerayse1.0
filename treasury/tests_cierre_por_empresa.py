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
