"""Reporte 2: situacion financiera.

Lee solo `build_financial_period_snapshot`, la misma lectura de la cabecera del
dashboard de tesoreria, y separa lo que es SALDO a la fecha de corte (KPIs,
saldos bancarios y vencimientos) de lo que es FLUJO del periodo (movimientos).
Cada numero es una clave del snapshot: aca no se suma ni se resta plata.
"""

from __future__ import annotations

from treasury.services import build_financial_period_snapshot

from .alcance import ReportScope, fecha_label
from .documento import Kpi, NoteBlock, ReportDocument, TableBlock
from .pdf import format_value

SLUG = "situacion-financiera"
TITULO = "Situación financiera"

SIN_CUENTA_DE_SUCURSAL = "La cuenta bancaria es de la empresa: su saldo figura en el reporte General."
SIN_CUENTAS = "No hay cuentas bancarias activas para este alcance."


def nombre_archivo(scope: ReportScope) -> str:
    return scope.filename(SLUG)


def build_situacion_financiera(scope: ReportScope) -> ReportDocument:
    snapshot = build_financial_period_snapshot(
        date_from=scope.date_from,
        date_to=scope.date_to,
        sucursal=scope.sucursal,
        empresa_ids=scope.empresa_ids,
    )
    corte = fecha_label(snapshot["reference_date"])
    general = snapshot["sucursal"] is None
    blocks = [
        # La deuda del snapshot no tiene fecha: es la abierta al emitir, no la del corte.
        NoteBlock(
            f"Saldos de caja fuerte y bancos al {corte}; la deuda pendiente es la vigente a la emisión "
            f"del reporte. Los movimientos son del {fecha_label(snapshot['date_from'])} "
            f"al {fecha_label(snapshot['date_to'])}."
        ),
        _saldos_bancarios(snapshot),
        _movimientos(snapshot),
        _vencimientos(snapshot),
    ]
    blocks += [NoteBlock(text) for text in _aclaraciones(snapshot, corte)]
    return ReportDocument(
        slug=SLUG,
        titulo=TITULO,
        razon_social=scope.razon_social,
        cuit=scope.cuit,
        alcance_label=scope.alcance_label,
        periodo_label=scope.periodo_label,
        emitido_label=scope.emitido_label,
        kpis=_kpis(snapshot, corte, general=general),
        blocks=blocks,
    )


def _kpis(snapshot: dict, corte: str, *, general: bool) -> list[Kpi]:
    if general:
        caja_hint = f"Efectivo al {corte}"
        total_hint = "Caja fuerte más bancos"
    else:
        # Por sucursal la caja fuerte es el neto de lo imputado a ella, no efectivo en mano.
        caja_hint = f"Neto de movimientos imputados a la sucursal al {corte}"
        total_hint = "Caja fuerte imputada más cuentas propias de la sucursal"
    kpis = [
        Kpi("Caja fuerte", snapshot["central_cash_total"], caja_hint),
        Kpi("Bancos", snapshot["total_bank_balance"], f"Saldo bancario al {corte}"),
        Kpi("Total disponible", snapshot["total_consolidated"], total_hint),
    ]
    if snapshot["show_bank_credit_cards"]:
        kpis.append(
            Kpi(
                "Pendiente de acreditación",
                snapshot["pending_accreditation_total"],
                "Ventas con tarjeta del período menos lo acreditado",
            )
        )
    count = snapshot["pending_count"]
    facturas = f"{format_value(count)} factura{'' if count == 1 else 's'}"
    kpis.append(Kpi("Deuda pendiente", snapshot["pending_total"], facturas))
    if general:
        if snapshot["debt_vs_bank_covered"]:
            cobertura = "el banco cubre la deuda pendiente"
        else:
            cobertura = "el banco no alcanza para cubrir la deuda pendiente"
        hint = f"Saldo bancario al {corte} menos la deuda a la emisión: {cobertura}."
        kpis.append(Kpi("Banco menos deuda", snapshot["debt_vs_bank_difference"], hint))
    return kpis


