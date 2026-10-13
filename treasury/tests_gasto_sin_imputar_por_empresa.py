"""Egresos de boveda sin imputar: quedan en la empresa de la boveda.

La lectura economica cuenta como "gasto sin imputar" los egresos administrativos
de la boveda que todavia no tienen sucursal, rubro o periodo. Los del banco ya se
cortaban por la empresa de la cuenta; los de la boveda no, asi que un egreso sin
imputar de una empresa aparecia en el tablero y en el reporte de la otra. Ahora la
boveda usa el mismo criterio que las cuentas (`bank_account_empresa_scope_query`):
una boveda vieja sin empresa sigue visible hasta que se le asigne una.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from cashops.models import Empresa, Sucursal
from treasury.models import CajaCentral, MovimientoCajaCentral
from treasury.services import build_economic_period_snapshot, register_central_cash_movement
from users.models import Role

User = get_user_model()


class GastoSinImputarPorEmpresaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin-sin-imputar", password="test", role=Role.objects.create(code="ADMIN", name="Administrador")
        )
        self.armadi = Empresa.objects.create(nombre="ARMADI SIN IMPUTAR SRL")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO SIN IMPUTAR SRL")
        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.belgrano = Sucursal.objects.create(
            nombre="Belgrano sin imputar", codigo="BSI", razon_social="ARMADI SIN IMPUTAR SRL", empresa=self.armadi
        )
        self.dia = date(2026, 9, 10)

    def _egreso_sin_imputar(self, empresa, monto):
        register_central_cash_movement(
            empresa=empresa,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=monto,
            concepto="Gasto sin imputar",
            fecha=self.dia,
            actor=self.admin,
        )

    def _sin_imputar(self, *, sucursal=None, empresa_ids=None):
        snapshot = build_economic_period_snapshot(
            date_from=date(2026, 9, 1), date_to=date(2026, 9, 30), sucursal=sucursal, empresa_ids=empresa_ids
        )
        return snapshot["treasury_unmapped_expenses_total"], snapshot["treasury_unmapped_expenses_count"]

    def test_egreso_de_otra_empresa_no_aparece_en_el_general(self):
        self._egreso_sin_imputar(self.armadi, Decimal("30.00"))
        self._egreso_sin_imputar(self.mapogo, Decimal("70.00"))

        self.assertEqual(self._sin_imputar(empresa_ids=[self.armadi.pk]), (Decimal("30.00"), 1))
        self.assertEqual(self._sin_imputar(empresa_ids=[self.mapogo.pk]), (Decimal("70.00"), 1))
        self.assertEqual(self._sin_imputar(empresa_ids=[self.armadi.pk, self.mapogo.pk]), (Decimal("100.00"), 2))

    def test_egreso_de_otra_empresa_no_aparece_en_la_sucursal(self):
        self._egreso_sin_imputar(self.armadi, Decimal("30.00"))
        self._egreso_sin_imputar(self.mapogo, Decimal("70.00"))

        self.assertEqual(self._sin_imputar(sucursal=self.belgrano), (Decimal("30.00"), 1))

    def test_boveda_vieja_sin_empresa_sigue_visible(self):
        MovimientoCajaCentral.objects.create(
            caja_central=CajaCentral.objects.create(nombre="Boveda legacy"),
            fecha=self.dia,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            monto=Decimal("40.00"),
            concepto="Egreso de boveda sin empresa",
            creado_por=self.admin,
        )

        self.assertEqual(self._sin_imputar(empresa_ids=[self.armadi.pk]), (Decimal("40.00"), 1))
        self.assertEqual(self._sin_imputar(empresa_ids=[self.mapogo.pk]), (Decimal("40.00"), 1))
        self.assertEqual(self._sin_imputar(sucursal=self.belgrano), (Decimal("40.00"), 1))
