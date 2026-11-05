"""Reparto de egresos entre sucursales con una clave de porcentajes.

Pedido de tesoreria (04/11/2026, confirmado el 06/11): los impuestos de ARMADI
van con la clave Terminal 27, Yo Helados 4, Centro 25, EB1 34, EB2 10 y los
sueldos de estructura en partes iguales entre los cinco locales. Cada parte se
registra como un egreso de su sucursal, como ya se hacia a mano.
"""

import importlib
import uuid
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.forms import BankMovementForm, EgresoTesoreriaForm
from treasury.models import (
    ClaveReparto,
    ClaveRepartoSucursal,
    CuentaBancaria,
    MovimientoBancario,
    MovimientoCajaCentral,
)
from treasury.reparto import repartir_importe
from treasury.services import (
    build_economic_period_snapshot,
    create_bank_movement_repartido,
    get_boveda,
    register_central_cash_movement,
    register_egreso_tesoreria_repartido,
)

User = get_user_model()
OCTUBRE = date(2026, 10, 1)
CODIGOS = ("TERM-01", "YH-05", "CENT-02", "EB1-03", "EB2-04")
IMPUESTOS = {"TERM-01": "27", "YH-05": "4", "CENT-02": "25", "EB1-03": "34", "EB2-04": "10"}


class RepartoBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin-reparto", password="test", email="r@test.com")
        self.armadi = Empresa.objects.create(nombre="ARMADI Reparto")
        self.vivre = Empresa.objects.create(nombre="MAPOGO Reparto")
        self.suc = {
            codigo: Sucursal.objects.create(
                codigo=codigo, nombre=f"Local {codigo}", razon_social="ARMADI", empresa=self.armadi
            )
            for codigo in CODIGOS
        }
        self.suc_vivre = Sucursal.objects.create(
            codigo="VIV-01", nombre="Vivre", razon_social="MAPOGO", empresa=self.vivre
        )
        self.rubro = RubroOperativo.objects.create(nombre="IMPUESTOS AFIP REPARTO")
        self.personal = RubroOperativo.objects.create(nombre="PERSONAL REPARTO")
        self.impuestos = self._clave("Impuestos", IMPUESTOS)
        self.sueldos = self._clave("Sueldos de estructura", {codigo: "20" for codigo in CODIGOS})
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Macro ARMADI", banco="Macro", tipo_cuenta="CC", numero_cuenta="1",
            empresa=self.armadi, creado_por=self.admin,
        )
        self.cuenta_vivre = CuentaBancaria.objects.create(
            nombre="Macro Vivre", banco="Macro", tipo_cuenta="CC", numero_cuenta="2",
            empresa=self.vivre, creado_por=self.admin,
        )
        register_central_cash_movement(
            empresa=self.armadi,
            tipo=MovimientoCajaCentral.Tipo.APORTE,
            monto=Decimal("100000.00"),
            concepto="Efectivo",
            fecha=date(2026, 10, 1),
            actor=self.admin,
        )

    def _clave(self, nombre, porcentajes, empresa=None):
        clave = ClaveReparto.objects.create(empresa=empresa or self.armadi, nombre=nombre)
        for codigo, porcentaje in porcentajes.items():
            sucursal = self.suc.get(codigo) or Sucursal.objects.get(codigo=codigo)
            ClaveRepartoSucursal.objects.create(clave=clave, sucursal=sucursal, porcentaje=Decimal(porcentaje))
        return clave

    def _importes(self, partes):
        return {parte.sucursal.codigo: parte.importe for parte in partes}


