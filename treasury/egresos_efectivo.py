"""Egresos en efectivo, uno por uno, para conciliar (pedido de tesoreria del 05/11/2026).

Junta en una sola lista lo que salio en efectivo:
- de la caja fuerte: pagos de facturas (EGRESO_PAGO) y egresos sin factura
  (EGRESO_ADMIN);
- de las cajas de los locales: los gastos (MovimientoCaja GASTO).

Es solo lectura: no cambia ningun saldo ni ninguna lectura existente. No entran
los depositos al banco ni los ajustes de saldo (arqueos, cierres negativos):
mueven o corrigen plata pero no son gastos, y estan en el libro de efectivo
central.

El pago de una factura no guarda sucursal ni rubro: se leen de la factura que
pago, igual que en el libro de efectivo central. Por eso el filtro por sucursal
o rubro mira tambien la factura (el libro, al filtrar por sucursal, los pierde).

Los gastos de cajas que todavia no se validaron entran marcados: el informe
economico los suma recien al validarlas, pero la plata ya salio del local.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.db.models import Q

CAJA_FUERTE = "caja_fuerte"
LOCAL = "local"
ORIGENES = {
    CAJA_FUERTE: "Caja fuerte",
    LOCAL: "Caja del local",
}
SIN_SUCURSAL = "Sin sucursal"
SIN_RUBRO = "Sin rubro"
CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass(frozen=True)
class EgresoEfectivo:
    fecha: date
    origen: str
    sucursal: object | None
    rubro: object | None
    proveedor_o_categoria: str
    detalle: str
    importe: Decimal
    referencia: str
    cargado_por: str
    # Para armar el enlace en pantalla sin que este modulo conozca las URLs.
    caja_id: int | None = None
    cuenta_por_pagar_id: int | None = None
    estado_caja: str = ""
    caja_sin_validar: bool = False

    @property
    def origen_label(self) -> str:
        return ORIGENES[self.origen]

    @property
    def sucursal_label(self) -> str:
        return self.sucursal.nombre if self.sucursal is not None else SIN_SUCURSAL

    @property
    def rubro_label(self) -> str:
        return self.rubro.nombre if self.rubro is not None else SIN_RUBRO


def _usuario(user) -> str:
    if user is None:
        return ""
    return user.get_full_name() or user.get_username()


def _egresos_caja_fuerte(*, date_from, date_to, empresa_ids, sucursal, rubro) -> list[EgresoEfectivo]:
    from .models import MovimientoCajaCentral

    movimientos = MovimientoCajaCentral.objects.filter(
        estado=MovimientoCajaCentral.Estado.REGISTRADO,
        tipo__in=[MovimientoCajaCentral.Tipo.EGRESO_PAGO, MovimientoCajaCentral.Tipo.EGRESO_ADMIN],
        fecha__range=(date_from, date_to),
    ).select_related(
        "sucursal_gasto",
        "rubro_operativo",
        "creado_por",
        "pago_tesoreria__cuenta_por_pagar__sucursal",
        "pago_tesoreria__cuenta_por_pagar__proveedor",
        "pago_tesoreria__cuenta_por_pagar__categoria__rubro_operativo",
    )
    if empresa_ids is not None:
        movimientos = movimientos.filter(caja_central__empresa_id__in=empresa_ids)
    if sucursal is not None:
        movimientos = movimientos.filter(
            Q(sucursal_gasto=sucursal)
            | Q(sucursal_gasto__isnull=True, pago_tesoreria__cuenta_por_pagar__sucursal=sucursal)
        )
    if rubro is not None:
        movimientos = movimientos.filter(
            Q(rubro_operativo=rubro)
            | Q(rubro_operativo__isnull=True, pago_tesoreria__cuenta_por_pagar__categoria__rubro_operativo=rubro)
        )
    egresos = []
    for movimiento in movimientos.order_by("fecha", "id"):
        deuda = movimiento.pago_tesoreria.cuenta_por_pagar if movimiento.pago_tesoreria_id else None
        if deuda is not None:
            egresos.append(
                EgresoEfectivo(
                    fecha=movimiento.fecha,
                    origen=CAJA_FUERTE,
                    sucursal=movimiento.sucursal_gasto or deuda.sucursal,
                    rubro=movimiento.rubro_operativo or deuda.categoria.rubro_operativo,
                    proveedor_o_categoria=deuda.proveedor.razon_social,
                    detalle=movimiento.concepto,
                    importe=movimiento.monto,
                    referencia=deuda.referencia_comprobante or f"Factura #{deuda.pk}",
                    cargado_por=_usuario(movimiento.creado_por),
                    cuenta_por_pagar_id=deuda.pk,
                )
            )
            continue
        egresos.append(
            EgresoEfectivo(
                fecha=movimiento.fecha,
                origen=CAJA_FUERTE,
                sucursal=movimiento.sucursal_gasto,
                rubro=movimiento.rubro_operativo,
                proveedor_o_categoria="Egreso sin factura",
                detalle=movimiento.concepto,
                importe=movimiento.monto,
                referencia=f"Movimiento #{movimiento.pk}",
                cargado_por=_usuario(movimiento.creado_por),
            )
        )
    return egresos


def _gastos_de_locales(*, date_from, date_to, empresa_ids, sucursal, rubro) -> list[EgresoEfectivo]:
    from cashops.models import Caja, MovimientoCaja

    gastos = (
        MovimientoCaja.objects.filter(
            tipo=MovimientoCaja.Tipo.GASTO,
            estado=MovimientoCaja.Estado.REGISTRADO,
            impacta_saldo_caja=True,
            caja__fecha_operativa__range=(date_from, date_to),
        )
        .exclude(caja__estado=Caja.Estado.ANULADA)
        .select_related("caja__sucursal", "rubro_operativo", "creado_por")
    )
    if empresa_ids is not None:
        gastos = gastos.filter(caja__sucursal__empresa_id__in=empresa_ids)
    if sucursal is not None:
        gastos = gastos.filter(caja__sucursal=sucursal)
    if rubro is not None:
        gastos = gastos.filter(rubro_operativo=rubro)
    egresos = []
    for gasto in gastos.order_by("caja__fecha_operativa", "id"):
        caja = gasto.caja
        egresos.append(
            EgresoEfectivo(
                fecha=caja.fecha_operativa,
                origen=LOCAL,
                sucursal=caja.sucursal,
                rubro=gasto.rubro_operativo,
                proveedor_o_categoria=gasto.categoria,
                detalle=gasto.observacion,
                importe=gasto.monto,
                referencia=f"Caja #{caja.pk}",
                cargado_por=_usuario(gasto.creado_por),
                caja_id=caja.pk,
                estado_caja=caja.get_validacion_estado_display(),
                caja_sin_validar=caja.validacion_estado in Caja.VALIDACION_BLOQUEA_TOTALES,
            )
        )
    return egresos


def leer_egresos_efectivo(*, date_from, date_to, empresa_ids=None, sucursal=None, rubro=None, origen="") -> dict:
    """Los egresos en efectivo del alcance pedido, ordenados por fecha.

    `empresa_ids` None es sin filtro de empresa (como el resto de las lecturas);
    una lista vacia no devuelve nada. Siempre se aplica, aunque venga sucursal.
    """
    if empresa_ids is not None and not empresa_ids:
        egresos = []
    else:
        filtros = dict(date_from=date_from, date_to=date_to, empresa_ids=empresa_ids, sucursal=sucursal, rubro=rubro)
        egresos = []
        if origen in ("", CAJA_FUERTE):
            egresos += _egresos_caja_fuerte(**filtros)
        if origen in ("", LOCAL):
            egresos += _gastos_de_locales(**filtros)
    # sort es estable: dentro de una misma caja queda el orden de carga.
    egresos.sort(key=lambda e: (e.fecha, e.origen, e.sucursal_label, e.referencia))

    total_caja_fuerte = sum((e.importe for e in egresos if e.origen == CAJA_FUERTE), Decimal("0.00"))
    total_locales = sum((e.importe for e in egresos if e.origen == LOCAL), Decimal("0.00"))
    sin_validar = [e for e in egresos if e.caja_sin_validar]

    por_rubro: dict[str, dict] = {}
    for egreso in egresos:
        fila = por_rubro.setdefault(
            egreso.rubro_label,
            {"rubro": egreso.rubro_label, "caja_fuerte": Decimal("0.00"), "locales": Decimal("0.00"), "cantidad": 0},
        )
        fila["caja_fuerte" if egreso.origen == CAJA_FUERTE else "locales"] += egreso.importe
        fila["cantidad"] += 1
    resumen_rubros = sorted(por_rubro.values(), key=lambda f: (f["rubro"] == SIN_RUBRO, f["rubro"]))
    for fila in resumen_rubros:
        fila["total"] = fila["caja_fuerte"] + fila["locales"]

    return {
        "egresos": egresos,
        "cantidad": len(egresos),
        "total": total_caja_fuerte + total_locales,
        "total_caja_fuerte": total_caja_fuerte,
        "total_locales": total_locales,
        "sin_validar_cantidad": len(sin_validar),
        "sin_validar_total": sum((e.importe for e in sin_validar), Decimal("0.00")),
        "resumen_rubros": resumen_rubros,
    }


def _texto_excel(valor) -> str:
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    return ILLEGAL_CHARACTERS_RE.sub("", str(valor or ""))


def excel_egresos_efectivo(lectura: dict, *, titulo: str, alcance: str) -> bytes:
    """La misma lista en Excel, con una columna para marcar lo conciliado."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    columnas = [
        ("Conciliado", 11),
        ("Fecha", 11),
        ("Origen", 15),
        ("Sucursal", 22),
        ("Rubro", 22),
        ("Proveedor / categoría", 28),
        ("Detalle", 44),
        ("Importe", 15),
        ("Comprobante / caja", 20),
        ("Estado de la caja", 24),
        ("Cargado por", 24),
    ]
    dinero = '"$" #,##0.00;-"$" #,##0.00;"-"'
    fuente = Font(name="Arial", size=10)
    negrita = Font(name="Arial", size=10, bold=True)

    libro = Workbook()
    hoja = libro.active
    hoja.title = "Egresos en efectivo"
    hoja["A1"] = _texto_excel(titulo)
    hoja["A1"].font = Font(name="Arial", size=13, bold=True)
    hoja["A2"] = _texto_excel(alcance)
    hoja["A2"].font = Font(name="Arial", size=9, italic=True, color="555555")
    cabecera = 4
    for indice, (nombre, ancho) in enumerate(columnas, start=1):
        celda = hoja.cell(row=cabecera, column=indice, value=nombre)
        celda.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E3D")
        celda.alignment = Alignment(vertical="center", wrap_text=True)
        hoja.column_dimensions[get_column_letter(indice)].width = ancho
    for fila, egreso in enumerate(lectura["egresos"], start=cabecera + 1):
        valores = [
            None,
            egreso.fecha,
            egreso.origen_label,
            egreso.sucursal_label,
            egreso.rubro_label,
            _texto_excel(egreso.proveedor_o_categoria),
            _texto_excel(egreso.detalle),
            egreso.importe,
            _texto_excel(egreso.referencia),
            egreso.estado_caja,
            _texto_excel(egreso.cargado_por),
        ]
        for columna, valor in enumerate(valores, start=1):
            hoja.cell(row=fila, column=columna, value=valor).font = fuente
        hoja.cell(row=fila, column=2).number_format = "dd/mm/yyyy"
        hoja.cell(row=fila, column=8).number_format = dinero
    ultima = cabecera + len(lectura["egresos"])
    hoja.auto_filter.ref = f"A{cabecera}:{get_column_letter(len(columnas))}{max(ultima, cabecera)}"
    hoja.freeze_panes = f"A{cabecera + 1}"
    etiqueta = hoja.cell(row=ultima + 2, column=7, value="Total de lo filtrado")
    etiqueta.font = negrita
    etiqueta.alignment = Alignment(horizontal="right")
    # SUBTOTAL(109) suma solo las filas visibles: al filtrar, el total acompana.
    total = hoja.cell(
        row=ultima + 2, column=8, value=f"=SUBTOTAL(109,H{cabecera + 1}:H{max(ultima, cabecera + 1)})"
    )
    total.font = negrita
    total.number_format = dinero
    salida = BytesIO()
    libro.save(salida)
    return salida.getvalue()
