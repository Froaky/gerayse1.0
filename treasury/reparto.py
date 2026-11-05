"""Reparto de un egreso comun entre sucursales con una clave de porcentajes.

Pedido de tesoreria (04/11/2026): los impuestos de ARMADI van con la clave de
impuestos y los sueldos de estructura en partes iguales entre los locales. Un
egreso repartido se registra como un egreso por sucursal con su parte: es la
convencion que ya se usaba a mano, asi que ninguna lectura cambia.

Este modulo solo calcula las partes. Registrarlas es de los servicios
(`register_egreso_tesoreria_repartido`, `create_bank_movement_repartido`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

CENTAVO = Decimal("0.01")
CIEN = Decimal("100")


@dataclass(frozen=True)
class ParteReparto:
    sucursal: object
    porcentaje: Decimal
    importe: Decimal


def _dinero(valor: Decimal) -> str:
    entero, _, decimales = f"{valor:.2f}".partition(".")
    signo = "-" if entero.startswith("-") else ""
    return signo + f"{abs(int(entero)):,}".replace(",", ".") + "," + decimales


def _porcentaje(valor: Decimal) -> str:
    texto = f"{valor.normalize():f}"
    return texto.replace(".", ",")


def validar_clave(clave, *, empresa_id=None) -> list:
    """Las partes de la clave, o ValidationError si no se puede usar.

    Se valida al usar y no solo al guardar: una sucursal puede desactivarse o
    cambiar de empresa despues de armada la clave.
    """
    errores = []
    if not clave.activa:
        errores.append("La clave de reparto está desactivada.")
    if empresa_id is not None and clave.empresa_id != empresa_id:
        errores.append("La clave de reparto es de otra empresa.")
    partes = list(clave.partes.select_related("sucursal").order_by("id"))
    if not partes:
        errores.append("La clave de reparto no tiene sucursales.")
    total = sum((parte.porcentaje for parte in partes), Decimal("0"))
    if partes and total != CIEN:
        errores.append(f"Los porcentajes de la clave suman {_porcentaje(total)} % y tienen que sumar 100 %.")
    for parte in partes:
        if parte.sucursal.empresa_id != clave.empresa_id:
            errores.append(f"La sucursal {parte.sucursal} no es de la empresa de la clave.")
        elif not parte.sucursal.activa:
            errores.append(f"La sucursal {parte.sucursal} está inactiva.")
    if errores:
        raise ValidationError({"clave_reparto": errores})
    return partes


def repartir_importe(monto: Decimal, clave, *, empresa_id=None) -> list[ParteReparto]:
    """Reparte `monto` con la clave, redondeando a centavos.

    La ultima sucursal se lleva la diferencia de redondeo, asi las partes suman
    exactamente el total. Una parte que da cero no se registra (un egreso de
    $ 0 no existe).
    """
    if monto is None or monto <= 0:
        raise ValidationError({"monto": "El importe debe ser mayor que cero."})
    partes = validar_clave(clave, empresa_id=empresa_id)
    resultado = []
    asignado = Decimal("0.00")
    for indice, parte in enumerate(partes):
        if indice == len(partes) - 1:
            importe = monto - asignado
        else:
            importe = (monto * parte.porcentaje / CIEN).quantize(CENTAVO, rounding=ROUND_HALF_UP)
            # Con importes de centavos el redondeo hacia arriba puede pasarse
            # del total: ninguna parte toma mas de lo que queda sin asignar.
            importe = min(importe, monto - asignado)
        asignado += importe
        if importe > 0:
            resultado.append(ParteReparto(parte.sucursal, parte.porcentaje, importe))
    return resultado


def token_de_parte(token_alta, sucursal):
    """Token de alta de una parte: deriva del token del formulario, asi
    reenviar el mismo formulario devuelve las mismas partes y no duplica."""
    if token_alta is None:
        return None
    return uuid.uuid5(uuid.UUID(str(token_alta)), f"reparto-sucursal-{sucursal.pk}")


def observaciones_de_parte(observaciones: str, clave, parte: ParteReparto, total: Decimal) -> str:
    texto = f"Reparto {clave.nombre}: {_porcentaje(parte.porcentaje)} % de $ {_dinero(total)}"
    observaciones = (observaciones or "").strip()
    return (f"{observaciones} | {texto}" if observaciones else texto)[:255]


def etiqueta_de_clave(clave) -> str:
    """Texto del selector: el nombre y como reparte, para elegir sin dudas."""
    partes = ", ".join(
        f"{parte.sucursal.codigo} {_porcentaje(parte.porcentaje)} %"
        for parte in clave.partes.select_related("sucursal").order_by("id")
    )
    return f"{clave.nombre} ({partes})" if partes else clave.nombre