class RepartirImporteTests(RepartoBase):
    def test_reparte_con_la_clave_y_suma_exacto(self):
        partes = repartir_importe(Decimal("1000.01"), self.impuestos)

        self.assertEqual(
            self._importes(partes),
            {
                "TERM-01": Decimal("270.00"),
                "YH-05": Decimal("40.00"),
                "CENT-02": Decimal("250.00"),
                "EB1-03": Decimal("340.00"),
                # La ultima se lleva el redondeo.
                "EB2-04": Decimal("100.01"),
            },
        )
        self.assertEqual(sum(p.importe for p in partes), Decimal("1000.01"))

    def test_partes_iguales_entre_los_cinco_locales(self):
        partes = repartir_importe(Decimal("1000.00"), self.sueldos)

        self.assertEqual(set(self._importes(partes).values()), {Decimal("200.00")})
        self.assertEqual(len(partes), 5)

    def test_con_centavos_ninguna_parte_queda_negativa_ni_se_pasa_del_total(self):
        partes = repartir_importe(Decimal("0.03"), self.sueldos)

        self.assertTrue(all(p.importe > 0 for p in partes))
        self.assertEqual(sum(p.importe for p in partes), Decimal("0.03"))

    def test_una_clave_que_no_suma_100_no_se_usa(self):
        clave = self._clave("Mal armada", {"TERM-01": "50", "YH-05": "40"})

        with self.assertRaises(ValidationError) as error:
            repartir_importe(Decimal("100.00"), clave)
        self.assertIn("suman 90 %", str(error.exception))

    def test_una_clave_de_otra_empresa_no_se_usa(self):
        with self.assertRaises(ValidationError):
            repartir_importe(Decimal("100.00"), self.impuestos, empresa_id=self.vivre.pk)

    def test_una_sucursal_desactivada_frena_el_reparto(self):
        self.suc["YH-05"].activa = False
        self.suc["YH-05"].save(update_fields=["activa"])

        with self.assertRaises(ValidationError):
            repartir_importe(Decimal("100.00"), self.impuestos)

    def test_una_clave_desactivada_no_se_usa(self):
        self.impuestos.activa = False
        self.impuestos.save(update_fields=["activa"])

        with self.assertRaises(ValidationError):
            repartir_importe(Decimal("100.00"), self.impuestos)


class EgresoRepartidoTests(RepartoBase):
    def _repartir(self, clave, monto="1000.00", token=None, **extra):
        datos = dict(
            empresa=self.armadi,
            clave=clave,
            fuente="CAJA_CENTRAL",
            fecha=date(2026, 10, 14),
            monto=Decimal(monto),
            concepto="Sueldos (Ariel)",
            rubro=self.personal,
            periodo=OCTUBRE,
            token_alta=token,
            actor=self.admin,
        )
        datos.update(extra)
        return register_egreso_tesoreria_repartido(**datos)

    def test_registra_un_egreso_por_sucursal_y_la_caja_fuerte_baja_el_total(self):
        antes = get_boveda(self.armadi).saldo_actual

        movimientos = self._repartir(self.sueldos)

        self.assertEqual(len(movimientos), 5)
        self.assertEqual({m.sucursal_gasto.codigo for m in movimientos}, set(CODIGOS))
        self.assertTrue(all(m.tipo == MovimientoCajaCentral.Tipo.EGRESO_ADMIN for m in movimientos))
        self.assertTrue(all(m.monto == Decimal("200.00") for m in movimientos))
        self.assertTrue(all(m.concepto == "Sueldos (Ariel)" for m in movimientos))
        self.assertEqual(get_boveda(self.armadi).saldo_actual, antes - Decimal("1000.00"))
        self.assertIn("Reparto Sueldos de estructura: 20 % de $ 1.000,00", movimientos[0].observaciones)

    def test_reenviar_el_mismo_formulario_no_duplica(self):
        token = uuid.uuid4()
        primero = self._repartir(self.impuestos, token=token)
        segundo = self._repartir(self.impuestos, token=token)

        self.assertEqual([m.pk for m in primero], [m.pk for m in segundo])
        self.assertEqual(MovimientoCajaCentral.objects.filter(tipo="EGRESO_ADMIN").count(), 5)

    def test_si_una_parte_falla_no_queda_ninguna(self):
        with self.assertRaises(ValidationError):
            self._repartir(self.impuestos, rubro=None)

        self.assertFalse(MovimientoCajaCentral.objects.filter(tipo="EGRESO_ADMIN").exists())

    def test_cada_sucursal_suma_su_parte_en_el_resultado(self):
        self._repartir(self.impuestos, monto="1000.00", rubro=self.rubro)

        terminal = build_economic_period_snapshot(
            date_from=OCTUBRE, date_to=date(2026, 10, 31), sucursal=self.suc["TERM-01"]
        )
        empresa = build_economic_period_snapshot(
            date_from=OCTUBRE, date_to=date(2026, 10, 31), empresa_ids=[self.armadi.pk]
        )
        self.assertEqual(terminal["treasury_expense_total"], Decimal("270.00"))
        self.assertEqual(empresa["treasury_expense_total"], Decimal("1000.00"))

    def test_desde_el_banco_tambien_reparte(self):
        movimientos = self._repartir(self.impuestos, fuente="BANCO", cuenta_bancaria=self.cuenta)

        self.assertEqual(len(movimientos), 5)
        self.assertEqual(
            sum(MovimientoBancario.objects.filter(cuenta_bancaria=self.cuenta).values_list("monto", flat=True)),
            Decimal("1000.00"),
        )


