def app_context(request):
    ctx = {"app_name": "Gerayse"}
    if not request.user.is_authenticated:
        return ctx

    from cashops.models import Empresa

    user = request.user
    all_empresas = list(Empresa.objects.filter(activa=True).order_by("nombre"))

    # Si el usuario no tiene empresas marcadas, no ve ni opera ninguna empresa.
    permitidas_ids = set(user.empresas_permitidas.values_list("pk", flat=True))
    empresas = [empresa for empresa in all_empresas if empresa.pk in permitidas_ids]

    valid_ids = {empresa.pk for empresa in empresas}
    empresa_ids = request.session.get("empresa_ids")

    # Migrar desde la clave de sesion vieja (empresa_activa_id -> empresa_ids).
    if empresa_ids is None:
        old_id = request.session.get("empresa_activa_id")
        if old_id and old_id in valid_ids:
            empresa_ids = [old_id]
            request.session["empresa_ids"] = empresa_ids
            request.session.pop("empresa_activa_id", None)

    # Sin seleccion previa, activar todas las empresas explicitamente permitidas.
    if empresa_ids is None:
        empresa_ids = [empresa.pk for empresa in empresas]
        request.session["empresa_ids"] = empresa_ids

    # Auto-seleccionar empresa_principal si no hay sesion activa todavia.
    if not empresa_ids and user.empresa_principal_id and user.empresa_principal_id in valid_ids:
        empresa_ids = [user.empresa_principal_id]
        request.session["empresa_ids"] = empresa_ids

    # Auto-seleccionar si hay una unica empresa disponible para este usuario.
    if not empresa_ids and len(empresas) == 1:
        empresa_ids = [empresas[0].pk]
        request.session["empresa_ids"] = empresa_ids

    # Limpiar IDs de sesion que ya no son validos para este usuario.
    empresa_ids = [empresa_id for empresa_id in (empresa_ids or []) if empresa_id in valid_ids]
    if empresa_ids != request.session.get("empresa_ids"):
        request.session["empresa_ids"] = empresa_ids

    empresas_activas = [empresa for empresa in empresas if empresa.pk in set(empresa_ids)]
    empresa_activa = empresas_activas[0] if len(empresas_activas) == 1 else None

    ctx["empresa_activa"] = empresa_activa
    ctx["empresas_activas"] = empresas_activas
    ctx["empresas_disponibles"] = empresas
    ctx["selected_empresa_ids_set"] = set(empresa_ids)
    return ctx
