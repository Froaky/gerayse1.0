import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from .forms import (
    CajaAperturaForm,
    CanalIngresoForm,
    ClosedBoxMovementAnnulForm,
    ClosedBoxMovementEditForm,
    CierreCajaForm,
    EmpresaForm,
    IngresoEfectivoForm,
    GastoRapidoForm,
    LimiteRubroOperativoForm,
    RubroOperativoForm,
    SucursalForm,
    TurnoForm,
    TransferenciaEntreCajasForm,
    TransferenciaEntreSucursalesForm,
    VentaGeneralForm,
)
from .models import CanalIngreso, Caja, CierreCaja, Empresa, LimiteRubroOperativo, MovimientoCaja, MovimientoCajaCorreccion, RubroOperativo, Sucursal, Turno
from .permissions import can_correct_closed_box, ensure_cashops_read, ensure_cashops_write, ensure_closed_box_correction, ensure_config_read, ensure_config_write
from .services import (
    annul_closed_box_movement,
    BRANCH_TRANSFER_DISABLED_REASON,
    CLOSING_DIFF_THRESHOLD,
    OPERATIONAL_ALERT_SCOPE_POLICY,
    OPERATIONAL_ALERT_SCOPE_POLICY_RULES,
    build_box_activity_timeline,
    build_alert_panel_queryset,
    build_box_control_scope,
    build_branch_control_scope,
    build_global_control_scope,
    build_management_daily_matrix,
    build_operational_control_snapshot,
    build_operational_period_summary,
    build_box_sales_breakdown,
    close_box,
    describe_box_follow_up,
    get_cash_movement_type_label,
    get_income_channel_map,
    is_closed_box_movement_correctable,
    open_box,
    register_cash_income,
    register_general_sale,
    register_expense,
    resync_operational_control_for_rubro,
    transfer_between_boxes,
    transfer_between_branches,
    update_closed_box_movement,
)
def _boxes_for_request(request):
    queryset = Caja.objects.select_related("sucursal", "turno", "usuario")
    if request.user.is_authenticated and request.user.is_cashops_admin():
        empresa_ids = _get_empresa_ids(request)
        if empresa_ids:
            return queryset.filter(sucursal__empresa_id__in=empresa_ids)
        return queryset
    return queryset.filter(usuario=request.user)


def _owned_open_boxes(request):
    queryset = Caja.objects.select_related("turno", "sucursal", "usuario").filter(estado=Caja.Estado.ABIERTA)
    if request.user.is_authenticated and request.user.is_cashops_admin():
        empresa_ids = _get_empresa_ids(request)
        if empresa_ids:
            return queryset.filter(sucursal__empresa_id__in=empresa_ids)
        return queryset
    return queryset.filter(usuario=request.user)


def _box_movements_prefetch():
    return Prefetch(
        "movimientos",
        queryset=MovimientoCaja.objects.select_related("creado_por", "rubro_operativo", "transferencia").prefetch_related(
            Prefetch(
                "correcciones",
                queryset=MovimientoCajaCorreccion.objects.select_related("creado_por").order_by("-creado_en", "-id"),
                to_attr="prefetched_corrections",
            )
        ).order_by("-creado_en", "-id"),
        to_attr="prefetched_movements",
    )


def _get_box_for_request(request, box_id: int):
    box = get_object_or_404(Caja.objects.select_related("sucursal", "turno", "usuario"), pk=box_id)
    if request.user.is_authenticated and request.user.is_cashops_admin():
        empresa_ids = _get_empresa_ids(request)
        if empresa_ids and box.sucursal.empresa_id not in empresa_ids:
            raise PermissionDenied("Esta caja no pertenece a las empresas seleccionadas.")
        return box
    if box.usuario_id != request.user.id:
        raise PermissionDenied("No tenes permiso para operar esta caja.")
    return box


def _require_cashops_admin(request) -> None:
    ensure_config_write(request.user)


def _require_config_read(request) -> None:
    ensure_config_read(request.user)


def _require_config_write(request) -> None:
    ensure_config_write(request.user)


def _require_cashops_read(request) -> None:
    ensure_cashops_read(request.user)


def _require_cashops_write(request) -> None:
    ensure_cashops_write(request.user)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _render_form(request, full_template: str, partial_template: str, context: dict, status: int = 200):
    template = partial_template if _is_htmx(request) else full_template
    return render(request, template, context, status=status)


def _get_empresa_ids(request):
    """Retorna la lista de empresa IDs seleccionados en sesión (multi-empresa)."""
    ids = request.session.get("empresa_ids")
    if ids is not None:
        return ids
    # Compatibilidad con la clave vieja
    old_id = request.session.get("empresa_activa_id")
    if old_id:
        return [old_id]
    return []


def _get_empresa_activa(request):
    """Retorna la única empresa activa si hay exactamente una seleccionada; None si hay 0 o 2+."""
    ids = _get_empresa_ids(request)
    if len(ids) == 1:
        try:
            return Empresa.objects.get(pk=ids[0], activa=True)
        except Empresa.DoesNotExist:
            return None
    return None


def _sucursales_for_empresa(request):
    """Queryset de sucursales filtrado por las empresas seleccionadas en sesión."""
    qs = Sucursal.objects.all()
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids:
        qs = qs.filter(empresa_id__in=empresa_ids)
    return qs


def _sucursales_for_dashboard(request):
    return _sucursales_for_empresa(request)


def _apply_validation_error(form, error: ValidationError) -> None:
    if hasattr(error, "message_dict"):
        for field, messages_list in error.message_dict.items():
            target = field if field in form.fields else None
            for message in messages_list:
                form.add_error(target, message)
        return
    for message in error.messages:
        form.add_error(None, message)


def _handle_operation_error(form, error: Exception, fallback_message: str) -> None:
    if isinstance(error, ValidationError):
        _apply_validation_error(form, error)
    else:
        form.add_error(None, fallback_message)


