from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .forms import (
    CajaAperturaForm,
    CierreCajaForm,
    GastoRapidoForm,
    SucursalForm,
    TurnoForm,
    TransferenciaEntreCajasForm,
    TransferenciaEntreSucursalesForm,
    VentaTarjetaForm,
)
from .models import Caja, Sucursal, Turno
from .services import (
    close_box,
    open_box,
    register_card_sale,
    register_expense,
    transfer_between_boxes,
    transfer_between_branches,
)


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _render_form(request, full_template: str, partial_template: str, context: dict, status: int = 200):
    template = partial_template if _is_htmx(request) else full_template
    return render(request, template, context, status=status)


def dashboard(request):
    boxes = Caja.objects.select_related("sucursal", "turno", "usuario").order_by("-abierta_en")
    selected_box = None
    box_id = request.GET.get("box")
    if box_id:
        selected_box = boxes.filter(pk=box_id).first()
    if selected_box is None:
        selected_box = boxes.filter(estado=Caja.Estado.ABIERTA).first()

    recent_movements = []
    if selected_box is not None:
        recent_movements = (
            selected_box.movimientos.select_related("transferencia", "creado_por")
            .order_by("-creado_en", "-id")[:20]
        )

    context = {
        "selected_box": selected_box,
        "open_boxes": boxes.filter(estado=Caja.Estado.ABIERTA),
        "recent_movements": recent_movements,
        "turnos_abiertos": Turno.objects.select_related("sucursal").filter(estado=Turno.Estado.ABIERTO),
        "sucursales": Sucursal.objects.filter(activa=True),
    }
    return render(request, "cashops/dashboard.html", context)


