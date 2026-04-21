from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import (
    ArqueoForm,
    BankAccountFilterForm,
    BankAccountForm,
    BankMovementFilterForm,
    BankMovementForm,
    BankReconciliationFilterForm,
    CardAccreditationFilterForm,
    CardAccreditationForm,
    CashPaymentForm,
    CentralCashMovementForm,
    ChequePaymentForm,
    DisponibilidadesFilterForm,
    ECheqPaymentForm,
    PayableAnnulForm,
    PayableCategoryFilterForm,
    PayableCategoryForm,
    PayableFilterForm,
    PayableForm,
    PaymentAnnulForm,
    PaymentFilterForm,
    PosBatchFilterForm,
    PosBatchForm,
    SupplierFilterForm,
    SupplierForm,
    SupplierHistoryFilterForm,
    TreasuryDashboardFilterForm,
    TransferPaymentForm,
)
from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from .permissions import ensure_treasury_admin
from .services import (
    annul_payable,
    annul_payment,
    build_bank_reconciliation_snapshot,
    build_economic_period_snapshot,
    build_disponibilidades_snapshot,
    build_financial_period_snapshot,
    build_supplier_history_snapshot,
    build_treasury_dashboard_snapshot,
    close_treasury_month,
    create_bank_account,
    create_bank_movement,
    create_payable_category,
    create_pos_batch,
    create_supplier,
    get_or_create_default_caja_central,
    link_payment_to_bank_movement,
    register_arqueo,
    register_card_accreditation,
    register_cash_payment,
    register_central_cash_movement,
    register_cheque_payment,
    register_echeq_payment,
    register_payable,
    register_transfer_payment,
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


def _require_treasury_admin(request) -> None:
    if not request.user.is_authenticated:
        raise PermissionDenied("Debes iniciar sesion.")
    ensure_treasury_admin(request.user)


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


def _money(value) -> str:
    value = value or Decimal("0.00")
    formatted = f"{value:,.2f}"
    return f"$ {formatted.replace(',', '_').replace('.', ',').replace('_', '.')}"


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


def _supplier_item(supplier: Proveedor) -> dict:
    meta_bits = [supplier.contacto or "", supplier.telefono or "", supplier.email or ""]
    return {
        "href": reverse("treasury:proveedores_detail", args=[supplier.pk]),
        "title": supplier.razon_social,
        "subtitle": supplier.identificador_fiscal or "Sin identificador fiscal",
        "badge": "Activo" if supplier.activo else "Inactivo",
        "badge_class": "badge-success" if supplier.activo else "badge-muted",
        "meta": " · ".join(bit for bit in meta_bits if bit),
    }


def _category_item(category: CategoriaCuentaPagar) -> dict:
    return {
        "href": reverse("treasury:categorias_update", args=[category.pk]),
        "title": category.nombre,
        "subtitle": f"Rubro: {category.rubro_label}",
        "badge": "Activa" if category.activo else "Inactiva",
        "badge_class": "badge-success" if category.activo else "badge-muted",
        "meta": "",
    }


def _bank_account_item(bank_account: CuentaBancaria) -> dict:
    return {
        "href": reverse("treasury:cuentas_bancarias_update", args=[bank_account.pk]),
        "title": bank_account.nombre,
        "subtitle": f"{bank_account.banco} · {bank_account.get_tipo_cuenta_display()}",
        "badge": "Activa" if bank_account.activa else "Inactiva",
        "badge_class": "badge-success" if bank_account.activa else "badge-muted",
        "meta": bank_account.alias or bank_account.cbu or bank_account.numero_cuenta,
    }


def _payable_item(payable: CuentaPorPagar) -> dict:
    badge, badge_class = _payable_badge(payable)
    return {
        "href": reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk]),
        "title": payable.proveedor.razon_social,
        "subtitle": f"{payable.concepto} · {payable.categoria.nombre}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": f"Vence {payable.fecha_vencimiento:%d/%m/%Y} · Pendiente {_money(payable.saldo_pendiente)}",
    }


