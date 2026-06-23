from dataclasses import dataclass
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.utils import timezone

from .models import (
    AlertaOperativa,
    Caja,
    CanalIngreso,
    CierreCaja,
    Justificacion,
    LimiteRubroOperativo,
    MovimientoCaja,
    MovimientoCajaCorreccion,
    RubroOperativo,
    Sucursal,
    Transferencia,
    Turno,
)
from .permissions import can_assign_box_to_user, ensure_can_operate_box, ensure_closed_box_correction, is_cashops_admin


CLOSING_DIFF_THRESHOLD = Decimal("10000.00")
OPERATIONAL_WARNING_RATIO = Decimal("0.90")
PERCENTAGE_QUANTIZER = Decimal("0.01")
MAX_OPERATIONAL_LIMIT_PERCENTAGE = Decimal("100.00")
OPERATIONAL_CONTROL_BASE_CODE = "EGRESOS_OPERATIVOS_DEL_PERIODO"
OPERATIONAL_CONTROL_BASE_LABEL = "Egresos operativos del periodo"
UNCATEGORIZED_OPERATIONAL_CATEGORY_NAME = "Sin clasificar"
BRANCH_TRANSFER_DISABLED_REASON = (
    "La transferencia entre sucursales ya no esta habilitada en la operatoria actual. "
    "Mantene solo traspasos entre cajas."
)
OPERATIONAL_ALERT_SCOPE_POLICY = (
    "Las alertas equivalentes se muestran todas y se ordenan de la mas especifica a la mas general: "
    "Caja, Sucursal y Global."
)
OPERATIONAL_ALERT_SCOPE_POLICY_RULES = (
    "Rubro excedido puede coexistir en global, sucursal y caja para el mismo rubro y periodo.",
    "Diferencia grave se registra solo a nivel caja porque nace de un cierre concreto.",
    "El filtro de alcance es de lectura: no altera el motor ni consolida registros persistidos.",
)

MOVEMENT_TYPE_LABELS = {
    MovimientoCaja.Tipo.APERTURA: "Apertura",
    MovimientoCaja.Tipo.INGRESO_EFECTIVO: "Cobro en efectivo",
    MovimientoCaja.Tipo.GASTO: "Egreso operativo",
    MovimientoCaja.Tipo.VENTA_TARJETA: "Venta tarjeta (POS)",
    MovimientoCaja.Tipo.VENTA_TRANSFERENCIA: "Venta transferencia",
    MovimientoCaja.Tipo.VENTA_PEDIDOSYA: "Venta PedidosYa",
    MovimientoCaja.Tipo.VENTA_QR: "Venta QR / MercadoPago",
    MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA: "Traspaso salida",
    MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA: "Traspaso entrada",
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_SALIDA: "Transferencia sucursal salida",
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_ENTRADA: "Transferencia sucursal entrada",
    MovimientoCaja.Tipo.AJUSTE_CIERRE: "Ajuste de cierre",
}
BOX_BREAKDOWN_EXCLUDED_TYPES = {
    MovimientoCaja.Tipo.APERTURA,
    MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_ENTRADA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_SALIDA,
    MovimientoCaja.Tipo.AJUSTE_CIERRE,
}
CLOSED_BOX_CORRECTION_BLOCKED_TYPES = {
    MovimientoCaja.Tipo.APERTURA,
    MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_ENTRADA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA,
    MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_SALIDA,
    MovimientoCaja.Tipo.AJUSTE_CIERRE,
}


def get_cash_movement_type_label(tipo: str, channel_map: dict[str, str] | None = None) -> str:
    if channel_map and tipo in channel_map:
        return channel_map[tipo]
    return MOVEMENT_TYPE_LABELS.get(tipo, tipo.replace("_", " ").title())


def build_box_sales_breakdown(movements) -> dict:
    channel_map = get_income_channel_map()
    grouped_totals = defaultdict(lambda: Decimal("0.00"))
    breakdown_movements = []
    total = Decimal("0.00")

    for movement in movements:
        if getattr(movement, "estado", MovimientoCaja.Estado.REGISTRADO) != MovimientoCaja.Estado.REGISTRADO:
            continue
        if movement.sentido != MovimientoCaja.Sentido.INGRESO:
            continue
        if movement.tipo in BOX_BREAKDOWN_EXCLUDED_TYPES:
            continue
        label = get_cash_movement_type_label(movement.tipo, channel_map)
        movement.tipo_label = label
        grouped_totals[(movement.tipo, label, movement.impacta_saldo_caja)] += movement.monto
        total += movement.monto
        breakdown_movements.append(movement)

    groups = [
        {
            "tipo": tipo,
            "label": label,
            "impacta_saldo_caja": impacta_saldo_caja,
            "total": amount,
        }
        for (tipo, label, impacta_saldo_caja), amount in grouped_totals.items()
    ]
    groups.sort(key=lambda item: (item["impacta_saldo_caja"] is False, item["label"]))

    return {
        "total": total,
        "groups": groups,
        "movements": sorted(breakdown_movements, key=lambda movement: (movement.creado_en, movement.pk), reverse=True),
    }


