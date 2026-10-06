"""Importacion del banco desde el extracto del Macro, cruzado con el desglose de
transferencias de tesoreria, y lectura de las planillas en Excel.

El extracto es la verdad del banco: cada linea queda cargada, ya estaba, queda
afuera a proposito (cheques) o va a revisar. El desglose solo dice a que
proveedor y sucursal va cada debito.
"""

import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from cashops.models import Empresa, RubroOperativo, Sucursal
from treasury.extracto_macro import LineaExtracto, TipoCruce, cruzar_debitos, leer_extracto_macro, saldo_inicial
from treasury.importacion_planillas import (
    Accion,
    FilaBanco,
    Importador,
    leer_libro_efectivo,
    leer_reparto,
    parsear_monto,
    planilla_de_solapa,
    repartir,
)
from treasury.importacion_planillas import _periodo_de_transferencia
from treasury.lectura_xlsx import leer_xlsx
from treasury.models import (
    CategoriaCuentaPagar,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)

User = get_user_model()


def _col(indice: int) -> str:
    letras = ""
    indice += 1
    while indice:
        indice, resto = divmod(indice - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def crear_xlsx(ruta, hojas: dict):
    """Arma un .xlsx minimo: textos inline, numeros y fechas con estilo de fecha."""
    origen = date(1899, 12, 30)
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        hojas_xml, rels_xml = [], []
        for numero, (nombre, filas) in enumerate(hojas.items(), start=1):
            hojas_xml.append(f'<sheet name="{escape(nombre)}" sheetId="{numero}" r:id="rId{numero}"/>')
            rels_xml.append(
                f'<Relationship Id="rId{numero}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                f'relationships/worksheet" Target="worksheets/sheet{numero}.xml"/>'
            )
            filas_xml = []
            for nro_fila, valores in enumerate(filas, start=1):
                celdas = []
                for indice, valor in enumerate(valores):
                    ref = f"{_col(indice)}{nro_fila}"
                    if valor is None or valor == "":
                        continue
                    if isinstance(valor, date):
                        celdas.append(f'<c r="{ref}" s="1"><v>{(valor - origen).days}</v></c>')
                    elif isinstance(valor, (int, float, Decimal)):
                        celdas.append(f'<c r="{ref}"><v>{valor}</v></c>')
                    else:
                        celdas.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(valor))}</t></is></c>')
                filas_xml.append(f'<row r="{nro_fila}">{"".join(celdas)}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{numero}.xml",
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(filas_xml)}</sheetData></worksheet>',
            )
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{"".join(hojas_xml)}</sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'{"".join(rels_xml)}</Relationships>',
        )
        zf.writestr(
            "xl/styles.xml",
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
        )


def linea(orden, fecha, concepto, importe, referencia="1", causal="3862", saldo=Decimal("0.00")):
    return LineaExtracto(
        orden=orden,
        fecha=fecha,
        referencia=referencia,
        causal=causal,
        concepto=concepto,
        importe=Decimal(importe),
        saldo=saldo,
    )


class LecturaTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_montos_de_excel_y_de_tesoreria(self):
        self.assertEqual(parsear_monto("100000.0"), Decimal("100000.00"))
        self.assertEqual(parsear_monto("87874.979999999996"), Decimal("87874.98"))
        self.assertEqual(parsear_monto("100.000"), Decimal("100000.00"))
        self.assertEqual(parsear_monto("1.00"), Decimal("1.00"))
        self.assertEqual(parsear_monto(Decimal("12.5")), Decimal("12.50"))
        self.assertEqual(parsear_monto("-15048.60"), Decimal("-15048.60"))

    def test_lee_textos_numeros_y_fechas_del_xlsx(self):
        ruta = Path(self.tmp.name) / "libro.xlsx"
        crear_xlsx(ruta, {"Hoja A": [["Fecha", "Importe"], [date(2026, 8, 3), Decimal("62500.5")]]})
        (nombre, filas), = leer_xlsx(ruta)
        self.assertEqual(nombre, "Hoja A")
        self.assertEqual(filas[0][1], ["Fecha", "Importe"])
        self.assertEqual(filas[1][1], [date(2026, 8, 3), Decimal("62500.5")])

    def test_extracto_queda_en_orden_cronologico_y_cierra(self):
        # El PDF lista del mas nuevo al mas viejo, partido en tablas.
        ruta = Path(self.tmp.name) / "extracto.xlsx"
        crear_xlsx(
            ruta,
            {
                "Table 1": [
                    ["Fecha", "", "Nro. de Referencia", "Causal", "Concepto", "Importe", "Saldo"],
                    ["04/08/2026", "", 3, 1684, "DBCR 25413 S/DB TASA GRAL", Decimal("-10.00"), Decimal("1090.00")],
                ],
                "Table 2": [
                    ["Fecha", "Nro. de Referencia", "Causal", "Concepto", "", "Importe", "Saldo"],
                    ["03/08/2026", 2, 4098, "PAGO PCT Armadi Srl", 30717862453, Decimal("100.00"), Decimal("1100.00")],
                    ["03/08/2026", 1, 3862, "TRF MO CCDO DIST T", "", Decimal("-50.00"), Decimal("1000.00")],
                ],
            },
        )
        lineas = leer_extracto_macro(ruta)
        self.assertEqual([l.referencia for l in lineas], ["1", "2", "3"])
        self.assertEqual(saldo_inicial(lineas), Decimal("1050.00"))
        self.assertEqual(lineas[1].concepto, "PAGO PCT Armadi Srl 30717862453")

    def test_extracto_que_no_cierra_frena(self):
        ruta = Path(self.tmp.name) / "roto.xlsx"
        crear_xlsx(
            ruta,
            {
                "Table 1": [
                    ["04/08/2026", 3, 1684, "DBCR", Decimal("-10.00"), Decimal("999.00")],
                    ["03/08/2026", 1, 3862, "TRF", Decimal("-50.00"), Decimal("1000.00")],
                ]
            },
        )
        with self.assertRaises(ValidationError):
            leer_extracto_macro(ruta)

    def test_solapas_del_libro_de_efectivo(self):
        self.assertEqual(planilla_de_solapa("EC1 TERMINAL"), "EC1")
        self.assertEqual(planilla_de_solapa("VIVRÉ "), "VIVRE")
        self.assertEqual(planilla_de_solapa("YO HELADO"), "YH")
        self.assertEqual(planilla_de_solapa("PP OVEJA NEGRA"), "PP")
        self.assertEqual(planilla_de_solapa("EB1 "), "EB1")

    def test_reparto_suma_exacto(self):
        reparto = leer_reparto("EC1=32,YO=7,EC2=22,EB=39")
        self.assertEqual(list(reparto), ["TERM-01", "YH-05", "CENT-02", "EB1-03"])
        partes = repartir(Decimal("18.15"), reparto)
        self.assertEqual(sum(i for _c, i in partes), Decimal("18.15"))

    def test_el_sueldo_de_la_primera_quincena_es_del_mes_anterior_y_el_adelanto_no(self):
        casos = [
            (date(2026, 8, 4), "SUELDO COMPLETO CARLOS VAZQ", date(2026, 7, 1)),
            (date(2026, 8, 11), "SUELDO ADELANTO LILIANA", date(2026, 8, 1)),
            (date(2026, 8, 20), "SUELDO COMPLETO CARLOS VAZQ", date(2026, 8, 1)),
            (date(2026, 8, 4), "COSALTA", date(2026, 8, 1)),
        ]
        for fecha, denominacion, periodo in casos:
            self.assertEqual(_periodo_de_transferencia(fecha, denominacion), periodo, denominacion)


