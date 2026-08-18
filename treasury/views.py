from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import urlencode
from django.views.decorators.http import require_http_methods

from .forms import (
    ArqueoForm,
    BankAccountFilterForm,
    BankAccountForm,
    BankMovementAnnulForm,
    BankMovementFilterForm,
    BankMovementForm,
    BankMovementImputationForm,
    BankPaymentMethodCorrectionForm,
    BankReconciliationFilterForm,
    CardAccreditationFilterForm,
    CardAccreditationForm,
    CashPaymentForm,
    CentralCashMovementAnnulForm,
    CentralCashMovementForm,
    ChequePaymentForm,
    DisponibilidadesFilterForm,
    ECheqPaymentForm,
    PayableAnnulForm,
    PayableCategoryFilterForm,
    PayableCategoryForm,
    InitialBankBalanceForm,
    PayableFilterForm,
    PayableForm,
    PaymentAnnulForm,
    PaymentFilterForm,
    PosBatchFilterForm,
    PosBatchForm,
    SpecialCommitmentDecisionForm,
    SpecialCommitmentFilterForm,
    SpecialCommitmentForm,
    CargaInicialCajaCentralForm,
    EgresoTesoreriaForm,
    SupplierFilterForm,
    SupplierForm,
    SupplierHistoryFilterForm,
    SupplierPaymentBatchForm,
    SupplierPickerForm,
    open_payables_queryset,
    TreasuryDashboardFilterForm,
    TransferPaymentForm,
)
from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
    SaldoInicialCuentaBancaria,
)
from .permissions import (
    can_delete_central_cash_movement,
    ensure_delete_central_cash_movement,
    ensure_treasury_permission,
)
from .services import (
    _central_cash_balance_until,
    annul_central_cash_movement,
    annul_payable,
    annul_payment,
    bank_account_empresa_scope_query,
    build_bank_reconciliation_snapshot,
    build_economic_period_snapshot,
    build_economic_rubro_detail,
    build_disponibilidades_snapshot,
    build_financial_period_snapshot,
    build_special_commitments_snapshot,
    build_supplier_history_snapshot,
    CENTRAL_CASH_IN_TYPES,
    CENTRAL_CASH_OUT_TYPES,
    annul_bank_movement,
    close_treasury_month,
    complete_bank_movement_imputation,
    correct_bank_payment_method,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_pos_batch,
    create_supplier,
    decide_special_commitment,
    get_boveda,
    formato_money,
    importe_asignado_del_movimiento,
    importe_sin_asignar_del_movimiento,
    lineas_que_parecen_la_misma_factura,
    pay_debt_from_bank_movement,
    pay_debts_from_bank_movement,
    is_central_cash_movement_annullable,
    link_payment_to_bank_movement,
    register_arqueo,
    register_card_accreditation,
    register_cash_payment,
    register_central_cash_movement,
    register_cheque_payment,
    register_echeq_payment,
    register_carga_inicial_caja_central,
    register_egreso_tesoreria,
    register_payable,
    register_special_commitment,
    register_supplier_payment_batch,
    register_transfer_payment,
    scope_central_cash_movements,
    set_initial_bank_balance,
    toggle_bank_account,
    toggle_payable_category,
    toggle_supplier,
    update_bank_account,
    update_bank_movement,
    update_payable,
    update_payable_category,
    update_pos_batch,
    update_supplier,
)


TREASURY_WRITE_VIEW_NAMES = {
    "proveedores_create",
    "proveedores_update",
    "proveedores_toggle",
    "categorias_create",
    "categorias_update",
    "categorias_toggle",
    "cuentas_bancarias_create",
    "cuentas_bancarias_update",
    "cuentas_bancarias_toggle",
    "bank_initial_balances_create",
    "cuentas_por_pagar_create",
    "cuentas_por_pagar_update",
    "cuentas_por_pagar_annul",
    "compromisos_especiales_create",
    "compromisos_especiales_decide",
    "pagos_transferencia_create",
    "pagos_cheque_create",
    "pagos_echeq_create",
    "pagos_efectivo_create",
    "pagos_proveedor_create",
    "pagos_annul",
    "bank_movements_create",
    "bank_movements_edit_confirm",
    "bank_movements_update",
    "bank_movements_delete_confirm",
    "bank_movements_link",
    "bank_movements_imputation",
    "bank_movements_correct_method",
    "pos_batches_create",
    "card_accreditations_register",
    "central_cash_create",
    "carga_inicial_caja_central",
    "egreso_tesoreria_create",
    "arqueo_create",
    "close_month",
}


def _require_treasury_admin(request) -> None:
    if not request.user.is_authenticated:
        raise PermissionDenied("Debes iniciar sesion.")
    view_name = request.resolver_match.url_name if request.resolver_match else ""
    action = "write" if request.method != "GET" or view_name in TREASURY_WRITE_VIEW_NAMES else "read"
    ensure_treasury_permission(request.user, action)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _render_form(request, context: dict, status: int = 200):
    template = "treasury/partials/form_card.html" if _is_htmx(request) else "treasury/form_page.html"
    return render(request, template, context, status=status)


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


def _get_empresa_ids(request):
    """Retorna la lista de empresa IDs seleccionados en sesión."""
    ids = request.session.get("empresa_ids")
    if ids is not None:
        return ids
    if request.user.is_authenticated:
        allowed_ids = list(request.user.empresas_permitidas.values_list("pk", flat=True))
        old_id = request.session.get("empresa_activa_id")
        if old_id and old_id in allowed_ids:
            return [old_id]
        return allowed_ids
    return []


def _filter_sucursal_qs(request, qs):
    """Aplica filtro de empresa al queryset de Sucursal."""
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        return qs.filter(empresa_id__in=empresa_ids)
    return qs


def _money(value) -> str:
    # Un solo formato de plata en todo el sistema: services.formato_money.
    return formato_money(value)


def _payable_badge(payable: CuentaPorPagar) -> tuple[str, str]:
    status = payable.estado_visible
    if status == "VENCIDA":
        return status, "badge-danger"
    if status == CuentaPorPagar.Estado.PAGADA:
        return "Pagada", "badge-success"
    if status == CuentaPorPagar.Estado.PARCIAL:
        return "Parcial", "badge-warning"
    if status == CuentaPorPagar.Estado.ANULADA:
        return "Anulada", "badge-muted"
    return "Pendiente", "badge"


def _payment_badge(payment: PagoTesoreria) -> tuple[str, str]:
    if payment.estado == PagoTesoreria.Estado.ANULADO:
        return "Anulado", "badge-muted"
    if payment.estado_bancario == PagoTesoreria.EstadoBancario.IMPACTADO:
        return "Impactado", "badge-success"
    if payment.estado_bancario == PagoTesoreria.EstadoBancario.RECHAZADO:
        return "Rechazado", "badge-danger"
    return payment.get_medio_pago_display(), "badge"


def _special_commitment_badge(commitment: CompromisoEspecial) -> tuple[str, str]:
    if commitment.estado == CompromisoEspecial.Estado.APROBADO:
        return "Aprobado", "badge-success"
    if commitment.estado == CompromisoEspecial.Estado.EJECUTADO:
        return "Ejecutado", "badge-success"
    if commitment.estado == CompromisoEspecial.Estado.RECHAZADO:
        return "Rechazado", "badge-danger"
    if commitment.estado == CompromisoEspecial.Estado.APROBACION_PENDIENTE:
        return "Requiere aprobacion", "badge-warning"
    if commitment.estado == CompromisoEspecial.Estado.CANCELADO:
        return "Cancelado", "badge-muted"
    return "Pendiente", "badge"


def _supplier_item(supplier: Proveedor) -> dict:
    meta_bits = [supplier.contacto or "", supplier.telefono or "", supplier.email or ""]
    return {
        "href": reverse("treasury:proveedores_detail", args=[supplier.pk]),
        "title": supplier.razon_social,
        "subtitle": supplier.identificador_fiscal or "Sin identificador fiscal",
        "badge": "Activo" if supplier.activo else "Inactivo",
        "badge_class": "badge-success" if supplier.activo else "badge-muted",
        "meta": " - ".join(bit for bit in meta_bits if bit),
    }


def _category_item(category: CategoriaCuentaPagar) -> dict:
    if not category.rubro_operativo_id:
        badge = "Pendiente rubro"
        badge_class = "badge-warning"
    else:
        badge = "Activa" if category.activo else "Inactiva"
        badge_class = "badge-success" if category.activo else "badge-muted"
    return {
        "href": reverse("treasury:categorias_update", args=[category.pk]),
        "title": category.nombre,
        "subtitle": f"Rubro: {category.rubro_label}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": "",
    }


def _bank_movement_rubro_label(movement: MovimientoBancario) -> str:
    if movement.rubro_operativo_id:
        return movement.rubro_operativo.nombre
    if movement.categoria_id:
        if movement.categoria.rubro_operativo_id:
            return movement.categoria.rubro_label
        return f"{movement.categoria.nombre} (legacy)"
    return "No aplica"


def _bank_movement_can_be_manually_changed(movement: MovimientoBancario) -> bool:
    if movement.estado != MovimientoBancario.Estado.REGISTRADO:
        return False
    if movement.origen != MovimientoBancario.Origen.MANUAL:
        return False
    if movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        return False
    return not hasattr(movement, "acreditacion_tarjeta")


def _bank_movement_can_correct_payment_method(movement: MovimientoBancario, *, tiene_pagos=None) -> bool:
    """US-4.11: un egreso vigente que paga facturas no se puede editar (editar
    moveria monto, fecha y cuenta) pero SI se puede re-tipificar: cargaron
    transferencia y era cheque. `tiene_pagos` evita repetir la consulta cuando el
    llamador ya tiene la lista de pagos vinculados."""
    if movement.estado != MovimientoBancario.Estado.REGISTRADO:
        return False
    if movement.tipo != MovimientoBancario.Tipo.DEBITO:
        return False
    if tiene_pagos is None:
        tiene_pagos = movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists()
    return bool(tiene_pagos)


def _bank_account_item(bank_account: CuentaBancaria) -> dict:
    initial_balance = bank_account.saldos_iniciales.order_by("-fecha_referencia", "-id").first()
    meta = bank_account.alias or bank_account.cbu or bank_account.numero_cuenta
    if initial_balance:
        meta = (
            f"{meta} | Saldo inicial: {_money(initial_balance.importe)} "
            f"desde {initial_balance.fecha_referencia:%d/%m/%Y}"
        )
    empresa_label = bank_account.empresa.nombre if bank_account.empresa_id else "Sin empresa asignada"
    return {
        "href": reverse("treasury:cuentas_bancarias_update", args=[bank_account.pk]),
        "title": bank_account.nombre,
        "subtitle": f"{bank_account.banco} - {bank_account.get_tipo_cuenta_display()} | {empresa_label}",
        "badge": "Activa" if bank_account.activa else "Inactiva",
        "badge_class": "badge-success" if bank_account.activa else "badge-muted",
        "meta": meta,
    }


def _special_commitment_item(commitment: CompromisoEspecial) -> dict:
    badge, badge_class = _special_commitment_badge(commitment)
    return {
        "href": reverse("treasury:compromisos_especiales_detail", args=[commitment.pk]),
        "title": commitment.concepto,
        "subtitle": f"{commitment.get_tipo_display()} - {commitment.sustento_referencia}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": (
            f"Vence {commitment.vencimiento:%d/%m/%Y}" if commitment.vencimiento else f"Fecha {commitment.fecha_compromiso:%d/%m/%Y}"
        )
        + f" - {_money(commitment.monto_estimado)}",
    }


def _action(url: str, label: str, kind: str = "secondary") -> dict:
    return {"href": url, "label": label, "kind": kind}


def _payable_item(payable: CuentaPorPagar) -> dict:
    badge, badge_class = _payable_badge(payable)
    return {
        "href": reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk]),
        "title": payable.proveedor.razon_social,
        "subtitle": f"{payable.sucursal_label} - {payable.concepto} - Rubro {payable.categoria.rubro_label}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": (
            f"{payable.origen_label} - Periodo {payable.periodo_referencia:%m/%Y} - "
            f"Vence {payable.fecha_vencimiento:%d/%m/%Y} - Pendiente {_money(payable.saldo_pendiente)}"
        ),
    }


def _payment_item(payment: PagoTesoreria) -> dict:
    badge, badge_class = _payment_badge(payment)
    account_label = payment.cuenta_bancaria.nombre if payment.cuenta_bancaria_id else "Caja central"
    return {
        "href": reverse("treasury:pagos_detail", args=[payment.pk]),
        "title": payment.cuenta_por_pagar.proveedor.razon_social,
        "subtitle": f"{payment.get_medio_pago_display()} - {payment.cuenta_por_pagar.concepto}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": f"{payment.fecha_pago:%d/%m/%Y} - {_money(payment.monto)} - {account_label}",
    }


@login_required
def index(request):
    return redirect("treasury:dashboard")


@login_required
def dashboard(request):
    _require_treasury_admin(request)
    from cashops.models import Sucursal

    today = timezone.localdate()
    first_day_of_month = today.replace(day=1)
    filter_form = TreasuryDashboardFilterForm(
        request.GET or None,
        initial={"fecha_desde": first_day_of_month, "fecha_hasta": today},
    )
    filter_form.fields["sucursal"].queryset = _filter_sucursal_qs(request, Sucursal.objects.all())
    if filter_form.is_valid():
        sucursal = filter_form.cleaned_data.get("sucursal")
        date_from = filter_form.cleaned_data.get("fecha_desde") or first_day_of_month
        date_to = filter_form.cleaned_data.get("fecha_hasta") or today
    else:
        sucursal = None
        date_from = first_day_of_month
        date_to = today

    empresa_ids = _get_empresa_ids(request)
    snapshot = build_financial_period_snapshot(date_from=date_from, date_to=date_to, sucursal=sucursal, empresa_ids=empresa_ids)
    economic_snapshot = build_economic_period_snapshot(date_from=date_from, date_to=date_to, sucursal=sucursal, empresa_ids=empresa_ids)

    sections = [
        {
            "label": "Deudas",
            "description": "Obligaciones pendientes con proveedores.",
            "href": reverse("treasury:cuentas_por_pagar_list"),
            "count": snapshot["pending_count"],
        },
        {
            "label": "Pagos",
            "description": "Egresos internos por transferencia, cheque o efectivo.",
            "href": reverse("treasury:pagos_list"),
            "count": PagoTesoreria.objects.count(),
        },
        {
            "label": "Movimientos",
            "description": "Registro interno de cuentas de control.",
            "href": reverse("treasury:bank_movements_list"),
            "count": MovimientoBancario.objects.count(),
        },
        {
            "label": "Compromisos especiales",
            "description": "Impuestos, planes, embargos y autorizaciones.",
            "href": reverse("treasury:compromisos_especiales_list"),
            "count": CompromisoEspecial.objects.count(),
        },
        {
            "label": "Efectivo",
            "description": "Libro de caja central y egresos en efectivo.",
            "href": reverse("treasury:central_cash_list"),
            "count": MovimientoCajaCentral.objects.count(),
        },
        {
            "label": "Proveedores",
            "description": "Maestro de terceros.",
            "href": reverse("treasury:proveedores_list"),
            "count": Proveedor.objects.count(),
        },
    ]

    sucursales = _filter_sucursal_qs(request, Sucursal.objects.all())

    return render(
        request,
        "treasury/dashboard.html",
        {
            "sections": sections,
            "filter_form": filter_form,
            "snapshot": snapshot,
            "economic_snapshot": economic_snapshot,
            "sucursales": sucursales,
            "selected_sucursal": sucursal,
            "cashops_dashboard_url": reverse("cashops:dashboard"),
            "all_pending_payables": snapshot["all_pending_payables"],
            "recent_payments": snapshot["recent_payments"],
            "recent_batches": snapshot["recent_batches"],
            "recent_movements": snapshot["recent_movements"],
            "money": _money,
        },
    )