def describe_box_follow_up(caja: Caja, movements) -> dict:
    active_movements = [
        movement
        for movement in movements
        if getattr(movement, "estado", MovimientoCaja.Estado.REGISTRADO) == MovimientoCaja.Estado.REGISTRADO
    ]
    post_opening_movements = [movement for movement in active_movements if movement.tipo != MovimientoCaja.Tipo.APERTURA]
    last_movement = movements[0] if movements else None
    last_activity_at = caja.cerrada_en or (last_movement.creado_en if last_movement else caja.abierta_en)

    if caja.estado == Caja.Estado.CERRADA:
        return {
            "label": "Cerrada",
            "badge_class": "badge-muted",
            "detail": "Caja cerrada y disponible solo para consulta.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
    if not post_opening_movements:
        return {
            "label": "Abierta sin movimientos",
            "badge_class": "badge-warning",
            "detail": "Se abrio la caja pero no registra cargas posteriores a la apertura.",
            "last_activity_at": last_activity_at,
            "post_opening_count": 0,
        }
    return {
        "label": "Carga en curso",
        "badge_class": "badge-success",
        "detail": "La caja sigue abierta y ya tiene movimientos cargados.",
        "last_activity_at": last_activity_at,
        "post_opening_count": len(post_opening_movements),
    }


def build_box_activity_timeline(caja: Caja, movements) -> list[dict]:
    channel_map = get_income_channel_map()
    events = [
        {
            "timestamp": caja.abierta_en,
            "kind": "APERTURA",
            "badge_class": "badge",
            "badge_label": "Apertura",
            "title": "Caja abierta",
            "detail": f"Apertura de caja para {caja.sucursal.nombre} en {caja.turno.get_tipo_display()}.",
            "user_label": str(caja.usuario),
            "amount": caja.monto_inicial,
        }
    ]

    for movement in movements:
        if movement.tipo == MovimientoCaja.Tipo.APERTURA:
            continue
        detail_parts = []
        if movement.rubro_operativo_id:
            detail_parts.append(f"Rubro {movement.rubro_operativo.nombre}")
        elif movement.categoria:
            detail_parts.append(movement.categoria)
        if movement.observacion:
            detail_parts.append(movement.observacion)
        if movement.transferencia_id:
            detail_parts.append(f"Transferencia #{movement.transferencia_id}")
        is_annulled = movement.estado == MovimientoCaja.Estado.ANULADO
        if is_annulled:
            detail_parts.append(f"Anulado: {movement.motivo_anulacion}")
        events.append(
            {
                "timestamp": movement.creado_en,
                "kind": "MOVIMIENTO",
                "badge_class": "badge-muted" if is_annulled else ("badge-danger" if movement.sentido == MovimientoCaja.Sentido.EGRESO else "badge-success"),
                "badge_label": "Anulado" if is_annulled else movement.get_sentido_display(),
                "title": get_cash_movement_type_label(movement.tipo, channel_map),
                "detail": " - ".join(detail_parts) if detail_parts else "Movimiento operativo registrado.",
                "user_label": str(movement.creado_por) if movement.creado_por else "Sin usuario",
                "amount": movement.monto,
            }
        )
        for correction in getattr(movement, "prefetched_corrections", []):
            events.append(
                {
                    "timestamp": correction.creado_en,
                    "kind": "CORRECCION",
                    "badge_class": "badge-warning",
                    "badge_label": correction.get_accion_display(),
                    "title": f"Corrección movimiento #{movement.id}",
                    "detail": correction.motivo,
                    "user_label": str(correction.creado_por) if correction.creado_por else "Sin usuario",
                    "amount": correction.monto_nuevo if correction.monto_nuevo is not None else correction.monto_anterior,
                }
            )

    cierre = getattr(caja, "cierre", None)
    if cierre is not None:
        events.append(
            {
                "timestamp": cierre.cerrado_en,
                "kind": "CIERRE",
                "badge_class": "badge-muted",
                "badge_label": "Cierre",
                "title": "Caja cerrada",
                "detail": (
                    f"Saldo esperado ${cierre.saldo_esperado} - "
                    f"saldo fisico ${cierre.saldo_fisico} - "
                    f"diferencia ${cierre.diferencia}."
                ),
                "user_label": str(cierre.cerrado_por) if cierre.cerrado_por else "Sin usuario",
                "amount": cierre.saldo_fisico,
            }
        )
        justificacion = getattr(cierre, "justificacion", None)
        if justificacion is not None:
            events.append(
                {
                    "timestamp": justificacion.creado_en,
                    "kind": "JUSTIFICACION",
                    "badge_class": "badge-warning",
                    "badge_label": "Justificacion",
                    "title": "Justificacion de cierre",
                    "detail": justificacion.motivo,
                    "user_label": str(justificacion.creado_por) if justificacion.creado_por else "Sin usuario",
                    "amount": None,
                }
            )

    events.sort(key=lambda event: event["timestamp"], reverse=True)
    return events


def get_income_channel_map() -> dict[str, str]:
    return {c.codigo: c.nombre for c in CanalIngreso.objects.filter(activo=True).order_by("orden")}


def _get_active_channels() -> list:
    return list(CanalIngreso.objects.filter(activo=True).order_by("orden"))


def _excluded_income_channel_codes(channels: list[CanalIngreso]) -> list[str]:
    return [channel.codigo for channel in channels if channel.excluir_de_totales]


def _included_income_filter(excluded_channel_codes: list[str]) -> Q:
    income_filter = Q(sentido=MovimientoCaja.Sentido.INGRESO)
    if excluded_channel_codes:
        income_filter &= ~Q(tipo__in=excluded_channel_codes)
    return income_filter


def _excluded_income_by_channel(movement_qs, channels: list[CanalIngreso]) -> list[dict]:
    excluded_channels = {channel.codigo: channel for channel in channels if channel.excluir_de_totales}
    if not excluded_channels:
        return []
    rows = (
        movement_qs.filter(tipo__in=excluded_channels.keys(), sentido=MovimientoCaja.Sentido.INGRESO)
        .values("tipo")
        .annotate(total=Sum("monto"))
    )
    return sorted(
        [
            {
                "label": excluded_channels[row["tipo"]].nombre,
                "tipo": row["tipo"],
                "total": row["total"] or Decimal("0.00"),
                "display_label": f"Ventas facturacion de {excluded_channels[row['tipo']].nombre.upper()}",
            }
            for row in rows
        ],
        key=lambda item: item["label"],
    )


@dataclass(frozen=True)
class OperationalControlScope:
    kind: str
    fecha_operativa: date
    sucursal: Sucursal | None = None
    caja: Caja | None = None

    @property
    def label(self) -> str:
        if self.kind == "CAJA" and self.caja is not None:
            return f"Caja #{self.caja.pk}"
        if self.kind == "SUCURSAL" and self.sucursal is not None:
            return self.sucursal.nombre
        return "Global"

    @property
    def kind_label(self) -> str:
        if self.kind == "CAJA":
            return "Caja"
        if self.kind == "SUCURSAL":
            return "Sucursal"
        return "Global"

    @property
    def dedupe_scope(self) -> str:
        if self.kind == "CAJA" and self.caja is not None:
            return f"caja-{self.caja.pk}"
        if self.kind == "SUCURSAL" and self.sucursal is not None:
            return f"sucursal-{self.sucursal.pk}"
        return "global"


def _require_actor(actor, message: str = "Se requiere un usuario autenticado para operar.") -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied(message)


def _lock_caja(caja: Caja) -> Caja:
    return Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)


def _create_movement(
    *,
    caja: Caja,
    tipo: str,
    sentido: str,
    monto: Decimal,
    impacta_saldo_caja: bool = True,
    categoria: str = "",
    observacion: str = "",
    rubro_operativo: RubroOperativo | None = None,
    transferencia: Transferencia | None = None,
    creado_por=None,
) -> MovimientoCaja:
    return MovimientoCaja.objects.create(
        caja=caja,
        tipo=tipo,
        sentido=sentido,
        monto=monto,
        impacta_saldo_caja=impacta_saldo_caja,
        categoria=categoria,
        observacion=observacion,
        rubro_operativo=rubro_operativo,
        transferencia=transferencia,
        creado_por=creado_por,
    )


def _validate_available_funds(caja: Caja, monto: Decimal) -> None:
    if caja.saldo_esperado < monto:
        raise ValidationError({"monto": "El monto supera el saldo disponible de la caja origen."})


def calculate_expected_balance(caja: Caja) -> Decimal:
    caja.refresh_from_db()
    return caja.saldo_esperado


def _quantize_percentage(value: Decimal) -> Decimal:
    return value.quantize(PERCENTAGE_QUANTIZER, rounding=ROUND_HALF_UP)


def _warning_threshold(limit_value: Decimal) -> Decimal:
    return _quantize_percentage(limit_value * OPERATIONAL_WARNING_RATIO)


def get_uncategorized_operational_category() -> RubroOperativo:
    category = RubroOperativo.objects.filter(es_sistema=True).first()
    if category:
        return category

    category = RubroOperativo.objects.filter(nombre__iexact=UNCATEGORIZED_OPERATIONAL_CATEGORY_NAME).first()
    if category:
        updated_fields = []
        if category.activo:
            category.activo = False
            updated_fields.append("activo")
        if not category.es_sistema:
            category.es_sistema = True
            updated_fields.append("es_sistema")
        if updated_fields:
            category.save(update_fields=updated_fields + ["actualizado_en"])
        return category

    return RubroOperativo.objects.create(
        nombre=UNCATEGORIZED_OPERATIONAL_CATEGORY_NAME,
        activo=False,
        es_sistema=True,
    )


def build_global_control_scope(*, fecha_operativa: date) -> OperationalControlScope:
    return OperationalControlScope(kind="GLOBAL", fecha_operativa=fecha_operativa)


def build_branch_control_scope(*, fecha_operativa: date, sucursal: Sucursal) -> OperationalControlScope:
    return OperationalControlScope(kind="SUCURSAL", fecha_operativa=fecha_operativa, sucursal=sucursal)


def build_box_control_scope(*, caja: Caja) -> OperationalControlScope:
    if not hasattr(caja, "turno") or not hasattr(caja, "sucursal"):
        caja = Caja.objects.select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    return OperationalControlScope(
        kind="CAJA",
        fecha_operativa=caja.fecha_operativa,
        sucursal=caja.sucursal,
        caja=caja,
    )


def _period_boxes_for_operational_scope(
    *,
    date_from: date,
    date_to: date,
    sucursal: Sucursal | None = None,
    empresa_ids: list[int] | None = None,
):
    boxes = Caja.objects.select_related("sucursal", "turno", "usuario", "cierre").filter(
        fecha_operativa__gte=date_from,
        fecha_operativa__lte=date_to,
    )
    if sucursal is not None:
        boxes = boxes.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        boxes = boxes.filter(sucursal__empresa_id__in=empresa_ids)
    return boxes


def _period_boxes_cash_balance(
    *,
    date_from: date,
    date_to: date,
    sucursal: Sucursal | None = None,
    empresa_ids: list[int] | None = None,
) -> tuple[Decimal, int]:
    boxes = list(
        _period_boxes_for_operational_scope(
            date_from=date_from,
            date_to=date_to,
            sucursal=sucursal,
            empresa_ids=empresa_ids,
        )
    )
    total = sum(
        (
            box.cierre.saldo_fisico
            if hasattr(box, "cierre")
            else box.saldo_esperado
            for box in boxes
        ),
        Decimal("0.00"),
    )
    return total, len(boxes)


def _movement_scope_filter(scope: OperationalControlScope) -> Q:
    query = Q(caja__fecha_operativa=scope.fecha_operativa)
    if scope.kind == "CAJA" and scope.caja is not None:
        return query & Q(caja=scope.caja)
    if scope.kind == "SUCURSAL" and scope.sucursal is not None:
        return query & Q(caja__sucursal=scope.sucursal)
    return query


