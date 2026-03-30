from django.core.exceptions import PermissionDenied


def is_cashops_admin(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    checker = getattr(user, "is_cashops_admin", None)
    if callable(checker):
        return checker()
    return False


def ensure_cashops_admin(user) -> None:
    if not is_cashops_admin(user):
        raise PermissionDenied("No tenes permisos de administrador para esta operacion.")


def can_operate_box(user, box) -> bool:
    if is_cashops_admin(user):
        return True
    return bool(user and getattr(user, "is_authenticated", False) and box.usuario_id == user.id)


def ensure_can_operate_box(user, box) -> None:
    if not can_operate_box(user, box):
        raise PermissionDenied("No tenes permiso para operar esta caja.")


def can_assign_box_to_user(actor, responsible_user) -> bool:
    if not actor or not getattr(actor, "is_authenticated", False):
        return False
    if is_cashops_admin(actor):
        return True
    return actor.pk == responsible_user.pk
