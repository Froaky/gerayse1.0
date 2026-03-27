from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import AlertaOperativa, Caja, CierreCaja, Justificacion, MovimientoCaja, Sucursal, Transferencia, Turno


CLOSING_DIFF_THRESHOLD = Decimal("10000.00")


def _lock_caja(caja: Caja) -> Caja:
    return Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)


def _create_movement(
    *,
    caja: Caja,
    tipo: str,
    sentido: str,
    monto: Decimal,
    categoria: str = "",
    observacion: str = "",
    transferencia: Transferencia | None = None,
    creado_por=None,
) -> MovimientoCaja:
    return MovimientoCaja.objects.create(
        caja=caja,
        tipo=tipo,
        sentido=sentido,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        transferencia=transferencia,
        creado_por=creado_por,
    )


def calculate_expected_balance(caja: Caja) -> Decimal:
    caja.refresh_from_db()
    return caja.saldo_esperado


@transaction.atomic
def open_box(*, user, turno: Turno, sucursal: Sucursal, monto_inicial: Decimal) -> Caja:
    if user is None:
        raise ValidationError({"usuario": "Se requiere un usuario para abrir una caja."})
    if turno.estado != Turno.Estado.ABIERTO:
        raise ValidationError({"turno": "El turno debe estar abierto para abrir una caja."})
    if turno.sucursal_id != sucursal.id:
        raise ValidationError({"sucursal": "La sucursal debe coincidir con el turno."})
    if monto_inicial < 0:
        raise ValidationError({"monto_inicial": "El monto inicial no puede ser negativo."})

    turno = Turno.objects.select_for_update().select_related("sucursal").get(pk=turno.pk)
    caja = Caja.objects.create(
        sucursal=sucursal,
        turno=turno,
        usuario=user,
        monto_inicial=monto_inicial,
        estado=Caja.Estado.ABIERTA,
        abierta_en=timezone.now(),
    )
    _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.APERTURA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto_inicial,
        categoria="APERTURA",
        observacion="Monto inicial de caja",
        creado_por=user,
    )
    return caja


def _validate_open_box(caja: Caja) -> Caja:
    caja = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").get(pk=caja.pk)
    if caja.estado != Caja.Estado.ABIERTA:
        raise ValidationError({"caja": "La caja esta cerrada."})
    if caja.turno.estado != Turno.Estado.ABIERTO:
        raise ValidationError({"turno": "El turno de la caja esta cerrado."})
    return caja


@transaction.atomic
def register_expense(
    *,
    caja: Caja,
    monto: Decimal,
    categoria: str,
    observacion: str = "",
    creado_por=None,
) -> MovimientoCaja:
    caja = _validate_open_box(caja)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.GASTO,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria=categoria,
        observacion=observacion,
        creado_por=creado_por,
    )


@transaction.atomic
def register_card_sale(*, caja: Caja, monto: Decimal, observacion: str = "", creado_por=None) -> MovimientoCaja:
    caja = _validate_open_box(caja)
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    return _create_movement(
        caja=caja,
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        categoria="POS",
        observacion=observacion,
        creado_por=creado_por,
    )


@transaction.atomic
def transfer_between_boxes(
    *,
    caja_origen: Caja,
    caja_destino: Caja,
    monto: Decimal,
    observacion: str = "",
    creado_por=None,
) -> Transferencia:
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})

    if caja_origen.pk == caja_destino.pk:
        raise ValidationError({"caja_destino": "El origen y el destino no pueden ser la misma caja."})

    cajas = Caja.objects.select_for_update().select_related("sucursal", "turno", "usuario").filter(
        pk__in=[caja_origen.pk, caja_destino.pk]
    ).order_by("pk")
    locked = {box.pk: box for box in cajas}
    caja_origen = _validate_open_box(locked[caja_origen.pk])
    caja_destino = _validate_open_box(locked[caja_destino.pk])

    transferencia = Transferencia.objects.create(
        tipo=Transferencia.Tipo.ENTRE_CAJAS,
        clase=Transferencia.Clase.DINERO,
        caja_origen=caja_origen,
        caja_destino=caja_destino,
        sucursal_origen=caja_origen.sucursal,
        sucursal_destino=caja_destino.sucursal,
        monto=monto,
        observacion=observacion,
        creado_por=creado_por,
    )
    _create_movement(
        caja=caja_origen,
        tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SALIDA,
        sentido=MovimientoCaja.Sentido.EGRESO,
        monto=monto,
        categoria="TRANSFERENCIA",
        observacion=observacion,
        transferencia=transferencia,
        creado_por=creado_por,
    )
    _create_movement(
        caja=caja_destino,
        tipo=MovimientoCaja.Tipo.TRANSFERENCIA_ENTRADA,
        sentido=MovimientoCaja.Sentido.INGRESO,
        monto=monto,
        categoria="TRANSFERENCIA",
        observacion=observacion,
        transferencia=transferencia,
        creado_por=creado_por,
    )
    return transferencia


