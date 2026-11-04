"""Reporte 1: situacion economica del periodo.

Fuente unica: `build_economic_period_snapshot`, la misma lectura de la seccion
"Situacion economica y rentabilidad" del dashboard. Aca no se calcula plata:
cada KPI y cada celda es una clave del snapshot. Las unicas condiciones son de
presentacion: que filas se listan y el % cuando no hubo ventas.

Desde el 04/11/2026, a pedido de Tais, el PDF muestra solo ventas, egresos y
resultado: sin separar caja, tesoreria y facturas, sin la palabra "deuda" y sin
objetivos. La pantalla del dashboard sigue con todo el detalle. Lo que falta
pagar sale en el reporte "Desglose de deudas".
"""

from __future__ import annotations

from treasury.services import build_economic_period_snapshot

from .alcance import ReportScope
from .documento import Kpi, NoteBlock, Pct, ReportDocument, TableBlock
from .pdf import format_value

SLUG = "situacion-economica"
TITULO = "Situación económica"

TABLA_RUBROS = "Egresos por rubro"
COLUMNAS = ["Rubro", "Egresos", "% s/ventas"]
ANCHOS = [0.56, 0.26, 0.18]
NOTA_EGRESOS = (
    "Egresos = gastos de caja + gastos de tesorería + facturas de proveedores del período, "
    "pagadas o no. Lo que falta pagar está en el reporte Desglose de deudas."
)
NOTA_PORCENTAJE = "El % de cada rubro es sobre el total de ventas del período."
NOTA_SOLO_VENTAS = (
    "No se listan los rubros que en el período solo tuvieron ventas, sin egresos; "
    "esas ventas sí cuentan en el total de ventas."
)


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return singular if cantidad == 1 else plural


def _pct_sobre_ventas(snapshot: dict, valor) -> Pct:
    # Sin ventas el snapshot devuelve 0,00 %, pero el porcentaje no existe: "—".
    return Pct(valor if snapshot["sales_total"] > 0 else None)


def _solo_ventas(item: dict) -> bool:
    # Fila que el snapshot arma solo por ventas (las de tarjeta no tienen rubro y
    # caen en "Sin rubro"): sin egresos no aporta a una tabla de egresos.
    return not item["total_expense"]


def _kpis(snapshot: dict) -> list[Kpi]:
    margen = ""
    if snapshot["sales_total"] > 0:
        margen = "Margen " + format_value(Pct(snapshot["margin_pct"]))
    return [
        Kpi("Ventas", snapshot["sales_total"]),
        Kpi("Egresos", snapshot["expense_total"]),
        Kpi("Resultado", snapshot["economic_result"], margen),
    ]


def _nombre_fila(item: dict) -> str:
    if item.get("grupo"):
        hijos = item["children_count"]
        return f"{item['rubro_nombre']} · {hijos} {_plural(hijos, 'rubro', 'rubros')}"
    return item["rubro_nombre"]


def _tabla_rubros(snapshot: dict) -> TableBlock:
    rows = [
        [
            _nombre_fila(item),
            item["total_expense"],
            _pct_sobre_ventas(snapshot, item["expense_ratio_over_sales"]),
        ]
        for item in snapshot["items"]
        if not _solo_ventas(item)
    ]
    return TableBlock(
        title=TABLA_RUBROS,
        columns=COLUMNAS,
        rows=rows,
        widths=ANCHOS,
        numeric_cols=frozenset({1, 2}),
        empty_text="No hay egresos imputados a rubros en el período.",
    )


def _notas(snapshot: dict) -> list[NoteBlock]:
    notas = [NOTA_EGRESOS, NOTA_PORCENTAJE]
    if any(_solo_ventas(item) for item in snapshot["items"]):
        notas.append(NOTA_SOLO_VENTAS)

    egresos = snapshot["treasury_unmapped_expenses_count"]
    if egresos:
        notas.append(
            f"{egresos} {_plural(egresos, 'egreso', 'egresos')} de tesorería sin imputar "
            f"(sucursal, rubro o período) por {format_value(snapshot['treasury_unmapped_expenses_total'])} "
            f"{_plural(egresos, 'no suma', 'no suman')} en los egresos ni en el resultado."
        )

    facturas = snapshot["unmapped_payables_count"]
    if facturas:
        notas.append(
            f"{facturas} {_plural(facturas, 'factura', 'facturas')} del período sin rubro operativo "
            f"por {format_value(snapshot['unmapped_payables_total'])} "
            f"{_plural(facturas, 'no suma', 'no suman')} en los egresos ni en el resultado."
        )
    return [NoteBlock(texto) for texto in notas]


def lectura_sucursal(scope: ReportScope):
    """Sucursal con la que se lee el snapshot. Una empresa de sucursal unica (sin
    otras, ni cerradas) se lee por esa sucursal, igual que el dashboard."""
    return scope.sucursal or scope.sucursal_unica


def build_situacion_economica(scope: ReportScope) -> ReportDocument:
    # Con sucursal el snapshot ignora empresa_ids; la pertenencia la valida el form.
    snapshot = build_economic_period_snapshot(
        date_from=scope.date_from,
        date_to=scope.date_to,
        sucursal=lectura_sucursal(scope),
        empresa_ids=scope.empresa_ids,
    )
    return ReportDocument(
        slug=SLUG,
        titulo=TITULO,
        razon_social=scope.razon_social,
        cuit=scope.cuit,
        alcance_label=scope.alcance_label,
        periodo_label=scope.periodo_label,
        emitido_label=scope.emitido_label,
        kpis=_kpis(snapshot),
        blocks=[_tabla_rubros(snapshot), *_notas(snapshot)],
    )


def nombre_archivo(scope: ReportScope) -> str:
    return scope.filename(SLUG)
