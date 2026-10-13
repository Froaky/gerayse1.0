"""Reporte 1: situacion economica del periodo.

Fuente unica: `build_economic_period_snapshot`, la misma lectura de la seccion
"Situacion economica y rentabilidad" del dashboard. Aca no se calcula plata:
cada KPI y cada celda es una clave del snapshot. Las unicas condiciones son de
presentacion: que filas se listan y el % cuando no hubo ventas.
"""

from __future__ import annotations

from treasury.services import build_economic_period_snapshot

from .alcance import ReportScope
from .documento import Kpi, NoteBlock, Pct, ReportDocument, TableBlock
from .pdf import format_value

SLUG = "situacion-economica"
TITULO = "Situación económica"

TABLA_RUBROS = "Gasto por rubro"
COLUMNAS = ["Rubro", "Caja", "Tesorería", "Deuda", "Total", "% s/ventas", "Desvío"]
ANCHOS = [0.225, 0.135, 0.135, 0.135, 0.135, 0.10, 0.135]
SIN_OBJETIVO = "Sin objetivo"


def _plural(cantidad: int, singular: str, plural: str) -> str:
    return singular if cantidad == 1 else plural


def _pct_sobre_ventas(snapshot: dict, valor) -> Pct:
    # Sin ventas el snapshot devuelve 0,00 %, pero el porcentaje no existe: "—".
    return Pct(valor if snapshot["sales_total"] > 0 else None)


def _solo_ventas(item: dict) -> bool:
    # Fila que el snapshot arma solo por ventas (las de tarjeta no tienen rubro y
    # caen en "Sin rubro"): sin gasto ni objetivo no aporta a una tabla de gasto.
    return not (
        item["cash_expense_total"] or item["treasury_expense_total"] or item["debt_total"] or item["has_objective"]
    )


def _kpis(snapshot: dict) -> list[Kpi]:
    kpis = [
        Kpi("Ventas", snapshot["sales_total"]),
        Kpi("Gasto de caja", snapshot["cash_expense_total"]),
        Kpi("Gasto de tesorería", snapshot["treasury_expense_total"]),
        Kpi(
            "Deuda del período",
            snapshot["debt_period_total"],
            "De eso falta pagar " + format_value(snapshot["debt_pending_total"]),
        ),
        Kpi("Resultado económico", snapshot["economic_result"]),
        Kpi("Margen", _pct_sobre_ventas(snapshot, snapshot["margin_pct"])),
    ]
    con_objetivo = snapshot["objective_items_count"]
    if con_objetivo > 0:
        kpis += [
            Kpi(
                "Objetivo",
                snapshot["objective_total"],
                f"{con_objetivo} {_plural(con_objetivo, 'rubro', 'rubros')} con objetivo",
            ),
            Kpi("Real comparado", snapshot["objective_scope_real_total"]),
            Kpi("Desvío vs objetivo", snapshot["deviation_total"]),
        ]
    return kpis


def _nombre_fila(item: dict) -> str:
    if item.get("grupo"):
        hijos = item["children_count"]
        return f"{item['rubro_nombre']} · {hijos} {_plural(hijos, 'rubro', 'rubros')}"
    return item["rubro_nombre"]


def _tabla_rubros(snapshot: dict) -> TableBlock:
    rows = [
        [
            _nombre_fila(item),
            item["cash_expense_total"],
            item["treasury_expense_total"],
            item["debt_total"],
            item["total_expense"],
            _pct_sobre_ventas(snapshot, item["expense_ratio_over_sales"]),
            # Mismo criterio que el dashboard, para rubros y para grupos.
            item["deviation_amount"] if item["has_objective"] else SIN_OBJETIVO,
        ]
        for item in snapshot["items"]
        if not _solo_ventas(item)
    ]
    return TableBlock(
        title=TABLA_RUBROS,
        columns=COLUMNAS,
        rows=rows,
        widths=ANCHOS,
        numeric_cols=frozenset(range(1, 7)),
        empty_text="No hay gastos imputados a rubros en el período.",
    )


def _notas(snapshot: dict, scope: ReportScope) -> list[NoteBlock]:
    notas = ["El % de cada rubro es sobre el total de ventas del período."]
    if any(_solo_ventas(item) for item in snapshot["items"]):
        notas.append(
            "No se listan los rubros que en el período solo tuvieron ventas, sin gasto ni objetivo; "
            "esas ventas sí cuentan en el total de ventas."
        )

    egresos = snapshot["treasury_unmapped_expenses_count"]
    if egresos:
        notas.append(
            f"{egresos} {_plural(egresos, 'egreso', 'egresos')} de tesorería sin imputar "
            f"(sucursal, rubro o período) por {format_value(snapshot['treasury_unmapped_expenses_total'])} "
            f"{_plural(egresos, 'no suma', 'no suman')} en los rubros ni en el resultado económico."
        )

    deudas = snapshot["unmapped_payables_count"]
    if deudas:
        notas.append(
            f"{deudas} {_plural(deudas, 'deuda', 'deudas')} del período sin rubro operativo "
            f"por {format_value(snapshot['unmapped_payables_total'])} "
            f"{_plural(deudas, 'no suma', 'no suman')} en los rubros ni en el resultado económico."
        )

    # Un grupo con objetivo en solo algunos de sus rubros: el desvio no cubre el grupo entero.
    for item in snapshot["items"]:
        if item.get("grupo") and item["has_objective"] and not item["objective_covers_all_children"]:
            notas.append(
                f"{item['rubro_nombre']}: el desvío compara {item['objective_children_count']} "
                f"de {item['children_count']} rubros del grupo; los demás no tienen objetivo cargado."
            )

    if not snapshot["branch_objectives_enabled"]:
        notas.append(
            "En el reporte general solo se comparan objetivos globales. "
            "Los objetivos propios de cada sucursal se aplican en el reporte de esa sucursal."
        )
    return [NoteBlock(texto) for texto in notas]


def lectura_sucursal(scope: ReportScope):
    """Sucursal con la que se lee el snapshot. Una empresa de sucursal unica (sin
    otras, ni cerradas) se lee por esa sucursal: asi aplica sus objetivos propios,
    que en el general no se comparan, y no hay otro reporte donde verlos."""
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
        blocks=[_tabla_rubros(snapshot), *_notas(snapshot, scope)],
    )


def nombre_archivo(scope: ReportScope) -> str:
    return scope.filename(SLUG)
