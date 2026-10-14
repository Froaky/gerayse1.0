"""Reporte 3: desglose de deudas abiertas por mes de imputacion.

Lee `build_open_payables_breakdown`, que usa el mismo alcance que la deuda
pendiente de la lectura financiera: el saldo total del reporte concilia con
"pending_total". Los subtotales y el total salen del servicio; aca no se suma.
"""

from __future__ import annotations

from django.utils import timezone

from treasury.services import build_open_payables_breakdown

from .alcance import ReportScope, fecha_label, nombre_mes
from .documento import Kpi, NoteBlock, ReportDocument, TableBlock
from .pdf import format_value

SLUG = "desglose-deudas"
TITULO = "Desglose de deudas"

TABLA = "Facturas abiertas por mes"
SIN_DEUDAS = "No hay deudas abiertas para el alcance elegido."
SIN_SUCURSAL = "Sin sucursal"
NOTA_VENCIDA = "* Vencida a la fecha de emisión del reporte."
NOTA_PAGADO = "Pagado = importe de la factura menos su saldo pendiente."
NOTA_SIN_FONDOS = "Pagado incluye {monto} cancelados sin movimiento de fondos (regularización o proveedor interno)."
NOTA_INTERNO = "(interno): proveedor interno; sus facturas se cancelan sin movimiento de fondos."

COLUMNAS_GENERAL = ["Proveedor", "Comprobante", "Sucursal", "Emisión", "Vence", "Importe", "Pagado", "Saldo"]
# Con la letra de las tablas (Public Sans 7,5): fechas al ancho justo de "dd/mm/aaaa *", el
# comprobante corta en el espacio ("FC A" / "00004-00012345"), la sucursal es el codigo
# ("BELGRANO", "PP-OV-06") y "Sin sucursal" entra en una linea. Las cifras son proporcionales:
# entran montos de hasta unos $ 13 millones; uno mas ancho achica las cifras del cuerpo, y los
# subtotales en negrita se achican aparte (ver pdf.numeric_font_sizes). Lo que sobra va al proveedor.
ANCHOS_GENERAL = [0.151, 0.144, 0.104, 0.101, 0.11, 0.13, 0.13, 0.13]
COLUMNAS_SUCURSAL = ["Proveedor", "Comprobante", "Emisión", "Vence", "Importe", "Pagado", "Saldo"]
ANCHOS_SUCURSAL = [0.255, 0.144, 0.101, 0.11, 0.13, 0.13, 0.13]


def _filtro_de_periodos(scope: ReportScope):
    """(desde, hasta) del filtro de periodos, o (None, None) si no hay filtro."""
    if (scope.date_from is None) != (scope.date_to is None):
        raise ValueError("El filtro de períodos necesita mes desde y mes hasta.")
    return scope.date_from, scope.date_to


def _facturas(cantidad: int) -> str:
    return f"{format_value(cantidad)} {'factura' if cantidad == 1 else 'facturas'}"


def _proveedor(payable) -> str:
    nombre = payable.proveedor.razon_social
    return f"{nombre} (interno)" if payable.proveedor.es_interno else nombre


def _vence(payable, reference_date):
    if payable.fecha_vencimiento < reference_date:
        return f"{fecha_label(payable.fecha_vencimiento)} *"
    return payable.fecha_vencimiento


def _fila(cells: dict, columnas: list[str]) -> list:
    return [cells.get(columna, "") for columna in columnas]