def _payment_item(payment: PagoTesoreria) -> dict:
    badge, badge_class = _payment_badge(payment)
    return {
        "href": reverse("treasury:pagos_detail", args=[payment.pk]),
        "title": payment.cuenta_por_pagar.proveedor.razon_social,
        "subtitle": f"{payment.get_medio_pago_display()} · {payment.cuenta_por_pagar.concepto}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": f"{payment.fecha_pago:%d/%m/%Y} · {_money(payment.monto)} · {payment.cuenta_bancaria.nombre}",
    }


def _action(url: str, label: str, kind: str = "secondary") -> dict:
    return {"href": url, "label": label, "kind": kind}


def _payable_item(payable: CuentaPorPagar) -> dict:
    badge, badge_class = _payable_badge(payable)
    return {
        "href": reverse("treasury:cuentas_por_pagar_detail", args=[payable.pk]),
        "title": payable.proveedor.razon_social,
        "subtitle": f"{payable.concepto} - Rubro {payable.categoria.rubro_label}",
        "badge": badge,
        "badge_class": badge_class,
        "meta": (
            f"Periodo {payable.periodo_referencia:%m/%Y} - "
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
    if filter_form.is_valid():
        sucursal = filter_form.cleaned_data.get("sucursal")
        date_from = filter_form.cleaned_data.get("fecha_desde") or first_day_of_month
        date_to = filter_form.cleaned_data.get("fecha_hasta") or today
    else:
        sucursal = None
        date_from = first_day_of_month
        date_to = today

    snapshot = build_financial_period_snapshot(date_from=date_from, date_to=date_to, sucursal=sucursal)
    economic_snapshot = build_economic_period_snapshot(date_from=date_from, date_to=date_to, sucursal=sucursal)

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

    sucursales = Sucursal.objects.all()

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
            "due_today_payables": snapshot["due_today_payables"],
            "overdue_payables": snapshot["overdue_payables"],
            "upcoming_payables": snapshot["upcoming_payables"],
            "recent_payments": snapshot["recent_payments"],
            "recent_batches": snapshot["recent_batches"],
            "recent_movements": snapshot["recent_movements"],
            "money": _money,
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
    queryset = CategoriaCuentaPagar.objects.order_by("nombre")
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
            "title": "Categorias de deuda",
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
    category = toggle_payable_category(category=category, actor=request.user)
    messages.success(request, f"Categoria {category.nombre} {'activada' if category.activo else 'desactivada'}.")
    return redirect("treasury:categorias_list")


@login_required
def cuentas_bancarias_list(request):
    _require_treasury_admin(request)
    form = BankAccountFilterForm(request.GET or None)
    queryset = CuentaBancaria.objects.order_by("banco", "nombre")
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
            "filter_form": form,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def cuentas_bancarias_create(request):
    _require_treasury_admin(request)
    form = BankAccountForm(request.POST or None)
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
    bank_account = get_object_or_404(CuentaBancaria, pk=bank_account_id)
    form = BankAccountForm(request.POST or None, instance=bank_account)
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
    bank_account = get_object_or_404(CuentaBancaria, pk=bank_account_id)
    bank_account = toggle_bank_account(bank_account=bank_account, actor=request.user)
    messages.success(
        request,
        f"Cuenta bancaria {bank_account.nombre} {'activada' if bank_account.activa else 'desactivada'}.",
    )
    return redirect("treasury:cuentas_bancarias_list")


@login_required
def cuentas_por_pagar_list(request):
    _require_treasury_admin(request)
    form = PayableFilterForm(request.GET or None)
    queryset = CuentaPorPagar.objects.select_related("proveedor", "categoria").order_by(
        "fecha_vencimiento", "proveedor__razon_social"
    )
    if form.is_valid():
        q = (form.cleaned_data.get("q") or "").strip()
        proveedor = form.cleaned_data.get("proveedor")
        categoria = form.cleaned_data.get("categoria")
        estado = form.cleaned_data.get("estado")
        sucursal = form.cleaned_data.get("sucursal")
        if q:
            queryset = queryset.filter(
                Q(proveedor__razon_social__icontains=q)
                | Q(concepto__icontains=q)
                | Q(referencia_comprobante__icontains=q)
            )
        if proveedor:
            queryset = queryset.filter(proveedor=proveedor)
        if categoria:
            queryset = queryset.filter(categoria=categoria)
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
            "subtitle": "Deuda abierta, parcial, vencida o cerrada con historial.",
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
        ),
        pk=payable_id,
    )
    payments = payable.pagos.select_related("cuenta_bancaria", "creado_por", "anulado_por").order_by("-fecha_pago", "-id")
    badge, badge_class = _payable_badge(payable)
    fields = [
        {"label": "Proveedor", "value": payable.proveedor.razon_social},
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
            "subtitle": f"{payable.proveedor.razon_social} · {payable.categoria.nombre}",
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
def pagos_list(request):
    _require_treasury_admin(request)
    form = PaymentFilterForm(request.GET or None)
    queryset = PagoTesoreria.objects.select_related("cuenta_por_pagar__proveedor", "cuenta_bancaria").order_by(
        "-fecha_pago", "-id"
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
        _action(reverse("treasury:pagos_transferencia_create"), "Transferencia", "primary"),
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
    form = form_class(request.POST or None, initial=_payment_form_initial(request))
    if request.method == "POST" and form.is_valid():
        kwargs = {
            "payable": form.cleaned_data["cuenta_por_pagar"],
            "fecha_pago": form.cleaned_data["fecha_pago"],
            "monto": form.cleaned_data["monto"],
            "observaciones": form.cleaned_data.get("observaciones", ""),
            "actor": request.user,
        }
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
    movements = MovimientoBancario.objects.all().select_related(
        "cuenta_bancaria",
        "pago_tesoreria",
        "creado_por",
        "categoria",
        "proveedor",
    )
    
    if filter_form.is_valid():
        q = filter_form.cleaned_data.get("q")
        account = filter_form.cleaned_data.get("cuenta_bancaria")
        tipo = filter_form.cleaned_data.get("tipo")
        clase = filter_form.cleaned_data.get("clase")
        df = filter_form.cleaned_data.get("fecha_desde")
        dt = filter_form.cleaned_data.get("fecha_hasta")
        sucursal = filter_form.cleaned_data.get("sucursal")
        
        if q:
            movements = movements.filter(Q(concepto__icontains=q) | Q(referencia__icontains=q))
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
            movements = movements.filter(cuenta_bancaria__sucursal=sucursal)

    items = []
    for m in movements[:50]:
        items.append({
            "title": f"{m.get_clase_display()} - {m.concepto}",
            "subtitle": f"{m.fecha.strftime('%d/%m/%Y')} | {m.cuenta_bancaria}",
            "badge": _money(m.monto),
            "badge_class": "badge-success" if m.tipo == MovimientoBancario.Tipo.CREDITO else "badge-danger",
            "href": reverse("treasury:bank_movements_detail", args=[m.pk]),
            "meta": (
                f"Origen: {m.get_origen_display()} | Ref: {m.referencia or '-'}"
                f" | Rubro: {m.categoria.nombre if m.categoria_id else '-'}"
            ),
        })

    return render(request, "treasury/list_page.html", {
        "title": "Movimientos Bancarios",
        "subtitle": "Egresos e ingresos reales en cuentas bancarias",
        "filter_form": filter_form,
        "items": items,
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
                create_bank_movement(**form.cleaned_data, actor=request.user)
                messages.success(request, "Movimiento registrado correctamente.")
                return redirect("treasury:bank_movements_list")
            except ValidationError as error:
                _handle_operation_error(form, error, "No se pudo registrar el movimiento bancario.")
    else:
        form = BankMovementForm()

    return render(request, "treasury/form_page.html", {
        "title": "Registrar Movimiento Bancario",
        "form": form,
        "back_url": reverse("treasury:bank_movements_list")
    })

@login_required
def bank_movements_detail(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(
        MovimientoBancario.objects.select_related(
            "cuenta_bancaria",
            "pago_tesoreria",
            "creado_por",
            "actualizado_por",
            "categoria",
            "proveedor",
        ),
        pk=pk,
    )
    
    fields = [
        {"label": "Fecha", "value": movement.fecha.strftime("%d/%m/%Y")},
        {"label": "Cuenta", "value": str(movement.cuenta_bancaria)},
        {"label": "Tipo", "value": movement.get_tipo_display()},
        {"label": "Tipo financiero", "value": movement.get_clase_display()},
        {"label": "Monto", "value": _money(movement.monto)},
        {"label": "Rubro / categoria", "value": movement.categoria.nombre if movement.categoria_id else "No aplica"},
        {"label": "Proveedor", "value": movement.proveedor.razon_social if movement.proveedor_id else "No aplica"},
        {"label": "Concepto", "value": movement.concepto},
        {"label": "Referencia", "value": movement.referencia or "Sin referencia"},
        {"label": "Origen", "value": movement.get_origen_display()},
        {"label": "Creado por", "value": str(movement.creado_por) if movement.creado_por else "Sistema"},
        {"label": "Creado en", "value": movement.creado_en.strftime("%d/%m/%Y %H:%M")},
    ]
    if movement.actualizado_por:
        fields.append({"label": "Actualizado por", "value": str(movement.actualizado_por)})
        fields.append({"label": "Actualizado en", "value": movement.actualizado_en.strftime("%d/%m/%Y %H:%M")})
    
    fields.append({"label": "Observaciones", "value": movement.observaciones or "Sin observaciones"})

    actions = []
    if not movement.pago_tesoreria and movement.tipo == MovimientoBancario.Tipo.DEBITO:
        actions.append(_action(reverse("treasury:bank_movements_link", args=[movement.pk]), "Vincular a pago", "primary"))

    extra_sections = []
    if movement.pago_tesoreria:
        extra_sections.append({
            "title": "Pago vinculado",
            "items": [_payment_item(movement.pago_tesoreria)],
        })

    return render(request, "treasury/detail_page.html", {
        "title": "Detalle de Movimiento",
        "subtitle": f"Ref: {movement.referencia or movement.id}",
        "fields": fields,
        "actions": actions,
        "extra_sections": extra_sections,
        "back_url": reverse("treasury:bank_movements_list"),
        "section_label": movement.get_tipo_display(),
        "section_label_class": "badge-success" if movement.tipo == MovimientoBancario.Tipo.CREDITO else "badge-danger"
    })

@login_required
def bank_movements_link(request, pk):
    _require_treasury_admin(request)
    movement = get_object_or_404(MovimientoBancario, pk=pk)
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
                messages.error(request, str(e))
    
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
    accreditations = AcreditacionTarjeta.objects.all().select_related("movimiento_bancario__cuenta_bancaria", "lote_pos")
    
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
        "create_label": "Registrar acreditacion"
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
                _handle_operation_error(form, error, "No se pudo registrar la acreditacion.")
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
    form = DisponibilidadesFilterForm(request.GET or None)
    
    if form.is_valid():
        year = int(form.cleaned_data["year"])
        month = int(form.cleaned_data["month"])
        sucursal = form.cleaned_data.get("sucursal")
    else:
        today = timezone.localdate()
        year, month = today.year, today.month
        sucursal = None

    snapshot = build_disponibilidades_snapshot(year, month, sucursal=sucursal)
    
    return render(request, "treasury/disponibilidades_report.html", {
        "form": form,
        "snapshot": snapshot,
        "title": "Flujo de Disponibilidades",
        "subtitle": f"Consolidado de Efectivo y Bancos - {snapshot['first_day']:%m/%Y}" if not sucursal else f"Sucursal: {sucursal.nombre} - {snapshot['first_day']:%m/%Y}"
    })


@login_required
def central_cash_movements(request):
    _require_treasury_admin(request)
    from cashops.models import Sucursal
    sucursal_id = request.GET.get("sucursal")
    movements = MovimientoCajaCentral.objects.all().select_related("pago_tesoreria", "creado_por", "caja_central__sucursal")
    
    if sucursal_id:
        movements = movements.filter(caja_central__sucursal_id=sucursal_id)
    
    items = []
    for m in movements[:100]:
        badge_class = "badge-success" if m.monto > 0 else "badge-danger" # logic depends on type
        # Simplistic: INGRESO/APORTE/RETIRO_BANCO are positive for cash
        if m.tipo in [MovimientoCajaCentral.Tipo.INGRESO_CAJA, MovimientoCajaCentral.Tipo.APORTE, MovimientoCajaCentral.Tipo.RETIRO_BANCO, MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO]:
            badge_class = "badge-success"
            prefix = "+"
        else:
            badge_class = "badge-danger"
            prefix = "-"
            
        items.append({
            "title": f"{m.get_tipo_display()}",
            "subtitle": f"{m.fecha:%d/%m/%Y} - {m.concepto}",
            "badge": f"{prefix}{_money(m.monto)}",
            "badge_class": badge_class,
            "meta": m.observaciones or f"Registrado por {m.creado_por}"
        })
        
    return render(request, "treasury/list_page.html", {
        "title": "Libro de Efectivo Central",
        "subtitle": "Historial de ingresos y egresos de la caja central",
        "items": items,
        "create_url": reverse("treasury:central_cash_create"),
        "create_label": "Registrar movimiento manual"
    })


@login_required
def central_cash_create(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        form = CentralCashMovementForm(request.POST)
        if form.is_valid():
            try:
                register_central_cash_movement(
                    tipo=form.cleaned_data["tipo"],
                    monto=form.cleaned_data["monto"],
                    concepto=form.cleaned_data["concepto"],
                    fecha=form.cleaned_data["fecha"],
                    observaciones=form.cleaned_data["observaciones"],
                    actor=request.user
                )
                messages.success(request, "Movimiento registrado correctamente.")
                return redirect("treasury:central_cash_list")
            except ValidationError as e:
                form.add_error(None, e)
    else:
        form = CentralCashMovementForm()
        
    return render(request, "treasury/form_page.html", {
        "title": "Nuevo Movimiento de Efectivo",
        "form": form,
        "back_url": reverse("treasury:central_cash_list")
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
    caja = get_or_create_default_caja_central()
    if request.method == "POST":
        form = ArqueoForm(request.POST)
        if form.is_valid():
            register_arqueo(
                caja_central=caja,
                saldo_contado=form.cleaned_data["saldo_contado_efectivo"],
                observaciones=form.cleaned_data["observaciones"],
                actor=request.user
            )
            messages.success(request, "Arqueo registrado correctamente.")
            return redirect("treasury:arqueo_list")
    else:
        form = ArqueoForm(initial={"saldo_contado_efectivo": caja.saldo_actual})
        
    return render(request, "treasury/form_page.html", {
        "title": "Realizar Arqueo de Efectivo",
        "form": form,
        "back_url": reverse("treasury:arqueo_list")
    })


@login_required
def close_month_action(request):
    _require_treasury_admin(request)
    if request.method == "POST":
        year = int(request.POST.get("year"))
        month = int(request.POST.get("month"))
        try:
            close_treasury_month(year, month, actor=request.user)
            messages.success(request, f"Periodo {month}/{year} cerrado correctamente.")
        except ValidationError as e:
            messages.error(request, str(e))
            
    return redirect(request.META.get('HTTP_REFERER', reverse('treasury:disponibilidades')))