class DebitoRepartidoTests(RepartoBase):
    def _repartir(self, clave=None, **extra):
        datos = dict(
            clave=clave or self.impuestos,
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.IMPUESTO,
            rubro_operativo=self.rubro,
            periodo_pago=OCTUBRE,
            fecha=date(2026, 10, 20),
            monto=Decimal("50000.00"),
            concepto="DB IMP. AFIP",
            referencia="123",
            token_alta=uuid.uuid4(),
            actor=self.admin,
        )
        datos.update(extra)
        return create_bank_movement_repartido(**datos)

    def test_un_debito_por_sucursal_que_suman_el_del_extracto(self):
        movimientos = self._repartir()

        self.assertEqual(
            {m.sucursal_gasto.codigo: m.monto for m in movimientos},
            {
                "TERM-01": Decimal("13500.00"),
                "YH-05": Decimal("2000.00"),
                "CENT-02": Decimal("12500.00"),
                "EB1-03": Decimal("17000.00"),
                "EB2-04": Decimal("5000.00"),
            },
        )
        self.assertTrue(all(m.referencia == "123" and m.concepto == "DB IMP. AFIP" for m in movimientos))

    def test_un_credito_no_se_reparte(self):
        with self.assertRaises(ValidationError):
            self._repartir(tipo=MovimientoBancario.Tipo.CREDITO, clase=MovimientoBancario.Clase.OTRO_INGRESO)
        self.assertFalse(MovimientoBancario.objects.exists())

    def test_un_retiro_no_se_reparte(self):
        with self.assertRaises(ValidationError):
            self._repartir(clase=MovimientoBancario.Clase.RETIRO)

    def test_la_cuenta_de_otra_empresa_no_usa_la_clave(self):
        with self.assertRaises(ValidationError):
            self._repartir(cuenta_bancaria=self.cuenta_vivre)
        self.assertFalse(MovimientoBancario.objects.exists())


