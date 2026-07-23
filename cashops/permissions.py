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


def can_validate_cash(user) -> bool:
    return _has_module_permission(user, PermissionModule.CASHOPS_VALIDATE, "write")


def can_load_debt_on_closed_box(user) -> bool:
    return _has_module_permission(user, PermissionModule.CASHOPS_DEBT_CLOSED, "write")


def can_delete_box_movement(user) -> bool:
    return _has_module_permission(user, PermissionModule.CASHOPS_MOV_DELETE, "write")


def can_delete_movement_in_box(user, box) -> bool:
    # Permiso dedicado: elimina movimientos/deudas en cualquier caja (abierta o cerrada).
    if can_delete_box_movement(user):
        return True
    # Compatibilidad: quien ya puede corregir cajas cerradas conserva el borrado ahi.
    if box is not None and box.estado == box.Estado.CERRADA and can_correct_closed_box(user):
        return True
    return False


def ensure_delete_movement_in_box(user, box) -> None:
    if not can_delete_movement_in_box(user, box):
        raise PermissionDenied("No tenés permiso para eliminar movimientos de esta caja.")


def ensure_cash_validation(user) -> None:
    if not can_validate_cash(user):
        raise PermissionDenied("No tenes permiso para validar efectivo.")


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
