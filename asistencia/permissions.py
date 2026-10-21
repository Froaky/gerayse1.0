from django.core.exceptions import PermissionDenied

from users.models import PermissionModule


def _has_module_permission(user, module: str, action: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    checker = getattr(user, "has_module_permission", None)
    if callable(checker):
        return checker(module, action)
    return False


# EP-14: los dos son permisos por accion, como los de EP-13: se chequean con
# "write". Lectura sola en la matriz no habilita nada.
def can_fichar(user) -> bool:
    return _has_module_permission(user, PermissionModule.ASISTENCIA_FICHAR, "write")


def can_administrar(user) -> bool:
    return _has_module_permission(user, PermissionModule.ASISTENCIA_ADMIN, "write")


def can_acceder(user) -> bool:
    return can_fichar(user) or can_administrar(user)


def ensure_fichar(user) -> None:
    if not can_fichar(user):
        raise PermissionDenied("No tenes permiso para fichar asistencia.")


def ensure_administrar(user) -> None:
    if not can_administrar(user):
        raise PermissionDenied("No tenes permiso para administrar asistencia.")


def ensure_acceder(user) -> None:
    if not can_acceder(user):
        raise PermissionDenied("No tenes permiso para entrar a asistencia.")