def build_desglose_deudas(scope: ReportScope) -> ReportDocument:
    desde, hasta = _filtro_de_periodos(scope)
    data = build_open_payables_breakdown(
        sucursal=scope.sucursal,
        empresa_ids=scope.empresa_ids,
        periodo_desde=desde,
        periodo_hasta=hasta,
    )
    reference_date = data["reference_date"]
    columnas = COLUMNAS_GENERAL if scope.es_general else COLUMNAS_SUCURSAL
    anchos = ANCHOS_GENERAL if scope.es_general else ANCHOS_SUCURSAL

    rows = []
    group_rows = set()
    total_rows = set()
    hay_internos = False
    for group in data["groups"]:
        mes = nombre_mes(group["periodo"])
        group_rows.add(len(rows))
        rows.append(_fila({"Proveedor": f"{mes} · {_facturas(group['count'])}"}, columnas))
        for payable in group["payables"]:
            hay_internos = hay_internos or payable.proveedor.es_interno
            rows.append(
                _fila(
                    {
                        "Proveedor": _proveedor(payable),
                        "Comprobante": (payable.referencia_comprobante or "").strip() or payable.concepto,
                        "Sucursal": payable.sucursal.codigo if payable.sucursal_id else SIN_SUCURSAL,
                        "Emisión": payable.fecha_emision,
                        "Vence": _vence(payable, reference_date),
                        "Importe": payable.importe_total,
                        "Pagado": payable.total_pagado,
                        "Saldo": payable.saldo_pendiente,
                    },
                    columnas,
                )
            )
        total_rows.add(len(rows))
        rows.append(
            _fila(
                {"Proveedor": f"Subtotal {mes}", "Importe": group["importe_total"], "Saldo": group["saldo_total"]},
                columnas,
            )
        )
    if rows:
        total_rows.add(len(rows))
        total = {"Proveedor": "Total", "Importe": data["importe_total"], "Saldo": data["saldo_total"]}
        rows.append(_fila(total, columnas))

    periodo_label = f"Deuda abierta al {fecha_label(reference_date)}"
    if desde is not None:
        periodo_label += f" · períodos {desde:%m/%Y} a {hasta:%m/%Y}"

    meses = len(data["groups"])
    meses_hint = ""
    if meses:
        primero, ultimo = data["groups"][0]["periodo"], data["groups"][-1]["periodo"]
        meses_hint = nombre_mes(primero) if meses == 1 else f"{nombre_mes(primero)} a {nombre_mes(ultimo)}"

    blocks = [
        TableBlock(
            title=TABLA,
            columns=columnas,
            rows=rows,
            widths=anchos,
            numeric_cols=frozenset(columnas.index(nombre) for nombre in ("Importe", "Pagado", "Saldo")),
            group_rows=frozenset(group_rows),
            total_rows=frozenset(total_rows),
            empty_text=SIN_DEUDAS,
        )
    ]
    if rows:
        blocks += [NoteBlock(NOTA_VENCIDA), NoteBlock(NOTA_PAGADO)]
    # US-3.20: "Pagado" incluye lo cancelado sin movimiento de fondos; la nota lo aclara.
    if data["settled_without_funds_total"] > 0:
        blocks.append(NoteBlock(NOTA_SIN_FONDOS.format(monto=format_value(data["settled_without_funds_total"]))))
    if hay_internos:
        blocks.append(NoteBlock(NOTA_INTERNO))

    return ReportDocument(
        slug=SLUG,
        titulo=TITULO,
        razon_social=scope.razon_social,
        cuit=scope.cuit,
        alcance_label=scope.alcance_label,
        periodo_label=periodo_label,
        emitido_label=scope.emitido_label,
        kpis=[
            Kpi("Deuda abierta", data["saldo_total"], _facturas(data["count"])),
            Kpi("Vencida", data["vencido_total"], _facturas(data["vencido_count"])),
            Kpi("Meses", meses, meses_hint),
        ],
        blocks=blocks,
    )


def nombre_archivo(scope: ReportScope) -> str:
    """`desglose-deudas_{empresa}_{alcance}_al_aaaa-mm-dd[_periodos_aaaa-mm_aaaa-mm].pdf`."""
    desde, hasta = _filtro_de_periodos(scope)
    sufijo = f"al_{timezone.localdate():%Y-%m-%d}"
    if desde is not None:
        sufijo += f"_periodos_{desde:%Y-%m}_{hasta:%Y-%m}"
    return scope.filename(SLUG, sufijo=sufijo)
