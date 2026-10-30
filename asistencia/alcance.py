"""Empresas que el usuario puede ver en asistencia (AGENTS.md seccion 8).

Es la regla estricta de los reportes de tesoreria: empresa activa, seleccionada
en la sesion Y permitida para el usuario. No confia en que el context processor
ya haya limpiado la sesion: un id viejo o tocado a mano no abre otra empresa.
No se importa el `_get_empresa_ids` de cashops/treasury porque es privado de cada
app y ademas no revalida contra los permisos.
"""

from cashops.models import Empresa, Sucursal

from .permissions import can_administrar, can_cargar_horarios


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


def locales_de_horarios(request):
    """Locales cuya grilla de horarios puede cargar el usuario (EP-14 D18).

    Quien administra: todos los de su alcance. El encargado (permiso de cargar
    horarios): su sucursal base y los otros locales donde ficha, y de esos solo
    los que esten en su alcance de empresas. Sin permiso, ninguno.
    """
    user = request.user
    alcance = sucursales_en_alcance(request)
    if can_administrar(user):
        return alcance
    if can_cargar_horarios(user):
        return alcance.filter(pk__in=user.locales_de_fichaje().values("pk"))
    return Sucursal.objects.none()
