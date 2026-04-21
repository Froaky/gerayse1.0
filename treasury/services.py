from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from .permissions import ensure_treasury_admin


def _require_actor(actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Se requiere usuario para operar tesoreria.")
    ensure_treasury_admin(actor)


def _save_instance(instance):
    instance.full_clean()
    instance.save()
    return instance


def _first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def _recalculate_payable_locked(payable: CuentaPorPagar) -> CuentaPorPagar:
    total_pagado = (
        PagoTesoreria.objects.filter(
            cuenta_por_pagar=payable,
            estado=PagoTesoreria.Estado.REGISTRADO,
        ).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    payable.saldo_pendiente = payable.importe_total - total_pagado
    if payable.saldo_pendiente == payable.importe_total:
        payable.estado = CuentaPorPagar.Estado.PENDIENTE
    elif payable.saldo_pendiente == Decimal("0.00"):
        payable.estado = CuentaPorPagar.Estado.PAGADA
    else:
        payable.estado = CuentaPorPagar.Estado.PARCIAL
    payable.full_clean()
    payable.save()
    return payable


def create_supplier(
    *,
    razon_social,
    identificador_fiscal="",
    direccion="",
    contacto="",
    telefono="",
    email="",
    sitio_web="",
    alias_bancario="",
    cbu="",
    observaciones="",
    activo=True,
    actor=None,
) -> Proveedor:
    _require_actor(actor)
    supplier = Proveedor(
        razon_social=razon_social,
        identificador_fiscal=identificador_fiscal,
        direccion=direccion,
        contacto=contacto,
        telefono=telefono,
        email=email,
        sitio_web=sitio_web,
        alias_bancario=alias_bancario,
        cbu=cbu,
        observaciones=observaciones,
        activo=activo,
        creado_por=actor,
    )
    return _save_instance(supplier)


def update_supplier(
    *,
    supplier: Proveedor,
    razon_social,
    identificador_fiscal="",
    direccion="",
    contacto="",
    telefono="",
    email="",
    sitio_web="",
    alias_bancario="",
    cbu="",
    observaciones="",
    activo=True,
    actor=None,
) -> Proveedor:
    _require_actor(actor)
    supplier.razon_social = razon_social
    supplier.identificador_fiscal = identificador_fiscal
    supplier.direccion = direccion
    supplier.contacto = contacto
    supplier.telefono = telefono
    supplier.email = email
    supplier.sitio_web = sitio_web
    supplier.alias_bancario = alias_bancario
    supplier.cbu = cbu
    supplier.observaciones = observaciones
    supplier.activo = activo
    supplier.actualizado_por = actor
    return _save_instance(supplier)


def toggle_supplier(*, supplier: Proveedor, actor=None) -> Proveedor:
    _require_actor(actor)
    supplier.activo = not supplier.activo
    supplier.save(update_fields=["activo", "actualizado_en"])
    return supplier


def create_payable_category(*, nombre, actor=None, activo=True, rubro_operativo=None) -> CategoriaCuentaPagar:
    _require_actor(actor)
    category = CategoriaCuentaPagar(
        nombre=nombre,
        rubro_operativo=rubro_operativo,
        activo=activo,
        creado_por=actor,
    )
    return _save_instance(category)


def update_payable_category(
    *,
    category: CategoriaCuentaPagar,
    nombre,
    actor=None,
    activo=True,
    rubro_operativo=None,
) -> CategoriaCuentaPagar:
    _require_actor(actor)
    category.nombre = nombre
    category.rubro_operativo = rubro_operativo
    category.activo = activo
    category.actualizado_por = actor
    return _save_instance(category)


def toggle_payable_category(*, category: CategoriaCuentaPagar, actor=None) -> CategoriaCuentaPagar:
    _require_actor(actor)
    category.activo = not category.activo
    category.save(update_fields=["activo", "actualizado_en"])
    return category


def create_bank_account(
    *,
    nombre,
    banco,
    tipo_cuenta,
    numero_cuenta,
    alias="",
    cbu="",
    sucursal_bancaria="",
    sucursal=None,
    activa=True,
    actor=None,
) -> CuentaBancaria:
    _require_actor(actor)
    bank_account = CuentaBancaria(
        nombre=nombre,
        banco=banco,
        tipo_cuenta=tipo_cuenta,
        numero_cuenta=numero_cuenta,
        alias=alias,
        cbu=cbu,
        sucursal_bancaria=sucursal_bancaria,
        sucursal=sucursal,
        activa=activa,
        creado_por=actor,
    )
    return _save_instance(bank_account)


def update_bank_account(
    *,
    bank_account: CuentaBancaria,
    nombre,
    banco,
    tipo_cuenta,
    numero_cuenta,
    alias="",
    cbu="",
    sucursal_bancaria="",
    sucursal=None,
    activa=True,
    actor=None,
) -> CuentaBancaria:
    _require_actor(actor)
    bank_account.nombre = nombre
    bank_account.banco = banco
    bank_account.tipo_cuenta = tipo_cuenta
    bank_account.numero_cuenta = numero_cuenta
    bank_account.alias = alias
    bank_account.cbu = cbu
    bank_account.sucursal_bancaria = sucursal_bancaria
    bank_account.sucursal = sucursal
    bank_account.activa = activa
    bank_account.actualizado_por = actor
    return _save_instance(bank_account)


def toggle_bank_account(*, bank_account: CuentaBancaria, actor=None) -> CuentaBancaria:
    _require_actor(actor)
    bank_account.activa = not bank_account.activa
    bank_account.save(update_fields=["activa", "actualizado_en"])
    return bank_account


def register_payable(
    *,
    sucursal=None,
    proveedor: Proveedor,
    categoria: CategoriaCuentaPagar,
    concepto: str,
    fecha_emision,
    fecha_vencimiento,
    periodo_referencia=None,
    importe_total: Decimal,
    referencia_comprobante: str = "",
    observaciones: str = "",
    actor=None,
) -> CuentaPorPagar:
    _require_actor(actor)
    if not proveedor.activo:
        raise ValidationError({"proveedor": "El proveedor esta inactivo."})
    if not categoria.activo:
        raise ValidationError({"categoria": "La categoria esta inactiva."})
    payable = CuentaPorPagar(
        sucursal=sucursal,
        proveedor=proveedor,
        categoria=categoria,
        concepto=concepto,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        periodo_referencia=_first_day_of_month(periodo_referencia or fecha_emision),
        importe_total=importe_total,
        saldo_pendiente=importe_total,
        estado=CuentaPorPagar.Estado.PENDIENTE,
        referencia_comprobante=referencia_comprobante,
        observaciones=observaciones,
        creado_por=actor,
    )
    return _save_instance(payable)


def update_payable(
    *,
    payable: CuentaPorPagar,
    sucursal=None,
    proveedor: Proveedor,
    categoria: CategoriaCuentaPagar,
    concepto: str,
    fecha_emision,
    fecha_vencimiento,
    periodo_referencia=None,
    importe_total: Decimal,
    referencia_comprobante: str = "",
    observaciones: str = "",
    actor=None,
) -> CuentaPorPagar:
    _require_actor(actor)
    if payable.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        raise ValidationError({"__all__": "No se puede editar una cuenta por pagar con pagos registrados."})
    if not proveedor.activo:
        raise ValidationError({"proveedor": "El proveedor esta inactivo."})
    if not categoria.activo:
        raise ValidationError({"categoria": "La categoria esta inactiva."})
    payable.sucursal = sucursal
    payable.proveedor = proveedor
    payable.categoria = categoria
    payable.concepto = concepto
    payable.fecha_emision = fecha_emision
    payable.fecha_vencimiento = fecha_vencimiento
    payable.periodo_referencia = _first_day_of_month(periodo_referencia or fecha_emision)
    payable.importe_total = importe_total
    payable.saldo_pendiente = importe_total
    payable.estado = CuentaPorPagar.Estado.PENDIENTE
    payable.referencia_comprobante = referencia_comprobante
    payable.observaciones = observaciones
    payable.actualizado_por = actor
    return _save_instance(payable)


def annul_payable(*, payable: CuentaPorPagar, motivo: str, actor=None) -> CuentaPorPagar:
    _require_actor(actor)
    if payable.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        raise ValidationError({"__all__": "No se puede anular una deuda con pagos registrados."})
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para anular."})
    payable.estado = CuentaPorPagar.Estado.ANULADA
    payable.motivo_anulacion = motivo
    payable.anulada_por = actor
    payable.anulada_en = timezone.now()
    payable.saldo_pendiente = Decimal("0.00")
    payable.actualizado_por = actor
    return _save_instance(payable)


@transaction.atomic
def register_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    medio_pago: str,
    fecha_pago,
    monto: Decimal,
    referencia: str = "",
    fecha_diferida=None,
    observaciones: str = "",
    actor=None,
) -> PagoTesoreria:
    _require_actor(actor)
    locked_payable = CuentaPorPagar.objects.select_for_update().get(pk=payable.pk)
    if bank_account:
        bank_account = CuentaBancaria.objects.get(pk=bank_account.pk)
        if not bank_account.activa:
            raise ValidationError({"cuenta_bancaria": "La cuenta bancaria esta inactiva."})
    if locked_payable.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar esta anulada."})
    if locked_payable.estado == CuentaPorPagar.Estado.PAGADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar ya esta cancelada."})
    if medio_pago == PagoTesoreria.MedioPago.TRANSFERENCIA and fecha_diferida is not None:
        raise ValidationError({"fecha_diferida": "La transferencia no admite fecha diferida."})
    if medio_pago in {PagoTesoreria.MedioPago.CHEQUE, PagoTesoreria.MedioPago.ECHEQ} and not referencia:
        raise ValidationError({"referencia": "La referencia es obligatoria para cheque y ECHEQ."})
    payment = PagoTesoreria(
        cuenta_por_pagar=locked_payable,
        cuenta_bancaria=bank_account,
        medio_pago=medio_pago,
        fecha_pago=fecha_pago,
        fecha_diferida=fecha_diferida,
        monto=monto,
        referencia=referencia,
        observaciones=observaciones,
        creado_por=actor,
    )
    payment.save(skip_domain_guard=True)
    
    if medio_pago == PagoTesoreria.MedioPago.EFECTIVO:
        register_central_cash_movement(
            tipo=MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            monto=monto,
            concepto=f"Pago a {locked_payable.proveedor}: {locked_payable.concepto}",
            fecha=fecha_pago,
            pago_tesoreria=payment,
            actor=actor,
        )
        
    _recalculate_payable_locked(locked_payable)
    return payment


def register_transfer_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    fecha_pago,
    monto: Decimal,
    referencia: str = "",
    observaciones: str = "",
    actor=None,
) -> PagoTesoreria:
    return register_payment(
        payable=payable,
        bank_account=bank_account,
        medio_pago=PagoTesoreria.MedioPago.TRANSFERENCIA,
        fecha_pago=fecha_pago,
        monto=monto,
        referencia=referencia,
        observaciones=observaciones,
        actor=actor,
    )