def _limit_scope_filter(scope: OperationalControlScope) -> Q:
    if scope.sucursal is None:
        return Q(sucursal__isnull=True)
    return Q(sucursal=scope.sucursal) | Q(sucursal__isnull=True)


def _rubro_alert_scope_filter(scope: OperationalControlScope) -> Q:
    if scope.kind == "CAJA" and scope.caja is not None:
        return Q(caja=scope.caja)
    if scope.kind == "SUCURSAL" and scope.sucursal is not None:
        return Q(caja__isnull=True, sucursal=scope.sucursal)
    return Q(caja__isnull=True, sucursal__isnull=True)


def _alerts_filter_for_scope(scope: OperationalControlScope) -> Q:
    rubro_alerts = Q(
        tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
        periodo_fecha=scope.fecha_operativa,
    ) & _rubro_alert_scope_filter(scope)
    if scope.kind == "CAJA" and scope.caja is not None:
        closure_alerts = Q(tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE, caja=scope.caja)
    elif scope.kind == "SUCURSAL" and scope.sucursal is not None:
        closure_alerts = Q(
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            sucursal=scope.sucursal,
            periodo_fecha=scope.fecha_operativa,
        )
    else:
        closure_alerts = Q(
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            periodo_fecha=scope.fecha_operativa,
        )
    return rubro_alerts | closure_alerts


def _effective_limits_by_category(
    *,
    rubro_ids: list[int],
    scope: OperationalControlScope,
) -> dict[int, LimiteRubroOperativo]:
    if not rubro_ids:
        return {}

    limit_map: dict[int, LimiteRubroOperativo] = {}
    limits = (
        LimiteRubroOperativo.objects.select_related("rubro", "sucursal")
        .filter(rubro_id__in=rubro_ids)
        .filter(_limit_scope_filter(scope))
        .order_by("rubro_id", "sucursal_id")
    )
    for limit in limits:
        current = limit_map.get(limit.rubro_id)
        if current is None:
            limit_map[limit.rubro_id] = limit
            continue
        if scope.sucursal is not None and limit.sucursal_id == scope.sucursal.id:
            limit_map[limit.rubro_id] = limit
    return limit_map


def _build_expense_alert_key(*, scope: OperationalControlScope, rubro: RubroOperativo) -> str:
    return f"RUBRO_EXCEDIDO:{scope.dedupe_scope}:{scope.fecha_operativa.isoformat()}:{rubro.pk}"


def _build_closing_alert_key(*, cierre: CierreCaja) -> str:
    return f"DIFERENCIA_GRAVE:cierre:{cierre.pk}"


def _upsert_alert(*, dedupe_key: str | None = None, **defaults) -> AlertaOperativa:
    if dedupe_key:
        alert, created = AlertaOperativa.objects.get_or_create(dedupe_key=dedupe_key, defaults=defaults)
        if created:
            return alert
        update_fields = []
        for field_name, value in defaults.items():
            if getattr(alert, field_name) != value:
                setattr(alert, field_name, value)
                update_fields.append(field_name)
        if alert.resuelta:
            alert.resuelta = False
            update_fields.append("resuelta")
        if update_fields:
            alert.save(update_fields=update_fields)
        return alert
    return AlertaOperativa.objects.create(**defaults)


def get_alerts_for_scope(
    scope: OperationalControlScope,
    *,
    resuelta: bool | None = False,
    limit: int | None = None,
):
    queryset = AlertaOperativa.objects.select_related(
        "caja",
        "sucursal",
        "rubro_operativo",
        "turno",
        "usuario",
        "cierre",
    ).filter(_alerts_filter_for_scope(scope))
    if resuelta is not None:
        queryset = queryset.filter(resuelta=resuelta)
    queryset = queryset.order_by("-creada_en", "-id")
    if limit is not None:
        return queryset[:limit]
    return queryset


def build_alert_panel_queryset(
    *,
    estado: str = "activas",
    periodo_desde=None,
    periodo_hasta=None,
    rubro: RubroOperativo | None = None,
    sucursal: Sucursal | None = None,
    alcance: str = "todos",
    empresa_ids: list[int] | None = None,
):
    """Lee alertas persistidas para auditoria usando periodo operativo real."""
    severity_order = Case(
        When(tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE, then=Value(0)),
        When(tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO, then=Value(1)),
        default=Value(9),
        output_field=IntegerField(),
    )
    scope_order = Case(
        When(caja__isnull=False, then=Value(0)),
        When(sucursal__isnull=False, then=Value(1)),
        default=Value(2),
        output_field=IntegerField(),
    )
    queryset = AlertaOperativa.objects.select_related(
        "caja",
        "sucursal",
        "rubro_operativo",
        "turno",
        "usuario",
        "cierre",
    ).annotate(severity_order=severity_order, scope_order=scope_order)
    if estado == "activas":
        queryset = queryset.filter(resuelta=False)
    elif estado == "resueltas":
        queryset = queryset.filter(resuelta=True)
    if periodo_desde:
        queryset = queryset.filter(periodo_fecha__gte=periodo_desde)
    if periodo_hasta:
        queryset = queryset.filter(periodo_fecha__lte=periodo_hasta)
    if rubro is not None:
        queryset = queryset.filter(rubro_operativo=rubro)
    if sucursal is not None:
        queryset = queryset.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        if not empresa_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(
                Q(caja__sucursal__empresa_id__in=empresa_ids)
                | Q(sucursal__empresa_id__in=empresa_ids)
                | Q(turno__empresa_id__in=empresa_ids)
                | Q(caja__isnull=True, sucursal__isnull=True, turno__isnull=True)
            )
    if alcance == "global":
        queryset = queryset.filter(caja__isnull=True, sucursal__isnull=True)
    elif alcance == "sucursal":
        queryset = queryset.filter(caja__isnull=True, sucursal__isnull=False)
    elif alcance == "caja":
        queryset = queryset.filter(caja__isnull=False)
    return queryset.order_by("resuelta", "severity_order", "-periodo_fecha", "scope_order", "-creada_en", "-id")


