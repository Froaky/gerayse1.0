"""Casos de uso del padron de empleados (EP-14 US-14.2).

El form no es el unico camino (la carga masiva tambien crea empleados), asi que
el permiso, el alcance por empresa y las invariantes se validan aca.
"""

import re
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
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
    # Primero el candado de la grilla de su local y despues la fila del
    # empleado: el mismo orden que la grilla, asi no se traban entre si.
    _bloquear_grilla(empleado.sucursal)
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
    # EP-14 D18: los horarios de los dias que estuvo de baja (cargados antes de
    # irse) no le hacen faltas al volver: se borran, y el historial los guarda.
    # De hoy quedan el franco y los turnos que todavia no empezaron; de mañana en
    # adelante, todo, por si la baja fue un error.
    if empleado.dado_de_baja_en:
        dia_de_baja = timezone.localdate(empleado.dado_de_baja_en)
        ahora = timezone.localtime()
        for horario in Horario.objects.filter(
            empleado=empleado, fecha__gt=dia_de_baja, fecha__lte=ahora.date()
        ).order_by("fecha"):
            antes = Celda.de_horario(horario)
            despues = Celda()
            if horario.fecha == ahora.date():
                despues = Celda(franco=antes.franco, entradas=tuple(e for e in antes.entradas if e > ahora.time()))
                if despues == antes:
                    continue
            _aplicar_celda(actor, empleado, horario.fecha, horario, antes, despues)
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
    # get_or_create y despues el bloqueo: dos guardados juntos de una empresa sin
    # reglas no chocan en el OneToOne (get_or_create reintenta el get). Si algo
    # no valida, la transaccion se deshace entera, fila nueva incluida.
    reglas, _ = ReglasAsistencia.objects.get_or_create(empresa=empresa)
    reglas = ReglasAsistencia.objects.select_for_update(of=("self",)).get(pk=reglas.pk)
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


# Llave del candado de Postgres que pone en fila a quienes guardan la grilla de
# un mismo local (EP-14 D18). Es un numero fijo que no usa ninguna otra parte.
CANDADO_GRILLA = 1418


class GrillaDesactualizada(ValidationError):
    """Otra persona cambio casillas de la grilla despues de que el usuario la
    abrio. `conflictos`: {(empleado_id, fecha): Celda que hay ahora}."""

    def __init__(self, conflictos: dict):
        self.conflictos = conflictos
        super().__init__("Otra persona cambió esta semana mientras la cargabas.")


