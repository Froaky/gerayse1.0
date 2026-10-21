"""Casos de uso del padron de empleados (EP-14 US-14.2).

El form no es el unico camino (la carga masiva tambien crea empleados), asi que
el permiso, el alcance por empresa y las invariantes se validan aca.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Empleado
from .permissions import ensure_administrar


def _require_actor(actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Hace falta un usuario para operar el padrón.")


def _ensure_empresa_permitida(actor, empresa) -> None:
    if not actor.empresas_permitidas.filter(pk=empresa.pk).exists():
        raise ValidationError({"empresa": "No tenés acceso a esa empresa."})


def _guardar(empleado: Empleado) -> Empleado:
    empleado.full_clean()
    empleado.save()
    return empleado


@transaction.atomic
def crear_empleado(*, actor, nombre: str, dni: str, empresa, sucursal) -> Empleado:
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empresa)
    return _guardar(Empleado(nombre=nombre, dni=dni, empresa=empresa, sucursal=sucursal, creado_por=actor))


@transaction.atomic
def actualizar_empleado(*, actor, empleado: Empleado, nombre: str, dni: str, empresa, sucursal) -> Empleado:
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empleado.empresa)
    _ensure_empresa_permitida(actor, empresa)
    empleado = Empleado.objects.select_for_update(of=("self",)).get(pk=empleado.pk)
    empleado.nombre = nombre
    empleado.dni = dni
    empleado.empresa = empresa
    empleado.sucursal = sucursal
    return _guardar(empleado)


@transaction.atomic
def dar_de_baja_empleado(*, actor, empleado: Empleado) -> Empleado:
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empleado.empresa)
    # of=("self",): el modelo tiene FK nullable y sus LEFT JOIN invalidan el
    # FOR UPDATE en Postgres (el SQLite local no lo muestra).
    empleado = Empleado.objects.select_for_update(of=("self",)).get(pk=empleado.pk)
    if not empleado.activo:
        raise ValidationError("El empleado ya está dado de baja.")
    empleado.activo = False
    empleado.dado_de_baja_en = timezone.now()
    empleado.dado_de_baja_por = actor
    # Sin full_clean a proposito: la baja no cambia datos del empleado, y no
    # puede quedar trabada porque a su local le cambiaron la empresa despues.
    empleado.save(update_fields=["activo", "dado_de_baja_en", "dado_de_baja_por", "actualizado_en"])
    return empleado


@transaction.atomic
def reactivar_empleado(*, actor, empleado: Empleado) -> Empleado:
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empleado.empresa)
    empleado = Empleado.objects.select_for_update(of=("self",)).get(pk=empleado.pk)
    if empleado.activo:
        raise ValidationError("El empleado ya está activo.")
    # Si mientras tanto se cargo otro activo con el mismo DNI, no se reactiva.
    # Como en la baja, no se revalida el local: eso se corrige editando.
    if empleado._dni_ocupado():
        raise ValidationError({"dni": "Ya hay un empleado activo con ese DNI."})
    empleado.activo = True
    empleado.dado_de_baja_en = None
    empleado.dado_de_baja_por = None
    empleado.save(update_fields=["activo", "dado_de_baja_en", "dado_de_baja_por", "actualizado_en"])
    return empleado