def build_operational_control_snapshot(
    scope: OperationalControlScope,
    *,
    sync_alerts: bool = False,
) -> dict:
    movement_qs = MovimientoCaja.objects.filter(_movement_scope_filter(scope)).exclude(
        tipo=MovimientoCaja.Tipo.APERTURA
    ).filter(estado=MovimientoCaja.Estado.REGISTRADO)
    _channels = _get_active_channels()
    _excluded_income_codes = _excluded_income_channel_codes(_channels)
    totals = movement_qs.aggregate(
        total_ingresos=Sum("monto", filter=_included_income_filter(_excluded_income_codes)),
        total_egresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.EGRESO)),
    )
    expense_qs = movement_qs.filter(tipo=MovimientoCaja.Tipo.GASTO)
    totals_by_category = {
        row["rubro_operativo"]: row["total_gastado"] or Decimal("0.00")
        for row in expense_qs.values("rubro_operativo").annotate(total_gastado=Sum("monto"))
    }
    base_calculo_total = sum(totals_by_category.values(), Decimal("0.00"))
    rubro_ids = set(totals_by_category.keys())
    rubro_ids.update(
        RubroOperativo.objects.filter(activo=True, es_sistema=False).values_list("id", flat=True)
    )
    rubro_ids.update(
        LimiteRubroOperativo.objects.filter(_limit_scope_filter(scope)).values_list("rubro_id", flat=True)
    )
    rubros = list(RubroOperativo.objects.filter(pk__in=rubro_ids).order_by("nombre"))
    effective_limits = _effective_limits_by_category(rubro_ids=list(rubro_ids), scope=scope)

    items = []
    for rubro in rubros:
        total_gastado = totals_by_category.get(rubro.id, Decimal("0.00"))
        porcentaje_consumido = (
            _quantize_percentage((total_gastado * Decimal("100.00")) / base_calculo_total)
            if base_calculo_total > 0
            else Decimal("0.00")
        )
        limit_config = effective_limits.get(rubro.id)
        if limit_config is None:
            estado_item = "SIN_LIMITE"
            estado_label = "Sin limite"
            badge_class = "badge-muted"
            warning_threshold = None
        elif porcentaje_consumido > limit_config.porcentaje_maximo:
            estado_item = "ROJO"
            estado_label = "Excedido"
            badge_class = "badge-danger"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)
        elif porcentaje_consumido >= _warning_threshold(limit_config.porcentaje_maximo):
            estado_item = "AMARILLO"
            estado_label = "Cerca del limite"
            badge_class = "badge-warning"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)
        else:
            estado_item = "VERDE"
            estado_label = "Controlado"
            badge_class = "badge-success"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)

        items.append(
            {
                "rubro": rubro,
                "total_gastado": total_gastado,
                "porcentaje_consumido": porcentaje_consumido,
                "porcentaje_maximo": limit_config.porcentaje_maximo if limit_config else None,
                "warning_threshold": warning_threshold,
                "estado": estado_item,
                "estado_label": estado_label,
                "badge_class": badge_class,
                "limit_scope_label": (
                    limit_config.sucursal.nombre if limit_config and limit_config.sucursal_id else "Global"
                ),
                "has_limit": limit_config is not None,
                "alert_should_exist": estado_item == "ROJO" and limit_config is not None,
            }
        )

    status_order = {"ROJO": 0, "AMARILLO": 1, "VERDE": 2, "SIN_LIMITE": 3}
    items.sort(key=lambda item: (status_order[item["estado"]], item["rubro"].nombre.lower()))

    _channel_by_codigo = {c.codigo: c for c in _channels}
    _non_cash_tipos = [c.codigo for c in _channels if c.codigo != MovimientoCaja.Tipo.INGRESO_EFECTIVO]
    ventas_rows = (
        movement_qs.filter(tipo__in=_non_cash_tipos, sentido=MovimientoCaja.Sentido.INGRESO)
        .values("tipo")
        .annotate(total=Sum("monto"))
    )
    ventas_por_canal = sorted(
        [
            {
                "label": _channel_by_codigo[row["tipo"]].nombre if row["tipo"] in _channel_by_codigo else row["tipo"],
                "tipo": row["tipo"],
                "total": row["total"] or Decimal("0.00"),
                "excluir_de_totales": _channel_by_codigo[row["tipo"]].excluir_de_totales if row["tipo"] in _channel_by_codigo else False,
            }
            for row in ventas_rows
        ],
        key=lambda v: v["label"],
    )
    total_ventas_digitales = sum(
        (v["total"] for v in ventas_por_canal if not v["excluir_de_totales"]),
        Decimal("0.00"),
    )
    ventas_excluidas_por_canal = _excluded_income_by_channel(movement_qs, _channels)
    total_ingresos_excluidos = sum((v["total"] for v in ventas_excluidas_por_canal), Decimal("0.00"))
    ingreso_efectivo_total = (
        movement_qs.filter(tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    saldo_efectivo_caja = scope.caja.saldo_esperado if scope.kind == "CAJA" and scope.caja else None

    snapshot = {
        "scope": scope,
        "scope_kind": scope.kind,
        "scope_kind_label": scope.kind_label,
        "scope_label": scope.label,
        "fecha_operativa": scope.fecha_operativa,
        "base_calculo_codigo": OPERATIONAL_CONTROL_BASE_CODE,
        "base_calculo_label": OPERATIONAL_CONTROL_BASE_LABEL,
        "base_calculo_total": base_calculo_total,
        "total_operativo": base_calculo_total,
        "total_ingresos": totals["total_ingresos"] or Decimal("0.00"),
        "total_egresos": totals["total_egresos"] or Decimal("0.00"),
        "saldo_neto": (totals["total_ingresos"] or Decimal("0.00")) - (totals["total_egresos"] or Decimal("0.00")),
        "ventas_por_canal": ventas_por_canal,
        "total_ventas_digitales": total_ventas_digitales,
        "ventas_excluidas_por_canal": ventas_excluidas_por_canal,
        "total_ingresos_excluidos": total_ingresos_excluidos,
        "ingreso_efectivo_total": ingreso_efectivo_total,
        "saldo_efectivo_caja": saldo_efectivo_caja,
        "items": items,
    }
    if sync_alerts:
        sync_operational_alerts_for_scope(scope, snapshot_items=items)
    active_alerts = list(get_alerts_for_scope(scope, resuelta=False))
    snapshot["active_alerts"] = active_alerts
    snapshot["active_alert_count"] = len(active_alerts)
    return snapshot


def build_operational_period_summary(*, date_from: date, date_to: date, sucursal: Sucursal | None = None, empresa_ids: list[int] | None = None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    movement_qs = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
    ).exclude(tipo=MovimientoCaja.Tipo.APERTURA).filter(estado=MovimientoCaja.Estado.REGISTRADO)
    if sucursal is not None:
        movement_qs = movement_qs.filter(caja__sucursal=sucursal)
    elif empresa_ids is not None:
        movement_qs = movement_qs.filter(caja__sucursal__empresa_id__in=empresa_ids)

    _channels = _get_active_channels()
    _excluded_income_codes = _excluded_income_channel_codes(_channels)
    totals = movement_qs.aggregate(
        total_ingresos=Sum("monto", filter=_included_income_filter(_excluded_income_codes)),
        total_egresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.EGRESO)),
    )
    expense_qs = movement_qs.filter(tipo=MovimientoCaja.Tipo.GASTO)
    totals_by_category = {
        row["rubro_operativo"]: row["total_gastado"] or Decimal("0.00")
        for row in expense_qs.values("rubro_operativo").annotate(total_gastado=Sum("monto"))
    }
    base_calculo_total = sum(totals_by_category.values(), Decimal("0.00"))
    rubro_ids = set(totals_by_category.keys())
    rubro_ids.update(
        RubroOperativo.objects.filter(activo=True, es_sistema=False).values_list("id", flat=True)
    )
    rubro_ids.update(
        LimiteRubroOperativo.objects.filter(_limit_scope_filter(
            build_branch_control_scope(fecha_operativa=date_from, sucursal=sucursal)
            if sucursal is not None
            else build_global_control_scope(fecha_operativa=date_from)
        )).values_list("rubro_id", flat=True)
    )
    rubros = list(RubroOperativo.objects.filter(pk__in=rubro_ids).order_by("nombre"))
    scope = (
        build_branch_control_scope(fecha_operativa=date_from, sucursal=sucursal)
        if sucursal is not None
        else build_global_control_scope(fecha_operativa=date_from)
    )
    effective_limits = _effective_limits_by_category(rubro_ids=list(rubro_ids), scope=scope)

    items = []
    for rubro in rubros:
        total_gastado = totals_by_category.get(rubro.id, Decimal("0.00"))
        porcentaje_consumido = (
            _quantize_percentage((total_gastado * Decimal("100.00")) / base_calculo_total)
            if base_calculo_total > 0
            else Decimal("0.00")
        )
        limit_config = effective_limits.get(rubro.id)
        if limit_config is None:
            estado_item = "SIN_LIMITE"
            estado_label = "Sin limite"
            badge_class = "badge-muted"
            warning_threshold = None
        elif porcentaje_consumido > limit_config.porcentaje_maximo:
            estado_item = "ROJO"
            estado_label = "Excedido"
            badge_class = "badge-danger"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)
        elif porcentaje_consumido >= _warning_threshold(limit_config.porcentaje_maximo):
            estado_item = "AMARILLO"
            estado_label = "Cerca del limite"
            badge_class = "badge-warning"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)
        else:
            estado_item = "VERDE"
            estado_label = "Controlado"
            badge_class = "badge-success"
            warning_threshold = _warning_threshold(limit_config.porcentaje_maximo)

        items.append(
            {
                "rubro": rubro,
                "total_gastado": total_gastado,
                "porcentaje_consumido": porcentaje_consumido,
                "porcentaje_maximo": limit_config.porcentaje_maximo if limit_config else None,
                "warning_threshold": warning_threshold,
                "estado": estado_item,
                "estado_label": estado_label,
                "badge_class": badge_class,
                "limit_scope_label": (
                    limit_config.sucursal.nombre if limit_config and limit_config.sucursal_id else "Global"
                ),
                "has_limit": limit_config is not None,
                "alert_should_exist": False,
            }
        )

    status_order = {"ROJO": 0, "AMARILLO": 1, "VERDE": 2, "SIN_LIMITE": 3}
    items.sort(key=lambda item: (status_order[item["estado"]], item["rubro"].nombre.lower()))

    _channel_by_codigo = {c.codigo: c for c in _channels}
    _non_cash_tipos = [c.codigo for c in _channels if c.codigo != MovimientoCaja.Tipo.INGRESO_EFECTIVO]
    ventas_rows = (
        movement_qs.filter(tipo__in=_non_cash_tipos, sentido=MovimientoCaja.Sentido.INGRESO)
        .values("tipo")
        .annotate(total=Sum("monto"))
    )
    ventas_por_canal = sorted(
        [
            {
                "label": _channel_by_codigo[row["tipo"]].nombre if row["tipo"] in _channel_by_codigo else row["tipo"],
                "tipo": row["tipo"],
                "total": row["total"] or Decimal("0.00"),
                "excluir_de_totales": _channel_by_codigo[row["tipo"]].excluir_de_totales if row["tipo"] in _channel_by_codigo else False,
            }
            for row in ventas_rows
        ],
        key=lambda v: v["label"],
    )
    total_ventas_digitales = sum(
        (v["total"] for v in ventas_por_canal if not v["excluir_de_totales"]),
        Decimal("0.00"),
    )
    ventas_excluidas_por_canal = _excluded_income_by_channel(movement_qs, _channels)
    total_ingresos_excluidos = sum((v["total"] for v in ventas_excluidas_por_canal), Decimal("0.00"))
    ingreso_efectivo_total = (
        movement_qs.filter(tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    saldo_real_cajas_periodo, cajas_periodo_count = _period_boxes_cash_balance(
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )

    return {
        "scope_kind": "SUCURSAL" if sucursal is not None else "GLOBAL",
        "scope_kind_label": "Sucursal" if sucursal is not None else "Global",
        "scope_label": sucursal.nombre if sucursal is not None else "Global",
        "period_from": date_from,
        "period_to": date_to,
        "is_period_summary": True,
        "base_calculo_codigo": OPERATIONAL_CONTROL_BASE_CODE,
        "base_calculo_label": OPERATIONAL_CONTROL_BASE_LABEL,
        "base_calculo_total": base_calculo_total,
        "total_operativo": base_calculo_total,
        "total_ingresos": totals["total_ingresos"] or Decimal("0.00"),
        "total_egresos": totals["total_egresos"] or Decimal("0.00"),
        "saldo_neto": (totals["total_ingresos"] or Decimal("0.00")) - (totals["total_egresos"] or Decimal("0.00")),
        "ventas_por_canal": ventas_por_canal,
        "total_ventas_digitales": total_ventas_digitales,
        "ventas_excluidas_por_canal": ventas_excluidas_por_canal,
        "total_ingresos_excluidos": total_ingresos_excluidos,
        "ingreso_efectivo_total": ingreso_efectivo_total,
        "saldo_efectivo_caja": None,
        "saldo_real_cajas_periodo": saldo_real_cajas_periodo,
        "cajas_periodo_count": cajas_periodo_count,
        "items": items,
        "active_alerts": [],
        "active_alert_count": 0,
    }


def build_management_daily_matrix(*, date_from: date, date_to: date, sucursal: Sucursal | None = None, empresa_ids: list[int] | None = None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    movement_qs = MovimientoCaja.objects.select_related(
        "caja",
        "caja__sucursal",
        "caja__turno",
        "rubro_operativo",
        "creado_por",
    ).filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
    ).exclude(tipo=MovimientoCaja.Tipo.APERTURA).filter(estado=MovimientoCaja.Estado.REGISTRADO)
    if sucursal is not None:
        movement_qs = movement_qs.filter(caja__sucursal=sucursal)
    elif empresa_ids is not None:
        movement_qs = movement_qs.filter(caja__sucursal__empresa_id__in=empresa_ids)

    _channels = _get_active_channels()
    _income_codigos = [c.codigo for c in _channels]
    _included_income_codigos = [c.codigo for c in _channels if not c.excluir_de_totales]
    _excluded_income_codigos = _excluded_income_channel_codes(_channels)
    income_rows = (
        movement_qs.filter(tipo__in=_income_codigos, sentido=MovimientoCaja.Sentido.INGRESO)
        .values("caja__fecha_operativa", "tipo")
        .annotate(total=Sum("monto"))
    )
    expense_rows = (
        movement_qs.filter(tipo=MovimientoCaja.Tipo.GASTO)
        .values("caja__fecha_operativa", "rubro_operativo", "rubro_operativo__nombre")
        .annotate(total=Sum("monto"))
    )

    channel_keys = _income_codigos
    channel_labels = [{"key": c.codigo, "label": c.nombre, "excluir_de_totales": c.excluir_de_totales} for c in _channels]
    excluded_channel_labels = [
        {
            "key": c.codigo,
            "label": c.nombre,
            "display_label": f"Ventas facturacion de {c.nombre.upper()}",
        }
        for c in _channels
        if c.excluir_de_totales
    ]
    rubro_ids = set()
    rubro_names = {}
    for row in expense_rows:
        rubro_id = row["rubro_operativo"]
        if rubro_id is None:
            continue
        rubro_ids.add(rubro_id)
        rubro_names[rubro_id] = row["rubro_operativo__nombre"] or "Sin rubro"
    rubros = [{"id": rubro_id, "nombre": rubro_names[rubro_id]} for rubro_id in sorted(rubro_ids, key=lambda pk: rubro_names[pk].lower())]

    incomes_by_day = defaultdict(lambda: defaultdict(lambda: Decimal("0.00")))
    expenses_by_day = defaultdict(lambda: defaultdict(lambda: Decimal("0.00")))
    for row in income_rows:
        incomes_by_day[row["caja__fecha_operativa"]][row["tipo"]] += row["total"] or Decimal("0.00")
    for row in expense_rows:
        rubro_id = row["rubro_operativo"]
        if rubro_id is not None:
            expenses_by_day[row["caja__fecha_operativa"]][rubro_id] += row["total"] or Decimal("0.00")

    days = []
    current = date_from
    total_income = Decimal("0.00")
    total_excluded_income = Decimal("0.00")
    total_expense = Decimal("0.00")
    while current <= date_to:
        income_by_channel = {key: incomes_by_day[current][key] for key in channel_keys}
        expense_by_rubro = {item["id"]: expenses_by_day[current][item["id"]] for item in rubros}
        day_income = sum((income_by_channel[key] for key in _included_income_codigos), Decimal("0.00"))
        day_excluded_income = sum((income_by_channel[key] for key in _excluded_income_codigos), Decimal("0.00"))
        day_expense = sum(expense_by_rubro.values(), Decimal("0.00"))
        total_income += day_income
        total_excluded_income += day_excluded_income
        total_expense += day_expense
        days.append(
            {
                "date": current,
                "income_by_channel": income_by_channel,
                "expense_by_rubro": expense_by_rubro,
                "income_values": [income_by_channel[key] for key in channel_keys],
                "excluded_income_values": [income_by_channel[item["key"]] for item in excluded_channel_labels],
                "expense_values": [expense_by_rubro[item["id"]] for item in rubros],
                "total_income": day_income,
                "total_excluded_income": day_excluded_income,
                "total_expense": day_expense,
                "net_result": day_income - day_expense,
            }
        )
        current += timedelta(days=1)

    detail_movements = movement_qs.order_by("caja__fecha_operativa", "caja_id", "id")
    excluded_channel_totals = [
        {
            **channel,
            "total": sum((day["income_by_channel"][channel["key"]] for day in days), Decimal("0.00")),
        }
        for channel in excluded_channel_labels
    ]

    return {
        "date_from": date_from,
        "date_to": date_to,
        "sucursal": sucursal,
        "channels": channel_labels,
        "excluded_channels": excluded_channel_totals,
        "rubros": rubros,
        "days": days,
        "detail_movements": detail_movements,
        "total_income": total_income,
        "total_excluded_income": total_excluded_income,
        "total_expense": total_expense,
        "net_result": total_income - total_expense,
    }


def build_operational_category_overview(*, fecha_operativa, sucursal: Sucursal | None = None) -> dict:
    scope = (
        build_branch_control_scope(fecha_operativa=fecha_operativa, sucursal=sucursal)
        if sucursal is not None
        else build_global_control_scope(fecha_operativa=fecha_operativa)
    )
    snapshot = build_operational_control_snapshot(scope)
    return {
        "fecha_operativa": snapshot["fecha_operativa"],
        "scope_label": snapshot["scope_label"],
        "scope_branch": scope.sucursal,
        "total_operativo": snapshot["total_operativo"],
        "items": snapshot["items"],
    }


def sync_operational_alerts_for_scope(
    scope: OperationalControlScope,
    *,
    snapshot_items: list[dict] | None = None,
) -> list[AlertaOperativa]:
    if snapshot_items is None:
        snapshot_items = build_operational_control_snapshot(scope)["items"]

    active_keys: set[str] = set()
    active_alerts: list[AlertaOperativa] = []
    for item in snapshot_items:
        if not item["alert_should_exist"]:
            continue
        dedupe_key = _build_expense_alert_key(scope=scope, rubro=item["rubro"])
        active_keys.add(dedupe_key)
        alert_defaults = {
            "tipo": AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
            "cierre": None,
            "periodo_fecha": scope.fecha_operativa,
            "rubro_operativo": item["rubro"],
            "mensaje": (
                f"El rubro {item['rubro'].nombre} representa {item['porcentaje_consumido']}% sobre "
                f"{OPERATIONAL_CONTROL_BASE_LABEL.lower()} y supera su limite de {item['porcentaje_maximo']}% "
                f"en {scope.kind_label.lower()} {scope.label}."
            ),
            "resuelta": False,
        }
        if scope.kind == "CAJA" and scope.caja is not None:
            alert_defaults.update(
                {
                    "caja": scope.caja,
                    "sucursal": scope.sucursal,
                    "turno": scope.caja.turno,
                    "usuario": scope.caja.usuario,
                }
            )
        elif scope.kind == "SUCURSAL" and scope.sucursal is not None:
            alert_defaults.update(
                {
                    "caja": None,
                    "sucursal": scope.sucursal,
                    "turno": None,
                    "usuario": None,
                }
            )
        else:
            alert_defaults.update(
                {
                    "caja": None,
                    "sucursal": None,
                    "turno": None,
                    "usuario": None,
                }
            )
        active_alerts.append(_upsert_alert(dedupe_key=dedupe_key, **alert_defaults))

    stale_alerts = AlertaOperativa.objects.filter(
        tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO,
        periodo_fecha=scope.fecha_operativa,
        resuelta=False,
    ).filter(_rubro_alert_scope_filter(scope))
    if active_keys:
        stale_alerts = stale_alerts.exclude(dedupe_key__in=active_keys)
    stale_alerts.update(resuelta=True)
    return active_alerts


def resync_operational_control_for_caja(caja: Caja) -> None:
    caja = Caja.objects.select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    scopes = [
        build_global_control_scope(fecha_operativa=caja.fecha_operativa),
        build_branch_control_scope(fecha_operativa=caja.fecha_operativa, sucursal=caja.sucursal),
        build_box_control_scope(caja=caja),
    ]
    for scope in scopes:
        build_operational_control_snapshot(scope, sync_alerts=True)


def _distinct_operational_scope_rows(*, rubro: RubroOperativo | None = None):
    queryset = MovimientoCaja.objects.filter(
        tipo=MovimientoCaja.Tipo.GASTO,
        rubro_operativo__isnull=False,
        estado=MovimientoCaja.Estado.REGISTRADO,
    )
    if rubro is not None:
        queryset = queryset.filter(rubro_operativo=rubro)
    return queryset.values(
        "caja__fecha_operativa",
        "caja__sucursal_id",
        "caja_id",
    ).distinct()


def resync_operational_control_for_rubro(rubro: RubroOperativo) -> None:
    rows = list(_distinct_operational_scope_rows(rubro=rubro))
    if not rows:
        return

    branch_ids = {row["caja__sucursal_id"] for row in rows if row["caja__sucursal_id"]}
    box_ids = {row["caja_id"] for row in rows if row["caja_id"]}
    branches = Sucursal.objects.in_bulk(branch_ids)
    boxes = Caja.objects.select_related("turno", "sucursal", "usuario").in_bulk(box_ids)

    for fecha_operativa in {row["caja__fecha_operativa"] for row in rows}:
        build_operational_control_snapshot(
            build_global_control_scope(fecha_operativa=fecha_operativa),
            sync_alerts=True,
        )
    for fecha_operativa, sucursal_id in {
        (row["caja__fecha_operativa"], row["caja__sucursal_id"])
        for row in rows
        if row["caja__sucursal_id"]
    }:
        sucursal = branches.get(sucursal_id)
        if sucursal is None:
            continue
        build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=fecha_operativa, sucursal=sucursal),
            sync_alerts=True,
        )
    for box_id in {row["caja_id"] for row in rows if row["caja_id"]}:
        box = boxes.get(box_id)
        if box is None:
            continue
        build_operational_control_snapshot(build_box_control_scope(caja=box), sync_alerts=True)


