import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .alcance import empresas_en_alcance, sucursales_en_alcance
from .forms import EmpleadoFiltroForm, EmpleadoForm, ImportarEmpleadosForm
from .importacion import Estado, FilaListado, ListadoIlegible, leer_listado, procesar_listado
from .models import Empleado
from .permissions import can_administrar, can_fichar, ensure_acceder, ensure_administrar
from .services import actualizar_empleado, crear_empleado, dar_de_baja_empleado, reactivar_empleado

SESION_LISTADO = "asistencia_listado"


@login_required
def inicio(request):
    ensure_acceder(request.user)
    return render(
        request,
        "asistencia/inicio.html",
        {
            "title": "Asistencia",
            "puede_fichar": can_fichar(request.user),
            "puede_administrar": can_administrar(request.user),
        },
    )


def _empleados_en_alcance(request):
    return Empleado.objects.filter(empresa__in=empresas_en_alcance(request)).select_related(
        "empresa", "sucursal", "dado_de_baja_por"
    )


def _empleado_en_alcance_or_404(request, empleado_id: int) -> Empleado:
    # Un empleado de otra empresa responde 404, como en tesoreria: para este
    # usuario no existe.
    return get_object_or_404(_empleados_en_alcance(request), pk=empleado_id)


def _volcar_error(form, error) -> None:
    if isinstance(error, IntegrityError):
        form.add_error("dni", "Ya hay un empleado activo con ese DNI.")
    elif hasattr(error, "message_dict"):
        for campo, mensajes in error.message_dict.items():
            destino = campo if campo in form.fields else None
            for mensaje in mensajes:
                form.add_error(destino, mensaje)
    else:
        for mensaje in error.messages:
            form.add_error(None, mensaje)


def _mensaje_de_error(error) -> str:
    return " ".join(error.messages) if isinstance(error, ValidationError) else str(error)


@login_required
def empleado_list(request):
    ensure_administrar(request.user)
    empresas = empresas_en_alcance(request)
    sucursales = sucursales_en_alcance(request)
    filtro = EmpleadoFiltroForm(request.GET or None, empresas=empresas, sucursales=sucursales)
    base = _empleados_en_alcance(request)
    empleados = base
    estado = "activos"
    if filtro.is_valid():
        datos = filtro.cleaned_data
        estado = datos.get("estado") or "activos"
        q = (datos.get("q") or "").strip()
        if q:
            empleados = empleados.filter(Q(nombre__icontains=q) | Q(dni__icontains=q.replace(".", "")))
        if datos.get("empresa"):
            empleados = empleados.filter(empresa=datos["empresa"])
        if datos.get("sucursal"):
            empleados = empleados.filter(sucursal=datos["sucursal"])
    if estado == "baja":
        empleados = empleados.filter(activo=False)
    elif estado != "todos":
        empleados = empleados.filter(activo=True)
    return render(
        request,
        "asistencia/empleado_list.html",
        {
            "title": "Empleados",
            "filtro": filtro,
            "empleados": empleados,
            "activos_count": base.filter(activo=True).count(),
            "baja_count": base.filter(activo=False).count(),
        },
    )