def register_cheque_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    fecha_pago,
    monto: Decimal,
    referencia: str,
    fecha_diferida=None,
    observaciones: str = "",
    actor=None,
) -> PagoTesoreria:
    return register_payment(
        payable=payable,
        bank_account=bank_account,
        medio_pago=PagoTesoreria.MedioPago.CHEQUE,
        fecha_pago=fecha_pago,
        monto=monto,
        referencia=referencia,
        fecha_diferida=fecha_diferida,
        observaciones=observaciones,
        actor=actor,
    )


def register_echeq_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    fecha_pago,
    monto: Decimal,
    referencia: str,
    fecha_diferida=None,
    observaciones: str = "",
    actor=None,
) -> PagoTesoreria:
    return register_payment(
        payable=payable,
        bank_account=bank_account,
        medio_pago=PagoTesoreria.MedioPago.ECHEQ,
        fecha_pago=fecha_pago,
        monto=monto,
        referencia=referencia,
        fecha_diferida=fecha_diferida,
        observaciones=observaciones,
        actor=actor,
    )


def register_cash_payment(
    *,
    payable: CuentaPorPagar,
    fecha_pago,
    monto: Decimal,
    observaciones: str = "",
    actor=None,
) -> PagoTesoreria:
    return register_payment(
        payable=payable,
        bank_account=None,
        medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
        fecha_pago=fecha_pago,
        monto=monto,
        observaciones=observaciones,
        actor=actor,
    )