def _build_close_preview_context(box: Caja, raw_amount: str | None = None):
    saldo_fisico = None
    diferencia = None
    requires_justification = False
    invalid_amount = False
    has_preview = False

    if raw_amount not in (None, ""):
        try:
            saldo_fisico = Decimal(raw_amount)
            diferencia = saldo_fisico - box.saldo_esperado
            requires_justification = abs(diferencia) > CLOSING_DIFF_THRESHOLD
            has_preview = True
        except (InvalidOperation, TypeError):
            invalid_amount = True

    return {
        "box": box,
        "saldo_esperado": box.saldo_esperado,
        "saldo_fisico": saldo_fisico,
        "diferencia": diferencia,
        "requires_justification": requires_justification,
        "closing_diff_threshold": CLOSING_DIFF_THRESHOLD,
        "invalid_amount": invalid_amount,
        "has_preview": has_preview,
    }


def _parse_dashboard_date(request):
    parsed = parse_date(request.GET.get("fecha") or "")
    return parsed or timezone.localdate()


def _parse_dashboard_period(request):
    default_date = _parse_dashboard_date(request)
    period_from = parse_date(request.GET.get("fecha_desde") or "") or default_date
    period_to = parse_date(request.GET.get("fecha_hasta") or "") or period_from
    if period_to < period_from:
        period_from, period_to = period_to, period_from
    return period_from, period_to


def _default_month_range():
    today = timezone.localdate()
    first_day = today.replace(day=1)
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    return first_day, next_month - timezone.timedelta(days=1)


def _parse_management_matrix_filters(request, empresa_ids=None):
    default_from, default_to = _default_month_range()
    date_from = parse_date(request.GET.get("fecha_desde") or "") or default_from
    date_to = parse_date(request.GET.get("fecha_hasta") or "") or default_to
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    sucursal = None
    sucursal_id = request.GET.get("sucursal")
    if sucursal_id:
        try:
            sucursal_qs = Sucursal.objects.filter(activa=True)
            if empresa_ids:
                sucursal_qs = sucursal_qs.filter(empresa_id__in=empresa_ids)
            sucursal = sucursal_qs.get(pk=int(sucursal_id))
        except (Sucursal.DoesNotExist, TypeError, ValueError):
            sucursal = None
    return date_from, date_to, sucursal


def _resolve_dashboard_scope(request):
    is_admin = request.user.is_authenticated and request.user.is_cashops_admin()
    requested_scope = (request.GET.get("scope") or ("global" if is_admin else "")).lower()
    selected_box = None
    selected_branch = None
    snapshot = None
    scope_date = _parse_dashboard_date(request)
    has_explicit_dates = bool(request.GET.get("fecha_desde") or request.GET.get("fecha_hasta") or request.GET.get("fecha"))
    period_from, period_to = _parse_dashboard_period(request)

    empresa_ids = _get_empresa_ids(request)

    if requested_scope == "box":
        box_id = request.GET.get("box")
        if box_id:
            try:
                selected_box = _get_box_for_request(request, int(box_id))
            except (TypeError, ValueError):
                selected_box = None
        if selected_box is None:
            requested_scope = "global" if is_admin else ""
        else:
            scope_date = selected_box.fecha_operativa
            snapshot = build_operational_control_snapshot(
                build_box_control_scope(caja=selected_box),
                sync_alerts=True,
            )
    elif requested_scope == "branch" and is_admin:
        branch_id = request.GET.get("sucursal")
        if branch_id:
            try:
                selected_branch = Sucursal.objects.get(pk=int(branch_id), activa=True)
            except (Sucursal.DoesNotExist, TypeError, ValueError):
                selected_branch = None
        if selected_branch is None:
            requested_scope = "global"
        else:
            snapshot = build_operational_period_summary(
                date_from=period_from,
                date_to=period_to,
                sucursal=selected_branch,
            )
    elif requested_scope == "global" and is_admin:
        if not has_explicit_dates:
            period_from, period_to = _default_month_range()
        snapshot = build_operational_period_summary(
            date_from=period_from,
            date_to=period_to,
            empresa_ids=empresa_ids or None,
        )
        if snapshot and empresa_ids:
            empresas_filtradas = list(Empresa.objects.filter(pk__in=empresa_ids).order_by("nombre"))
            snapshot["scope_label"] = ", ".join(e.nombre for e in empresas_filtradas)
    else:
        requested_scope = "global" if is_admin else ""

    return {
        "scope_name": requested_scope,
        "scope_date": scope_date,
        "selected_box": selected_box,
        "selected_branch": selected_branch,
        "snapshot": snapshot,
        "is_admin": is_admin,
        "period_from": period_from,
        "period_to": period_to,
    }


@login_required
def dashboard(request):
    _require_cashops_read(request)
    is_admin = request.user.is_authenticated and request.user.is_cashops_admin()
    if not request.GET.get("scope") and not is_admin:
        open_boxes = Caja.objects.filter(usuario=request.user, estado=Caja.Estado.ABIERTA)
        if open_boxes.count() == 1:
            box = open_boxes.first()
            return redirect(f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}")

    boxes = _boxes_for_request(request).order_by("-abierta_en")
    scope_context = _resolve_dashboard_scope(request)
    selected_box = scope_context["selected_box"]
    dashboard_snapshot = scope_context["snapshot"]

    recent_movements = []
    selected_box_sales_breakdown = {"total": Decimal("0.00"), "groups": [], "movements": []}
    if selected_box is not None:
        recent_movements = (
            selected_box.movimientos.select_related("transferencia", "creado_por", "rubro_operativo")
            .order_by("-creado_en", "-id")[:20]
        )
        selected_box_sales_breakdown = build_box_sales_breakdown(
            selected_box.movimientos.select_related("creado_por", "rubro_operativo", "transferencia").order_by("-creado_en", "-id")
        )
        channel_map = get_income_channel_map()
        for movement in recent_movements:
            movement.tipo_label = get_cash_movement_type_label(movement.tipo, channel_map)

    is_admin = scope_context["is_admin"]

    context = {
        "selected_box": selected_box,
        "selected_branch": scope_context["selected_branch"],
        "open_boxes": boxes.filter(estado=Caja.Estado.ABIERTA),
        "recent_movements": recent_movements,
        "selected_box_sales_breakdown": selected_box_sales_breakdown,
        "turnos_disponibles": Turno.objects.select_related("empresa").all() if is_admin else [],
        "sucursales": _sucursales_for_dashboard(request).filter(activa=True) if is_admin else [],
        "dashboard_scope": scope_context["scope_name"],
        "dashboard_scope_date": scope_context["scope_date"],
        "dashboard_period_from": scope_context["period_from"],
        "dashboard_period_to": scope_context["period_to"],
        "dashboard_snapshot": dashboard_snapshot,
        "alertas": dashboard_snapshot["active_alerts"][:4] if dashboard_snapshot else [],
        "is_cashops_admin": is_admin,
    }
    return render(request, "cashops/dashboard.html", context)


