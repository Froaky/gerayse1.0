from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.db.models import Q

from .forms import PersonalForm

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


class GerayseLogoutView(LogoutView):
    next_page = reverse_lazy("users:login")


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _hx_redirect(url: str) -> HttpResponse:
    response = HttpResponse(status=204)
    response["HX-Redirect"] = url
    return response


def _render_form(request, context: dict, status: int = 200, template: str = "cashops/form_page.html"):
    # Reusing cashops form_page for consistency if possible, or using a local one
    return render(request, template, context, status=status)


@login_required
def personal_list(request):
    if not request.user.is_cashops_admin():
        return redirect("cashops:dashboard")

    q = (request.GET.get("q") or "").strip()
    users = User.objects.select_related("role").all().order_by("last_name", "first_name")
    if q:
        users = users.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(role__name__icontains=q)
        )
    return render(
        request,
        "users/personal_list.html",
        {
            "items": users,
            "title": "Personal",
            "create_url": reverse("users:personal_create"),
            "query": q,
        }
    )


@login_required
def personal_create(request):
    if not request.user.is_cashops_admin():
        return redirect("cashops:dashboard")

    form = PersonalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Personal {user.get_full_name()} creado.")
        return _hx_redirect(reverse("users:personal_list")) if _is_htmx(request) else redirect("users:personal_list")

    return _render_form(
        request,
        {
            "title": "Nuevo Personal",
            "subtitle": "Carga los datos base, contacto y rol operativo.",
            "form": form,
            "submit_label": "Guardar Personal",
            "back_url": reverse("users:personal_list"),
            "form_action": reverse("users:personal_create"),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )


@login_required
def personal_update(request, user_id: int):
    if not request.user.is_cashops_admin():
        return redirect("cashops:dashboard")

    user = get_object_or_404(User, pk=user_id)
    form = PersonalForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Personal {user.get_full_name()} actualizado.")
        return _hx_redirect(reverse("users:personal_list")) if _is_htmx(request) else redirect("users:personal_list")

    return _render_form(
        request,
        {
            "title": f"Editar Personal: {user.get_full_name()}",
            "subtitle": "Actualiza datos operativos y rol.",
            "form": form,
            "submit_label": "Actualizar Personal",
            "back_url": reverse("users:personal_list"),
            "form_action": reverse("users:personal_update", args=[user.id]),
        },
        status=400 if request.method == "POST" and not form.is_valid() else 200,
    )
