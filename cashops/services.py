from dataclasses import dataclass
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Q, Sum, Value, When
from django.utils import timezone

from .models import (
    AlertaOperativa,
    Caja,
    CajaCorreccion,
    CajaValidacion,
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
from .permissions import (
    can_assign_box_to_user,
    ensure_can_operate_box,
    ensure_cash_validation,
    ensure_cash_validation_undo,
    ensure_closed_box_correction,
    ensure_delete_movement_in_box,
    is_cashops_admin,
)


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

    if caja.estado == Caja.Estado.ANULADA:
        return {
            "label": "Eliminada",
            "badge_class": "badge-danger",
            "detail": "Caja eliminada/anulada y conservada solo por auditoría.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
    if caja.estado == Caja.Estado.CERRADA and caja.validacion_estado == Caja.ValidacionEstado.RECHAZADA:
        return {
            "label": "Validación rechazada",
            "badge_class": "badge-danger",
            "detail": "El efectivo entregado no coincidió: corregir la carga y volver a validar. No contabiliza en totales.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
    if caja.estado == Caja.Estado.ABIERTA and caja.validacion_estado == Caja.ValidacionEstado.RECHAZADA:
        # Caja devuelta al cajero por un rechazo de validacion: sigue abierta
        # para corregirla, pero fuera de todos los totales hasta revalidarse.
        return {
            "label": "Devuelta por rechazo",
            "badge_class": "badge-danger",
            "detail": "La validación del efectivo fue rechazada y la caja volvió al cajero: corregí lo que haga falta y volvé a cerrarla para pedir la validación de nuevo.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
    if caja.estado == Caja.Estado.CERRADA and caja.validacion_estado == Caja.ValidacionEstado.PENDIENTE:
        return {
            "label": "Pendiente de validación",
            "badge_class": "badge-warning",
            "detail": "Cerrada con efectivo declarado: no contabiliza en totales hasta que se valide.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
    if caja.estado == Caja.Estado.CERRADA and caja.validacion_estado == Caja.ValidacionEstado.VALIDADA:
        validador = f" por {caja.validada_por}" if caja.validada_por else ""
        fecha_validacion = f" el {caja.validada_en:%d/%m/%Y %H:%M}" if caja.validada_en else ""
        return {
            "label": "Efectivo validado",
            "badge_class": "badge-success",
            "detail": f"Caja cerrada con efectivo validado{validador}{fecha_validacion}. Contabiliza normal y queda disponible para consulta.",
            "last_activity_at": last_activity_at,
            "post_opening_count": len(post_opening_movements),
        }
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

    for deuda in caja.deudas_originadas.select_related("proveedor").all():
        is_annulled_debt = deuda.estado == deuda.Estado.ANULADA
        if is_annulled_debt:
            debt_detail = f"{deuda.proveedor} - {deuda.concepto}. Anulada: {deuda.motivo_anulacion or 'sin motivo registrado'}."
        else:
            debt_detail = f"{deuda.proveedor} - {deuda.concepto}. No salio efectivo de la caja; tesoreria paga despues."
        events.append(
            {
                "timestamp": deuda.creado_en,
                "kind": "GASTO_DEUDA",
                "badge_class": "badge-muted" if is_annulled_debt else "badge-warning",
                "badge_label": "Deuda anulada" if is_annulled_debt else "Deuda",
                "title": "Gasto registrado como deuda",
                "detail": debt_detail,
                "user_label": str(deuda.creado_por) if deuda.creado_por else "Sin usuario",
                "amount": deuda.importe_total,
                "debt_id": deuda.id,
                "debt_active": not is_annulled_debt,
            }
        )

    for validacion in caja.validaciones.all():
        is_rechazo = validacion.accion == CajaValidacion.Accion.RECHAZO
        events.append(
            {
                "timestamp": validacion.creado_en,
                "kind": "VALIDACION",
                "badge_class": "badge-danger" if is_rechazo else "badge-success",
                "badge_label": validacion.get_accion_display(),
                "title": "Validación de efectivo rechazada" if is_rechazo else "Efectivo validado",
                "detail": validacion.motivo or f"Efectivo esperado ${validacion.efectivo_esperado}.",
                "user_label": str(validacion.usuario) if validacion.usuario else "Sin usuario",
                "amount": validacion.efectivo_esperado,
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
    token_alta=None,
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
        token_alta=token_alta,
    )


def _existing_by_creation_token(manager, token_alta):
    """Devuelve el registro que ya se creo con ese token de alta, si existe.

    El token viaja oculto en el formulario y es unico por render: si ya hay un
    registro con el mismo token, este POST es un reenvio del mismo envio (doble
    click, reintento despues de un timeout, volver atras) y no debe crear nada.
    Sin token el comportamiento es el historico: cada POST crea.
    """
    if not token_alta:
        return None
    return manager.filter(token_alta=token_alta).first()


def _create_movement_once(*, token_alta=None, **kwargs) -> MovimientoCaja:
    """Crea el movimiento tolerando el envio simultaneo del mismo formulario.

    El chequeo de token que hace cada servicio cubre el reenvio normal, donde el
    segundo POST llega despues de que el primero commiteo. Este savepoint cubre
    la carrera real: si los dos POST pasan ese chequeo, el unique de la base
    rechaza al segundo y devolvemos el movimiento que quedo grabado en lugar de
    romper la operacion del cajero.
    """
    try:
        with transaction.atomic():
            return _create_movement(token_alta=token_alta, **kwargs)
    except IntegrityError:
        existing = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
        if existing is None:
            raise
        return existing


def _existing_transfer_by_creation_token(token_alta) -> Transferencia | None:
    """Devuelve el traspaso que ya se grabo con ese token de alta, si existe.

    El token vive en el movimiento de salida, que es el que representa el envio
    del formulario; desde ahi se llega al traspaso completo.
    """
    salida = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
    if salida is None:
        return None
    return salida.transferencia


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
    ).exclude(estado=Caja.Estado.ANULADA).exclude(
        validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
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
    ).filter(_alerts_filter_for_scope(scope)).exclude(
        # EP-13: las alertas de una caja pendiente de validacion no cuentan
        # hasta que la caja vuelva a contabilizar.
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
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
    ).annotate(severity_order=severity_order, scope_order=scope_order).exclude(
        # EP-13: alertas de cajas pendientes de validacion quedan fuera del
        # panel hasta que la caja se valide y vuelva a contabilizar.
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
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
    if getattr(scope, "caja", None) is None:
        # EP-13: fuera de la pantalla propia de la caja, una caja pendiente
        # de validacion no aporta a ningun total ni alerta.
        movement_qs = movement_qs.exclude(caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES)
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
    ).exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
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
    ).exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
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


def treasury_month_is_closed(fecha) -> bool:
    """True si el mes de tesoreria de esa fecha ya esta cerrado. El cierre es
    GLOBAL (CierreMensualTesoreria solo tiene unique por mes y nunca se escribe
    la sucursal), asi que NO hay que filtrar por sucursal: filtrarla daria
    siempre False. Import perezoso via apps.get_model para no acoplar cashops
    a treasury (mismo patron que _push_box_closure_to_central_cash)."""
    from django.apps import apps

    if fecha is None:
        return False
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    return CierreMensualTesoreria.objects.filter(mes=fecha.replace(day=1), cerrado=True).exists()


MONTH_CLOSED_MESSAGE = (
    "El mes de tesoreria de esa fecha ya esta cerrado: es una foto congelada y no se "
    "pueden agregar cajas a un periodo cerrado. Elegi una fecha de un mes abierto."
)


def _treasury_month_is_closed_for_empresa(fecha, empresa_id) -> bool:
    """Mes de tesoreria cerrado PARA ESA EMPRESA.

    Desde que el cierre mensual es por empresa (treasury 0034),
    treasury_month_is_closed quedo global y sobre-bloquea cruzado: una empresa
    que cierra su mes frena a las demas. Los guards nuevos usan este, que
    filtra por la empresa de la caja. Una fila de cierre sin empresa (legacy)
    bloquea igual, porque no se sabe de quien es la foto."""
    from django.apps import apps

    if fecha is None:
        return False
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    return CierreMensualTesoreria.objects.filter(
        Q(empresa_id=empresa_id) | Q(empresa__isnull=True),
        mes=fecha.replace(day=1),
        cerrado=True,
    ).exists()


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
    if treasury_month_is_closed(fecha_operativa):
        raise ValidationError({"fecha_operativa": MONTH_CLOSED_MESSAGE})
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
        fecha_operativa=fecha_operativa,
        estado=Caja.Estado.ABIERTA,
    ).exists():
        raise ValidationError(
            {"usuario": "Ya existe una caja abierta para ese usuario en este turno, sucursal y fecha."}
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


def _lock_box_for_debt(caja: Caja, *, actor=None, permitir_caja_cerrada: bool = False) -> Caja:
    """Bloquea y valida una caja para cargar deuda (gasto como deuda).

    A diferencia de _validate_open_box, admite cajas CERRADAS cuando el actor
    tiene el permiso (permitir_caja_cerrada=True), porque la deuda no mueve
    efectivo ni reabre la caja. Nunca admite ANULADA. Mantiene el control de
    propiedad/alcance (ensure_can_operate_box) y el lock por fila.
    """
    caja = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    if actor is not None:
        ensure_can_operate_box(actor, caja)
    if caja.estado == Caja.Estado.ABIERTA:
        return caja
    if caja.estado == Caja.Estado.CERRADA and permitir_caja_cerrada:
        return caja
    if caja.estado == Caja.Estado.CERRADA:
        raise ValidationError({"caja": "La caja esta cerrada."})
    raise ValidationError({"caja": "La caja no admite cargar deuda en este estado."})


@transaction.atomic
def register_cash_income(
    *,
    caja: Caja,
    monto: Decimal,
    categoria: str,
    observacion: str = "",
    creado_por=None,
    actor=None,
    token_alta=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    # Antes del lock: si el envio ya se grabo, devolvemos ese movimiento incluso
    # si la caja se cerro en el medio.
    already = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
    if already is not None:
        return already
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement_once(
        caja=caja,
        tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        creado_por=actor,
        token_alta=token_alta,
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
    token_alta=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    # Antes del lock: si el envio ya se grabo, devolvemos ese movimiento incluso
    # si la caja se cerro en el medio. Tampoco se repite la transferencia de
    # mercaderia ni el resync de alertas, que ya corrieron con el primer POST.
    already = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
    if already is not None:
        return already
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if rubro_operativo is None:
        raise ValidationError({"rubro_operativo": "El rubro es obligatorio para gastos operativos."})
    if not rubro_operativo.activo or rubro_operativo.es_sistema:
        raise ValidationError({"rubro_operativo": "Tenes que elegir un rubro operativo activo y valido."})
    movement = _create_movement_once(
        caja=caja,
        tipo=MovimientoCaja.Tipo.GASTO,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        rubro_operativo=rubro_operativo,
        creado_por=actor,
        token_alta=token_alta,
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
def register_box_expense_debt(
    *,
    caja: Caja,
    proveedor,
    categoria=None,
    rubro=None,
    monto: Decimal,
    concepto: str,
    referencia_comprobante: str = "",
    observacion: str = "",
    fecha_factura=None,
    fecha_vencimiento=None,
    sucursal=None,
    permitir_caja_cerrada: bool = False,
    actor=None,
    token_alta=None,
):
    """EP-13 US-13.6: gasto desde caja registrado como deuda pendiente.

    No crea ningun movimiento de caja: el efectivo no sale de la caja y el
    gasto entra a la lectura economica una sola vez, como deuda del periodo
    de la fecha de factura. Tesoreria registra el pago real despues y recien
    ahi impacta la lectura financiera.

    permitir_caja_cerrada habilita cargar la deuda sobre una caja ya cerrada
    (para quien tenga el permiso CASHOPS_DEBT_CLOSED); no reabre la caja ni
    toca su efectivo. fecha_factura define la fecha de emision y el periodo
    economico; si no se indica, se usa la fecha operativa de la caja.
    """
    from treasury.models import CuentaPorPagar
    from treasury.services import _ensure_payable_category_is_economic, get_or_create_payable_category_for_rubro

    _require_actor(actor)
    # Antes de resolver categoria y tomar el lock: si el envio ya se grabo,
    # devolvemos esa deuda en vez de crear una segunda.
    already = _existing_by_creation_token(CuentaPorPagar.objects, token_alta)
    if already is not None:
        return already
    # El cajero elige un RUBRO; la deuda igual necesita una categoria economica.
    # Si no vino categoria explicita, la resolvemos (reusa/crea) desde el rubro.
    if categoria is None:
        categoria = get_or_create_payable_category_for_rubro(rubro, actor=actor)
    caja = _lock_box_for_debt(caja, actor=actor, permitir_caja_cerrada=permitir_caja_cerrada)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    concepto = (concepto or "").strip()
    if not concepto:
        raise ValidationError({"concepto": "El concepto es obligatorio."})
    if not proveedor.activo:
        raise ValidationError({"proveedor": "El proveedor esta inactivo."})
    if not categoria.activo:
        raise ValidationError({"categoria": "La categoría está inactiva."})
    _ensure_payable_category_is_economic(categoria)
    fecha_factura = fecha_factura or caja.fecha_operativa
    # La deuda se imputa a la sucursal de la caja, salvo que el usuario elija
    # otra que tenga habilitada (sucursales_para_deuda) y sea de la misma empresa.
    if sucursal is None or sucursal.pk == caja.sucursal_id:
        sucursal_deuda = caja.sucursal
    else:
        if actor is None or not actor.sucursales_para_deuda().filter(pk=sucursal.pk).exists():
            raise ValidationError({"sucursal": "No podés cargar deuda para esa sucursal."})
        if sucursal.empresa_id != caja.sucursal.empresa_id:
            raise ValidationError({"sucursal": "La sucursal debe pertenecer a la empresa de la caja."})
        sucursal_deuda = sucursal
    payable = CuentaPorPagar(
        sucursal=sucursal_deuda,
        caja_origen=caja,
        proveedor=proveedor,
        categoria=categoria,
        concepto=concepto,
        fecha_emision=fecha_factura,
        fecha_vencimiento=fecha_vencimiento or fecha_factura,
        periodo_referencia=fecha_factura.replace(day=1),
        importe_total=monto,
        saldo_pendiente=monto,
        estado=CuentaPorPagar.Estado.PENDIENTE,
        referencia_comprobante=referencia_comprobante,
        observaciones=observacion,
        creado_por=actor,
        token_alta=token_alta,
    )
    try:
        with transaction.atomic():
            payable.full_clean()
            payable.save()
    except (IntegrityError, ValidationError):
        # Puede ser el envio simultaneo del mismo formulario. Solo si la deuda ya
        # quedo grabada con este token la devolvemos: cualquier otro rechazo
        # (referencia repetida, fechas, montos) sigue viaje al cajero.
        existing = _existing_by_creation_token(CuentaPorPagar.objects, token_alta)
        if existing is None:
            raise
        return existing
    return payable


@transaction.atomic
def register_card_sale(
    *,
    caja: Caja,
    monto: Decimal,
    observacion: str = "",
    creado_por=None,
    actor=None,
    token_alta=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    already = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
    if already is not None:
        return already
    caja = _validate_open_box(caja, actor=actor)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement_once(
        caja=caja,
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        impacta_saldo_caja=False,
        categoria="POS",
        observacion=observacion,
        creado_por=actor,
        token_alta=token_alta,
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
    token_alta=None,
) -> MovimientoCaja:
    actor = actor or creado_por
    _require_actor(actor)
    already = _existing_by_creation_token(MovimientoCaja.objects, token_alta)
    if already is not None:
        return already
    caja = _validate_open_box(caja, actor=actor)

    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if rubro is None:
        raise ValidationError({"rubro": "El rubro es obligatorio para registrar la venta."})

    canal = CanalIngreso.objects.filter(codigo=tipo_venta, activo=True).first()
    if not canal:
        raise ValidationError({"tipo_venta": "Canal de ingreso no válido."})

    movement = _create_movement_once(
        caja=caja,
        tipo=tipo_venta,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        impacta_saldo_caja=canal.impacta_saldo_caja,
        categoria=rubro.nombre,
        observacion=observacion,
        rubro_operativo=rubro,
        creado_por=actor,
        token_alta=token_alta,
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
    token_alta=None,
) -> Transferencia:
    actor = actor or creado_por
    _require_actor(actor)
    # El traspaso graba tres registros (transferencia + salida + entrada), asi
    # que el token no puede resolverse con un savepoint por movimiento: se
    # chequea antes de empezar y otra vez con las cajas ya bloqueadas, para que
    # un reenvio nunca deje la mitad de la operacion.
    already = _existing_transfer_by_creation_token(token_alta)
    if already is not None:
        return already
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
    # Con el lock tomado: si el envio simultaneo gano la carrera, ya commiteo y
    # aca lo vemos. Todavia no grabamos nada, asi que salir es seguro.
    already = _existing_transfer_by_creation_token(token_alta)
    if already is not None:
        return already
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
    # El token queda en la salida, que es el movimiento que representa el envio.
    _create_movement(
        caja=caja_origen,
        tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria="TRANSFERENCIA",
        observacion=observacion,
        transferencia=transferencia,
        creado_por=actor,
        token_alta=token_alta,
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
        # of=("self","caja"): `rubro_operativo` es nullable, asi que su
        # select_related entra como LEFT OUTER JOIN y Postgres rechaza un FOR
        # UPDATE que abarque el lado nullable de un outer join. Bloqueamos solo
        # el movimiento y su caja, que es lo que se valida y se toca. SQLite
        # ignora FOR UPDATE, por eso esto no se ve en los tests locales.
        MovimientoCaja.objects.select_for_update(of=("self", "caja"))
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


def is_box_movement_deletable(movement: MovimientoCaja) -> bool:
    """Un movimiento se puede eliminar (anular) si su caja no esta anulada, el
    movimiento sigue REGISTRADO y su tipo no forma parte de un par cross-caja
    (transferencias/apertura/ajuste de cierre). Aplica a cajas abiertas Y cerradas."""
    return (
        movement.caja.estado != Caja.Estado.ANULADA
        and movement.estado == MovimientoCaja.Estado.REGISTRADO
        and movement.tipo not in CLOSED_BOX_CORRECTION_BLOCKED_TYPES
    )


def _validate_box_movement_for_deletion(movement: MovimientoCaja, *, actor) -> MovimientoCaja:
    _require_actor(actor)
    movement = (
        # of=("self","caja"): ver el comentario en
        # _validate_closed_box_movement_for_correction. `rubro_operativo` es
        # nullable y su LEFT JOIN rompe el FOR UPDATE en Postgres.
        MovimientoCaja.objects.select_for_update(of=("self", "caja"))
        .select_related("caja", "caja__turno", "caja__sucursal", "caja__usuario", "rubro_operativo")
        .get(pk=movement.pk)
    )
    ensure_delete_movement_in_box(actor, movement.caja)
    if movement.caja.estado == Caja.Estado.ANULADA:
        raise ValidationError({"caja": "La caja esta anulada; no se pueden eliminar sus movimientos."})
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


def _snapshot_box_values(caja: Caja) -> dict:
    return {
        "estado": caja.estado,
        "usuario": caja.usuario,
        "sucursal": caja.sucursal,
        "turno": caja.turno,
        "fecha_operativa": caja.fecha_operativa,
        "monto_inicial": caja.monto_inicial,
    }


def _create_box_correction(
    *,
    caja: Caja,
    accion: str,
    motivo: str,
    previous: dict,
    actor=None,
) -> CajaCorreccion:
    return CajaCorreccion.objects.create(
        caja=caja,
        accion=accion,
        motivo=motivo,
        estado_anterior=previous["estado"],
        estado_nuevo=caja.estado,
        usuario_anterior=previous["usuario"],
        usuario_nuevo=caja.usuario,
        sucursal_anterior=previous["sucursal"],
        sucursal_nueva=caja.sucursal,
        turno_anterior=previous["turno"],
        turno_nuevo=caja.turno,
        fecha_operativa_anterior=previous["fecha_operativa"],
        fecha_operativa_nueva=caja.fecha_operativa,
        monto_inicial_anterior=previous["monto_inicial"],
        monto_inicial_nuevo=caja.monto_inicial,
        creado_por=actor,
    )


def _validate_box_for_full_correction(caja: Caja, *, actor) -> Caja:
    _require_actor(actor)
    ensure_closed_box_correction(actor)
    caja = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    if caja.estado == Caja.Estado.ANULADA:
        raise ValidationError({"caja": "La caja ya fue eliminada."})
    return caja


def _reverse_central_cash_closure_for_box(caja: Caja, *, actor) -> None:
    from django.apps import apps

    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    reversal_concept = f"Anulacion cierre caja #{caja.id}"
    if MovimientoCajaCentral.objects.filter(
        concepto=reversal_concept, estado="REGISTRADO"
    ).exists():
        return

    # Los dos filtros llevan estado: un movimiento ya anulado no se revierte de
    # nuevo. Sin esto, anular el ingreso de un cierre y despues anular la caja
    # sacaba la plata dos veces contra un solo ingreso.
    closure_concepts = [f"Cierre caja #{caja.id}", f"Cierre caja #{caja.id} - saldo negativo"]
    closure_movements = MovimientoCajaCentral.objects.filter(
        tipo__in=["INGRESO_CAJA", "AJUSTE_NEGATIVO"],
        estado="REGISTRADO",
    ).filter(
        Q(caja_cierre=caja) | Q(concepto__in=closure_concepts)
    )
    for movement in closure_movements:
        if movement.tipo == "INGRESO_CAJA":
            reversal_type = "AJUSTE_NEGATIVO"
            observations = "Reversa auditada por anulacion de caja cerrada."
        else:
            reversal_type = "AJUSTE_POSITIVO"
            observations = "Reversa auditada de saldo negativo por anulacion de caja cerrada."
        # Igual que el push: si el mes original ya esta cerrado, la reversa se
        # fecha al dia de la anulacion para no alterar un snapshot congelado.
        reversal_date = movement.fecha
        if CierreMensualTesoreria.objects.filter(mes=movement.fecha.replace(day=1), cerrado=True).exists():
            reversal_date = timezone.localdate()
            observations = f"{observations} Mes de tesoreria original cerrado; reversa fechada al dia de la anulacion."
        MovimientoCajaCentral.objects.create(
            caja_central=movement.caja_central,
            fecha=reversal_date,
            tipo=reversal_type,
            monto=movement.monto,
            concepto=reversal_concept,
            observaciones=observations,
            caja_cierre=caja,
            # La reversa hereda la sucursal del movimiento que revierte, si no
            # el egreso compensatorio queda sin local y descuadra el traqueo.
            sucursal_origen_id=movement.sucursal_origen_id or caja.sucursal_id,
            creado_por=actor,
        )


@transaction.atomic
def update_box_metadata(
    *,
    caja: Caja,
    usuario,
    sucursal: Sucursal,
    turno: Turno,
    fecha_operativa: date,
    monto_inicial: Decimal,
    motivo: str,
    actor=None,
) -> Caja:
    caja = _validate_box_for_full_correction(caja, actor=actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la edición es obligatorio."})
    if monto_inicial < 0:
        raise ValidationError({"monto_inicial": "El efectivo inicial no puede ser negativo."})
    if sucursal.empresa_id != turno.empresa_id:
        raise ValidationError({"turno": "El turno debe pertenecer a la misma empresa de la sucursal."})
    if not can_assign_box_to_user(actor, usuario):
        raise PermissionDenied("No tenés permiso para asignar esta caja a ese usuario.")
    # Puerta trasera del guard de open_box: editando la caja se la podria MOVER a
    # un mes ya cerrado. Solo se bloquea si cambia de mes, para no trabar la
    # correccion de una caja que ya vivia en ese periodo.
    if (
        fecha_operativa
        and caja.fecha_operativa
        and fecha_operativa.replace(day=1) != caja.fecha_operativa.replace(day=1)
        and treasury_month_is_closed(fecha_operativa)
    ):
        raise ValidationError({"fecha_operativa": MONTH_CLOSED_MESSAGE})
    if caja.estado == Caja.Estado.ABIERTA and Caja.objects.filter(
        estado=Caja.Estado.ABIERTA,
        usuario=usuario,
        turno=turno,
        sucursal=sucursal,
        fecha_operativa=fecha_operativa,
    ).exclude(pk=caja.pk).exists():
        raise ValidationError({"caja": "Ya existe una caja abierta para ese responsable, sucursal, turno y fecha."})

    previous = _snapshot_box_values(caja)
    caja.usuario = usuario
    caja.sucursal = sucursal
    caja.turno = turno
    caja.fecha_operativa = fecha_operativa
    caja.monto_inicial = monto_inicial
    caja.full_clean()
    caja.save(update_fields=["usuario", "sucursal", "turno", "fecha_operativa", "monto_inicial"])

    _create_box_correction(
        caja=caja,
        accion=CajaCorreccion.Accion.EDICION,
        motivo=motivo,
        previous=previous,
        actor=actor,
    )
    if caja.estado == Caja.Estado.CERRADA and hasattr(caja, "cierre"):
        _recalculate_closed_box_after_correction(caja, actor=actor, motivo=motivo)
    else:
        resync_operational_control_for_caja(caja)
    return caja


def _apply_debt_annulment(deuda, *, motivo: str, actor, now) -> None:
    """Anula (soft-delete) una CuentaPorPagar originada en una caja: estado
    ANULADA, saldo 0 y auditoria (motivo/quien/cuando). Compartido por la
    anulacion de caja completa y la eliminacion puntual de una deuda."""
    deuda.estado = deuda.Estado.ANULADA
    deuda.saldo_pendiente = Decimal("0.00")
    deuda.motivo_anulacion = (motivo or "")[:255]
    deuda.anulada_por = actor
    deuda.anulada_en = now
    deuda.actualizado_por = actor
    deuda.save(
        update_fields=[
            "estado",
            "saldo_pendiente",
            "motivo_anulacion",
            "anulada_por",
            "anulada_en",
            "actualizado_por",
            "actualizado_en",
        ]
    )


@transaction.atomic
def annul_box(
    *,
    caja: Caja,
    motivo: str,
    actor=None,
) -> Caja:
    caja = _validate_box_for_full_correction(caja, actor=actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la eliminación es obligatorio."})

    # EP-13: la anulacion de la caja debe revertir TODO su impacto, incluidas
    # las deudas que origino. Con pagos registrados no se puede anular:
    # primero hay que resolver la deuda en tesoreria.
    deudas_activas = [
        deuda for deuda in caja.deudas_originadas.all()
        if deuda.estado != deuda.Estado.ANULADA
    ]
    for deuda in deudas_activas:
        if deuda.pagos.filter(estado="REGISTRADO").exists():
            raise ValidationError(
                {
                    "motivo": (
                        f"La deuda #{deuda.id} originada en esta caja tiene pagos registrados. "
                        "Anula o resolve esa deuda en tesoreria antes de eliminar la caja."
                    )
                }
            )

    previous = _snapshot_box_values(caja)
    now = timezone.now()
    _reverse_central_cash_closure_for_box(caja, actor=actor)
    for deuda in deudas_activas:
        _apply_debt_annulment(
            deuda,
            motivo=f"Anulacion de caja origen #{caja.id}: {motivo}",
            actor=actor,
            now=now,
        )
    MovimientoCaja.objects.filter(caja=caja, estado=MovimientoCaja.Estado.REGISTRADO).update(
        estado=MovimientoCaja.Estado.ANULADO,
        motivo_anulacion=motivo,
        anulado_por=actor,
        anulado_en=now,
        actualizado_por=actor,
        actualizado_en=now,
    )
    caja.estado = Caja.Estado.ANULADA
    if caja.cerrada_en is None:
        caja.cerrada_en = now
        caja.cerrada_por = actor
    caja.save(update_fields=["estado", "cerrada_en", "cerrada_por"])
    _create_box_correction(
        caja=caja,
        accion=CajaCorreccion.Accion.ANULACION,
        motivo=motivo,
        previous=previous,
        actor=actor,
    )
    AlertaOperativa.objects.filter(caja=caja, resuelta=False).update(resuelta=True)
    return caja


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
def annul_box_movement(
    *,
    movement: MovimientoCaja,
    motivo: str,
    actor=None,
) -> MovimientoCaja:
    """Elimina (anula con auditoria) un movimiento de caja, en cajas ABIERTAS o
    CERRADAS. Nada se borra fisico: queda estado=ANULADO con motivo + quien/cuando
    + fila en MovimientoCajaCorreccion. El saldo se revierte solo (saldo_esperado
    ignora lo ANULADO). En cajas cerradas ademas se recalcula el cierre; en abiertas
    solo se resincroniza el motor operativo. Gateado por el permiso de borrado."""
    movement = _validate_box_movement_for_deletion(movement, actor=actor)
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

    caja = movement.caja
    if caja.estado == Caja.Estado.CERRADA and hasattr(caja, "cierre"):
        _recalculate_closed_box_after_correction(caja, actor=actor, motivo=motivo)
    else:
        resync_operational_control_for_caja(caja)
    return movement


@transaction.atomic
def annul_box_originated_debt(
    *,
    deuda,
    motivo: str,
    actor=None,
):
    """Elimina (anula con auditoria) un 'gasto como deuda' cargado de mas desde la
    caja. Soft-delete de la CuentaPorPagar (estado ANULADA, saldo 0, motivo +
    quien/cuando). Se bloquea si la deuda ya tiene pagos registrados: primero hay
    que resolver esos pagos en tesoreria. Gateado por el permiso de borrado y por
    el aislamiento de caja/empresa que aplica la vista."""
    from treasury.models import CuentaPorPagar, PagoTesoreria

    _require_actor(actor)
    deuda = (
        # of=("self",): `caja_origen` es nullable, asi que su select_related
        # entra como LEFT OUTER JOIN y Postgres rechaza un FOR UPDATE que
        # abarque el lado nullable. La caja aca solo se lee (permiso y estado);
        # lo unico que se modifica es la deuda, asi que alcanza con bloquearla.
        # SQLite ignora FOR UPDATE, por eso esto no se ve en los tests locales.
        CuentaPorPagar.objects.select_for_update(of=("self",))
        .select_related("caja_origen", "caja_origen__sucursal", "caja_origen__turno", "caja_origen__usuario")
        .get(pk=deuda.pk)
    )
    caja = deuda.caja_origen
    if caja is None:
        raise ValidationError({"deuda": "La deuda no esta asociada a una caja."})
    ensure_delete_movement_in_box(actor, caja)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la eliminación es obligatorio."})
    if deuda.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"deuda": "La deuda ya fue anulada."})
    if deuda.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        raise ValidationError(
            {"motivo": "La deuda tiene pagos registrados. Anulá o resolvé esos pagos en tesorería antes de eliminarla."}
        )
    _apply_debt_annulment(deuda, motivo=motivo, actor=actor, now=timezone.now())
    return deuda


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

    # Una caja devuelta por rechazo de validacion conserva su CierreCaja
    # (registro auditable: no se borra). Al volver a cerrarla, ese cierre se
    # actualiza con los numeros nuevos; el detalle de cada intento anterior
    # queda en CajaValidacion (efectivo declarado + motivo del rechazo).
    estado_cierre = CierreCaja.Estado.JUSTIFICADO if abs_difference > CLOSING_DIFF_THRESHOLD else CierreCaja.Estado.AUTO
    cierre = CierreCaja.objects.select_for_update().filter(caja=caja).first()
    if cierre is not None:
        cierre.saldo_esperado = saldo_esperado
        cierre.saldo_fisico = saldo_fisico
        cierre.diferencia = diferencia
        cierre.estado = estado_cierre
        cierre.ajuste_movimiento = ajuste_movimiento
        cierre.cerrado_por = actor
        # auto_now_add solo aplica en el insert: en el re-cierre la fecha se
        # actualiza a mano para que el cierre refleje ESTE cierre, no el rechazado.
        cierre.cerrado_en = timezone.now()
        cierre.save(update_fields=[
            "saldo_esperado", "saldo_fisico", "diferencia", "estado",
            "ajuste_movimiento", "cerrado_por", "cerrado_en",
        ])
    else:
        cierre = CierreCaja.objects.create(
            caja=caja,
            saldo_esperado=saldo_esperado,
            saldo_fisico=saldo_fisico,
            diferencia=diferencia,
            estado=estado_cierre,
            ajuste_movimiento=ajuste_movimiento,
            cerrado_por=actor,
        )

    if abs_difference > CLOSING_DIFF_THRESHOLD and justificacion.strip():
        # update_or_create: el re-cierre de una caja devuelta puede volver a dar
        # diferencia grave y la Justificacion es OneToOne con el cierre.
        Justificacion.objects.update_or_create(
            cierre=cierre,
            defaults={"motivo": justificacion.strip(), "creado_por": actor},
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
            mensaje=f"Diferencia grave detectada en caja {caja.id}: {diferencia}.",
            resuelta=False,
        )

    caja.estado = Caja.Estado.CERRADA
    caja.cerrada_en = timezone.now()
    caja.cerrada_por = actor
    # EP-13: toda caja que involucro efectivo (movimientos, monto inicial o
    # saldo fisico declarado) queda pendiente de validacion y no contabiliza
    # en ningun total hasta que un usuario con permiso la valide.
    requires_validation = (
        saldo_fisico != 0
        or caja.monto_inicial > 0
        or caja.movimientos.filter(
            estado=MovimientoCaja.Estado.REGISTRADO,
            impacta_saldo_caja=True,
        ).exists()
    )
    caja.validacion_estado = (
        Caja.ValidacionEstado.PENDIENTE if requires_validation else Caja.ValidacionEstado.NO_REQUERIDA
    )
    caja.save(update_fields=["estado", "cerrada_en", "cerrada_por", "validacion_estado"])
    caja_ref.estado = caja.estado
    caja_ref.cerrada_en = caja.cerrada_en
    caja_ref.cerrada_por = caja.cerrada_por
    caja_ref.validacion_estado = caja.validacion_estado

    # EP-13: el efectivo llega a la caja central de tesoreria recien al
    # validarse la caja. Una caja sin efectivo no tiene nada que empujar.
    if not requires_validation:
        _push_box_closure_to_central_cash(caja, saldo_fisico=saldo_fisico, actor=actor)

    # Al quedar pendiente, la caja sale de los totales de sucursal/global:
    # resincronizar los snapshots y alertas para que no queden calculados con
    # una caja que ya no contabiliza.
    resync_operational_control_for_caja(caja)

    return cierre


def _push_box_closure_to_central_cash(caja: Caja, *, saldo_fisico: Decimal, actor) -> None:
    """Registra el saldo fisico del cierre en la boveda de la empresa.

    Idempotente por vinculo estructural (caja_cierre): un movimiento manual
    de tesoreria con el mismo texto de concepto no puede suprimir el push.

    Antes esto buscaba una caja central de la sucursal y, si no existia, la
    creaba al vuelo. Asi nacieron 6 cajas en produccion, una por sucursal, con
    toda la recaudacion adentro, mientras los egresos salian de otra caja: la
    plata estaba bien pero ninguna pantalla lo mostraba. Ahora el efectivo entra
    a la boveda de la empresa y la sucursal queda registrada en el movimiento
    (`sucursal_origen`), que es lo que sostiene la contabilidad por local.
    """
    if saldo_fisico == 0 or not caja.sucursal_id:
        return
    from django.apps import apps
    from django.core.exceptions import ValidationError

    from treasury.services import get_boveda

    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    # El guard filtra estado: si el ingreso de un cierre se anulo, este push
    # tiene que poder volver a empujar el efectivo. Sin el filtro, el guard veia
    # el movimiento anulado y la revalidacion no reponia la plata.
    if MovimientoCajaCentral.objects.filter(
        caja_cierre=caja,
        tipo__in=["INGRESO_CAJA", "AJUSTE_NEGATIVO"],
        estado="REGISTRADO",
    ).exists():
        return
    if not caja.sucursal.empresa_id:
        raise ValidationError(
            {
                "sucursal": (
                    f"La sucursal {caja.sucursal.nombre} no tiene empresa asignada, "
                    "asi que no se sabe a que boveda mandar el efectivo."
                )
            }
        )
    # Un solo resolvedor de boveda en todo el sistema. Que existan dos era
    # exactamente el bug: cashops mandaba el efectivo a una caja por sucursal y
    # treasury sacaba los egresos de otra caja global.
    caja_central = get_boveda(caja.sucursal.empresa_id)
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
    # Si el mes de la fecha operativa ya esta cerrado en tesoreria, el
    # movimiento se fecha al dia de la validacion: el snapshot mensual
    # congelado es inmutable y un movimiento retro-fechado desapareceria de
    # la cadena de disponibilidades.
    movement_date = caja.fecha_operativa
    month_start = caja.fecha_operativa.replace(day=1)
    if CierreMensualTesoreria.objects.filter(mes=month_start, cerrado=True).exists():
        movement_date = timezone.localdate()
        nota = (
            f"Efectivo del cierre de caja del {caja.fecha_operativa:%d/%m/%Y} "
            "validado con el mes de tesoreria ya cerrado."
        )
        central_observations = f"{central_observations} {nota}".strip()
    MovimientoCajaCentral.objects.create(
        caja_central=caja_central,
        fecha=movement_date,
        tipo=central_type,
        monto=central_amount,
        concepto=central_concept,
        observaciones=central_observations,
        caja_cierre=caja,
        # La sucursal viaja en el movimiento, no en la boveda: es de donde
        # salio este efectivo y es lo que permite el traqueo por local.
        sucursal_origen_id=caja.sucursal_id,
        creado_por=actor,
    )


@transaction.atomic
def validate_box_cash(*, caja: Caja, actor=None) -> Caja:
    """EP-13: valida el efectivo de una caja cerrada y la vuelve contable.

    Solo un usuario con el permiso de accion validar efectivo puede hacerlo.
    Al validar, el saldo fisico del cierre se empuja a la caja central y la
    caja vuelve a aportar a todos los totales del sistema.
    """
    _require_actor(actor)
    ensure_cash_validation(actor)
    caja = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").get(pk=caja.pk)
    if caja.estado != Caja.Estado.CERRADA:
        raise ValidationError({"caja": "Solo se puede validar el efectivo de una caja cerrada."})
    if caja.validacion_estado not in Caja.VALIDACION_BLOQUEA_TOTALES:
        raise ValidationError({"caja": "La caja no esta pendiente de validacion."})
    cierre = getattr(caja, "cierre", None)
    efectivo_esperado = cierre.saldo_fisico if cierre is not None else Decimal("0.00")
    caja.validacion_estado = Caja.ValidacionEstado.VALIDADA
    caja.validada_por = actor
    caja.validada_en = timezone.now()
    caja.save(update_fields=["validacion_estado", "validada_por", "validada_en"])
    CajaValidacion.objects.create(
        caja=caja,
        accion=CajaValidacion.Accion.VALIDACION,
        efectivo_esperado=efectivo_esperado,
        usuario=actor,
    )
    if cierre is not None:
        _push_box_closure_to_central_cash(caja, saldo_fisico=cierre.saldo_fisico, actor=actor)
    resync_operational_control_for_caja(caja)
    return caja


def _annul_closing_adjustment(cierre: CierreCaja, *, motivo: str, actor) -> None:
    """Anula (con auditoria, nada se borra) el AJUSTE_CIERRE de un cierre cuya
    caja vuelve a manos del cajero. El ajuste absorbia la diferencia del cierre
    rechazado; anulado, el saldo esperado de la caja reabierta vuelve al valor
    previo al cierre, porque la property saldo_esperado ignora lo ANULADO."""
    movimiento = cierre.ajuste_movimiento
    if movimiento is None or movimiento.estado != MovimientoCaja.Estado.REGISTRADO:
        return
    MovimientoCajaCorreccion.objects.create(
        movimiento=movimiento,
        accion=MovimientoCajaCorreccion.Accion.ANULACION,
        motivo=motivo,
        monto_anterior=movimiento.monto,
        monto_nuevo=None,
        categoria_anterior=movimiento.categoria,
        categoria_nueva=movimiento.categoria,
        observacion_anterior=movimiento.observacion,
        observacion_nueva=movimiento.observacion,
        rubro_operativo_anterior=movimiento.rubro_operativo,
        rubro_operativo_nuevo=movimiento.rubro_operativo,
        creado_por=actor,
    )
    movimiento.estado = MovimientoCaja.Estado.ANULADO
    movimiento.motivo_anulacion = motivo
    movimiento.anulado_por = actor
    movimiento.anulado_en = timezone.now()
    movimiento.actualizado_por = actor
    movimiento.full_clean()
    movimiento.save(update_fields=[
        "estado", "motivo_anulacion", "anulado_por", "anulado_en",
        "actualizado_por", "actualizado_en",
    ])


@transaction.atomic
def reject_box_cash(*, caja: Caja, motivo: str, actor=None) -> Caja:
    """EP-13: rechaza la validacion con motivo y DEVUELVE la caja al cajero.

    Antes el rechazo dejaba la caja cerrada en un callejon sin salida: el
    cajero no podia corregirla (corregir caja cerrada es otro permiso) ni
    volver a presentarla. Ahora rechazar tiene dos efectos: registra el motivo
    (auditado en CajaValidacion, visible para el cajero) y reabre la caja a
    nombre del mismo responsable para que corrija y vuelva a cerrarla. La caja
    queda ABIERTA + RECHAZADA: sigue fuera de todos los totales hasta que se
    cierre de nuevo y se valide.
    """
    _require_actor(actor)
    ensure_cash_validation(actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para rechazar la validacion."})
    caja = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").get(pk=caja.pk)
    if caja.estado != Caja.Estado.CERRADA:
        raise ValidationError({"caja": "Solo se puede rechazar la validacion de una caja cerrada."})
    if caja.validacion_estado not in Caja.VALIDACION_BLOQUEA_TOTALES:
        raise ValidationError({"caja": "La caja no esta pendiente de validacion."})
    if _treasury_month_is_closed_for_empresa(caja.fecha_operativa, caja.sucursal.empresa_id):
        raise ValidationError(
            {
                "caja": (
                    "El mes de tesoreria de esa caja ya esta cerrado: es una foto congelada "
                    "y no se puede devolver la caja para corregirla."
                )
            }
        )
    # El rechazo reabre la caja a nombre del mismo cajero: si ya abrio otra en
    # el mismo turno/sucursal/fecha, chocarian. Mejor un mensaje claro antes
    # que la constraint de unica caja abierta.
    if Caja.objects.filter(
        estado=Caja.Estado.ABIERTA,
        usuario=caja.usuario,
        turno=caja.turno,
        sucursal=caja.sucursal,
        fecha_operativa=caja.fecha_operativa,
    ).exists():
        raise ValidationError(
            {
                "caja": (
                    f"{caja.usuario} ya tiene otra caja abierta para ese turno, sucursal y fecha, "
                    "y el rechazo le devuelve esta caja abierta. Resolvé esa caja primero."
                )
            }
        )
    cierre = getattr(caja, "cierre", None)
    caja.validacion_estado = Caja.ValidacionEstado.RECHAZADA
    caja.save(update_fields=["validacion_estado"])
    CajaValidacion.objects.create(
        caja=caja,
        accion=CajaValidacion.Accion.RECHAZO,
        motivo=motivo,
        efectivo_esperado=cierre.saldo_fisico if cierre is not None else Decimal("0.00"),
        usuario=actor,
    )

    # --- Devolucion de la caja al cajero ------------------------------------
    if cierre is not None:
        # Sin esto, el ajuste del cierre rechazado seguiria REGISTRADO y el
        # saldo esperado de la caja reabierta arrancaria distorsionado.
        _annul_closing_adjustment(cierre, motivo=f"Rechazo de validación: {motivo}", actor=actor)
        # La alerta de diferencia grave describia un cierre que ya no rige; si
        # el re-cierre vuelve a dar grave, el upsert por cierre la reactiva.
        AlertaOperativa.objects.filter(
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            cierre=cierre,
            resuelta=False,
        ).update(resuelta=True)
    caja.estado = Caja.Estado.ABIERTA
    caja.cerrada_en = None
    caja.cerrada_por = None
    caja.save(update_fields=["estado", "cerrada_en", "cerrada_por"])
    resync_operational_control_for_caja(caja)
    # Punto de extension del modulo de avisos: cuando exista, el aviso al
    # cajero ("Validacion de efectivo rechazada" + motivo textual) nace aca,
    # con el rechazo confirmado y la caja ya devuelta, en esta transaccion.
    return caja


@transaction.atomic
def revert_box_cash_validation(*, caja: Caja, motivo: str, actor=None) -> Caja:
    """Deshace la validacion del efectivo de una caja (EP-13 al reves).

    Validar empuja el saldo fisico del cierre a la boveda de la empresa. Si la
    validacion estuvo mal (se conto mal, se valido otra caja), la unica salida
    era eliminar la caja entera. Revertir anula ese ingreso en la boveda
    (auditado, nada se borra) y devuelve la caja a Pendiente de validacion:
    desde ahi se puede corregir y revalidar, o eliminar la caja, en cualquier
    orden y sin que la plata se mueva dos veces. El guard del push filtra por
    estado REGISTRADO, asi que una revalidacion posterior vuelve a empujar el
    monto vigente una sola vez; y la anulacion de caja tambien filtra por
    REGISTRADO, asi que eliminar despues de revertir no descuenta de nuevo.
    """
    from django.apps import apps

    _require_actor(actor)
    ensure_cash_validation_undo(actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para revertir una validacion."})
    caja = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").get(pk=caja.pk)
    if caja.estado != Caja.Estado.CERRADA or caja.validacion_estado != Caja.ValidacionEstado.VALIDADA:
        raise ValidationError({"caja": "Solo se puede revertir una caja con el efectivo ya validado."})
    if _treasury_month_is_closed_for_empresa(caja.fecha_operativa, caja.sucursal.empresa_id):
        raise ValidationError(
            {
                "caja": (
                    "El mes de tesoreria de esa caja ya esta cerrado: es una foto congelada "
                    "y la plata de esa validacion quedo dentro del mes. No se puede revertir."
                )
            }
        )

    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
    empujes = list(
        MovimientoCajaCentral.objects.select_for_update(of=("self",)).filter(
            caja_cierre=caja,
            tipo__in=["INGRESO_CAJA", "AJUSTE_NEGATIVO"],
            estado="REGISTRADO",
        )
    )

    # La boveda no puede quedar en negativo en silencio: si el efectivo de este
    # cierre ya se uso (pagos, depositos), se corta aca con un mensaje claro.
    delta = Decimal("0.00")
    boveda = None
    for movimiento in empujes:
        boveda = movimiento.caja_central
        if movimiento.tipo == "INGRESO_CAJA":
            delta -= movimiento.monto
        else:
            delta += movimiento.monto
    if boveda is not None and delta < 0 and boveda.saldo_actual + delta < 0:
        raise ValidationError(
            {
                "caja": (
                    "Ese efectivo ya se uso: revertir la validacion dejaria la boveda "
                    f"{boveda.nombre} con saldo negativo. Revisa los movimientos de la "
                    "boveda antes de revertir esta caja."
                )
            }
        )

    now = timezone.now()
    for movimiento in empujes:
        movimiento.estado = "ANULADO"
        movimiento.motivo_anulacion = f"Reversión de validación caja #{caja.pk}: {motivo}"
        movimiento.anulado_por = actor
        movimiento.anulado_en = now
        movimiento.full_clean()
        movimiento.save(update_fields=["estado", "motivo_anulacion", "anulado_por", "anulado_en"])

    cierre = getattr(caja, "cierre", None)
    caja.validacion_estado = Caja.ValidacionEstado.PENDIENTE
    caja.validada_por = None
    caja.validada_en = None
    caja.save(update_fields=["validacion_estado", "validada_por", "validada_en"])
    # La validacion original queda en la bitacora; la reversion es un evento mas.
    CajaValidacion.objects.create(
        caja=caja,
        accion=CajaValidacion.Accion.REVERSION,
        motivo=motivo,
        efectivo_esperado=cierre.saldo_fisico if cierre is not None else Decimal("0.00"),
        usuario=actor,
    )
    resync_operational_control_for_caja(caja)
    return caja


@transaction.atomic
def update_declared_closing_cash(*, caja: Caja, saldo_fisico: Decimal, justificacion: str = "", motivo: str, actor=None) -> CierreCaja:
    """Corrige el efectivo fisico declarado al cerrar una caja (US-03).

    Es para el error de conteo: los movimientos estan bien, lo que esta mal es
    el numero que se declaro al cerrar. Solo sobre cajas cerradas PENDIENTES o
    RECHAZADAS: nunca sobre una validada, porque ese declarado ya se empujo a
    la boveda (para eso esta revert_box_cash_validation). Rehace la matematica
    del cierre igual que close_box: anula el ajuste de cierre viejo (auditado),
    recalcula la diferencia contra el esperado vigente y genera el ajuste o la
    justificacion que corresponda. No mueve plata en tesoreria: el push llega
    recien al validar, y va a llevar el monto corregido.
    """
    _require_actor(actor)
    ensure_closed_box_correction(actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de la corrección es obligatorio."})
    caja = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").get(pk=caja.pk)
    if caja.estado != Caja.Estado.CERRADA:
        raise ValidationError({"caja": "Solo se puede corregir el efectivo declarado de una caja cerrada."})
    if caja.validacion_estado == Caja.ValidacionEstado.VALIDADA:
        raise ValidationError(
            {"caja": "La caja ya tiene el efectivo validado. Primero hay que revertir la validación."}
        )
    if caja.validacion_estado not in Caja.VALIDACION_BLOQUEA_TOTALES:
        raise ValidationError({"caja": "La caja no está pendiente de validación."})
    cierre = CierreCaja.objects.select_for_update().filter(caja=caja).first()
    if cierre is None:
        raise ValidationError({"caja": "La caja no tiene un cierre para corregir."})

    declarado_anterior = cierre.saldo_fisico

    # Misma matematica que close_box: primero se anula el ajuste viejo (que
    # absorbia la diferencia del declarado anterior), despues se recalcula.
    _annul_closing_adjustment(cierre, motivo=f"Corrección del efectivo declarado: {motivo}", actor=actor)

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
            observacion="Ajuste de cierre automatico (declarado corregido)",
            creado_por=actor,
        )

    cierre.saldo_esperado = saldo_esperado
    cierre.saldo_fisico = saldo_fisico
    cierre.diferencia = diferencia
    cierre.estado = (
        CierreCaja.Estado.JUSTIFICADO if abs_difference > CLOSING_DIFF_THRESHOLD else CierreCaja.Estado.AUTO
    )
    cierre.ajuste_movimiento = ajuste_movimiento
    cierre.save(update_fields=["saldo_esperado", "saldo_fisico", "diferencia", "estado", "ajuste_movimiento"])

    if abs_difference > CLOSING_DIFF_THRESHOLD and justificacion.strip():
        Justificacion.objects.update_or_create(
            cierre=cierre,
            defaults={"motivo": justificacion.strip(), "creado_por": actor},
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
            mensaje=f"Diferencia grave detectada en caja {caja.id}: {diferencia}.",
            resuelta=False,
        )
    else:
        AlertaOperativa.objects.filter(
            tipo=AlertaOperativa.Tipo.DIFERENCIA_GRAVE,
            cierre=cierre,
            resuelta=False,
        ).update(resuelta=True)

    # Bitacora: el valor anterior y el nuevo quedan a la vista de quien valida.
    CajaValidacion.objects.create(
        caja=caja,
        accion=CajaValidacion.Accion.CORRECCION,
        motivo=f"Efectivo declarado corregido de ${declarado_anterior} a ${saldo_fisico}. Motivo: {motivo}",
        efectivo_esperado=saldo_fisico,
        usuario=actor,
    )
    resync_operational_control_for_caja(caja)
    return cierre