@transaction.atomic
def transfer_between_branches(
    *,
    sucursal_origen: Sucursal,
    sucursal_destino: Sucursal,
    clase: str,
    monto: Decimal | None = None,
    observacion: str = "",
    caja_origen: Caja | None = None,
    caja_destino: Caja | None = None,
    creado_por=None,
) -> Transferencia:
    if sucursal_origen.pk == sucursal_destino.pk:
        raise ValidationError({"sucursal_destino": "El origen y el destino no pueden ser la misma sucursal."})

    if clase == Transferencia.Clase.DINERO and (monto is None or monto <= 0):
        raise ValidationError({"monto": "El monto es obligatorio para transferencias de dinero."})
    if clase == Transferencia.Clase.MERCADERIA and not observacion:
        raise ValidationError({"observacion": "La observacion es obligatoria para mercaderia."})

    if caja_origen and caja_origen.sucursal_id != sucursal_origen.pk:
        raise ValidationError({"caja_origen": "La caja de origen debe pertenecer a la sucursal origen."})
    if caja_destino and caja_destino.sucursal_id != sucursal_destino.pk:
        raise ValidationError({"caja_destino": "La caja de destino debe pertenecer a la sucursal destino."})

    transferencia = Transferencia.objects.create(
        tipo=Transferencia.Tipo.ENTRE_SUCURSALES,
        clase=clase,
        caja_origen=caja_origen,
        caja_destino=caja_destino,
        sucursal_origen=sucursal_origen,
        sucursal_destino=sucursal_destino,
        monto=monto if clase == Transferencia.Clase.DINERO else None,
        observacion=observacion,
        creado_por=creado_por,
    )

    if clase == Transferencia.Clase.DINERO and caja_origen and caja_destino:
        cajas = Caja.objects.select_for_update().select_related("turno", "sucursal", "usuario").filter(
            pk__in=[caja_origen.pk, caja_destino.pk]
        ).order_by("pk")
        locked = {box.pk: box for box in cajas}
        caja_origen = _validate_open_box(locked[caja_origen.pk])
        caja_destino = _validate_open_box(locked[caja_destino.pk])
        _create_movement(
            caja=caja_origen,
            tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_SALIDA,
            sentido=MovimientoCaja.Sentido.EGRESO,
            monto=monto,
            categoria="TRANSFERENCIA SUCURSAL",
            observacion=observacion,
            transferencia=transferencia,
            creado_por=creado_por,
        )
        _create_movement(
            caja=caja_destino,
            tipo=MovimientoCaja.Tipo.TRANSFERENCIA_SUCURSAL_ENTRADA,
            sentido=MovimientoCaja.Sentido.INGRESO,
            monto=monto,
            categoria="TRANSFERENCIA SUCURSAL",
            observacion=observacion,
            transferencia=transferencia,
            creado_por=creado_por,
        )

    return transferencia


@transaction.atomic
def close_box(
    *,
    caja: Caja,
    saldo_fisico: Decimal,
    justificacion: str = "",
    cerrado_por=None,
) -> CierreCaja:
    caja_ref = caja
    caja = _lock_caja(caja)
    if caja.estado != Caja.Estado.ABIERTA:
        raise ValidationError({"caja": "La caja ya esta cerrada."})

    saldo_esperado = caja.saldo_esperado
    diferencia = saldo_fisico - saldo_esperado
    abs_difference = abs(diferencia)

    if abs_difference > CLOSING_DIFF_THRESHOLD and not justificacion.strip():
        raise ValidationError({"justificacion": "La diferencia supera 10.000 y requiere justificacion."})

    ajuste_movimiento = None
    if diferencia != 0:
        ajuste_movimiento = _create_movement(
            caja=caja,
            tipo=MovimientoCaja.Tipo.AJUSTE_CIERRE,
            sentido=MovimientoCaja.Sentido.INGRESO if diferencia > 0 else MovimientoCaja.Sentido.EGRESO,
            monto=abs_difference,
            categoria="CIERRE",
            observacion=(
                "Ajuste de cierre automatico"
                if abs_difference <= CLOSING_DIFF_THRESHOLD
                else "Ajuste de cierre con diferencia grave"
            ),
            creado_por=cerrado_por,
        )

    cierre = CierreCaja.objects.create(
        caja=caja,
        saldo_esperado=saldo_esperado,
        saldo_fisico=saldo_fisico,
        diferencia=diferencia,
        estado=CierreCaja.Estado.JUSTIFICADO if abs_difference > CLOSING_DIFF_THRESHOLD else CierreCaja.Estado.AUTO,
        ajuste_movimiento=ajuste_movimiento,
        cerrado_por=cerrado_por,
    )

    if abs_difference > CLOSING_DIFF_THRESHOLD and justificacion.strip():
        Justificacion.objects.create(cierre=cierre, motivo=justificacion.strip(), creado_por=cerrado_por)
        AlertaOperativa.objects.create(
            cierre=cierre,
            caja=caja,
            sucursal=caja.sucursal,
            mensaje=f"Diferencia grave detectada en caja {caja.id}: {diferencia}.",
        )

    caja.estado = Caja.Estado.CERRADA
    caja.cerrada_en = timezone.now()
    caja.cerrada_por = cerrado_por
    caja.save(update_fields=["estado", "cerrada_en", "cerrada_por"])
    if not caja.turno.cajas.filter(estado=Caja.Estado.ABIERTA).exists():
        caja.turno.estado = Turno.Estado.CERRADO
        caja.turno.cerrado_en = timezone.now()
        caja.turno.save(update_fields=["estado", "cerrado_en"])
    caja_ref.estado = caja.estado
    caja_ref.cerrada_en = caja.cerrada_en
    caja_ref.cerrada_por = caja.cerrada_por
    return cierre
