def app_context(request):
    ctx = {"app_name": "Gerayse"}
    if not request.user.is_authenticated:
        return ctx

    from cashops.models import Empresa

    empresas = list(Empresa.objects.filter(activa=True).order_by("nombre"))
    empresa_activa = None
    eid = request.session.get("empresa_activa_id")
    if eid:
        empresa_activa = next((e for e in empresas if e.pk == eid), None)
        if empresa_activa is None:
            request.session.pop("empresa_activa_id", None)
    if empresa_activa is None and len(empresas) == 1:
        empresa_activa = empresas[0]
        request.session["empresa_activa_id"] = empresa_activa.pk
    ctx["empresa_activa"] = empresa_activa
    ctx["empresas_disponibles"] = empresas
    return ctx
