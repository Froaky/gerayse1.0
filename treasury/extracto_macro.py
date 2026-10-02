"""Extracto del banco Macro ("Ultimos movimientos") y su cruce con el desglose.

El extracto llega como Excel convertido del PDF: muchas tablas con fecha,
referencia, causal, concepto, importe con signo y saldo, del movimiento mas
nuevo al mas viejo. Es la verdad del banco. La planilla de transferencias de
tesoreria (el "desglose") dice a que proveedor, rubro y sucursal corresponde
cada pago, pero tiene errores de tipeo, fechas de orden y no de debito, y
porciones que se pagaron juntas en una sola transferencia. Por eso el cruce es
por importe y fecha cercana, de lo mas seguro a lo menos seguro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import combinations

from django.core.exceptions import ValidationError

from treasury.importacion_texto import CENTAVO, dinero, normalizar, parsear_fecha, parsear_monto, texto_de_celda
from treasury.lectura_xlsx import leer_xlsx

# Cuanto se puede correr el debito respecto de la fecha anotada en la planilla:
# la planilla anota el dia de la orden (a veces un sabado) y el banco debita
# el dia habil siguiente; algunas filas se anotaron unos dias despues.
VENTANA_DIAS = (-3, 10)
# Diferencia que se acepta como error de tipeo entre planilla y banco: un peso,
# o el 0,1% del importe. Por encima no se adivina: va a revisar.
TOLERANCIA_MINIMA = Decimal("1.00")
TOLERANCIA_RELATIVA = Decimal("0.001")

# Causales del Macro de cheques debitados (camara y canje interno): los
# e-cheq los carga tesoreria aparte, cuando se debitan.
CAUSALES_CHEQUE = {"85", "2837"}


class Categoria:
    CHEQUE = "CHEQUE"
    COBRO_TRANSFERENCIA = "COBRO_TRANSFERENCIA"  # "PAGO PCT": cobros a clientes, cientos por dia
    LIQUIDACION_TARJETA = "LIQUIDACION_TARJETA"  # "LIQ COMER PAYWAY"
    OTRO_CREDITO = "OTRO_CREDITO"
    DEBITO = "DEBITO"


@dataclass(frozen=True)
class LineaExtracto:
    orden: int
    fecha: date
    referencia: str
    causal: str
    concepto: str
    importe: Decimal  # con signo: negativo es debito
    saldo: Decimal

    @property
    def monto(self) -> Decimal:
        return abs(self.importe)

    @property
    def es_debito(self) -> bool:
        return self.importe < 0

    def descripcion(self) -> str:
        return f"{self.fecha:%d/%m} {self.concepto} {dinero(self.importe)} (ref {self.referencia})"


def categoria(linea: LineaExtracto) -> str:
    texto = normalizar(linea.concepto)
    if not linea.es_debito:
        if "PAGO PCT" in texto:
            return Categoria.COBRO_TRANSFERENCIA
        if "LIQ COMER" in texto:
            return Categoria.LIQUIDACION_TARJETA
        return Categoria.OTRO_CREDITO
    if linea.causal in CAUSALES_CHEQUE or re.search(r"\bE?CHEQ(UE)?\b", texto):
        return Categoria.CHEQUE
    return Categoria.DEBITO


def leer_extracto_macro(ruta) -> list[LineaExtracto]:
    """Lineas del extracto en orden cronologico. Verifica que cada saldo sea el
    anterior mas el importe: si una tabla del PDF se perdio o se duplico al
    convertirlo, la cadena se corta y se frena antes de importar nada."""
    lineas = []
    for _hoja, filas in leer_xlsx(ruta):
        for _numero, valores in filas:
            if not valores:
                continue
            fecha = parsear_fecha(valores[0])
            if fecha is None:
                continue
            resto = [v for v in valores[1:] if v != ""]
            if len(resto) < 4:
                raise ValidationError(f"Linea del extracto incompleta: {valores}")
            importe, saldo = parsear_monto(resto[-2]), parsear_monto(resto[-1])
            if importe is None or saldo is None:
                raise ValidationError(f"Linea del extracto sin importe o saldo: {valores}")
            lineas.append(
                LineaExtracto(
                    orden=0,
                    fecha=fecha,
                    referencia=texto_de_celda(resto[0]),
                    causal=texto_de_celda(resto[1]),
                    concepto=" ".join(texto_de_celda(v) for v in resto[2:-2]),
                    importe=importe,
                    saldo=saldo,
                )
            )
    lineas.reverse()
    for anterior, actual in zip(lineas, lineas[1:]):
        if anterior.saldo + actual.importe != actual.saldo:
            raise ValidationError(
                "El extracto no cierra: despues de "
                f"{anterior.descripcion()} con saldo {dinero(anterior.saldo)} viene "
                f"{actual.descripcion()} con saldo {dinero(actual.saldo)}. Falta o sobra una linea."
            )
    return [replace(linea, orden=indice) for indice, linea in enumerate(lineas)]


def saldo_inicial(lineas: list[LineaExtracto]) -> Decimal | None:
    if not lineas:
        return None
    return lineas[0].saldo - lineas[0].importe


@dataclass(eq=False)
class Porcion:
    fila: object  # FilaBanco del desglose
    columna: str  # EC1, EC2, EB, EB2, PP, H; "" si la fila no tiene reparto
    monto: Decimal


@dataclass
class Cruce:
    linea: LineaExtracto
    asignaciones: list  # [(Porcion, importe de esta linea que le toca)]
    tipo: str
    diferencia: Decimal = Decimal("0.00")  # banco menos planilla, en los aproximados


class TipoCruce:
    EXACTO = "EXACTO"
    PORCIONES_JUNTAS = "PORCIONES_JUNTAS"  # varias sucursales de la fila en una transferencia
    FILA_EN_VARIAS_LINEAS = "FILA_EN_VARIAS_LINEAS"  # un pago anotado que el banco partio
    APROXIMADO = "APROXIMADO"  # error de tipeo chico: manda el importe del banco


@dataclass
class ResultadoCruce:
    cruces: list = field(default_factory=list)
    porciones_sin_linea: list = field(default_factory=list)
    lineas_sin_fila: list = field(default_factory=list)


def porciones_de(fila) -> list[Porcion]:
    if fila.reparto:
        return [Porcion(fila, columna, monto) for columna, monto in fila.reparto.items()]
    return [Porcion(fila, "", fila.monto)]


def _en_ventana(linea: LineaExtracto, fila) -> bool:
    dias = (linea.fecha - fila.fecha).days
    return VENTANA_DIAS[0] <= dias <= VENTANA_DIAS[1]


def _distancia(linea: LineaExtracto, fila) -> tuple:
    dias = (linea.fecha - fila.fecha).days
    # Mas cerca primero; a igual distancia, el debito posterior a la orden.
    return (abs(dias), dias < 0)


def _tolerancia(monto: Decimal) -> Decimal:
    return max(TOLERANCIA_MINIMA, (monto * TOLERANCIA_RELATIVA).quantize(CENTAVO, rounding=ROUND_HALF_UP))


def cruzar_debitos(lineas: list[LineaExtracto], filas: list) -> ResultadoCruce:
    """Asigna a cada debito del extracto las porciones del desglose que paga.

    Pasadas, de la mas segura a la menos:
    1. una porcion = una linea, importe exacto;
    2. varias porciones de una misma fila = una linea (Cosalta EB+PP);
    3. una fila de una sola porcion = dos o tres lineas del mismo dia;
    4. una porcion, o lo que queda de una fila, = una linea con una diferencia
       chica (error de tipeo). Manda el importe del banco.
    Siempre dentro de VENTANA_DIAS y eligiendo la linea mas cercana en fecha.
    """
    libres = {linea.orden: linea for linea in lineas}
    usadas = set()
    resultado = ResultadoCruce()
    filas = sorted(filas, key=lambda f: (f.fecha, getattr(f, "numero", 0)))
    porciones_por_fila = [(fila, porciones_de(fila)) for fila in filas]

    def libres_de(porciones):
        return [p for p in porciones if id(p) not in usadas]

    def tomar(linea, asignaciones, tipo, diferencia=Decimal("0.00")):
        libres.pop(linea.orden)
        for porcion, _importe in asignaciones:
            usadas.add(id(porcion))
        resultado.cruces.append(Cruce(linea=linea, asignaciones=asignaciones, tipo=tipo, diferencia=diferencia))

    # 1. Porcion exacta. Se arman todos los pares posibles y se asignan de los
    # mas cercanos en fecha a los mas lejanos, para que un pago de un importe
    # que se repite (Las Flores paga 62.500 casi todos los dias) no se quede
    # con el debito de otro dia.
    pares = []
    for fila, porciones in porciones_por_fila:
        for porcion in porciones:
            for linea in lineas:
                if linea.monto == porcion.monto and _en_ventana(linea, fila):
                    pares.append((_distancia(linea, fila), linea.orden, id(porcion), linea, porcion))
    for _dist, orden, id_porcion, linea, porcion in sorted(pares, key=lambda par: par[:3]):
        if orden in libres and id_porcion not in usadas:
            tomar(linea, [(porcion, porcion.monto)], TipoCruce.EXACTO)

    # 2. Varias porciones de la misma fila en una sola linea, de los grupos mas
    # grandes a los mas chicos (la fila entera primero).
    for fila, porciones in porciones_por_fila:
        hubo = True
        while hubo:
            hubo = False
            pendientes = libres_de(porciones)
            for tamano in range(len(pendientes), 1, -1):
                for grupo in combinations(pendientes, tamano):
                    total = sum(p.monto for p in grupo)
                    candidatas = [
                        linea for linea in libres.values() if linea.monto == total and _en_ventana(linea, fila)
                    ]
                    if candidatas:
                        linea = min(candidatas, key=lambda l: _distancia(l, fila))
                        tomar(linea, [(p, p.monto) for p in grupo], TipoCruce.PORCIONES_JUNTAS)
                        hubo = True
                        break
                if hubo:
                    break

    # 3. Una fila de una sola porcion pendiente que el banco debito en 2 o 3
    # lineas el mismo dia (Edesa, intereses AFIP).
    for fila, porciones in porciones_por_fila:
        pendientes = libres_de(porciones)
        if len(pendientes) != 1:
            continue
        porcion = pendientes[0]
        por_dia = {}
        for linea in libres.values():
            if _en_ventana(linea, fila) and linea.monto < porcion.monto:
                por_dia.setdefault(linea.fecha, []).append(linea)
        encontrado = None
        for dia in sorted(por_dia, key=lambda d: abs((d - fila.fecha).days)):
            for cantidad in (2, 3):
                for grupo in combinations(por_dia[dia], cantidad):
                    if sum(l.monto for l in grupo) == porcion.monto:
                        encontrado = grupo
                        break
                if encontrado:
                    break
            if encontrado:
                break
        if encontrado:
            for linea in encontrado:
                libres.pop(linea.orden)
                resultado.cruces.append(
                    Cruce(linea=linea, asignaciones=[(porcion, linea.monto)], tipo=TipoCruce.FILA_EN_VARIAS_LINEAS)
                )
            usadas.add(id(porcion))

    # 4. Aproximados: una porcion sola, o todo lo que queda de una fila.
    for fila, porciones in porciones_por_fila:
        pendientes = libres_de(porciones)
        grupos = [[p] for p in pendientes]
        if len(pendientes) > 1:
            grupos.append(pendientes)
        for grupo in grupos:
            if any(id(p) in usadas for p in grupo):
                continue
            total = sum(p.monto for p in grupo)
            candidatas = [
                linea
                for linea in libres.values()
                if _en_ventana(linea, fila) and abs(linea.monto - total) <= _tolerancia(total)
            ]
            if not candidatas:
                continue
            linea = min(candidatas, key=lambda l: (abs(l.monto - total), _distancia(l, fila)))
            diferencia = linea.monto - total
            # La diferencia la absorbe la porcion mas grande: es un error de
            # tipeo del total, y asi el debito suma exacto lo que dice el banco.
            mayor = max(grupo, key=lambda p: p.monto)
            asignaciones = [(p, p.monto + (diferencia if p is mayor else Decimal("0.00"))) for p in grupo]
            tomar(linea, asignaciones, TipoCruce.APROXIMADO, diferencia)

    resultado.porciones_sin_linea = [p for _f, porciones in porciones_por_fila for p in porciones if id(p) not in usadas]
    resultado.lineas_sin_fila = sorted(libres.values(), key=lambda l: l.orden)
    return resultado
