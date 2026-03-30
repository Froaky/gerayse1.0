from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CuentaBancaria, CuentaPorPagar, PagoTesoreria, Proveedor
from .permissions import ensure_treasury_admin


def _require_actor(actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Se requiere un usuario autenticado para operar.")
    ensure_treasury_admin(actor)


def _recalculate_payable_locked(payable: CuentaPorPagar) -> CuentaPorPagar:
    total_pagado = payable.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).aggregate(
        total=Sum("monto")
    )["total"] or Decimal("0.00")
    payable.saldo_pendiente = payable.importe_total - total_pagado
    if payable.saldo_pendiente == Decimal("0.00"):
        payable.estado = CuentaPorPagar.Estado.PAGADA
    elif payable.saldo_pendiente < payable.importe_total:
        payable.estado = CuentaPorPagar.Estado.PARCIAL
    else:
        payable.estado = CuentaPorPagar.Estado.PENDIENTE
    payable.save(update_fields=["saldo_pendiente", "estado", "actualizado_en"])
    return payable


@transaction.atomic
def create_supplier(
    *,
    razon_social: str,
    identificador_fiscal: str = "",
    contacto: str = "",
    telefono: str = "",
    email: str = "",
    alias_bancario: str = "",
    cbu: str = "",
    observaciones: str = "",
    actor=None,
) -> Proveedor:
    _require_actor(actor)
    supplier = Proveedor(
        razon_social=razon_social,
        identificador_fiscal=identificador_fiscal,
        contacto=contacto,
        telefono=telefono,
        email=email,
        alias_bancario=alias_bancario,
        cbu=cbu,
        observaciones=observaciones,
        creado_por=actor,
    )
    supplier.full_clean()
    supplier.save()
    return supplier


@transaction.atomic
def toggle_supplier(*, supplier: Proveedor, actor=None) -> Proveedor:
    _require_actor(actor)
    supplier = Proveedor.objects.select_for_update().get(pk=supplier.pk)
    supplier.activo = not supplier.activo
    supplier.save(update_fields=["activo", "actualizado_en"])
    return supplier


@transaction.atomic
def create_bank_account(
    *,
    nombre: str,
    banco: str,
    tipo_cuenta: str,
    numero_cuenta: str,
    alias: str = "",
    cbu: str = "",
    sucursal_bancaria: str = "",
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
        creado_por=actor,
    )
    bank_account.full_clean()
    bank_account.save()
    return bank_account


@transaction.atomic
def register_payable(
    *,
    proveedor: Proveedor,
    concepto: str,
    fecha_emision,
    fecha_vencimiento,
    importe_total: Decimal,
    referencia_comprobante: str = "",
    observaciones: str = "",
    actor=None,
) -> CuentaPorPagar:
    _require_actor(actor)
    supplier = Proveedor.objects.select_for_update().get(pk=proveedor.pk)
    if not supplier.activo:
        raise ValidationError({"proveedor": "No podes crear obligaciones para proveedores inactivos."})
    payable = CuentaPorPagar(
        proveedor=supplier,
        concepto=concepto,
        referencia_comprobante=referencia_comprobante,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        importe_total=importe_total,
        saldo_pendiente=importe_total,
        estado=CuentaPorPagar.Estado.PENDIENTE,
        observaciones=observaciones,
        creado_por=actor,
    )
    payable.full_clean()
    payable.save()
    return payable


@transaction.atomic
def annul_payable(*, payable: CuentaPorPagar, motivo: str, actor=None) -> CuentaPorPagar:
    _require_actor(actor)
    payable = CuentaPorPagar.objects.select_for_update().get(pk=payable.pk)
    if payable.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar ya esta anulada."})
    if payable.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        raise ValidationError(
            {"cuenta_por_pagar": "No podes anular una cuenta por pagar con pagos registrados."}
        )
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de anulacion es obligatorio."})
    payable.estado = CuentaPorPagar.Estado.ANULADA
    payable.saldo_pendiente = Decimal("0.00")
    payable.motivo_anulacion = motivo
    payable.anulada_por = actor
    payable.anulada_en = timezone.now()
    payable.full_clean()
    payable.save(
        update_fields=[
            "estado",
            "saldo_pendiente",
            "motivo_anulacion",
            "anulada_por",
            "anulada_en",
            "actualizado_en",
        ]
    )
    return payable


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
    payable = CuentaPorPagar.objects.select_for_update().select_related("proveedor").get(pk=payable.pk)
    bank_account = CuentaBancaria.objects.get(pk=bank_account.pk)

    if payable.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar esta anulada."})
    if payable.estado == CuentaPorPagar.Estado.PAGADA or payable.saldo_pendiente == Decimal("0.00"):
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar ya esta cancelada."})
    if not bank_account.activa:
        raise ValidationError({"cuenta_bancaria": "La cuenta bancaria esta inactiva."})
    if monto <= 0:
        raise ValidationError({"monto": "El monto debe ser mayor que cero."})
    if monto > payable.saldo_pendiente:
        raise ValidationError({"monto": "El pago no puede superar el saldo pendiente."})

    payment = PagoTesoreria(
        cuenta_por_pagar=payable,
        cuenta_bancaria=bank_account,
        medio_pago=medio_pago,
        fecha_pago=fecha_pago,
        fecha_diferida=fecha_diferida,
        monto=monto,
        referencia=referencia,
        observaciones=observaciones,
        creado_por=actor,
    )
    payment.full_clean()
    payment.save(skip_domain_guard=True)
    _recalculate_payable_locked(payable)
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


@transaction.atomic
def annul_payment(*, payment: PagoTesoreria, motivo: str, actor=None) -> PagoTesoreria:
    _require_actor(actor)
    base_payment = PagoTesoreria.objects.select_related("cuenta_por_pagar").get(pk=payment.pk)
    payable = CuentaPorPagar.objects.select_for_update().get(pk=base_payment.cuenta_por_pagar_id)
    payment = PagoTesoreria.objects.select_for_update().get(pk=base_payment.pk)

    if payment.estado == PagoTesoreria.Estado.ANULADO:
        raise ValidationError({"pago": "El pago ya esta anulado."})
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de anulacion es obligatorio."})

    payment.estado = PagoTesoreria.Estado.ANULADO
    payment.motivo_anulacion = motivo
    payment.anulado_por = actor
    payment.anulado_en = timezone.now()
    payment.full_clean()
    payment.save(
        skip_domain_guard=True,
        update_fields=[
            "estado",
            "motivo_anulacion",
            "anulado_por",
            "anulado_en",
        ]
    )
    _recalculate_payable_locked(payable)
    return payment
