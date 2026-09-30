"""Importacion de las planillas de banco y efectivo central de tesoreria.

La regla que importa: una fila de la planilla que paga a un proveedor con
deuda cargada en Gerayse PAGA esa deuda; no se carga como gasto. Si se cargara
como gasto, el mes contaria dos veces lo mismo y la deuda quedaria abierta.
"""

import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.importacion_planillas import (
    Accion,
    Importador,
    leer_planilla_banco,
    leer_planilla_efectivo,
    parsear_monto,
)
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import register_cash_payment

User = get_user_model()

CABECERA_BANCO = "TRANSFERENCIAS AGOSTO,,,,SUCURSALES\nFECHA ,DENOMINACIÓN,RUBRO,MONTO,EC1,EC2,EB,EB2,PP,H\n"


class ParseoTests(TestCase):
    def test_montos_con_formato_argentino(self):
        self.assertEqual(parsear_monto('"$1.234.567,89"'.strip('"')), Decimal("1234567.89"))
        self.assertEqual(parsear_monto("934906,5"), Decimal("934906.50"))
        self.assertEqual(parsear_monto("62500"), Decimal("62500.00"))
        self.assertEqual(parsear_monto("1,00"), Decimal("1.00"))
        self.assertIsNone(parsear_monto(""))

    def test_banco_busca_la_cabecera_y_lee_el_reparto(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "banco.csv"
            ruta.write_text(
                CABECERA_BANCO
                + '01/08/2026,LAS FLORES,PAN,"100.000,00","62.500,00","25.000,00","12.500,00",,,,"100000,00",OK\n'
                + ',,,,,,,,,,"0,00",OK\n',
                encoding="utf-8",
            )
            filas = leer_planilla_banco(ruta)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0].monto, Decimal("100000.00"))
        self.assertEqual(
            filas[0].reparto,
            {"EC1": Decimal("62500.00"), "EC2": Decimal("25000.00"), "EB": Decimal("12500.00")},
        )


class ImportadorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin-imp", password="x", email="imp@test.com")
        self.empresa = Empresa.objects.create(nombre="ARMADI TEST")
        self.admin.empresas_permitidas.set([self.empresa])
        self.term = Sucursal.objects.create(codigo="TERM-01", nombre="Terminal", razon_social="A", empresa=self.empresa)
        self.cent = Sucursal.objects.create(codigo="CENT-02", nombre="Centro", razon_social="A", empresa=self.empresa)
        self.rubro_pan = RubroOperativo.objects.create(nombre="PAN")
        self.rubro_verdura = RubroOperativo.objects.create(nombre="VERDURAS")
        self.rubro_servicios = RubroOperativo.objects.create(nombre="SERVICIOS")
        self.cat_pan = CategoriaCuentaPagar.objects.create(
            nombre="Pan", rubro_operativo=self.rubro_pan, creado_por=self.admin
        )
        self.cat_verdura = CategoriaCuentaPagar.objects.create(
            nombre="Verdura", rubro_operativo=self.rubro_verdura, creado_por=self.admin
        )
        self.las_flores = Proveedor.objects.create(razon_social="LAS FLORES", creado_por=self.admin)
        self.pare_carrito = Proveedor.objects.create(razon_social="PARE CARRITO", creado_por=self.admin)
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Macro",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="1-1",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    # --- helpers ---
    def _deuda(self, proveedor, sucursal, importe, emision, categoria=None):
        return CuentaPorPagar.objects.create(
            proveedor=proveedor,
            categoria=categoria or self.cat_pan,
            concepto="Factura",
            fecha_emision=emision,
            fecha_vencimiento=emision,
            periodo_referencia=emision.replace(day=1),
            importe_total=Decimal(importe),
            saldo_pendiente=Decimal(importe),
            sucursal=sucursal,
            creado_por=self.admin,
        )

    def _banco(self, *lineas):
        ruta = Path(self.tmp.name) / "banco.csv"
        ruta.write_text(CABECERA_BANCO + "\n".join(lineas) + "\n", encoding="utf-8")
        return leer_planilla_banco(ruta)

    def _efectivo(self, planilla, *lineas):
        ruta = Path(self.tmp.name) / f"efectivo_{planilla}.csv"
        ruta.write_text("Fecha,Proveedor,Cantidad,Tipo de pago\n" + "\n".join(lineas) + "\n", encoding="utf-8")
        return leer_planilla_efectivo(ruta, planilla)

    def _importador(self, banco=(), efectivo=()):
        importador = Importador(actor=self.admin, cuenta_banco=self.cuenta)
        importador.agregar_banco(list(banco))
        importador.agregar_efectivo(list(efectivo))
        importador.planificar()
        return importador

    def _acciones(self, importador):
        return sorted((op.accion, op.sucursal.codigo if op.sucursal else "", op.monto) for op in importador.operaciones)

    # --- banco ---
    def test_transferencia_paga_las_deudas_de_cada_sucursal_en_lugar_de_cargar_gasto(self):
        deuda_term = self._deuda(self.las_flores, self.term, "62500.00", date(2026, 7, 30))
        deuda_cent = self._deuda(self.las_flores, self.cent, "25000.00", date(2026, 7, 30))
        importador = self._importador(
            banco=self._banco('01/08/2026,LAS FLORES,PAN,"87.500,00","62.500,00","25.000,00",,,,,"87500,00",OK')
        )
        self.assertEqual(
            self._acciones(importador),
            [
                (Accion.PAGAR_DEUDAS, "CENT-02", Decimal("25000.00")),
                (Accion.PAGAR_DEUDAS, "TERM-01", Decimal("62500.00")),
            ],
        )
        importador.aplicar()

        deuda_term.refresh_from_db()
        deuda_cent.refresh_from_db()
        self.assertEqual(deuda_term.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(deuda_cent.estado, CuentaPorPagar.Estado.PAGADA)
        # Un debito por sucursal, igual que carga tesoreria, y ninguno queda
        # como gasto: el costo ya lo cuenta la deuda.
        debitos = MovimientoBancario.objects.filter(cuenta_bancaria=self.cuenta)
        self.assertEqual(debitos.count(), 2)
        self.assertEqual(
            set(debitos.values_list("origen", flat=True)), {MovimientoBancario.Origen.PAGO_TESORERIA}
        )
        self.assertEqual(sum(debitos.values_list("monto", flat=True)), Decimal("87500.00"))

    def test_paga_primero_las_facturas_mas_recientes_anteriores_al_pago(self):
        vieja = self._deuda(self.las_flores, self.term, "10000.00", date(2026, 6, 10))
        reciente = self._deuda(self.las_flores, self.term, "30000.00", date(2026, 7, 28))
        posterior = self._deuda(self.las_flores, self.term, "5000.00", date(2026, 8, 5))
        importador = self._importador(
            banco=self._banco('01/08/2026,LAS FLORES,PAN,"30.000,00","30.000,00",,,,,,"30000,00",OK')
        )
        importador.aplicar()
        for deuda in (vieja, reciente, posterior):
            deuda.refresh_from_db()
        self.assertEqual(reciente.estado, CuentaPorPagar.Estado.PAGADA)
        self.assertEqual(vieja.saldo_pendiente, Decimal("10000.00"))
        self.assertEqual(posterior.saldo_pendiente, Decimal("5000.00"))

    def test_lo_que_no_cubren_las_deudas_entra_como_gasto_con_rubro(self):
        self._deuda(self.las_flores, self.term, "20000.00", date(2026, 7, 30))
        importador = self._importador(
            banco=self._banco('01/08/2026,LAS FLORES,PAN,"50.000,00","50.000,00",,,,,,"50000,00",OK')
        )
        self.assertEqual(
            self._acciones(importador),
            [
                (Accion.EGRESO, "TERM-01", Decimal("30000.00")),
                (Accion.PAGAR_DEUDAS, "TERM-01", Decimal("20000.00")),
            ],
        )
        importador.aplicar()
        gasto = MovimientoBancario.objects.get(origen=MovimientoBancario.Origen.MANUAL)
        self.assertEqual(gasto.monto, Decimal("30000.00"))
        self.assertEqual(gasto.rubro_operativo, self.rubro_pan)
        self.assertEqual(gasto.sucursal_gasto, self.term)
        self.assertEqual(gasto.periodo_pago, date(2026, 8, 1))
        self.assertEqual(gasto.proveedor, self.las_flores)

    def test_sin_proveedor_entra_como_egreso_de_tesoreria(self):
        importador = self._importador(
            banco=self._banco('03/08/2026,EDESA,SERVICIOS,"1.000,00","1.000,00",,,,,,"1000,00",OK')
        )
        importador.aplicar()
        gasto = MovimientoBancario.objects.get()
        self.assertEqual(gasto.origen, MovimientoBancario.Origen.EGRESO_TESORERIA)
        self.assertEqual(gasto.rubro_operativo, self.rubro_servicios)

    def test_echeq_y_filas_que_no_cuadran_no_se_importan(self):
        importador = self._importador(
            banco=self._banco(
                '04/08/2026,ECHEQ LAS FLORES AL 11/08,PAN,"5.000,00","5.000,00",,,,,,"5000,00",OK',
                '29/08/2026,LAS FLORES,PAN,"10.000,00","12.000,00",,,,,,"12000,00",ERROR',
                '31/08/2026,INTERESES PUNITORIOS,IMPUESTOS,"104,00",,,,,,,"0,00",ERROR',
            )
        )
        self.assertEqual(
            sorted(op.accion for op in importador.operaciones),
            [Accion.EXCLUIDA, Accion.REVISAR, Accion.REVISAR],
        )
        importador.aplicar()
        self.assertFalse(MovimientoBancario.objects.exists())

    def test_fila_sin_reparto_de_un_proveedor_con_deudas_se_reparte_por_las_deudas(self):
        self._deuda(self.las_flores, self.term, "7000.00", date(2026, 8, 1))
        self._deuda(self.las_flores, self.cent, "3000.00", date(2026, 8, 2))
        importador = self._importador(
            banco=self._banco('10/08/2026,LAS FLORES,PAN,"10.000,00",,,,,,,"0,00",ERROR')
        )
        self.assertEqual(
            self._acciones(importador),
            [
                (Accion.PAGAR_DEUDAS, "CENT-02", Decimal("3000.00")),
                (Accion.PAGAR_DEUDAS, "TERM-01", Decimal("7000.00")),
            ],
        )

    # --- efectivo ---
    def test_efectivo_paga_la_deuda_del_proveedor_del_rubro_y_el_resto_es_gasto(self):
        deuda = self._deuda(self.pare_carrito, self.term, "80000.00", date(2026, 7, 2), self.cat_verdura)
        importador = self._importador(efectivo=self._efectivo("EC1", '3/07/2026,VERDURA,"$84.540,00",Efectivo'))
        self.assertEqual(
            self._acciones(importador),
            [
                (Accion.EGRESO, "TERM-01", Decimal("4540.00")),
                (Accion.PAGAR_DEUDAS, "TERM-01", Decimal("80000.00")),
            ],
        )
        importador.aplicar()
        deuda.refresh_from_db()
        self.assertEqual(deuda.estado, CuentaPorPagar.Estado.PAGADA)
        tipos = sorted(MovimientoCajaCentral.objects.values_list("tipo", "monto"))
        self.assertEqual(
            tipos,
            [
                (MovimientoCajaCentral.Tipo.EGRESO_ADMIN, Decimal("4540.00")),
                (MovimientoCajaCentral.Tipo.EGRESO_PAGO, Decimal("80000.00")),
            ],
        )
        egreso = MovimientoCajaCentral.objects.get(tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN)
        self.assertEqual(egreso.rubro_operativo, self.rubro_verdura)
        self.assertEqual(egreso.sucursal_gasto, self.term)

    def test_lo_que_tesoreria_ya_pago_en_gerayse_ese_mes_no_se_repite(self):
        deuda = self._deuda(self.pare_carrito, self.term, "84540.00", date(2026, 7, 2), self.cat_verdura)
        register_cash_payment(payable=deuda, fecha_pago=date(2026, 7, 31), monto=Decimal("84540.00"), actor=self.admin)
        otra = self._deuda(self.pare_carrito, self.term, "50000.00", date(2026, 7, 2), self.cat_verdura)
        antes = MovimientoCajaCentral.objects.count()

        importador = self._importador(efectivo=self._efectivo("EC1", '3/07/2026,VERDURA,"$84.540,00",Efectivo'))
        self.assertEqual(self._acciones(importador), [(Accion.YA_EN_GERAYSE, "TERM-01", Decimal("84540.00"))])
        importador.aplicar()
        otra.refresh_from_db()
        self.assertEqual(otra.saldo_pendiente, Decimal("50000.00"))
        self.assertEqual(MovimientoCajaCentral.objects.count(), antes)

    def test_el_pago_registrado_el_mes_siguiente_tambien_cuenta_como_ya_cargado(self):
        # Los sueldos de julio se pagaron en Gerayse el 10 de agosto.
        vivre_emp = Empresa.objects.create(nombre="MAPOGO TEST")
        self.admin.empresas_permitidas.add(vivre_emp)
        vivre = Sucursal.objects.create(codigo="VIV-01", nombre="Vivre", razon_social="M", empresa=vivre_emp)
        RubroOperativo.objects.create(nombre="PERSONAL")
        # Hay un proveedor generico "SUELDOS" sin deudas: no tiene que ganarle
        # al de VIVRE, que es donde estan las deudas y los pagos.
        Proveedor.objects.create(razon_social="SUELDOS", creado_por=self.admin)
        sueldos_mapogo = Proveedor.objects.create(razon_social="MAPOGO SRL SUELDOS", creado_por=self.admin)
        deuda = self._deuda(sueldos_mapogo, vivre, "18000.00", date(2026, 7, 31))
        register_cash_payment(payable=deuda, fecha_pago=date(2026, 8, 10), monto=Decimal("18000.00"), actor=self.admin)

        importador = self._importador(
            efectivo=self._efectivo("VIVRE", '31/07/2026,SUELDOS,"$19.000,00",Efectivo')
        )
        self.assertEqual(
            self._acciones(importador),
            [
                (Accion.EGRESO, "VIV-01", Decimal("1000.00")),
                (Accion.YA_EN_GERAYSE, "VIV-01", Decimal("18000.00")),
            ],
        )

    def test_concepto_sin_rubro_reconocible_va_a_revisar(self):
        importador = self._importador(efectivo=self._efectivo("EC1", '31/08/2026,PABLO LEVIN DEUDA JUNIO,"$2.000,00",Efectivo'))
        self.assertEqual([op.accion for op in importador.operaciones], [Accion.REVISAR])

    # --- idempotencia y simulacion ---
    def test_planificar_no_escribe_nada(self):
        self._deuda(self.las_flores, self.term, "62500.00", date(2026, 7, 30))
        self._importador(
            banco=self._banco('01/08/2026,LAS FLORES,PAN,"62.500,00","62.500,00",,,,,,"62500,00",OK'),
            efectivo=self._efectivo("EC1", '3/07/2026,VERDURA,"$1.000,00",Efectivo'),
        )
        self.assertFalse(MovimientoBancario.objects.exists())
        self.assertFalse(MovimientoCajaCentral.objects.exists())
        self.assertFalse(PagoTesoreria.objects.exists())

    def test_correr_dos_veces_no_duplica(self):
        self._deuda(self.las_flores, self.term, "20000.00", date(2026, 7, 30))
        self._deuda(self.pare_carrito, self.term, "1000.00", date(2026, 7, 1), self.cat_verdura)
        banco = self._banco('01/08/2026,LAS FLORES,PAN,"50.000,00","50.000,00",,,,,,"50000,00",OK')
        efectivo = self._efectivo("EC1", '3/07/2026,VERDURA,"$5.000,00",Efectivo')
        self._importador(banco=banco, efectivo=efectivo).aplicar()
        conteo = (
            MovimientoBancario.objects.count(),
            MovimientoCajaCentral.objects.count(),
            PagoTesoreria.objects.count(),
        )

        segunda = self._importador(banco=banco, efectivo=efectivo)
        self.assertEqual({op.accion for op in segunda.operaciones}, {Accion.YA_IMPORTADA})
        segunda.aplicar()
        self.assertEqual(
            (
                MovimientoBancario.objects.count(),
                MovimientoCajaCentral.objects.count(),
                PagoTesoreria.objects.count(),
            ),
            conteo,
        )
