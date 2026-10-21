"""Empresas que el usuario puede ver en asistencia (AGENTS.md seccion 8).

Es la regla estricta de los reportes de tesoreria: empresa activa, seleccionada
en la sesion Y permitida para el usuario. No confia en que el context processor
ya haya limpiado la sesion: un id viejo o tocado a mano no abre otra empresa.
No se importa el `_get_empresa_ids` de cashops/treasury porque es privado de cada
app y ademas no revalida contra los permisos.
"""

from cashops.models import Empresa, Sucursal


def empresas_en_alcance(request):
    user = request.user
    if not user.is_authenticated:
        return Empresa.objects.none()
    empresas = Empresa.objects.filter(activa=True, pk__in=user.empresas_permitidas.values("pk"))
    seleccion = request.session.get("empresa_ids")
    if seleccion is not None:
        empresas = empresas.filter(pk__in=seleccion)
    return empresas.order_by("nombre")


def sucursales_en_alcance(request):
    return Sucursal.objects.filter(activa=True, empresa__in=empresas_en_alcance(request)).order_by("nombre")
