from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_POST

from .forms import OwnProfileForm, PersonalForm, RoleForm, UserAccessForm, UserCreateForm
from .models import PermissionModule, Role, RolePermission, UserPermission

User = get_user_model()

PERMISSION_MODULE_META = {
    PermissionModule.CASHOPS: {
        "label": "Caja operativa",
        "scope": "Cajas, apertura, movimientos, ingresos, egresos, traspasos y cierre operativo.",
    },
    PermissionModule.CASHOPS_CLOSED_FIX: {
        "label": "Corrección de cajas cerradas",
        "scope": "Editar o anular movimientos de cajas cerradas con motivo, auditoría y recálculo operativo.",
    },
    PermissionModule.CONFIG: {
        "label": "Configuración",
        "scope": "Rubros, limites, empresas, sucursales, turnos y reinicio de datos.",
    },
    PermissionModule.TREASURY: {
        "label": "Tesorería",
        "scope": "Proveedores, deudas, pagos, bancos, caja central y reportes.",
    },
    PermissionModule.USERS: {
        "label": "Usuarios",
        "scope": "Alta, edición, roles, permisos, archivo, baja y links de primer ingreso.",
    },
}


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
                "placeholder": "Contraseña",
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


def _ensure_users_permission(request, action: str) -> None:
    if not request.user.has_module_permission(PermissionModule.USERS, action):
        raise PermissionDenied("No tenes permisos para gestionar usuarios.")


def _style_password_form(form) -> None:
    for field in form.fields.values():
        field.widget.attrs.setdefault("class", "app-input")
    if "old_password" in form.fields:
        form.fields["old_password"].label = "Contraseña actual"
        form.fields["old_password"].widget.attrs.setdefault("placeholder", "Contraseña actual")
    if "new_password1" in form.fields:
        form.fields["new_password1"].label = "Contraseña nueva"
        form.fields["new_password1"].widget.attrs.setdefault("placeholder", "Contraseña nueva")
    if "new_password2" in form.fields:
        form.fields["new_password2"].label = "Confirmar contraseña nueva"
        form.fields["new_password2"].widget.attrs.setdefault("placeholder", "Confirmar contraseña nueva")


def _display_name(user) -> str:
    return user.get_full_name() or user.get_username()


def _first_access_url(request, user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return request.build_absolute_uri(reverse("users:first_access", args=[uid, token]))


def _permission_rows_for_user(user):
    rows = []
    for module, label in PermissionModule.choices:
        can_read, can_write, source = user.configured_permission_values(module)
        if not user.is_active:
            can_read = False
            can_write = False
            source = "Archivado"
        rows.append(
            {
                "module": module,
                "label": label,
                "read": can_read,
                "write": can_write,
                "scope": PERMISSION_MODULE_META[module]["scope"],
                "source": source,
            }
        )
    return rows


def _permission_rows_for_role(role):
    role.ensure_permission_rows()
    permissions = {permission.module: permission for permission in role.permissions.all()}
    rows = []
    for module, label in PermissionModule.choices:
        permission = permissions[module]
        rows.append(
            {
                "module": module,
                "label": label,
                "read": permission.can_read or permission.can_write,
                "write": permission.can_write,
                "scope": PERMISSION_MODULE_META[module]["scope"],
            }
        )
    return rows


def _target_user_has_users_write_after_toggle(target_user, module: str, action: str, new_value: bool) -> bool:
    if module != PermissionModule.USERS:
        return target_user.has_module_permission(PermissionModule.USERS, "write")
    if action == "write":
        return new_value
    if action == "read" and not new_value:
        return False
    return target_user.has_module_permission(PermissionModule.USERS, "write")


def _role_grants_permission(role, module: str, action: str) -> bool:
    if not role:
        return False
    if (role.code or "").strip().upper() in User.ADMIN_ROLE_CODES:
        return True
    permission = RolePermission.objects.filter(role=role, module=module).first()
    if permission:
        return permission.can_write if action == "write" else permission.can_read or permission.can_write
    if module == PermissionModule.CASHOPS:
        return True
    return False


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
            "access_rows": _permission_rows_for_user(target_user),
            "first_access_url": first_access_url,
            "can_delete": target_user.pk != request.user.pk,
            "can_edit_permissions": request.user.has_module_permission(PermissionModule.USERS, "write"),
            "empresas_del_usuario": list(target_user.empresas_permitidas.order_by("nombre")),
        },
        status=status,
    )