@require_http_methods(["GET", "POST"])
def sucursal_create(request):
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
            "subtitle": "Alta rapida para operar con cajas y turnos.",
            "form": form,
            "submit_label": "Guardar sucursal",
            "back_url": reverse("cashops:sucursal_list"),
            "form_action": reverse("cashops:sucursal_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


def sucursal_list(request):
    return render(
        request,
        "cashops/list_page.html",
        {
            "title": "Sucursales",
            "create_url": reverse("cashops:sucursal_create"),
            "items": Sucursal.objects.all(),
        },
    )


@require_http_methods(["GET", "POST"])
def turno_create(request):
    form = TurnoForm(request.POST or None)
    form.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
    if request.method == "POST" and form.is_valid():
        turno = form.save(commit=False)
        turno.estado = Turno.Estado.ABIERTO
        if request.user.is_authenticated:
            turno.creado_por = request.user
        turno.save()
        messages.success(request, f"Turno {turno.get_tipo_display()} creado.")
        url = reverse("cashops:turno_list")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo turno",
            "subtitle": "Defini T.M. o T.T. para la sucursal.",
            "form": form,
            "submit_label": "Guardar turno",
            "back_url": reverse("cashops:turno_list"),
            "form_action": reverse("cashops:turno_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


def turno_list(request):
    return render(
        request,
        "cashops/list_page.html",
        {
            "title": "Turnos",
            "create_url": reverse("cashops:turno_create"),
            "items": Turno.objects.select_related("sucursal").all(),
        },
    )


@require_http_methods(["GET", "POST"])
def open_box_view(request):
    form = CajaAperturaForm(request.POST or None)
    form.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
    form.fields["turno"].queryset = Turno.objects.select_related("sucursal").filter(estado=Turno.Estado.ABIERTO)
    if request.method == "POST" and form.is_valid():
        box = open_box(
            user=request.user if request.user.is_authenticated else None,
            turno=form.cleaned_data["turno"],
            sucursal=form.cleaned_data["sucursal"],
            monto_inicial=form.cleaned_data["monto_inicial"],
        )
        messages.success(request, "Caja abierta.")
        url = f"{reverse('cashops:dashboard')}?box={box.pk}"
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Abrir caja",
            "subtitle": "Una caja individual por usuario, turno y sucursal.",
            "form": form,
            "submit_label": "Abrir caja",
            "back_url": reverse("cashops:dashboard"),
            "form_action": reverse("cashops:box_open"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@require_http_methods(["GET", "POST"])
def register_expense_view(request, box_id: int):
    box = get_object_or_404(Caja.objects.select_related("turno", "sucursal", "usuario"), pk=box_id)
    form = GastoRapidoForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        register_expense(
            caja=box,
            monto=form.cleaned_data["monto"],
            categoria=form.cleaned_data["categoria"],
            observacion=form.cleaned_data["observacion"],
            creado_por=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Gasto registrado.")
        url = f"{reverse('cashops:dashboard')}?box={box.pk}"
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Nuevo gasto",
            "subtitle": f"Caja activa: {box.id}",
            "form": form,
            "submit_label": "Guardar gasto",
            "back_url": f"{reverse('cashops:dashboard')}?box={box.pk}",
            "form_action": reverse("cashops:box_expense", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@require_http_methods(["GET", "POST"])
def register_card_sale_view(request, box_id: int):
    box = get_object_or_404(Caja.objects.select_related("turno", "sucursal", "usuario"), pk=box_id)
    form = VentaTarjetaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        register_card_sale(
            caja=box,
            monto=form.cleaned_data["monto"],
            observacion=form.cleaned_data["observacion"],
            creado_por=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Venta POS registrada.")
        url = f"{reverse('cashops:dashboard')}?box={box.pk}"
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Venta por tarjeta",
            "subtitle": f"Caja activa: {box.id}",
            "form": form,
            "submit_label": "Guardar venta",
            "back_url": f"{reverse('cashops:dashboard')}?box={box.pk}",
            "form_action": reverse("cashops:box_pos", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@require_http_methods(["GET", "POST"])
def transfer_between_boxes_view(request):
    form = TransferenciaEntreCajasForm(request.POST or None)
    open_boxes = Caja.objects.select_related("turno", "sucursal", "usuario").filter(estado=Caja.Estado.ABIERTA)
    form.fields["caja_origen"].queryset = open_boxes
    form.fields["caja_destino"].queryset = open_boxes
    if request.method == "POST" and form.is_valid():
        transfer_between_boxes(
            caja_origen=form.cleaned_data["caja_origen"],
            caja_destino=form.cleaned_data["caja_destino"],
            monto=form.cleaned_data["monto"],
            observacion=form.cleaned_data["observacion"],
            creado_por=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Traspaso entre cajas registrado.")
        url = reverse("cashops:dashboard")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Traspaso entre cajas",
            "subtitle": "Moviendo efectivo entre cajas activas.",
            "form": form,
            "submit_label": "Guardar traspaso",
            "back_url": reverse("cashops:dashboard"),
            "form_action": reverse("cashops:transfer_boxes"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@require_http_methods(["GET", "POST"])
def transfer_between_branches_view(request):
    form = TransferenciaEntreSucursalesForm(request.POST or None)
    form.fields["sucursal_origen"].queryset = Sucursal.objects.filter(activa=True)
    form.fields["sucursal_destino"].queryset = Sucursal.objects.filter(activa=True)
    open_boxes = Caja.objects.select_related("turno", "sucursal", "usuario").filter(estado=Caja.Estado.ABIERTA)
    form.fields["caja_origen"].queryset = open_boxes
    form.fields["caja_destino"].queryset = open_boxes
    if request.method == "POST" and form.is_valid():
        transfer_between_branches(
            sucursal_origen=form.cleaned_data["sucursal_origen"],
            sucursal_destino=form.cleaned_data["sucursal_destino"],
            clase=form.cleaned_data["clase"],
            monto=form.cleaned_data["monto"],
            observacion=form.cleaned_data["observacion"],
            caja_origen=form.cleaned_data["caja_origen"],
            caja_destino=form.cleaned_data["caja_destino"],
            creado_por=request.user if request.user.is_authenticated else None,
        )
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


@require_http_methods(["GET", "POST"])
def close_box_view(request, box_id: int):
    box = get_object_or_404(Caja.objects.select_related("turno", "sucursal", "usuario"), pk=box_id)
    form = CierreCajaForm(request.POST or None, caja=box)
    if request.method == "POST" and form.is_valid():
        close_box(
            caja=box,
            saldo_fisico=form.cleaned_data["saldo_fisico"],
            justificacion=form.cleaned_data["justificacion"],
            cerrado_por=request.user if request.user.is_authenticated else None,
        )
        messages.success(request, "Caja cerrada.")
        url = reverse("cashops:dashboard")
        return _hx_redirect(url) if _is_htmx(request) else redirect(url)

    return _render_form(
        request,
        "cashops/form_page.html",
        "cashops/partials/form_card.html",
        {
            "title": "Cerrar caja",
            "subtitle": f"Caja activa: {box.id} | Saldo esperado: {box.saldo_esperado}",
            "form": form,
            "submit_label": "Cerrar caja",
            "back_url": f"{reverse('cashops:dashboard')}?box={box.pk}",
            "form_action": reverse("cashops:box_close", args=[box.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )

