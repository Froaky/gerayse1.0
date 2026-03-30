from django.core.exceptions import PermissionDenied


def is_treasury_admin(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    checker = getattr(user, "is_cashops_admin", None)
    if callable(checker):
        return checker()
    return False


def ensure_treasury_admin(user) -> None:
    if not is_treasury_admin(user):
        raise PermissionDenied("No tenes permisos de tesoreria para esta operacion.")

