"""Normalizacion de textos, importes y fechas de las planillas de tesoreria.

Lo comparten el importador de planillas y la lectura del extracto del banco.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENTAVO = Decimal("0.01")


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    texto = re.sub(r"[^A-Z0-9 ]+", " ", texto.upper())
    return re.sub(r"\s+", " ", texto).strip()


def parsear_monto(valor) -> Decimal | None:
    """Importe de una celda -> Decimal con centavos. Vacio o ilegible -> None.

    Acepta lo que escribe tesoreria ("$1.234,56", "1.234,56", "934906,5",
    "62500", "100.000") y lo que guarda Excel como numero ("100000.0",
    "87874.979999999996"). Sin coma, un punto seguido de exactamente tres
    digitos es separador de miles; si no, es el punto decimal.
    """
    if valor is None or valor == "":
        return None
    if isinstance(valor, (Decimal, int)):
        return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    if isinstance(valor, float):
        return Decimal(str(valor)).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    texto = str(valor).replace("$", "").replace(" ", "").strip()
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
        texto = texto.replace(".", "")
    try:
        return Decimal(texto).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def parsear_fecha(valor) -> date | None:
    if isinstance(valor, date):
        return valor
    match = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", str(valor or ""))
    if not match:
        return None
    dia, mes, anio = (int(parte) for parte in match.groups())
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def texto_de_celda(valor) -> str:
    """Un numero leido del Excel (una referencia, un CUIT) como texto, sin '.0'."""
    if isinstance(valor, Decimal):
        return str(valor.quantize(Decimal(1))) if valor == valor.to_integral_value() else str(valor)
    return str(valor or "").strip()


def dinero(valor: Decimal) -> str:
    entero, _, decimales = f"{valor:.2f}".partition(".")
    signo = "-" if entero.startswith("-") else ""
    return signo + f"{abs(int(entero)):,}".replace(",", ".") + "," + decimales