def resync_all_operational_controls() -> int:
    rows = list(_distinct_operational_scope_rows())
    if not rows:
        return 0

    branch_ids = {row["caja__sucursal_id"] for row in rows if row["caja__sucursal_id"]}
    box_ids = {row["caja_id"] for row in rows if row["caja_id"]}
    branches = Sucursal.objects.in_bulk(branch_ids)
    boxes = Caja.objects.select_related("turno", "sucursal", "usuario").in_bulk(box_ids)

    recalculated = 0
    for fecha_operativa in {row["caja__fecha_operativa"] for row in rows}:
        build_operational_control_snapshot(
            build_global_control_scope(fecha_operativa=fecha_operativa),
            sync_alerts=True,
        )
        recalculated += 1
    for fecha_operativa, sucursal_id in {
        (row["caja__fecha_operativa"], row["caja__sucursal_id"])
        for row in rows
        if row["caja__sucursal_id"]
    }:
        sucursal = branches.get(sucursal_id)
        if sucursal is None:
            continue
        build_operational_control_snapshot(
            build_branch_control_scope(fecha_operativa=fecha_operativa, sucursal=sucursal),
            sync_alerts=True,
        )
        recalculated += 1
    for box_id in {row["caja_id"] for row in rows if row["caja_id"]}:
        box = boxes.get(box_id)
        if box is None:
            continue
        build_operational_control_snapshot(build_box_control_scope(caja=box), sync_alerts=True)
        recalculated += 1
    return recalculated