def _bloquear_grilla(sucursal) -> None:
    """Un guardado por local a la vez. En Postgres, con un candado de la
    transaccion que no traba ninguna tabla (ni cajas ni fichajes); SQLite ya
    escribe de a uno."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [CANDADO_GRILLA, sucursal.pk])


def _aplicar_celda(actor, empleado, fecha, horario, antes: Celda, celda: Celda) -> None:
    if celda.vacia:
        # El horario se borra, pero el historial guarda lo que habia.
        horario.delete()
    else:
        horario = horario or Horario(empleado=empleado, fecha=fecha)
        horario.franco = celda.franco
        horario.entrada_1, horario.entrada_2 = (*celda.entradas, None, None)[:2]
        horario.cargado_por = actor
        horario.full_clean()
        try:
            with transaction.atomic():
                horario.save()
        except IntegrityError as error:
            # Red por si dos guardados se cruzan igual (en Postgres lo evita el candado).
            raise ValidationError("Otra persona guardó esta semana al mismo tiempo: volvé a abrirla.") from error
    HorarioCambio.objects.create(empleado=empleado, fecha=fecha, antes=antes.texto, despues=celda.texto, cambiado_por=actor)


@transaction.atomic
def guardar_semana(*, actor, sucursal, lunes, celdas: dict, originales=None, hoy=None) -> int:
    """Guarda las casillas de la grilla de un local (US-14.13).

    `celdas`: {(empleado_id, fecha): Celda nueva}; una vacia borra el horario.
    `originales`: {(empleado_id, fecha): Celda que el usuario vio al abrir la
    grilla}. Si ya no es la que hay en la base, otra persona la cambio en el
    medio: no se pisa nada y no se guarda nada (GrillaDesactualizada). Una
    casilla igual a la de la base no se toca, aunque sea de un dia cerrado.
    Cambiar un dia cerrado para el usuario, un empleado que no es de la grilla
    o un dia de otra semana frenan todo. Devuelve cuantas cambiaron (cada una
    queda en el historial).
    """
    _require_actor(actor)
    administra = _ensure_local_de_horarios(actor, sucursal)
    _bloquear_grilla(sucursal)
    hoy = hoy or timezone.localdate()
    originales = originales or {}
    dias = set(dias_de_la_semana(lunes))
    empleados = {e.pk: e for e in empleados_de_la_grilla(sucursal)}
    for empleado_id, fecha in celdas:
        if empleado_id not in empleados or fecha not in dias:
            raise ValidationError("La grilla cambió mientras la cargabas: volvé a abrirla.")
    # order_by(): sin el orden por nombre no hay JOIN con Empleado, y of=("self",)
    # bloquea solo los horarios, no las filas de empleado que usa el fichaje.
    existentes = {
        (h.empleado_id, h.fecha): h
        for h in Horario.objects.select_for_update(of=("self",))
        .filter(empleado_id__in=list(empleados), fecha__in=dias)
        .order_by()
    }
    conflictos, cambios = {}, []
    for clave, celda in celdas.items():
        actual = Celda.de_horario(existentes.get(clave))
        if actual == celda:
            # Ya esta como lo quiere: un doble envio, o el otro cargo lo mismo.
            continue
        # Un dia cerrado frena antes que un choque: volver a guardar no lo arreglaria.
        if not dia_editable(clave[1], administra=administra, hoy=hoy):
            raise PermissionDenied("Hoy y los días anteriores solo los cambia administración.")
        if clave in originales and originales[clave] != actual:
            conflictos[clave] = actual
            continue
        cambios.append((clave, actual, celda))
    if conflictos:
        raise GrillaDesactualizada(conflictos)
    for (empleado_id, fecha), actual, celda in cambios:
        _aplicar_celda(actor, empleados[empleado_id], fecha, existentes.get((empleado_id, fecha)), actual, celda)
    return len(cambios)


@transaction.atomic
def copiar_semana_anterior(*, actor, sucursal, lunes, hoy=None) -> int:
    """Llena con la semana anterior solo las casillas vacias que el usuario
    puede tocar: nunca pisa lo ya cargado."""
    _require_actor(actor)
    administra = _ensure_local_de_horarios(actor, sucursal)
    _bloquear_grilla(sucursal)
    hoy = hoy or timezone.localdate()
    empleados = list(empleados_de_la_grilla(sucursal))
    dias = [d for d in dias_de_la_semana(lunes) if dia_editable(d, administra=administra, hoy=hoy)]
    ocupados = set(
        Horario.objects.filter(empleado__in=empleados, fecha__in=dias).order_by().values_list("empleado_id", "fecha")
    )
    anteriores = Horario.objects.filter(empleado__in=empleados, fecha__in=[d - timedelta(days=7) for d in dias]).order_by()
    celdas = {
        (h.empleado_id, h.fecha + timedelta(days=7)): Celda.de_horario(h)
        for h in anteriores
        if (h.empleado_id, h.fecha + timedelta(days=7)) not in ocupados
    }
    # Se copian solo sobre casillas vacias: si en el medio alguien cargo una, no se pisa.
    originales = {clave: Celda() for clave in celdas}
    return guardar_semana(actor=actor, sucursal=sucursal, lunes=lunes, celdas=celdas, originales=originales, hoy=hoy)
