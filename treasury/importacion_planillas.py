"""Importacion de las planillas de tesoreria que se llevaban fuera de Gerayse.

Entradas (Excel o CSV):

- Extracto del banco (Ultimos movimientos del Macro, convertido del PDF). Si
  viene, el banco se carga linea por linea del extracto: es la verdad del banco.
- Desglose de transferencias (pestana "MOV AGO 26"): una fila por pago, con el
  total y el reparto por sucursal en columnas (EC1, EC2, EB, EB2, PP, H). Dice
  a que proveedor, rubro y sucursal va cada debito del extracto. Sin extracto,
  el banco se carga desde estas filas (modo anterior).
- Efectivo central ("egresos pendientes de efectuar"): una solapa por sucursal
  con fecha, proveedor, rubro e importe de lo que tesoreria pago en efectivo.

Casi todo lo que sale en esas planillas ya esta en Gerayse del otro lado: los
cajeros cargan las facturas como deuda. Por eso importar NO es cargar gasto.
Cuando hay deuda abierta del proveedor en la sucursal, el pago la paga, y el
gasto sigue siendo el de la deuda. Solo lo que no tiene deuda entra como egreso
con rubro, sucursal y periodo. Cargarlo todo como gasto duplicaba el resultado
economico y dejaba las deudas abiertas.

Que facturas paga un pago: primero una que coincida justo con el importe; si
no hay, las mas recientes anteriores a la fecha del pago. Tesoreria paga la
cuenta corriente de la ultima semana, no la factura mas vieja.

Flujo en dos pasos. `Importador.planificar()` no escribe nada: arma la lista
de operaciones con su motivo, que sale en el informe para revisar.
`Importador.aplicar()` ejecuta cada operacion por los servicios de siempre, con
un token de alta deterministico por fila, asi que correrlo dos veces no duplica.
"""

from __future__ import annotations