class CruceTests(SimpleTestCase):
    def fila(self, numero, fecha, denominacion, monto, **reparto):
        return FilaBanco(
            numero=numero,
            fecha=fecha,
            denominacion=denominacion,
            rubro="PAN",
            monto=Decimal(monto),
            reparto={k: Decimal(v) for k, v in reparto.items()},
        )

    def test_porciones_pagadas_juntas_y_fila_partida_por_el_banco(self):
        cosalta = self.fila(1, date(2026, 8, 1), "COSALTA", "196500", EB="91000", PP="105500")
        edesa = self.fila(2, date(2026, 8, 28), "EDESA", "813285.70", H="813285.70")
        lineas = [
            linea(0, date(2026, 8, 3), "TRF MO", "-196500"),
            linea(1, date(2026, 8, 28), "BIND*EDESA", "-549716.00"),
            linea(2, date(2026, 8, 28), "BIND*EDESA", "-263569.70"),
        ]
        resultado = cruzar_debitos(lineas, [cosalta, edesa])
        tipos = sorted(c.tipo for c in resultado.cruces)
        self.assertEqual(
            tipos, [TipoCruce.FILA_EN_VARIAS_LINEAS, TipoCruce.FILA_EN_VARIAS_LINEAS, TipoCruce.PORCIONES_JUNTAS]
        )
        self.assertFalse(resultado.porciones_sin_linea)
        self.assertFalse(resultado.lineas_sin_fila)

    def test_el_importe_que_se_repite_va_al_dia_mas_cercano(self):
        lunes = self.fila(1, date(2026, 8, 3), "LAS FLORES", "62500", EC1="62500")
        martes = self.fila(2, date(2026, 8, 4), "LAS FLORES", "62500", EC1="62500")
        lineas = [linea(0, date(2026, 8, 4), "TRF", "-62500"), linea(1, date(2026, 8, 3), "TRF", "-62500")]
        resultado = cruzar_debitos(lineas, [martes, lunes])
        por_fila = {c.asignaciones[0][0].fila.numero: c.linea.fecha for c in resultado.cruces}
        self.assertEqual(por_fila, {1: date(2026, 8, 3), 2: date(2026, 8, 4)})

    def test_error_de_tipeo_chico_manda_el_banco_y_grande_no_se_adivina(self):
        inbox = self.fila(1, date(2026, 8, 5), "INBOX", "351496.98", EC1="175748.49", EC2="175748.49")
        salta = self.fila(2, date(2026, 8, 1), "SALTA REFRESCOS", "224086.15", EC1="224086.15")
        lineas = [linea(0, date(2026, 8, 5), "TRANSF", "-351426.98"), linea(1, date(2026, 8, 3), "TRF", "-244086.15")]
        resultado = cruzar_debitos(lineas, [inbox, salta])
        (cruce,) = resultado.cruces
        self.assertEqual(cruce.tipo, TipoCruce.APROXIMADO)
        self.assertEqual(sum(i for _p, i in cruce.asignaciones), Decimal("351426.98"))
        self.assertEqual([p.fila.denominacion for p in resultado.porciones_sin_linea], ["SALTA REFRESCOS"])
        self.assertEqual([l.monto for l in resultado.lineas_sin_fila], [Decimal("244086.15")])


class ImportarDesdeExtractoTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin-ext", password="x", email="ext@test.com")
        self.empresa = Empresa.objects.create(nombre="ARMADI EXT")
        self.admin.empresas_permitidas.set([self.empresa])
        self.term = Sucursal.objects.create(codigo="TERM-01", nombre="Terminal", razon_social="A", empresa=self.empresa)
        self.cent = Sucursal.objects.create(codigo="CENT-02", nombre="Centro", razon_social="A", empresa=self.empresa)
        self.rubro_pan = RubroOperativo.objects.create(nombre="PAN")
        for nombre in (
            "VENTAS EN SUCURSAL",
            "IMPUESTOS RETENIDOS EN BANCO",
            "COMISIONES BANCO",
            "PERSONAL",
            "IMPUESTOS AFIP",
            "TARJETA DE CRÉDITO",
            "EMBARGO",
        ):
            RubroOperativo.objects.create(nombre=nombre)
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Pan", rubro_operativo=self.rubro_pan, creado_por=self.admin
        )
        self.las_flores = Proveedor.objects.create(razon_social="LAS FLORES", creado_por=self.admin)
        self.sueldos = Proveedor.objects.create(razon_social="SUELDOS", creado_por=self.admin)
        self.cuenta = CuentaBancaria.objects.create(
            nombre="Macro",
            banco="Macro",
            tipo_cuenta=CuentaBancaria.Tipo.CUENTA_CORRIENTE,
            numero_cuenta="9-9",
            empresa=self.empresa,
            creado_por=self.admin,
        )
        self.deuda = CuentaPorPagar.objects.create(
            proveedor=self.las_flores,
            categoria=self.categoria,
            concepto="Factura",
            fecha_emision=date(2026, 7, 31),
            fecha_vencimiento=date(2026, 7, 31),
            periodo_referencia=date(2026, 7, 1),
            importe_total=Decimal("62500.00"),
            saldo_pendiente=Decimal("62500.00"),
            sucursal=self.term,
            creado_por=self.admin,
        )
        self.lineas = [
            linea(0, date(2026, 8, 3), "PAGO PCT ARMADI SRL", "100.00", referencia="11", causal="4098"),
            linea(1, date(2026, 8, 3), "PAGO PCT ARMADI SRL", "250.00", referencia="12", causal="4098"),
            linea(2, date(2026, 8, 3), "TRF MO CCDO DIST T", "-62500.00", referencia="86840245"),
            linea(3, date(2026, 8, 3), "DBCR 25413 S/DB TASA GRAL", "-100.00", referencia="13", causal="1684"),
            linea(4, date(2026, 8, 4), "CHEQUE P/CAMARA", "-681235.00", referencia="14", causal="85"),
            linea(5, date(2026, 8, 6), "DB PAGO REMUNERACIONES", "-1000.00", referencia="15", causal="1693"),
            linea(6, date(2026, 8, 6), "TRF MO CCDO DIST T", "-777.00", referencia="16"),
        ]
        self.desglose = [
            FilaBanco(
                numero=4,
                fecha=date(2026, 8, 1),
                denominacion="LAS FLORES",
                rubro="PAN",
                monto=Decimal("62500.00"),
                reparto={"EC1": Decimal("62500.00")},
            )
        ]

    def _importador(self):
        importador = Importador(
            actor=self.admin,
            cuenta_banco=self.cuenta,
            repartos={
                "impuestos": leer_reparto("EC1=60,EC2=40"),
                "sueldos": leer_reparto("EC1=75,EC2=25"),
            },
        )
        importador.agregar_extracto(self.lineas)
        importador.agregar_banco(self.desglose)
        importador.planificar()
        return importador

    def test_cada_linea_del_extracto_queda_explicada(self):
        importador = self._importador()
        acciones = sorted((op.accion, op.concepto, op.monto) for op in importador.operaciones)
        self.assertIn((Accion.INGRESO, "Acreditación", Decimal("350.00")), acciones)
        self.assertIn((Accion.PAGAR_DEUDAS, "LAS FLORES", Decimal("62500.00")), acciones)
        self.assertIn((Accion.EGRESO, "DBCR S/DB", Decimal("60.00")), acciones)
        self.assertIn((Accion.EGRESO, "DBCR S/DB", Decimal("40.00")), acciones)
        self.assertIn((Accion.EXCLUIDA, "CHEQUE P/CAMARA", Decimal("681235.00")), acciones)
        self.assertIn((Accion.REVISAR, "TRF MO CCDO DIST T", Decimal("777.00")), acciones)
        conciliacion = importador.conciliacion_extracto()
        self.assertEqual(conciliacion["sin_explicar_creditos"], Decimal("0.00"))
        self.assertEqual(conciliacion["sin_explicar_debitos"], Decimal("0.00"))

    def test_afip_visa_y_embargos_fuera_del_desglose_van_con_la_clave_de_impuestos(self):
        self.lineas += [
            linea(7, date(2026, 8, 18), "AFIP", "-1000.00", referencia="17"),
            linea(8, date(2026, 8, 7), "DB TARJETA DE CREDITO VISA", "-500.00", referencia="18"),
            linea(9, date(2026, 8, 13), "camaraonlineembargo", "-200.00", referencia="19"),
        ]
        importador = self._importador()
        cargos = sorted(
            (op.rubro.nombre, op.accion, op.sucursal.codigo, op.monto)
            for op in importador.operaciones
            if op.rubro is not None and op.rubro.nombre in ("IMPUESTOS AFIP", "TARJETA DE CRÉDITO", "EMBARGO")
        )
        self.assertEqual(
            cargos,
            [
                ("EMBARGO", Accion.EGRESO, "CENT-02", Decimal("80.00")),
                ("EMBARGO", Accion.EGRESO, "TERM-01", Decimal("120.00")),
                ("IMPUESTOS AFIP", Accion.EGRESO, "CENT-02", Decimal("400.00")),
                ("IMPUESTOS AFIP", Accion.EGRESO, "TERM-01", Decimal("600.00")),
                ("TARJETA DE CRÉDITO", Accion.EGRESO, "CENT-02", Decimal("200.00")),
                ("TARJETA DE CRÉDITO", Accion.EGRESO, "TERM-01", Decimal("300.00")),
            ],
        )

    def test_aplicar_carga_el_banco_como_el_extracto_y_no_duplica(self):
        importador = self._importador()
        importador.aplicar()
        self.assertFalse([op for op in importador.operaciones if op.accion == Accion.ERROR])

        self.deuda.refresh_from_db()
        self.assertEqual(self.deuda.estado, CuentaPorPagar.Estado.PAGADA)
        acreditacion = MovimientoBancario.objects.get(tipo=MovimientoBancario.Tipo.CREDITO)
        self.assertEqual(acreditacion.clase, MovimientoBancario.Clase.ACREDITACION)
        self.assertEqual(acreditacion.monto, Decimal("350.00"))
        self.assertEqual(acreditacion.rubro_operativo.nombre, "VENTAS EN SUCURSAL")

        dbcr = MovimientoBancario.objects.filter(concepto="DBCR S/DB").order_by("sucursal_gasto__codigo")
        self.assertEqual(
            [(m.sucursal_gasto.codigo, m.monto, m.clase) for m in dbcr],
            [
                ("CENT-02", Decimal("40.00"), MovimientoBancario.Clase.IMPUESTO),
                ("TERM-01", Decimal("60.00"), MovimientoBancario.Clase.IMPUESTO),
            ],
        )
        sueldos = MovimientoBancario.objects.filter(concepto="DB Pago Remuneraciones")
        self.assertEqual(sum(m.monto for m in sueldos), Decimal("1000.00"))
        # Pagado el 6 de agosto: son los sueldos de julio.
        self.assertEqual({m.periodo_pago for m in sueldos}, {date(2026, 7, 1)})
        self.assertEqual({m.proveedor_id for m in sueldos}, {self.sueldos.pk})
        self.assertEqual({m.rubro_operativo.nombre for m in sueldos}, {"PERSONAL"})

        # El saldo del banco se mueve exactamente lo que dice el extracto, sin
        # el cheque (lo carga tesoreria aparte) ni la linea a revisar.
        creditos = sum(m.monto for m in MovimientoBancario.objects.filter(tipo="CREDITO"))
        debitos = sum(m.monto for m in MovimientoBancario.objects.filter(tipo="DEBITO"))
        self.assertEqual(creditos - debitos, Decimal("350.00") - Decimal("62500.00") - Decimal("100.00") - Decimal("1000.00"))

        conteo = (MovimientoBancario.objects.count(), PagoTesoreria.objects.count())
        segunda = self._importador()
        self.assertFalse([op for op in segunda.operaciones if op.accion in (Accion.PAGAR_DEUDAS, Accion.EGRESO, Accion.INGRESO)])
        segunda.aplicar()
        self.assertEqual((MovimientoBancario.objects.count(), PagoTesoreria.objects.count()), conteo)

    def test_sin_clave_de_reparto_el_cargo_va_a_revisar(self):
        importador = Importador(actor=self.admin, cuenta_banco=self.cuenta)
        importador.agregar_extracto([self.lineas[3]])
        importador.planificar()
        self.assertEqual([op.accion for op in importador.operaciones], [Accion.REVISAR])


