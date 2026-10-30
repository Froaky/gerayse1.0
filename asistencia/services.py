"""Casos de uso del padron de empleados (EP-14 US-14.2).

El form no es el unico camino (la carga masiva tambien crea empleados), asi que
el permiso, el alcance por empresa y las invariantes se validan aca.
"""

import re
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .horarios import Celda, dia_editable, dias_de_la_semana
from .models import CarnetAnulado, Empleado, Horario, HorarioCambio, Marca, ReglasAsistencia, nuevo_codigo_carnet
from .permissions import can_administrar, ensure_administrar, ensure_cargar_horarios


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
    if not empleado.dni and empleado._nombre_ocupado():
        raise ValidationError(
            {"nombre": "Ya hay un empleado activo con ese nombre en esta empresa. Si es otra persona, cargale el DNI."}
        )
    empleado.activo = True
    empleado.dado_de_baja_en = None
    empleado.dado_de_baja_por = None
    empleado.save(update_fields=["activo", "dado_de_baja_en", "dado_de_baja_por", "actualizado_en"])
    return empleado


def _codigo_libre() -> str:
    # nuevo_codigo_carnet ya descarta los codigos vigentes y los anulados.
    return nuevo_codigo_carnet()


@transaction.atomic
def anular_carnet(*, actor, empleado: Empleado, motivo: str, anulaciones_previas: int | None = None) -> Empleado:
    """El carnet vigente deja de servir para fichar y el empleado recibe uno
    nuevo para imprimir. Queda quien, cuando y por que (US-14.3).

    `anulaciones_previas` es cuantas anulaciones veia la pantalla: si mientras
    tanto se anulo (doble click, otra pestana), no se anula el carnet nuevo
    que quiza ya se imprimio."""
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empleado.empresa)
    motivo = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", motivo or "").split())
    if not motivo:
        raise ValidationError({"motivo": "Contá por qué se anula el carnet."})
    empleado = Empleado.objects.select_for_update(of=("self",)).get(pk=empleado.pk)
    if not empleado.activo:
        raise ValidationError("El empleado está dado de baja: su carnet ya no sirve para fichar.")
    if anulaciones_previas is not None and empleado.carnets_anulados.count() != anulaciones_previas:
        raise ValidationError(
            "Este carnet ya se anuló recién y el vigente es otro. Si también hay que anular ese, volvé a intentarlo."
        )
    CarnetAnulado.objects.create(
        empleado=empleado,
        codigo=empleado.codigo_carnet,
        motivo=motivo[:200],
        anulado_por=actor,
        anulado_en=timezone.now(),
    )
    empleado.codigo_carnet = _codigo_libre()
    empleado.save(update_fields=["codigo_carnet", "actualizado_en"])
    return empleado


def _limpiar_motivo(motivo: str) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", motivo or "").split())[:200]


@transaction.atomic
def agregar_marca(*, actor, empleado: Empleado, momento, tipo: str, sucursal, motivo: str) -> Marca:
    """Marca cargada a mano por administracion (US-14.5): alguien se olvido el
    carnet, se corto internet o llego antes que el cajero. Queda senalada como
    manual, con motivo, quien y cuando."""
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empleado.empresa)
    motivo = _limpiar_motivo(motivo)
    if not motivo:
        raise ValidationError({"motivo": "Contá por qué se agrega la marca."})
    if tipo not in Marca.Tipo.values:
        raise ValidationError({"tipo": "Elegí si es una entrada o una salida."})
    if sucursal.empresa_id is None or not sucursal.activa:
        raise ValidationError({"sucursal": "Elegí un local activo."})
    _ensure_empresa_permitida(actor, sucursal.empresa)
    if momento > timezone.now() + timedelta(minutes=5):
        raise ValidationError({"hora": "No se puede cargar una marca en el futuro."})
    Empleado.objects.select_for_update(of=("self",)).get(pk=empleado.pk)
    if Marca.objects.filter(empleado=empleado, tipo=tipo, momento=momento, anulada=False).exists():
        raise ValidationError("Ya hay una marca igual para ese empleado a esa hora.")
    return Marca.objects.create(
        empleado=empleado,
        tipo=tipo,
        momento=momento,
        sucursal=sucursal,
        origen=Marca.Origen.MANUAL,
        registrada_por=actor,
        motivo=motivo,
    )


@transaction.atomic
def anular_marca(*, actor, marca: Marca, motivo: str) -> Marca:
    """La marca deja de contar pero no se borra (US-14.5)."""
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, marca.empleado.empresa)
    motivo = _limpiar_motivo(motivo)
    if not motivo:
        raise ValidationError({"motivo": "Contá por qué se anula la marca."})
    marca = Marca.objects.select_for_update(of=("self",)).get(pk=marca.pk)
    if marca.anulada:
        raise ValidationError("Esa marca ya está anulada.")
    marca.anulada = True
    marca.anulada_en = timezone.now()
    marca.anulada_por = actor
    marca.motivo_anulacion = motivo
    marca.save(update_fields=["anulada", "anulada_en", "anulada_por", "motivo_anulacion"])
    return marca