@transaction.atomic
def open_box(*, user, turno: Turno, sucursal: Sucursal, fecha_operativa, monto_inicial: Decimal, actor=None) -> Caja:
    actor = actor or user
    _require_actor(actor)
    if user is None:
        raise ValidationError({"usuario": "Se requiere un usuario responsable para abrir una caja."})
    if not can_assign_box_to_user(actor, user):
        raise PermissionDenied("No tenes permiso para asignar una caja a otro usuario.")
    if monto_inicial < 0:
        raise ValidationError({"monto_inicial": "El monto inicial no puede ser negativo."})
    if not is_cashops_admin(actor) and getattr(user, "usuario_fijo", False):
        base_sucursal_id = getattr(user, "sucursal_base_id", None)
        if base_sucursal_id is None:
            raise ValidationError({"sucursal": "El usuario fijo necesita sucursal base."})
        if sucursal.id != base_sucursal_id:
            raise ValidationError({"sucursal": "El usuario fijo solo puede abrir cajas en su sucursal base."})

    turno = Turno.objects.select_for_update().select_related("empresa").get(pk=turno.pk)
    if Caja.objects.filter(
        usuario=user,
        turno=turno,
        sucursal=sucursal,
        estado=Caja.Estado.ABIERTA,
    ).exists():
        raise ValidationError(
            {"usuario": "Ya existe una caja abierta para ese usuario en este turno y sucursal."}
        )

    caja = Caja.objects.create(
        sucursal=sucursal,
        turno=turno,
        fecha_operativa=fecha_operativa,
        usuario=user,
        monto_inicial=monto_inicial,
        estado=Caja.Estado.ABIERTA,
        abierta_en=timezone.now(),
    )
    if monto_inicial > 0:
        _create_movement(
            caja=caja,
            tipo=MovimientoCaja.Tipo.APERTURA,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=monto_inicial,
            categoria="APERTURA",
            observacion="Monto inicial de caja",
            creado_por=actor,
        )
    return caja


def _validate_open_box(caja: Caja, *, actor=None, lock: bool = True) -> Caja:
    if lock:
        caja = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    if actor is not None:
        ensure_can_operate_box(actor, caja)
    if caja.estado != Caja.Estado.ABIERTA:
        raise ValidationError({"caja": "La caja esta cerrada."})
    pass  # turno is catalog-only; no estado to check
    return caja


@transaction.atomic
def register_cash_income(
    *,
    caja: Caja,
    monto: Decimal,
    categoria: str,
    observacion: str = "",
    creado_por=None,
    actor=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        creado_por=actor,
    )


