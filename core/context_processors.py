def app_context(request):
    ctx = {"app_name": "Gerayse"}
    if not request.user.is_authenticated:
        return ctx

    from cashops.models import Empresa

    empresas = list(Empresa.objects.filter(activa=True).order_by("nombre"))
    valid_ids = {e.pk for e in empresas}

    empresa_ids = request.session.get("empresa_ids")

    # Migrar desde la clave de sesión vieja (empresa_activa_id → empresa_ids)
    if empresa_ids is None:
        old_id = request.session.get("empresa_activa_id")
        if old_id and old_id in valid_ids:
            empresa_ids = [old_id]
            request.session["empresa_ids"] = empresa_ids
            request.session.pop("empresa_activa_id", None)

    # Auto-seleccionar si hay una única empresa activa
    if not empresa_ids and len(empresas) == 1:
        empresa_ids = [empresas[0].pk]
        request.session["empresa_ids"] = empresa_ids

    empresa_ids = [eid for eid in (empresa_ids or []) if eid in valid_ids]
    if empresa_ids != request.session.get("empresa_ids"):
        request.session["empresa_ids"] = empresa_ids

    empresas_activas = [e for e in empresas if e.pk in set(empresa_ids)]
    # empresa_activa solo se setea cuando hay exactamente 1 seleccionada (compatibilidad)
    empresa_activa = empresas_activas[0] if len(empresas_activas) == 1 else None

    ctx["empresa_activa"] = empresa_activa
    ctx["empresas_activas"] = empresas_activas
    ctx["empresas_disponibles"] = empresas
    ctx["selected_empresa_ids_set"] = set(empresa_ids)
    return ctx