def _render_form(request, form, *, title, submit_label, form_action, status=200):
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": title,
            "subtitle": "La empresa y el local salen de tus empresas seleccionadas.",
            "form": form,
            "submit_label": submit_label,
            "back_url": reverse("asistencia:empleado_list"),
            "form_action": form_action,
            "disable_htmx": True,
        },
        status=status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_create(request):
    ensure_administrar(request.user)
    form = EmpleadoForm(request.POST or None, empresas=empresas_en_alcance(request))
    if request.method == "POST" and form.is_valid():
        try:
            empleado = crear_empleado(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _volcar_error(form, error)
        else:
            messages.success(request, f"{empleado.nombre} quedó cargado en el padrón.")
            return redirect("asistencia:empleado_list")
    return _render_form(
        request,
        form,
        title="Nuevo empleado",
        submit_label="Guardar",
        form_action=reverse("asistencia:empleado_create"),
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_update(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    form = EmpleadoForm(request.POST or None, instance=empleado, empresas=empresas_en_alcance(request))
    if request.method == "POST" and form.is_valid():
        try:
            empleado = actualizar_empleado(actor=request.user, empleado=empleado, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _volcar_error(form, error)
        else:
            messages.success(request, f"{empleado.nombre} quedó actualizado.")
            return redirect("asistencia:empleado_list")
    return _render_form(
        request,
        form,
        title=f"Editar {empleado.nombre}",
        submit_label="Guardar cambios",
        form_action=reverse("asistencia:empleado_update", args=[empleado.pk]),
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["POST"])
def empleado_baja(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    try:
        empleado = dar_de_baja_empleado(actor=request.user, empleado=empleado)
    except ValidationError as error:
        messages.error(request, _mensaje_de_error(error))
    else:
        messages.success(request, f"{empleado.nombre} quedó dado de baja: ya no puede fichar.")
    return redirect("asistencia:empleado_list")


@login_required
@require_http_methods(["POST"])
def empleado_reactivar(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    try:
        empleado = reactivar_empleado(actor=request.user, empleado=empleado)
    except (ValidationError, IntegrityError) as error:
        mensaje = "Ya hay un empleado activo con ese DNI." if isinstance(error, IntegrityError) else _mensaje_de_error(error)
        messages.error(request, mensaje)
    else:
        messages.success(request, f"{empleado.nombre} quedó activo otra vez.")
    return redirect("asistencia:empleado_list")


def _render_importacion(request, form, filas=None, *, confirmado=False, token="", status=200):
    filas = filas or []
    return render(
        request,
        "asistencia/empleado_importar.html",
        {
            "title": "Cargar empleados desde un listado",
            "form": form,
            "filas": filas,
            "confirmado": confirmado,
            "token": token,
            "listas": sum(1 for f in filas if f.estado == Estado.LISTA),
            "cargadas": sum(1 for f in filas if f.estado == Estado.CARGADA),
            "rechazadas": sum(1 for f in filas if f.estado == Estado.RECHAZADA),
        },
        status=status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_importar(request):
    ensure_administrar(request.user)
    form = ImportarEmpleadosForm(request.POST or None, request.FILES or None)
    if request.method != "POST":
        # No se borra la revision pendiente: puede estar abierta en otra pestana,
        # y el token ya impide confirmar una que no sea la ultima.
        return _render_importacion(request, form)
    if not form.is_valid():
        return _render_importacion(request, form, status=400)
    try:
        filas = leer_listado(form.cleaned_data["archivo"])
    except ListadoIlegible as error:
        form.add_error("archivo", str(error))
        return _render_importacion(request, form, status=400)
    filas = procesar_listado(filas, actor=request.user, sucursales=sucursales_en_alcance(request), guardar=False)
    token = ""
    if any(f.estado == Estado.LISTA for f in filas):
        # El token ata el boton de confirmar a esta revision: si en otra pestana
        # se reviso otro listado, confirmar este no carga aquel.
        token = secrets.token_urlsafe(16)
        request.session[SESION_LISTADO] = {"token": token, "filas": [f.datos() for f in filas]}
    else:
        request.session.pop(SESION_LISTADO, None)
    return _render_importacion(request, ImportarEmpleadosForm(), filas, token=token)


@login_required
@require_http_methods(["POST"])
def empleado_importar_confirmar(request):
    ensure_administrar(request.user)
    datos = request.session.get(SESION_LISTADO)
    # En bytes: compare_digest con str rechaza (500) un token con caracteres no ASCII.
    token = str(request.POST.get("token", "")).encode()
    if not datos or not secrets.compare_digest(token, str(datos.get("token", "")).encode()):
        messages.error(request, "Ese listado ya no es el último que revisaste. Subilo de nuevo.")
        return redirect("asistencia:empleado_importar")
    del request.session[SESION_LISTADO]
    filas = procesar_listado(
        [FilaListado(**fila) for fila in datos["filas"]],
        actor=request.user,
        sucursales=sucursales_en_alcance(request),
        guardar=True,
    )
    cargadas = sum(1 for f in filas if f.estado == Estado.CARGADA)
    messages.success(request, f"Se cargaron {cargadas} empleado{'s' if cargadas != 1 else ''}.")
    return _render_importacion(request, ImportarEmpleadosForm(), filas, confirmado=True)
