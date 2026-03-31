from django.shortcuts import redirect, render


def dashboard(request):
    context = {
        "active_box": {
            "label": "Caja activa",
            "name": "Caja 04",
            "turno": "T.M.",
            "branch": "Sucursal Centro",
            "balance": "AR$ 248.500",
            "status": "Abierta",
        },
        "operational_summary": [
            {"label": "Ingresos", "value": "AR$ 320.000"},
            {"label": "Egresos", "value": "AR$ 71.500"},
            {"label": "Diferencia", "value": "AR$ 0"},
        ],
        "quick_actions": [
            {"label": "Gasto", "hint": "Carga rapida", "href": "#gastos"},
            {"label": "Venta POS", "hint": "Un solo monto", "href": "#pos"},
            {"label": "Traspaso", "hint": "Entre cajas", "href": "#traspasos"},
            {"label": "Cerrar caja", "hint": "Validacion final", "href": "#cierre"},
        ],
        "recent_movements": [
            {
                "title": "Venta por tarjeta",
                "meta": "Hace 8 min",
                "amount": "+ AR$ 42.000",
                "tone": "positive",
            },
            {
                "title": "Gasto operativo",
                "meta": "Hace 17 min",
                "amount": "- AR$ 8.500",
                "tone": "negative",
            },
            {
                "title": "Traspaso desde Caja 02",
                "meta": "Hace 31 min",
                "amount": "+ AR$ 15.000",
                "tone": "neutral",
            },
        ],
        "alerts": [
            "Faltante menor dentro de rango de cierre automatico.",
            "No hay cierres pendientes para la caja activa.",
        ],
    }
    return render(request, "core/dashboard.html", context)


home = dashboard


def public_home(request):
    if request.user.is_authenticated:
        return redirect("cashops:dashboard")
    return render(request, "core/home.html")


home = public_home