import csv
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from cashops.models import RubroOperativo, Sucursal
from treasury.extracto_macro import (
    Categoria,
    LineaExtracto,
    TipoCruce,
    categoria,
    cruzar_debitos,
    saldo_inicial,
)
from treasury.importacion_texto import CENTAVO, dinero, normalizar, parsear_fecha, parsear_monto, texto_de_celda
from treasury.lectura_xlsx import leer_xlsx
from treasury.models import (
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import (
    create_bank_movement,
    pay_debts_from_bank_movement,
    register_cash_payment,
    register_egreso_tesoreria,
)

# Marca de todo lo que escribe el importador (observaciones del movimiento o
# pago). Sirve para no confundir lo importado con lo que tesoreria ya cargo.
PREFIJO_OBSERVACIONES = "Importado de planilla"
# Diferencias de redondeo de la planilla (el reparto suma 1 centavo de mas o de
# menos). Por encima de esto la fila no cuadra y va a revisar.
TOLERANCIA = Decimal("0.05")

# Namespace fijo: el token de cada operacion sale de la fila, asi que la misma
# fila produce el mismo token en cualquier corrida.
NAMESPACE_IMPORTACION = uuid.UUID("5b0e7c52-8f6d-4a5e-9a51-6f2c3d1e7a90")

# Columnas de la planilla -> codigo de sucursal en Gerayse.
COLUMNA_A_SUCURSAL = {
    "EC1": "TERM-01",
    "EC2": "CENT-02",
    "EB": "EB1-03",
    "EB1": "EB1-03",
    "EB2": "EB2-04",
    "PP": "PP-OV-06",
    "H": "YH-05",
    "YH": "YH-05",
    "YO": "YH-05",
    "VIVRE": "VIV-01",
}
COLUMNAS_REPARTO_BANCO = ("EC1", "EC2", "EB", "EB2", "PP", "H")
SUCURSAL_OVEJA_NEGRA = "PP-OV-06"
PLANILLA_OVEJA_NEGRA = "PP"
# Gastos personales de Ariel (socio): tesoreria los imputa a Yo Helados.
SUCURSAL_GASTOS_ARIEL = "YH-05"
RUBRO_GASTOS_ARIEL = "ARIEL VARIOS"

# Rubro de la planilla de banco -> rubro de Gerayse. Lo que no esta se busca
# por el mismo nombre.
RUBRO_BANCO = {
    "BEBIDAS S ALCOHOL": "BEBIDAS SIN ALCOHOL",
    "CAFE": "CAFE/TE/AZUCAR/LECHE",
    "CERVEZAS": "CERVEZA",
    "DESCARTABLES": "DESCARTABLE",
    "SUELDOS": "PERSONAL",
    "IMPUESTOS": "IMPUESTOS AFIP",
    "VERDURA": "VERDURAS",
}

# Texto de la planilla de efectivo -> rubro. Se aplica a la columna RUBRO y, si
# no da, al proveedor (a veces las dos columnas estan invertidas, y las
# planillas viejas no tienen rubro: el concepto ES el rubro). Primera
# coincidencia gana, por eso RETIRO y los sueldos van antes que el resto.
RUBRO_EFECTIVO = (
    (r"RETIRO", "RETIRO SOCIOS"),
    (r"SUELDO|\bSAC\b|ADELANTO|\bVAC\b|LUCHI|GASTOS MEDICOS|LIQ FINAL|LIQUIDACION", "PERSONAL"),
    (r"REINVERS|UNIFORME", "REINVERSION"),
    (r"ALQUI?L?E?R|DEPO ALQ", "ALQUILER"),
    (r"VERDURA|PARE CARRITO", "VERDURAS"),
    (r"\bPO ?L+O\b|CRASH|AMIBUE", "POLLO"),
    (r"AL ?MACE|S?C?ICILIANA|SCILIANA", "ALMACEN"),
    (r"FIAMBRE", "FIAMBRES Y LACTEOS"),
    (r"CARNE|BRUNETTI", "CARNE"),
    (r"CAFE|KENYA|CAXAMB", "CAFE/TE/AZUCAR/LECHE"),
    (r"^PAN$|CINCO MARIAS", "PAN"),
    (r"BEBIDAS|SALTA REFRESCOS", "BEBIDAS SIN ALCOHOL"),
    (r"DESCARTABLE|PETIT PLAST", "DESCARTABLE"),
    (r"EDESA|GASNOR|SERVICIO|AQUALAND|SISTEMA|REMIS|GERAYSE", "SERVICIOS"),
    (r"MANTENIMI|EXTRACTOR|ARREGLO|CHAPA|MESAS", "MANTENIMIENTO"),
    (r"^VARIOS", "VARIOS"),
    (r"IMPUEST|^IVA |IMPUESTOS IVA", "IMPUESTOS AFIP"),
)
# Lo que tesoreria anota en Oveja Negra sin proveedor ("S/E") va a VARIOS:
# pedido de tesoreria, porque antes se cargaba solo como gasto de panaderia.
RUBRO_SIN_ESPECIFICAR = "VARIOS"

# Nombre en la planilla -> razon social en Gerayse (todo normalizado).
ALIAS_PROVEEDOR = {
    "DISTRIBUIDORA BADIE": "BADIE",
    "NICO HONGOS": "NICOLAS HONGOS",
    "13 HNOS": "13 HERMANOS",
    "5 MARIAS": "CINCO MARIAS",
    "GLATSTEIN": "RUBEN GLAINSTEN",
    "LUISINA TIMO SORRENTINOS": "LUISINA TIMO",
    "MICBEL TF": "MICBEL",
    "PANES DEL MUNDO SEM 1 Y 2": "PANES DEL MUNDO",
    "OLIMPO 24 07": "OLIMPO",
    "GASNOR EFECTIVO": "GASNOR",
    "KENYA CAFE": "CAFE KENYA",
    "KENYA": "CAFE KENYA",
    "CAXAMBU": "CAFE CAXAMBU",
    "BRUNETTI CARNE": "BRUNETTI",
    "ALMACEN LA SCILIANA": "SICILIANA",
    "ALMACE LA SCILIANA": "SICILIANA",
    "CRASH AMIBUE": "CRASH POLLO",
    "AMIBUE CRASH": "CRASH POLLO",
    "OVEJA NEGRA": "OVEJA NEGRA ARMADI SRL",
}

# En efectivo el concepto suele ser el rubro, no el proveedor. Estos rubros
# tienen un solo proveedor que los cajeros cargan como deuda; los demas
# ("ALMACEN", "FIAMBRES") tienen varios y quedan como egreso con aviso.
PROVEEDOR_INFERIDO_EFECTIVO = (
    (r"VERDURA", "PARE CARRITO", None),
    (r"POLL+O", "CRASH POLLO", None),
    (r"^CAFE$", "CAFE KENYA", None),
    (r"^CARNE$", "BRUNETTI", None),
    # Los sueldos de VIVRE se cargan como deuda del proveedor "MAPOGO SRL SUELDOS".
    (r"SUELDO|\bSAC\b", "MAPOGO SRL SUELDOS", "VIV-01"),
    (r"^ALQUILER$", "PIEVE ALEJ ALQUILER", "VIV-01"),
)

# Debitos del extracto que no estan en el desglose pero se sabe que son: se
# reparten entre sucursales con la clave de reparto que corresponda (la de
# impuestos o la de sueldos), igual que tesoreria reparte el 931 o la TISSH.
# (patron, clave de reparto, clase, rubro, proveedor)
CARGOS_SIN_DESGLOSE = (
    (r"DBCR", "impuestos", MovimientoBancario.Clase.IMPUESTO, "IMPUESTOS RETENIDOS EN BANCO", None),
    (r"DEBITO FISCAL|PERCEPCION|\bIVA\b", "impuestos", MovimientoBancario.Clase.IMPUESTO, "IMPUESTOS RETENIDOS EN BANCO", None),
    (r"COMISION", "impuestos", MovimientoBancario.Clase.COMISION_BANCARIA, "COMISIONES BANCO", None),
    (r"MANTENIMIENTO MENSUAL", "impuestos", MovimientoBancario.Clase.COMISION_BANCARIA, "COMISIONES BANCO", None),
    (r"MUNIC", "impuestos", MovimientoBancario.Clase.IMPUESTO, "IMPUESTOS AFIP", None),
    # Tesoreria reparte con la misma clave todos los impuestos: 931, IVA, tasas.
    (r"^(IMP\.? )?AFIP\b", "impuestos", MovimientoBancario.Clase.IMPUESTO, "IMPUESTOS AFIP", None),
    # La tarjeta Visa y los embargos tambien van con la clave de impuestos.
    (r"TARJETA DE CREDITO", "impuestos", MovimientoBancario.Clase.OTRO_EGRESO, "TARJETA DE CRÉDITO", None),
    (r"EMBARGO", "impuestos", MovimientoBancario.Clase.OTRO_EGRESO, "EMBARGO", None),
    (r"LIQ COMER", "impuestos", MovimientoBancario.Clase.OTRO_EGRESO, "CONTRACARGOS Y DEVOLUCIONES PAYWAY", None),
    (r"REMUNERACION", "sueldos", MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS, "PERSONAL", "SUELDOS"),
)
# Los sueldos que se pagan en la primera quincena son del mes anterior.
DIA_CORTE_SUELDOS = 15


class Accion:
    PAGAR_DEUDAS = "PAGAR_DEUDAS"
    EGRESO = "EGRESO"
    INGRESO = "INGRESO"
    YA_EN_GERAYSE = "YA_EN_GERAYSE"
    YA_IMPORTADA = "YA_IMPORTADA"
    EXCLUIDA = "EXCLUIDA"
    SIN_EXTRACTO = "SIN_EXTRACTO"
    REVISAR = "REVISAR"
    ERROR = "ERROR"


ACCIONES_QUE_ESCRIBEN = {Accion.PAGAR_DEUDAS, Accion.EGRESO, Accion.INGRESO}


def primer_dia(fecha: date) -> date:
    return fecha.replace(day=1)


def _mes_anterior(fecha: date) -> date:
    mes = primer_dia(fecha)
    return date(mes.year - 1, 12, 1) if mes.month == 1 else date(mes.year, mes.month - 1, 1)


def _periodo_de_transferencia(fecha: date, denominacion: str) -> date:
    """Mes economico de una fila del desglose. Un sueldo pagado en la primera
    quincena es del mes anterior, como lo imputa tesoreria; un adelanto no."""
    texto = normalizar(denominacion)
    if fecha.day <= DIA_CORTE_SUELDOS and re.search(r"SUELDO", texto) and not re.search(r"ADELANTO", texto):
        return _mes_anterior(fecha)
    return primer_dia(fecha)


def _meses_vecinos(fecha: date) -> list[date]:
    """El mes de la fecha, el anterior y el siguiente, en ese orden."""
    mes = primer_dia(fecha)
    siguiente = date(mes.year + 1, 1, 1) if mes.month == 12 else date(mes.year, mes.month + 1, 1)
    return [mes, _mes_anterior(fecha), siguiente]


def repartir(monto: Decimal, porcentajes: dict) -> list[tuple[str, Decimal]]:
    """Reparte un importe segun porcentajes ({codigo: peso}), redondeando a
    centavos; la ultima parte se lleva el redondeo para que sumen exacto."""
    total_pesos = sum(porcentajes.values(), Decimal("0"))
    partes = []
    asignado = Decimal("0.00")
    items = list(porcentajes.items())
    for indice, (codigo, peso) in enumerate(items):
        if indice == len(items) - 1:
            importe = monto - asignado
        else:
            importe = (monto * peso / total_pesos).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        asignado += importe
        if importe:
            partes.append((codigo, importe))
    return partes


@dataclass
class FilaBanco:
    numero: int
    fecha: date
    denominacion: str
    rubro: str
    monto: Decimal
    reparto: dict  # columna -> Decimal


@dataclass
class FilaEfectivo:
    planilla: str  # EC1, EC2, EB1, EB2, VIVRE, YH, PP
    numero: int
    fecha: date
    concepto: str  # columna proveedor (o concepto en las planillas viejas)
    monto: Decimal
    rubro_texto: str = ""  # columna rubro, si la planilla la tiene


@dataclass
class Operacion:
    origen: str  # BANCO | EFECTIVO
    planilla: str
    fila: int
    clave: str
    fecha: date
    concepto: str
    monto: Decimal
    accion: str
    parte: str = ""
    sucursal: Sucursal | None = None
    proveedor: Proveedor | None = None
    rubro: RubroOperativo | None = None
    periodo: date | None = None
    asignaciones: list = field(default_factory=list)  # [(CuentaPorPagar, Decimal)]
    motivo: str = ""
    aviso: str = ""
    resultado: str = ""
    # Banco desde el extracto
    tipo_movimiento: str = MovimientoBancario.Tipo.DEBITO
    clase: str = ""  # explicita para cargos e ingresos del extracto
    referencia: str = ""
    extracto: str = ""  # la linea del banco que lo respalda, para el informe

    def token(self, sufijo: str = "") -> uuid.UUID:
        return uuid.uuid5(NAMESPACE_IMPORTACION, f"{self.clave}|{self.parte}{sufijo}")


# --- lectura -------------------------------------------------------------------
def _filas_de_archivo(ruta) -> list[tuple[str, list[tuple[int, list]]]]:
    """[(hoja, [(numero, valores)])] de un .xlsx o de un CSV (una sola hoja)."""
    ruta = Path(ruta)
    if ruta.suffix.lower() in (".xlsx", ".xlsm"):
        return leer_xlsx(ruta)
    with open(ruta, encoding="utf-8", newline="") as archivo:
        filas = [(numero, fila) for numero, fila in enumerate(csv.reader(archivo), start=1)]
    return [(ruta.stem, filas)]


def _buscar_cabecera(filas, requeridas) -> tuple[int, list[str]] | None:
    for indice, (_numero, valores) in enumerate(filas):
        nombres = [normalizar(v) if isinstance(v, str) else "" for v in valores]
        if all(any(re.fullmatch(r, n) for n in nombres) for r in requeridas):
            return indice, nombres
    return None


def _celda(valores, idx):
    return valores[idx] if idx is not None and idx < len(valores) else ""


def leer_planilla_banco(ruta) -> list[FilaBanco]:
    """Desglose de transferencias (Excel o CSV). La cabecera se busca (la
    planilla trae filas de titulo arriba) y las columnas se toman por nombre."""
    for _hoja, filas in _filas_de_archivo(ruta):
        encontrada = _buscar_cabecera(filas, [r"FECHA", r"MONTO"])
        if encontrada is None:
            continue
        indice, cabecera = encontrada

        def columna(nombre):
            return cabecera.index(nombre) if nombre in cabecera else None

        idx_fecha, idx_monto = columna("FECHA"), columna("MONTO")
        idx_denominacion, idx_rubro = columna("DENOMINACION"), columna("RUBRO")
        idx_reparto = {c: columna(c) for c in COLUMNAS_REPARTO_BANCO if columna(c) is not None}
        resultado = []
        for numero, valores in filas[indice + 1 :]:
            fecha = parsear_fecha(_celda(valores, idx_fecha))
            monto = parsear_monto(_celda(valores, idx_monto))
            if fecha is None or monto is None:
                continue
            reparto = {}
            for col, idx in idx_reparto.items():
                valor = parsear_monto(_celda(valores, idx))
                if valor:
                    reparto[col] = valor
            resultado.append(
                FilaBanco(
                    numero=numero,
                    fecha=fecha,
                    denominacion=texto_de_celda(_celda(valores, idx_denominacion)),
                    rubro=texto_de_celda(_celda(valores, idx_rubro)),
                    monto=monto,
                    reparto=reparto,
                )
            )
        return resultado
    raise ValidationError(f"{ruta}: no encuentro la fila de cabecera con FECHA y MONTO.")


def _filas_efectivo(filas, planilla: str) -> list[FilaEfectivo]:
    """Filas de una solapa de efectivo. Con cabecera, las columnas se toman por
    nombre (PROVEEDOR, RUBRO, CANTIDAD; tesoreria escribio "PROVEEDIR" en una).
    Sin cabecera, el formato viejo: fecha, concepto, importe."""
    encontrada = _buscar_cabecera(filas, [r"FECHA", r"CANTIDAD|IMPORTE|MONTO"])
    if encontrada is None:
        idx_fecha, idx_concepto, idx_rubro, idx_monto, desde = 0, 1, None, 2, 0
    else:
        indice, cabecera = encontrada

        def columna(*patrones):
            for i, nombre in enumerate(cabecera):
                if any(re.fullmatch(p, nombre) for p in patrones):
                    return i
            return None

        idx_fecha = columna(r"FECHA")
        idx_concepto = columna(r"PROVEED\w*", r"CONCEPTO", r"DETALLE")
        idx_rubro = columna(r"RUBRO")
        idx_monto = columna(r"CANTIDAD", r"IMPORTE", r"MONTO")
        desde = indice + 1
    resultado = []
    for numero, valores in filas[desde:]:
        fecha = parsear_fecha(_celda(valores, idx_fecha))
        monto = parsear_monto(_celda(valores, idx_monto))
        if fecha is None or monto is None:
            continue
        resultado.append(
            FilaEfectivo(
                planilla=planilla,
                numero=numero,
                fecha=fecha,
                concepto=texto_de_celda(_celda(valores, idx_concepto)),
                monto=monto,
                rubro_texto=texto_de_celda(_celda(valores, idx_rubro)),
            )
        )
    return resultado


def leer_planilla_efectivo(ruta, planilla: str) -> list[FilaEfectivo]:
    filas = _filas_de_archivo(ruta)[0][1]
    return _filas_efectivo(filas, planilla)


def planilla_de_solapa(nombre: str) -> str | None:
    """Nombre de la solapa del libro de efectivo -> planilla (EC1, YH, PP...)."""
    texto = normalizar(nombre)
    for patron, planilla in (
        (r"\bEC1\b|TERMINAL", "EC1"),
        (r"\bEC2\b|CENTRO", "EC2"),
        (r"\bEB1\b", "EB1"),
        (r"\bEB2\b", "EB2"),
        (r"VIVRE", "VIVRE"),
        (r"YO HELADO|^YH\b|HELADO", "YH"),
        (r"\bPP\b|OVEJA", "PP"),
    ):
        if re.search(patron, texto):
            return planilla
    return None


def leer_libro_efectivo(ruta) -> list[FilaEfectivo]:
    """Libro de efectivo con una solapa por sucursal."""
    resultado = []
    for nombre, filas in leer_xlsx(ruta):
        planilla = planilla_de_solapa(nombre)
        if planilla is None:
            raise ValidationError(f"Solapa '{nombre}': no se a que sucursal corresponde.")
        resultado.extend(_filas_efectivo(filas, planilla))
    return resultado


def filas_efectivo_de_directorio(directorio) -> list[FilaEfectivo]:
    """Lee `efectivo_<PLANILLA>.csv` (EC1, EC2, EB1, EB2, PP, VIVRE, YH)."""
    filas = []
    for ruta in sorted(Path(directorio).glob("efectivo_*.csv")):
        planilla = ruta.stem.split("_", 1)[1].upper()
        if planilla not in COLUMNA_A_SUCURSAL:
            raise ValidationError(f"{ruta.name}: no se a que sucursal corresponde '{planilla}'.")
        filas.extend(leer_planilla_efectivo(ruta, planilla))
    return filas


def leer_reparto(texto: str) -> dict:
    """"EC1=32,YO=7,EC2=22,EB=39" -> {codigo_sucursal: Decimal}. Acepta las
    columnas de la planilla (EC1, EB, H...) o los codigos de Gerayse."""
    reparto = {}
    for parte in (texto or "").split(","):
        if not parte.strip():
            continue
        nombre, _, valor = parte.partition("=")
        nombre = nombre.strip().upper()
        codigo = COLUMNA_A_SUCURSAL.get(nombre, nombre)
        peso = parsear_monto(valor)
        if peso is None or peso <= 0:
            raise ValidationError(f"Reparto invalido: '{parte}'.")
        reparto[codigo] = peso
    return reparto


def _claves_estables(prefijo: str, filas, firma) -> list[str]:
    """Identidad de cada fila que no depende del numero de linea: si Tais
    inserta una fila en el medio, las demas conservan su token. Dos filas
    identicas (mismo dia, concepto e importe) se distinguen por su orden."""
    vistas = defaultdict(int)
    claves = []
    for fila in filas:
        base = f"{prefijo}|{firma(fila)}"
        vistas[base] += 1
        claves.append(f"{base}|{vistas[base]}")
    return claves


def _es_echeq(denominacion: str) -> bool:
    return bool(re.search(r"\bE?CHEQ\b", normalizar(denominacion)))


def _cuit_de(concepto: str) -> str | None:
    match = re.search(r"TRANSF\s+(\d{11})\b", normalizar(concepto))
    return match.group(1) if match else None


class Importador:
    def __init__(
        self,
        *,
        actor,
        cuenta_banco: CuentaBancaria | None = None,
        etiqueta_banco: str = "banco",
        repartos: dict | None = None,
    ):
        self.actor = actor
        self.cuenta_banco = cuenta_banco
        self.etiqueta_banco = etiqueta_banco
        self.repartos = repartos or {}
        self.filas_banco: list[FilaBanco] = []
        self.filas_efectivo: list[FilaEfectivo] = []
        self.extracto: list[LineaExtracto] = []
        self.operaciones: list[Operacion] = []

    # --- carga -------------------------------------------------------------
    def agregar_banco(self, filas: list[FilaBanco]):
        self.filas_banco.extend(filas)

    def agregar_efectivo(self, filas: list[FilaEfectivo]):
        self.filas_efectivo.extend(filas)

    def agregar_extracto(self, lineas: list[LineaExtracto]):
        self.extracto.extend(lineas)

    # --- catalogos ----------------------------------------------------------
    def _cargar_catalogos(self):
        self.sucursales = {s.codigo.upper(): s for s in Sucursal.objects.select_related("empresa")}
        self.rubros = {
            normalizar(r.nombre): r for r in RubroOperativo.objects.filter(activo=True, es_sistema=False)
        }
        self.proveedores = {}
        for proveedor in Proveedor.objects.filter(activo=True).order_by("pk"):
            self.proveedores.setdefault(normalizar(proveedor.razon_social), proveedor)

        # Saldo de cada deuda abierta, que el plan va descontando a medida que
        # asigna pagos: dos filas no pueden pagar la misma plata dos veces.
        self.deudas_por_clave = defaultdict(list)
        self.saldo = {}
        abiertas = (
            CuentaPorPagar.objects.filter(
                estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL],
                saldo_pendiente__gt=0,
                compromiso_especial__isnull=True,
            )
            .select_related("proveedor", "sucursal", "categoria__rubro_operativo")
            .order_by("fecha_emision", "pk")
        )
        for deuda in abiertas:
            self.deudas_por_clave[(deuda.proveedor_id, deuda.sucursal_id)].append(deuda)
            self.saldo[deuda.pk] = deuda.saldo_pendiente

        # Pagos en efectivo que tesoreria ya registro en Gerayse, por proveedor,
        # sucursal de la deuda y mes: la planilla de efectivo los repite.
        # Clave (proveedor, sucursal, mes del pago en Gerayse).
        self.efectivo_registrado = defaultdict(Decimal)
        pagos = PagoTesoreria.objects.filter(
            medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
            estado=PagoTesoreria.Estado.REGISTRADO,
        ).select_related("cuenta_por_pagar")
        for pago in pagos:
            if pago.observaciones.startswith(PREFIJO_OBSERVACIONES):
                # Lo pago una corrida anterior del importador: no es algo que
                # tesoreria ya habia cargado, es la importacion de otra fila.
                # Contarlo haria que una fila que falta se de por cargada.
                continue
            deuda = pago.cuenta_por_pagar
            clave = (deuda.proveedor_id, deuda.sucursal_id, primer_dia(pago.fecha_pago))
            self.efectivo_registrado[clave] += pago.monto
        self.efectivo_registrado_inicial = dict(self.efectivo_registrado)

        # Movimientos que ya estan en la cuenta: la planilla o el extracto
        # pueden repetir alguno que tesoreria cargo a mano.
        self.debitos_existentes = defaultdict(int)  # (fecha, monto, sucursal)
        self.movimientos_existentes = Counter()  # (tipo, fecha, monto)
        if self.cuenta_banco is not None:
            for mov in MovimientoBancario.objects.filter(
                cuenta_bancaria=self.cuenta_banco,
                estado=MovimientoBancario.Estado.REGISTRADO,
            ).only("tipo", "fecha", "monto", "sucursal_gasto_id"):
                self.movimientos_existentes[(mov.tipo, mov.fecha, mov.monto)] += 1
                if mov.tipo == MovimientoBancario.Tipo.DEBITO:
                    self.debitos_existentes[(mov.fecha, mov.monto, mov.sucursal_gasto_id)] += 1

    def _empresa_de_la_cuenta(self):
        """La empresa de la cuenta, o la de las sucursales del reparto si la
        cuenta es legacy y no la tiene cargada (la de ARMADI no la tiene).
        Si las columnas apuntan a mas de una empresa no se adivina: queda None
        y la planilla sin reparto va a revisar."""
        if self.cuenta_banco is None:
            return None
        if self.cuenta_banco.empresa_id:
            return self.cuenta_banco.empresa_id
        empresas = {
            self.sucursales[COLUMNA_A_SUCURSAL[col]].empresa_id
            for fila in self.filas_banco
            for col in fila.reparto
            if COLUMNA_A_SUCURSAL[col] in self.sucursales
        }
        return empresas.pop() if len(empresas) == 1 else None

    def _proveedor(self, nombre: str) -> Proveedor | None:
        clave = normalizar(nombre)
        clave = ALIAS_PROVEEDOR.get(clave, clave)
        return self.proveedores.get(clave)

    def _rubro(self, nombre: str) -> RubroOperativo | None:
        return self.rubros.get(normalizar(nombre)) if nombre else None

    def _rubro_banco(self, rubro_planilla: str) -> RubroOperativo | None:
        clave = normalizar(rubro_planilla)
        return self._rubro(RUBRO_BANCO.get(clave, clave))

    def _rubro_de_texto(self, texto: str) -> RubroOperativo | None:
        if not texto:
            return None
        exacto = self._rubro(texto)
        if exacto is not None:
            return exacto
        normal = normalizar(texto)
        for patron, rubro in RUBRO_EFECTIVO:
            if re.search(patron, normal):
                return self._rubro(rubro)
        return None

    def _rubro_efectivo(self, concepto: str, rubro_texto: str = "") -> RubroOperativo | None:
        # Primero la columna rubro; si no dice nada util, el concepto (en las
        # filas con las columnas invertidas, el rubro quedo en "proveedor").
        return self._rubro_de_texto(rubro_texto) or self._rubro_de_texto(concepto)

    def _proveedor_inferido(self, texto: str, sucursal: Sucursal) -> Proveedor | None:
        # La regla gana al nombre directo: en Gerayse hay un proveedor
        # "SUELDOS" sin deudas, y los sueldos de VIVRE son del proveedor
        # "MAPOGO SRL SUELDOS", que es donde estan sus deudas y sus pagos.
        normal = normalizar(texto)
        for patron, nombre, solo_sucursal in PROVEEDOR_INFERIDO_EFECTIVO:
            if solo_sucursal and sucursal.codigo.upper() != solo_sucursal:
                continue
            if re.search(patron, normal):
                inferido = self.proveedores.get(normalizar(nombre))
                if inferido is not None:
                    return inferido
        return self._proveedor(texto)

    def _proveedor_efectivo(self, concepto: str, sucursal: Sucursal, rubro_texto: str = "") -> Proveedor | None:
        return self._proveedor_inferido(concepto, sucursal) or (
            self._proveedor_inferido(rubro_texto, sucursal) if rubro_texto else None
        )

    # --- asignacion de deudas ---------------------------------------------------
    def _elegir_deudas(self, proveedor, sucursal, fecha, monto, empresa_id=None):
        """Devuelve [(deuda, importe)] y lo que no alcanzo a cubrir.

        Una deuda que coincide justo con el importe gana. Si no, se toman las
        facturas mas recientes anteriores al pago, hacia atras. Con sucursal
        None se buscan en todas las sucursales de la empresa (filas sin
        reparto en la planilla).
        """
        if sucursal is not None:
            candidatas = list(self.deudas_por_clave.get((proveedor.pk, sucursal.pk), []))
        else:
            candidatas = [
                deuda
                for (prov_id, _suc), deudas in self.deudas_por_clave.items()
                if prov_id == proveedor.pk
                for deuda in deudas
                if deuda.sucursal_id and deuda.sucursal.empresa_id == empresa_id
            ]
        candidatas = [d for d in candidatas if d.fecha_emision <= fecha and self.saldo[d.pk] > 0]
        exactas = [d for d in candidatas if abs(self.saldo[d.pk] - monto) <= CENTAVO]
        if exactas:
            elegida = max(exactas, key=lambda d: (d.fecha_emision, d.pk))
            importe = min(monto, self.saldo[elegida.pk])
            self.saldo[elegida.pk] -= importe
            return [(elegida, importe)], monto - importe

        asignaciones = []
        resto = monto
        for deuda in sorted(candidatas, key=lambda d: (d.fecha_emision, d.pk), reverse=True):
            if resto <= 0:
                break
            importe = min(resto, self.saldo[deuda.pk])
            asignaciones.append((deuda, importe))
            self.saldo[deuda.pk] -= importe
            resto -= importe
        return asignaciones, resto

    # --- planificacion ------------------------------------------------------
    def planificar(self) -> list[Operacion]:
        self._cargar_catalogos()
        self.operaciones = []
        self.empresa_banco_id = None
        self.empresa_banco_id = self._empresa_de_la_cuenta()
        claves_efectivo = _claves_estables(
            "EFECTIVO",
            self.filas_efectivo,
            lambda f: f"{f.planilla}|{f.fecha:%Y-%m-%d}|{normalizar(f.concepto)}|{f.monto}",
        )
        # Orden cronologico entre banco y efectivo: una deuda la paga el pago
        # que ocurrio primero, sea por banco o en efectivo.
        eventos = [(f.fecha, 1, "EFECTIVO", f, c) for f, c in zip(self.filas_efectivo, claves_efectivo)]
        if self.extracto:
            eventos += self._eventos_extracto()
        else:
            claves_banco = _claves_estables(
                f"BANCO|{self.etiqueta_banco}",
                self.filas_banco,
                lambda f: f"{f.fecha:%Y-%m-%d}|{normalizar(f.denominacion)}|{f.monto}",
            )
            eventos += [(f.fecha, 0, "BANCO", f, c) for f, c in zip(self.filas_banco, claves_banco)]
        eventos.sort(key=lambda e: (e[0], e[1], e[2], self._orden_evento(e)))
        self._cargar_tokens_existentes([e[4] for e in eventos if e[4]])

        despachar = {
            "EFECTIVO": self._planificar_efectivo,
            "BANCO": self._planificar_banco,
            "CRUCE": self._planificar_cruce,
            "CARGO": self._planificar_debito_suelto,
            "CREDITO": self._planificar_credito,
            "CHEQUE": self._planificar_cheque,
            "SIN_EXTRACTO": self._planificar_sin_extracto,
            "ECHEQ_PLANILLA": self._planificar_echeq_planilla,
        }
        for _fecha, _prioridad, tipo, dato, clave in eventos:
            self.operaciones.extend(despachar[tipo](dato, clave))
        self.operaciones.sort(key=lambda op: (op.origen, op.planilla, op.fecha, op.fila, op.parte))
        return self.operaciones

    @staticmethod
    def _orden_evento(evento) -> tuple:
        dato = evento[3]
        if isinstance(dato, LineaExtracto):
            return (dato.orden,)
        orden = getattr(getattr(dato, "linea", None), "orden", None)
        if orden is not None:
            return (orden,)
        return (getattr(dato, "planilla", ""), getattr(dato, "numero", 0))

    # --- idempotencia -----------------------------------------------------------
    # Una fila que ya se importo en otra corrida no se vuelve a planificar. Se
    # miran los tokens de TODAS las formas en que pudo haberse importado (pago o
    # egreso): en la segunda corrida sus deudas ya estan pagadas y el plan de la
    # misma fila saldria distinto. Se resuelve ANTES de repartir deudas, para que
    # una fila ya importada no le saque deudas a las demas.
    @staticmethod
    def _token(clave: str, parte: str) -> uuid.UUID:
        return uuid.uuid5(NAMESPACE_IMPORTACION, f"{clave}|{parte}")

    def _partes_banco(self, codigo: str) -> list[str]:
        return [f"{codigo}:pago", f"{codigo}:egreso"]

    PARTES_EFECTIVO = ("pago#1", "egreso")
    PARTE_CREDITO = "credito"

    def _cargar_tokens_existentes(self, claves):
        candidatos = []
        for clave in claves:
            if clave.startswith(("BANCO|", "EXTRACTO|")):
                for codigo in set(COLUMNA_A_SUCURSAL.values()):
                    candidatos.extend(self._token(clave, parte) for parte in self._partes_banco(codigo))
                candidatos.append(self._token(clave, self.PARTE_CREDITO))
            else:
                candidatos.extend(self._token(clave, parte) for parte in self.PARTES_EFECTIVO)
        # Se traen todos los tokens y se cruzan aca: un IN con miles de UUID es
        # un pedido enorme, y las tablas tienen pocos miles de filas con token.
        candidatos = set(candidatos)
        self.tokens_existentes = set()
        for modelo in (MovimientoBancario, MovimientoCajaCentral, PagoTesoreria):
            existentes = modelo.objects.filter(token_alta__isnull=False).values_list("token_alta", flat=True)
            self.tokens_existentes.update(t for t in existentes if t in candidatos)

    def _porcion_importada(self, clave: str, codigo: str) -> bool:
        return any(self._token(clave, parte) in self.tokens_existentes for parte in self._partes_banco(codigo))

    def _fila_banco_importada(self, clave: str) -> bool:
        return any(self._porcion_importada(clave, codigo) for codigo in set(COLUMNA_A_SUCURSAL.values()))

    def _credito_importado(self, clave: str) -> bool:
        return self._token(clave, self.PARTE_CREDITO) in self.tokens_existentes

    def _fila_efectivo_importada(self, clave: str) -> bool:
        return any(self._token(clave, parte) in self.tokens_existentes for parte in self.PARTES_EFECTIVO)

    def _op(self, origen, planilla, fila, clave, fecha, concepto, monto, accion, **extra) -> Operacion:
        return Operacion(
            origen=origen,
            planilla=planilla,
            fila=fila,
            clave=clave,
            fecha=fecha,
            concepto=concepto,
            monto=monto,
            accion=accion,
            **extra,
        )

    # --- banco desde el desglose (sin extracto) ------------------------------------
    def _planificar_banco(self, fila: FilaBanco, clave: str) -> list[Operacion]:
        base = dict(
            origen="BANCO",
            planilla=self.etiqueta_banco,
            fila=fila.numero,
            clave=clave,
            fecha=fila.fecha,
            concepto=fila.denominacion,
        )
        if _es_echeq(fila.denominacion):
            return [
                self._op(
                    **base,
                    monto=fila.monto,
                    accion=Accion.EXCLUIDA,
                    motivo="E-cheq: se registra en Gerayse cuando se debita, no en la emision.",
                )
            ]
        if self.cuenta_banco is None:
            return [self._op(**base, monto=fila.monto, accion=Accion.REVISAR, motivo="Falta la cuenta bancaria.")]

        proveedor = self._proveedor(fila.denominacion)
        rubro = self._rubro_banco(fila.rubro)
        periodo = _periodo_de_transferencia(fila.fecha, fila.denominacion)
        reparto_total = sum(fila.reparto.values(), Decimal("0.00"))

        if fila.reparto and abs(reparto_total - fila.monto) > TOLERANCIA:
            return [
                self._op(
                    **base,
                    monto=fila.monto,
                    accion=Accion.REVISAR,
                    motivo=(
                        f"El reparto por sucursal suma {dinero(reparto_total)} y el total es "
                        f"{dinero(fila.monto)}."
                    ),
                )
            ]

        if not fila.reparto:
            if self._fila_banco_importada(clave):
                return [
                    self._op(
                        **base,
                        monto=fila.monto,
                        accion=Accion.YA_IMPORTADA,
                        proveedor=proveedor,
                        motivo="Esta fila ya se importo en una corrida anterior.",
                    )
                ]
            return self._planificar_sin_reparto(base, fila, proveedor, rubro, fila.fecha, fila.monto, periodo)

        operaciones = []
        for columna, monto in fila.reparto.items():
            operaciones.extend(
                self._planificar_porcion(
                    base, COLUMNA_A_SUCURSAL[columna], monto, proveedor, rubro, fila.rubro, fila.fecha, periodo
                )
            )
        return operaciones

    def _planificar_sin_reparto(self, base, fila, proveedor, rubro, fecha, monto, periodo, aviso_extra=""):
        # Sin reparto solo se puede importar si hay deudas del proveedor que
        # digan de que sucursal es cada peso.
        if proveedor is None:
            return [
                self._op(
                    **dict(base, monto=monto),
                    accion=Accion.REVISAR,
                    motivo="La fila no tiene reparto por sucursal y no es de un proveedor con deudas.",
                )
            ]
        asignaciones, resto = self._elegir_deudas(proveedor, None, fecha, monto, self.empresa_banco_id)
        if not asignaciones or resto > TOLERANCIA:
            for deuda, importe in asignaciones:
                self.saldo[deuda.pk] += importe
            return [
                self._op(
                    **dict(base, monto=monto),
                    accion=Accion.REVISAR,
                    proveedor=proveedor,
                    motivo=(
                        "La fila no tiene reparto por sucursal y las deudas abiertas del proveedor "
                        f"no alcanzan a cubrirla (faltan {dinero(resto)})."
                    ),
                )
            ]
        # Un debito por sucursal, igual que cuando tiene reparto.
        por_sucursal = defaultdict(list)
        for deuda, importe in asignaciones:
            por_sucursal[deuda.sucursal].append((deuda, importe))
        operaciones = []
        for sucursal, asigs in por_sucursal.items():
            operaciones.append(
                self._op(
                    **dict(base, monto=sum((i for _d, i in asigs), Decimal("0.00"))),
                    accion=Accion.PAGAR_DEUDAS,
                    parte=f"{sucursal.codigo}:pago",
                    sucursal=sucursal,
                    proveedor=proveedor,
                    rubro=rubro or asigs[0][0].categoria.rubro_operativo,
                    periodo=periodo,
                    asignaciones=asigs,
                    aviso=" ".join(
                        a for a in ("Sin reparto en la planilla: la sucursal sale de las deudas pagadas.", aviso_extra) if a
                    ),
                )
            )
        return operaciones

    def _planificar_porcion(self, base, codigo, monto, proveedor, rubro, rubro_texto, fecha, periodo, aviso_extra=""):
        """Una porcion (sucursal) de un pago: paga deudas del proveedor y lo que
        no cubren entra como gasto. Comun al desglose solo y al extracto."""
        clave = base["clave"]
        sucursal = self.sucursales.get(codigo)
        porcion = dict(base, monto=monto)
        if sucursal is None:
            return [self._op(**porcion, accion=Accion.REVISAR, motivo=f"No existe la sucursal {codigo}.")]
        empresa_id = self.empresa_banco_id
        if empresa_id is not None and sucursal.empresa_id != empresa_id:
            return [
                self._op(
                    **porcion,
                    accion=Accion.REVISAR,
                    sucursal=sucursal,
                    motivo="La sucursal no es de la empresa de la cuenta bancaria.",
                )
            ]
        if self._porcion_importada(clave, codigo):
            return [
                self._op(
                    **porcion,
                    accion=Accion.YA_IMPORTADA,
                    sucursal=sucursal,
                    proveedor=proveedor,
                    motivo="Esta fila ya se importo en una corrida anterior.",
                )
            ]
        existente = (fecha, monto, sucursal.pk)
        if self.debitos_existentes.get(existente, 0) > 0:
            self.debitos_existentes[existente] -= 1
            self.movimientos_existentes[(MovimientoBancario.Tipo.DEBITO, fecha, monto)] -= 1
            return [
                self._op(
                    **porcion,
                    accion=Accion.YA_EN_GERAYSE,
                    parte=f"{codigo}:existente",
                    sucursal=sucursal,
                    motivo="Ya hay un debito de ese dia, importe y sucursal en la cuenta.",
                )
            ]

        operaciones = []
        asignaciones, resto = ([], monto)
        if proveedor is not None:
            asignaciones, resto = self._elegir_deudas(proveedor, sucursal, fecha, monto)
        cubierto = monto - resto
        if asignaciones:
            operaciones.append(
                self._op(
                    **dict(porcion, monto=cubierto),
                    accion=Accion.PAGAR_DEUDAS,
                    parte=f"{codigo}:pago",
                    sucursal=sucursal,
                    proveedor=proveedor,
                    rubro=rubro or asignaciones[0][0].categoria.rubro_operativo,
                    periodo=periodo,
                    asignaciones=asignaciones,
                    aviso=aviso_extra,
                )
            )
        if resto > TOLERANCIA:
            avisos = [aviso_extra] if aviso_extra and not asignaciones else []
            if asignaciones:
                avisos.append("Las deudas abiertas no alcanzaron: el resto entra como gasto.")
            elif proveedor is not None:
                avisos.append("El proveedor no tiene deudas abiertas en la sucursal: entra como gasto.")
            if rubro is None:
                operaciones.append(
                    self._op(
                        **dict(porcion, monto=resto),
                        accion=Accion.REVISAR,
                        parte=f"{codigo}:egreso",
                        sucursal=sucursal,
                        proveedor=proveedor,
                        motivo=f"El rubro '{rubro_texto or '(vacio)'}' no existe en Gerayse.",
                        aviso=" ".join(avisos),
                    )
                )
                return operaciones
            operaciones.append(
                self._op(
                    **dict(porcion, monto=resto),
                    accion=Accion.EGRESO,
                    parte=f"{codigo}:egreso",
                    sucursal=sucursal,
                    proveedor=proveedor,
                    rubro=rubro,
                    periodo=periodo,
                    aviso=" ".join(avisos),
                )
            )
        return operaciones

    # --- banco desde el extracto ---------------------------------------------
    def _eventos_extracto(self) -> list[tuple]:
        """Clasifica las lineas del extracto y las cruza con el desglose."""
        prefijo = f"EXTRACTO|{self.cuenta_banco.pk if self.cuenta_banco else '-'}"
        claves = dict(
            zip(
                [l.orden for l in self.extracto],
                _claves_estables(
                    prefijo,
                    self.extracto,
                    lambda l: f"{l.fecha:%Y-%m-%d}|{l.referencia}|{l.causal}|{l.importe}",
                ),
            )
        )
        self._clave_de_linea = claves
        eventos = []
        debitos = []
        por_dia = defaultdict(list)  # (fecha, categoria) -> lineas de cobros que se suman
        for linea in self.extracto:
            cat = categoria(linea)
            if cat == Categoria.CHEQUE:
                eventos.append((linea.fecha, 0, "CHEQUE", linea, claves[linea.orden]))
            elif cat == Categoria.DEBITO:
                debitos.append(linea)
            elif cat in (Categoria.COBRO_TRANSFERENCIA, Categoria.LIQUIDACION_TARJETA):
                por_dia[(linea.fecha, cat)].append(linea)
            else:
                eventos.append((linea.fecha, 0, "CREDITO", ("LINEA", cat, [linea]), claves[linea.orden]))
        for (fecha, cat), lineas in por_dia.items():
            eventos.append((fecha, 0, "CREDITO", ("DIA", cat, lineas), f"{prefijo}|{fecha:%Y-%m-%d}|{cat}"))

        filas_echeq = [f for f in self.filas_banco if _es_echeq(f.denominacion)]
        filas = [f for f in self.filas_banco if not _es_echeq(f.denominacion)]
        self._filas_echeq = filas_echeq
        cruce = cruzar_debitos(debitos, filas)
        # Las transferencias "TRANSF <CUIT> FAC" traen el CUIT del que cobra:
        # lo que ya cruzo dice de quien es cada CUIT, y eso ayuda a revisar las
        # que no estan en la planilla.
        self._cuit_proveedor = defaultdict(set)
        for c in cruce.cruces:
            cuit = _cuit_de(c.linea.concepto)
            if cuit:
                for porcion, _importe in c.asignaciones:
                    self._cuit_proveedor[cuit].add(porcion.fila.denominacion)
        for c in cruce.cruces:
            eventos.append((c.linea.fecha, 0, "CRUCE", c, claves[c.linea.orden]))
        for linea in cruce.lineas_sin_fila:
            eventos.append((linea.fecha, 0, "CARGO", linea, claves[linea.orden]))
        for porcion in cruce.porciones_sin_linea:
            eventos.append((porcion.fila.fecha, 2, "SIN_EXTRACTO", porcion, ""))
        for fila in filas_echeq:
            eventos.append((fila.fecha, 2, "ECHEQ_PLANILLA", fila, ""))
        self._porciones_sin_linea = cruce.porciones_sin_linea
        return eventos

    def _base_extracto(self, linea: LineaExtracto, clave: str, concepto: str, fila: int | None = None) -> dict:
        return dict(
            origen="BANCO",
            planilla="extracto",
            fila=fila if fila is not None else linea.orden + 1,
            clave=clave,
            fecha=linea.fecha,
            concepto=concepto,
            extracto=linea.descripcion(),
            referencia=linea.referencia,
        )

    def _planificar_cruce(self, cruce, clave: str) -> list[Operacion]:
        linea = cruce.linea
        avisos = {
            TipoCruce.PORCIONES_JUNTAS: "Varias sucursales de la fila se pagaron en una sola transferencia.",
            TipoCruce.FILA_EN_VARIAS_LINEAS: "El banco debito este pago en varias lineas.",
            TipoCruce.APROXIMADO: (
                f"La planilla difiere del banco en {dinero(-cruce.diferencia)}: se usa el importe del banco."
            ),
        }
        aviso = avisos.get(cruce.tipo, "")
        varias = len(cruce.asignaciones) > 1
        operaciones = []
        for porcion, importe in cruce.asignaciones:
            fila = porcion.fila
            base = self._base_extracto(linea, clave, fila.denominacion, fila=fila.numero)
            if varias:
                codigo = COLUMNA_A_SUCURSAL.get(porcion.columna, porcion.columna or "SIN")
                base["referencia"] = f"{linea.referencia}-{codigo}"
            proveedor = self._proveedor(fila.denominacion)
            rubro = self._rubro_banco(fila.rubro)
            periodo = _periodo_de_transferencia(linea.fecha, fila.denominacion)
            if not porcion.columna:
                if self._fila_banco_importada(clave):
                    operaciones.append(
                        self._op(
                            **dict(base, monto=importe),
                            accion=Accion.YA_IMPORTADA,
                            motivo="Esta linea ya se importo en una corrida anterior.",
                        )
                    )
                    continue
                operaciones.extend(
                    self._planificar_sin_reparto(base, fila, proveedor, rubro, linea.fecha, importe, periodo, aviso)
                )
                continue
            operaciones.extend(
                self._planificar_porcion(
                    base,
                    COLUMNA_A_SUCURSAL[porcion.columna],
                    importe,
                    proveedor,
                    rubro,
                    fila.rubro,
                    linea.fecha,
                    periodo,
                    aviso,
                )
            )
        return operaciones

    def _planificar_debito_suelto(self, linea: LineaExtracto, clave: str) -> list[Operacion]:
        """Debito del extracto sin fila en el desglose."""
        texto = normalizar(linea.concepto)
        base = self._base_extracto(linea, clave, linea.concepto)
        for patron, nombre_reparto, clase, nombre_rubro, nombre_proveedor in CARGOS_SIN_DESGLOSE:
            if not re.search(patron, texto):
                continue
            return self._planificar_cargo(base, linea, clave, nombre_reparto, clase, nombre_rubro, nombre_proveedor)
        return [
            self._op(
                **dict(base, monto=linea.monto),
                accion=Accion.REVISAR,
                motivo="Debito del banco que no esta en la planilla de transferencias.",
                aviso=self._pista_para_linea(linea),
            )
        ]

    def _planificar_cargo(self, base, linea, clave, nombre_reparto, clase, nombre_rubro, nombre_proveedor):
        concepto = self._concepto_cargo(linea)
        base = dict(base, concepto=concepto)
        reparto = self.repartos.get(nombre_reparto)
        if not reparto:
            return [
                self._op(
                    **dict(base, monto=linea.monto),
                    accion=Accion.REVISAR,
                    motivo=f"No esta en la planilla y falta la clave de reparto de {nombre_reparto}.",
                )
            ]
        if any(self._porcion_importada(clave, codigo) for codigo in reparto):
            return [
                self._op(
                    **dict(base, monto=linea.monto),
                    accion=Accion.YA_IMPORTADA,
                    motivo="Esta linea ya se importo en una corrida anterior.",
                )
            ]
        existente = (MovimientoBancario.Tipo.DEBITO, linea.fecha, linea.monto)
        if self.movimientos_existentes.get(existente, 0) > 0:
            self.movimientos_existentes[existente] -= 1
            return [
                self._op(
                    **dict(base, monto=linea.monto),
                    accion=Accion.YA_EN_GERAYSE,
                    motivo="Ya hay un debito de ese dia e importe en la cuenta.",
                )
            ]
        rubro = self._rubro(nombre_rubro)
        proveedor = self.proveedores.get(normalizar(nombre_proveedor)) if nombre_proveedor else None
        if clase == MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS and proveedor is None:
            clase = MovimientoBancario.Clase.OTRO_EGRESO
        periodo = primer_dia(linea.fecha)
        if nombre_reparto == "sueldos" and linea.fecha.day <= DIA_CORTE_SUELDOS:
            periodo = _mes_anterior(linea.fecha)
        clave_texto = ", ".join(f"{c} {p}" for c, p in reparto.items())
        aviso = f"No esta en la planilla: repartido con la clave de {nombre_reparto} ({clave_texto})."
        operaciones = []
        partes = repartir(linea.monto, reparto)
        for codigo, importe in partes:
            sucursal = self.sucursales.get(codigo)
            parte_base = dict(base, monto=importe)
            if len(partes) > 1:
                parte_base["referencia"] = f"{linea.referencia}-{codigo}"
            if sucursal is None or rubro is None:
                operaciones.append(
                    self._op(
                        **parte_base,
                        accion=Accion.REVISAR,
                        parte=f"{codigo}:egreso",
                        motivo=f"No existe la sucursal {codigo}." if sucursal is None else f"No existe el rubro {nombre_rubro}.",
                    )
                )
                continue
            operaciones.append(
                self._op(
                    **parte_base,
                    accion=Accion.EGRESO,
                    parte=f"{codigo}:egreso",
                    sucursal=sucursal,
                    proveedor=proveedor,
                    rubro=rubro,
                    periodo=periodo,
                    clase=clase,
                    aviso=aviso,
                )
            )
        return operaciones

    @staticmethod
    def _concepto_cargo(linea: LineaExtracto) -> str:
        texto = normalizar(linea.concepto)
        if "DBCR" in texto:
            return "DBCR S/DB" if "S DB" in texto else "DBCR S/CR"
        if "REMUNERACION" in texto:
            return "DB Pago Remuneraciones"
        if "COMISION" in texto and ("TRANSFERE" in texto or "TRF" in texto):
            return "Comisiones Trf"
        if "LIQ COMER" in texto:
            return "Pago Liq Comer Payway"
        return re.sub(r"\s+", " ", linea.concepto).strip().title()[:160]

    def _pista_para_linea(self, linea: LineaExtracto) -> str:
        """Ayuda para revisar un debito sin fila: una fila de e-cheq de la
        planilla con ese importe, el proveedor de ese CUIT, o (solo para
        transferencias) la porcion pendiente parecida en importe y fecha."""
        for fila in getattr(self, "_filas_echeq", []):
            importes = [fila.monto, *fila.reparto.values()]
            if linea.monto in importes:
                return (
                    f"La planilla lo tiene como e-cheq ({fila.denominacion}) pero el banco lo muestra como "
                    "transferencia: confirmar si lo carga quien carga los e-cheq."
                )
        cuit = _cuit_de(linea.concepto)
        if cuit and getattr(self, "_cuit_proveedor", {}).get(cuit):
            nombres = ", ".join(sorted(self._cuit_proveedor[cuit]))
            return f"Transferencia al mismo CUIT que {nombres}: falta en la planilla."
        if not re.search(r"\bTRF\b|TRANSF", normalizar(linea.concepto)):
            return ""
        mejor = None
        for porcion in getattr(self, "_porciones_sin_linea", []):
            if "EFECTIVO" in normalizar(porcion.fila.denominacion):
                continue
            dias = (linea.fecha - porcion.fila.fecha).days
            if not -3 <= dias <= 10:
                continue
            diferencia = abs(linea.monto - porcion.monto)
            if diferencia <= porcion.monto * Decimal("0.1") and (mejor is None or diferencia < mejor[0]):
                mejor = (diferencia, porcion)
        if mejor:
            porcion = mejor[1]
            return (
                f"Podria ser {porcion.fila.denominacion} {porcion.columna} del {porcion.fila.fecha:%d/%m}, "
                f"que en la planilla dice {dinero(porcion.monto)}."
            )
        return ""

    def _planificar_credito(self, dato, clave: str) -> list[Operacion]:
        modo, cat, lineas = dato
        primera = lineas[0]
        monto = sum((l.monto for l in lineas), Decimal("0.00"))
        if modo == "DIA":
            concepto = "Acreditación" if cat == Categoria.COBRO_TRANSFERENCIA else "Pago Liq Comer Payway"
            detalle = f"{len(lineas)} lineas del {primera.fecha:%d/%m}"
            clase = MovimientoBancario.Clase.ACREDITACION
            rubro = self._rubro("VENTAS EN SUCURSAL")
            referencia = ""
        else:
            concepto = re.sub(r"\s+", " ", primera.concepto).strip().title()[:160]
            detalle = primera.descripcion()
            clase = MovimientoBancario.Clase.OTRO_INGRESO
            rubro = None
            referencia = primera.referencia
        base = dict(
            origen="BANCO",
            planilla="extracto",
            fila=primera.orden + 1,
            clave=clave,
            fecha=primera.fecha,
            concepto=concepto,
            monto=monto,
            extracto=detalle,
            referencia=referencia,
            tipo_movimiento=MovimientoBancario.Tipo.CREDITO,
            clase=clase,
            rubro=rubro,
        )
        if self._credito_importado(clave):
            return [self._op(**base, accion=Accion.YA_IMPORTADA, motivo="Ya se importo en una corrida anterior.")]
        existente = (MovimientoBancario.Tipo.CREDITO, primera.fecha, monto)
        if self.movimientos_existentes.get(existente, 0) > 0:
            self.movimientos_existentes[existente] -= 1
            return [
                self._op(**base, accion=Accion.YA_EN_GERAYSE, motivo="Ya hay un credito de ese dia e importe en la cuenta.")
            ]
        return [self._op(**base, accion=Accion.INGRESO, parte=self.PARTE_CREDITO)]

    def _planificar_cheque(self, linea: LineaExtracto, clave: str) -> list[Operacion]:
        return [
            self._op(
                **dict(self._base_extracto(linea, clave, linea.concepto), monto=linea.monto),
                accion=Accion.EXCLUIDA,
                motivo="Cheque o e-cheq: lo carga tesoreria aparte cuando se debita.",
            )
        ]

    def _planificar_sin_extracto(self, porcion, _clave) -> list[Operacion]:
        fila = porcion.fila
        codigo = COLUMNA_A_SUCURSAL.get(porcion.columna, "")
        aviso = ""
        if "EFECTIVO" in normalizar(fila.denominacion):
            aviso = "Parece pagado en efectivo: entra por la planilla de efectivo, no por el banco."
        return [
            self._op(
                origen="BANCO",
                planilla="desglose",
                fila=fila.numero,
                clave="",
                fecha=fila.fecha,
                concepto=fila.denominacion,
                monto=porcion.monto,
                accion=Accion.SIN_EXTRACTO,
                sucursal=self.sucursales.get(codigo),
                motivo="No hay un debito en el extracto que coincida con esta porcion de la planilla.",
                aviso=aviso,
            )
        ]

    def _planificar_echeq_planilla(self, fila: FilaBanco, _clave) -> list[Operacion]:
        return [
            self._op(
                origen="BANCO",
                planilla="desglose",
                fila=fila.numero,
                clave="",
                fecha=fila.fecha,
                concepto=fila.denominacion,
                monto=fila.monto,
                accion=Accion.EXCLUIDA,
                motivo="E-cheq de la planilla: lo carga tesoreria aparte cuando se debita.",
            )
        ]

    # --- efectivo ---------------------------------------------------------------
    def _planificar_efectivo(self, fila: FilaEfectivo, clave: str) -> list[Operacion]:
        base = dict(
            origen="EFECTIVO",
            planilla=fila.planilla,
            fila=fila.numero,
            clave=clave,
            fecha=fila.fecha,
            concepto=fila.concepto if not fila.rubro_texto else f"{fila.concepto} ({fila.rubro_texto})",
        )
        if self._fila_efectivo_importada(clave):
            return [
                self._op(
                    **base,
                    monto=fila.monto,
                    accion=Accion.YA_IMPORTADA,
                    motivo="Esta fila ya se importo en una corrida anterior.",
                )
            ]
        texto = normalizar(f"{fila.concepto} {fila.rubro_texto}")
        codigo = COLUMNA_A_SUCURSAL.get(fila.planilla)
        aviso_sucursal = ""
        if "OVEJA NEGRA" in texto and codigo != SUCURSAL_OVEJA_NEGRA:
            # Los gastos propios de la panaderia (sueldos) se anotaban en la
            # solapa de EB1, pero en Gerayse Oveja Negra es su propia sucursal.
            if re.search(r"SUELDO|\bSAC\b", texto):
                codigo = SUCURSAL_OVEJA_NEGRA
                aviso_sucursal = f"Anotado en la solapa {fila.planilla}, imputado a Oveja Negra."
        gasto_de_ariel = bool(re.search(r"EDESA ARIEL", texto))
        if gasto_de_ariel and codigo != SUCURSAL_GASTOS_ARIEL:
            # La luz de Ariel (socio) tesoreria la imputa siempre a Yo Helados,
            # aunque la anote en la solapa de EC1.
            codigo = SUCURSAL_GASTOS_ARIEL
            aviso_sucursal = f"Anotado en la solapa {fila.planilla}, imputado a Yo Helados (gasto de Ariel)."
        sucursal = self.sucursales.get(codigo or "")
        if sucursal is None:
            return [
                self._op(**base, monto=fila.monto, accion=Accion.REVISAR, motivo=f"No existe la sucursal {codigo}.")
            ]

        periodo = primer_dia(fila.fecha)
        if re.search(r"DEUDA JUN", texto):
            periodo = date(fila.fecha.year, 6, 1)
        rubro = self._rubro_efectivo(fila.concepto, fila.rubro_texto)
        if gasto_de_ariel:
            rubro = self._rubro(RUBRO_GASTOS_ARIEL) or rubro
        # Un gasto personal de Ariel nunca paga una factura de proveedor.
        sin_proveedor = fila.planilla == PLANILLA_OVEJA_NEGRA or gasto_de_ariel
        if sin_proveedor:
            # Oveja Negra: tesoreria no tiene el proveedor de lo que pago. Va
            # como egreso con su rubro, o VARIOS si no dice nada ("S/E").
            proveedor = None
            if rubro is None:
                rubro = self._rubro(RUBRO_SIN_ESPECIFICAR)
        else:
            proveedor = self._proveedor_efectivo(fila.concepto, sucursal, fila.rubro_texto)
        operaciones = []
        resto = fila.monto
        hubo_deuda_o_pago = False

        if proveedor is not None:
            # Lo que tesoreria ya pago en Gerayse a ese proveedor en esa
            # sucursal es esta misma plata: se descuenta antes de pagar nada.
            # Primero el mismo mes; despues el anterior y el siguiente, porque
            # tesoreria registra con dias de diferencia (lo de julio lo cargo
            # el 31, los sueldos de julio el 10 de agosto).
            ya = Decimal("0.00")
            for mes in _meses_vecinos(fila.fecha):
                clave_registrado = (proveedor.pk, sucursal.pk, mes)
                disponible = self.efectivo_registrado.get(clave_registrado, Decimal("0.00"))
                tomado = min(disponible, resto - ya)
                if tomado > 0:
                    self.efectivo_registrado[clave_registrado] = disponible - tomado
                    ya += tomado
            if ya > 0:
                resto -= ya
                hubo_deuda_o_pago = True
                operaciones.append(
                    self._op(
                        **base,
                        monto=ya,
                        accion=Accion.YA_EN_GERAYSE,
                        parte="registrado",
                        sucursal=sucursal,
                        proveedor=proveedor,
                        motivo="Tesoreria ya registro en Gerayse pagos en efectivo a este proveedor ese mes.",
                    )
                )
            if resto > TOLERANCIA:
                asignaciones, resto = self._elegir_deudas(proveedor, sucursal, fila.fecha, resto)
                if asignaciones:
                    hubo_deuda_o_pago = True
                    operaciones.append(
                        self._op(
                            **base,
                            monto=sum((i for _d, i in asignaciones), Decimal("0.00")),
                            accion=Accion.PAGAR_DEUDAS,
                            parte="pago",
                            sucursal=sucursal,
                            proveedor=proveedor,
                            rubro=rubro,
                            periodo=periodo,
                            asignaciones=asignaciones,
                            aviso=aviso_sucursal,
                        )
                    )

        if resto <= TOLERANCIA:
            return operaciones
        if rubro is None:
            operaciones.append(
                self._op(
                    **base,
                    monto=resto,
                    accion=Accion.REVISAR,
                    parte="egreso",
                    sucursal=sucursal,
                    proveedor=proveedor,
                    motivo="No se puede deducir el rubro del concepto.",
                )
            )
            return operaciones
        avisos = [a for a in (aviso_sucursal,) if a]
        if sin_proveedor:
            avisos.append("Oveja Negra: sin proveedor, entra como gasto.")
        elif hubo_deuda_o_pago:
            avisos.append("Las deudas abiertas no alcanzaron: el resto entra como gasto.")
        elif proveedor is None:
            abiertas = sum(
                self.saldo[d.pk]
                for (_prov, suc), deudas in self.deudas_por_clave.items()
                if suc == sucursal.pk
                for d in deudas
                if d.categoria.rubro_operativo_id == rubro.pk
                and primer_dia(d.fecha_emision) == primer_dia(fila.fecha)
                and self.saldo[d.pk] > 0
            )
            if abiertas > 0:
                avisos.append(
                    f"Hay {dinero(abiertas)} de deuda abierta de {rubro.nombre} en la sucursal ese mes: "
                    "si esta fila la paga, indicar el proveedor para no contar el gasto dos veces."
                )
        operaciones.append(
            self._op(
                **base,
                monto=resto,
                accion=Accion.EGRESO,
                parte="egreso",
                sucursal=sucursal,
                proveedor=None,
                rubro=rubro,
                periodo=periodo,
                aviso=" ".join(avisos),
            )
        )
        return operaciones

    # --- aplicacion -----------------------------------------------------------
    def unidades(self) -> list[list[Operacion]]:
        """Operaciones agrupadas por lo que se aplica todo junto o nada: la
        fila entera en efectivo, la porcion de cada sucursal en banco. Es la
        misma unidad con la que `_fila_*_importada` decide si ya se importo;
        si se aplicara de a una operacion, una fila con el egreso grabado y el
        pago caido se daria por importada y el pago no se reintentaria nunca."""
        grupos = defaultdict(list)
        for op in self.operaciones:
            if op.accion in ACCIONES_QUE_ESCRIBEN:
                grupos[_unidad(op)].append(op)
        return list(grupos.values())

    def aplicar_unidad(self, unidad: list[Operacion]):
        try:
            with transaction.atomic():
                for op in unidad:
                    op.resultado = self._aplicar_operacion(op)
        except Exception as error:  # noqa: BLE001 - el error se informa en la fila y sigue
            mensaje = _mensaje_de_error(error)
            for op in unidad:
                op.accion = Accion.ERROR
                op.resultado = mensaje

    def aplicar(self) -> list[Operacion]:
        for unidad in self.unidades():
            self.aplicar_unidad(unidad)
        return self.operaciones

    def _observaciones(self, op: Operacion) -> str:
        texto = f"{PREFIJO_OBSERVACIONES} {op.origen.lower()} {op.planilla}, fila {op.fila}."
        if op.extracto:
            texto += f" Banco: {op.extracto}"
        return texto[:255]

    def _aplicar_operacion(self, op: Operacion) -> str:
        if op.origen == "BANCO":
            return self._aplicar_banco(op)
        return self._aplicar_efectivo(op)

    def _concepto_banco(self, op: Operacion) -> str:
        if op.clase:
            return op.concepto[:160]
        return f"TF {op.concepto.strip().title()}"[:160]

    def _aplicar_banco(self, op: Operacion) -> str:
        if op.tipo_movimiento == MovimientoBancario.Tipo.CREDITO:
            movimiento = create_bank_movement(
                cuenta_bancaria=self.cuenta_banco,
                tipo=MovimientoBancario.Tipo.CREDITO,
                fecha=op.fecha,
                monto=op.monto,
                concepto=self._concepto_banco(op),
                clase=op.clase,
                rubro_operativo=op.rubro,
                referencia=op.referencia,
                observaciones=self._observaciones(op),
                token_alta=op.token(),
                actor=self.actor,
            )
            return f"Credito #{movimiento.pk}"
        if op.accion == Accion.PAGAR_DEUDAS:
            movimiento = create_bank_movement(
                cuenta_bancaria=self.cuenta_banco,
                tipo=MovimientoBancario.Tipo.DEBITO,
                fecha=op.fecha,
                monto=op.monto,
                concepto=self._concepto_banco(op),
                clase=MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
                proveedor=op.proveedor,
                rubro_operativo=op.rubro,
                sucursal_gasto=op.sucursal,
                periodo_pago=op.periodo,
                referencia=op.referencia,
                observaciones=self._observaciones(op),
                token_alta=op.token(),
                actor=self.actor,
            )
            asignaciones = [
                (CuentaPorPagar.objects.get(pk=deuda.pk), importe) for deuda, importe in op.asignaciones
            ]
            pagos = pay_debts_from_bank_movement(
                bank_movement=movimiento,
                asignaciones=asignaciones,
                observaciones=self._observaciones(op),
                actor=self.actor,
            )
            return f"Debito #{movimiento.pk}, {len(pagos)} pago(s)"
        if op.clase or op.proveedor is not None:
            movimiento = create_bank_movement(
                cuenta_bancaria=self.cuenta_banco,
                tipo=MovimientoBancario.Tipo.DEBITO,
                fecha=op.fecha,
                monto=op.monto,
                concepto=self._concepto_banco(op),
                clase=op.clase or MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
                proveedor=op.proveedor,
                rubro_operativo=op.rubro,
                sucursal_gasto=op.sucursal,
                periodo_pago=op.periodo,
                referencia=op.referencia,
                observaciones=self._observaciones(op),
                token_alta=op.token(),
                actor=self.actor,
            )
            return f"Debito #{movimiento.pk}"
        movimiento = register_egreso_tesoreria(
            # La sucursal ya se valido contra la empresa de la cuenta al
            # planificar; la cuenta de ARMADI es legacy y no tiene empresa.
            empresa=op.sucursal.empresa_id,
            fuente="BANCO",
            fecha=op.fecha,
            monto=op.monto,
            concepto=self._concepto_banco(op),
            cuenta_bancaria=self.cuenta_banco,
            observaciones=self._observaciones(op),
            rubro=op.rubro,
            sucursal=op.sucursal,
            periodo=op.periodo,
            token_alta=op.token(),
            actor=self.actor,
        )
        return f"Debito #{movimiento.pk}"

    def _aplicar_efectivo(self, op: Operacion) -> str:
        if op.accion == Accion.PAGAR_DEUDAS:
            pagos = []
            for indice, (deuda, importe) in enumerate(op.asignaciones, start=1):
                pagos.append(
                    register_cash_payment(
                        payable=CuentaPorPagar.objects.get(pk=deuda.pk),
                        fecha_pago=op.fecha,
                        monto=importe,
                        observaciones=self._observaciones(op),
                        token_alta=op.token(f"#{indice}"),
                        actor=self.actor,
                    )
                )
            return f"{len(pagos)} pago(s) en efectivo"
        movimiento = register_egreso_tesoreria(
            empresa=op.sucursal.empresa_id,
            fuente="EFECTIVO",
            fecha=op.fecha,
            monto=op.monto,
            concepto=op.concepto.strip().title()[:160],
            observaciones=self._observaciones(op),
            rubro=op.rubro,
            sucursal=op.sucursal,
            periodo=op.periodo,
            token_alta=op.token(),
            actor=self.actor,
        )
        return f"Egreso #{movimiento.pk}"

    # --- informe --------------------------------------------------------------
    def resumen(self) -> dict:
        totales = defaultdict(lambda: [0, Decimal("0.00")])
        for op in self.operaciones:
            fila = totales[(op.origen, op.accion)]
            fila[0] += 1
            fila[1] += op.monto
        return dict(totales)

    def conciliacion_extracto(self) -> dict | None:
        """Cuadre del banco: todo lo del extracto tiene que quedar cargado, ya
        estar, quedar afuera a proposito (cheques) o ir a revisar."""
        if not self.extracto:
            return None
        creditos = sum((l.monto for l in self.extracto if not l.es_debito), Decimal("0.00"))
        debitos = sum((l.monto for l in self.extracto if l.es_debito), Decimal("0.00"))
        por_accion = defaultdict(lambda: {"CREDITO": Decimal("0.00"), "DEBITO": Decimal("0.00")})
        for op in self.operaciones:
            if op.origen == "BANCO" and op.planilla == "extracto":
                por_accion[op.accion][op.tipo_movimiento] += op.monto
        explicado = {
            tipo: sum((valores[tipo] for valores in por_accion.values()), Decimal("0.00"))
            for tipo in ("CREDITO", "DEBITO")
        }
        return {
            "saldo_inicial": saldo_inicial(self.extracto),
            "saldo_final": self.extracto[-1].saldo,
            "creditos": creditos,
            "debitos": debitos,
            "por_accion": {accion: dict(valores) for accion, valores in por_accion.items()},
            "sin_explicar_creditos": creditos - explicado["CREDITO"],
            "sin_explicar_debitos": debitos - explicado["DEBITO"],
        }

    def pagos_registrados_sin_fila(self) -> list[tuple]:
        """Pagos en efectivo que ya estaban en Gerayse y ninguna fila de la
        planilla explico. Pueden ser gastos que la planilla no tiene, o filas
        que se anotaron con otro concepto: los mira tesoreria."""
        meses = {primer_dia(f.fecha) for f in self.filas_efectivo}
        sucursales = {
            self.sucursales[COLUMNA_A_SUCURSAL[f.planilla]].pk
            for f in self.filas_efectivo
            if COLUMNA_A_SUCURSAL.get(f.planilla) in self.sucursales
        }
        sobrantes = []
        for (prov_id, suc_id, mes), restante in self.efectivo_registrado.items():
            if mes in meses and suc_id in sucursales and restante > TOLERANCIA:
                sobrantes.append((prov_id, suc_id, mes, restante))
        return sobrantes

    def posibles_duplicados_efectivo(self) -> list[tuple]:
        """Filas de efectivo con la misma solapa, fecha e importe: tesoreria
        dice que esta todo revisado, se cargan igual, pero se marcan."""
        vistos = defaultdict(list)
        for fila in self.filas_efectivo:
            vistos[(fila.planilla, fila.fecha, fila.monto)].append(fila)
        return [(clave, filas) for clave, filas in vistos.items() if len(filas) > 1]

    def escribir_informe(self, ruta):
        ruta = Path(ruta)
        with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.writer(archivo, delimiter=";")
            escritor.writerow(
                [
                    "origen",
                    "planilla",
                    "fila",
                    "fecha",
                    "concepto",
                    "sucursal",
                    "importe",
                    "accion",
                    "tipo",
                    "proveedor",
                    "rubro",
                    "periodo",
                    "deudas",
                    "extracto",
                    "motivo",
                    "aviso",
                    "resultado",
                ]
            )
            for op in self.operaciones:
                escritor.writerow(
                    [
                        op.origen,
                        op.planilla,
                        op.fila,
                        f"{op.fecha:%d/%m/%Y}",
                        op.concepto,
                        op.sucursal.codigo if op.sucursal else "",
                        dinero(op.monto),
                        op.accion,
                        op.tipo_movimiento if op.origen == "BANCO" else "",
                        op.proveedor.razon_social if op.proveedor else "",
                        op.rubro.nombre if op.rubro else "",
                        f"{op.periodo:%m/%Y}" if op.periodo else "",
                        " | ".join(
                            f"#{d.pk} {d.fecha_emision:%d/%m} {dinero(i)}" for d, i in op.asignaciones
                        ),
                        op.extracto,
                        op.motivo,
                        op.aviso,
                        op.resultado,
                    ]
                )


def _unidad(op: Operacion) -> tuple:
    if op.origen == "BANCO":
        return (op.clave, op.parte.split(":")[0])
    return (op.clave, "")


def _mensaje_de_error(error) -> str:
    if isinstance(error, ValidationError):
        if hasattr(error, "message_dict"):
            return "; ".join(f"{k}: {' '.join(v)}" for k, v in error.message_dict.items())
        return " ".join(error.messages)
    return f"{type(error).__name__}: {error}"


__all__ = [
    "Accion",
    "FilaBanco",
    "FilaEfectivo",
    "Importador",
    "Operacion",
    "dinero",
    "filas_efectivo_de_directorio",
    "leer_libro_efectivo",
    "leer_planilla_banco",
    "leer_planilla_efectivo",
    "leer_reparto",
    "normalizar",
    "parsear_fecha",
    "parsear_monto",
    "planilla_de_solapa",
    "repartir",
]