def _saldos_bancarios(snapshot: dict) -> TableBlock:
    rows = [
        [str(item["account"]), item["initial_amount"], item["credits"], item["debits"], item["balance"]]
        for item in snapshot["bank_balances"]
    ]
    total_rows = frozenset()
    if rows:
        total_rows = frozenset({len(rows)})
        rows.append(["Total", "", "", "", snapshot["total_bank_balance"]])
    return TableBlock(
        title="Saldos bancarios",
        columns=["Cuenta", "Saldo inicial", "Créditos", "Débitos", "Saldo"],
        rows=rows,
        widths=[0.36, 0.16, 0.16, 0.16, 0.16],
        numeric_cols=frozenset({1, 2, 3, 4}),
        total_rows=total_rows,
        empty_text=SIN_CUENTAS if snapshot["sucursal"] is None else SIN_CUENTA_DE_SUCURSAL,
    )


def _movimientos(snapshot: dict) -> TableBlock:
    # (tipo, concepto, importe): "grupo" es el titulo de un bloque y "neto" su total.
    filas = [
        ("grupo", "Cajas de sucursal", None),
        ("", "Caja ingresos", snapshot["cash_income"]),
        ("", "Caja egresos", snapshot["cash_expense"]),
        ("neto", "Caja neto", snapshot["cash_net"]),
        ("grupo", "Caja fuerte", None),
        ("", "Entradas caja fuerte", snapshot["central_cash_income_period"]),
        ("", "Salidas caja fuerte", snapshot["central_cash_expense_period"]),
        ("", "Gastos caja fuerte (incluidos en las salidas)", snapshot["central_cash_admin_expense_period"]),
        ("neto", "Neto caja fuerte", snapshot["central_cash_net_period"]),
        ("grupo", "Banco", None),
    ]
    # Por sucursal el banco solo lee los debitos imputados: creditos y neto son de la empresa.
    if snapshot["show_bank_credit_cards"]:
        filas.append(("", "Banco créditos", snapshot["bank_credits"]))
    filas.append(("", "Banco débitos", snapshot["bank_debits"]))
    if snapshot["show_bank_credit_cards"]:
        filas.append(("neto", "Banco neto", snapshot["bank_net"]))
    return TableBlock(
        title="Movimientos del período",
        columns=["Concepto", "Importe"],
        rows=[[concepto, importe] for _, concepto, importe in filas],
        widths=[0.7, 0.3],
        numeric_cols=frozenset({1}),
        group_rows=frozenset(i for i, (tipo, _, _) in enumerate(filas) if tipo == "grupo"),
        total_rows=frozenset(i for i, (tipo, _, _) in enumerate(filas) if tipo == "neto"),
    )


def _vencimientos(snapshot: dict) -> TableBlock:
    return TableBlock(
        title="Vencimientos de deuda",
        columns=["Plazo", "Facturas", "Importe"],
        rows=[
            ["Crítico (vencidas y menos de 4 días)", snapshot["red_count"], snapshot["red_total"]],
            ["Próximo (4 a 7 días)", snapshot["yellow_count"], snapshot["yellow_total"]],
            ["En plazo (8 días o más)", snapshot["green_count"], snapshot["green_total"]],
            ["Total", snapshot["pending_count"], snapshot["pending_total"]],
        ],
        widths=[0.55, 0.15, 0.3],
        numeric_cols=frozenset({1, 2}),
        total_rows=frozenset({3}),
    )


def _aclaraciones(snapshot: dict, corte: str) -> list[str]:
    textos = []
    if snapshot["bank_balances"]:
        textos.append(
            "En Saldos bancarios, los créditos y débitos son los acumulados de cada cuenta desde su saldo "
            f"inicial (o desde su primer movimiento, si no tiene) hasta el {corte}, no los del período."
        )
    textos.append(
        "La deuda pendiente es lo que falta pagar de todas las facturas registradas hasta la emisión de "
        "este reporte, no solo las del período, descontados los pagos registrados hasta ese momento. "
        f"Los plazos se cuentan desde el {corte}."
    )
    if snapshot["sucursal"] is None:
        textos.append(
            "Banco menos deuda usa solo el saldo bancario: no incluye caja fuerte ni acreditaciones por cobrar. "
            "Pendiente de acreditación es un dato del período: ventas con tarjeta menos lo acreditado en "
            "banco, descuentos incluidos; un valor negativo indica que el banco acreditó más de lo vendido."
        )
    else:
        textos.append(
            "Por sucursal, el banco muestra solo los débitos imputados a esta sucursal y la caja fuerte "
            "solo los movimientos imputados a ella. Los créditos bancarios, aportes, depósitos y retiros "
            "de la empresa figuran en el reporte General."
        )
    return textos