class EfectivoFormatoNuevoTests(TestCase):
    """El libro de efectivo final trae proveedor y rubro por separado (a veces
    invertidos) y una solapa de Oveja Negra sin proveedor."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin-ef", password="x", email="ef@test.com")
        self.empresa = Empresa.objects.create(nombre="ARMADI EF")
        self.admin.empresas_permitidas.set([self.empresa])
        self.term = Sucursal.objects.create(codigo="TERM-01", nombre="Terminal", razon_social="A", empresa=self.empresa)
        self.pp = Sucursal.objects.create(codigo="PP-OV-06", nombre="Oveja", razon_social="A", empresa=self.empresa)
        self.verduras = RubroOperativo.objects.create(nombre="VERDURAS")
        self.varios = RubroOperativo.objects.create(nombre="VARIOS")
        self.categoria = CategoriaCuentaPagar.objects.create(
            nombre="Verdura", rubro_operativo=self.verduras, creado_por=self.admin
        )
        self.pare = Proveedor.objects.create(razon_social="PARE CARRITO", creado_por=self.admin)
        self.deuda_term = self._deuda(self.term)
        self.deuda_pp = self._deuda(self.pp)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _deuda(self, sucursal):
        return CuentaPorPagar.objects.create(
            proveedor=self.pare,
            categoria=self.categoria,
            concepto="Factura",
            fecha_emision=date(2026, 7, 1),
            fecha_vencimiento=date(2026, 7, 1),
            periodo_referencia=date(2026, 7, 1),
            importe_total=Decimal("1000.00"),
            saldo_pendiente=Decimal("1000.00"),
            sucursal=sucursal,
            creado_por=self.admin,
        )

    def test_columnas_invertidas_y_oveja_negra_sin_proveedor(self):
        ruta = Path(self.tmp.name) / "efectivo.xlsx"
        crear_xlsx(
            ruta,
            {
                "EC1 TERMINAL": [
                    ["Fecha", "Proveedor", "Rubro", "Cantidad", "Tipo de pago"],
                    [date(2026, 7, 3), "VERDURA", "PARE CARRITO", Decimal("1000.0"), "Efectivo"],
                ],
                "PP OVEJA NEGRA": [
                    ["MOVIEMIENTOS EN EFECTIVO CAJA FUERTE"],
                    ["Fecha", "Proveedor", "Cantidad", "Tipo de pago"],
                    [date(2026, 7, 5), "s/e", Decimal("150280"), "Efectivo"],
                    [date(2026, 7, 6), "PARE CARRITO", Decimal("1000"), "Efectivo"],
                ],
            },
        )
        filas = leer_libro_efectivo(ruta)
        importador = Importador(actor=self.admin)
        importador.agregar_efectivo(filas)
        importador.planificar()
        acciones = sorted((op.planilla, op.accion, op.monto, op.rubro.nombre if op.rubro else "") for op in importador.operaciones)
        self.assertEqual(
            acciones,
            [
                ("EC1", Accion.PAGAR_DEUDAS, Decimal("1000.00"), "VERDURAS"),
                ("PP", Accion.EGRESO, Decimal("1000.00"), "VERDURAS"),
                ("PP", Accion.EGRESO, Decimal("150280.00"), "VARIOS"),
            ],
        )
        importador.aplicar()
        self.deuda_term.refresh_from_db()
        self.deuda_pp.refresh_from_db()
        self.assertEqual(self.deuda_term.estado, CuentaPorPagar.Estado.PAGADA)
        # En Oveja Negra tesoreria no tiene el proveedor: no se pagan deudas.
        self.assertEqual(self.deuda_pp.saldo_pendiente, Decimal("1000.00"))
        self.assertEqual(
            MovimientoCajaCentral.objects.filter(tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN, sucursal_gasto=self.pp).count(),
            2,
        )

    def test_la_luz_de_ariel_va_a_yo_helados_aunque_se_anote_en_ec1(self):
        Sucursal.objects.create(codigo="YH-05", nombre="Yo Helados", razon_social="A", empresa=self.empresa)
        RubroOperativo.objects.create(nombre="ARIEL VARIOS")
        ruta = Path(self.tmp.name) / "efectivo.xlsx"
        crear_xlsx(
            ruta,
            {
                "EC1 TERMINAL": [
                    ["Fecha", "Proveedor", "Rubro", "Cantidad", "Tipo de pago"],
                    [date(2026, 8, 3), "EDESA ARIEL", "VARIOS", Decimal("156034"), "Efectivo"],
                ],
            },
        )
        importador = Importador(actor=self.admin)
        importador.agregar_efectivo(leer_libro_efectivo(ruta))
        importador.planificar()
        (op,) = importador.operaciones
        self.assertEqual((op.accion, op.sucursal.codigo, op.rubro.nombre), (Accion.EGRESO, "YH-05", "ARIEL VARIOS"))
        self.assertIsNone(op.proveedor)