@transaction.atomic
def annul_payment(*, payment: PagoTesoreria, motivo: str, actor=None) -> PagoTesoreria:
    _require_actor(actor)
    locked_payment = PagoTesoreria.objects.select_for_update().select_related("cuenta_por_pagar").get(pk=payment.pk)
    payable = CuentaPorPagar.objects.select_for_update().get(pk=locked_payment.cuenta_por_pagar_id)
    if locked_payment.estado == PagoTesoreria.Estado.ANULADO:
        raise ValidationError({"pago": "El pago ya esta anulado."})
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para anular."})
    locked_payment.estado = PagoTesoreria.Estado.ANULADO
    locked_payment.estado_bancario = PagoTesoreria.EstadoBancario.ANULADO
    locked_payment.motivo_anulacion = motivo
    locked_payment.anulado_por = actor
    locked_payment.anulado_en = timezone.now()
    locked_payment.actualizado_por = actor
    locked_payment.actualizado_en = timezone.now()
    locked_payment.save(skip_domain_guard=True)
    _recalculate_payable_locked(payable)
    return locked_payment


# --- Bank Movements & Conciliation (EP-04) ---

def _infer_bank_movement_class(*, tipo: str, origen: str, payment: PagoTesoreria | None = None) -> str:
    if origen == MovimientoBancario.Origen.ACREDITACION_TARJETA:
        return MovimientoBancario.Clase.ACREDITACION
    if origen == MovimientoBancario.Origen.PAGO_TESORERIA and payment is not None:
        return {
            PagoTesoreria.MedioPago.CHEQUE: MovimientoBancario.Clase.CHEQUE,
            PagoTesoreria.MedioPago.ECHEQ: MovimientoBancario.Clase.ECHEQ,
        }.get(payment.medio_pago, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS)
    return (
        MovimientoBancario.Clase.OTRO_INGRESO
        if tipo == MovimientoBancario.Tipo.CREDITO
        else MovimientoBancario.Clase.OTRO_EGRESO
    )


def _existing_accreditation_duplicate_qs(
    *,
    cuenta_bancaria: CuentaBancaria,
    fecha_acreditacion: date,
    canal: str,
    monto_neto: Decimal,
    referencia_externa: str,
    modo_registro: str,
    periodo_desde=None,
    periodo_hasta=None,
):
    queryset = AcreditacionTarjeta.objects.filter(
        movimiento_bancario__cuenta_bancaria=cuenta_bancaria,
        canal__iexact=(canal or "").strip(),
        modo_registro=modo_registro,
    )
    referencia_externa = (referencia_externa or "").strip()
    if referencia_externa:
        return queryset.filter(referencia_externa__iexact=referencia_externa)
    if modo_registro == AcreditacionTarjeta.ModoRegistro.PERIODO:
        return queryset.filter(
            movimiento_bancario__fecha=fecha_acreditacion,
            periodo_desde=periodo_desde,
            periodo_hasta=periodo_hasta,
            movimiento_bancario__monto=monto_neto,
        )
    return queryset.filter(
        movimiento_bancario__fecha=fecha_acreditacion,
        movimiento_bancario__monto=monto_neto,
    )


def _accreditation_scope_query(*, date_from: date, date_to: date) -> Q:
    return Q(
        modo_registro=AcreditacionTarjeta.ModoRegistro.DIARIA,
        movimiento_bancario__fecha__gte=date_from,
        movimiento_bancario__fecha__lte=date_to,
    ) | Q(
        modo_registro=AcreditacionTarjeta.ModoRegistro.PERIODO,
        periodo_desde__isnull=False,
        periodo_hasta__isnull=False,
        periodo_desde__gte=date_from,
        periodo_hasta__lte=date_to,
    )


def create_bank_movement(
    *,
    cuenta_bancaria: CuentaBancaria,
    tipo: str,
    fecha: date,
    monto: Decimal,
    concepto: str,
    clase: str | None = None,
    categoria: CategoriaCuentaPagar = None,
    proveedor: Proveedor = None,
    referencia: str = "",
    observaciones: str = "",
    origen: str = MovimientoBancario.Origen.MANUAL,
    pago_tesoreria: PagoTesoreria = None,
    actor=None,
) -> MovimientoBancario:
    _require_actor(actor)
    movement = MovimientoBancario(
        cuenta_bancaria=cuenta_bancaria,
        tipo=tipo,
        fecha=fecha,
        monto=monto,
        concepto=concepto,
        clase=clase or _infer_bank_movement_class(tipo=tipo, origen=origen, payment=pago_tesoreria),
        categoria=categoria,
        proveedor=proveedor,
        referencia=referencia,
        observaciones=observaciones,
        origen=origen,
        pago_tesoreria=pago_tesoreria,
        creado_por=actor,
    )
    return _save_instance(movement)


def update_bank_movement(
    *,
    movement: MovimientoBancario,
    fecha: date,
    monto: Decimal,
    concepto: str,
    clase: str | None = None,
    categoria: CategoriaCuentaPagar = None,
    proveedor: Proveedor = None,
    referencia: str = "",
    observaciones: str = "",
    actor=None,
) -> MovimientoBancario:
    _require_actor(actor)
    movement.fecha = fecha
    movement.monto = monto
    movement.concepto = concepto
    if clase:
        movement.clase = clase
    movement.categoria = categoria
    movement.proveedor = proveedor
    movement.referencia = referencia
    movement.observaciones = observaciones
    movement.actualizado_por = actor
    return _save_instance(movement)


def create_pos_batch(
    *,
    fecha_lote: date,
    total_lote: Decimal,
    cuenta_bancaria: CuentaBancaria = None,
    terminal: str = "",
    operador: str = "",
    observaciones: str = "",
    actor=None,
) -> LotePOS:
    _require_actor(actor)
    batch = LotePOS(
        fecha_lote=fecha_lote,
        total_lote=total_lote,
        cuenta_bancaria=cuenta_bancaria,
        terminal=terminal,
        operador=operador,
        observaciones=observaciones,
        creado_por=actor,
    )
    return _save_instance(batch)


