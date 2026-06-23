from django.core.exceptions import PermissionDenied

from users.models import PermissionModule


def _has_module_permission(user, module: str, action: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    checker = getattr(user, "has_module_permission", None)
    if callable(checker):
        return checker(module, action)
    return False


def is_cashops_admin(user) -> bool:
    return _has_module_permission(user, PermissionModule.CONFIG, "write")


def ensure_cashops_admin(user) -> None:
    if not is_cashops_admin(user):
        raise PermissionDenied("No tenes permisos de administrador para esta operacion.")


def ensure_cashops_read(user) -> None:
    if not _has_module_permission(user, PermissionModule.CASHOPS, "read"):
        raise PermissionDenied("No tenes permisos para ver caja operativa.")


def ensure_cashops_write(user) -> None:
    if not _has_module_permission(user, PermissionModule.CASHOPS, "write"):
        raise PermissionDenied("No tenes permisos para operar caja.")


def can_correct_closed_box(user) -> bool:
    return _has_module_permission(user, PermissionModule.CASHOPS_CLOSED_FIX, "write")


def ensure_closed_box_correction(user) -> None:
    if not can_correct_closed_box(user):
        raise PermissionDenied("No tenes permisos para corregir cajas cerradas.")


def ensure_config_read(user) -> None:
    if not _has_module_permission(user, PermissionModule.CONFIG, "read"):
        raise PermissionDenied("No tenes permisos para ver configuracion.")


def ensure_config_write(user) -> None:
    if not _has_module_permission(user, PermissionModule.CONFIG, "write"):
        raise PermissionDenied("No tenes permisos para modificar configuracion.")


def can_operate_box(user, box) -> bool:
    if is_cashops_admin(user):
        return True
    return bool(
        _has_module_permission(user, PermissionModule.CASHOPS, "write")
        and box.usuario_id == user.id
    )


def ensure_can_operate_box(user, box) -> None:
    if not can_operate_box(user, box):
        raise PermissionDenied("No tenes permiso para operar esta caja.")


def can_assign_box_to_user(actor, responsible_user) -> bool:
    if not actor or not getattr(actor, "is_authenticated", False):
        return False
    if is_cashops_admin(actor):
        return True
    return actor.pk == responsible_user.pk and _has_module_permission(actor, PermissionModule.CASHOPS, "write")
