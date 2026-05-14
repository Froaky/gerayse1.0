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


def _require_treasury_admin(request) -> None:
    ensure_treasury_permission(request.user, "write" if request.method != "GET" else "read")