def update_pos_batch(
    *,
    batch: LotePOS,
    fecha_lote: date,
    total_lote: Decimal,
    cuenta_bancaria: CuentaBancaria = None,
    terminal: str = "",
    operador: str = "",
    observaciones: str = "",
    actor=None,
) -> LotePOS:
    _require_actor(actor)
    batch.fecha_lote = fecha_lote
    batch.total_lote = total_lote
    batch.cuenta_bancaria = cuenta_bancaria
    batch.terminal = terminal
    batch.operador = operador
    batch.observaciones = observaciones
    batch.actualizado_por = actor
    return _save_instance(batch)


@transaction.atomic
def register_card_accreditation(
    *,
    cuenta_bancaria: CuentaBancaria,
    fecha_acreditacion: date,
    monto_neto: Decimal,
    canal: str,
    referencia_externa: str = "",
    lote_pos: LotePOS = None,
    modo_registro: str = AcreditacionTarjeta.ModoRegistro.DIARIA,
    periodo_desde=None,
    periodo_hasta=None,
    descuentos: list[dict] = None,  # list of {'tipo': '...', 'monto': 123, 'descripcion': '...'}
    actor=None,
) -> AcreditacionTarjeta:
    """
    US-4.2 & US-4.4: Registers a bank credit movement and links it to an accreditation record
    with multiple potential discounts/retentions.
    """
    _require_actor(actor)

    duplicate_qs = _existing_accreditation_duplicate_qs(
        cuenta_bancaria=cuenta_bancaria,
        fecha_acreditacion=fecha_acreditacion,
        canal=canal,
        monto_neto=monto_neto,
        referencia_externa=referencia_externa,
        modo_registro=modo_registro,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
    )
    if duplicate_qs.exists():
        raise ValidationError(
            {
                "referencia_externa": (
                    "Ya existe una acreditacion equivalente para esta cuenta, canal y referencia o periodo."
                )
            }
        )

    # 1. Create the bank movement (credit)
    movement = create_bank_movement(
        cuenta_bancaria=cuenta_bancaria,
        tipo=MovimientoBancario.Tipo.CREDITO,
        fecha=fecha_acreditacion,
        monto=monto_neto,
        concepto=f"Acreditacion Tarjeta {canal}",
        clase=MovimientoBancario.Clase.ACREDITACION,
        referencia=referencia_externa,
        origen=MovimientoBancario.Origen.ACREDITACION_TARJETA,
        actor=actor,
    )

    # 2. Create the accreditation record
    accreditation = AcreditacionTarjeta(
        movimiento_bancario=movement,
        modo_registro=modo_registro,
        canal=canal,
        lote_pos=lote_pos,
        periodo_desde=periodo_desde,
        periodo_hasta=periodo_hasta,
        referencia_externa=referencia_externa,
        creado_por=actor,
    )
    accreditation = _save_instance(accreditation)

    # 3. Register discounts if any
    if descuentos:
        for d in descuentos:
            DescuentoAcreditacion.objects.create(
                acreditacion=accreditation,
                tipo=d["tipo"],
                monto=d["monto"],
                descripcion=d["descripcion"],
                creado_por=actor,
            )

    return accreditation


@transaction.atomic
def link_payment_to_bank_movement(
    *,
    payment: PagoTesoreria,
    bank_movement: MovimientoBancario,
    actor=None,
) -> MovimientoBancario:
    """
    US-4.5: Links a treasury payment to a bank movement.
    Ensures they match in amount and account.
    """
    _require_actor(actor)

    if payment.monto != bank_movement.monto:
        raise ValidationError("El monto del pago y el movimiento bancario no coinciden.")
    if payment.cuenta_bancaria_id != bank_movement.cuenta_bancaria_id:
        raise ValidationError("La cuenta bancaria del pago y el movimiento no coinciden.")

    bank_movement.pago_tesoreria = payment
    bank_movement.origen = MovimientoBancario.Origen.PAGO_TESORERIA
    bank_movement.clase = _infer_bank_movement_class(
        tipo=bank_movement.tipo,
        origen=MovimientoBancario.Origen.PAGO_TESORERIA,
        payment=payment,
    )
    bank_movement.proveedor = payment.cuenta_por_pagar.proveedor
    bank_movement.categoria = payment.cuenta_por_pagar.categoria
    bank_movement.actualizado_por = actor
    bank_movement.save()

    # Update payment status if it was REGISTERED to something indicating bank reflection
    # (Actually PagoTesoreria has estado_bancario)
    payment.estado_bancario = PagoTesoreria.EstadoBancario.IMPACTADO
    payment.actualizado_por = actor
    payment.save(skip_domain_guard=True)

    return bank_movement


