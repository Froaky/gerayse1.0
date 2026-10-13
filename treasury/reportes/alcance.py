"""Alcance de un reporte: empresa, sucursal (o general) y periodo.

La empresa da la razon social del encabezado (`Empresa.nombre`; las empresas se
crearon desde `Sucursal.razon_social` en cashops/0010) y su CUIT
(`Empresa.identificador_fiscal`). La validacion de que la sucursal pertenezca a
la empresa se hace en el formulario: los snapshots ignoran `empresa_ids` cuando
reciben una sucursal, asi que este objeto asume que ya llega validado.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

from django.utils import timezone
from django.utils.text import slugify

MESES = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)


def nombre_mes(value: date) -> str:
    """"Septiembre 2026"."""
    return f"{MESES[value.month - 1]} {value.year}"


def fecha_label(value: date) -> str:
    return value.strftime("%d/%m/%Y")


def rango_de_meses(mes_desde: date, mes_hasta: date, *, hoy: date | None = None) -> tuple[date, date]:
    """Del dia 1 de `mes_desde` al ultimo dia de `mes_hasta`; si `mes_hasta` es el
    mes en curso, hasta hoy (la lectura economica no tiene datos del futuro)."""
    hoy = hoy or timezone.localdate()
    date_from = mes_desde.replace(day=1)
    ultimo = calendar.monthrange(mes_hasta.year, mes_hasta.month)[1]
    date_to = mes_hasta.replace(day=ultimo)
    if date_to > hoy >= mes_hasta.replace(day=1):
        date_to = hoy
    return date_from, date_to


@dataclass(frozen=True)
class ReportScope:
    empresa: object  # cashops.models.Empresa
    sucursal: object | None = None  # cashops.models.Sucursal; None = general de la empresa
    date_from: date | None = None
    date_to: date | None = None
    # Empresa con una sola sucursal activa (Mapogo): el general se rotula con ella.
    sucursal_unica: object | None = None
    emitido: datetime | None = None

    @property
    def empresa_ids(self) -> list[int]:
        return [self.empresa.pk]

    @property
    def razon_social(self) -> str:
        return self.empresa.nombre

    @property
    def cuit(self) -> str | None:
        return (self.empresa.identificador_fiscal or "").strip() or None

    @property
    def es_general(self) -> bool:
        return self.sucursal is None

    @property
    def alcance_label(self) -> str:
        if self.sucursal is not None:
            return f"Sucursal {self.sucursal.nombre}"
        if self.sucursal_unica is not None:
            return f"Sucursal {self.sucursal_unica.nombre} (única de la empresa)"
        return "General (todas las sucursales)"

    @property
    def alcance_slug(self) -> str:
        if self.sucursal is not None:
            return slugify(self.sucursal.codigo) or f"sucursal-{self.sucursal.pk}"
        return "general"

    @property
    def periodo_label(self) -> str:
        if self.date_from is None or self.date_to is None:
            return ""
        return f"Período {fecha_label(self.date_from)} al {fecha_label(self.date_to)}"

    @property
    def emitido_label(self) -> str:
        emitido = timezone.localtime(self.emitido) if self.emitido else timezone.localtime()
        return emitido.strftime("%d/%m/%Y %H:%M")

    def filename(self, slug: str, *, sufijo: str | None = None) -> str:
        """`{slug}_{empresa}_{general|codigo}_{aaaa-mm}_{aaaa-mm}.pdf`, o con el sufijo
        que pase el reporte (el desglose de deudas usa `al_aaaa-mm-dd`)."""
        partes = [slug, slugify(self.razon_social) or f"empresa-{self.empresa.pk}", self.alcance_slug]
        if sufijo:
            partes.append(sufijo)
        elif self.date_from and self.date_to:
            partes += [self.date_from.strftime("%Y-%m"), self.date_to.strftime("%Y-%m")]
        return "_".join(partes) + ".pdf"