@transaction.atomic
def register_expense(
    *,
    caja: Caja,
    monto: Decimal,
    rubro_operativo: RubroOperativo,
    categoria: str,
    observacion: str = "",
    sucursal_destino=None,
    creado_por=None,
    actor=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if rubro_operativo is None:
        raise ValidationError({"rubro_operativo": "El rubro es obligatorio para gastos operativos."})
    if not rubro_operativo.activo or rubro_operativo.es_sistema:
        raise ValidationError({"rubro_operativo": "Tenes que elegir un rubro operativo activo y valido."})
    movement = _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.GASTO,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        rubro_operativo=rubro_operativo,
        creado_por=actor,
    )
    if sucursal_destino is not None:
        Transferencia.objects.create(
            tipo=Transferencia.Tipo.ENTRE_SUCURSALES,
            clase=Transferencia.Clase.MERCADERIA,
            sucursal_origen=caja.sucursal,
            sucursal_destino=sucursal_destino,
            observacion=f"Egreso #{movement.id}: {categoria}" if not observacion else f"Egreso #{movement.id}: {observacion}",
            creado_por=actor,
        )
    resync_operational_control_for_caja(caja)
    return movement


@transaction.atomic
def register_card_sale(
    *,
    caja: Caja,
    monto: Decimal,
    observacion: str = "",
    creado_por=None,
    actor=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        impacta_saldo_caja=False,
        categoria="POS",
        observacion=observacion,
        creado_por=actor,
    )


@transaction.atomic
def register_general_sale(
    *,
    caja: Caja,
    monto: Decimal,
    tipo_venta: str,
    rubro: RubroOperativo,
    observacion: str = "",
    creado_por=None,
    actor=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    caja = _validate_open_box(caja, actor=actor)

    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if rubro is None:
        raise ValidationError({"rubro": "El rubro es obligatorio para registrar la venta."})

    canal = CanalIngreso.objects.filter(codigo=tipo_venta, activo=True).first()
    if not canal:
        raise ValidationError({"tipo_venta": "Canal de ingreso no válido."})

    movement = _create_movement(
        caja=caja,
        tipo=tipo_venta,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        impacta_saldo_caja=canal.impacta_saldo_caja,
        categoria=rubro.nombre,
        observacion=observacion,
        rubro_operativo=rubro,
        creado_por=actor,
    )
    return movement


@transaction.atomic
def transfer_between_boxes(
    *,
    caja_origen: Caja,
    caja_destino: Caja,
    monto: Decimal,
    observacion: str = "",
    creado_por=None,
    actor=None,
) -> Transferencia:
    actor = actor or creado_por
    _require_actor(actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if caja_origen.pk == caja_destino.pk:
        raise ValidationError({"caja_destino": "El origen y el destino no pueden ser la misma caja."})

    cajas = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").filter(
        pk__in=[caja_origen.pk, caja_destino.pk]
    ).order_by("pk")
    locked = {box.pk: box for box in cajas}
    caja_origen = _validate_open_box(locked[caja_origen.pk], actor=actor, lock=False)
    caja_destino = _validate_open_box(locked[caja_destino.pk], actor=actor, lock=False)
    if caja_origen.sucursal_id != caja_destino.sucursal_id:
        raise ValidationError(
            {"caja_destino": "El arrastre o traspaso entre cajas solo se permite dentro de la misma sucursal."}
        )
    _validate_available_funds(caja_origen, monto)

    transferencia = Transferencia.objects.create(
        tipo=Transferencia.Tipo.ENTRE_CAJAS,
        clase=Transferencia.Clase.DINERO,
        caja_origen=caja_origen,
        caja_destino=caja_destino,
        sucursal_origen=caja_origen.sucursal,
        sucursal_destino=caja_destino.sucursal,
        monto=monto,
        observacion=observacion,
        creado_por=actor,
    )
    _create_movement(
        caja=caja_origen,
        tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria="TRANSFERENCIA",
        observacion=observacion,
        transferencia=transferencia,
        creado_por=actor,
    )
    _create_movement(
        caja=caja_destino,
        tipo=MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        categoria="TRANSFERENCIA",
        observacion=observacion,
        transferencia=transferencia,
        creado_por=actor,
    )
    return transferencia


@transaction.atomic
def transfer_between_branches(
    *,
    sucursal_origen: Sucursal,
    sucursal_destino: Sucursal,
    clase: str,
    monto: Decimal | None = None,
    observacion: str = "",
    caja_origen: Caja | None = None,
    caja_destino: Caja | None = None,
    creado_por=None,
    actor=None,
) -> Transferencia:
    actor = actor or creado_por
    _require_actor(actor)
    raise ValidationError({"__all__": BRANCH_TRANSFER_DISABLED_REASON})
    if sucursal_origen.pk == sucursal_destino.pk:
        raise ValidationError({"sucursal_destino": "El origen y el destino no pueden ser la misma sucursal."})

    if clase == Transferencia.Clase.DINERO and (monto is None or monto <= 0):
        raise ValidationError({"monto": "El monto es obligatorio para transferencias de dinero."})
    if clase == Transferencia.Clase.DINERO and (caja_origen is None or caja_destino is None):
        raise ValidationError(
            {
                "caja_origen": "Las transferencias de dinero requieren caja origen y destino.",
                "caja_destino": "Las transferencias de dinero requieren caja origen y destino.",
            }
        )
    if clase == Transferencia.Clase.MERCADERIA and not observacion:
        raise ValidationError({"observacion": "La observacion es obligatoria para mercaderia."})

    if caja_origen and caja_origen.sucursal_id != sucursal_origen.pk:
        raise ValidationError({"caja_origen": "La caja de origen debe pertenecer a la sucursal origen."})
    if caja_destino and caja_destino.sucursal_id != sucursal_destino.pk:
        raise ValidationError({"caja_destino": "La caja de destino debe pertenecer a la sucursal destino."})

    if clase == Transferencia.Clase.DINERO and caja_origen and caja_destino:
        cajas = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").filter(
            pk__in=[caja_origen.pk, caja_destino.pk]
        ).order_by("pk")
        locked = {box.pk: box for box in cajas}
        caja_origen = _validate_open_box(locked[caja_origen.pk], actor=actor, lock=False)
        caja_destino = _validate_open_box(locked[caja_destino.pk], actor=actor, lock=False)
        _validate_available_funds(caja_origen, monto)

    transferencia = Transferencia.objects.create(
        tipo=Transferencia.Tipo.ENTRE_SUCURSALES,
        clase=clase,
        caja_origen=caja_origen,
        caja_destino=caja_destino,
        sucursal_origen=sucursal_origen,
        sucursal_destino=sucursal_destino,
        monto=monto if clase == Transferencia.Clase.DINERO else None,
        observacion=observacion,
        creado_por=actor,
    )

    if clase == Transferencia.Clase.DINERO and caja_origen and caja_destino:
        _create_movement(
            caja=caja_origen,
            tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_SALIDA,
            sentido=MovimientoCaja.Sentido.EGRESO,
            monto=monto,
            categoria="TRANSFERENCIA SUCURSAL",
            observacion=observacion,
            transferencia=transferencia,
            creado_por=actor,
        )
        _create_movement(
            caja=caja_destino,
            tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_ENTRADA,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=monto,
            categoria="TRANSFERENCIA SUCURSAL",
            observacion=observacion,
            transferencia=transferencia,
            creado_por=actor,
        )

    return transferencia


def is_closed_box_movement_correctable(movement: MovimientoCaja) -> bool:
    return (
        movement.caja.estado == Caja.Estado.CERRADA
        and movement.estado == MovimientoCaja.Estado.REGISTRADO
        and movement.tipo not in CLOSED_BOX_CORRECTION_BLOCKED_TYPES
    )


def _validate_closed_box_movement_for_correction(movement: MovimientoCaja, *, actor) -> MovimientoCaja:
    _require_actor(actor)
    ensure_closed_box_correction(actor)
    movement = (
        MovimientoCaja.objects.select_for_update()
        .select_related("caja", "caja__turno", "caja__sucursal", "caja__usuario", "rubro_operativo")
        .get(pk=movement.pk)
    )
    if movement.caja.estado != Caja.Estado.CERRADA:
        raise ValidationError({"caja": "Solo se pueden corregir movimientos de cajas cerradas."})
    if movement.estado != MovimientoCaja.Estado.REGISTRADO:
        raise ValidationError({"movimiento": "El movimiento ya fue anulado."})
    if movement.tipo in CLOSED_BOX_CORRECTION_BLOCKED_TYPES:
        raise ValidationError({"movimiento": "Este tipo de movimiento requiere un circuito de corrección específico."})
    return movement


