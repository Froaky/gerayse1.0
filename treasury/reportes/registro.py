"""Reportes PDF disponibles en la UI. Sumar un reporte es sumar una entrada aca."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import deudas, economica, financiera
from .alcance import ReportScope
from .documento import ReportDocument


@dataclass(frozen=True)
class ReporteDef:
    slug: str
    titulo: str
    descripcion: str
    # True: mes desde/hasta obligatorios. False: filtro de periodos opcional.
    usa_periodo: bool
    builder: Callable[[ReportScope], ReportDocument]
    nombre_archivo: Callable[[ReportScope], str]


REPORTES = (
    ReporteDef(
        slug=economica.SLUG,
        titulo=economica.TITULO,
        descripcion="Ventas, gasto por rubro con su porcentaje sobre las ventas, resultado económico y margen.",
        usa_periodo=True,
        builder=economica.build_situacion_economica,
        nombre_archivo=economica.nombre_archivo,
    ),
    ReporteDef(
        slug=financiera.SLUG,
        titulo=financiera.TITULO,
        descripcion="Saldos de caja fuerte y bancos, movimientos del período, deuda pendiente y vencimientos.",
        usa_periodo=True,
        builder=financiera.build_situacion_financiera,
        nombre_archivo=financiera.nombre_archivo,
    ),
    ReporteDef(
        slug=deudas.SLUG,
        titulo=deudas.TITULO,
        descripcion="Facturas abiertas agrupadas por el mes al que corresponden, con subtotales por mes.",
        usa_periodo=False,
        builder=deudas.build_desglose_deudas,
        nombre_archivo=deudas.nombre_archivo,
    ),
)

REGISTRO = {reporte.slug: reporte for reporte in REPORTES}
