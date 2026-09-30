"""Importacion de las planillas de tesoreria que se llevaban fuera de Gerayse.

Dos planillas de Google, exportadas a CSV:

- Banco (pestana "MOV AGO 26" de MOVIMIENTOS): una fila por transferencia, con
  el total y el reparto por sucursal en columnas (EC1, EC2, EB, EB2, PP, H).
- Efectivo central ("egresos pendientes de efectuar"): una planilla por
  sucursal con fecha, concepto e importe de lo que tesoreria pago en efectivo.

Casi todo lo que sale en esas planillas ya esta en Gerayse del otro lado: los
cajeros cargan las facturas como deuda. Por eso importar NO es cargar gasto.
Cuando hay deuda abierta del proveedor en la sucursal, la fila la paga, y el
gasto sigue siendo el de la deuda. Solo lo que no tiene deuda entra como egreso
con rubro, sucursal y periodo. Cargarlo todo como gasto duplicaba el resultado
economico y dejaba las deudas abiertas.

Que facturas paga una fila: primero una que coincida justo con el importe; si
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
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction

from cashops.models import RubroOperativo, Sucursal
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

CENTAVO = Decimal("0.01")
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
    "VIVRE": "VIV-01",
}
COLUMNAS_REPARTO_BANCO = ("EC1", "EC2", "EB", "EB2", "PP", "H")
SUCURSAL_OVEJA_NEGRA = "PP-OV-06"

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

# La planilla de efectivo no trae rubro: el concepto casi siempre ES el rubro
# ("VERDURA", "SUELDOS"). Primera coincidencia gana, por eso RETIRO y los
# sueldos van antes que el resto.
RUBRO_EFECTIVO = (
    (r"RETIRO", "RETIRO SOCIOS"),
    (r"SUELDO|\bSAC\b|ADELANTO|\bVAC\b|LUCHI|GASTOS MEDICOS", "PERSONAL"),
    (r"ALQUI?LE?R", "ALQUILER"),
    (r"VERDURA|PARE CARRITO", "VERDURAS"),
    (r"POLL+O", "POLLO"),
    (r"ALMACE|S?C?ICILIANA|SCILIANA", "ALMACEN"),
    (r"FIAMBRE", "FIAMBRES Y LACTEOS"),
    (r"CARNE|BRUNETTI", "CARNE"),
    (r"CAFE|KENYA", "CAFE/TE/AZUCAR/LECHE"),
    (r"^PAN$", "PAN"),
    (r"SALTA REFRESCOS", "BEBIDAS SIN ALCOHOL"),
    (r"DESCARTABLE", "DESCARTABLE"),
    (r"EDESA|SERVICIO|AQUALAND|SISTEMA|REMIS", "SERVICIOS"),
    (r"MANTENIMIENTO|EXTRACTOR|ARREGLO|CHAPA|MESAS", "MANTENIMIENTO"),
    (r"UNIFORME|^VARIOS", "VARIOS"),
    (r"^IVA |IMPUESTOS IVA", "IMPUESTOS AFIP"),
)

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
    "BRUNETTI CARNE": "BRUNETTI",
    "ALMACEN LA SCILIANA": "SICILIANA",
    "ALMACE LA SCILIANA": "SICILIANA",
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


class Accion:
    PAGAR_DEUDAS = "PAGAR_DEUDAS"
    EGRESO = "EGRESO"
    YA_EN_GERAYSE = "YA_EN_GERAYSE"
    YA_IMPORTADA = "YA_IMPORTADA"
    EXCLUIDA = "EXCLUIDA"
    REVISAR = "REVISAR"
    ERROR = "ERROR"


ACCIONES_QUE_ESCRIBEN = {Accion.PAGAR_DEUDAS, Accion.EGRESO}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto.upper())
    return re.sub(r"\s+", " ", texto).strip()


def parsear_monto(valor: str) -> Decimal | None:
    """"$1.234,56", "1.234,56", "934906,5" o "62500" -> Decimal. Vacio -> None."""
    texto = (valor or "").replace("$", "").replace(" ", "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(".", "")
    try:
        return Decimal(texto).quantize(CENTAVO)
    except InvalidOperation:
        return None


def parsear_fecha(valor: str) -> date | None:
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", valor or "")
    if not match:
        return None
    dia, mes, anio = (int(parte) for parte in match.groups())
    return date(anio, mes, dia)


def primer_dia(fecha: date) -> date:
    return fecha.replace(day=1)


def _meses_vecinos(fecha: date) -> list[date]:
    """El mes de la fecha, el anterior y el siguiente, en ese orden."""
    mes = primer_dia(fecha)
    anterior = date(mes.year - 1, 12, 1) if mes.month == 1 else date(mes.year, mes.month - 1, 1)
    siguiente = date(mes.year + 1, 1, 1) if mes.month == 12 else date(mes.year, mes.month + 1, 1)
    return [mes, anterior, siguiente]


def dinero(valor: Decimal) -> str:
    entero, _, decimales = f"{valor:.2f}".partition(".")
    return f"{int(entero):,}".replace(",", ".") + "," + decimales


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
    concepto: str
    monto: Decimal


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

    def token(self, sufijo: str = "") -> uuid.UUID:
        return uuid.uuid5(NAMESPACE_IMPORTACION, f"{self.clave}|{self.parte}{sufijo}")


def leer_planilla_banco(ruta) -> list[FilaBanco]:
    """Lee el CSV de la pestana del mes. La cabecera se busca (la planilla
    trae filas de titulo arriba) y las columnas se toman por nombre."""
    with open(ruta, encoding="utf-8", newline="") as archivo:
        filas = list(csv.reader(archivo))
    cabecera_idx = next(
        (i for i, fila in enumerate(filas) if {"FECHA", "MONTO"} <= {normalizar(c) for c in fila}),
        None,
    )
    if cabecera_idx is None:
        raise ValidationError(f"{ruta}: no encuentro la fila de cabecera con FECHA y MONTO.")
    cabecera = [normalizar(c) for c in filas[cabecera_idx]]

    def columna(nombre):
        return cabecera.index(nombre) if nombre in cabecera else None

    idx_fecha, idx_monto = columna("FECHA"), columna("MONTO")
    idx_denominacion, idx_rubro = columna("DENOMINACION"), columna("RUBRO")
    idx_reparto = {c: columna(c) for c in COLUMNAS_REPARTO_BANCO if columna(c) is not None}

    def celda(fila, idx):
        return fila[idx] if idx is not None and idx < len(fila) else ""

    resultado = []
    for numero, fila in enumerate(filas[cabecera_idx + 1 :], start=cabecera_idx + 2):
        fecha = parsear_fecha(celda(fila, idx_fecha))
        monto = parsear_monto(celda(fila, idx_monto))
        if fecha is None or monto is None:
            continue
        reparto = {}
        for col, idx in idx_reparto.items():
            valor = parsear_monto(celda(fila, idx))
            if valor:
                reparto[col] = valor
        resultado.append(
            FilaBanco(
                numero=numero,
                fecha=fecha,
                denominacion=celda(fila, idx_denominacion).strip(),
                rubro=celda(fila, idx_rubro).strip(),
                monto=monto,
                reparto=reparto,
            )
        )
    return resultado


def leer_planilla_efectivo(ruta, planilla: str) -> list[FilaEfectivo]:
    with open(ruta, encoding="utf-8", newline="") as archivo:
        filas = list(csv.reader(archivo))
    resultado = []
    for numero, fila in enumerate(filas, start=1):
        if len(fila) < 3:
            continue
        fecha = parsear_fecha(fila[0])
        monto = parsear_monto(fila[2])
        if fecha is None or monto is None:
            continue
        resultado.append(
            FilaEfectivo(planilla=planilla, numero=numero, fecha=fecha, concepto=fila[1].strip(), monto=monto)
        )
    return resultado


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


class Importador:
    def __init__(self, *, actor, cuenta_banco: CuentaBancaria | None = None, etiqueta_banco: str = "banco"):
        self.actor = actor
        self.cuenta_banco = cuenta_banco
        self.etiqueta_banco = etiqueta_banco
        self.filas_banco: list[FilaBanco] = []
        self.filas_efectivo: list[FilaEfectivo] = []
        self.operaciones: list[Operacion] = []

    # --- carga -------------------------------------------------------------
    def agregar_banco(self, filas: list[FilaBanco]):
        self.filas_banco.extend(filas)

    def agregar_efectivo(self, filas: list[FilaEfectivo]):
        self.filas_efectivo.extend(filas)

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
            deuda = pago.cuenta_por_pagar
            clave = (deuda.proveedor_id, deuda.sucursal_id, primer_dia(pago.fecha_pago))
            self.efectivo_registrado[clave] += pago.monto
        self.efectivo_registrado_inicial = dict(self.efectivo_registrado)

        # Debitos que ya estan en la cuenta: la planilla puede repetir alguno.
        self.debitos_existentes = defaultdict(int)
        if self.cuenta_banco is not None:
            for mov in MovimientoBancario.objects.filter(
                cuenta_bancaria=self.cuenta_banco,
                tipo=MovimientoBancario.Tipo.DEBITO,
                estado=MovimientoBancario.Estado.REGISTRADO,
            ).only("fecha", "monto", "sucursal_gasto_id"):
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

    def _rubro_efectivo(self, concepto: str) -> RubroOperativo | None:
        texto = normalizar(concepto)
        for patron, rubro in RUBRO_EFECTIVO:
            if re.search(patron, texto):
                return self._rubro(rubro)
        return None

    def _proveedor_efectivo(self, concepto: str, sucursal: Sucursal) -> Proveedor | None:
        # La regla gana al nombre directo: en Gerayse hay un proveedor
        # "SUELDOS" sin deudas, y los sueldos de VIVRE son del proveedor
        # "MAPOGO SRL SUELDOS", que es donde estan sus deudas y sus pagos.
        texto = normalizar(concepto)
        for patron, nombre, solo_sucursal in PROVEEDOR_INFERIDO_EFECTIVO:
            if solo_sucursal and sucursal.codigo.upper() != solo_sucursal:
                continue
            if re.search(patron, texto):
                inferido = self.proveedores.get(normalizar(nombre))
                if inferido is not None:
                    return inferido
        return self._proveedor(concepto)

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
        claves_banco = _claves_estables(
            f"BANCO|{self.etiqueta_banco}",
            self.filas_banco,
            lambda f: f"{f.fecha:%Y-%m-%d}|{normalizar(f.denominacion)}|{f.monto}",
        )
        claves_efectivo = _claves_estables(
            "EFECTIVO",
            self.filas_efectivo,
            lambda f: f"{f.planilla}|{f.fecha:%Y-%m-%d}|{normalizar(f.concepto)}|{f.monto}",
        )
        # Orden cronologico entre las dos planillas: una deuda la paga el pago
        # que ocurrio primero, sea por banco o en efectivo.
        filas = [("BANCO", f, c) for f, c in zip(self.filas_banco, claves_banco)]
        filas += [("EFECTIVO", f, c) for f, c in zip(self.filas_efectivo, claves_efectivo)]
        filas.sort(key=lambda item: (item[1].fecha, item[0], getattr(item[1], "planilla", ""), item[1].numero))
        self._cargar_tokens_existentes([clave for _o, _f, clave in filas])
        self.empresa_banco_id = self._empresa_de_la_cuenta()

        for origen, fila, clave in filas:
            if origen == "BANCO":
                self.operaciones.extend(self._planificar_banco(fila, clave))
            else:
                self.operaciones.extend(self._planificar_efectivo(fila, clave))
        self.operaciones.sort(key=lambda op: (op.origen, op.planilla, op.fecha, op.fila, op.parte))
        return self.operaciones

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

    def _cargar_tokens_existentes(self, claves):
        candidatos = []
        for clave in claves:
            if clave.startswith("BANCO|"):
                for codigo in set(COLUMNA_A_SUCURSAL.values()):
                    candidatos.extend(self._token(clave, parte) for parte in self._partes_banco(codigo))
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

    def _planificar_banco(self, fila: FilaBanco, clave: str) -> list[Operacion]:
        base = dict(
            origen="BANCO",
            planilla=self.etiqueta_banco,
            fila=fila.numero,
            clave=clave,
            fecha=fila.fecha,
            concepto=fila.denominacion,
        )
        if re.search(r"\bE?CHEQ\b", normalizar(fila.denominacion)):
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
        periodo = primer_dia(fila.fecha)
        empresa_id = self.empresa_banco_id
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
            # Sin reparto solo se puede importar si hay deudas del proveedor
            # que digan de que sucursal es cada peso.
            if proveedor is None:
                return [
                    self._op(
                        **base,
                        monto=fila.monto,
                        accion=Accion.REVISAR,
                        motivo="La fila no tiene reparto por sucursal y no es de un proveedor con deudas.",
                    )
                ]
            asignaciones, resto = self._elegir_deudas(proveedor, None, fila.fecha, fila.monto, empresa_id)
            if not asignaciones or resto > TOLERANCIA:
                for deuda, importe in asignaciones:
                    self.saldo[deuda.pk] += importe
                return [
                    self._op(
                        **base,
                        monto=fila.monto,
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
                        **base,
                        monto=sum((i for _d, i in asigs), Decimal("0.00")),
                        accion=Accion.PAGAR_DEUDAS,
                        parte=f"{sucursal.codigo}:pago",
                        sucursal=sucursal,
                        proveedor=proveedor,
                        rubro=rubro or asigs[0][0].categoria.rubro_operativo,
                        periodo=periodo,
                        asignaciones=asigs,
                        aviso="Sin reparto en la planilla: la sucursal sale de las deudas pagadas.",
                    )
                )
            return operaciones

        operaciones = []
        for columna, monto in fila.reparto.items():
            codigo = COLUMNA_A_SUCURSAL[columna]
            sucursal = self.sucursales.get(codigo)
            porcion = dict(base, monto=monto)
            if sucursal is None:
                operaciones.append(
                    self._op(**porcion, accion=Accion.REVISAR, motivo=f"No existe la sucursal {codigo}.")
                )
                continue
            if empresa_id is not None and sucursal.empresa_id != empresa_id:
                operaciones.append(
                    self._op(
                        **porcion,
                        accion=Accion.REVISAR,
                        sucursal=sucursal,
                        motivo="La sucursal no es de la empresa de la cuenta bancaria.",
                    )
                )
                continue
            if self._porcion_importada(clave, codigo):
                operaciones.append(
                    self._op(
                        **porcion,
                        accion=Accion.YA_IMPORTADA,
                        sucursal=sucursal,
                        proveedor=proveedor,
                        motivo="Esta fila ya se importo en una corrida anterior.",
                    )
                )
                continue
            existente = (fila.fecha, monto, sucursal.pk)
            if self.debitos_existentes.get(existente, 0) > 0:
                self.debitos_existentes[existente] -= 1
                operaciones.append(
                    self._op(
                        **porcion,
                        accion=Accion.YA_EN_GERAYSE,
                        parte=f"{codigo}:existente",
                        sucursal=sucursal,
                        motivo="Ya hay un debito de ese dia, importe y sucursal en la cuenta.",
                    )
                )
                continue

            asignaciones, resto = ([], monto)
            if proveedor is not None:
                asignaciones, resto = self._elegir_deudas(proveedor, sucursal, fila.fecha, monto)
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
                    )
                )
            if resto > TOLERANCIA:
                aviso = ""
                if asignaciones:
                    aviso = "Las deudas abiertas no alcanzaron: el resto entra como gasto."
                elif proveedor is not None:
                    aviso = "El proveedor no tiene deudas abiertas en la sucursal: entra como gasto."
                if rubro is None:
                    operaciones.append(
                        self._op(
                            **dict(porcion, monto=resto),
                            accion=Accion.REVISAR,
                            parte=f"{codigo}:egreso",
                            sucursal=sucursal,
                            proveedor=proveedor,
                            motivo=f"El rubro '{fila.rubro or '(vacio)'}' no existe en Gerayse.",
                        )
                    )
                    continue
                operaciones.append(
                    self._op(
                        **dict(porcion, monto=resto),
                        accion=Accion.EGRESO,
                        parte=f"{codigo}:egreso",
                        sucursal=sucursal,
                        proveedor=proveedor,
                        rubro=rubro,
                        periodo=periodo,
                        aviso=aviso,
                    )
                )
        return operaciones

    def _planificar_efectivo(self, fila: FilaEfectivo, clave: str) -> list[Operacion]:
        base = dict(
            origen="EFECTIVO",
            planilla=fila.planilla,
            fila=fila.numero,
            clave=clave,
            fecha=fila.fecha,
            concepto=fila.concepto,
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
        texto = normalizar(fila.concepto)
        codigo = COLUMNA_A_SUCURSAL.get(fila.planilla)
        aviso_sucursal = ""
        if "OVEJA NEGRA" in texto and codigo != SUCURSAL_OVEJA_NEGRA:
            # Los gastos propios de la panaderia (sueldos) se anotaban en la
            # solapa de EB1, pero en Gerayse Oveja Negra es su propia sucursal.
            if re.search(r"SUELDO|\bSAC\b", texto):
                codigo = SUCURSAL_OVEJA_NEGRA
                aviso_sucursal = f"Anotado en la solapa {fila.planilla}, imputado a Oveja Negra."
        sucursal = self.sucursales.get(codigo or "")
        if sucursal is None:
            return [
                self._op(**base, monto=fila.monto, accion=Accion.REVISAR, motivo=f"No existe la sucursal {codigo}.")
            ]

        periodo = primer_dia(fila.fecha)
        if re.search(r"DEUDA JUN", texto):
            periodo = date(fila.fecha.year, 6, 1)
        rubro = self._rubro_efectivo(fila.concepto)
        proveedor = self._proveedor_efectivo(fila.concepto, sucursal)
        operaciones = []
        resto = fila.monto

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
        if proveedor is not None:
            avisos.append("Las deudas abiertas no alcanzaron: el resto entra como gasto.")
        elif rubro is not None:
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
    def aplicar(self) -> list[Operacion]:
        for op in self.operaciones:
            if op.accion not in ACCIONES_QUE_ESCRIBEN:
                continue
            try:
                with transaction.atomic():
                    op.resultado = self._aplicar_operacion(op)
            except Exception as error:  # noqa: BLE001 - el error se informa en la fila y sigue
                op.accion = Accion.ERROR
                op.resultado = _mensaje_de_error(error)
        return self.operaciones

    def _observaciones(self, op: Operacion) -> str:
        return f"Importado de planilla {op.origen.lower()} {op.planilla}, fila {op.fila}."[:255]

    def _aplicar_operacion(self, op: Operacion) -> str:
        if op.origen == "BANCO":
            return self._aplicar_banco(op)
        return self._aplicar_efectivo(op)

    def _concepto_banco(self, op: Operacion) -> str:
        return f"TF {op.concepto.strip().title()}"[:160]

    def _aplicar_banco(self, op: Operacion) -> str:
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
        if op.proveedor is not None:
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
                    "proveedor",
                    "rubro",
                    "periodo",
                    "deudas",
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
                        op.proveedor.razon_social if op.proveedor else "",
                        op.rubro.nombre if op.rubro else "",
                        f"{op.periodo:%m/%Y}" if op.periodo else "",
                        " | ".join(
                            f"#{d.pk} {d.fecha_emision:%d/%m} {dinero(i)}" for d, i in op.asignaciones
                        ),
                        op.motivo,
                        op.aviso,
                        op.resultado,
                    ]
                )


def _mensaje_de_error(error) -> str:
    if isinstance(error, ValidationError):
        if hasattr(error, "message_dict"):
            return "; ".join(f"{k}: {' '.join(v)}" for k, v in error.message_dict.items())
        return " ".join(error.messages)
    return f"{type(error).__name__}: {error}"


def filas_efectivo_de_directorio(directorio) -> list[FilaEfectivo]:
    """Lee `efectivo_<PLANILLA>.csv` (EC1, EC2, EB1, EB2, PP, VIVRE, YH)."""
    filas = []
    for ruta in sorted(Path(directorio).glob("efectivo_*.csv")):
        planilla = ruta.stem.split("_", 1)[1].upper()
        if planilla not in COLUMNA_A_SUCURSAL:
            raise ValidationError(f"{ruta.name}: no se a que sucursal corresponde '{planilla}'.")
        filas.extend(leer_planilla_efectivo(ruta, planilla))
    return filas


__all__ = [
    "Accion",
    "Importador",
    "Operacion",
    "filas_efectivo_de_directorio",
    "leer_planilla_banco",
    "leer_planilla_efectivo",
    "normalizar",
    "parsear_fecha",
    "parsear_monto",
]