def _recalculate_closed_box_after_correction(caja: Caja, *, actor=None, motivo: str = "") -> CierreCaja:
    caja = Caja.objects.select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    cierre = CierreCaja.objects.select_for_update().select_related("caja").get(caja=caja)
    cierre.saldo_esperado = caja.saldo_esperado
    cierre.diferencia = cierre.saldo_fisico - cierre.saldo_esperado
    cierre.estado = (
        CierreCaja.Estado.JUSTIFICADO
        if abs(cierre.diferencia) > CLOSING_DIFF_THRESHOLD
        else CierreCaja.Estado.AUTO
    )
    cierre.save(update_fields=["saldo_esperado", "diferencia", "estado"])

    if abs(cierre.diferencia) > CLOSING_DIFF_THRESHOLD:
        if not hasattr(cierre, "justificacion"):
            Justificacion.objects.create(
                cierre=cierre,
                motivo=motivo or "Corrección posterior de movimiento en caja cerrada.",
                creado_por=actor,
            )
        _upsert_alert(
            dedupe_key=_build_closing_alert_key(cierre=cierre),
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            cierre=cierre,
            caja=caja,
            turno=caja.turno,
            sucursal=caja.sucursal,
            usuario=caja.usuario,
            rubro_operativo=None,
            periodo_fecha=caja.fecha_operativa,
            mensaje=f"Diferencia grave detectada en caja {caja.id}: {cierre.diferencia}.",
            resuelta=False,
        )
    else:
        AlertaOperativa.objects.filter(
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            cierre=cierre,
            resuelta=False,
        ).update(resuelta=True)

    resync_operational_control_for_caja(caja)
    return cierre


@transaction.atomic
def update_closed_box_movement(
    *,
    movement: MovimientoCaja,
    monto: Decimal,
    categoria: str = "",
    observacion: str = "",
    rubro_operativo: RubroOperativo | None = None,
    motivo: str,
    actor=None,
) -> MovimientoCaja:
    movement = _validate_closed_box_movement_for_correction(movement, actor=actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la corrección es obligatorio."})
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if movement.tipo == MovimientoCaja.Tipo.GASTO and rubro_operativo is None:
        raise ValidationError({"rubro_operativo": "El rubro es obligatorio para gastos operativos."})
    if rubro_operativo and not rubro_operativo.activo and not rubro_operativo.es_sistema:
        raise ValidationError({"rubro_operativo": "Solo podes usar rubros operativos activos."})

    previous = {
        "monto": movement.monto,
        "categoria": movement.categoria,
        "observacion": movement.observacion,
        "rubro_operativo": movement.rubro_operativo,
    }
    movement.monto = monto
    movement.categoria = (categoria or "").strip()
    movement.observacion = (observacion or "").strip()
    movement.rubro_operativo = rubro_operativo
    movement.actualizado_por = actor
    movement.full_clean()
    movement.save(update_fields=["monto", "categoria", "observacion", "rubro_operativo", "actualizado_por", "actualizado_en"])

    MovimientoCajaCorreccion.objects.create(
        movimiento=movement,
        accion=MovimientoCajaCorreccion.Accion.EDICION,
        motivo=motivo,
        monto_anterior=previous["monto"],
        monto_nuevo=movement.monto,
        categoria_anterior=previous["categoria"],
        categoria_nueva=movement.categoria,
        observacion_anterior=previous["observacion"],
        observacion_nueva=movement.observacion,
        rubro_operativo_anterior=previous["rubro_operativo"],
        rubro_operativo_nuevo=movement.rubro_operativo,
        creado_por=actor,
    )
    _recalculate_closed_box_after_correction(movement.caja, actor=actor, motivo=motivo)
    return movement


@transaction.atomic
def annul_closed_box_movement(
    *,
    movement: MovimientoCaja,
    motivo: str,
    actor=None,
) -> MovimientoCaja:
    movement = _validate_closed_box_movement_for_correction(movement, actor=actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la anulación es obligatorio."})

    MovimientoCajaCorreccion.objects.create(
        movimiento=movement,
        accion=MovimientoCajaCorreccion.Accion.ANULACION,
        motivo=motivo,
        monto_anterior=movement.monto,
        monto_nuevo=None,
        categoria_anterior=movement.categoria,
        categoria_nueva=movement.categoria,
        observacion_anterior=movement.observacion,
        observacion_nueva=movement.observacion,
        rubro_operativo_anterior=movement.rubro_operativo,
        rubro_operativo_nuevo=movement.rubro_operativo,
        creado_por=actor,
    )
    movement.estado = MovimientoCaja.Estado.ANULADO
    movement.motivo_anulacion = motivo
    movement.anulado_por = actor
    movement.anulado_en = timezone.now()
    movement.actualizado_por = actor
    movement.full_clean()
    movement.save(update_fields=["estado", "motivo_anulacion", "anulado_por", "anulado_en", "actualizado_por", "actualizado_en"])
    _recalculate_closed_box_after_correction(movement.caja, actor=actor, motivo=motivo)
    return movement


@transaction.atomic
def close_box(
    *,
    caja: Caja,
    saldo_fisico: Decimal,
    justificacion: str = "",
    cerrado_por=None,
    actor=None,
) -> CierreCaja:
    actor = actor or cerrado_por
    _require_actor(actor)
    caja_ref = caja
    caja = _validate_open_box(_lock_caja(caja), actor=actor, lock=False)

    saldo_esperado = caja.saldo_esperado
    diferencia = saldo_fisico - saldo_esperado
    abs_difference = abs(diferencia)

    if abs_difference > CLOSING_DIFF_THRESHOLD and not justificacion.strip():
        raise ValidationError({"justificacion": "La diferencia supera 10.000 y requiere justificacion."})

    ajuste_movimiento = None
    if diferencia != 0 and abs_difference <= CLOSING_DIFF_THRESHOLD:
        ajuste_movimiento = _create_movement(
            caja=caja,
            tipo=MovimientoCaja.Tipo.AJUSTE_CIERRE,
            sentido=MovimientoCaja.Sentido.INGRESO if diferencia > 0 else MovimientoCaja.Sentido.EGRESO,
            monto=abs_difference,
            categoria="CIERRE",
            observacion="Ajuste de cierre automatico",
            creado_por=actor,
        )

    cierre = CierreCaja.objects.create(
        caja=caja,
        saldo_esperado=saldo_esperado,
        saldo_fisico=saldo_fisico,
        diferencia=diferencia,
        estado=CierreCaja.Estado.JUSTIFICADO if abs_difference > CLOSING_DIFF_THRESHOLD else CierreCaja.Estado.AUTO,
        ajuste_movimiento=ajuste_movimiento,
        cerrado_por=actor,
    )

    if abs_difference > CLOSING_DIFF_THRESHOLD and justificacion.strip():
        Justificacion.objects.create(cierre=cierre, motivo=justificacion.strip(), creado_por=actor)
        _upsert_alert(
            dedupe_key=_build_closing_alert_key(cierre=cierre),
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            cierre=cierre,
            caja=caja,
            turno=caja.turno,
            sucursal=caja.sucursal,
            usuario=caja.usuario,
            rubro_operativo=None,
            periodo_fecha=caja.fecha_operativa,
            mensaje=f"Diferencia grave detectada en caja {caja.id}: {diferencia}.",
            resuelta=False,
        )

    caja.estado = Caja.Estado.CERRADA
    caja.cerrada_en = timezone.now()
    caja.cerrada_por = actor
    caja.save(update_fields=["estado", "cerrada_en", "cerrada_por"])
    caja_ref.estado = caja.estado
    caja_ref.cerrada_en = caja.cerrada_en
    caja_ref.cerrada_por = caja.cerrada_por

    if saldo_fisico != 0 and caja.sucursal_id:
        from django.apps import apps
        CajaCentral = apps.get_model("treasury", "CajaCentral")
        MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
        caja_central = CajaCentral.objects.filter(sucursal=caja.sucursal, activo=True).first()
        if caja_central is None:
            caja_central = CajaCentral.objects.create(
                sucursal=caja.sucursal,
                nombre=f"Caja Central {caja.sucursal.nombre}",
                activo=True,
            )
        if saldo_fisico > 0:
            central_type = "INGRESO_CAJA"
            central_amount = saldo_fisico
            central_concept = f"Cierre caja #{caja.id}"
            central_observations = ""
        else:
            central_type = "AJUSTE_NEGATIVO"
            central_amount = abs(saldo_fisico)
            central_concept = f"Cierre caja #{caja.id} - saldo negativo"
            central_observations = "Saldo fisico negativo informado al cierre de caja."
        MovimientoCajaCentral.objects.create(
            caja_central=caja_central,
            fecha=caja.fecha_operativa,
            tipo=central_type,
            monto=central_amount,
            concepto=central_concept,
            observaciones=central_observations,
            creado_por=actor,
        )

    return cierre
