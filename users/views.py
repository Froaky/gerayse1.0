from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .forms import PersonalForm, UserAccessForm

User = get_user_model()


class GerayseLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["username"].widget.attrs.update(
            {
                "class": "app-input",
                "placeholder": "Usuario",
                "autocomplete": "username",
                "autofocus": True,
            }
        )
        form.fields["password"].widget.attrs.update(
            {
                "class": "app-input",
                "placeholder": "Contrasena",
                "autocomplete": "current-password",
            }
        )
        return form

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self.request.user, "must_change_password", False):
            return redirect("users:password_change_required")
        return response


class GerayseLogoutView(LogoutView):
    next_page = reverse_lazy("users:login")


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _render_form(request, context: dict, status: int = 200, template: str = "cashops/form_page.html"):
    return render(request, template, context, status=status)


def _admin_redirect(request):
    if not request.user.is_cashops_admin():
        messages.error(request, "No tenes permisos para gestionar usuarios.")
        return redirect("cashops:dashboard")
    return None


def _style_password_form(form) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("class", "app-input")


def _display_name(user) -> str:
    return user.get_full_name() or user.get_username()


def _is_admin_role(role) -> bool:
    if not role or not role.code:
        return False
    return role.code.strip().upper() in User.ADMIN_ROLE_CODES


def _first_access_url(request, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("users:first_access", args=[uid, token]))


def _effective_access_rows(user):
    is_active = user.is_active
    is_admin = is_active and user.is_cashops_admin()
    can_use_cashops = is_active
    if not is_active:
        location_scope = "Usuario archivado: no puede ingresar."
    elif is_admin:
        location_scope = "Todas las sucursales y cajas."
    elif user.usuario_fijo and user.sucursal_base_id:
        location_scope = f"Sucursal base: {user.sucursal_base.nombre}."
    else:
        location_scope = "Solo cajas asignadas operativamente."

    return [
        {
            "module": "Caja operativa",
            "read": can_use_cashops,
            "write": can_use_cashops,
            "scope": location_scope,
            "source": "Usuario activo + caja asignada; admin ve todas.",
        },
        {
            "module": "Configuracion",
            "read": is_admin,
            "write": is_admin,
            "scope": "Rubros, limites, empresas, sucursales, turnos y reinicio de datos.",
            "source": "Rol ADMIN/ADMINISTRADOR o superusuario.",
        },
        {
            "module": "Tesoreria",
            "read": is_admin,
            "write": is_admin,
            "scope": "Proveedores, deudas, pagos, bancos, caja central y reportes.",
            "source": "Misma regla vigente de administrador operativo.",
        },
        {
            "module": "Usuarios",
            "read": is_admin,
            "write": is_admin,
            "scope": "Alta, edicion, permisos, archivo, baja y links de primer ingreso.",
            "source": "Misma regla vigente de administrador operativo.",
        },
    ]


def _render_user_detail(request, target_user, access_form=None, status=200):
    access_form = access_form or UserAccessForm(instance=target_user)
    first_access_url = None
    if target_user.is_active and target_user.must_change_password:
        first_access_url = _first_access_url(request, target_user)
    return render(
        request,
        "users/user_detail.html",
        {
            "target_user": target_user,
            "access_form": access_form,
            "access_rows": _effective_access_rows(target_user),
            "first_access_url": first_access_url,
            "can_delete": target_user.pk != request.user.pk,
        },
        status=status,
    )


@login_required
def user_list(request):
    guard = _admin_redirect(request)
    if guard:
        return guard

    q = (request.GET.get("q") or "").strip()
    status = request.GET.get("status") or "active"
    users = User.objects.select_related("role", "sucursal_base").all().order_by("last_name", "first_name", "username")
    if status == "archived":
        users = users.filter(is_active=False)
    elif status != "all":
        users = users.filter(is_active=True)
        status = "active"
    if q:
        users = users.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(username__icontains=q)
            | Q(role__name__icontains=q)
            | Q(role__code__icontains=q)
            | Q(sucursal_base__nombre__icontains=q)
        )
    return render(
        request,
        "users/user_list.html",
        {
            "items": users,
            "query": q,
            "status": status,
            "active_count": User.objects.filter(is_active=True).count(),
            "archived_count": User.objects.filter(is_active=False).count(),
        },
    )


@login_required
def user_create(request):
    guard = _admin_redirect(request)
    if guard:
        return guard

    form = PersonalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Usuario {_display_name(user)} creado. Ya tiene link de primer ingreso.")
        return _hx_redirect(reverse("users:user_detail", args=[user.pk])) if _is_htmx(request) else redirect("users:user_detail", user.pk)

    return _render_form(
        request,
        {
            "title": "Nuevo Usuario",
            "subtitle": "Carga los datos de acceso, rol operativo y contrasena default.",
            "form": form,
            "submit_label": "Crear usuario",
            "back_url": reverse("users:user_list"),
            "form_action": reverse("users:user_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def user_update(request, user_id: int):
    guard = _admin_redirect(request)
    if guard:
        return guard

    target_user = get_object_or_404(User, pk=user_id)
    form = PersonalForm(request.POST or None, instance=target_user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Usuario {_display_name(user)} actualizado.")
        return _hx_redirect(reverse("users:user_detail", args=[user.pk])) if _is_htmx(request) else redirect("users:user_detail", user.pk)

    return _render_form(
        request,
        {
            "title": f"Editar Usuario: {_display_name(target_user)}",
            "subtitle": "Actualiza identidad operativa, rol y contrasena si corresponde.",
            "form": form,
            "submit_label": "Actualizar usuario",
            "back_url": reverse("users:user_detail", args=[target_user.pk]),
            "form_action": reverse("users:user_update", args=[target_user.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def user_detail(request, user_id: int):
    guard = _admin_redirect(request)
    if guard:
        return guard

    target_user = get_object_or_404(User.objects.select_related("role", "sucursal_base"), pk=user_id)
    if request.method == "POST":
        access_form = UserAccessForm(request.POST, instance=target_user)
        if access_form.is_valid():
            role = access_form.cleaned_data.get("role")
            is_active = access_form.cleaned_data.get("is_active")
            if target_user.pk == request.user.pk and not is_active:
                access_form.add_error("is_active", "No podes archivar tu propio usuario.")
            if target_user.pk == request.user.pk and not request.user.is_superuser and not _is_admin_role(role):
                access_form.add_error("role", "No podes quitarte tu propio acceso administrador.")
        if access_form.is_valid():
            access_form.save()
            messages.success(request, f"Permisos de {_display_name(target_user)} actualizados.")
            return redirect("users:user_detail", target_user.pk)
        target_user.refresh_from_db()
        return _render_user_detail(request, target_user, access_form=access_form, status=400)

    return _render_user_detail(request, target_user)


@login_required
@require_POST
def user_archive(request, user_id: int):
    guard = _admin_redirect(request)
    if guard:
        return guard

    target_user = get_object_or_404(User, pk=user_id)
    if target_user.pk == request.user.pk:
        messages.error(request, "No podes archivar tu propio usuario.")
        return redirect("users:user_detail", target_user.pk)
    target_user.is_active = False
    target_user.save(update_fields=["is_active"])
    messages.success(request, f"Usuario {_display_name(target_user)} archivado.")
    return redirect("users:user_detail", target_user.pk)


@login_required
@require_POST
def user_restore(request, user_id: int):
    guard = _admin_redirect(request)
    if guard:
        return guard

    target_user = get_object_or_404(User, pk=user_id)
    target_user.is_active = True
    target_user.save(update_fields=["is_active"])
    messages.success(request, f"Usuario {_display_name(target_user)} reactivado.")
    return redirect("users:user_detail", target_user.pk)


@login_required
@require_POST
def user_delete(request, user_id: int):
    guard = _admin_redirect(request)
    if guard:
        return guard

    target_user = get_object_or_404(User, pk=user_id)
    if target_user.pk == request.user.pk:
        messages.error(request, "No podes eliminar tu propio usuario.")
        return redirect("users:user_detail", target_user.pk)
    user_label = _display_name(target_user)
    try:
        target_user.delete()
    except ProtectedError:
        messages.error(request, "No se puede eliminar porque tiene operacion asociada. Archivarlo conserva la trazabilidad.")
        return redirect("users:user_detail", target_user.pk)
    messages.success(request, f"Usuario {user_label} eliminado.")
    return redirect("users:user_list")


@login_required
def password_change_required(request):
    if not getattr(request.user, "must_change_password", False) and request.method == "GET":
        return redirect("cashops:dashboard")

    form = PasswordChangeForm(request.user, request.POST or None)
    _style_password_form(form)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, user)
        messages.success(request, "Contrasena actualizada. Ya podes operar con tu usuario.")
        return redirect("cashops:dashboard")
    return render(
        request,
        "users/password_change_required.html",
        {"form": form},
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


def first_access(request, uidb64: str, token: str):
    target_user = None
    validlink = False
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        target_user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        target_user = None

    if (
        target_user is not None
        and target_user.is_active
        and target_user.must_change_password
        and default_token_generator.check_token(target_user, token)
    ):
        validlink = True

    form = SetPasswordForm(target_user, request.POST or None) if validlink else None
    if form is not None:
        _style_password_form(form)
    if request.method == "POST" and validlink and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        messages.success(request, "Contrasena creada. Ingresa con tu usuario y la nueva contrasena.")
        return redirect("users:login")

    return render(
        request,
        "users/first_access.html",
        {
            "form": form,
            "validlink": validlink,
            "target_user": target_user,
        },
        status=400 if request.method == "POST" and validlink and form is not None and not form.is_valid() else 200,
    )
