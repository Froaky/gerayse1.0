from django.shortcuts import redirect, render


def public_home(request):
    """Landing publica. Si ya hay sesion, va derecho a la operacion de cajas."""
    if request.user.is_authenticated:
        return redirect("cashops:dashboard")
    return render(request, "core/home.html")


# config/urls.py importa core.views.home para la raiz del sitio.
home = public_home
