from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .permissions import can_administrar, can_fichar, ensure_acceder


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