@transaction.atomic
def guardar_reglas(*, actor, empresa, jornada_minutos: int, tolerancia_mensual_minutos: int) -> ReglasAsistencia:
    """Reglas de calculo de una empresa (EP-14 D18 / US-14.10)."""
    _require_actor(actor)
    ensure_administrar(actor)
    _ensure_empresa_permitida(actor, empresa)
    reglas = ReglasAsistencia.objects.select_for_update().filter(empresa=empresa).first()
    reglas = reglas or ReglasAsistencia(empresa=empresa)
    reglas.jornada_minutos = jornada_minutos
    reglas.tolerancia_mensual_minutos = tolerancia_mensual_minutos
    reglas.actualizado_por = actor
    reglas.actualizado_en = timezone.now()
    reglas.full_clean()
    reglas.save()
    return reglas


def empleados_de_la_grilla(sucursal):
    """Las filas de la grilla de horarios de un local: sus empleados activos."""
    return Empleado.objects.filter(activo=True, sucursal=sucursal, empresa_id=sucursal.empresa_id).order_by("nombre", "pk")


def _ensure_local_de_horarios(actor, sucursal) -> bool:
    """Revalida en el servicio lo que la vista ya filtro (EP-14 D18): quien
    administra carga en los locales de sus empresas; el encargado, solo en su
    sucursal base y los otros locales donde ficha. Devuelve si administra."""
    ensure_cargar_horarios(actor)
    if not sucursal.activa or not actor.empresas_permitidas.filter(pk=sucursal.empresa_id).exists():
        raise PermissionDenied("No podés cargar horarios de ese local.")
    administra = can_administrar(actor)
    if not administra and not actor.locales_de_fichaje().filter(pk=sucursal.pk).exists():
        raise PermissionDenied("No podés cargar horarios de ese local.")
    return administra


def _aplicar_celda(actor, empleado, fecha, horario, celda: Celda) -> bool:
    antes = Celda.de_horario(horario)
    if antes == celda:
        return False
    if celda.vacia:
        # El horario se borra, pero el historial guarda lo que habia.
        horario.delete()
    else:
        horario = horario or Horario(empleado=empleado, fecha=fecha)
        horario.franco = celda.franco
        horario.entrada_1, horario.entrada_2 = (*celda.entradas, None, None)[:2]
        horario.cargado_por = actor
        horario.full_clean()
        horario.save()
    HorarioCambio.objects.create(empleado=empleado, fecha=fecha, antes=antes.texto, despues=celda.texto, cambiado_por=actor)
    return True


@transaction.atomic
def guardar_semana(*, actor, sucursal, lunes, celdas: dict, hoy=None) -> int:
    """Guarda las casillas de la grilla de un local (US-14.13).

    `celdas`: {(empleado_id, fecha): Celda}. Solo se tocan las que vienen; una
    vacia borra el horario. Una casilla de un empleado que no es de la grilla,
    de otra semana o de un dia cerrado para el usuario frena todo: no se guarda
    nada. Devuelve cuantas cambiaron (cada una queda en el historial).
    """
    _require_actor(actor)
    administra = _ensure_local_de_horarios(actor, sucursal)
    hoy = hoy or timezone.localdate()
    dias = set(dias_de_la_semana(lunes))
    empleados = {e.pk: e for e in empleados_de_la_grilla(sucursal)}
    for empleado_id, fecha in celdas:
        if empleado_id not in empleados or fecha not in dias:
            raise ValidationError("La grilla cambió mientras la cargabas: volvé a abrirla.")
        if not dia_editable(fecha, administra=administra, hoy=hoy):
            raise PermissionDenied("Hoy y los días anteriores solo los cambia administración.")
    existentes = {
        (h.empleado_id, h.fecha): h
        for h in Horario.objects.select_for_update().filter(empleado_id__in=list(empleados), fecha__in=dias)
    }
    return sum(
        _aplicar_celda(actor, empleados[empleado_id], fecha, existentes.get((empleado_id, fecha)), celda)
        for (empleado_id, fecha), celda in celdas.items()
    )


@transaction.atomic
def copiar_semana_anterior(*, actor, sucursal, lunes, hoy=None) -> int:
    """Llena con la semana anterior solo las casillas vacias que el usuario
    puede tocar: nunca pisa lo ya cargado."""
    _require_actor(actor)
    administra = _ensure_local_de_horarios(actor, sucursal)
    hoy = hoy or timezone.localdate()
    empleados = list(empleados_de_la_grilla(sucursal))
    dias = [d for d in dias_de_la_semana(lunes) if dia_editable(d, administra=administra, hoy=hoy)]
    ocupados = set(
        Horario.objects.select_for_update()
        .filter(empleado__in=empleados, fecha__in=dias)
        .values_list("empleado_id", "fecha")
    )
    anteriores = Horario.objects.filter(empleado__in=empleados, fecha__in=[d - timedelta(days=7) for d in dias])
    celdas = {
        (h.empleado_id, h.fecha + timedelta(days=7)): Celda.de_horario(h)
        for h in anteriores
        if (h.empleado_id, h.fecha + timedelta(days=7)) not in ocupados
    }
    return guardar_semana(actor=actor, sucursal=sucursal, lunes=lunes, celdas=celdas, hoy=hoy)