@login_required
def box_tracking_view(request):
    _require_cashops_read(request)
    empresa_ids = _get_empresa_ids(request)
    sucursales = _sucursales_for_dashboard(request).filter(activa=True).order_by("nombre")
    selected_branch_ids = []
    for raw_id in request.GET.getlist("sucursal"):
        try:
            selected_branch_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    status_filter = (request.GET.get("estado") or "todas").lower()
    date_from = parse_date(request.GET.get("fecha_desde") or "")
    date_to = parse_date(request.GET.get("fecha_hasta") or "")

    boxes = (
        _boxes_for_request(request)
        .select_related("sucursal", "turno", "usuario", "cerrada_por")
        .prefetch_related(_box_movements_prefetch(), "cierre__justificacion")
        .order_by("-fecha_operativa", "-abierta_en", "-id")
    )
    if selected_branch_ids:
        boxes = boxes.filter(sucursal_id__in=selected_branch_ids)
    if date_from:
        boxes = boxes.filter(fecha_operativa__gte=date_from)
    if date_to:
        boxes = boxes.filter(fecha_operativa__lte=date_to)
    if status_filter == "abiertas":
        boxes = boxes.filter(estado=Caja.Estado.ABIERTA)
    elif status_filter == "cerradas":
        boxes = boxes.filter(estado=Caja.Estado.CERRADA)

    rows = []
    for box in boxes:
        movements = list(getattr(box, "prefetched_movements", []))
        sales_breakdown = build_box_sales_breakdown(movements)
        follow_up = describe_box_follow_up(box, movements)
        rows.append(
            {
                "box": box,
                "movements": movements,
                "sales_breakdown": sales_breakdown,
                "follow_up": follow_up,
                "resume_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
                "detail_url": reverse("cashops:box_detail", args=[box.pk]),
            }
        )

    return render(
        request,
        "cashops/box_tracking.html",
        {
            "title": "Seguimiento de cajas",
            "rows": rows,
            "sucursales": sucursales,
            "selected_branch_ids": selected_branch_ids,
            "status_filter": status_filter,
            "fecha_desde": request.GET.get("fecha_desde", ""),
            "fecha_hasta": request.GET.get("fecha_hasta", ""),
            "empresa_ids": empresa_ids,
        },
    )


@login_required
def box_detail_view(request, box_id: int):
    _require_cashops_read(request)
    box = _get_box_for_request(request, box_id)
    box = (
        Caja.objects.select_related("sucursal", "turno", "usuario", "cerrada_por")
        .prefetch_related(_box_movements_prefetch(), "cierre__justificacion")
        .get(pk=box.pk)
    )
    movements = list(getattr(box, "prefetched_movements", []))
    sales_breakdown = build_box_sales_breakdown(movements)
    follow_up = describe_box_follow_up(box, movements)
    timeline = build_box_activity_timeline(box, movements)
    latest_event = timeline[0] if timeline else None
    channel_map = get_income_channel_map()
    can_fix_closed_box = can_correct_closed_box(request.user)
    for movement in movements:
        movement.tipo_label = get_cash_movement_type_label(movement.tipo, channel_map)
        movement.can_fix_closed_box = can_fix_closed_box and is_closed_box_movement_correctable(movement)
        if movement.can_fix_closed_box:
            movement.edit_url = reverse("cashops:closed_box_movement_edit", args=[movement.pk])
            movement.delete_url = reverse("cashops:closed_box_movement_delete", args=[movement.pk])

    return render(
        request,
        "cashops/box_detail.html",
        {
            "box": box,
            "movements": movements,
            "sales_breakdown": sales_breakdown,
            "follow_up": follow_up,
            "timeline": timeline,
            "latest_event": latest_event,
            "can_correct_closed_box": can_fix_closed_box,
            "resume_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
            "tracking_url": reverse("cashops:box_tracking"),
        },
    )


def _get_correctable_movement_for_request(request, movement_id: int) -> MovimientoCaja:
    ensure_closed_box_correction(request.user)
    movement = get_object_or_404(
        MovimientoCaja.objects.select_related("caja", "caja__sucursal", "caja__turno", "caja__usuario", "rubro_operativo"),
        pk=movement_id,
    )
    _get_box_for_request(request, movement.caja_id)
    return movement


