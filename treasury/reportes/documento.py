"""Documento neutro de un reporte PDF.

Los builders arman un `ReportDocument` leyendo los snapshots que ya existen; el
renderer (`pdf.py`) lo dibuja. Aca no hay Django ni formulas: los montos viajan
como `Decimal` y el renderer los formatea con `formato_money`, asi los tests
comparan numeros y no strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Pct:
    """Porcentaje ya calculado por el snapshot. Se muestra "12,34 %"; None -> "—"."""

    value: Decimal | None


# Valor de una celda o de un KPI. Decimal = plata; int = cantidad; date = dd/mm/aaaa.
Value = str | int | Decimal | Pct | date | None


@dataclass(frozen=True)
class Kpi:
    label: str
    value: Value
    hint: str = ""


@dataclass(frozen=True)
class TableBlock:
    title: str
    columns: list[str]
    rows: list[list[Value]]
    # Fracciones del ancho util de la pagina; tienen que sumar 1.
    widths: list[float]
    numeric_cols: frozenset[int] = frozenset()
    # Indices (sobre `rows`) de filas titulo de grupo: ocupan todo el ancho.
    group_rows: frozenset[int] = frozenset()
    # Indices (sobre `rows`) de subtotales y totales: van en negrita.
    total_rows: frozenset[int] = frozenset()
    empty_text: str = "Sin datos para el alcance elegido."

    def __post_init__(self):
        if len(self.widths) != len(self.columns):
            raise ValueError("TableBlock: widths y columns tienen que tener el mismo largo.")
        if abs(sum(self.widths) - 1) > 0.001:
            raise ValueError("TableBlock: widths tiene que sumar 1.")
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("TableBlock: cada fila tiene que tener una celda por columna.")


@dataclass(frozen=True)
class NoteBlock:
    text: str


Block = TableBlock | NoteBlock


@dataclass(frozen=True)
class ReportDocument:
    slug: str
    titulo: str
    razon_social: str
    cuit: str | None
    # "Rotulo: valor" ("Sucursal: Estación Belgrano"): el renderer pinta en gris lo que
    # va hasta el primer ": ". Sin ": " se muestra todo como valor ("Deuda abierta al 13/10/2026").
    alcance_label: str
    periodo_label: str
    emitido_label: str
    kpis: list[Kpi] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)

    def kpi(self, label: str) -> Kpi:
        """Busca un KPI por su rotulo (lo usan los tests y el renderer no)."""
        for item in self.kpis:
            if item.label == label:
                return item
        raise KeyError(label)

    def table(self, title: str) -> TableBlock:
        for block in self.blocks:
            if isinstance(block, TableBlock) and block.title == title:
                return block
        raise KeyError(title)

    @property
    def notes(self) -> list[str]:
        return [block.text for block in self.blocks if isinstance(block, NoteBlock)]
