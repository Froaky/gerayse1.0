from django.core.exceptions import PermissionDenied

from users.models import PermissionModule


def is_treasury_admin(user) -> bool:
    checker = getattr(user, "has_module_permission", None)
    if callable(checker):
        return checker(PermissionModule.TREASURY, "write")
    return False


def ensure_treasury_permission(user, action: str = "write") -> None:
    checker = getattr(user, "has_module_permission", None)
    if not callable(checker) or not checker(PermissionModule.TREASURY, action):
        raise PermissionDenied("No tenes permisos de tesoreria para esta operacion.")


def ensure_treasury_admin(user) -> None:
    ensure_treasury_permission(user, "write")


def can_delete_central_cash_movement(user) -> bool:
    """Anular un movimiento de la boveda es un permiso aparte de cargar.

    Cargar un movimiento y sacarle plata a la caja fuerte no son la misma
    responsabilidad. En produccion lo tienen solo dos personas de administracion.
    """
    checker = getattr(user, "has_module_permission", None)
    if callable(checker):
        return checker(PermissionModule.TREASURY_MOV_DELETE, "write")
    return False


def ensure_delete_central_cash_movement(user) -> None:
    if not can_delete_central_cash_movement(user):
        raise PermissionDenied("No tenes permiso para anular movimientos de la caja fuerte.")


def _require_treasury_admin(request) -> None:
    ensure_treasury_permission(request.user, "write" if request.method != "GET" else "read")