@login_required
def user_list(request):
    _ensure_users_permission(request, "read")

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
    _ensure_users_permission(request, "write")

    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "usuario creado correctamente")
        list_url = reverse("users:user_list")
        return _hx_redirect(list_url) if _is_htmx(request) else redirect("users:user_list")

    return _render_form(
        request,
        {
            "title": "Nuevo Usuario",
            "subtitle": "Carga solo los datos mínimos de acceso.",
            "form": form,
            "submit_label": "Crear usuario",
            "back_url": reverse("users:user_list"),
            "form_action": reverse("users:user_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def user_update(request, user_id: int):
    _ensure_users_permission(request, "write")

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
            "subtitle": "Actualiza identidad operativa, rol y contraseña si corresponde.",
            "form": form,
            "submit_label": "Actualizar usuario",
            "back_url": reverse("users:user_detail", args=[target_user.pk]),
            "form_action": reverse("users:user_update", args=[target_user.pk]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def user_detail(request, user_id: int):
    _ensure_users_permission(request, "write" if request.method == "POST" else "read")

    target_user = get_object_or_404(User.objects.select_related("role", "sucursal_base"), pk=user_id)
    if request.method == "POST":
        access_form = UserAccessForm(request.POST, instance=target_user)
        if access_form.is_valid():
            role = access_form.cleaned_data.get("role")
            is_active = access_form.cleaned_data.get("is_active")
            if target_user.pk == request.user.pk and not is_active:
                access_form.add_error("is_active", "No podés archivar tu propio usuario.")
            if target_user.pk == request.user.pk and not request.user.is_superuser:
                has_user_override = UserPermission.objects.filter(
                    user=target_user,
                    module=PermissionModule.USERS,
                    can_write=True,
                ).exists()
                if not _role_grants_permission(role, PermissionModule.USERS, "write") and not has_user_override:
                    access_form.add_error("role", "No podés quitarte tu propio acceso a usuarios.")
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
    _ensure_users_permission(request, "write")

    target_user = get_object_or_404(User, pk=user_id)
    if target_user.pk == request.user.pk:
        messages.error(request, "No podés archivar tu propio usuario.")
        return redirect("users:user_detail", target_user.pk)
    target_user.is_active = False
    target_user.save(update_fields=["is_active"])
    messages.success(request, f"Usuario {_display_name(target_user)} archivado.")
    return redirect("users:user_detail", target_user.pk)


@login_required
@require_POST
def user_restore(request, user_id: int):
    _ensure_users_permission(request, "write")

    target_user = get_object_or_404(User, pk=user_id)
    target_user.is_active = True
    target_user.save(update_fields=["is_active"])
    messages.success(request, f"Usuario {_display_name(target_user)} reactivado.")
    return redirect("users:user_detail", target_user.pk)


@login_required
@require_POST
def user_delete(request, user_id: int):
    _ensure_users_permission(request, "write")

    target_user = get_object_or_404(User, pk=user_id)
    if target_user.pk == request.user.pk:
        messages.error(request, "No podés eliminar tu propio usuario.")
        return redirect("users:user_detail", target_user.pk)
    user_label = _display_name(target_user)
    try:
        target_user.delete()
    except ProtectedError:
        messages.error(request, "No se puede eliminar porque tiene operación asociada. Archivarlo conserva la trazabilidad.")
        return redirect("users:user_detail", target_user.pk)
    messages.success(request, f"Usuario {user_label} eliminado.")
    return redirect("users:user_list")


@login_required
@require_POST
def user_permission_toggle(request, user_id: int, module: str, action: str):
    _ensure_users_permission(request, "write")
    if module not in PermissionModule.values or action not in {"read", "write"}:
        raise PermissionDenied("Permiso inválido.")

    target_user = get_object_or_404(User, pk=user_id)
    current_read, current_write, _ = target_user.configured_permission_values(module)
    permission, created = UserPermission.objects.get_or_create(
        user=target_user,
        module=module,
        defaults={"can_read": current_read, "can_write": current_write},
    )
    if created:
        permission.can_read = current_read
        permission.can_write = current_write

    if action == "read":
        permission.can_read = not current_read
        if not permission.can_read:
            permission.can_write = False
    else:
        permission.can_write = not current_write
        if permission.can_write:
            permission.can_read = True

    if target_user.pk == request.user.pk and not request.user.is_superuser:
        new_value = permission.can_write if action == "write" else permission.can_read
        if not _target_user_has_users_write_after_toggle(target_user, module, action, new_value):
            messages.error(request, "No podés quitarte tu propio permiso para gestionar usuarios.")
            return redirect("users:user_detail", target_user.pk)

    permission.save()
    messages.success(request, f"Permiso de {target_user} actualizado.")
    return redirect("users:user_detail", target_user.pk)


@login_required
def role_list(request):
    _ensure_users_permission(request, "read")
    q = (request.GET.get("q") or "").strip()
    roles = Role.objects.prefetch_related("permissions").order_by("name")
    if q:
        roles = roles.filter(Q(code__icontains=q) | Q(name__icontains=q))
    return render(
        request,
        "users/role_list.html",
        {
            "roles": roles,
            "query": q,
            "can_edit_permissions": request.user.has_module_permission(PermissionModule.USERS, "write"),
        },
    )


@login_required
def role_create(request):
    _ensure_users_permission(request, "write")
    form = RoleForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        role = form.save()
        role.ensure_permission_rows()
        messages.success(request, f"Rol {role.name} creado. Configurá sus permisos default.")
        return redirect("users:role_detail", role.pk)
    return _render_form(
        request,
        {
            "title": "Nuevo rol",
            "subtitle": "Definí el nombre del rol. Sus permisos default se configuran al guardar.",
            "form": form,
            "submit_label": "Crear rol",
            "back_url": reverse("users:role_list"),
            "form_action": reverse("users:role_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def role_detail(request, role_id: int):
    _ensure_users_permission(request, "write" if request.method == "POST" else "read")
    role = get_object_or_404(Role.objects.prefetch_related("permissions"), pk=role_id)
    form = RoleForm(request.POST or None, instance=role)
    if request.method == "POST" and form.is_valid():
        role = form.save()
        role.ensure_permission_rows()
        messages.success(request, f"Rol {role.name} actualizado.")
        return redirect("users:role_detail", role.pk)
    return render(
        request,
        "users/role_detail.html",
        {
            "role": role,
            "form": form,
            "permission_rows": _permission_rows_for_role(role),
            "can_edit_permissions": request.user.has_module_permission(PermissionModule.USERS, "write"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
@require_POST
def role_permission_toggle(request, role_id: int, module: str, action: str):
    _ensure_users_permission(request, "write")
    if module not in PermissionModule.values or action not in {"read", "write"}:
        raise PermissionDenied("Permiso inválido.")
    role = get_object_or_404(Role, pk=role_id)
    permission, _ = RolePermission.objects.get_or_create(role=role, module=module)

    current_value = permission.can_write if action == "write" else permission.can_read
    if request.user.role_id == role.pk and module == PermissionModule.USERS and current_value and not request.user.is_superuser:
        messages.error(request, "No podés quitar permisos de usuarios al rol con el que estás operando.")
        return redirect("users:role_detail", role.pk)

    if action == "read":
        permission.can_read = not permission.can_read
        if not permission.can_read:
            permission.can_write = False
    else:
        permission.can_write = not permission.can_write
        if permission.can_write:
            permission.can_read = True
    permission.save()
    messages.success(request, f"Permiso default de {role.name} actualizado.")
    return redirect("users:role_detail", role.pk)


@login_required
def account_settings(request):
    profile_form = OwnProfileForm(instance=request.user)
    password_form = PasswordChangeForm(request.user)
    _style_password_form(password_form)

    if request.method == "POST" and request.POST.get("form_kind") == "profile":
        profile_form = OwnProfileForm(request.POST, instance=request.user)
        if profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Tus datos fueron actualizados.")
            return redirect("users:account_settings")
        _style_password_form(password_form)
    elif request.method == "POST" and request.POST.get("form_kind") == "password":
        password_form = PasswordChangeForm(request.user, request.POST)
        _style_password_form(password_form)
        if password_form.is_valid():
            user = password_form.save()
            user.must_change_password = False
            user.save(update_fields=["must_change_password"])
            update_session_auth_hash(request, user)
            messages.success(request, "Contraseña actualizada.")
            return redirect("users:account_settings")

    return render(
        request,
        "users/account_settings.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
        },
        status=400 if request.method == "POST" else 200,
    )


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
        messages.success(request, "Contraseña actualizada. Ya podés operar con tu usuario.")
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
        messages.success(request, "Contraseña creada. Ingresá con tu usuario y la nueva contraseña.")
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