class PantallasRepartoTests(RepartoBase):
    def setUp(self):
        super().setUp()
        self.admin.empresas_permitidas.set([self.armadi, self.vivre])
        self.client.force_login(self.admin)
        self._empresas([self.armadi.pk, self.vivre.pk])

    def _empresas(self, ids):
        sesion = self.client.session
        sesion["empresa_ids"] = ids
        sesion.save()

    def _egreso(self, **extra):
        datos = {
            "empresa": self.armadi.pk,
            "fuente": "CAJA_CENTRAL",
            "fecha": "2026-10-14",
            "monto": "1000.00",
            "rubro": self.personal.pk,
            "concepto": "Sueldos (Maria)",
            "sucursal": "",
            "clave_reparto": self.sueldos.pk,
            "periodo": "2026-10-01",
            "observaciones": "",
            "token_alta": str(uuid.uuid4()),
        }
        datos.update(extra)
        return self.client.post(reverse("treasury:egreso_tesoreria_create"), datos)

    def test_el_egreso_ofrece_las_claves_con_sus_porcentajes(self):
        response = self.client.get(reverse("treasury:egreso_tesoreria_create"))

        self.assertContains(response, "Repartir entre sucursales")
        self.assertContains(response, "Impuestos (TERM-01 27 %, YH-05 4 %, CENT-02 25 %, EB1-03 34 %, EB2-04 10 %)")

    def test_el_egreso_repartido_registra_cinco_y_lo_dice(self):
        response = self._egreso()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(MovimientoCajaCentral.objects.filter(tipo="EGRESO_ADMIN").count(), 5)
        mensajes = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any(m.startswith("Se registraron 5 movimientos, uno por sucursal") for m in mensajes))

    def test_sin_sucursal_ni_reparto_pide_uno_de_los_dos(self):
        response = self._egreso(clave_reparto="")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Indique la sucursal o elija un reparto.")
        self.assertFalse(MovimientoCajaCentral.objects.filter(tipo="EGRESO_ADMIN").exists())

    def test_con_sucursal_y_sin_reparto_sigue_como_siempre(self):
        response = self._egreso(clave_reparto="", sucursal=self.suc["TERM-01"].pk)

        self.assertEqual(response.status_code, 302)
        egreso = MovimientoCajaCentral.objects.get(tipo="EGRESO_ADMIN")
        self.assertEqual((egreso.sucursal_gasto.codigo, egreso.monto), ("TERM-01", Decimal("1000.00")))

    def test_la_clave_de_otra_empresa_no_se_acepta(self):
        response = self._egreso(empresa=self.vivre.pk)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La clave de reparto es de otra empresa.")
        self.assertFalse(MovimientoCajaCentral.objects.filter(tipo="EGRESO_ADMIN").exists())

    def test_sin_claves_en_la_empresa_no_aparece_el_campo(self):
        self.assertNotIn("clave_reparto", EgresoTesoreriaForm(empresa_ids=[self.vivre.pk]).fields)
        self.assertIn("clave_reparto", EgresoTesoreriaForm(empresa_ids=[self.armadi.pk]).fields)

    def test_el_debito_repartido_desde_movimientos_bancarios(self):
        response = self.client.post(
            reverse("treasury:bank_movements_create"),
            {
                "cuenta_bancaria": self.cuenta.pk,
                "tipo": MovimientoBancario.Tipo.DEBITO,
                "clase": MovimientoBancario.Clase.IMPUESTO,
                "rubro_operativo": self.rubro.pk,
                "proveedor": "",
                "sucursal_gasto": "",
                "clave_reparto": self.impuestos.pk,
                "periodo_pago": "2026-10-01",
                "fecha": "2026-10-20",
                "monto": "50000.00",
                "concepto": "DB IMP. AFIP",
                "referencia": "",
                "observaciones": "",
                "token_alta": str(uuid.uuid4()),
            },
        )

        self.assertRedirects(response, reverse("treasury:bank_movements_list"), fetch_redirect_response=False)
        self.assertEqual(MovimientoBancario.objects.filter(cuenta_bancaria=self.cuenta).count(), 5)
        self.assertEqual(
            sorted(MovimientoBancario.objects.values_list("sucursal_gasto__codigo", flat=True)), sorted(CODIGOS)
        )

    def test_editar_un_movimiento_no_ofrece_repartirlo(self):
        movimiento = create_bank_movement_repartido(
            clave=self.impuestos,
            cuenta_bancaria=self.cuenta,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.IMPUESTO,
            rubro_operativo=self.rubro,
            periodo_pago=OCTUBRE,
            fecha=date(2026, 10, 20),
            monto=Decimal("100.00"),
            concepto="DB",
            actor=self.admin,
        )[0]

        self.assertNotIn("clave_reparto", BankMovementForm(instance=movimiento).fields)
        self.assertIn("clave_reparto", BankMovementForm().fields)


class SemillaDeClavesTests(TestCase):
    """La migracion crea las dos claves de ARMADI solo si estan sus sucursales."""

    def _crear(self):
        modulo = importlib.import_module("treasury.migrations.0039_claves_reparto_armadi")
        modulo.crear_claves(apps, None)

    def test_crea_impuestos_y_sueldos_con_sus_porcentajes(self):
        armadi = Empresa.objects.create(nombre="ARMADI Semilla")
        for codigo in CODIGOS + ("PP-OV-06",):
            Sucursal.objects.create(codigo=codigo, nombre=codigo, razon_social="ARMADI", empresa=armadi)

        self._crear()
        self._crear()  # correrla de nuevo no duplica

        impuestos = ClaveReparto.objects.get(nombre="Impuestos")
        sueldos = ClaveReparto.objects.get(nombre="Sueldos de estructura")
        self.assertEqual(
            {p.sucursal.codigo: p.porcentaje for p in impuestos.partes.all()},
            {codigo: Decimal(valor) for codigo, valor in IMPUESTOS.items()},
        )
        self.assertEqual({p.porcentaje for p in sueldos.partes.all()}, {Decimal("20")})
        # La panaderia no es un local: no entra en ninguna clave.
        self.assertNotIn("PP-OV-06", {p.sucursal.codigo for p in sueldos.partes.all()})
        self.assertEqual(ClaveReparto.objects.count(), 2)

    def test_sin_las_sucursales_no_crea_nada(self):
        Sucursal.objects.create(
            codigo="TERM-01", nombre="Terminal", razon_social="X", empresa=Empresa.objects.create(nombre="Otra")
        )

        self._crear()

        self.assertFalse(ClaveReparto.objects.exists())