@login_required
def economic_rubro_detail(request, rubro_id):
    _require_treasury_admin(request)
    from cashops.models import RubroOperativo, Sucursal

    today = timezone.localdate()
    first_day_of_month = today.replace(day=1)
    get_object_or_404(RubroOperativo, pk=rubro_id)
    filter_form = TreasuryDashboardFilterForm(
        request.GET or None,
        initial={"fecha_desde": first_day_of_month, "fecha_hasta": today},
    )
    filter_form.fields["sucursal"].queryset = _filter_sucursal_qs(request, Sucursal.objects.all())
    if filter_form.is_valid():
        sucursal = filter_form.cleaned_data.get("sucursal")
        date_from = filter_form.cleaned_data.get("fecha_desde") or first_day_of_month
        date_to = filter_form.cleaned_data.get("fecha_hasta") or today
    else:
        sucursal = None
        date_from = first_day_of_month
        date_to = today

    empresa_ids = _get_empresa_ids(request)
    detail = build_economic_rubro_detail(
        rubro_id=rubro_id,
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    summary = build_economic_period_snapshot(
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    # Contra la lista plana: si el rubro esta dentro de un grupo, en `items` ya
    # no tiene fila propia y la cabecera de esta pantalla quedaria vacia.
    summary_item = next(
        (item for item in summary["rubro_items"] if item["rubro"] and item["rubro"].pk == detail["rubro"].pk),
        None,
    )
    periodo_qs = f"fecha_desde={date_from.isoformat()}&fecha_hasta={date_to.isoformat()}"
    if sucursal is not None:
        periodo_qs += f"&sucursal={sucursal.pk}"
    grupo = detail["rubro"].grupo_de_lectura
    if grupo is not None:
        # Se vuelve al desglose del grupo, que es de donde se entro.
        back_url = f"{reverse('treasury:economic_grupo_detail', args=[grupo.pk])}?{periodo_qs}"
        back_label = f"Volver a {grupo.nombre}"
    else:
        back_url = f"{reverse('treasury:dashboard')}?{periodo_qs}"
        back_label = "Volver al dashboard"

    return render(
        request,
        "treasury/economic_rubro_detail.html",
        {
            "detail": detail,
            "summary_item": summary_item,
            "filter_form": filter_form,
            "back_url": back_url,
            "back_label": back_label,
        },
    )


@login_required
def economic_grupo_detail(request, grupo_id):
    """Desglose de un grupo: sus rubros con los mismos importes de la fila.

    No recalcula nada: toma la lista plana del mismo snapshot que arma el
    dashboard y se queda con los rubros del grupo. Asi el total de la cabecera
    reconcilia siempre contra la fila que se clickeo.
    """
    _require_treasury_admin(request)
    from cashops.models import GrupoRubro, Sucursal

    today = timezone.localdate()
    first_day_of_month = today.replace(day=1)
    grupo = get_object_or_404(GrupoRubro, pk=grupo_id)
    filter_form = TreasuryDashboardFilterForm(
        request.GET or None,
        initial={"fecha_desde": first_day_of_month, "fecha_hasta": today},
    )
    filter_form.fields["sucursal"].queryset = _filter_sucursal_qs(request, Sucursal.objects.all())
    if filter_form.is_valid():
        sucursal = filter_form.cleaned_data.get("sucursal")
        date_from = filter_form.cleaned_data.get("fecha_desde") or first_day_of_month
        date_to = filter_form.cleaned_data.get("fecha_hasta") or today
    else:
        sucursal = None
        date_from = first_day_of_month
        date_to = today

    empresa_ids = _get_empresa_ids(request)
    summary = build_economic_period_snapshot(
        date_from=date_from,
        date_to=date_to,
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    rubro_ids = set(grupo.rubros.values_list("pk", flat=True))
    items = [
        item
        for item in summary["rubro_items"]
        if item["rubro"] is not None and item["rubro"].pk in rubro_ids
    ]
    grupo_row = next(
        (row for row in summary["items"] if row.get("grupo") and row["grupo"].pk == grupo.pk),
        None,
    )
    periodo_qs = f"fecha_desde={date_from.isoformat()}&fecha_hasta={date_to.isoformat()}"
    if sucursal is not None:
        periodo_qs += f"&sucursal={sucursal.pk}"
    back_url = f"{reverse('treasury:dashboard')}?{periodo_qs}"

    return render(
        request,
        "treasury/economic_grupo_detail.html",
        {
            "grupo": grupo,
            "grupo_row": grupo_row,
            "items": items,
            "economic_snapshot": summary,
            "selected_sucursal": sucursal,
            "filter_form": filter_form,
            "back_url": back_url,
            "periodo_qs": periodo_qs,
        },
    )


@login_required
def proveedores_list(request):
    _require_treasury_admin(request)
    form = SupplierFilterForm(request.GET or None)
    queryset = Proveedor.objects.order_by("razon_social")
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        active = form.cleaned_data.get("activo")
        if q:
            queryset = queryset.filter(
                Q(razon_social__icontains=q)
                | Q(identificador_fiscal__icontains=q)
                | Q(contacto__icontains=q)
                | Q(email__icontains=q)
            )
        if active == "1":
            queryset = queryset.filter(activo=True)
        elif active == "0":
            queryset = queryset.filter(activo=False)
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Proveedores",
            "subtitle": "Alta y mantenimiento del maestro de proveedores.",
            "items": [_supplier_item(item) for item in queryset],
            "create_url": reverse("treasury:proveedores_create"),
            "filter_form": form,
        },
    )


@login_required
def proveedores_detail(request, supplier_id: int):
    _require_treasury_admin(request)
    supplier = get_object_or_404(Proveedor, pk=supplier_id)
    filter_form = SupplierHistoryFilterForm(request.GET or None)
    date_from = date_to = None
    if filter_form.is_valid():
        date_from = filter_form.cleaned_data.get("fecha_desde")
        date_to = filter_form.cleaned_data.get("fecha_hasta")
    snapshot = build_supplier_history_snapshot(supplier=supplier, date_from=date_from, date_to=date_to)
    fields = [
        {"label": "Identificador", "value": supplier.identificador_fiscal or "Sin dato"},
        {"label": "Contacto", "value": supplier.contacto or "Sin dato"},
        {"label": "Saldo historico", "value": _money(snapshot["historical_pending"])},
        {"label": "Total historico", "value": _money(snapshot["historical_total"])},
        {"label": "Pagado historico", "value": _money(snapshot["historical_paid"])},
        {"label": "Estado", "value": "Activo" if supplier.activo else "Inactivo"},
    ]
    extra_sections = [
        {"title": "Obligaciones", "items": [_payable_item(payable) for payable in snapshot["payables"][:10]], "empty_label": "No hay obligaciones para este proveedor."},
        {"title": "Pagos", "items": [_payment_item(payment) for payment in snapshot["payments"][:10]], "empty_label": "No hay pagos para este proveedor."},
    ]
    actions = [
        _action(reverse("treasury:proveedores_update", args=[supplier.pk]), "Editar", "primary"),
        _action(reverse("treasury:proveedores_toggle", args=[supplier.pk]), "Activar" if not supplier.activo else "Desactivar"),
    ]
    return render(
        request,
        "treasury/detail_page.html",
        {
            "title": supplier.razon_social,
            "subtitle": supplier.email or "Historial financiero del proveedor.",
            "back_url": reverse("treasury:proveedores_list"),
            "section_label": "Proveedor",
            "fields": fields,
            "actions": actions,
            "extra_sections": extra_sections,
            "filter_form": filter_form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def proveedores_create(request):
    _require_treasury_admin(request)
    form = SupplierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            supplier = create_supplier(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar el proveedor.")
        else:
            messages.success(request, f"Proveedor {supplier.razon_social} guardado.")
            url = reverse("treasury:proveedores_detail", args=[supplier.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": "Nuevo proveedor",
            "subtitle": "Datos base para deuda, pagos y trazabilidad.",
            "form": form,
            "submit_label": "Guardar proveedor",
            "back_url": reverse("treasury:proveedores_list"),
            "form_action": reverse("treasury:proveedores_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def proveedores_update(request, supplier_id: int):
    _require_treasury_admin(request)
    supplier = get_object_or_404(Proveedor, pk=supplier_id)
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        try:
            supplier = update_supplier(supplier=supplier, actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo actualizar el proveedor.")
        else:
            messages.success(request, f"Proveedor {supplier.razon_social} actualizado.")
            url = reverse("treasury:proveedores_detail", args=[supplier.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": f"Editar proveedor: {supplier.razon_social}",
            "subtitle": "Actualiza datos de contacto y estado.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("treasury:proveedores_detail", args=[supplier.pk]),
            "form_action": reverse("treasury:proveedores_update", args=[supplier.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def proveedores_toggle(request, supplier_id: int):
    _require_treasury_admin(request)
    supplier = get_object_or_404(Proveedor, pk=supplier_id)
    supplier = toggle_supplier(supplier=supplier, actor=request.user)
    messages.success(request, f"Proveedor {supplier.razon_social} {'activado' if supplier.activo else 'desactivado'}.")
    return redirect("treasury:proveedores_detail", supplier.pk)


@login_required
def categorias_list(request):
    _require_treasury_admin(request)
    form = PayableCategoryFilterForm(request.GET or None)
    queryset = CategoriaCuentaPagar.objects.select_related("rubro_operativo").order_by("nombre")
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        active = form.cleaned_data.get("activo")
        if q:
            queryset = queryset.filter(nombre__icontains=q)
        if active == "1":
            queryset = queryset.filter(activo=True)
        elif active == "0":
            queryset = queryset.filter(activo=False)
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Categorías de deuda",
            "subtitle": "Ordenan deuda y facilitan filtros de vencimiento.",
            "items": [_category_item(item) for item in queryset],
            "create_url": reverse("treasury:categorias_create"),
            "filter_form": form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def categorias_create(request):
    _require_treasury_admin(request)
    form = PayableCategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_payable_category(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar la categoria.")
        else:
            messages.success(request, "Categoria guardada.")
            url = reverse("treasury:categorias_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": "Nueva categoria",
            "subtitle": "Clasificacion para cuentas por pagar.",
            "form": form,
            "submit_label": "Guardar categoria",
            "back_url": reverse("treasury:categorias_list"),
            "form_action": reverse("treasury:categorias_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def categorias_update(request, category_id: int):
    _require_treasury_admin(request)
    category = get_object_or_404(CategoriaCuentaPagar, pk=category_id)
    form = PayableCategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        try:
            update_payable_category(category=category, actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo actualizar la categoria.")
        else:
            messages.success(request, "Categoria actualizada.")
            url = reverse("treasury:categorias_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": f"Editar categoria: {category.nombre}",
            "subtitle": "Renombra o ajusta el estado operativo.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("treasury:categorias_list"),
            "form_action": reverse("treasury:categorias_update", args=[category.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def categorias_toggle(request, category_id: int):
    _require_treasury_admin(request)
    category = get_object_or_404(CategoriaCuentaPagar, pk=category_id)
    try:
        category = toggle_payable_category(category=category, actor=request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, f"Categoria {category.nombre} {'activada' if category.activo else 'desactivada'}.")
    return redirect("treasury:categorias_list")


@login_required
def cuentas_bancarias_list(request):
    _require_treasury_admin(request)
    form = BankAccountFilterForm(request.GET or None)
    queryset = (
        CuentaBancaria.objects.select_related("empresa")
        .prefetch_related("saldos_iniciales")
        .order_by("banco", "nombre")
    )
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        queryset = queryset.filter(bank_account_empresa_scope_query(empresa_ids))
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        active = form.cleaned_data.get("activa")
        if q:
            queryset = queryset.filter(
                Q(nombre__icontains=q)
                | Q(banco__icontains=q)
                | Q(numero_cuenta__icontains=q)
                | Q(alias__icontains=q)
                | Q(cbu__icontains=q)
            )
        if active == "1":
            queryset = queryset.filter(activa=True)
        elif active == "0":
            queryset = queryset.filter(activa=False)
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Cuentas bancarias",
            "subtitle": "Origen real de pagos administrativos y trazabilidad.",
            "items": [_bank_account_item(item) for item in queryset],
            "create_url": reverse("treasury:cuentas_bancarias_create"),
            "secondary_url": reverse("treasury:bank_initial_balances_list"),
            "secondary_label": "Saldos iniciales",
            "filter_form": form,
        },
    )


def _get_scoped_bank_account_or_404(request, bank_account_id: int) -> CuentaBancaria:
    queryset = CuentaBancaria.objects.all()
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        queryset = queryset.filter(bank_account_empresa_scope_query(empresa_ids))
    return get_object_or_404(queryset, pk=bank_account_id)


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_bancarias_create(request):
    _require_treasury_admin(request)
    form = BankAccountForm(request.POST or None, empresa_ids=_get_empresa_ids(request))
    if request.method == "POST" and form.is_valid():
        try:
            create_bank_account(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar la cuenta bancaria.")
        else:
            messages.success(request, "Cuenta bancaria guardada.")
            url = reverse("treasury:cuentas_bancarias_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": "Nueva cuenta bancaria",
            "subtitle": "Cuenta origen para transferencias, cheques y ECHEQ.",
            "form": form,
            "submit_label": "Guardar cuenta",
            "back_url": reverse("treasury:cuentas_bancarias_list"),
            "form_action": reverse("treasury:cuentas_bancarias_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_bancarias_update(request, bank_account_id: int):
    _require_treasury_admin(request)
    bank_account = _get_scoped_bank_account_or_404(request, bank_account_id)
    form = BankAccountForm(
        request.POST or None,
        instance=bank_account,
        empresa_ids=_get_empresa_ids(request),
    )
    if request.method == "POST" and form.is_valid():
        try:
            update_bank_account(bank_account=bank_account, actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo actualizar la cuenta bancaria.")
        else:
            messages.success(request, "Cuenta bancaria actualizada.")
            url = reverse("treasury:cuentas_bancarias_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": f"Editar cuenta: {bank_account.nombre}",
            "subtitle": "Ajusta identificacion, banco y estado operativo.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("treasury:cuentas_bancarias_list"),
            "form_action": reverse("treasury:cuentas_bancarias_update", args=[bank_account.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["POST"])
def cuentas_bancarias_toggle(request, bank_account_id: int):
    _require_treasury_admin(request)
    bank_account = _get_scoped_bank_account_or_404(request, bank_account_id)
    bank_account = toggle_bank_account(bank_account=bank_account, actor=request.user)
    messages.success(
        request,
        f"Cuenta bancaria {bank_account.nombre} {'activada' if bank_account.activa else 'desactivada'}.",
    )
    return redirect("treasury:cuentas_bancarias_list")


@login_required
def bank_initial_balances_list(request):
    _require_treasury_admin(request)
    balances = SaldoInicialCuentaBancaria.objects.select_related(
        "cuenta_bancaria",
        "creado_por",
        "actualizado_por",
    )
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        balances = balances.filter(
            bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
        )
    items = []
    for balance in balances[:100]:
        corrected_by = f" | Corregido por: {balance.actualizado_por}" if balance.actualizado_por_id else ""
        previous = f" | Anterior: {_money(balance.importe_anterior)}" if balance.importe_anterior is not None else ""
        items.append(
            {
                "href": reverse("treasury:bank_initial_balances_create"),
                "title": balance.cuenta_bancaria.nombre,
                "subtitle": f"{balance.cuenta_bancaria.banco} | Desde {balance.fecha_referencia:%d/%m/%Y}",
                "badge": _money(balance.importe),
                "badge_class": "badge-info",
                "meta": f"Motivo: {balance.motivo}{previous}{corrected_by}",
            }
        )
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Saldos iniciales bancarios",
            "subtitle": "Punto de partida auditado por cuenta; no son movimientos bancarios reales.",
            "items": items,
            "create_url": reverse("treasury:bank_initial_balances_create"),
            "empty_message": "No hay saldos iniciales bancarios cargados.",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def bank_initial_balances_create(request):
    _require_treasury_admin(request)
    form = InitialBankBalanceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            set_initial_bank_balance(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar el saldo inicial bancario.")
        else:
            messages.success(request, "Saldo inicial bancario guardado.")
            return redirect("treasury:bank_initial_balances_list")
    return _render_form(
        request,
        {
            "title": "Saldo inicial bancario",
            "subtitle": "Carga o corrige el punto de partida de una cuenta sin crear movimientos reales.",
            "form": form,
            "submit_label": "Guardar saldo inicial",
            "back_url": reverse("treasury:bank_initial_balances_list"),
            "form_action": reverse("treasury:bank_initial_balances_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def cuentas_por_pagar_list(request):
    _require_treasury_admin(request)
    form = PayableFilterForm(request.GET or None)
    queryset = CuentaPorPagar.objects.select_related(
        "proveedor", "categoria", "categoria__rubro_operativo", "sucursal", "caja_origen"
    ).order_by("fecha_vencimiento", "proveedor__razon_social")
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        if not empresa_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(Q(sucursal__empresa_id__in=empresa_ids) | Q(sucursal__isnull=True))
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        proveedor = form.cleaned_data.get("proveedor")
        categoria = form.cleaned_data.get("categoria")
        rubro = form.cleaned_data.get("rubro")
        estado = form.cleaned_data.get("estado")
        sucursal = form.cleaned_data.get("sucursal")
        if q:
            queryset = queryset.filter(
                Q(proveedor__razon_social__icontains=q)
                | Q(concepto__icontains=q)
                | Q(referencia_comprobante__icontains=q)
                | Q(categoria__rubro_operativo__nombre__icontains=q)
            )
        if proveedor:
            queryset = queryset.filter(proveedor=proveedor)
        if categoria:
            queryset = queryset.filter(categoria=categoria)
        if rubro:
            queryset = queryset.filter(categoria__rubro_operativo=rubro)
        if estado == "VENCIDA":
            queryset = queryset.filter(
                estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL],
                fecha_vencimiento__lt=timezone.localdate(),
            )
        elif estado:
            queryset = queryset.filter(estado=estado)
        if sucursal:
            queryset = queryset.filter(sucursal=sucursal)
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Cuentas por pagar",
            "subtitle": "Deuda abierta, parcial, vencida o cerrada, trazable por periodo y rubro.",
            "items": [_payable_item(item) for item in queryset],
            "create_url": reverse("treasury:cuentas_por_pagar_create"),
            "filter_form": form,
        },
    )


@login_required
def cuentas_por_pagar_detail(request, payable_id: int):
    _require_treasury_admin(request)
    payable = get_object_or_404(
        CuentaPorPagar.objects.select_related(
            "proveedor",
            "categoria",
            "categoria__rubro_operativo",
            "creado_por",
            "anulada_por",
            "sucursal",
            "caja_origen",
        ),
        pk=payable_id,
    )
    payments = payable.pagos.select_related("cuenta_bancaria", "creado_por", "anulado_por").order_by("-fecha_pago", "-id")
    badge, badge_class = _payable_badge(payable)
    fields = [
        {"label": "Proveedor", "value": payable.proveedor.razon_social},
        {"label": "Sucursal", "value": payable.sucursal_label},
        {"label": "Origen", "value": payable.origen_label},
        {"label": "Categoria", "value": payable.categoria.nombre},
        {"label": "Rubro operativo", "value": payable.categoria.rubro_label},
        {"label": "Concepto", "value": payable.concepto},
        {"label": "Referencia", "value": payable.referencia_comprobante or "Sin referencia"},
        {"label": "Periodo economico", "value": payable.periodo_referencia.strftime("%m/%Y")},
        {"label": "Vencimiento", "value": payable.fecha_vencimiento.strftime("%d/%m/%Y")},
        {"label": "Importe total", "value": _money(payable.importe_total)},
        {"label": "Pagado", "value": _money(payable.total_pagado)},
        {"label": "Saldo pendiente", "value": _money(payable.saldo_pendiente)},
        {"label": "Estado", "value": badge},
        {"label": "Creado por", "value": str(payable.creado_por) if payable.creado_por else "Sistema"},
        {"label": "Creado en", "value": payable.creado_en.strftime("%d/%m/%Y %H:%M")},
    ]
    if payable.actualizado_por and (payable.actualizado_por != payable.creado_por or payable.actualizado_en > (payable.creado_en + timezone.timedelta(seconds=1))):
        fields.append({"label": "Actualizado por", "value": str(payable.actualizado_por)})
        fields.append({"label": "Actualizado en", "value": payable.actualizado_en.strftime("%d/%m/%Y %H:%M")})
    
    if payable.estado == CuentaPorPagar.Estado.ANULADA:
        fields.append({"label": "Anulado por", "value": str(payable.anulada_por) if payable.anulada_por else "N/A"})
        fields.append({"label": "Motivo anulacion", "value": payable.motivo_anulacion or "Sin motivo"})
    
    fields.append({"label": "Observaciones", "value": payable.observaciones or "Sin observaciones"})
    actions = []
    if payable.estado in {CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL}:
        actions.extend(
            [
                _action(reverse("treasury:cuentas_por_pagar_update", args=[payable.pk]), "Editar"),
                _action(reverse("treasury:pagos_transferencia_create") + f"?payable={payable.pk}", "Transferencia", "primary"),
                _action(reverse("treasury:pagos_cheque_create") + f"?payable={payable.pk}", "Cheque"),
                _action(reverse("treasury:pagos_echeq_create") + f"?payable={payable.pk}", "ECHEQ"),
                _action(reverse("treasury:pagos_efectivo_create") + f"?payable={payable.pk}", "Efectivo"),
            ]
        )
        if not payments.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
            actions.append(_action(reverse("treasury:cuentas_por_pagar_annul", args=[payable.pk]), "Anular"))
    extra_sections = [
        {"title": "Pagos registrados", "items": [_payment_item(payment) for payment in payments], "empty_label": "Todavia no hay pagos para esta obligacion."}
    ]
    return render(
        request,
        "treasury/detail_page.html",
        {
            "title": payable.concepto,
            "subtitle": f"{payable.proveedor.razon_social} - {payable.categoria.nombre}",
            "back_url": reverse("treasury:cuentas_por_pagar_list"),
            "section_label": badge,
            "section_label_class": badge_class,
            "fields": fields,
            "actions": actions,
            "extra_sections": extra_sections,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_por_pagar_create(request):
    _require_treasury_admin(request)
    form = PayableForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            payable = register_payable(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar la cuenta por pagar.")
        else:
            messages.success(request, "Cuenta por pagar guardada.")
            url = reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": "Nueva cuenta por pagar",
            "subtitle": "Obligacion financiera con proveedor y vencimiento.",
            "form": form,
            "submit_label": "Guardar cuenta",
            "back_url": reverse("treasury:cuentas_por_pagar_list"),
            "form_action": reverse("treasury:cuentas_por_pagar_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_por_pagar_update(request, payable_id: int):
    _require_treasury_admin(request)
    payable = get_object_or_404(CuentaPorPagar, pk=payable_id)
    form = PayableForm(request.POST or None, instance=payable)
    if request.method == "POST" and form.is_valid():
        try:
            payable = update_payable(payable=payable, actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo actualizar la cuenta por pagar.")
        else:
            messages.success(request, "Cuenta por pagar actualizada.")
            url = reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": f"Editar deuda: {payable.concepto}",
            "subtitle": "Solo se permite mientras no tenga pagos registrados.",
            "form": form,
            "submit_label": "Guardar cambios",
            "back_url": reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk]),
            "form_action": reverse("treasury:cuentas_por_pagar_update", args=[payable.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_por_pagar_annul(request, payable_id: int):
    _require_treasury_admin(request)
    payable = get_object_or_404(CuentaPorPagar, pk=payable_id)
    form = PayableAnnulForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            annul_payable(payable=payable, motivo=form.cleaned_data["motivo"], actor=request.user)
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo anular la cuenta por pagar.")
        else:
            messages.success(request, "Cuenta por pagar anulada.")
            return redirect("treasury:cuentas_por_pagar_detail", payable.pk)
    return _render_form(
        request,
        {
            "title": f"Anular deuda: {payable.concepto}",
            "subtitle": "La anulacion no borra historial y deja saldo en cero.",
            "form": form,
            "submit_label": "Confirmar anulacion",
            "back_url": reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk]),
            "form_action": reverse("treasury:cuentas_por_pagar_annul", args=[payable.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def compromisos_especiales_list(request):
    _require_treasury_admin(request)
    form = SpecialCommitmentFilterForm(request.GET or None)
    queryset = CompromisoEspecial.objects.select_related("cuenta_por_pagar", "sucursal").order_by(
        "vencimiento",
        "fecha_compromiso",
        "id",
    )
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        if not empresa_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(Q(sucursal__empresa_id__in=empresa_ids) | Q(sucursal__isnull=True))
    if form.is_valid():
        tipo = form.cleaned_data.get("tipo")
        estado = form.cleaned_data.get("estado")
        sucursal = form.cleaned_data.get("sucursal")
        fecha_desde = form.cleaned_data.get("fecha_desde")
        fecha_hasta = form.cleaned_data.get("fecha_hasta")
        if tipo:
            queryset = queryset.filter(tipo=tipo)
        if estado:
            queryset = queryset.filter(estado=estado)
        if sucursal:
            queryset = queryset.filter(sucursal=sucursal)
        if fecha_desde:
            queryset = queryset.filter(Q(vencimiento__gte=fecha_desde) | Q(vencimiento__isnull=True, fecha_compromiso__gte=fecha_desde))
        if fecha_hasta:
            queryset = queryset.filter(Q(vencimiento__lte=fecha_hasta) | Q(vencimiento__isnull=True, fecha_compromiso__lte=fecha_hasta))
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Compromisos especiales",
            "subtitle": "Impuestos, planes, embargos, adelantos y pagos excepcionales con sustento y autorizacion.",
            "items": [_special_commitment_item(item) for item in queryset],
            "create_url": reverse("treasury:compromisos_especiales_create"),
            "filter_form": form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def compromisos_especiales_create(request):
    _require_treasury_admin(request)
    form = SpecialCommitmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            commitment = register_special_commitment(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo guardar el compromiso especial.")
        else:
            messages.success(request, "Compromiso especial guardado.")
            url = reverse("treasury:compromisos_especiales_detail", args=[commitment.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": "Nuevo compromiso especial",
            "subtitle": "Clasifica impuestos, planes, embargos y autorizaciones sobre la deuda.",
            "form": form,
            "submit_label": "Guardar compromiso",
            "back_url": reverse("treasury:compromisos_especiales_list"),
            "form_action": reverse("treasury:compromisos_especiales_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def compromisos_especiales_detail(request, commitment_id: int):
    _require_treasury_admin(request)
    commitment = get_object_or_404(
        CompromisoEspecial.objects.select_related(
            "cuenta_por_pagar",
            "cuenta_por_pagar__proveedor",
            "sucursal",
            "autorizado_por",
            "creado_por",
        ),
        pk=commitment_id,
    )
    badge, badge_class = _special_commitment_badge(commitment)
    fields = [
        {"label": "Tipo", "value": commitment.get_tipo_display()},
        {"label": "Concepto", "value": commitment.concepto},
        {"label": "Sustento", "value": commitment.sustento_referencia},
        {"label": "Monto", "value": _money(commitment.monto_estimado)},
        {"label": "Estado", "value": badge},
        {"label": "Prioridad", "value": commitment.get_prioridad_display()},
        {"label": "Sucursal", "value": commitment.sucursal.nombre if commitment.sucursal_id else "Sin sucursal"},
        {"label": "Vencimiento", "value": commitment.vencimiento.strftime("%d/%m/%Y") if commitment.vencimiento else "Sin vencimiento"},
        {"label": "Organismo", "value": commitment.organismo or "No aplica"},
        {"label": "Beneficiario", "value": commitment.beneficiario or "No aplica"},
        {"label": "Expediente", "value": commitment.expediente or "No aplica"},
        {"label": "Periodo fiscal", "value": commitment.periodo_fiscal.strftime("%m/%Y") if commitment.periodo_fiscal else "No aplica"},
        {"label": "Plan", "value": commitment.plan_nombre or "No aplica"},
        {"label": "Cuota", "value": f"{commitment.numero_cuota}/{commitment.total_cuotas}" if commitment.numero_cuota else "No aplica"},
        {"label": "Capital", "value": _money(commitment.capital)},
        {"label": "Interes financiero", "value": _money(commitment.interes_financiero)},
        {"label": "Interes resarcitorio", "value": _money(commitment.interes_resarcitorio)},
        {"label": "Autorizado por", "value": str(commitment.autorizado_por) if commitment.autorizado_por else "Sin autorizacion"},
        {"label": "Comentario autorizacion", "value": commitment.comentario_autorizacion or "Sin comentario"},
    ]
    actions = []
    if commitment.cuenta_por_pagar_id:
        actions.append(_action(reverse("treasury:cuentas_por_pagar_detail", args=[commitment.cuenta_por_pagar_id]), "Ver deuda"))
    if commitment.requiere_autorizacion and commitment.estado == CompromisoEspecial.Estado.APROBACION_PENDIENTE:
        actions.append(_action(reverse("treasury:compromisos_especiales_decide", args=[commitment.pk]), "Autorizar", "primary"))
    return render(
        request,
        "treasury/detail_page.html",
        {
            "title": commitment.concepto,
            "subtitle": commitment.get_tipo_display(),
            "back_url": reverse("treasury:compromisos_especiales_list"),
            "section_label": badge,
            "section_label_class": badge_class,
            "fields": fields,
            "actions": actions,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def compromisos_especiales_decide(request, commitment_id: int):
    _require_treasury_admin(request)
    commitment = get_object_or_404(CompromisoEspecial, pk=commitment_id)
    form = SpecialCommitmentDecisionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            commitment = decide_special_commitment(
                commitment=commitment,
                aprobado=form.cleaned_data["decision"] == "approve",
                comentario=form.cleaned_data["comentario"],
                actor=request.user,
            )
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo registrar la decision.")
        else:
            messages.success(request, "Decision registrada.")
            return redirect("treasury:compromisos_especiales_detail", commitment.pk)
    return _render_form(
        request,
        {
            "title": f"Autorizar compromiso: {commitment.concepto}",
            "subtitle": "La decision queda auditada con usuario, fecha y comentario.",
            "form": form,
            "submit_label": "Registrar decision",
            "back_url": reverse("treasury:compromisos_especiales_detail", args=[commitment.pk]),
            "form_action": reverse("treasury:compromisos_especiales_decide", args=[commitment.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def pagos_list(request):
    _require_treasury_admin(request)
    form = PaymentFilterForm(request.GET or None)
    queryset = PagoTesoreria.objects.select_related("cuenta_por_pagar__proveedor", "cuenta_bancaria").order_by(
        "-fecha_pago", "-id"
    )
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        if not empresa_ids:
            queryset = queryset.none()
        else:
            queryset = queryset.filter(
                Q(cuenta_por_pagar__sucursal__empresa_id__in=empresa_ids)
                | Q(cuenta_por_pagar__sucursal__isnull=True)
            )
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        medio_pago = form.cleaned_data.get("medio_pago")
        bank_account = form.cleaned_data.get("cuenta_bancaria")
        estado = form.cleaned_data.get("estado")
        sucursal = form.cleaned_data.get("sucursal")
        if q:
            queryset = queryset.filter(
                Q(cuenta_por_pagar__proveedor__razon_social__icontains=q)
                | Q(cuenta_por_pagar__concepto__icontains=q)
                | Q(referencia__icontains=q)
            )
        if medio_pago:
            queryset = queryset.filter(medio_pago=medio_pago)
        if bank_account:
            queryset = queryset.filter(cuenta_bancaria=bank_account)
        if estado:
            queryset = queryset.filter(estado=estado)
        if sucursal:
            queryset = queryset.filter(cuenta_por_pagar__sucursal=sucursal)
    actions = [
        # Camino recomendado: elegir proveedor y pagar 1 o varias de sus facturas
        # de una sola vez. Los cuatro de abajo siguen para el pago de a uno.
        _action(reverse("treasury:pagos_proveedor_create"), "Pagar por proveedor", "primary"),
        _action(reverse("treasury:pagos_transferencia_create"), "Transferencia"),
        _action(reverse("treasury:pagos_cheque_create"), "Cheque"),
        _action(reverse("treasury:pagos_echeq_create"), "ECHEQ"),
        _action(reverse("treasury:pagos_efectivo_create"), "Efectivo"),
    ]
    return render(
        request,
        "treasury/list_page.html",
        {
            "title": "Pagos de tesoreria",
            "subtitle": "Trazabilidad completa de egresos administrativos.",
            "items": [_payment_item(item) for item in queryset],
            "actions": actions,
            "filter_form": form,
        },
    )


def _payment_form_initial(request):
    initial = {"fecha_pago": timezone.localdate()}
    payable_id = request.GET.get("payable")
    if payable_id and payable_id.isdigit():
        initial["cuenta_por_pagar"] = payable_id
    return initial


def _register_payment_view(request, form_class, service_func, title: str, subtitle: str):
    _require_treasury_admin(request)
    # empresa_ids acota las deudas y las cuentas bancarias ofrecidas a las
    # empresas seleccionadas: sin esto el desplegable mezclaba deudas de todas
    # las empresas (el listado de cuentas por pagar si filtraba).
    empresa_ids = _get_empresa_ids(request)
    form = form_class(
        request.POST or None,
        initial=_payment_form_initial(request),
        empresa_ids=empresa_ids,
    )
    if request.method == "POST" and form.is_valid():
        kwargs = {
            "payable": form.cleaned_data["cuenta_por_pagar"],
            "fecha_pago": form.cleaned_data["fecha_pago"],
            "monto": form.cleaned_data["monto"],
            "observaciones": form.cleaned_data.get("observaciones", ""),
            "token_alta": form.creation_token(),
            "actor": request.user,
        }
        # El pago en efectivo sale de la boveda de una empresa. Normalmente la
        # deduce de la sucursal de la deuda, pero muchas deudas se cargan sin
        # sucursal: si el usuario tiene una sola empresa habilitada, esa es la
        # respuesta y no hace falta molestarlo.
        if service_func is register_cash_payment and empresa_ids and len(empresa_ids) == 1:
            kwargs["empresa"] = empresa_ids[0]
        if "cuenta_bancaria" in form.cleaned_data:
            kwargs["bank_account"] = form.cleaned_data["cuenta_bancaria"]
        if "referencia" in form.cleaned_data:
            kwargs["referencia"] = form.cleaned_data.get("referencia", "")
        if form.cleaned_data.get("fecha_diferida"):
            kwargs["fecha_diferida"] = form.cleaned_data["fecha_diferida"]
        try:
            payment = service_func(**kwargs)
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar el pago.")
        else:
            messages.success(request, "Pago registrado.")
            url = reverse("treasury:pagos_detail", args=[payment.pk])
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)
    return _render_form(
        request,
        {
            "title": title,
            "subtitle": subtitle,
            "form": form,
            "submit_label": "Registrar pago",
            "back_url": reverse("treasury:pagos_list"),
            "form_action": request.path,
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_transferencia_create(request):
    return _register_payment_view(
        request,
        TransferPaymentForm,
        register_transfer_payment,
        "Pago por transferencia",
        "Deja trazabilidad del egreso administrativo y recalcula la deuda.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_cheque_create(request):
    return _register_payment_view(
        request,
        ChequePaymentForm,
        register_cheque_payment,
        "Pago por cheque",
        "Registra instrumento diferido con referencia obligatoria.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_echeq_create(request):
    return _register_payment_view(
        request,
        ECheqPaymentForm,
        register_echeq_payment,
        "Pago por ECHEQ",
        "Registra el pago electronico diferido con referencia obligatoria.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_efectivo_create(request):
    return _register_payment_view(
        request,
        CashPaymentForm,
        register_cash_payment,
        "Pago en efectivo",
        "Registra un egreso interno en caja central y recompone la deuda.",
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_proveedor_create(request):
    """Pago por proveedor: se elige el proveedor y se pagan 1 o VARIAS de sus
    facturas impagas en una sola operacion. Se registra un pago por factura, asi
    el seguimiento por factura queda igual que cargandolas de a una."""
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    back_url = reverse("treasury:pagos_list")

    proveedor_id = request.POST.get("proveedor") or request.GET.get("proveedor")
    proveedor = None
    if proveedor_id and str(proveedor_id).isdigit():
        proveedor = Proveedor.objects.filter(pk=proveedor_id).first()

    # Paso 1: elegir proveedor (solo los que tienen facturas impagas).
    if proveedor is None:
        picker = SupplierPickerForm(request.GET or None, empresa_ids=empresa_ids)
        return _render_form(
            request,
            {
                "title": "Pago por proveedor",
                "subtitle": "Elegí el proveedor y después tildá las facturas que vas a pagar.",
                "form": picker,
                "submit_label": "Ver facturas impagas",
                "back_url": back_url,
                "form_action": request.path,
                "form_method": "get",
            },
        )

    form = SupplierPaymentBatchForm(
        request.POST or None,
        proveedor=proveedor,
        empresa_ids=empresa_ids,
        initial={"fecha_pago": timezone.localdate()},
    )
    if request.method == "POST" and form.is_valid():
        try:
            pagos = register_supplier_payment_batch(
                proveedor=proveedor,
                lineas=form.lineas_seleccionadas(),
                bank_account=form.cleaned_data.get("cuenta_bancaria"),
                medio_pago=form.cleaned_data["medio_pago"],
                fecha_pago=form.cleaned_data["fecha_pago"],
                referencia=form.cleaned_data.get("referencia", ""),
                observaciones=form.cleaned_data.get("observaciones", ""),
                token_alta=form.creation_token(),
                actor=request.user,
            )
        except (ValidationError, IntegrityError) as error:
            _handle_operation_error(form, error, "No se pudo registrar el pago.")
        else:
            total = sum((pago.monto for pago in pagos), Decimal("0.00"))
            messages.success(
                request,
                f"{len(pagos)} factura(s) de {proveedor} pagadas por ${total}.",
            )
            url = reverse("treasury:pagos_list")
            return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return render(
        request,
        "treasury/supplier_payment_batch.html",
        {
            "title": f"Pagar facturas de {proveedor}",
            "subtitle": "Tildá una o varias facturas. El importe viene con el saldo completo y lo podés editar.",
            "form": form,
            "proveedor": proveedor,
            "back_url": reverse("treasury:pagos_proveedor_create"),
            "form_action": f"{request.path}?{urlencode({'proveedor': proveedor.pk})}",
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def pagos_detail(request, payment_id: int):
    _require_treasury_admin(request)
    payment = get_object_or_404(
        PagoTesoreria.objects.select_related(
            "cuenta_por_pagar__proveedor",
            "cuenta_por_pagar__categoria",
            "cuenta_bancaria",
            "creado_por",
            "anulado_por",
        ),
        pk=payment_id,
    )
    badge, badge_class = _payment_badge(payment)
    fields = [
        {"label": "Proveedor", "value": payment.cuenta_por_pagar.proveedor.razon_social},
        {"label": "Obligacion", "value": payment.cuenta_por_pagar.concepto},
        {"label": "Categoria", "value": payment.cuenta_por_pagar.categoria.nombre},
        {"label": "Cuenta de registro", "value": payment.cuenta_bancaria.nombre if payment.cuenta_bancaria_id else "Caja central"},
        {"label": "Medio de pago", "value": payment.get_medio_pago_display()},
        {"label": "Monto", "value": _money(payment.monto)},
        {"label": "Fecha de pago", "value": payment.fecha_pago.strftime("%d/%m/%Y")},
        {"label": "Fecha diferida", "value": payment.fecha_diferida.strftime("%d/%m/%Y") if payment.fecha_diferida else "No aplica"},
        {"label": "Referencia", "value": payment.referencia or "Sin referencia"},
        {"label": "Estado", "value": payment.get_estado_display()},
        {"label": "Estado bancario", "value": payment.get_estado_bancario_display()},
        {"label": "Creado por", "value": str(payment.creado_por) if payment.creado_por else "Sistema"},
        {"label": "Creado en", "value": payment.creado_en.strftime("%d/%m/%Y %H:%M")},
    ]
    if payment.actualizado_por and (payment.actualizado_por != payment.creado_por or payment.actualizado_en > (payment.creado_en + timezone.timedelta(seconds=1))):
        fields.append({"label": "Actualizado por", "value": str(payment.actualizado_por)})
        fields.append({"label": "Actualizado en", "value": payment.actualizado_en.strftime("%d/%m/%Y %H:%M")})
    
    if payment.estado == PagoTesoreria.Estado.ANULADO:
        fields.append({"label": "Anulado por", "value": str(payment.anulado_por) if payment.anulado_por else "N/A"})
        fields.append({"label": "Motivo anulacion", "value": payment.motivo_anulacion or "Sin motivo"})
    
    fields.append({"label": "Observaciones", "value": payment.observaciones or "Sin observaciones"})
    actions = [_action(reverse("treasury:cuentas_por_pagar_detail", args=[payment.cuenta_por_pagar_id]), "Ver deuda")]
    if payment.estado == PagoTesoreria.Estado.REGISTRADO and payment.movimiento_bancario_id:
        # US-4.11: la correccion vive en el movimiento bancario porque el
        # instrumento es uno solo aunque pague varias facturas.
        actions.append(
            _action(
                reverse("treasury:bank_movements_correct_method", args=[payment.movimiento_bancario_id]),
                "Corregir tipo de pago",
            )
        )
    if payment.estado == PagoTesoreria.Estado.REGISTRADO:
        actions.append(_action(reverse("treasury:pagos_annul", args=[payment.pk]), "Anular", "primary"))
    return render(
        request,
        "treasury/detail_page.html",
        {
            "title": f"{payment.get_medio_pago_display()} {_money(payment.monto)}",
            "subtitle": payment.cuenta_por_pagar.proveedor.razon_social,
            "back_url": reverse("treasury:pagos_list"),
            "section_label": badge,
            "section_label_class": badge_class,
            "fields": fields,
            "actions": actions,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def pagos_annul(request, payment_id: int):
    _require_treasury_admin(request)
    payment = get_object_or_404(PagoTesoreria, pk=payment_id)
    form = PaymentAnnulForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            annul_payment(payment=payment, motivo=form.cleaned_data["motivo"], actor=request.user)
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo anular el pago.")
        else:
            messages.success(request, "Pago anulado.")
            return redirect("treasury:pagos_detail", payment.pk)
    return _render_form(
        request,
        {
            "title": "Anular pago",
            "subtitle": "La anulacion conserva trazabilidad y recompone el saldo pendiente.",
            "form": form,
            "submit_label": "Confirmar anulacion",
            "back_url": reverse("treasury:pagos_detail", args=[payment.pk]),
            "form_action": reverse("treasury:pagos_annul", args=[payment.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


# --- Bank Movements & Conciliation (EP-04) ---

@login_required
def bank_movements_list(request):
    _require_treasury_admin(request)
    filter_form = BankMovementFilterForm(request.GET)
    movements = MovimientoBancario.objects.filter(
        estado=MovimientoBancario.Estado.REGISTRADO,
    ).select_related(
        "cuenta_bancaria",
        "cuenta_bancaria__sucursal",
        "creado_por",
        "categoria",
        "categoria__rubro_operativo",
        "rubro_operativo",
        "proveedor",
        "sucursal_gasto",
    )
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        movement_scope = bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
        if empresa_ids:
            movement_scope = movement_scope | Q(sucursal_gasto__empresa_id__in=empresa_ids)
        movements = movements.filter(movement_scope)

    # Backlog real de imputacion del contexto activo, antes de los filtros del
    # usuario: si no, filtrar por creditos mostraria "0 pendientes" enganoso.
    pending_imputation = movements.filter(
        tipo=MovimientoBancario.Tipo.DEBITO,
        estado=MovimientoBancario.Estado.REGISTRADO,
    ).exclude(clase=MovimientoBancario.Clase.RETIRO).filter(
        Q(rubro_operativo__isnull=True)
        | Q(sucursal_gasto__isnull=True)
        | Q(periodo_pago__isnull=True)
    ).aggregate(total=Sum("monto"), cantidad=Count("id"))
    pending_imputation_total = pending_imputation["total"] or Decimal("0.00")
    pending_imputation_count = pending_imputation["cantidad"] or 0

    if filter_form.is_valid():
        q = filter_form.cleaned_data.get("q")
        account = filter_form.cleaned_data.get("cuenta_bancaria")
        tipo = filter_form.cleaned_data.get("tipo")
        clase = filter_form.cleaned_data.get("clase")
        df = filter_form.cleaned_data.get("fecha_desde")
        dt = filter_form.cleaned_data.get("fecha_hasta")
        sucursal = filter_form.cleaned_data.get("sucursal")
        
        if q:
            search_filter = Q(concepto__icontains=q) | Q(referencia__icontains=q)
            try:
                search_filter |= Q(monto=Decimal(q.replace(",", ".")))
            except (InvalidOperation, ValueError):
                pass
            movements = movements.filter(search_filter)
        if account:
            movements = movements.filter(cuenta_bancaria=account)
        if tipo:
            movements = movements.filter(tipo=tipo)
        if clase:
            movements = movements.filter(clase=clase)
        if df:
            movements = movements.filter(fecha__gte=df)
        if dt:
            movements = movements.filter(fecha__lte=dt)
        if sucursal:
            movements = movements.filter(Q(cuenta_bancaria__sucursal=sucursal) | Q(sucursal_gasto=sucursal))
        imputacion = filter_form.cleaned_data.get("imputacion")
        if imputacion == "pendientes":
            movements = movements.filter(
                tipo=MovimientoBancario.Tipo.DEBITO,
                estado=MovimientoBancario.Estado.REGISTRADO,
            ).exclude(clase=MovimientoBancario.Clase.RETIRO).filter(
                Q(rubro_operativo__isnull=True)
                | Q(sucursal_gasto__isnull=True)
                | Q(periodo_pago__isnull=True)
            )
        elif imputacion == "imputados":
            movements = movements.filter(
                tipo=MovimientoBancario.Tipo.DEBITO,
                rubro_operativo__isnull=False,
                sucursal_gasto__isnull=False,
                periodo_pago__isnull=False,
            )

    bank_totals = movements.aggregate(
        creditos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.CREDITO)),
        debitos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.DEBITO)),
        egresos_tesoreria=Sum(
            "monto",
            filter=Q(
                tipo=MovimientoBancario.Tipo.DEBITO,
                origen=MovimientoBancario.Origen.EGRESO_TESORERIA,
                rubro_operativo__isnull=False,
                sucursal_gasto__isnull=False,
                periodo_pago__isnull=False,
            ),
        ),
    )
    total_creditos = bank_totals["creditos"] or Decimal("0.00")
    total_debitos = bank_totals["debitos"] or Decimal("0.00")
    total_egresos_tesoreria = bank_totals["egresos_tesoreria"] or Decimal("0.00")
    filtered_count = movements.count()

    items = []
    for m in movements[:50]:
        meta = (
            f"Origen: {m.get_origen_display()} | Ref: {m.referencia or '-'}"
            f" | Rubro: {_bank_movement_rubro_label(m)}"
        )
        if m.tipo == MovimientoBancario.Tipo.DEBITO:
            sucursal_label = m.sucursal_gasto.nombre if m.sucursal_gasto_id else "sin sucursal"
            periodo_label = m.periodo_pago.strftime("%m/%Y") if m.periodo_pago else "sin periodo"
            meta += f" | Sucursal: {sucursal_label} | Periodo: {periodo_label}"
            if (
                m.estado == MovimientoBancario.Estado.REGISTRADO
                and m.clase != MovimientoBancario.Clase.RETIRO
                and not (m.rubro_operativo_id and m.sucursal_gasto_id and m.periodo_pago)
            ):
                meta = f"PENDIENTE DE IMPUTAR | {meta}"
        items.append({
            "title": f"{m.get_clase_display()} - {m.concepto}",
            "subtitle": f"{m.fecha.strftime('%d/%m/%Y')} | {m.cuenta_bancaria}",
            "badge": _money(m.monto),
            "badge_class": "badge-success" if m.tipo == MovimientoBancario.Tipo.CREDITO else "badge-danger",
            "href": reverse("treasury:bank_movements_detail", args=[m.pk]),
            "meta": meta,
        })

    subtitle = "Egresos e ingresos reales en cuentas bancarias"
    if filtered_count > len(items):
        subtitle += f". Mostrando {len(items)} de {filtered_count} movimientos filtrados"

    return render(request, "treasury/list_page.html", {
        "title": "Movimientos Bancarios",
        "subtitle": subtitle,
        "filter_form": filter_form,
        "summaries": [
            {"label": "Total creditos del filtro", "value": _money(total_creditos), "badge_class": "badge-success"},
            {"label": "Total debitos del filtro", "value": _money(total_debitos), "badge_class": "badge-danger"},
            {
                "label": "Egresos bancarios de tesoreria",
                "value": _money(total_egresos_tesoreria),
                "small": "Con rubro, sucursal y periodo",
                "badge_class": "badge-info",
            },
            {
                "label": "Egresos pendientes de imputacion",
                "value": _money(pending_imputation_total),
                "small": f"{pending_imputation_count} movimiento{'s' if pending_imputation_count != 1 else ''} sin rubro, sucursal o periodo",
                "badge_class": "badge-warning" if pending_imputation_count else "badge-success",
            },
        ],
        "items": items,
        "empty_message": "No hay movimientos bancarios para los filtros aplicados.",
        "create_url": reverse("treasury:bank_movements_create"),
        "create_label": "Nuevo movimiento"
    })

@login_required
def bank_movements_create(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        form = BankMovementForm(request.POST)
        if form.is_valid():
            try:
                movement = create_bank_movement(**form.cleaned_data, actor=request.user)
                messages.success(request, "Movimiento registrado correctamente.")
                return redirect("treasury:bank_movements_detail", movement.pk)
            except ValidationError as error:
                _handle_operation_error(form, error, "No se pudo registrar el movimiento bancario.")
    else:
        form = BankMovementForm()

    return render(request, "treasury/form_page.html", {
        "title": "Registrar Movimiento Bancario",
        "form": form,
        "submit_label": "Guardar movimiento",
        "back_url": reverse("treasury:bank_movements_list"),
        "form_action": reverse("treasury:bank_movements_create"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def bank_movements_edit_confirm(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(MovimientoBancario, pk=pk)
    if not _bank_movement_can_be_manually_changed(movement):
        messages.error(request, "Este movimiento no se puede editar desde Banco porque no es un movimiento manual activo.")
        return redirect("treasury:bank_movements_detail", pk=movement.pk)
    if request.method == "POST":
        return redirect("treasury:bank_movements_update", pk=movement.pk)
    return render(
        request,
        "treasury/confirm_action.html",
        {
            "title": "Confirmar edición",
            "subtitle": f"{movement.concepto} - {_money(movement.monto)}",
            "question": "¿Seguro que quiere editar este movimiento?",
            "body": "La edición cambia el reflejo real del banco: saldos, reportes y disponibilidad se recalculan con los nuevos datos.",
            "post_url": reverse("treasury:bank_movements_edit_confirm", args=[movement.pk]),
            "confirm_label": "Sí, editar",
            "back_url": reverse("treasury:bank_movements_detail", args=[movement.pk]),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def bank_movements_update(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(MovimientoBancario, pk=pk)
    if not _bank_movement_can_be_manually_changed(movement):
        messages.error(request, "Este movimiento no se puede editar desde Banco porque no es un movimiento manual activo.")
        return redirect("treasury:bank_movements_detail", pk=movement.pk)
    form = BankMovementForm(request.POST or None, instance=movement)
    if request.method == "POST" and form.is_valid():
        try:
            movement = update_bank_movement(movement=movement, actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo editar el movimiento bancario.")
        else:
            messages.success(request, "Movimiento bancario editado. El saldo queda recalculado con los nuevos datos.")
            return redirect("treasury:bank_movements_detail", pk=movement.pk)
    return _render_form(
        request,
        {
            "title": "Editar movimiento bancario",
            "subtitle": "Los cambios impactan saldos bancarios, reportes y disponibilidad.",
            "form": form,
            "submit_label": "Guardar edición",
            "back_url": reverse("treasury:bank_movements_detail", args=[movement.pk]),
            "form_action": reverse("treasury:bank_movements_update", args=[movement.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def bank_movements_imputation(request, pk):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    movement_qs = MovimientoBancario.objects.select_related("cuenta_bancaria", "cuenta_bancaria__empresa")
    if empresa_ids is not None:
        movement_qs = movement_qs.filter(
            bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
        )
    movement = get_object_or_404(movement_qs, pk=pk)
    if (
        movement.estado != MovimientoBancario.Estado.REGISTRADO
        or movement.tipo != MovimientoBancario.Tipo.DEBITO
        or movement.clase == MovimientoBancario.Clase.RETIRO
    ):
        messages.error(request, "Solo se puede completar la imputacion de un egreso bancario registrado.")
        return redirect("treasury:bank_movements_detail", pk=movement.pk)
    if not movement.cuenta_bancaria.activa:
        messages.error(
            request,
            "La cuenta bancaria esta inactiva. Reactivala para completar la imputacion de este egreso.",
        )
        return redirect("treasury:bank_movements_detail", pk=movement.pk)
    form = BankMovementImputationForm(
        request.POST or None,
        instance=movement,
        empresa_ids=empresa_ids,
    )
    if request.method == "POST" and form.is_valid():
        try:
            movement = complete_bank_movement_imputation(
                movement=movement,
                rubro_operativo=form.cleaned_data["rubro_operativo"],
                sucursal_gasto=form.cleaned_data["sucursal_gasto"],
                periodo_pago=form.cleaned_data["periodo_pago"],
                actor=request.user,
            )
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo completar la imputacion del movimiento.")
        else:
            messages.success(
                request,
                "Imputacion completada. El egreso ya suma en la situacion economica por rubro y sucursal.",
            )
            return redirect("treasury:bank_movements_detail", pk=movement.pk)
    return _render_form(
        request,
        {
            "title": "Completar imputacion del egreso",
            "subtitle": (
                f"{movement.concepto} - {_money(movement.monto)}. "
                "Solo se completan rubro, sucursal y periodo; monto, fecha y cuenta no cambian. "
                "Si este debito corresponde al pago de una deuda ya cargada, usa Vincular a pago "
                "en lugar de imputarlo, para no contar el gasto dos veces en la lectura economica."
            ),
            "form": form,
            "submit_label": "Guardar imputacion",
            "back_url": reverse("treasury:bank_movements_detail", args=[movement.pk]),
            "form_action": reverse("treasury:bank_movements_imputation", args=[movement.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def bank_movements_correct_method(request, pk):
    """US-4.11: corregir el tipo financiero de un egreso que ya paga facturas.

    Es la salida al caso "me confundi de instrumento": antes habia que anular los
    pagos y volver a cargar todo, porque el boton Editar desaparece apenas el
    movimiento queda vinculado a un pago.
    """
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    movement_qs = MovimientoBancario.objects.select_related("cuenta_bancaria", "proveedor")
    if empresa_ids is not None:
        movement_qs = movement_qs.filter(
            bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
        )
    movement = get_object_or_404(movement_qs, pk=pk)
    detalle_url = reverse("treasury:bank_movements_detail", args=[movement.pk])
    pagos = list(movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).order_by("pk"))
    if not _bank_movement_can_correct_payment_method(movement, tiene_pagos=bool(pagos)):
        messages.error(
            request,
            "Solo se puede corregir el tipo de pago de un egreso vigente que paga facturas.",
        )
        return redirect(detalle_url)

    form = BankPaymentMethodCorrectionForm(
        request.POST or None,
        initial={"medio_pago": pagos[0].medio_pago, "referencia": movement.referencia},
    )
    if request.method == "POST" and form.is_valid():
        try:
            correct_bank_payment_method(
                bank_movement=movement,
                medio_pago=form.cleaned_data["medio_pago"],
                referencia=form.cleaned_data["referencia"],
                actor=request.user,
            )
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo corregir el tipo de pago.")
        else:
            messages.success(
                request,
                "Tipo de pago corregido. El movimiento y las facturas que paga quedan con el mismo medio.",
            )
            return redirect(detalle_url)
    facturas = "la factura que paga" if len(pagos) == 1 else f"las {len(pagos)} facturas que paga"
    return _render_form(
        request,
        {
            "title": "Corregir tipo de pago",
            "subtitle": (
                f"{movement.concepto} - {_money(movement.monto)}. Cambia el tipo financiero del "
                f"movimiento y el medio de pago de {facturas}. No cambia el importe, la fecha, la "
                "cuenta bancaria ni que facturas quedaron pagadas."
            ),
            "form": form,
            "submit_label": "Guardar correccion",
            "back_url": detalle_url,
            "form_action": reverse("treasury:bank_movements_correct_method", args=[movement.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def bank_movements_delete_confirm(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(MovimientoBancario, pk=pk)
    if not _bank_movement_can_be_manually_changed(movement):
        messages.error(request, "Este movimiento no se puede eliminar desde Banco porque no es un movimiento manual activo.")
        return redirect("treasury:bank_movements_detail", pk=movement.pk)
    form = BankMovementAnnulForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            movement = annul_bank_movement(
                movement=movement,
                motivo=form.cleaned_data["motivo"],
                actor=request.user,
            )
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo eliminar el movimiento bancario.")
        else:
            messages.success(request, "Movimiento bancario eliminado. El saldo queda recalculado sin este movimiento.")
            return redirect("treasury:bank_movements_detail", pk=movement.pk)
    return render(
        request,
        "treasury/confirm_action.html",
        {
            "title": "Confirmar eliminación",
            "subtitle": f"{movement.concepto} - {_money(movement.monto)}",
            "question": "¿Seguro que quiere eliminar este movimiento?",
            "body": "La eliminación descuenta este movimiento de saldos, reportes y disponibilidad. La auditoría queda guardada con el motivo.",
            "form": form,
            "post_url": reverse("treasury:bank_movements_delete_confirm", args=[movement.pk]),
            "confirm_label": "Sí, eliminar",
            "confirm_kind": "danger",
            "back_url": reverse("treasury:bank_movements_detail", args=[movement.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def bank_movements_detail(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(
        MovimientoBancario.objects.select_related(
            "cuenta_bancaria",
            "creado_por",
            "actualizado_por",
            "categoria",
            "categoria__rubro_operativo",
            "rubro_operativo",
            "proveedor",
            "sucursal_gasto",
        ),
        pk=pk,
    )
    
    fields = [
        {"label": "Fecha", "value": movement.fecha.strftime("%d/%m/%Y")},
        {"label": "Cuenta", "value": str(movement.cuenta_bancaria)},
        {"label": "Estado", "value": movement.get_estado_display()},
        {"label": "Tipo", "value": movement.get_tipo_display()},
        {"label": "Tipo financiero", "value": movement.get_clase_display()},
        {"label": "Monto", "value": _money(movement.monto)},
        {"label": "Rubro", "value": _bank_movement_rubro_label(movement)},
        {"label": "Proveedor", "value": movement.proveedor.razon_social if movement.proveedor_id else "No aplica"},
        {"label": "Sucursal", "value": movement.sucursal_gasto.nombre if movement.sucursal_gasto_id else "Sin asignar"},
        {"label": "Periodo", "value": movement.periodo_pago.strftime("%m/%Y") if movement.periodo_pago else "Sin periodo"},
        {"label": "Concepto", "value": movement.concepto},
        {"label": "Referencia", "value": movement.referencia or "Sin referencia"},
        {"label": "Origen", "value": movement.get_origen_display()},
        {"label": "Creado por", "value": str(movement.creado_por) if movement.creado_por else "Sistema"},
        {"label": "Creado en", "value": movement.creado_en.strftime("%d/%m/%Y %H:%M")},
    ]
    if movement.actualizado_por:
        fields.append({"label": "Actualizado por", "value": str(movement.actualizado_por)})
        fields.append({"label": "Actualizado en", "value": movement.actualizado_en.strftime("%d/%m/%Y %H:%M")})
    if movement.estado == MovimientoBancario.Estado.ANULADO:
        fields.append({"label": "Eliminado por", "value": str(movement.anulado_por) if movement.anulado_por else "Sistema"})
        fields.append({"label": "Eliminado en", "value": movement.anulado_en.strftime("%d/%m/%Y %H:%M") if movement.anulado_en else "-"})
        fields.append({"label": "Motivo de eliminación", "value": movement.motivo_anulacion})
    
    fields.append({"label": "Observaciones", "value": movement.observaciones or "Sin observaciones"})

    actions = []
    if _bank_movement_can_be_manually_changed(movement):
        actions.append(_action(reverse("treasury:bank_movements_edit_confirm", args=[movement.pk]), "Editar", "secondary"))
        actions.append(_action(reverse("treasury:bank_movements_delete_confirm", args=[movement.pk]), "Eliminar", "secondary"))
    pagos_vinculados = list(
        movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO)
        .select_related("cuenta_por_pagar", "cuenta_por_pagar__proveedor")
        .order_by("pk")
    )
    if _bank_movement_can_correct_payment_method(movement, tiene_pagos=bool(pagos_vinculados)):
        # US-4.11: reemplaza a "Editar" cuando el egreso ya paga facturas. Editar
        # de verdad esta bloqueado porque moveria monto, fecha y cuenta; esto solo
        # corrige con que instrumento se pago.
        actions.append(
            _action(
                reverse("treasury:bank_movements_correct_method", args=[movement.pk]),
                "Corregir tipo de pago",
                "secondary",
            )
        )
    sin_asignar = importe_sin_asignar_del_movimiento(movement)
    if (
        movement.estado == MovimientoBancario.Estado.REGISTRADO
        and sin_asignar > 0
        and movement.tipo == MovimientoBancario.Tipo.DEBITO
    ):
        # Dos caminos distintos y conviene que se lean como tales: "Pagar una
        # deuda" genera el pago solo desde esta transferencia (el camino nuevo),
        # y "Vincular a pago" asocia un pago que ya se cargo a mano.
        # US-4.10: siguen apareciendo mientras quede plata sin asignar, porque una
        # transferencia puede repartirse entre varias facturas.
        actions.append(
            _action(
                reverse("treasury:bank_movements_pay_debt", args=[movement.pk]),
                "Pagar una deuda" if not pagos_vinculados else "Asignar el resto a otra deuda",
                "primary",
            )
        )
        actions.append(_action(reverse("treasury:bank_movements_link", args=[movement.pk]), "Vincular a pago", "secondary"))
    if (
        movement.estado == MovimientoBancario.Estado.REGISTRADO
        and movement.tipo == MovimientoBancario.Tipo.DEBITO
        and movement.clase != MovimientoBancario.Clase.RETIRO
        and not (movement.rubro_operativo_id and movement.sucursal_gasto_id and movement.periodo_pago)
    ):
        actions.append(
            _action(reverse("treasury:bank_movements_imputation", args=[movement.pk]), "Completar imputacion", "primary")
        )

    extra_sections = []
    if pagos_vinculados:
        if len(pagos_vinculados) == 1 and sin_asignar <= 0:
            titulo = "Pago vinculado"
        else:
            titulo = (
                f"{len(pagos_vinculados)} factura{'s' if len(pagos_vinculados) > 1 else ''} "
                f"pagada{'s' if len(pagos_vinculados) > 1 else ''} con esta transferencia"
            )
            if sin_asignar > 0:
                titulo += f" - quedan {_money(sin_asignar)} sin asignar"
        extra_sections.append({
            "title": titulo,
            "items": [_payment_item(pago) for pago in pagos_vinculados],
        })

    return render(request, "treasury/detail_page.html", {
        "title": "Detalle de Movimiento",
        "subtitle": f"Ref: {movement.referencia or movement.id}",
        "fields": fields,
        "actions": actions,
        "extra_sections": extra_sections,
        "back_url": reverse("treasury:bank_movements_list"),
        "section_label": movement.get_tipo_display(),
        "section_label_class": (
            "badge-muted"
            if movement.estado == MovimientoBancario.Estado.ANULADO
            else "badge-success" if movement.tipo == MovimientoBancario.Tipo.CREDITO else "badge-danger"
        )
    })

@login_required
def bank_movements_pay_debt(request, pk):
    """Reparte una transferencia ya cargada entre una o varias facturas.

    US-4.10: el pago semanal de cuenta corriente sale en un solo monto y cubre
    facturas de proveedores distintos, asi que se listan TODAS las facturas
    impagas (no solo las que la transferencia alcanza a pagar enteras), con un
    filtro por proveedor y un importe por factura.

    Se elige cuanto va a cada una; la suma no puede pasar lo que le queda sin
    asignar a la transferencia. No se crea ningun debito nuevo: la transferencia
    sigue siendo un solo movimiento del extracto.
    """
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    movement = get_object_or_404(
        MovimientoBancario, pk=pk, estado=MovimientoBancario.Estado.REGISTRADO
    )
    detalle_url = reverse("treasury:bank_movements_detail", args=[movement.pk])
    if movement.tipo != MovimientoBancario.Tipo.DEBITO:
        messages.error(request, "Solo un debito puede pagar una deuda.")
        return redirect(detalle_url)

    sin_asignar = importe_sin_asignar_del_movimiento(movement)
    if sin_asignar <= 0:
        messages.error(request, "Esta transferencia ya esta asignada por completo.")
        return redirect(detalle_url)

    candidatas = open_payables_queryset(empresa_ids).select_related(
        "proveedor", "categoria", "sucursal", "caja_origen"
    )
    # Que no se ofrezcan facturas de la otra empresa: la cuenta bancaria manda.
    # En vista consolidada, `empresa_ids` trae las dos y sin este corte se podria
    # pagar una factura de ARMADI con una transferencia de MAPOGO. El servicio lo
    # rechaza igual; aca se evita que aparezca la opcion.
    empresa_de_la_cuenta = movement.cuenta_bancaria.empresa_id
    if empresa_de_la_cuenta:
        candidatas = candidatas.filter(
            Q(sucursal__empresa_id=empresa_de_la_cuenta) | Q(sucursal__isnull=True)
        )
    from cashops.models import Sucursal

    # Los combos se arman con el universo permitido (antes de los filtros), asi
    # que elegir un proveedor no hace desaparecer sucursales del selector.
    proveedores = Proveedor.objects.filter(
        pk__in=candidatas.values_list("proveedor_id", flat=True)
    ).order_by("razon_social")
    sucursales = Sucursal.objects.filter(
        pk__in=candidatas.values_list("sucursal_id", flat=True)
    ).order_by("codigo")

    # Tesoreria no paga facturas sueltas: paga la cuenta corriente de una semana
    # de un proveedor en una sucursal, y controla el subtotal contra su planilla
    # (una fila por proveedor / sucursal / fecha). Por eso los tres filtros y el
    # total de lo filtrado. El lapso corre sobre la fecha de factura, que es la
    # que anotan.
    filtro_proveedor = (request.GET.get("proveedor") or "").strip()
    proveedor_elegido = None
    if filtro_proveedor.isdigit():
        proveedor_elegido = proveedores.filter(pk=filtro_proveedor).first()
        if proveedor_elegido is not None:
            candidatas = candidatas.filter(proveedor=proveedor_elegido)

    filtro_sucursal = (request.GET.get("sucursal") or "").strip()
    sucursal_elegida = None
    if filtro_sucursal.isdigit():
        sucursal_elegida = sucursales.filter(pk=filtro_sucursal).first()
        if sucursal_elegida is not None:
            candidatas = candidatas.filter(sucursal=sucursal_elegida)

    desde = parse_date((request.GET.get("desde") or "").strip())
    hasta = parse_date((request.GET.get("hasta") or "").strip())
    if desde and hasta and desde > hasta:
        messages.error(request, "El desde no puede ser posterior al hasta.")
        desde = hasta = None
    if desde:
        candidatas = candidatas.filter(fecha_emision__gte=desde)
    if hasta:
        candidatas = candidatas.filter(fecha_emision__lte=hasta)

    # Lo que se manda en el form y en "Ver todas" para no perder el filtro.
    filtros_activos = {
        clave: valor
        for clave, valor in (
            ("proveedor", proveedor_elegido.pk if proveedor_elegido else None),
            ("sucursal", sucursal_elegida.pk if sucursal_elegida else None),
            ("desde", desde.isoformat() if desde else None),
            ("hasta", hasta.isoformat() if hasta else None),
        )
        if valor
    }

    pedir_confirmacion_duplicado = False
    if request.method == "POST":
        asignaciones = []
        errores = []
        elegidas = request.POST.getlist("payable_id")
        for payable_id in elegidas:
            factura = candidatas.filter(pk=payable_id).first()
            if factura is None:
                errores.append("Una de las facturas elegidas ya no esta disponible.")
                continue
            crudo = (request.POST.get(f"monto_{payable_id}") or "").strip()
            if not crudo:
                monto = min(factura.saldo_pendiente, sin_asignar)
            else:
                try:
                    monto = Decimal(crudo.replace(",", "."))
                except (InvalidOperation, ValueError):
                    errores.append(f"El importe de {factura.concepto} no es un numero.")
                    continue
            asignaciones.append((factura, monto))

        if not asignaciones and not errores:
            errores.append("Elegi al menos una factura.")

        # Mismo corte que en el pago por proveedor: los 10 pagos dobles de
        # produccion salieron de tildar las dos copias en la misma operacion.
        duplicados = lineas_que_parecen_la_misma_factura([f for f, _ in asignaciones])
        if duplicados and request.POST.get("confirmar_duplicado") != "1":
            detalle = "; ".join(
                " y ".join(f"#{p.pk} {p.concepto}" for p in lineas) for lineas in duplicados
            )
            errores.append(
                f"Estas marcando facturas que parecen la misma: {detalle}. Misma sucursal, "
                "misma fecha de factura y mismo importe. Desmarca una, o tilda «Son facturas "
                "distintas» y volve a enviar."
            )
            pedir_confirmacion_duplicado = True

        if not errores:
            try:
                pagos = pay_debts_from_bank_movement(
                    bank_movement=movement,
                    asignaciones=asignaciones,
                    actor=request.user,
                )
            except ValidationError as error:
                errores.extend(error.messages)
            else:
                queda = importe_sin_asignar_del_movimiento(
                    MovimientoBancario.objects.get(pk=movement.pk)
                )
                detalle = f"{len(pagos)} factura{'s' if len(pagos) > 1 else ''} pagada"
                detalle += "s" if len(pagos) > 1 else ""
                if queda > 0:
                    detalle += f". Quedan {_money(queda)} de la transferencia sin asignar"
                messages.success(request, f"{detalle}.")
                return redirect(detalle_url)
        for mensaje in errores:
            messages.error(request, mensaje)

    # Si el envio vuelve con error, las tildadas siguen tildadas: si no, para
    # confirmar un duplicado habria que marcar todo de nuevo.
    ya_elegidas = set(request.POST.getlist("payable_id")) if request.method == "POST" else set()
    facturas = [
        {
            "payable": factura,
            "sugerido": min(factura.saldo_pendiente, sin_asignar),
            "elegida": str(factura.pk) in ya_elegidas,
            # El sugerido se topea con lo que queda sin asignar de la
            # transferencia. Cuando eso es menos que el saldo, tildar la linea
            # tal cual viene deja la factura pagada a medias sin que nada lo
            # diga: paso en produccion con una factura de $33.000 precargada en
            # $21.750. Se avisa cuanto quedaria debiendo.
            "queda_debiendo": max(factura.saldo_pendiente - sin_asignar, Decimal("0.00")),
        }
        for factura in candidatas.order_by(
            "proveedor__razon_social", "sucursal__codigo", "fecha_emision", "pk"
        )
    ]
    # El total de lo filtrado es lo que tesoreria compara contra su planilla.
    total_filtrado = sum((fila["payable"].saldo_pendiente for fila in facturas), Decimal("0.00"))
    return render(
        request,
        "treasury/pay_debts_split.html",
        {
            "movement": movement,
            "sin_asignar": sin_asignar,
            "ya_asignado": importe_asignado_del_movimiento(movement),
            "facturas": facturas,
            "total_filtrado": total_filtrado,
            "pedir_confirmacion_duplicado": pedir_confirmacion_duplicado,
            "proveedores": proveedores,
            "proveedor_elegido": proveedor_elegido,
            "sucursales": sucursales,
            "sucursal_elegida": sucursal_elegida,
            "desde": desde,
            "hasta": hasta,
            "hay_filtro": bool(filtros_activos),
            "filtros_qs": urlencode(filtros_activos),
            "back_url": detalle_url,
            "money": _money,
        },
    )


@login_required
def bank_movements_link(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(MovimientoBancario, pk=pk, estado=MovimientoBancario.Estado.REGISTRADO)
    # Filter payments that are not linked yet, match the account and the amount
    payments = PagoTesoreria.objects.filter(
        cuenta_bancaria=movement.cuenta_bancaria,
        monto=movement.monto,
        estado=PagoTesoreria.Estado.REGISTRADO,
        movimiento_bancario__isnull=True
    ).select_related("cuenta_por_pagar__proveedor")

    if request.method == "POST":
        payment_id = request.POST.get("payment_id")
        if payment_id:
            payment = get_object_or_404(PagoTesoreria, pk=payment_id)
            try:
                link_payment_to_bank_movement(payment=payment, bank_movement=movement, actor=request.user)
                messages.success(request, "Vinculacion exitosa.")
                return redirect("treasury:bank_movements_detail", pk=movement.pk)
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
    
    items = []
    for p in payments:
        items.append({
            "title": f"{p.get_medio_pago_display()} - {p.cuenta_por_pagar.proveedor.razon_social}",
            "subtitle": f"Fecha: {p.fecha_pago.strftime('%d/%m/%Y')} | Ref: {p.referencia}",
            "badge": _money(p.monto),
            "href": "#", # No href, we use a form
            "id": p.pk
        })

    return render(request, "treasury/selection_page.html", {
        "title": "Vincular Pago a Movimiento",
        "subtitle": f"Movimiento: {movement.concepto} ({_money(movement.monto)})",
        "items": items,
        "post_url": reverse("treasury:bank_movements_link", args=[movement.pk]),
        "back_url": reverse("treasury:bank_movements_detail", args=[movement.pk])
    })


@login_required
def pos_batches_list(request):
    _require_treasury_admin(request)
    filter_form = PosBatchFilterForm(request.GET)
    batches = LotePOS.objects.all().select_related("cuenta_bancaria", "creado_por")
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        batches = batches.filter(bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__"))

    if filter_form.is_valid():
        q = filter_form.cleaned_data.get("q")
        account = filter_form.cleaned_data.get("cuenta_bancaria")
        df = filter_form.cleaned_data.get("fecha_desde")
        dt = filter_form.cleaned_data.get("fecha_hasta")
        sucursal = filter_form.cleaned_data.get("sucursal")
        
        if q:
            batches = batches.filter(Q(operador__icontains=q) | Q(terminal__icontains=q))
        if account:
            batches = batches.filter(cuenta_bancaria=account)
        if df:
            batches = batches.filter(fecha_lote__gte=df)
        if dt:
            batches = batches.filter(fecha_lote__lte=dt)
        if sucursal:
            batches = batches.filter(cuenta_bancaria__sucursal=sucursal)

    items = []
    for b in batches[:50]:
        items.append({
            "title": f"Lote {b.operador} {b.terminal}",
            "subtitle": f"Fecha: {b.fecha_lote.strftime('%d/%m/%Y')} | {b.cuenta_bancaria or 'Sin cuenta'}",
            "badge": _money(b.total_lote),
            "badge_class": "badge-info",
            "href": "#", # simplified detail
            "meta": f"Obs: {b.observaciones}"
        })

    return render(request, "treasury/list_page.html", {
        "title": "Lotes POS",
        "subtitle": "Registros de cierres de terminales de tarjeta",
        "filter_form": filter_form,
        "items": items,
        "create_url": reverse("treasury:pos_batches_create"),
        "create_label": "Nuevo lote"
    })

@login_required
def pos_batches_create(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        form = PosBatchForm(request.POST)
        if form.is_valid():
            try:
                create_pos_batch(**form.cleaned_data, actor=request.user)
                messages.success(request, "Lote registrado correctamente.")
                return redirect("treasury:pos_batches_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = PosBatchForm()

    return render(request, "treasury/form_page.html", {
        "title": "Registrar Lote POS",
        "form": form,
        "back_url": reverse("treasury:pos_batches_list")
    })


@login_required
def card_accreditations_list(request):
    _require_treasury_admin(request)
    filter_form = CardAccreditationFilterForm(request.GET)
    accreditations = AcreditacionTarjeta.objects.filter(
        movimiento_bancario__estado=MovimientoBancario.Estado.REGISTRADO,
    ).select_related("movimiento_bancario__cuenta_bancaria", "lote_pos")
    empresa_ids = _get_empresa_ids(request)
    if empresa_ids is not None:
        accreditations = accreditations.filter(
            bank_account_empresa_scope_query(empresa_ids, prefix="movimiento_bancario__cuenta_bancaria__")
        )

    if filter_form.is_valid():
        canal = filter_form.cleaned_data.get("canal")
        account = filter_form.cleaned_data.get("cuenta_bancaria")
        df = filter_form.cleaned_data.get("fecha_desde")
        dt = filter_form.cleaned_data.get("fecha_hasta")
        sucursal = filter_form.cleaned_data.get("sucursal")
        
        if canal:
            accreditations = accreditations.filter(canal__icontains=canal)
        if account:
            accreditations = accreditations.filter(movimiento_bancario__cuenta_bancaria=account)
        if df:
            accreditations = accreditations.filter(movimiento_bancario__fecha__gte=df)
        if dt:
            accreditations = accreditations.filter(movimiento_bancario__fecha__lte=dt)
        if sucursal:
            accreditations = accreditations.filter(movimiento_bancario__cuenta_bancaria__sucursal=sucursal)

    items = []
    for a in accreditations[:50]:
        if a.modo_registro == AcreditacionTarjeta.ModoRegistro.PERIODO and a.periodo_desde and a.periodo_hasta:
            alcance = f"Periodo {a.periodo_desde:%d/%m/%Y} a {a.periodo_hasta:%d/%m/%Y}"
        else:
            alcance = f"Dia {a.fecha_acreditacion:%d/%m/%Y}"
        items.append({
            "title": f"Acreditacion {a.canal}",
            "subtitle": f"Fecha: {a.fecha_acreditacion.strftime('%d/%m/%Y')} | {a.cuenta_bancaria}",
            "badge": _money(a.monto_acreditado),
            "badge_class": "badge-success",
            "href": "#",
            "meta": f"{alcance} | Neto: {_money(a.monto_acreditado)} | Descuentos: {_money(a.total_descuentos)}"
        })

    return render(request, "treasury/list_page.html", {
        "title": "Acreditaciones de Tarjeta",
        "subtitle": "Ingresos bancarios por ventas con tarjeta, con carga diaria o agrupada por periodo",
        "filter_form": filter_form,
        "items": items,
        "create_url": reverse("treasury:card_accreditations_register"),
        "create_label": "Registrar acreditación"
    })

@login_required
def card_accreditations_register(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        form = CardAccreditationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                descuentos = []
                if data.get("monto_descuentos"):
                    descuentos.append({
                        "tipo": DescuentoAcreditacion.Tipo.COMISION, # default
                        "monto": data["monto_descuentos"],
                        "descripcion": data["descripcion_descuentos"] or "Descuentos varios"
                    })
                
                register_card_accreditation(
                    cuenta_bancaria=data["cuenta_bancaria"],
                    fecha_acreditacion=data["fecha_acreditacion"],
                    monto_neto=data["monto_neto"],
                    canal=data["canal"],
                    referencia_externa=data["referencia_externa"],
                    lote_pos=data["lote_pos"],
                    modo_registro=data["modo_registro"],
                    periodo_desde=data.get("periodo_desde"),
                    periodo_hasta=data.get("periodo_hasta"),
                    descuentos=descuentos,
                    actor=request.user
                )
                messages.success(request, "Acreditacion registrada correctamente.")
                return redirect("treasury:card_accreditations_list")
            except ValidationError as error:
                _handle_operation_error(form, error, "No se pudo registrar la acreditación.")
    else:
        form = CardAccreditationForm()

    return render(request, "treasury/form_page.html", {
        "title": "Registrar Acreditacion Diaria o por Periodo",
        "form": form,
        "back_url": reverse("treasury:card_accreditations_list")
    })


@login_required
def bank_reconciliation(request):
    _require_treasury_admin(request)
    snapshot = None
    if request.GET.get("cuenta_bancaria"):
        form = BankReconciliationFilterForm(request.GET)
        if form.is_valid():
            snapshot = build_bank_reconciliation_snapshot(
                cuenta_bancaria=form.cleaned_data["cuenta_bancaria"],
                date_from=form.cleaned_data["fecha_desde"],
                date_to=form.cleaned_data["fecha_hasta"]
            )
    else:
        last_month = timezone.localdate() - timezone.timedelta(days=30)
        form = BankReconciliationFilterForm(initial={
            "fecha_desde": last_month,
            "fecha_hasta": timezone.localdate()
        })

    return render(request, "treasury/reconciliation_page.html", {
        "form": form,
        "snapshot": snapshot
    })


# --- Flujo de Disponibilidades (EP-05) ---

@login_required
def disponibilidades_report(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    form = DisponibilidadesFilterForm(request.GET or None, empresa_ids=empresa_ids)
    
    if form.is_valid():
        year = int(form.cleaned_data["year"])
        month = int(form.cleaned_data["month"])
        sucursal = form.cleaned_data.get("sucursal")
    else:
        today = timezone.localdate()
        year, month = today.year, today.month
        sucursal = None

    snapshot = build_disponibilidades_snapshot(year, month, sucursal=sucursal, empresa_ids=empresa_ids)

    # El cierre es por empresa: hay que decir cual. Se ofrecen las habilitadas y
    # se marca cuales ya cerraron este mes, para no dejar cerrar dos veces ni
    # cerrar la empresa equivocada en silencio.
    from cashops.models import Empresa

    empresas = Empresa.objects.filter(activa=True).order_by("nombre")
    if empresa_ids is not None:
        empresas = empresas.filter(pk__in=empresa_ids)
    ya_cerradas = set(
        CierreMensualTesoreria.objects.filter(
            mes=snapshot["first_day"], cerrado=True
        ).values_list("empresa_id", flat=True)
    )
    empresas_para_cierre = [
        {"pk": e.pk, "nombre": e.nombre, "cerrada": e.pk in ya_cerradas} for e in empresas
    ]

    return render(request, "treasury/disponibilidades_report.html", {
        "form": form,
        "snapshot": snapshot,
        "empresas_para_cierre": empresas_para_cierre,
        "title": "Flujo de Disponibilidades",
        "subtitle": f"Consolidado de Efectivo y Bancos - {snapshot['first_day']:%m/%Y}" if not sucursal else f"Sucursal: {sucursal.nombre} - {snapshot['first_day']:%m/%Y}",
        "reset_url": reverse("cashops:reset_operational_data") if settings.ENABLE_DANGER_RESET else "",
    })


@login_required
def central_cash_movements(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    form = DisponibilidadesFilterForm(request.GET or None, empresa_ids=empresa_ids, include_imputacion=True)
    if form.is_valid():
        year = int(form.cleaned_data["year"])
        month = int(form.cleaned_data["month"])
        sucursal = form.cleaned_data.get("sucursal")
        imputacion = form.cleaned_data.get("imputacion") or ""
    else:
        today = timezone.localdate()
        year, month = today.year, today.month
        sucursal = None
        imputacion = ""

    first_day = timezone.datetime(year, month, 1).date()
    if month == 12:
        next_month = timezone.datetime(year + 1, 1, 1).date()
    else:
        next_month = timezone.datetime(year, month + 1, 1).date()
    last_day = next_month - timezone.timedelta(days=1)

    period_movements = scope_central_cash_movements(
        MovimientoCajaCentral.objects.filter(fecha__range=(first_day, last_day)).select_related(
            "pago_tesoreria",
            "creado_por",
            "caja_central__empresa",
            "rubro_operativo",
            "sucursal_gasto",
            "sucursal_origen",
        ),
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    imputed_admin_expenses = period_movements.filter(
        tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
        rubro_operativo__isnull=False,
        sucursal_gasto__isnull=False,
        periodo_pago__isnull=False,
    )
    imputed_admin_total = imputed_admin_expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    imputed_admin_count = imputed_admin_expenses.count()

    def _filtrar_imputacion(queryset):
        if imputacion == "pendientes":
            return queryset.filter(
                tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            ).filter(
                Q(rubro_operativo__isnull=True)
                | Q(sucursal_gasto__isnull=True)
                | Q(periodo_pago__isnull=True)
            )
        if imputacion == "imputados":
            return queryset.filter(
                tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                rubro_operativo__isnull=False,
                sucursal_gasto__isnull=False,
                periodo_pago__isnull=False,
            )
        return queryset

    movements = _filtrar_imputacion(period_movements)
    totals = movements.aggregate(
        ingresos=Sum("monto", filter=Q(tipo__in=CENTRAL_CASH_IN_TYPES)),
        egresos=Sum("monto", filter=Q(tipo__in=CENTRAL_CASH_OUT_TYPES)),
    )
    total_ingresos = totals["ingresos"] or Decimal("0.00")
    total_egresos = totals["egresos"] or Decimal("0.00")
    # El LISTADO incluye los anulados (con su motivo) aunque los totales de
    # arriba no los cuenten: si se ocultaran, quien anulo no podria ver que anulo.
    movements_listado = _filtrar_imputacion(
        scope_central_cash_movements(
            MovimientoCajaCentral.objects.filter(fecha__range=(first_day, last_day)).select_related(
                "pago_tesoreria",
                "creado_por",
                "caja_central__empresa",
                "rubro_operativo",
                "sucursal_gasto",
                "sucursal_origen",
            ),
            sucursal=sucursal,
            empresa_ids=empresa_ids,
            incluir_anulados=True,
        )
    )
    
    puede_anular = can_delete_central_cash_movement(request.user)
    # El listado cortaba en 100 sin forma de llegar a los que quedaban afuera.
    # Como el orden es por fecha descendente, en un mes con mas de 100
    # movimientos los primeros dias quedaban INALCANZABLES: tesoreria filtraba
    # junio, veia del 26 en adelante y no podia anular un movimiento del 02/06.
    # El filtro por mes ya acota el volumen, asi que se listan todos; el tope
    # alto es un freno de seguridad y cuando actua se avisa.
    TOPE_LISTADO = 500
    movimientos_del_mes = list(movements_listado.order_by("-fecha", "-id")[:TOPE_LISTADO])
    total_listado = movements_listado.count()
    items = []
    for m in movimientos_del_mes:
        # Simplistic: INGRESO/APORTE/RETIRO_BANCO are positive for cash
        if m.tipo in CENTRAL_CASH_IN_TYPES:
            badge_class = "badge-success"
            prefix = "+"
        else:
            badge_class = "badge-danger"
            prefix = "-"

        # La sucursal sale del movimiento, nunca de la boveda: un egreso la
        # trae en sucursal_gasto y un ingreso en sucursal_origen. El fallback a
        # caja_central.sucursal ya no sirve, porque la boveda es de la empresa.
        sucursal_label = "sin sucursal imputada"
        if m.sucursal_gasto_id:
            sucursal_label = m.sucursal_gasto.nombre
        elif m.sucursal_origen_id:
            sucursal_label = m.sucursal_origen.nombre
        rubro_label = m.rubro_operativo.nombre if m.rubro_operativo_id else "sin rubro"
        if m.periodo_pago:
            periodo_label = f"{m.periodo_pago:%m/%Y}"
        elif m.tipo == MovimientoCajaCentral.Tipo.INGRESO_CAJA:
            periodo_label = f"{m.fecha:%m/%Y} por fecha de caja"
        else:
            periodo_label = "sin periodo"
        usuario_label = m.creado_por.get_username() if m.creado_por_id else "sin usuario"

        item = {
            "title": f"{m.get_tipo_display()}",
            "subtitle": f"{m.fecha:%d/%m/%Y} - {m.concepto}",
            "badge": f"{prefix}{_money(m.monto)}",
            "badge_class": badge_class,
            "meta": (
                f"Sucursal: {sucursal_label} | Rubro: {rubro_label} | "
                f"Periodo: {periodo_label} | Usuario: {usuario_label}"
            ),
        }
        # Los anulados SE SIGUEN MOSTRANDO, con su motivo: si se ocultaran, quien
        # anulo no podria ver que anulo. Lo que no hacen es sumar en los totales.
        if m.esta_anulado:
            item["title"] = f"{m.get_tipo_display()} (anulado)"
            item["badge_class"] = "badge-muted"
            item["meta"] = f"{item['meta']} | Anulado: {m.motivo_anulacion}"
        elif puede_anular and is_central_cash_movement_annullable(m):
            item["action_href"] = reverse("treasury:central_cash_annul_confirm", args=[m.pk])
            item["action_label"] = "Anular"
        items.append(item)
        
    # El saldo que se muestra es el del alcance que el usuario acaba de filtrar.
    # Antes se imprimia el saldo de una sola caja mientras se listaban los
    # movimientos de todas: en produccion eso mostraba -$61.826.287,87 al lado de
    # una lista que sumaba otra cosa. Y de paso un GET ya no crea ninguna caja.
    saldo_del_alcance = _central_cash_balance_until(
        reference_date=last_day, sucursal=sucursal, empresa_ids=empresa_ids
    )
    subtitle = (
        f"Periodo {first_day:%m/%Y}. Ingresos: {_money(total_ingresos)}. "
        f"Egresos: {_money(total_egresos)}. Saldo acumulado: {_money(saldo_del_alcance)}."
    )
    if imputacion == "pendientes":
        subtitle += " Mostrando solo egresos administrativos con sucursal, rubro o periodo pendiente."
    elif imputacion == "imputados":
        subtitle += " Mostrando solo egresos administrativos completos para lectura economica."
    if total_listado > len(movimientos_del_mes):
        subtitle += (
            f" Atencion: el periodo tiene {total_listado} movimientos y se muestran los "
            f"{len(movimientos_del_mes)} mas recientes. Filtra por sucursal para ver el resto."
        )
    return render(request, "treasury/list_page.html", {
        "title": "Libro de Efectivo Central",
        "filter_form": form,
        "subtitle": subtitle,
        "summaries": [
            {"label": "Total ingresos del filtro", "value": _money(total_ingresos), "badge_class": "badge-success"},
            {"label": "Total egresos del filtro", "value": _money(total_egresos), "badge_class": "badge-danger"},
            {
                "label": "Gastos de tesoreria imputados",
                "value": _money(imputed_admin_total),
                "small": f"{imputed_admin_count} movimientos con rubro, sucursal y periodo",
                "badge_class": "badge-info",
            },
        ],
        "items": items,
        "actions": [
            {"label": "Cargar saldo inicial", "href": reverse("treasury:carga_inicial_caja_central"), "kind": "secondary"},
            {"label": "Registrar egreso", "href": reverse("treasury:egreso_tesoreria_create"), "kind": "secondary"},
            {"label": "Movimiento manual", "href": reverse("treasury:central_cash_create"), "kind": "primary"},
        ],
    })


@login_required
def central_cash_create(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    if request.method == "POST":
        form = CentralCashMovementForm(request.POST, empresa_ids=empresa_ids)
        if form.is_valid():
            try:
                register_central_cash_movement(
                    empresa=form.cleaned_data["empresa"],
                    tipo=form.cleaned_data["tipo"],
                    monto=form.cleaned_data["monto"],
                    concepto=form.cleaned_data["concepto"],
                    fecha=form.cleaned_data["fecha"],
                    observaciones=form.cleaned_data["observaciones"],
                    token_alta=form.creation_token(),
                    actor=request.user
                )
                messages.success(request, "Movimiento registrado correctamente.")
                return redirect("treasury:central_cash_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = CentralCashMovementForm(empresa_ids=empresa_ids)

    return render(request, "treasury/form_page.html", {
        "title": "Nuevo Movimiento de Efectivo",
        "form": form,
        "back_url": reverse("treasury:central_cash_list")
    })


@login_required
@require_http_methods(["GET", "POST"])
def carga_inicial_caja_central(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    if request.method == "POST":
        form = CargaInicialCajaCentralForm(request.POST, empresa_ids=empresa_ids)
        if form.is_valid():
            try:
                register_carga_inicial_caja_central(
                    empresa=form.cleaned_data["empresa"],
                    fecha=form.cleaned_data["fecha"],
                    monto=form.cleaned_data["monto"],
                    motivo=form.cleaned_data["motivo"],
                    observaciones=form.cleaned_data["observaciones"],
                    token_alta=form.creation_token(),
                    actor=request.user,
                )
                messages.success(request, "Carga inicial de caja fuerte registrada y auditada.")
                return redirect(reverse("treasury:central_cash_list"))
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = CargaInicialCajaCentralForm(empresa_ids=empresa_ids)
    return render(request, "treasury/form_page.html", {
        "title": "Carga inicial de caja fuerte central",
        "subtitle": "Registra o ajusta el saldo inicial de efectivo de tesoreria. Queda auditado con fecha, usuario y motivo. No requiere una caja operativa abierta.",
        "form": form,
        "back_url": reverse("treasury:central_cash_list"),
    })


@login_required
@require_http_methods(["GET", "POST"])
def central_cash_annul_confirm(request, pk):
    """Anula un movimiento de la boveda cargado por error, con motivo.

    Reemplaza la practica de compensarlo con un ajuste positivo a mano, que es
    lo que venia haciendo administracion por no tener esta pantalla.
    """
    _require_treasury_admin(request)
    ensure_delete_central_cash_movement(request.user)
    movement = get_object_or_404(
        MovimientoCajaCentral.objects.select_related("caja_central__empresa"), pk=pk
    )
    if not is_central_cash_movement_annullable(movement):
        messages.error(
            request,
            "Este movimiento no se puede anular desde aca: lo genero otro proceso "
            "o pertenece a un mes ya cerrado.",
        )
        return redirect("treasury:central_cash_list")
    form = CentralCashMovementAnnulForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            annul_central_cash_movement(
                movement=movement,
                motivo=form.cleaned_data["motivo"],
                actor=request.user,
            )
        except ValidationError as error:
            _handle_operation_error(form, error, "No se pudo anular el movimiento.")
        else:
            messages.success(
                request,
                "Movimiento anulado. El saldo de la caja fuerte queda recalculado sin este movimiento.",
            )
            return redirect("treasury:central_cash_list")
    return render(
        request,
        "treasury/confirm_action.html",
        {
            "title": "Anular movimiento de caja fuerte",
            "subtitle": f"{movement.concepto} - {_money(movement.monto)}",
            "question": "¿Seguro que querés anular este movimiento?",
            "body": (
                "El movimiento deja de sumar en el saldo de la caja fuerte, en el arqueo y en "
                "los reportes. No se borra: queda con el motivo, tu usuario y la fecha."
            ),
            "form": form,
            "post_url": reverse("treasury:central_cash_annul_confirm", args=[movement.pk]),
            "confirm_label": "Sí, anular",
            "confirm_kind": "danger",
            "back_url": reverse("treasury:central_cash_list"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def egreso_tesoreria_create(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    if request.method == "POST":
        form = EgresoTesoreriaForm(request.POST, empresa_ids=empresa_ids)
        if form.is_valid():
            try:
                register_egreso_tesoreria(
                    empresa=form.cleaned_data["empresa"],
                    fuente=form.cleaned_data["fuente"],
                    fecha=form.cleaned_data["fecha"],
                    monto=form.cleaned_data["monto"],
                    concepto=form.cleaned_data["concepto"],
                    cuenta_bancaria=form.cleaned_data.get("cuenta_bancaria"),
                    observaciones=form.cleaned_data["observaciones"],
                    rubro=form.cleaned_data.get("rubro"),
                    sucursal=form.cleaned_data.get("sucursal"),
                    periodo=form.cleaned_data.get("periodo"),
                    token_alta=form.creation_token(),
                    actor=request.user,
                )
                messages.success(request, "Egreso administrativo de tesoreria registrado.")
                return redirect(reverse("treasury:central_cash_list"))
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = EgresoTesoreriaForm(empresa_ids=empresa_ids)
    return render(request, "treasury/form_page.html", {
        "title": "Egreso administrativo de tesoreria",
        "subtitle": "Pagos y gastos que salen directamente de tesorería (no de una caja operativa de sucursal). Si el origen es caja fuerte, reduce el libro de efectivo central. Si es banco, impacta el libro bancario.",
        "form": form,
        # El egreso administrativo NO cancela ninguna deuda: no existe circuito
        # entre esta pantalla y cuentas por pagar. Si el gasto ya esta cargado
        # como deuda, la lectura economica lo cuenta dos veces (la deuda suma
        # cuando se carga, y este egreso vuelve a sumar) y ademas la factura
        # sigue figurando impaga.
        "aviso": (
            "Usá esta pantalla solo para gastos que NO estén cargados como deuda "
            "(alquileres, sueldos, impuestos). Si la factura ya la cargaron los "
            "chicos, pagala desde Pagos: si hacés el egreso, el gasto queda "
            "contado dos veces y la factura sigue figurando impaga."
        ),
        "aviso_url": reverse("treasury:pagos_proveedor_create"),
        "aviso_url_label": "Ir a pagar por proveedor",
        "back_url": reverse("treasury:central_cash_list"),
    })


@login_required
def arqueo_list(request):
    _require_treasury_admin(request)
    arqueos = ArqueoDisponibilidades.objects.all().select_related("creado_por")
    
    items = []
    for a in arqueos[:50]:
        diff = a.diferencia
        items.append({
            "title": f"Arqueo {a.fecha:%d/%m/%Y %H:%M}",
            "subtitle": f"Contado: {_money(a.saldo_contado_efectivo)} | Sistema: {_money(a.saldo_sistema_efectivo)}",
            "badge": _money(diff),
            "badge_class": "badge-danger" if diff < 0 else ("badge-success" if diff > 0 else "badge-muted"),
            "meta": a.observaciones or f"Auditado por {a.creado_por}"
        })
        
    return render(request, "treasury/list_page.html", {
        "title": "Arqueos de Disponibilidades",
        "subtitle": "Auditorias de saldo fisico vs sistema",
        "items": items,
        "create_url": reverse("treasury:arqueo_create"),
        "create_label": "Nuevo arqueo"
    })


@login_required
def arqueo_create(request):
    _require_treasury_admin(request)
    empresa_ids = _get_empresa_ids(request)
    if request.method == "POST":
        form = ArqueoForm(request.POST, empresa_ids=empresa_ids)
        if form.is_valid():
            try:
                register_arqueo(
                    caja_central=get_boveda(form.cleaned_data["empresa"]),
                    saldo_contado=form.cleaned_data["saldo_contado_efectivo"],
                    observaciones=form.cleaned_data["observaciones"],
                    actor=request.user
                )
            except ValidationError as e:
                form.add_error(None, e)
            else:
                messages.success(request, "Arqueo registrado correctamente.")
                return redirect("treasury:arqueo_list")
    else:
        form = ArqueoForm(empresa_ids=empresa_ids)

    return render(request, "treasury/form_page.html", {
        "title": "Realizar Arqueo de Efectivo",
        "subtitle": (
            "Se cuenta el efectivo de la boveda de una empresa y el sistema calcula la "
            "diferencia contra su saldo. Elegi la empresa que estas contando."
        ),
        "form": form,
        "back_url": reverse("treasury:arqueo_list")
    })


@login_required
def close_month_action(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        year = int(request.POST.get("year"))
        month = int(request.POST.get("month"))
        # Cada empresa cierra su mes. Si el usuario tiene mas de una habilitada,
        # cierra la que esta viendo; el form manda la empresa explicita.
        empresa_ids = _get_empresa_ids(request)
        empresa_id = request.POST.get("empresa") or (empresa_ids[0] if empresa_ids else None)
        try:
            if not empresa_id:
                raise ValidationError("Elegi la empresa cuyo mes queres cerrar.")
            close_treasury_month(year, month, empresa=int(empresa_id), actor=request.user)
            messages.success(request, f"Periodo {month}/{year} cerrado correctamente.")
        except ValidationError as e:
            # str(ValidationError) devuelve repr(list(...)): el usuario veria el
            # mensaje entre corchetes y comillas. Mostramos el texto limpio.
            messages.error(request, " ".join(e.messages))
            
    return redirect(request.META.get('HTTP_REFERER', reverse('treasury:disponibilidades')))