def build_bank_reconciliation_snapshot(
    *,
    cuenta_bancaria: CuentaBancaria,
    date_from: date,
    date_to: date,
) -> dict:
    """
    US-4.6: Simple reconciliation logic.
    """
    from cashops.models import MovimientoCaja

    # 1. Total sold by Card (from CashOps)
    # We map this to the bank account indirectly if possible, or just global for the period
    # Note: CashOps records don't have account_id directly, but typically one branch uses one account.
    # For now, we take all card sales in the period.
    total_sales = MovimientoCaja.objects.filter(
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        creado_en__date__gte=date_from,
        creado_en__date__lte=date_to,
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    # 2. Total recorded in POS Batches
    batches = LotePOS.objects.filter(
        cuenta_bancaria=cuenta_bancaria,
        fecha_lote__gte=date_from,
        fecha_lote__lte=date_to,
    )
    total_batches = batches.aggregate(total=Sum("total_lote"))["total"] or Decimal("0.00")

    # 3. Total accredited in Bank
    accreditations = AcreditacionTarjeta.objects.filter(
        movimiento_bancario__cuenta_bancaria=cuenta_bancaria,
        movimiento_bancario__fecha__gte=date_from,
        movimiento_bancario__fecha__lte=date_to,
    )
    total_accredited_net = accreditations.aggregate(total=Sum("movimiento_bancario__monto"))["total"] or Decimal("0.00")

    total_discounts = DescuentoAcreditacion.objects.filter(
        acreditacion__in=accreditations
    ).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    total_accredited_bruto = total_accredited_net + total_discounts

    return {
        "cuenta_bancaria": cuenta_bancaria,
        "date_from": date_from,
        "date_to": date_to,
        "total_sales": total_sales,
        "total_batches": total_batches,
        "total_accredited_bruto": total_accredited_bruto,
        "total_accredited_net": total_accredited_net,
        "total_discounts": total_discounts,
        "diff_sales_batches": total_sales - total_batches,
        "diff_batches_accretion": total_batches - total_accredited_bruto,
    }


def _central_cash_balance_until(*, reference_date: date, sucursal=None) -> Decimal:
    movements = MovimientoCajaCentral.objects.filter(fecha__lte=reference_date)
    if sucursal is not None:
        movements = movements.filter(caja_central__sucursal=sucursal)
    sums = movements.aggregate(
        ingresos=Sum(
            "monto",
            filter=Q(
                tipo__in=[
                    MovimientoCajaCentral.Tipo.INGRESO_CAJA,
                    MovimientoCajaCentral.Tipo.APORTE,
                    MovimientoCajaCentral.Tipo.RETIRO_BANCO,
                    MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO,
                ]
            ),
        ),
        egresos=Sum(
            "monto",
            filter=Q(
                tipo__in=[
                    MovimientoCajaCentral.Tipo.EGRESO_PAGO,
                    MovimientoCajaCentral.Tipo.DEPOSITO_BANCO,
                    MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO,
                ]
            ),
        ),
    )
    return (sums["ingresos"] or Decimal("0.00")) - (sums["egresos"] or Decimal("0.00"))


def build_economic_period_snapshot(*, date_from: date, date_to: date, sucursal=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    from cashops.models import MovimientoCaja, RubroOperativo

    sale_query = Q(tipo__in=[
        MovimientoCaja.Tipo.VENTA_TARJETA,
        MovimientoCaja.Tipo.VENTA_TRANSFERENCIA,
        MovimientoCaja.Tipo.VENTA_PEDIDOSYA,
        MovimientoCaja.Tipo.VENTA_QR,
    ]) | Q(
        tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
        rubro_operativo__isnull=False,
    )
    sales = MovimientoCaja.objects.filter(
        caja__turno__fecha_operativa__gte=date_from,
        caja__turno__fecha_operativa__lte=date_to,
    ).filter(sale_query)
    expenses = MovimientoCaja.objects.filter(
        caja__turno__fecha_operativa__gte=date_from,
        caja__turno__fecha_operativa__lte=date_to,
        tipo=MovimientoCaja.Tipo.GASTO,
        rubro_operativo__isnull=False,
    )
    if sucursal is not None:
        sales = sales.filter(caja__sucursal=sucursal)
        expenses = expenses.filter(caja__sucursal=sucursal)

    sales_total = sales.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    cash_expense_total = expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    sales_by_rubro = {
        row["rubro_operativo"]: row["total"] or Decimal("0.00")
        for row in sales.values("rubro_operativo").annotate(total=Sum("monto"))
    }
    cash_expense_by_rubro = {
        row["rubro_operativo"]: row["total"] or Decimal("0.00")
        for row in expenses.values("rubro_operativo").annotate(total=Sum("monto"))
    }

    period_from = _first_day_of_month(date_from)
    period_to = _first_day_of_month(date_to)
    period_payables = CuentaPorPagar.objects.exclude(
        estado=CuentaPorPagar.Estado.ANULADA
    ).filter(
        periodo_referencia__gte=period_from,
        periodo_referencia__lte=period_to,
    )
    if sucursal is not None:
        period_payables = period_payables.filter(sucursal=sucursal)

    debt_period_total = period_payables.aggregate(total=Sum("importe_total"))["total"] or Decimal("0.00")
    debt_pending_total = period_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00")
    debt_rows = list(
        period_payables.values(
            "categoria__rubro_operativo",
            "categoria__rubro_operativo__nombre",
        ).annotate(
            total_deuda=Sum("importe_total"),
            pendiente=Sum("saldo_pendiente"),
            cantidad=Count("id"),
        )
    )
    debt_by_rubro = {}
    unmapped_payables_total = Decimal("0.00")
    unmapped_payables_pending = Decimal("0.00")
    unmapped_payables_count = 0
    for row in debt_rows:
        rubro_id = row["categoria__rubro_operativo"]
        if rubro_id is None:
            unmapped_payables_total += row["total_deuda"] or Decimal("0.00")
            unmapped_payables_pending += row["pendiente"] or Decimal("0.00")
            unmapped_payables_count += row["cantidad"] or 0
            continue
        debt_by_rubro[rubro_id] = {
            "debt_total": row["total_deuda"] or Decimal("0.00"),
            "debt_pending": row["pendiente"] or Decimal("0.00"),
            "count": row["cantidad"] or 0,
            "name": row["categoria__rubro_operativo__nombre"] or "Rubro",
        }

    rubro_ids = set(sales_by_rubro.keys()) | set(cash_expense_by_rubro.keys()) | set(debt_by_rubro.keys())
    rubros = {
        rubro.pk: rubro
        for rubro in RubroOperativo.objects.filter(pk__in=rubro_ids)
    }
    items = []
    for rubro_id in sorted(rubro_ids, key=lambda current: (rubros.get(current).nombre.lower() if rubros.get(current) else "")):
        rubro = rubros.get(rubro_id)
        debt_item = debt_by_rubro.get(rubro_id, {})
        expense_cash = cash_expense_by_rubro.get(rubro_id, Decimal("0.00"))
        expense_debt = debt_item.get("debt_total", Decimal("0.00"))
        total_expense = expense_cash + expense_debt
        expense_ratio = (
            ((total_expense * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
            if sales_total > 0
            else Decimal("0.00")
        )
        items.append(
            {
                "rubro": rubro,
                "rubro_nombre": rubro.nombre if rubro is not None else "Sin rubro",
                "sales_total": sales_by_rubro.get(rubro_id, Decimal("0.00")),
                "cash_expense_total": expense_cash,
                "debt_total": expense_debt,
                "debt_pending": debt_item.get("debt_pending", Decimal("0.00")),
                "payables_count": debt_item.get("count", 0),
                "total_expense": total_expense,
                "expense_ratio_over_sales": expense_ratio,
            }
        )
    items.sort(key=lambda item: (-item["total_expense"], item["rubro_nombre"].lower()))

    economic_result = sales_total - cash_expense_total - debt_period_total
    margin_pct = (
        ((economic_result * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
        if sales_total > 0
        else Decimal("0.00")
    )
    return {
        "date_from": date_from,
        "date_to": date_to,
        "period_from": period_from,
        "period_to": period_to,
        "sucursal": sucursal,
        "sales_total": sales_total,
        "cash_expense_total": cash_expense_total,
        "debt_period_total": debt_period_total,
        "debt_pending_total": debt_pending_total,
        "economic_result": economic_result,
        "margin_pct": margin_pct,
        "items": items,
        "unmapped_payables_total": unmapped_payables_total,
        "unmapped_payables_pending": unmapped_payables_pending,
        "unmapped_payables_count": unmapped_payables_count,
    }


def build_financial_period_snapshot(*, date_from: date, date_to: date, sucursal=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    from cashops.models import MovimientoCaja

    cash_movements = MovimientoCaja.objects.filter(
        caja__turno__fecha_operativa__gte=date_from,
        caja__turno__fecha_operativa__lte=date_to,
        impacta_saldo_caja=True,
    ).exclude(tipo=MovimientoCaja.Tipo.APERTURA)
    if sucursal is not None:
        cash_movements = cash_movements.filter(caja__sucursal=sucursal)

    cash_totals = cash_movements.aggregate(
        ingresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.INGRESO)),
        egresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.EGRESO)),
    )
    cash_income = cash_totals["ingresos"] or Decimal("0.00")
    cash_expense = cash_totals["egresos"] or Decimal("0.00")

    bank_movements = MovimientoBancario.objects.filter(fecha__gte=date_from, fecha__lte=date_to)
    if sucursal is not None:
        bank_movements = bank_movements.filter(cuenta_bancaria__sucursal=sucursal)

    bank_totals = bank_movements.aggregate(
        creditos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.CREDITO)),
        debitos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.DEBITO)),
    )
    bank_credits = bank_totals["creditos"] or Decimal("0.00")
    bank_debits = bank_totals["debitos"] or Decimal("0.00")

    bank_accounts = CuentaBancaria.objects.filter(activa=True)
    if sucursal is not None:
        bank_accounts = bank_accounts.filter(sucursal=sucursal)

    bank_balances = []
    total_bank_balance = Decimal("0.00")
    for account in bank_accounts.order_by("banco", "nombre"):
        account_movements = MovimientoBancario.objects.filter(cuenta_bancaria=account, fecha__lte=date_to)
        credits = (
            account_movements.filter(tipo=MovimientoBancario.Tipo.CREDITO).aggregate(total=Sum("monto"))["total"]
            or Decimal("0.00")
        )
        debits = (
            account_movements.filter(tipo=MovimientoBancario.Tipo.DEBITO).aggregate(total=Sum("monto"))["total"]
            or Decimal("0.00")
        )
        balance = credits - debits
        total_bank_balance += balance
        bank_balances.append({"account": account, "balance": balance})

    pending_payables = CuentaPorPagar.objects.filter(
        estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL]
    )
    if sucursal is not None:
        pending_payables = pending_payables.filter(sucursal=sucursal)

    reference_date = date_to
    overdue_payables = pending_payables.filter(fecha_vencimiento__lt=reference_date)
    due_today_payables = pending_payables.filter(fecha_vencimiento=reference_date)
    upcoming_window = reference_date + timedelta(days=7)
    upcoming_payables = pending_payables.filter(
        fecha_vencimiento__gt=reference_date,
        fecha_vencimiento__lte=upcoming_window,
    )

    digital_sales = MovimientoCaja.objects.filter(
        caja__turno__fecha_operativa__gte=date_from,
        caja__turno__fecha_operativa__lte=date_to,
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
    )
    if sucursal is not None:
        digital_sales = digital_sales.filter(caja__sucursal=sucursal)
    digital_sales_total = digital_sales.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    accreditations = AcreditacionTarjeta.objects.filter(_accreditation_scope_query(date_from=date_from, date_to=date_to))
    if sucursal is not None:
        accreditations = accreditations.filter(movimiento_bancario__cuenta_bancaria__sucursal=sucursal)

    accredited_net = accreditations.aggregate(total=Sum("movimiento_bancario__monto"))["total"] or Decimal("0.00")
    accreditation_discounts = (
        DescuentoAcreditacion.objects.filter(acreditacion__in=accreditations).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    accredited_gross = accredited_net + accreditation_discounts
    pending_accreditation_total = digital_sales_total - accredited_gross

    recent_movements = bank_movements.select_related("cuenta_bancaria", "categoria", "proveedor").order_by(
        "-fecha", "-id"
    )[:5]
    recent_batches = LotePOS.objects.filter(fecha_lote__gte=date_from, fecha_lote__lte=date_to)
    if sucursal is not None:
        recent_batches = recent_batches.filter(cuenta_bancaria__sucursal=sucursal)
    recent_batches = recent_batches.select_related("cuenta_bancaria").order_by("-fecha_lote", "-id")[:5]

    recent_payments = PagoTesoreria.objects.filter(
        estado=PagoTesoreria.Estado.REGISTRADO,
        fecha_pago__gte=date_from,
        fecha_pago__lte=date_to,
    )
    if sucursal is not None:
        recent_payments = recent_payments.filter(cuenta_por_pagar__sucursal=sucursal)
    recent_payments = recent_payments.select_related("cuenta_por_pagar__proveedor", "cuenta_bancaria").order_by(
        "-fecha_pago", "-id"
    )[:10]

    central_cash_total = _central_cash_balance_until(reference_date=reference_date, sucursal=sucursal)

    return {
        "date_from": date_from,
        "date_to": date_to,
        "reference_date": reference_date,
        "sucursal": sucursal,
        "cash_income": cash_income,
        "cash_expense": cash_expense,
        "cash_net": cash_income - cash_expense,
        "bank_credits": bank_credits,
        "bank_debits": bank_debits,
        "bank_net": bank_credits - bank_debits,
        "bank_balances": bank_balances,
        "total_bank_balance": total_bank_balance,
        "central_cash_total": central_cash_total,
        "total_consolidated": central_cash_total + total_bank_balance,
        "digital_sales_total": digital_sales_total,
        "accredited_net": accredited_net,
        "accredited_gross": accredited_gross,
        "accreditation_discounts": accreditation_discounts,
        "pending_accreditation_total": pending_accreditation_total,
        "pending_count": pending_payables.count(),
        "pending_total": pending_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "overdue_count": overdue_payables.count(),
        "overdue_total": overdue_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "due_today_count": due_today_payables.count(),
        "due_today_total": due_today_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "upcoming_count": upcoming_payables.count(),
        "upcoming_total": upcoming_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "overdue_payables": overdue_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")[:10],
        "due_today_payables": due_today_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")[:10],
        "upcoming_payables": upcoming_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")[:10],
        "recent_payments": recent_payments,
        "recent_movements": recent_movements,
        "recent_batches": recent_batches,
    }


def build_treasury_dashboard_snapshot(*, reference_date=None, sucursal_id=None) -> dict:
    reference_date = reference_date or timezone.localdate()
    
    pending_payables = CuentaPorPagar.objects.filter(
        estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL]
    )
    paid_payments = PagoTesoreria.objects.filter(
        estado=PagoTesoreria.Estado.REGISTRADO,
        fecha_pago__year=reference_date.year,
        fecha_pago__month=reference_date.month,
    )
    bank_accounts = CuentaBancaria.objects.filter(activa=True)
    cajas_centrales = CajaCentral.objects.filter(activo=True)
    
    if sucursal_id:
        pending_payables = pending_payables.filter(sucursal_id=sucursal_id)
        paid_payments = paid_payments.filter(cuenta_por_pagar__sucursal_id=sucursal_id)
        bank_accounts = bank_accounts.filter(sucursal_id=sucursal_id)
        cajas_centrales = cajas_centrales.filter(sucursal_id=sucursal_id)

    overdue_payables = pending_payables.filter(fecha_vencimiento__lt=reference_date)
    upcoming_window = reference_date + timedelta(days=7)
    upcoming_payables = pending_payables.filter(fecha_vencimiento__gte=reference_date, fecha_vencimiento__lte=upcoming_window)
    
    paid_period_total = paid_payments.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    
    # Bank balances
    bank_balances = []
    for account in bank_accounts:
        credits = MovimientoBancario.objects.filter(cuenta_bancaria=account, tipo=MovimientoBancario.Tipo.CREDITO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
        debits = MovimientoBancario.objects.filter(cuenta_bancaria=account, tipo=MovimientoBancario.Tipo.DEBITO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
        bank_balances.append({
            "account": account,
            "balance": credits - debits
        })
    
    recent_batches = LotePOS.objects.all().select_related("cuenta_bancaria").order_by("-fecha_lote", "-id")
    recent_movements = MovimientoBancario.objects.all().select_related("cuenta_bancaria").order_by("-fecha", "-id")
    recent_payments = PagoTesoreria.objects.filter(estado=PagoTesoreria.Estado.REGISTRADO).select_related("cuenta_por_pagar__proveedor", "cuenta_bancaria")

    if sucursal_id:
        recent_batches = recent_batches.filter(cuenta_bancaria__sucursal_id=sucursal_id)
        recent_movements = recent_movements.filter(cuenta_bancaria__sucursal_id=sucursal_id)
        recent_payments = recent_payments.filter(cuenta_por_pagar__sucursal_id=sucursal_id)

    # Central Cash balance (consolidated for the scope)
    central_cash_balance = Decimal("0.00")
    for caja in cajas_centrales:
        central_cash_balance += caja.saldo_actual

    return {
        "reference_date": reference_date,
        "pending_total": pending_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "pending_count": pending_payables.count(),
        "overdue_total": overdue_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "overdue_count": overdue_payables.count(),
        "paid_period_total": paid_period_total,
        "upcoming_payables": upcoming_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")[:10],
        "overdue_payables": overdue_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")[:10],
        "recent_payments": recent_payments.order_by("-fecha_pago", "-id")[:10],
        "bank_balances": bank_balances,
        "recent_batches": recent_batches[:5],
        "recent_movements": recent_movements[:5],
        "central_cash_balance": central_cash_balance,
    }


def build_supplier_history_snapshot(*, supplier: Proveedor, date_from=None, date_to=None) -> dict:
    payables = (
        CuentaPorPagar.objects.filter(proveedor=supplier)
        .select_related("categoria")
        .prefetch_related("pagos__cuenta_bancaria")
        .order_by("-fecha_vencimiento", "-id")
    )
    payments = (
        PagoTesoreria.objects.filter(cuenta_por_pagar__proveedor=supplier)
        .select_related("cuenta_por_pagar", "cuenta_bancaria")
        .order_by("-fecha_pago", "-id")
    )
    if date_from:
        payables = payables.filter(fecha_emision__gte=date_from)
        payments = payments.filter(fecha_pago__gte=date_from)
    if date_to:
        payables = payables.filter(fecha_emision__lte=date_to)
        payments = payments.filter(fecha_pago__lte=date_to)
    return {
        "supplier": supplier,
        "date_from": date_from,
        "date_to": date_to,
        "payables": payables,
        "payments": payments,
        "historical_total": payables.aggregate(total=Sum("importe_total"))["total"] or Decimal("0.00"),
        "historical_pending": payables.exclude(estado=CuentaPorPagar.Estado.ANULADA).aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "historical_paid": payments.filter(estado=PagoTesoreria.Estado.REGISTRADO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00"),
    }


# --- Flujo de Disponibilidades (EP-05) ---

def get_or_create_default_caja_central() -> CajaCentral:
    caja, created = CajaCentral.objects.get_or_create(nombre="Efectivo Central")
    return caja


def register_central_cash_movement(
    *,
    tipo: MovimientoCajaCentral.Tipo,
    monto: Decimal,
    concepto: str,
    fecha=None,
    pago_tesoreria: PagoTesoreria = None,
    movimiento_bancario: MovimientoBancario = None,
    observaciones: str = "",
    actor=None,
) -> MovimientoCajaCentral:
    _require_actor(actor)
    caja = get_or_create_default_caja_central()
    movement = MovimientoCajaCentral(
        caja_central=caja,
        fecha=fecha or timezone.localdate(),
        tipo=tipo,
        monto=monto,
        concepto=concepto,
        pago_tesoreria=pago_tesoreria,
        movimiento_bancario=movimiento_bancario,
        observaciones=observaciones,
        creado_por=actor,
    )
    return _save_instance(movement)


def build_disponibilidades_snapshot(year: int, month: int, sucursal=None) -> dict:
    """
    US-5.2: Calculates consolidated or branch-specific liquidity in a period.
    """
    first_day = date(year, month, 1)
    # Get last day of month
    if month == 12:
        next_month = timezone.datetime(year + 1, 1, 1).date()
    else:
        next_month = timezone.datetime(year, month + 1, 1).date()
    last_day = next_month - timedelta(days=1)

    # 1. Opening Balance (from previous closing)
    closing_filter = Q(mes__lt=first_day)
    if sucursal:
        closing_filter &= Q(sucursal=sucursal)
    else:
        closing_filter &= Q(sucursal__isnull=True) if not CierreMensualTesoreria.objects.filter(mes__lt=first_day, sucursal__isnull=False).exists() else Q()
        # If any branch cierre exists, we must be careful with global sum. 
        # For now, if no sucursal, we sum all closings of that month.
        pass

    closings_prev = CierreMensualTesoreria.objects.filter(closing_filter).order_by("-mes")
    
    # We take the most recent closing for the scope
    # Note: If global, we might have multiple branch closings. 
    # For simplicity, we calculate the sum.
    saldo_inicial_efectivo = Decimal("0.00")
    saldos_iniciales_bancarios = {} # Dict {str(id): combined_saldo}

    if sucursal:
        cp = closings_prev.first()
        if cp:
            saldo_inicial_efectivo = cp.saldo_final_efectivo
            saldos_iniciales_bancarios = cp.saldos_bancarios_json
    else:
        # Global: Sum of all branch closings for the last available month
        last_closing_month = closings_prev.values_list('mes', flat=True).first()
        if last_closing_month:
            relevant_closings = CierreMensualTesoreria.objects.filter(mes=last_closing_month)
            for c in relevant_closings:
                saldo_inicial_efectivo += c.saldo_final_efectivo
                for acc_id, balance in c.saldos_bancarios_json.items():
                    saldos_iniciales_bancarios[acc_id] = str(Decimal(saldos_iniciales_bancarios.get(acc_id, "0.00")) + Decimal(balance))

    # 2. Cash Flow in Period
    movements_cash = MovimientoCajaCentral.objects.filter(fecha__range=(first_day, last_day))
    if sucursal:
        movements_cash = movements_cash.filter(caja_central__sucursal=sucursal)
    
    cash_in = movements_cash.filter(tipo__in=[
        MovimientoCajaCentral.Tipo.INGRESO_CAJA,
        MovimientoCajaCentral.Tipo.APORTE,
        MovimientoCajaCentral.Tipo.RETIRO_BANCO,
        MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO
    ]).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    
    cash_out = movements_cash.filter(tipo__in=[
        MovimientoCajaCentral.Tipo.EGRESO_PAGO,
        MovimientoCajaCentral.Tipo.DEPOSITO_BANCO,
        MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO
    ]).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    
    saldo_final_efectivo = saldo_inicial_efectivo + cash_in - cash_out

    # 3. Bank Flow in Period
    bank_accounts = CuentaBancaria.objects.filter(activa=True)
    if sucursal:
        bank_accounts = bank_accounts.filter(sucursal=sucursal)
        
    accounts_info = []
    total_bancos_final = Decimal("0.00")

    for acc in bank_accounts:
        initial = Decimal(saldos_iniciales_bancarios.get(str(acc.id), "0.00"))
        
        m_period = MovimientoBancario.objects.filter(cuenta_bancaria=acc, fecha__range=(first_day, last_day))
        credits = m_period.filter(tipo=MovimientoBancario.Tipo.CREDITO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
        debits = m_period.filter(tipo=MovimientoBancario.Tipo.DEBITO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
        
        final = initial + credits - debits
        accounts_info.append({
            "account": acc,
            "initial": initial,
            "credits": credits,
            "debits": debits,
            "final": final,
        })
        total_bancos_final += final

    is_closed = CierreMensualTesoreria.objects.filter(mes=first_day, cerrado=True)
    if sucursal:
        is_closed = is_closed.filter(sucursal=sucursal)
    
    return {
        "year": year,
        "month": month,
        "sucursal": sucursal,
        "first_day": first_day,
        "last_day": last_day,
        "saldo_inicial_efectivo": saldo_inicial_efectivo,
        "cash_in": cash_in,
        "cash_out": cash_out,
        "saldo_final_efectivo": saldo_final_efectivo,
        "accounts_info": accounts_info,
        "total_bancos_final": total_bancos_final,
        "total_consolidado": saldo_final_efectivo + total_bancos_final,
        "is_closed": is_closed.exists()
    }


@transaction.atomic
def close_treasury_month(year: int, month: int, actor=None) -> CierreMensualTesoreria:
    _require_actor(actor)
    snapshot = build_disponibilidades_snapshot(year, month)
    
    first_day = snapshot["first_day"]
    if CierreMensualTesoreria.objects.filter(mes=first_day, cerrado=True).exists():
        raise ValidationError("Este mes ya se encuentra cerrado.")
        
    closing, created = CierreMensualTesoreria.objects.get_or_create(mes=first_day)
    closing.saldo_inicial_efectivo = snapshot["saldo_inicial_efectivo"]
    closing.saldo_final_efectivo = snapshot["saldo_final_efectivo"]
    
    # Store bank balances
    bancarios = {str(item["account"].id): str(item["final"]) for item in snapshot["accounts_info"]}
    closing.saldos_bancarios_json = bancarios
    
    closing.cerrado = True
    closing.cerrado_por = actor
    closing.cerrado_en = timezone.now()
    closing.save()
    
    return closing


def register_arqueo(
    *,
    caja_central: CajaCentral,
    saldo_contado: Decimal,
    observaciones: str = "",
    actor=None,
) -> ArqueoDisponibilidades:
    _require_actor(actor)
    saldo_sistema = caja_central.saldo_actual
    
    arqueo = ArqueoDisponibilidades(
        caja_central=caja_central,
        saldo_sistema_efectivo=saldo_sistema,
        saldo_contado_efectivo=saldo_contado,
        observaciones=observaciones,
        creado_por=actor,
    )
    arqueo.save()
    
    # Optionally create an adjustment movement if difference != 0?
    # Usually we want the user to do it explicitly to audit the reason.
    
    return arqueo