@login_required
@require_http_methods(["GET", "POST"])
def closed_box_movement_edit_view(request, movement_id: int):
    _require_cashops_read(request)
    movement = _get_correctable_movement_for_request(request, movement_id)
    form = ClosedBoxMovementEditForm(request.POST or None, movement=movement)
    back_url = reverse("cashops:box_detail", args=[movement.caja_id])
    if request.method == "POST" and form.is_valid():
        try:
            update_closed_box_movement(
                movement=movement,
                monto=form.cleaned_data["monto"],
                categoria=form.cleaned_data["categoria"],
                observacion=form.cleaned_data["observacion"],
                rubro_operativo=form.cleaned_data["rubro_operativo"],
                motivo=form.cleaned_data["motivo"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Movimiento corregido y cierre recalculado.")
            return redirect(back_url)
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": f"Editar movimiento #{movement.id}",
            "subtitle": "Seguro que queres editar este movimiento de una caja cerrada? Se guardara el motivo y se recalculara el cierre.",
            "form": form,
            "submit_label": "Confirmar edicion",
            "form_action": reverse("cashops:closed_box_movement_edit", args=[movement.id]),
            "back_url": back_url,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def closed_box_movement_delete_view(request, movement_id: int):
    _require_cashops_read(request)
    movement = _get_correctable_movement_for_request(request, movement_id)
    form = ClosedBoxMovementAnnulForm(request.POST or None)
    back_url = reverse("cashops:box_detail", args=[movement.caja_id])
    if request.method == "POST" and form.is_valid():
        try:
            annul_closed_box_movement(
                movement=movement,
                motivo=form.cleaned_data["motivo"],
                actor=request.user,
            )
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Movimiento anulado y cierre recalculado.")
            return redirect(back_url)
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": f"Eliminar movimiento #{movement.id}",
            "subtitle": "Seguro que queres eliminar este movimiento de una caja cerrada? No se borra el registro: queda anulado con auditoria.",
            "form": form,
            "submit_label": "Confirmar eliminacion",
            "form_action": reverse("cashops:closed_box_movement_delete", args=[movement.id]),
            "back_url": back_url,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
def alert_panel(request):
    _require_config_read(request)
    estado = (request.GET.get("estado") or "activas").lower()
    alcance = (request.GET.get("alcance") or "todos").lower()
    periodo_desde = parse_date(request.GET.get("periodo_desde") or request.GET.get("fecha_desde") or "")
    periodo_hasta = parse_date(request.GET.get("periodo_hasta") or request.GET.get("fecha_hasta") or "")
    rubro = None
    sucursal = None

    rubro_id = request.GET.get("rubro")
    if rubro_id:
        try:
            rubro = RubroOperativo.objects.get(pk=int(rubro_id))
        except (RubroOperativo.DoesNotExist, TypeError, ValueError):
            rubro = None
    sucursal_id = request.GET.get("sucursal")
    if sucursal_id:
        try:
            sucursal = Sucursal.objects.get(pk=int(sucursal_id))
        except (Sucursal.DoesNotExist, TypeError, ValueError):
            sucursal = None

    empresa_ids = _get_empresa_ids(request)
    alertas = build_alert_panel_queryset(
        estado=estado,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        rubro=rubro,
        sucursal=sucursal,
        alcance=alcance,
        empresa_ids=empresa_ids if empresa_ids else None,
    )
    sucursales_qs = Sucursal.objects.filter(activa=True)
    if empresa_ids:
        sucursales_qs = sucursales_qs.filter(empresa_id__in=empresa_ids)
    return render(
        request,
        "cashops/alert_panel.html",
        {
            "alertas": alertas,
            "estado_actual": estado,
            "alcance_actual": alcance,
            "periodo_desde": periodo_desde.isoformat() if periodo_desde else "",
            "periodo_hasta": periodo_hasta.isoformat() if periodo_hasta else "",
            "rubro_actual": rubro.pk if rubro else "",
            "sucursal_actual": sucursal.pk if sucursal else "",
            "rubros": RubroOperativo.objects.order_by("nombre"),
            "sucursales": sucursales_qs.order_by("nombre"),
            "scope_policy": OPERATIONAL_ALERT_SCOPE_POLICY,
            "scope_policy_rules": OPERATIONAL_ALERT_SCOPE_POLICY_RULES,
        },
    )


@login_required
def management_matrix(request):
    _require_config_read(request)
    empresa_ids = _get_empresa_ids(request)
    date_from, date_to, sucursal = _parse_management_matrix_filters(request, empresa_ids=empresa_ids)
    matrix = build_management_daily_matrix(
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids if empresa_ids else None,
    )
    sucursales_qs = Sucursal.objects.filter(activa=True)
    if empresa_ids:
        sucursales_qs = sucursales_qs.filter(empresa_id__in=empresa_ids)
    return render(
        request,
        "cashops/management_matrix.html",
        {
            "matrix": matrix,
            "sucursales": sucursales_qs.order_by("nombre"),
            "selected_sucursal": sucursal,
            "fecha_desde": date_from.isoformat(),
            "fecha_hasta": date_to.isoformat(),
        },
    )


@login_required
def management_matrix_export(request):
    _require_config_read(request)
    empresa_ids = _get_empresa_ids(request)
    date_from, date_to, sucursal = _parse_management_matrix_filters(request, empresa_ids=empresa_ids)
    matrix = build_management_daily_matrix(
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids if empresa_ids else None,
    )
    filename_scope = sucursal.codigo if sucursal else "global"
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="matriz-control-{filename_scope}-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["Matriz diaria de control"])
    writer.writerow(["Desde", date_from.isoformat(), "Hasta", date_to.isoformat(), "Sucursal", sucursal.nombre if sucursal else "Global"])
    writer.writerow([])
    writer.writerow(["Resumen diario"])
    writer.writerow(
        ["Fecha"]
        + [channel["label"] for channel in matrix["channels"]]
        + [f"Egreso {rubro['nombre']}" for rubro in matrix["rubros"]]
        + ["Facturacion separada", "Ingresos", "Egresos", "Resultado"]
    )
    for day in matrix["days"]:
        writer.writerow(
            [day["date"].isoformat()]
            + [day["income_by_channel"][channel["key"]] for channel in matrix["channels"]]
            + [day["expense_by_rubro"][rubro["id"]] for rubro in matrix["rubros"]]
            + [day["total_excluded_income"], day["total_income"], day["total_expense"], day["net_result"]]
        )
    writer.writerow([])
    writer.writerow(["Detalle trazable"])
    writer.writerow(["ID", "Fecha operativa", "Sucursal", "Caja", "Tipo", "Sentido", "Rubro", "Monto", "Categoria", "Observacion", "Usuario"])
    for movement in matrix["detail_movements"]:
        writer.writerow(
            [
                movement.pk,
                movement.caja.fecha_operativa.isoformat(),
                movement.caja.sucursal.nombre,
                movement.caja_id,
                movement.get_tipo_display() if hasattr(movement, "get_tipo_display") else movement.tipo,
                movement.get_sentido_display(),
                movement.rubro_operativo.nombre if movement.rubro_operativo_id else "",
                movement.monto,
                movement.categoria,
                movement.observacion,
                str(movement.creado_por) if movement.creado_por else "",
            ]
        )
    return response


@login_required
def operational_category_list(request):
    _require_config_read(request)
    categories = RubroOperativo.objects.annotate(limit_count=Count("limites")).order_by("nombre")
    return render(
        request,
        "cashops/operational_category_list.html",
        {
            "categories": categories,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def operational_category_create(request):
    _require_config_write(request)
    form = RubroOperativoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            category = form.save()
        except IntegrityError as error:
            _handle_operation_error(form, error, "No se pudo guardar el rubro.")
        else:
            messages.success(request, f"Rubro {category.nombre} guardado.")
            url = reverse("cashops:operational_category_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo rubro operativo",
            "subtitle": "Clasificacion principal para gastos operativos.",
            "form": form,
            "submit_label": "Guardar rubro",
            "back_url": reverse("cashops:operational_category_list"),
            "form_action": reverse("cashops:operational_category_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def operational_category_update(request, category_id: int):
    _require_config_write(request)
    category = get_object_or_404(RubroOperativo, pk=category_id)
    if category.es_sistema:
        raise PermissionDenied("El rubro de sistema no se puede editar desde esta pantalla.")
    form = RubroOperativoForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        try:
            category = form.save()
        except IntegrityError as error:
            _handle_operation_error(form, error, "No se pudo actualizar el rubro.")
        else:
            resync_operational_control_for_rubro(category)
            messages.success(request, f"Rubro {category.nombre} actualizado.")
            url = reverse("cashops:operational_category_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": f"Editar rubro: {category.nombre}",
            "subtitle": "Podes renombrar o desactivar el rubro.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("cashops:operational_category_list"),
            "form_action": reverse("cashops:operational_category_update", args=[category.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def operational_category_toggle(request, category_id: int):
    _require_config_write(request)
    category = get_object_or_404(RubroOperativo, pk=category_id)
    if category.es_sistema:
        raise PermissionDenied("El rubro de sistema no se puede activar ni desactivar manualmente.")
    category.activo = not category.activo
    category.save(update_fields=["activo", "actualizado_en"])
    resync_operational_control_for_rubro(category)
    messages.success(
        request,
        f"Rubro {category.nombre} {'activado' if category.activo else 'desactivado'}.",
    )
    url = reverse("cashops:operational_category_list")
    return _hx_redirect(url) if _is_htmx(request) else redirect(url)


@login_required
def operational_limit_list(request):
    _require_config_read(request)
    empresa_ids = _get_empresa_ids(request)
    limits = LimiteRubroOperativo.objects.select_related("rubro", "sucursal").order_by(
        "rubro__nombre",
        "sucursal__nombre",
        "id",
    )
    if empresa_ids:
        limits = limits.filter(
            Q(sucursal__empresa_id__in=empresa_ids) | Q(sucursal__isnull=True)
        )
    return render(
        request,
        "cashops/operational_limit_list.html",
        {
            "limits": limits,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def operational_limit_create(request):
    _require_config_write(request)
    form = LimiteRubroOperativoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            limit = form.save()
        except IntegrityError as error:
            _handle_operation_error(form, error, "No se pudo guardar el limite.")
        else:
            resync_operational_control_for_rubro(limit.rubro)
            messages.success(request, f"Limite guardado para {limit.rubro.nombre}.")
            url = reverse("cashops:operational_limit_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo limite por rubro",
            "subtitle": "Configura un porcentaje global o por sucursal.",
            "form": form,
            "submit_label": "Guardar limite",
            "back_url": reverse("cashops:operational_limit_list"),
            "form_action": reverse("cashops:operational_limit_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def operational_limit_update(request, limit_id: int):
    _require_config_write(request)
    limit = get_object_or_404(LimiteRubroOperativo.objects.select_related("rubro", "sucursal"), pk=limit_id)
    previous_rubro = limit.rubro
    form = LimiteRubroOperativoForm(request.POST or None, instance=limit)
    if request.method == "POST" and form.is_valid():
        try:
            limit = form.save()
        except IntegrityError as error:
            _handle_operation_error(form, error, "No se pudo actualizar el limite.")
        else:
            resync_operational_control_for_rubro(previous_rubro)
            if previous_rubro.pk != limit.rubro_id:
                resync_operational_control_for_rubro(limit.rubro)
            messages.success(request, f"Limite actualizado para {limit.rubro.nombre}.")
            url = reverse("cashops:operational_limit_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": f"Editar limite: {limit.rubro.nombre}",
            "subtitle": "Ajusta el porcentaje maximo global o por sucursal.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("cashops:operational_limit_list"),
            "form_action": reverse("cashops:operational_limit_update", args=[limit.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def sucursal_create(request):
    _require_config_write(request)
    form = SucursalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        sucursal = form.save()
        messages.success(request, f"Sucursal {sucursal.nombre} creada.")
        url = reverse("cashops:sucursal_list")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nueva sucursal",
            "subtitle": "Alta operativa con codigo, nombre y razon social.",
            "form": form,
            "submit_label": "Guardar sucursal",
            "back_url": reverse("cashops:sucursal_list"),
            "form_action": reverse("cashops:sucursal_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def sucursal_list(request):
    _require_config_read(request)
    empresa_ids = _get_empresa_ids(request)
    q = (request.GET.get("q") or "").strip()
    items = Sucursal.objects.all().order_by("nombre")
    if empresa_ids:
        items = items.filter(empresa_id__in=empresa_ids)
    if q:
        items = items.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(razon_social__icontains=q)
        )
    return render(
        request,
        "cashops/sucursal_list.html",
        {
            "title": "Sucursales",
            "create_url": reverse("cashops:sucursal_create"),
            "items": items,
            "query": q,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def sucursal_update(request, sucursal_id: int):
    _require_config_write(request)
    sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
    form = SucursalForm(request.POST or None, instance=sucursal)
    if request.method == "POST" and form.is_valid():
        sucursal = form.save()
        messages.success(request, f"Sucursal {sucursal.nombre} actualizada.")
        url = reverse("cashops:sucursal_list")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": f"Editar sucursal: {sucursal.nombre}",
            "subtitle": "Ajusta codigo, nombre, razon social y estado.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("cashops:sucursal_list"),
            "form_action": reverse("cashops:sucursal_update", args=[sucursal.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def sucursal_toggle(request, sucursal_id: int):
    _require_config_write(request)
    sucursal = get_object_or_404(Sucursal, pk=sucursal_id)
    sucursal.activa = not sucursal.activa
    sucursal.save(update_fields=["activa", "actualizada_en"])
    messages.success(
        request,
        f"Sucursal {sucursal.nombre} {'activada' if sucursal.activa else 'desactivada'}.",
    )
    url = reverse("cashops:sucursal_list")
    return _hx_redirect(url) if _is_htmx(request) else redirect(url)


@login_required
@require_http_methods(["GET", "POST"])
def turno_create(request):
    _require_config_write(request)
    form = TurnoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        turno = form.save(commit=False)
        if request.user.is_authenticated:
            turno.creado_por = request.user
        turno.save()
        messages.success(request, f"{turno.get_tipo_display()} configurado para {turno.empresa}.")
        url = reverse("cashops:turno_list")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo turno",
            "subtitle": "Configura Turno Mañana o Turno Tarde para la empresa. Aplica a todas sus sucursales.",
            "form": form,
            "submit_label": "Guardar turno",
            "back_url": reverse("cashops:turno_list"),
            "form_action": reverse("cashops:turno_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def turno_list(request):
    _require_config_read(request)
    empresa_ids = _get_empresa_ids(request)
    turnos = Turno.objects.select_related("empresa").all()
    if empresa_ids:
        turnos = turnos.filter(empresa_id__in=empresa_ids)
    return render(
        request,
        "cashops/list_page.html",
        {
            "title": "Turnos",
            "create_url": reverse("cashops:turno_create"),
            "items": turnos,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def open_box_view(request):
    _require_cashops_write(request)
    empresa_ids = _get_empresa_ids(request)
    form = CajaAperturaForm(request.POST or None, actor=request.user, empresa_ids=empresa_ids or None)
    if request.method == "POST" and form.is_valid():
        try:
            box = open_box(
                user=form.cleaned_data["usuario"],
                turno=form.cleaned_data["turno"],
                sucursal=form.cleaned_data["sucursal"],
                fecha_operativa=form.cleaned_data["fecha_operativa"],
                monto_inicial=form.cleaned_data["efectivo_inicial"],
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo abrir la caja.")
        else:
            turno = form.cleaned_data["turno"]
            sucursal = form.cleaned_data["sucursal"]
            fecha = form.cleaned_data["fecha_operativa"]
            messages.success(request, f"Caja abierta — {turno.get_tipo_display()} {fecha:%d/%m/%Y} en {sucursal.nombre}.")
            url = f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}"
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Abrir caja",
            "subtitle": "Selecciona el turno, sucursal y fecha para la apertura.",
            "form": form,
            "submit_label": "Abrir caja",
            "back_url": reverse("cashops:dashboard"),
            "form_action": reverse("cashops:box_open"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def register_expense_view(request, box_id: int):
    _require_cashops_write(request)
    box = _get_box_for_request(request, box_id)
    form = GastoRapidoForm(request.POST or None, sucursal=box.sucursal)
    if request.method == "POST" and form.is_valid():
        try:
            register_expense(
                caja=box,
                monto=form.cleaned_data["monto"],
                rubro_operativo=form.cleaned_data["rubro_operativo"],
                categoria=form.cleaned_data["rubro_operativo"].nombre,
                observacion=form.cleaned_data["observacion"],
                sucursal_destino=form.cleaned_data.get("sucursal_destino"),
                creado_por=request.user,
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar el egreso por rubro.")
        else:
            messages.success(request, "Egreso por rubro registrado.")
            url = f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}"
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Egreso por rubro",
            "subtitle": f"Caja activa: {box.id}. Registro operativo con rubro obligatorio.",
            "form": form,
            "submit_label": "Guardar egreso",
            "back_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
            "form_action": reverse("cashops:box_expense", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def register_sale_view(request, box_id: int):
    _require_cashops_write(request)
    box = _get_box_for_request(request, box_id)
    form = VentaGeneralForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            register_general_sale(
                caja=box,
                monto=form.cleaned_data["monto"],
                tipo_venta=form.cleaned_data["tipo_venta"],
                rubro=form.cleaned_data["rubro"],
                observacion=form.cleaned_data["observacion"],
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar la venta.")
        else:
            messages.success(request, "Ingreso operativo registrado con exito.")
            url = f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}"
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Registrar ingreso operativo",
            "subtitle": "Cobros y ventas operativas con rubro explicito.",
            "form": form,
            "submit_label": "Registrar ingreso",
            "back_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
            "form_action": reverse("cashops:register_sale", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def register_cash_income_view(request, box_id: int):
    _require_cashops_write(request)
    box = _get_box_for_request(request, box_id)
    form = IngresoEfectivoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            register_cash_income(
                caja=box,
                monto=form.cleaned_data["monto"],
                categoria=form.cleaned_data["categoria"],
                observacion=form.cleaned_data["observacion"],
                creado_por=request.user,
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar el ingreso en efectivo.")
        else:
            messages.success(request, "Ingreso en efectivo registrado.")
            url = f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}"
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Ingreso en efectivo",
            "subtitle": f"Caja activa: {box.id}",
            "form": form,
            "submit_label": "Guardar ingreso",
            "back_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
            "form_action": reverse("cashops:box_income", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def transfer_between_boxes_view(request):
    _require_cashops_write(request)
    form = TransferenciaEntreCajasForm(request.POST or None)
    open_boxes = _owned_open_boxes(request)
    form.fields["caja_origen"].queryset = _owned_open_boxes(request)
    form.fields["caja_destino"].queryset = open_boxes
    if request.method == "POST" and form.is_valid():
        try:
            transfer_between_boxes(
                caja_origen=form.cleaned_data["caja_origen"],
                caja_destino=form.cleaned_data["caja_destino"],
                monto=form.cleaned_data["monto"],
                observacion=form.cleaned_data["observacion"],
                creado_por=request.user,
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar el traspaso entre cajas.")
        else:
            messages.success(request, "Traspaso entre cajas registrado.")
            url = reverse("cashops:dashboard")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Arrastre entre cajas",
            "subtitle": "Unificacion o traspaso auditable dentro de la misma sucursal, incluso entre turnos o dias.",
            "form": form,
            "submit_label": "Guardar arrastre",
            "back_url": reverse("cashops:dashboard"),
            "form_action": reverse("cashops:transfer_boxes"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def transfer_between_branches_view(request):
    raise Http404(BRANCH_TRANSFER_DISABLED_REASON)
    _require_cashops_admin(request)
    form = TransferenciaEntreSucursalesForm(request.POST or None)
    form.fields["sucursal_origen"].queryset = Sucursal.objects.filter(activa=True)
    form.fields["sucursal_destino"].queryset = Sucursal.objects.filter(activa=True)
    open_boxes = Caja.objects.select_related("turno", "sucursal", "usuario").filter(estado=Caja.Estado.ABIERTA)
    form.fields["caja_origen"].queryset = _owned_open_boxes(request)
    form.fields["caja_destino"].queryset = open_boxes
    if request.method == "POST" and form.is_valid():
        try:
            transfer_between_branches(
                sucursal_origen=form.cleaned_data["sucursal_origen"],
                sucursal_destino=form.cleaned_data["sucursal_destino"],
                clase=form.cleaned_data["clase"],
                monto=form.cleaned_data["monto"],
                observacion=form.cleaned_data["observacion"],
                caja_origen=form.cleaned_data["caja_origen"],
                caja_destino=form.cleaned_data["caja_destino"],
                creado_por=request.user,
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar la transferencia entre sucursales.")
        else:
            messages.success(request, "Transferencia entre sucursales registrada.")
            url = reverse("cashops:dashboard")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Transferencia entre sucursales",
            "subtitle": "Envio de dinero o mercaderia con trazabilidad.",
            "form": form,
            "submit_label": "Guardar transferencia",
            "back_url": reverse("cashops:dashboard"),
            "form_action": reverse("cashops:transfer_branches"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def close_box_preview(request, box_id: int):
    _require_cashops_write(request)
    box = _get_box_for_request(request, box_id)
    context = _build_close_preview_context(box, request.GET.get("saldo_fisico"))
    return render(request, "cashops/partials/close_preview.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def close_box_view(request, box_id: int):
    _require_cashops_write(request)
    box = _get_box_for_request(request, box_id)
    form = CierreCajaForm(request.POST or None, caja=box)
    form.fields["saldo_fisico"].widget.attrs.update(
        {
            "hx-get": reverse("cashops:box_close_preview", args=[box.pk]),
            "hx-target": "#close-preview",
            "hx-trigger": "input changed delay:250ms, blur",
            "hx-include": "closest form",
        }
    )
    if request.method == "POST" and form.is_valid():
        try:
            cierre = close_box(
                caja=box,
                saldo_fisico=form.cleaned_data["saldo_fisico"],
                justificacion=form.cleaned_data["justificacion"],
                cerrado_por=request.user,
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo cerrar la caja.")
        else:
            if cierre.estado == CierreCaja.Estado.JUSTIFICADO:
                messages.warning(request, "Caja cerrada con diferencia grave y alerta registrada.")
            else:
                messages.success(request, "Caja cerrada.")
            url = reverse("cashops:dashboard")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    context = {
        "title": "Cerrar caja",
        "subtitle": f"Caja activa: {box.id}",
        "form": form,
        "submit_label": "Cerrar caja",
        "back_url": f"{reverse('cashops:dashboard')}?scope=box&box={box.pk}",
        "form_action": reverse("cashops:box_close", args=[box.pk]),
        "preview_url": reverse("cashops:box_close_preview", args=[box.pk]),
        "preview": _build_close_preview_context(box, request.POST.get("saldo_fisico")),
    }
    return render(
        request,
        "cashops/close_box.html",
        context,
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )

@login_required
def resolve_alert(request, alert_id: int):
    _require_config_write(request)
    alert = get_object_or_404(AlertaOperativa, pk=alert_id)
    alert.resuelta = True
    alert.save(update_fields=['resuelta'])
    messages.success(request, 'Alerta marcada como resuelta.')

    url = request.META.get('HTTP_REFERER') or reverse('cashops:dashboard')
    return _hx_redirect(url) if _is_htmx(request) else redirect(url)


# --- EP-12: Empresas ---

@login_required
def empresa_list(request):
    _require_config_read(request)
    empresas = Empresa.objects.all()
    items = []
    for e in empresas:
        num_suc = e.sucursales.count()
        items.append({
            "title": e.nombre,
            "subtitle": e.identificador_fiscal or "Sin identificador fiscal",
            "badge": "Activa" if e.activa else "Inactiva",
            "badge_class": "badge-success" if e.activa else "badge-muted",
            "meta": f"{num_suc} sucursal{'es' if num_suc != 1 else ''}",
            "href": reverse("cashops:empresa_update", args=[e.pk]),
        })
    return render(request, "cashops/list_page.html", {
        "title": "Empresas",
        "subtitle": "Razon social o unidad de negocio que agrupa sucursales.",
        "create_url": reverse("cashops:empresa_create"),
        "items": items,
        "show_danger_zone": True,
        "reset_url": reverse("cashops:reset_operational_data"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def empresa_create(request):
    _require_config_write(request)
    form = EmpresaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} creada.")
        return redirect(reverse("cashops:empresa_list"))
    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nueva empresa",
            "subtitle": "Alta de razon social o unidad de negocio.",
            "form": form,
            "submit_label": "Guardar empresa",
            "back_url": reverse("cashops:empresa_list"),
            "form_action": reverse("cashops:empresa_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empresa_update(request, empresa_id: int):
    _require_config_write(request)
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    form = EmpresaForm(request.POST or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        messages.success(request, f"Empresa {empresa.nombre} actualizada.")
        return redirect(reverse("cashops:empresa_list"))
    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": f"Editar empresa: {empresa.nombre}",
            "subtitle": "Ajusta nombre, identificador y estado.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("cashops:empresa_list"),
            "form_action": reverse("cashops:empresa_update", args=[empresa.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def empresa_toggle(request, empresa_id: int):
    _require_config_write(request)
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    empresa.activa = not empresa.activa
    empresa.save(update_fields=["activa", "actualizada_en"])
    messages.success(
        request,
        f"Empresa {empresa.nombre} {'activada' if empresa.activa else 'desactivada'}.",
    )
    return redirect(reverse("cashops:empresa_list"))


@login_required
@require_http_methods(["POST"])
def set_empresa_activa(request):
    """Mantiene compatibilidad hacia atrás — redirige a set_empresas_activas."""
    empresa_id = request.POST.get("empresa_id")
    if empresa_id:
        try:
            eid = int(empresa_id)
            permitidas_ids = set(request.user.empresas_permitidas.values_list("pk", flat=True))
            if permitidas_ids and eid not in permitidas_ids:
                pass
            else:
                Empresa.objects.get(pk=eid, activa=True)
                request.session["empresa_ids"] = [eid]
                request.session.pop("empresa_activa_id", None)
        except (Empresa.DoesNotExist, TypeError, ValueError):
            pass
    next_url = request.POST.get("next") or reverse("cashops:dashboard")
    return redirect(next_url)


@login_required
@require_http_methods(["POST"])
def set_empresas_activas(request):
    """Guarda en sesión la lista de empresas seleccionadas (multi-empresa)."""
    user = request.user
    permitidas_ids = set(user.empresas_permitidas.values_list("pk", flat=True))

    raw_ids = request.POST.getlist("empresa_ids")
    empresa_ids = []
    for v in raw_ids:
        try:
            eid = int(v)
            if permitidas_ids and eid not in permitidas_ids:
                continue
            if Empresa.objects.filter(pk=eid, activa=True).exists():
                empresa_ids.append(eid)
        except (TypeError, ValueError):
            pass
    request.session["empresa_ids"] = empresa_ids
    request.session.pop("empresa_activa_id", None)
    next_url = request.POST.get("next") or reverse("cashops:dashboard")
    return redirect(next_url)


# --- Canales de ingreso ---

@login_required
def canal_ingreso_list(request):
    _require_config_read(request)
    canales = CanalIngreso.objects.all()
    return render(request, "cashops/canal_ingreso_list.html", {"canales": canales})


@login_required
@require_http_methods(["GET", "POST"])
def canal_ingreso_create(request):
    _require_config_write(request)
    form = CanalIngresoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        canal = form.save(commit=False)
        canal.es_sistema = False
        canal.activo = True
        canal.codigo = canal.nombre.upper().replace(" ", "_")
        try:
            canal.full_clean()
            canal.save()
        except Exception as error:
            _handle_operation_error(form, error, "No se pudo guardar el canal.")
        else:
            messages.success(request, f"Canal {canal.nombre} creado.")
            url = reverse("cashops:canal_ingreso_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo canal de ingreso",
            "subtitle": "Agrega un nuevo medio de cobro para registrar ventas.",
            "form": form,
            "submit_label": "Guardar canal",
            "back_url": reverse("cashops:canal_ingreso_list"),
            "form_action": reverse("cashops:canal_ingreso_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def canal_ingreso_update(request, canal_id: int):
    _require_config_write(request)
    canal = get_object_or_404(CanalIngreso, pk=canal_id)
    if canal.es_sistema:
        raise PermissionDenied("Los canales del sistema no se pueden editar desde esta pantalla.")
    form = CanalIngresoForm(request.POST or None, instance=canal)
    if request.method == "POST" and form.is_valid():
        try:
            canal = form.save()
        except IntegrityError as error:
            _handle_operation_error(form, error, "No se pudo actualizar el canal.")
        else:
            messages.success(request, f"Canal {canal.nombre} actualizado.")
            url = reverse("cashops:canal_ingreso_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": f"Editar canal: {canal.nombre}",
            "subtitle": "Podes renombrar o reordenar el canal.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("cashops:canal_ingreso_list"),
            "form_action": reverse("cashops:canal_ingreso_update", args=[canal.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def canal_ingreso_toggle(request, canal_id: int):
    _require_config_write(request)
    canal = get_object_or_404(CanalIngreso, pk=canal_id)
    if canal.es_sistema:
        raise PermissionDenied("Los canales del sistema no se pueden activar ni desactivar manualmente.")
    canal.activo = not canal.activo
    canal.save(update_fields=["activo"])
    messages.success(
        request,
        f"Canal {canal.nombre} {'activado' if canal.activo else 'desactivado'}.",
    )
    url = reverse("cashops:canal_ingreso_list")
    return _hx_redirect(url) if _is_htmx(request) else redirect(url)


# --- Reinicio de datos operativos (solo para entornos de prueba) ---

@login_required
@require_http_methods(["GET", "POST"])
def reset_operational_data(request):
    _require_config_write(request)

    if request.method == "POST":
        step = request.POST.get("step", "1")

        if step == "2":
            from django.db import transaction
            from .models import AlertaOperativa, MovimientoCaja, CierreCaja, Transferencia
            from treasury.models import (
                DescuentoAcreditacion, AcreditacionTarjeta, MovimientoBancario,
                MovimientoCajaCentral, ArqueoDisponibilidades, CajaCentral,
                CompromisoEspecial, PagoTesoreria, CuentaPorPagar, LotePOS,
                CierreMensualTesoreria,
            )
            with transaction.atomic():
                # Cashops: orden respeta PROTECT FKs hacia Caja
                AlertaOperativa.objects.all().delete()
                MovimientoCaja.objects.all().delete()
                CierreCaja.objects.all().delete()    # cascadea Justificacion
                Transferencia.objects.all().delete()
                Caja.objects.all().delete()
                # Treasury: orden respeta PROTECT FKs en cadena
                DescuentoAcreditacion.objects.all().delete()
                AcreditacionTarjeta.objects.all().delete()
                MovimientoBancario.objects.all().delete()
                MovimientoCajaCentral.objects.all().delete()
                ArqueoDisponibilidades.objects.all().delete()
                CajaCentral.objects.all().delete()
                CompromisoEspecial.objects.all().delete()
                PagoTesoreria.objects.all().delete()
                CuentaPorPagar.objects.all().delete()
                LotePOS.objects.all().delete()
                CierreMensualTesoreria.objects.all().delete()

            messages.success(request, "Todos los datos operativos fueron eliminados. El sistema quedo vacio.")
            return redirect(reverse("cashops:empresa_list"))

        # step == "1" → mostrar segunda confirmacion
        return render(request, "cashops/reset_confirm.html", {"step": 2})

    return render(request, "cashops/reset_confirm.html", {"step": 1})
