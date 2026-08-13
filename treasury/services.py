from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, DateField, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone

from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    ObjetivoRubroEconomico,
    PagoTesoreria,
    Proveedor,
    SaldoInicialCuentaBancaria,
)
from .permissions import ensure_delete_central_cash_movement, ensure_treasury_admin


def formato_money(value) -> str:
    """Formato de plata argentino: $ 1.209.905,08.

    Vive aca porque los servicios tambien arman mensajes con importes (los que ve
    el usuario cuando algo no cuadra). `views._money` y el filtro `money` de las
    plantillas delegan a esta, asi hay UN solo formato en todo el sistema.
    """
    if value is None:
        value = Decimal("0.00")
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except (ArithmeticError, TypeError, ValueError):
            return "$ 0,00"
    formatted = f"{value:,.2f}"
    return f"$ {formatted.replace(',', '_').replace('.', ',').replace('_', '.')}"


_money = formato_money


def _require_actor(actor) -> None:
    if actor is None or not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Se requiere usuario para operar tesoreria.")
    ensure_treasury_admin(actor)


def _save_instance(instance):
    instance.full_clean()
    instance.save()
    return instance


def _existing_by_creation_token(manager, token_alta):
    """Idempotencia de alta (mismo patron que cashops): si vuelve el mismo
    token (doble click, reintento tras un timeout, volver atras y reenviar),
    se devuelve lo ya creado en lugar de mover plata de nuevo."""
    if not token_alta:
        return None
    return manager.filter(token_alta=token_alta).first()


def _guardar_alta_idempotente(instance, manager, token_alta):
    """Guarda una alta con full_clean dentro de un savepoint. Si la carrera del
    doble submit choca la constraint del token (sale como ValidationError o
    IntegrityError segun quien llegue primero a la base), devuelve lo ya
    creado; cualquier otro error se propaga igual que antes."""
    try:
        with transaction.atomic():
            return _save_instance(instance)
    except (IntegrityError, ValidationError):
        existing = _existing_by_creation_token(manager, token_alta)
        if existing is None:
            raise
        return existing


def _first_day_of_month(value: date) -> date:
    return value.replace(day=1)


def _first_day_of_next_month(value: date) -> date:
    first_day = _first_day_of_month(value)
    if first_day.month == 12:
        return date(first_day.year + 1, 1, 1)
    return date(first_day.year, first_day.month + 1, 1)


def _validate_payable_category_mapping(*, activo: bool, rubro_operativo) -> None:
    if activo and rubro_operativo is None:
        raise ValidationError({"rubro_operativo": "El rubro operativo es obligatorio para categorias activas."})
    if rubro_operativo is not None and (not rubro_operativo.activo or rubro_operativo.es_sistema):
        raise ValidationError({"rubro_operativo": "El rubro operativo debe estar activo y no puede ser de sistema."})


def _ensure_payable_category_is_economic(category: CategoriaCuentaPagar) -> None:
    if not category.rubro_operativo_id:
        raise ValidationError(
            {"categoria": "La categoria debe tener un rubro operativo asociado para registrar deuda economica."}
        )
    if not category.rubro_operativo.activo or category.rubro_operativo.es_sistema:
        raise ValidationError(
            {"categoria": "La categoria esta asociada a un rubro operativo inactivo o de sistema."}
        )


def _month_starts_between(date_from: date, date_to: date) -> list[date]:
    current = _first_day_of_month(date_from)
    end = _first_day_of_month(date_to)
    months = []
    while current <= end:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _resolve_economic_targets(*, period_from: date, period_to: date, sucursal=None) -> dict[tuple[int, date], ObjetivoRubroEconomico]:
    objectives = (
        ObjetivoRubroEconomico.objects.filter(
            activo=True,
            vigencia_desde__lte=period_to,
        )
        .filter(Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=period_from))
        .select_related("rubro_operativo", "sucursal")
    )
    if sucursal is None:
        objectives = objectives.filter(sucursal__isnull=True)
    else:
        objectives = objectives.filter(Q(sucursal=sucursal) | Q(sucursal__isnull=True))

    resolved: dict[tuple[int, date], ObjetivoRubroEconomico] = {}
    month_starts = _month_starts_between(period_from, period_to)
    branch_id = getattr(sucursal, "pk", None)

    for objective in objectives.order_by("rubro_operativo_id", "vigencia_desde", "pk"):
        for month_start in month_starts:
            if objective.vigencia_desde > month_start:
                continue
            if objective.vigencia_hasta and objective.vigencia_hasta < month_start:
                continue
            key = (objective.rubro_operativo_id, month_start)
            current = resolved.get(key)
            objective_priority = (
                1 if branch_id is not None and objective.sucursal_id == branch_id else 0,
                objective.vigencia_desde,
                objective.pk,
            )
            current_priority = (
                1 if current and branch_id is not None and current.sucursal_id == branch_id else 0,
                current.vigencia_desde if current else date.min,
                current.pk if current else 0,
            )
            if current is None or objective_priority > current_priority:
                resolved[key] = objective
    return resolved


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
    _validate_payable_category_mapping(activo=activo, rubro_operativo=rubro_operativo)
    category = CategoriaCuentaPagar(
        nombre=nombre,
        rubro_operativo=rubro_operativo,
        activo=activo,
        creado_por=actor,
    )
    return _save_instance(category)


def get_or_create_payable_category_for_rubro(rubro, *, actor=None) -> CategoriaCuentaPagar:
    """Devuelve una CategoriaCuentaPagar economica para el rubro elegido.

    El alta de deuda del cajero elige un RUBRO; el modelo de deuda igual
    necesita una categoria. Reutiliza una categoria activa ya asociada a ese
    rubro; si no hay ninguna, crea una canonica con el nombre del rubro. Asi el
    cajero clasifica por rubro y el economico sigue imputando por rubro.
    """
    if rubro is None:
        raise ValidationError({"rubro": "El rubro es obligatorio."})
    if not rubro.activo or rubro.es_sistema:
        raise ValidationError({"rubro": "Tenes que elegir un rubro operativo activo y valido."})
    category = (
        CategoriaCuentaPagar.objects.filter(rubro_operativo=rubro, activo=True)
        .order_by("id")
        .first()
    )
    if category is None:
        # Artefacto interno para el alta de deuda del cajero (elige rubro, no
        # gestiona tesoreria): se crea con _save_instance, sin el gate de
        # permiso de tesoreria de create_payable_category.
        category = _save_instance(
            CategoriaCuentaPagar(
                nombre=rubro.nombre,
                rubro_operativo=rubro,
                activo=True,
                creado_por=actor,
            )
        )
    return category


def update_payable_category(
    *,
    category: CategoriaCuentaPagar,
    nombre,
    actor=None,
    activo=True,
    rubro_operativo=None,
) -> CategoriaCuentaPagar:
    _require_actor(actor)
    _validate_payable_category_mapping(activo=activo, rubro_operativo=rubro_operativo)
    category.nombre = nombre
    category.rubro_operativo = rubro_operativo
    category.activo = activo
    category.actualizado_por = actor
    return _save_instance(category)


def toggle_payable_category(*, category: CategoriaCuentaPagar, actor=None) -> CategoriaCuentaPagar:
    _require_actor(actor)
    target_active = not category.activo
    _validate_payable_category_mapping(activo=target_active, rubro_operativo=category.rubro_operativo)
    category.activo = target_active
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
    empresa=None,
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
        empresa=empresa,
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
    empresa=None,
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
    bank_account.empresa = empresa
    bank_account.sucursal = sucursal
    bank_account.activa = activa
    bank_account.actualizado_por = actor
    return _save_instance(bank_account)


def toggle_bank_account(*, bank_account: CuentaBancaria, actor=None) -> CuentaBancaria:
    _require_actor(actor)
    bank_account.activa = not bank_account.activa
    bank_account.save(update_fields=["activa", "actualizado_en"])
    return bank_account


def set_initial_bank_balance(
    *,
    cuenta_bancaria: CuentaBancaria,
    fecha_referencia: date,
    importe: Decimal,
    motivo: str,
    actor=None,
) -> SaldoInicialCuentaBancaria:
    _require_actor(actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio."})
    balance, created = SaldoInicialCuentaBancaria.objects.get_or_create(
        cuenta_bancaria=cuenta_bancaria,
        fecha_referencia=fecha_referencia,
        defaults={
            "importe": importe,
            "motivo": motivo,
            "creado_por": actor,
        },
    )
    if created:
        return _save_instance(balance)

    balance.importe_anterior = balance.importe
    balance.importe = importe
    balance.motivo_correccion = motivo
    balance.actualizado_por = actor
    return _save_instance(balance)


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
        raise ValidationError({"categoria": "La categoría está inactiva."})
    _ensure_payable_category_is_economic(categoria)
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
        raise ValidationError({"categoria": "La categoría está inactiva."})
    _ensure_payable_category_is_economic(categoria)
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


def _ensure_special_commitment_can_be_paid(payable: CuentaPorPagar) -> None:
    commitment = getattr(payable, "compromiso_especial", None)
    if commitment is None:
        return
    if commitment.estado in {CompromisoEspecial.Estado.RECHAZADO, CompromisoEspecial.Estado.CANCELADO}:
        raise ValidationError({"cuenta_por_pagar": "El compromiso especial esta rechazado o cancelado."})
    if commitment.requiere_autorizacion and not commitment.aprobado:
        raise ValidationError({"cuenta_por_pagar": "El compromiso especial requiere aprobacion previa."})
    if not commitment.sustento_referencia and not payable.referencia_comprobante:
        raise ValidationError({"cuenta_por_pagar": "El compromiso especial requiere comprobante o sustento."})


def _mark_special_commitment_if_paid(payable: CuentaPorPagar, actor=None) -> None:
    commitment = getattr(payable, "compromiso_especial", None)
    if commitment is None or payable.estado != CuentaPorPagar.Estado.PAGADA:
        return
    if commitment.estado == CompromisoEspecial.Estado.EJECUTADO:
        return
    commitment.estado = CompromisoEspecial.Estado.EJECUTADO
    commitment.actualizado_por = actor
    commitment.full_clean()
    commitment.save(update_fields=["estado", "actualizado_por", "actualizado_en"])


def register_special_commitment(
    *,
    tipo: str,
    concepto: str,
    sustento_referencia: str,
    monto_estimado: Decimal,
    cuenta_por_pagar: CuentaPorPagar = None,
    sucursal=None,
    organismo: str = "",
    beneficiario: str = "",
    expediente: str = "",
    periodo_fiscal=None,
    fecha_compromiso=None,
    vencimiento=None,
    prioridad: str = CompromisoEspecial.Prioridad.MEDIA,
    requiere_autorizacion: bool = False,
    plan_nombre: str = "",
    numero_cuota=None,
    total_cuotas=None,
    capital: Decimal = Decimal("0.00"),
    interes_financiero: Decimal = Decimal("0.00"),
    interes_resarcitorio: Decimal = Decimal("0.00"),
    actor=None,
) -> CompromisoEspecial:
    _require_actor(actor)
    if tipo != CompromisoEspecial.Tipo.REQUERIMIENTO and cuenta_por_pagar is None:
        raise ValidationError({"cuenta_por_pagar": "El compromiso debe vincular una cuenta por pagar."})
    if cuenta_por_pagar is not None and cuenta_por_pagar.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "No se puede vincular una deuda anulada."})
    auto_requires_approval = tipo in {
        CompromisoEspecial.Tipo.ADELANTO,
        CompromisoEspecial.Tipo.SUELDO_EXTRAORDINARIO,
    }
    commitment = CompromisoEspecial(
        tipo=tipo,
        concepto=concepto,
        sustento_referencia=sustento_referencia,
        monto_estimado=monto_estimado,
        cuenta_por_pagar=cuenta_por_pagar,
        sucursal=sucursal or (cuenta_por_pagar.sucursal if cuenta_por_pagar else None),
        organismo=organismo,
        beneficiario=beneficiario,
        expediente=expediente,
        periodo_fiscal=periodo_fiscal,
        fecha_compromiso=fecha_compromiso or timezone.localdate(),
        vencimiento=vencimiento or (cuenta_por_pagar.fecha_vencimiento if cuenta_por_pagar else None),
        prioridad=prioridad,
        requiere_autorizacion=requiere_autorizacion or auto_requires_approval,
        estado=(
            CompromisoEspecial.Estado.APROBACION_PENDIENTE
            if (requiere_autorizacion or auto_requires_approval)
            else CompromisoEspecial.Estado.PENDIENTE
        ),
        plan_nombre=plan_nombre,
        numero_cuota=numero_cuota,
        total_cuotas=total_cuotas,
        capital=capital or Decimal("0.00"),
        interes_financiero=interes_financiero or Decimal("0.00"),
        interes_resarcitorio=interes_resarcitorio or Decimal("0.00"),
        creado_por=actor,
    )
    return _save_instance(commitment)


def decide_special_commitment(
    *,
    commitment: CompromisoEspecial,
    aprobado: bool,
    comentario: str = "",
    actor=None,
) -> CompromisoEspecial:
    _require_actor(actor)
    if not commitment.requiere_autorizacion:
        raise ValidationError({"compromiso": "Este compromiso no requiere autorizacion."})
    comentario = (comentario or "").strip()
    if not aprobado and not comentario:
        raise ValidationError({"comentario": "El comentario es obligatorio para rechazar."})
    commitment.estado = CompromisoEspecial.Estado.APROBADO if aprobado else CompromisoEspecial.Estado.RECHAZADO
    commitment.autorizado_por = actor
    commitment.autorizado_en = timezone.now()
    commitment.comentario_autorizacion = comentario
    commitment.actualizado_por = actor
    return _save_instance(commitment)


def build_special_commitments_snapshot(*, date_from: date, date_to: date, sucursal=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    commitments = CompromisoEspecial.objects.select_related(
        "cuenta_por_pagar",
        "sucursal",
        "autorizado_por",
    ).filter(
        Q(vencimiento__gte=date_from, vencimiento__lte=date_to)
        | Q(vencimiento__isnull=True, fecha_compromiso__gte=date_from, fecha_compromiso__lte=date_to)
    )
    if sucursal is not None:
        commitments = commitments.filter(sucursal=sucursal)

    totals = commitments.aggregate(total=Sum("monto_estimado"), count=Count("id"))
    by_type = list(commitments.values("tipo").annotate(total=Sum("monto_estimado"), count=Count("id")).order_by("tipo"))
    pending_approval = commitments.filter(
        requiere_autorizacion=True,
        estado=CompromisoEspecial.Estado.APROBACION_PENDIENTE,
    )
    due_commitments = commitments.filter(
        estado__in=[
            CompromisoEspecial.Estado.PENDIENTE,
            CompromisoEspecial.Estado.APROBACION_PENDIENTE,
            CompromisoEspecial.Estado.APROBADO,
        ]
    ).order_by("vencimiento", "fecha_compromiso", "id")
    plan_rows = commitments.filter(tipo=CompromisoEspecial.Tipo.PLAN_PAGO).values("plan_nombre").annotate(
        total=Sum("monto_estimado"),
        cuotas=Count("id"),
        pendiente=Sum("cuenta_por_pagar__saldo_pendiente"),
    ).order_by("plan_nombre")
    return {
        "date_from": date_from,
        "date_to": date_to,
        "sucursal": sucursal,
        "total": totals["total"] or Decimal("0.00"),
        "count": totals["count"] or 0,
        "by_type": by_type,
        "pending_approval_count": pending_approval.count(),
        "pending_approval_total": pending_approval.aggregate(total=Sum("monto_estimado"))["total"] or Decimal("0.00"),
        "due_commitments": due_commitments,
        "plan_rows": plan_rows,
    }


def _empresa_de_la_deuda(payable: CuentaPorPagar, empresa=None) -> int:
    """De que empresa sale el efectivo que paga esta deuda.

    Primero se deduce de la deuda: su sucursal, o la caja de la que nacio si fue
    un gasto cargado en caja. Muchas deudas se cargan sin sucursal, asi que la
    vista de pago aporta la empresa activa como respaldo. Si no hay ninguna de
    las dos hay que cortar: antes todo pago en efectivo caia en una caja global
    sin empresa, y eso es justo lo que dejo plata sin dueno en produccion.
    """
    empresa_id = None
    if payable.sucursal_id and payable.sucursal.empresa_id:
        empresa_id = payable.sucursal.empresa_id
    elif payable.caja_origen_id and payable.caja_origen.sucursal_id:
        empresa_id = payable.caja_origen.sucursal.empresa_id
    if not empresa_id and empresa is not None:
        empresa_id = getattr(empresa, "pk", empresa)
    if not empresa_id:
        raise ValidationError(
            {
                "cuenta_por_pagar": (
                    "No se puede saber de que boveda sale el efectivo: la deuda no tiene "
                    "sucursal y no se indico empresa. Asignale sucursal a la deuda."
                )
            }
        )
    return empresa_id


def _referencia_de_linea(referencia: str, indice: int, total: int) -> str:
    """Referencia de UNA linea cuando un mismo instrumento paga varias facturas.

    `PagoTesoreria` tiene unicidad por (cuenta, medio de pago, referencia), asi
    que repetir el numero de cheque u operacion tal cual en cada factura hace
    explotar la segunda. Se sufija "REF (2/3)". El recorte a 80 se hace sobre la
    base y no sobre el resultado: si no, una referencia al limite perderia el
    sufijo y volveria a chocar.
    """
    if not referencia or total <= 1:
        return referencia
    sufijo = f" ({indice}/{total})"
    return f"{referencia[: 80 - len(sufijo)]}{sufijo}"


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
    empresa=None,
    bank_movement: MovimientoBancario = None,
    token_alta=None,
    actor=None,
) -> PagoTesoreria:
    _require_actor(actor)
    # Reenvio del mismo formulario: el pago ya existe, se devuelve tal cual
    # ANTES de tomar ningun lock ni mover plata de nuevo.
    existing = _existing_by_creation_token(PagoTesoreria.objects, token_alta)
    if existing is not None:
        return existing
    locked_payable = CuentaPorPagar.objects.select_for_update().get(pk=payable.pk)
    if bank_account:
        bank_account = CuentaBancaria.objects.get(pk=bank_account.pk)
        if not bank_account.activa:
            raise ValidationError({"cuenta_bancaria": "La cuenta bancaria está inactiva."})
    if locked_payable.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar esta anulada."})
    if locked_payable.estado == CuentaPorPagar.Estado.PAGADA:
        raise ValidationError({"cuenta_por_pagar": "La cuenta por pagar ya esta cancelada."})
    _ensure_special_commitment_can_be_paid(locked_payable)
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
        token_alta=token_alta,
        creado_por=actor,
    )
    # Savepoint para la carrera: dos POST simultaneos con el mismo token pasan
    # ambos el chequeo de arriba; la constraint parcial corta al segundo y se
    # le devuelve el pago del primero. El save de PagoTesoreria pasa por
    # full_clean, asi que el duplicado puede salir como ValidationError
    # (validate_unique) o como IntegrityError segun quien llegue a la base.
    try:
        with transaction.atomic():
            payment.save(skip_domain_guard=True)
    except (IntegrityError, ValidationError):
        existing = _existing_by_creation_token(PagoTesoreria.objects, token_alta)
        if existing is None:
            raise
        return existing

    # Todo pago tiene que mover la disponibilidad de donde salio la plata: el
    # efectivo baja la caja fuerte, y transferencia/cheque/ECHEQ bajan el banco.
    if medio_pago == PagoTesoreria.MedioPago.EFECTIVO:
        register_central_cash_movement(
            empresa=_empresa_de_la_deuda(locked_payable, empresa),
            tipo=MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            monto=monto,
            concepto=f"Pago a {locked_payable.proveedor}: {locked_payable.concepto}",
            fecha=fecha_pago,
            pago_tesoreria=payment,
            actor=actor,
        )
    elif bank_movement is not None:
        # El debito ya existe en el extracto: se vincula en lugar de crear otro.
        # Va ANTES de _recalculate_payable_locked a proposito: si la deuda queda
        # PAGADA, el clean de PagoTesoreria rechaza volver a guardar el pago y la
        # vinculacion ya no se puede hacer.
        link_payment_to_bank_movement(
            payment=payment, bank_movement=bank_movement, actor=actor
        )
    elif bank_account is not None:
        _create_bank_movement_for_payment(payment, payable=locked_payable, actor=actor)

    _recalculate_payable_locked(locked_payable)
    locked_payable.refresh_from_db()
    _mark_special_commitment_if_paid(locked_payable, actor=actor)
    return payment


@transaction.atomic
def register_supplier_payment_batch(
    *,
    proveedor: Proveedor,
    lineas,
    bank_account: CuentaBancaria = None,
    medio_pago: str,
    fecha_pago,
    referencia: str = "",
    observaciones: str = "",
    token_alta=None,
    actor=None,
) -> list[PagoTesoreria]:
    """Paga VARIAS facturas de UN mismo proveedor en una sola operacion.

    `lineas` es un iterable de (CuentaPorPagar, monto). Se crea UN PagoTesoreria
    por deuda -> el seguimiento por factura queda intacto y toda la validacion de
    register_payment (sobrepago, deuda anulada/pagada, compromiso especial,
    recalculo de saldo) se sigue aplicando por deuda. Es atomico: si falla una
    linea no se registra ninguna.

    La referencia se sufija por linea ("REF (1/3)") porque PagoTesoreria tiene
    unicidad por (cuenta_bancaria, medio_pago, referencia): repetirla tal cual
    haria explotar el segundo pago.
    """
    _require_actor(actor)
    # Reenvio del mismo lote: el token viaja en el PRIMER pago (la constraint
    # es un token -> un registro). Se devuelve ese pago solo — la lista
    # original no se puede reconstruir — pero alcanza para que el reenvio no
    # pague el lote de nuevo.
    existing = _existing_by_creation_token(PagoTesoreria.objects, token_alta)
    if existing is not None:
        return [existing]
    lineas = [(payable, monto) for payable, monto in lineas if monto and monto > 0]
    if not lineas:
        raise ValidationError({"__all__": "Elegí al menos una factura con importe a pagar."})

    for payable, _ in lineas:
        if payable.proveedor_id != proveedor.pk:
            raise ValidationError(
                {"__all__": "Todas las facturas del pago deben ser del mismo proveedor."}
            )

    total = len(lineas)
    referencia = (referencia or "").strip()
    pagos = []
    # Orden estable por pk: evita deadlocks entre lotes concurrentes que compartan
    # deudas, porque register_payment toma select_for_update por deuda.
    for indice, (payable, monto) in enumerate(sorted(lineas, key=lambda item: item[0].pk), start=1):
        linea_referencia = _referencia_de_linea(referencia, indice, total)
        pagos.append(
            register_payment(
                payable=payable,
                bank_account=bank_account,
                medio_pago=medio_pago,
                fecha_pago=fecha_pago,
                monto=monto,
                referencia=linea_referencia,
                observaciones=observaciones,
                token_alta=token_alta if indice == 1 else None,
                actor=actor,
            )
        )
    return pagos


def register_transfer_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    fecha_pago,
    monto: Decimal,
    referencia: str = "",
    observaciones: str = "",
    bank_movement: MovimientoBancario = None,
    token_alta=None,
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
        bank_movement=bank_movement,
        token_alta=token_alta,
        actor=actor,
    )


def pay_debt_from_bank_movement(
    *,
    bank_movement: MovimientoBancario,
    payable: CuentaPorPagar,
    monto: Decimal = None,
    observaciones: str = "",
    referencia: str = None,
    actor=None,
) -> PagoTesoreria:
    """Paga una deuda desde una transferencia que ya esta en el extracto.

    Antes habia que cargar el pago a mano y despues vincularlo. Ahora se elige la
    factura y el pago se genera solo, sin crear un segundo debito.

    US-4.10: `monto` permite usar solo una parte de la transferencia, para
    repartirla entre varias facturas. Si no se pasa, se usa todo lo que le queda
    sin asignar (el comportamiento de antes, cuando pagaba una sola factura).

    `referencia` la pasa el reparto de varias facturas, ya sufijada por linea. Sin
    ella se usa la del movimiento tal cual, que es lo correcto para una factura
    sola.
    """
    _require_actor(actor)
    if bank_movement.estado != MovimientoBancario.Estado.REGISTRADO:
        raise ValidationError({"__all__": "El movimiento bancario esta anulado."})
    if bank_movement.tipo != MovimientoBancario.Tipo.DEBITO:
        raise ValidationError({"__all__": "Solo un debito puede pagar una deuda."})
    if payable.estado == CuentaPorPagar.Estado.ANULADA:
        raise ValidationError({"cuenta_por_pagar": "La deuda esta anulada."})

    # ARMADI y MAPOGO son empresas distintas: la plata de la cuenta de una no
    # puede pagar la factura de la otra. Se compara solo cuando los dos lados
    # tienen empresa conocida; las cuentas y las deudas legacy (sin empresa o sin
    # sucursal) siguen pasando, para no bloquear lo historico.
    empresa_de_la_cuenta = getattr(bank_movement.cuenta_bancaria, "empresa_id", None)
    empresa_de_la_deuda = payable.sucursal.empresa_id if payable.sucursal_id else None
    if empresa_de_la_cuenta and empresa_de_la_deuda and empresa_de_la_cuenta != empresa_de_la_deuda:
        raise ValidationError(
            {
                "cuenta_por_pagar": (
                    "Esa factura es de otra empresa: no se puede pagar con esta cuenta bancaria."
                )
            }
        )

    sin_asignar = importe_sin_asignar_del_movimiento(bank_movement)
    if sin_asignar <= 0:
        raise ValidationError({"__all__": "Esta transferencia ya esta asignada por completo."})
    monto = sin_asignar if monto is None else Decimal(monto)
    if monto <= 0:
        raise ValidationError({"monto": "El importe a asignar tiene que ser mayor que cero."})
    if monto > sin_asignar:
        raise ValidationError(
            {
                "monto": (
                    f"A esta transferencia le quedan {_money(sin_asignar)} sin asignar y estas "
                    f"queriendo asignar {_money(monto)}."
                )
            }
        )
    if monto > payable.saldo_pendiente:
        raise ValidationError(
            {
                "cuenta_por_pagar": (
                    f"A la factura le quedan {_money(payable.saldo_pendiente)} y estas queriendo "
                    f"asignarle {_money(monto)}."
                )
            }
        )
    return register_transfer_payment(
        payable=payable,
        bank_account=bank_movement.cuenta_bancaria,
        fecha_pago=bank_movement.fecha,
        monto=monto,
        referencia=(bank_movement.referencia or "") if referencia is None else referencia,
        observaciones=observaciones,
        bank_movement=bank_movement,
        actor=actor,
    )


@transaction.atomic
def pay_debts_from_bank_movement(
    *,
    bank_movement: MovimientoBancario,
    asignaciones,
    observaciones: str = "",
    actor=None,
) -> list:
    """US-4.10: reparte UNA transferencia entre VARIAS facturas.

    `asignaciones` es una lista de (CuentaPorPagar, monto). Pueden ser de
    proveedores distintos: el pago semanal de cuenta corriente sale en un solo
    monto y cubre las facturas de varios proveedores a la vez.

    Es todo o nada. Si una sola asignacion falla, no queda ninguna hecha: media
    transferencia repartida es peor que ninguna, porque despues no se sabe que
    parte falto.

    La suma se controla contra lo que le queda sin asignar al movimiento, con la
    fila bloqueada (ver link_payment_to_bank_movement).
    """
    _require_actor(actor)
    if not asignaciones:
        raise ValidationError({"__all__": "Elegi al menos una factura y su importe."})

    bloqueado = MovimientoBancario.objects.select_for_update(of=("self",)).get(pk=bank_movement.pk)
    if bloqueado.estado != MovimientoBancario.Estado.REGISTRADO:
        raise ValidationError({"__all__": "El movimiento bancario esta anulado."})
    if bloqueado.tipo != MovimientoBancario.Tipo.DEBITO:
        raise ValidationError({"__all__": "Solo un debito puede pagar una deuda."})

    total_a_asignar = sum((Decimal(monto) for _payable, monto in asignaciones), Decimal("0.00"))
    sin_asignar = importe_sin_asignar_del_movimiento(bloqueado)
    if total_a_asignar > sin_asignar:
        raise ValidationError(
            {
                "__all__": (
                    f"Estas repartiendo {_money(total_a_asignar)} y a la transferencia le quedan "
                    f"{_money(sin_asignar)} sin asignar."
                )
            }
        )

    # La referencia del movimiento viaja a cada pago, y PagoTesoreria tiene
    # unicidad por (cuenta, medio de pago, referencia): repartir una transferencia
    # CON referencia entre dos o mas facturas chocaba la constraint en el segundo
    # pago y, al ser todo o nada, hacia fallar el reparto entero. El reparto solo
    # funcionaba con transferencias sin referencia.
    # Se sufija por linea igual que register_supplier_payment_batch, numerando
    # desde los pagos que la transferencia ya tenia de un reparto anterior: asi
    # los indices no se repiten nunca para un mismo movimiento.
    referencia_base = (bloqueado.referencia or "").strip()
    ya_repartidas = bloqueado.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).count()
    total_lineas = ya_repartidas + len(asignaciones)

    vistas = set()
    pagos = []
    for orden, (payable, monto) in enumerate(asignaciones, start=ya_repartidas + 1):
        if payable.pk in vistas:
            raise ValidationError({"__all__": "Elegiste dos veces la misma factura."})
        vistas.add(payable.pk)
        pagos.append(
            pay_debt_from_bank_movement(
                bank_movement=bloqueado,
                payable=payable,
                monto=monto,
                observaciones=observaciones,
                referencia=_referencia_de_linea(referencia_base, orden, total_lineas),
                actor=actor,
            )
        )
    return pagos


def register_cheque_payment(
    *,
    payable: CuentaPorPagar,
    bank_account: CuentaBancaria,
    fecha_pago,
    monto: Decimal,
    referencia: str,
    fecha_diferida=None,
    observaciones: str = "",
    token_alta=None,
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
        token_alta=token_alta,
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
    token_alta=None,
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
        token_alta=token_alta,
        actor=actor,
    )


def register_cash_payment(
    *,
    payable: CuentaPorPagar,
    fecha_pago,
    monto: Decimal,
    observaciones: str = "",
    empresa=None,
    token_alta=None,
    actor=None,
) -> PagoTesoreria:
    return register_payment(
        payable=payable,
        bank_account=None,
        medio_pago=PagoTesoreria.MedioPago.EFECTIVO,
        fecha_pago=fecha_pago,
        monto=monto,
        observaciones=observaciones,
        empresa=empresa,
        token_alta=token_alta,
        actor=actor,
    )


def _create_bank_movement_for_payment(payment: PagoTesoreria, *, payable: CuentaPorPagar, actor=None):
    """Genera el debito bancario del pago, para que el saldo del banco baje de verdad.

    Antes de esto solo el pago en EFECTIVO movia una disponibilidad (la caja fuerte).
    Transferencia, cheque y ECHEQ bajaban el saldo_pendiente de la deuda pero NO
    tocaban el banco, asi que el saldo bancario y el KPI de cobertura de deuda
    quedaban sistematicamente optimistas hasta que alguien cargara el debito a mano.

    La imputacion (proveedor, categoria, rubro, sucursal, periodo) se hereda de la
    deuda pagada, igual que hace link_payment_to_bank_movement con un movimiento
    cargado a mano.

    Devuelve None sin crear nada si la deuda no tiene la imputacion completa: el
    clean() del modelo exige rubro + sucursal + periodo en todo debito vigente, y
    preferimos que el pago se registre igual antes que bloquear una cobranza por un
    dato de catalogo faltante. En ese caso el pago queda con estado_bancario
    PENDIENTE, visible como badge en el listado de pagos.
    OJO: para esos casos "Vincular a pago" NO siempre alcanza como salida, porque
    link_payment_to_bank_movement re-guarda el pago y el clean() de PagoTesoreria
    rechaza el re-guardado si la deuda ya quedo PAGADA. Es una limitacion previa a
    este slice; se completa la imputacion de la deuda y se re-registra el pago.

    ORDEN IMPORTANTE: se llama ANTES de _recalculate_payable_locked, cuando la deuda
    todavia no esta marcada PAGADA. Al reves, el mismo clean() del pago bloquearia el
    save() que marca estado_bancario. El test de pago total cubre esta dependencia.
    """
    rubro = payable.categoria.rubro_operativo if payable.categoria_id else None
    if not rubro or not payable.sucursal_id or not payable.periodo_referencia:
        return None
    movement = create_bank_movement(
        cuenta_bancaria=payment.cuenta_bancaria,
        tipo=MovimientoBancario.Tipo.DEBITO,
        fecha=payment.fecha_pago,
        monto=payment.monto,
        concepto=f"Pago a {payable.proveedor}: {payable.concepto}"[:160],
        categoria=payable.categoria,
        rubro_operativo=rubro,
        proveedor=payable.proveedor,
        sucursal_gasto=payable.sucursal,
        periodo_pago=payable.periodo_referencia,
        referencia=payment.referencia,
        origen=MovimientoBancario.Origen.PAGO_TESORERIA,
        pago_tesoreria=payment,
        generado_por_pago=True,
        actor=actor,
    )
    # US-4.10: el vinculo se escribe del lado del pago, asi que se setea despues
    # de tener el movimiento creado (antes viajaba en el propio create).
    payment.movimiento_bancario = movement
    payment.estado_bancario = PagoTesoreria.EstadoBancario.IMPACTADO
    payment.actualizado_por = actor
    payment.save(skip_domain_guard=True)
    return movement


def _release_bank_movement_from_annulled_payment(payment: PagoTesoreria, *, motivo: str, actor=None):
    """Al anular un pago, su movimiento bancario quedaba COLGADO: apuntando a un
    pago anulado y con origen PAGO_TESORERIA, combinacion que el clean() del
    modelo rechaza (exige pago REGISTRADO). Resultado: el movimiento no se podia
    editar, ni eliminar, ni re-vincular, ni imputar — callejon sin salida que solo
    se arreglaba por shell.

    Se lo devuelve a MANUAL conservando la imputacion (rubro/sucursal/periodo) y
    el proveedor. Se desvincula siempre (el clean() exige pago REGISTRADO cuando hay
    pago vinculado, sin excepcion ni para el movimiento anulado).

    Y ADEMAS se anula el movimiento si lo habia generado el sistema al registrar el
    pago (generado_por_pago): en ese caso el debito nunca existio en el banco, nadie
    lo vio en un resumen, asi que dejarlo vigente como MANUAL inflaria el egreso y
    contaria el gasto DOS VECES en la lectura economica (la deuda ya lo conto al
    cargarse, y un debito MANUAL cuenta como gasto por si mismo). Los movimientos
    cargados a mano y despues vinculados siguen liberandose sin anular: esa plata SI
    salio del banco y la decision de borrarla es de la persona.
    """
    movement = getattr(payment, "movimiento_bancario", None)
    if movement is None:
        return None
    generado_por_el_sistema = movement.generado_por_pago
    payment.movimiento_bancario = None

    # US-4.10: si el movimiento paga otras facturas, anular ESTE pago no lo
    # devuelve a MANUAL ni lo anula: la transferencia sigue existiendo en el
    # extracto y sigue pagando las demas. Solo se libera el importe de este pago,
    # que vuelve a quedar sin asignar.
    hermanos = (
        MovimientoBancario.objects.get(pk=movement.pk)
        .pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO)
        .exclude(pk=payment.pk)
        .exists()
    )
    if hermanos:
        nota = f"Pago #{payment.pk} anulado: {motivo}"
        movement.observaciones = f"{movement.observaciones} {nota}".strip()[:255]
        movement.actualizado_por = actor
        _save_instance(movement)
        return movement

    movement.origen = MovimientoBancario.Origen.MANUAL
    movement.generado_por_pago = False
    # Estas clases exigen proveedor; si el movimiento no lo tiene, se baja a
    # "otro egreso" para que siga siendo un movimiento manual valido.
    if movement.clase in {
        MovimientoBancario.Clase.CHEQUE,
        MovimientoBancario.Clase.ECHEQ,
        MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
    } and not movement.proveedor_id:
        movement.clase = MovimientoBancario.Clase.OTRO_EGRESO
    nota = f"Pago #{payment.pk} anulado: {motivo}"
    movement.observaciones = f"{movement.observaciones} {nota}".strip()[:255]
    if generado_por_el_sistema:
        movement.estado = MovimientoBancario.Estado.ANULADO
        movement.motivo_anulacion = f"Anulacion del pago #{payment.pk}: {motivo}"[:255]
        movement.anulado_por = actor
        movement.anulado_en = timezone.now()
    movement.actualizado_por = actor
    _save_instance(movement)
    return movement


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
    # Antes de marcar el pago como anulado: el movimiento vinculado no puede
    # quedar apuntando a un pago no REGISTRADO (lo rechaza su propio clean).
    _release_bank_movement_from_annulled_payment(locked_payment, motivo=motivo, actor=actor)
    # Y el efectivo tiene que volver a la boveda: si el pago fue en efectivo,
    # su EGRESO_PAGO quedaba vivo y la plata no volvia de ningun lado.
    _release_central_cash_movement_from_annulled_payment(locked_payment, motivo=motivo, actor=actor)
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

# Traduccion medio de pago -> tipo financiero del debito bancario. El medio de
# pago del PagoTesoreria es la fuente de verdad; la clase del movimiento se
# deriva de el. Una sola tabla para los dos usos (alta/vinculacion y correccion
# posterior), asi no se pueden desincronizar.
CLASE_POR_MEDIO_DE_PAGO = {
    PagoTesoreria.MedioPago.CHEQUE: MovimientoBancario.Clase.CHEQUE,
    PagoTesoreria.MedioPago.ECHEQ: MovimientoBancario.Clase.ECHEQ,
    PagoTesoreria.MedioPago.TRANSFERENCIA: MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
}


def _infer_bank_movement_class(*, tipo: str, origen: str, payment: PagoTesoreria | None = None) -> str:
    if origen == MovimientoBancario.Origen.ACREDITACION_TARJETA:
        return MovimientoBancario.Clase.ACREDITACION
    if origen == MovimientoBancario.Origen.PAGO_TESORERIA and payment is not None:
        # El pago en efectivo no tiene reflejo bancario, asi que no esta en la
        # tabla; si alguna vez llegara, cae en transferencia como hasta ahora.
        return CLASE_POR_MEDIO_DE_PAGO.get(
            payment.medio_pago, MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS
        )
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
        movimiento_bancario__estado=MovimientoBancario.Estado.REGISTRADO,
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


def _bank_accreditation_movement_scope_query(*, date_from: date, date_to: date) -> Q:
    """
    Scope used by the dashboard for card-sale accreditation follow-up.

    The dashboard compares card sales from cash boxes against bank credits
    registered in Treasury as accreditation movements. Those credits can be
    created directly as bank movements, without an AcreditacionTarjeta record,
    because real bank accreditations enter consolidated by lot and are not
    discriminated by branch or box.

    Existing AcreditacionTarjeta records are still supported, including grouped
    records that cover a sales period even when the bank movement date is later.
    """
    return Q(fecha__gte=date_from, fecha__lte=date_to) | Q(
        acreditacion_tarjeta__modo_registro=AcreditacionTarjeta.ModoRegistro.PERIODO,
        acreditacion_tarjeta__periodo_desde__isnull=False,
        acreditacion_tarjeta__periodo_hasta__isnull=False,
        acreditacion_tarjeta__periodo_desde__gte=date_from,
        acreditacion_tarjeta__periodo_hasta__lte=date_to,
    )


def bank_account_empresa_scope_query(empresa_ids, *, prefix: str = "") -> Q:
    """
    Company scope for bank accounts used by treasury lists and dashboards.

    The owning company is the `empresa` FK (US-4.9). Legacy accounts without
    an assigned company keep their previous visibility: they are included when
    they have no branch (global accounts) or when their branch belongs to the
    selected company scope, until administration completes the owner.
    """
    if not empresa_ids:
        return Q(**{f"{prefix}pk__in": []})
    return (
        Q(**{f"{prefix}empresa_id__in": empresa_ids})
        | Q(**{f"{prefix}empresa__isnull": True, f"{prefix}sucursal__isnull": True})
        | Q(
            **{
                f"{prefix}empresa__isnull": True,
                f"{prefix}sucursal__empresa_id__in": empresa_ids,
            }
        )
    )


def _bank_account_empresa_scope_query(empresa_ids) -> Q:
    return bank_account_empresa_scope_query(empresa_ids)


def _bank_movement_empresa_scope_query(empresa_ids) -> Q:
    return bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")


def _bank_balance_until(account: CuentaBancaria, reference_date: date) -> dict:
    initial_balance = (
        SaldoInicialCuentaBancaria.objects.filter(
            cuenta_bancaria=account,
            fecha_referencia__lte=reference_date,
        )
        .select_related("creado_por", "actualizado_por")
        .order_by("-fecha_referencia", "-id")
        .first()
    )
    movements = MovimientoBancario.objects.filter(
        cuenta_bancaria=account,
        fecha__lte=reference_date,
        estado=MovimientoBancario.Estado.REGISTRADO,
    )
    opening_amount = Decimal("0.00")
    movement_from = None
    if initial_balance is not None:
        opening_amount = initial_balance.importe
        movement_from = initial_balance.fecha_referencia
        movements = movements.filter(fecha__gte=initial_balance.fecha_referencia)
    credits = (
        movements.filter(tipo=MovimientoBancario.Tipo.CREDITO).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    debits = (
        movements.filter(tipo=MovimientoBancario.Tipo.DEBITO).aggregate(total=Sum("monto"))["total"]
        or Decimal("0.00")
    )
    return {
        "initial_balance": initial_balance,
        "initial_amount": opening_amount,
        "movement_from": movement_from,
        "credits": credits,
        "debits": debits,
        "balance": opening_amount + credits - debits,
    }


def _ensure_manual_bank_movement_mutable(movement: MovimientoBancario) -> None:
    if movement.estado == MovimientoBancario.Estado.ANULADO:
        raise ValidationError({"__all__": "No se puede editar ni eliminar un movimiento bancario anulado."})
    if movement.origen != MovimientoBancario.Origen.MANUAL:
        raise ValidationError(
            {"__all__": "Solo se pueden editar o eliminar movimientos manuales desde esta pantalla."}
        )
    if movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exists():
        raise ValidationError(
            {"__all__": "No se puede editar o eliminar un movimiento vinculado a un pago de tesorería."}
        )
    if hasattr(movement, "acreditacion_tarjeta"):
        raise ValidationError(
            {"__all__": "No se puede editar o eliminar un movimiento generado por una acreditación."}
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
    rubro_operativo=None,
    proveedor: Proveedor = None,
    sucursal_gasto=None,
    periodo_pago: date = None,
    referencia: str = "",
    observaciones: str = "",
    origen: str = MovimientoBancario.Origen.MANUAL,
    # Solo se usa para deducir la clase del movimiento (transferencia/cheque/echeq).
    # El vinculo lo escribe el llamador en PagoTesoreria.movimiento_bancario.
    pago_tesoreria: PagoTesoreria = None,
    generado_por_pago: bool = False,
    token_alta=None,
    actor=None,
) -> MovimientoBancario:
    _require_actor(actor)
    existing = _existing_by_creation_token(MovimientoBancario.objects, token_alta)
    if existing is not None:
        return existing
    movement = MovimientoBancario(
        cuenta_bancaria=cuenta_bancaria,
        tipo=tipo,
        fecha=fecha,
        monto=monto,
        concepto=concepto,
        clase=clase or _infer_bank_movement_class(tipo=tipo, origen=origen, payment=pago_tesoreria),
        categoria=categoria,
        rubro_operativo=rubro_operativo,
        proveedor=proveedor,
        sucursal_gasto=sucursal_gasto,
        periodo_pago=periodo_pago,
        referencia=referencia,
        observaciones=observaciones,
        origen=origen,
        generado_por_pago=generado_por_pago,
        token_alta=token_alta,
        creado_por=actor,
    )
    return _guardar_alta_idempotente(movement, MovimientoBancario.objects, token_alta)


def update_bank_movement(
    *,
    movement: MovimientoBancario,
    cuenta_bancaria: CuentaBancaria,
    tipo: str,
    fecha: date,
    monto: Decimal,
    concepto: str,
    clase: str | None = None,
    categoria: CategoriaCuentaPagar = None,
    rubro_operativo=None,
    proveedor: Proveedor = None,
    sucursal_gasto=None,
    periodo_pago: date = None,
    referencia: str = "",
    observaciones: str = "",
    # La vista de edicion comparte el form de alta y pasa **cleaned_data: el
    # token del render se acepta y se ignora, editar no es crear.
    token_alta=None,
    actor=None,
) -> MovimientoBancario:
    _require_actor(actor)
    _ensure_manual_bank_movement_mutable(movement)
    movement.cuenta_bancaria = cuenta_bancaria
    movement.tipo = tipo
    movement.fecha = fecha
    movement.monto = monto
    movement.concepto = concepto
    movement.clase = clase or _infer_bank_movement_class(tipo=tipo, origen=movement.origen, payment=None)
    movement.categoria = categoria
    movement.rubro_operativo = rubro_operativo
    movement.proveedor = proveedor
    movement.sucursal_gasto = sucursal_gasto
    movement.periodo_pago = periodo_pago
    movement.referencia = referencia
    movement.observaciones = observaciones
    movement.actualizado_por = actor
    return _save_instance(movement)


def annul_bank_movement(*, movement: MovimientoBancario, motivo: str, actor=None) -> MovimientoBancario:
    _require_actor(actor)
    _ensure_manual_bank_movement_mutable(movement)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo de eliminación es obligatorio."})
    movement.estado = MovimientoBancario.Estado.ANULADO
    movement.motivo_anulacion = motivo
    movement.anulado_por = actor
    movement.anulado_en = timezone.now()
    movement.actualizado_por = actor
    return _save_instance(movement)


def _mes_de_tesoreria_cerrado(fecha, empresa_id=None) -> bool:
    """Mes cerrado para UNA empresa (el cierre mensual es por empresa desde la
    0034). Sin empresa_id se mantiene el chequeo global, solo para llamadores
    que de verdad no tienen empresa. Una fila legacy sin empresa bloquea
    igual: no se sabe de quien es la foto."""
    qs = CierreMensualTesoreria.objects.filter(mes=fecha.replace(day=1), cerrado=True)
    if empresa_id is not None:
        qs = qs.filter(Q(empresa_id=empresa_id) | Q(empresa__isnull=True))
    return qs.exists()


def _ensure_central_cash_movement_annullable(movement: MovimientoCajaCentral) -> None:
    """Que se puede anular de la boveda y que no.

    Lo generado por otro proceso se anula desde su origen, no desde aca: si se
    anulara el movimiento suelto, el pago o la caja quedarian diciendo que la
    plata se movio cuando ya no se movio.
    """
    if movement.estado == MovimientoCajaCentral.Estado.ANULADO:
        raise ValidationError({"__all__": "El movimiento ya esta anulado."})
    if movement.reversa_de_id:
        raise ValidationError({"__all__": "No se puede anular la reversa de otro movimiento."})
    if movement.pago_tesoreria_id:
        raise ValidationError(
            {"__all__": "Este movimiento lo genero un pago de tesoreria: anula el pago."}
        )
    if movement.caja_cierre_id:
        raise ValidationError(
            {"__all__": "Este movimiento lo genero el cierre de una caja: anula la caja."}
        )
    if movement.tipo in {
        MovimientoCajaCentral.Tipo.INGRESO_CAJA,
        MovimientoCajaCentral.Tipo.EGRESO_PAGO,
    }:
        raise ValidationError(
            {"__all__": "Los ingresos de caja y los egresos por pago no se anulan a mano."}
        )
    # Pendiente de definicion con administracion: para un mes ya cerrado no
    # alcanza con anular. El saldo inicial de cada mes sale del valor GUARDADO en
    # CierreMensualTesoreria, asi que anular hacia atras no devuelve la plata a
    # ningun lado: hace falta contra-asentar en el mes abierto. Falta definir si
    # esa reversa tambien tiene que corregir el gasto por rubro del mes cerrado.
    # Hoy no hay ningun mes cerrado en produccion, asi que esto no bloquea nada.
    if _mes_de_tesoreria_cerrado(movement.fecha, movement.caja_central.empresa_id):
        raise ValidationError(
            {
                "__all__": (
                    f"El mes {movement.fecha:%m/%Y} esta cerrado en tesoreria. "
                    "Todavia no esta definido como se contra-asienta en el mes abierto."
                )
            }
        )


def is_central_cash_movement_annullable(movement: MovimientoCajaCentral) -> bool:
    try:
        _ensure_central_cash_movement_annullable(movement)
    except ValidationError:
        return False
    return True


@transaction.atomic
def annul_central_cash_movement(
    *, movement: MovimientoCajaCentral, motivo: str, actor=None
) -> MovimientoCajaCentral:
    """Anula un movimiento de la boveda con motivo y auditoria.

    Reemplaza la practica de compensar a mano con un ajuste positivo: en
    produccion 7 de los 8 AJUSTE_POSITIVO son parches de gastos cargados dos
    veces, cargados asi porque no habia forma de anular.
    """
    _require_actor(actor)
    ensure_delete_central_cash_movement(actor)
    # of=("self",) es obligatorio: el modelo tiene varias FK nullable y sus LEFT
    # JOIN invalidan el FOR UPDATE en Postgres (el SQLite local lo esconde).
    locked = MovimientoCajaCentral.objects.select_for_update(of=("self",)).get(pk=movement.pk)
    _ensure_central_cash_movement_annullable(locked)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para anular."})
    locked.estado = MovimientoCajaCentral.Estado.ANULADO
    locked.motivo_anulacion = motivo
    locked.anulado_por = actor
    locked.anulado_en = timezone.now()
    return _save_instance(locked)


def _release_central_cash_movement_from_annulled_payment(
    payment: PagoTesoreria, *, motivo: str, actor=None
) -> None:
    """Devuelve a la boveda el efectivo de un pago que se anula.

    Sin esto, anular un pago en efectivo dejaba vivo su EGRESO_PAGO: la deuda
    volvia a quedar pendiente pero la plata nunca volvia a la caja fuerte. El
    EGRESO_PAGO siempre lo genera el sistema, asi que siempre se anula.
    """
    movimientos = MovimientoCajaCentral.objects.filter(
        pago_tesoreria=payment,
        estado=MovimientoCajaCentral.Estado.REGISTRADO,
    )
    for movimiento in movimientos:
        nota = f"Anulacion del pago #{payment.pk}: {motivo}"
        movimiento.estado = MovimientoCajaCentral.Estado.ANULADO
        movimiento.motivo_anulacion = nota
        movimiento.anulado_por = actor
        movimiento.anulado_en = timezone.now()
        movimiento.observaciones = f"{movimiento.observaciones} {nota}".strip()[:255]
        movimiento.save(
            update_fields=[
                "estado",
                "motivo_anulacion",
                "anulado_por",
                "anulado_en",
                "observaciones",
            ]
        )


def complete_bank_movement_imputation(
    *,
    movement: MovimientoBancario,
    rubro_operativo,
    sucursal_gasto,
    periodo_pago: date,
    actor=None,
) -> MovimientoBancario:
    """US-10.13: completa rubro/sucursal/periodo de un debito historico.

    A diferencia de la edicion manual, aplica a cualquier origen (manual,
    pago de tesoreria o egreso) porque solo toca los campos de imputacion
    economica y no altera monto, fecha, cuenta ni vinculos del movimiento.
    """
    _require_actor(actor)
    if movement.estado != MovimientoBancario.Estado.REGISTRADO:
        raise ValidationError({"__all__": "Solo se puede imputar un movimiento bancario registrado."})
    if movement.tipo != MovimientoBancario.Tipo.DEBITO:
        raise ValidationError({"__all__": "Solo los egresos bancarios llevan imputacion por sucursal."})
    if movement.clase == MovimientoBancario.Clase.RETIRO:
        raise ValidationError(
            {"__all__": "Un retiro de banco mueve fondos a caja fuerte y no lleva imputacion economica."}
        )
    if (
        sucursal_gasto is not None
        and sucursal_gasto.empresa_id
        and movement.cuenta_bancaria.empresa_id
        and sucursal_gasto.empresa_id != movement.cuenta_bancaria.empresa_id
    ):
        raise ValidationError(
            {"sucursal_gasto": "La sucursal debe pertenecer a la empresa duena de la cuenta bancaria."}
        )
    movement.rubro_operativo = rubro_operativo
    movement.sucursal_gasto = sucursal_gasto
    movement.periodo_pago = periodo_pago
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
                    "Ya existe una acreditación equivalente para esta cuenta, canal y referencia o período."
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


def importe_asignado_del_movimiento(bank_movement: MovimientoBancario) -> Decimal:
    """Cuanto del movimiento ya esta asignado a deudas (pagos vigentes)."""
    total = bank_movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).aggregate(
        total=Sum("monto")
    )["total"]
    return total or Decimal("0.00")


def importe_sin_asignar_del_movimiento(bank_movement: MovimientoBancario) -> Decimal:
    """Cuanto del movimiento todavia no esta asignado a ninguna deuda."""
    return bank_movement.monto - importe_asignado_del_movimiento(bank_movement)


@transaction.atomic
def link_payment_to_bank_movement(
    *,
    payment: PagoTesoreria,
    bank_movement: MovimientoBancario,
    actor=None,
) -> MovimientoBancario:
    """US-4.5 + US-4.10: vincula un pago de tesoreria con su reflejo bancario.

    Un movimiento puede tener VARIOS pagos (una transferencia que paga 6
    facturas). Lo que ya no puede es que la suma de los pagos pase el importe del
    movimiento: eso seria sacar del banco mas plata de la que salio.

    El movimiento se bloquea antes de sumar. Sin el lock, dos vinculaciones
    simultaneas leen el mismo "queda por asignar", las dos pasan el control y
    juntas se pasan del importe (write skew clasico bajo READ COMMITTED). SQLite
    ignora el lock, asi que este caso no se puede testear localmente.
    """
    _require_actor(actor)

    if bank_movement.estado != MovimientoBancario.Estado.REGISTRADO:
        raise ValidationError("No se puede vincular un movimiento bancario eliminado.")
    if payment.cuenta_bancaria_id != bank_movement.cuenta_bancaria_id:
        raise ValidationError("La cuenta bancaria del pago y el movimiento no coinciden.")

    bank_movement = MovimientoBancario.objects.select_for_update(of=("self",)).get(pk=bank_movement.pk)
    pagos_previos = list(
        bank_movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO).exclude(pk=payment.pk)
    )
    ya_asignado = sum((p.monto for p in pagos_previos), Decimal("0.00"))
    sin_asignar = bank_movement.monto - ya_asignado
    if payment.monto > sin_asignar:
        if pagos_previos:
            raise ValidationError(
                f"A esta transferencia le quedan {_money(sin_asignar)} sin asignar y el pago es de "
                f"{_money(payment.monto)}."
            )
        raise ValidationError("El monto del pago y el movimiento bancario no coinciden.")

    payment.movimiento_bancario = bank_movement
    bank_movement.origen = MovimientoBancario.Origen.PAGO_TESORERIA
    bank_movement.clase = _infer_bank_movement_class(
        tipo=bank_movement.tipo,
        origen=MovimientoBancario.Origen.PAGO_TESORERIA,
        payment=payment,
    )
    payable = payment.cuenta_por_pagar
    # Con un solo pago el movimiento hereda proveedor y categoria de la deuda.
    # Con varios de proveedores distintos no hay UNO que poner: se dejan vacios y
    # los proveedores se leen de los pagos. El rubro/sucursal/periodo heredados
    # siguen siendo los de la PRIMERA factura porque clean() los exige; no se usan
    # para la lectura economica (los debitos con origen PAGO_TESORERIA quedan
    # fuera del gasto: el costo ya lo conto la deuda).
    proveedores = {p.cuenta_por_pagar.proveedor_id for p in pagos_previos} | {payable.proveedor_id}
    if len(proveedores) > 1:
        bank_movement.proveedor = None
        bank_movement.categoria = None
    else:
        bank_movement.proveedor = payable.proveedor
        bank_movement.categoria = payable.categoria
    # US-10.13: el debito vinculado hereda la imputacion de la deuda pagada
    # cuando el movimiento no la tenia; si sigue incompleta, full_clean bloquea
    # la vinculacion indicando exactamente que dato falta. Una categoria legacy
    # sin rubro no pisa un rubro ya cargado en el movimiento.
    # US-4.10: el segundo pago en adelante NO pisa el rubro: con facturas de
    # rubros distintos ganaria la ultima vinculada, que es arbitrario. Queda el de
    # la primera y no afecta ningun total (ver comentario de arriba).
    if payable.categoria.rubro_operativo_id and not pagos_previos:
        bank_movement.rubro_operativo = payable.categoria.rubro_operativo
    if not bank_movement.sucursal_gasto_id and payable.sucursal_id:
        bank_movement.sucursal_gasto = payable.sucursal
    if not bank_movement.periodo_pago and payable.periodo_referencia:
        bank_movement.periodo_pago = payable.periodo_referencia
    bank_movement.actualizado_por = actor
    _save_instance(bank_movement)

    # Update payment status if it was REGISTERED to something indicating bank reflection
    # (Actually PagoTesoreria has estado_bancario)
    payment.estado_bancario = PagoTesoreria.EstadoBancario.IMPACTADO
    payment.actualizado_por = actor
    payment.save(skip_domain_guard=True)

    return bank_movement


@transaction.atomic
def correct_bank_payment_method(
    *,
    bank_movement: MovimientoBancario,
    medio_pago: str,
    referencia: str = "",
    actor=None,
) -> MovimientoBancario:
    """US-4.11: corrige COMO se pago una deuda, sobre un egreso ya registrado.

    Caso real: cargaron el egreso como transferencia y era un cheque. Hasta ahora
    la unica salida era anular los pagos y rehacer todo, porque el detalle del
    movimiento esconde "Editar" apenas queda vinculado a un pago (con razon:
    editar de verdad cambia monto, fecha y cuenta, y eso si moveria la plata).

    Esto toca SOLO la tipificacion: el medio de pago de los pagos vigentes, el
    tipo financiero del movimiento -que se deriva del medio, ver
    CLASE_POR_MEDIO_DE_PAGO- y la referencia del instrumento. Monto, fecha,
    cuenta bancaria, deudas pagadas y vinculos quedan intactos, asi que ningun
    saldo, deuda ni lectura economica se mueve.
    """
    _require_actor(actor)
    if medio_pago not in CLASE_POR_MEDIO_DE_PAGO:
        raise ValidationError(
            {"medio_pago": "Ese medio de pago no corresponde a un egreso bancario."}
        )
    if bank_movement.estado != MovimientoBancario.Estado.REGISTRADO:
        raise ValidationError({"__all__": "No se puede corregir un movimiento bancario eliminado."})
    if bank_movement.tipo != MovimientoBancario.Tipo.DEBITO:
        raise ValidationError({"__all__": "Solo un egreso bancario tiene medio de pago."})

    # Mismo lock que la vinculacion: los pagos vigentes del movimiento son la
    # lista que se va a reescribir, y no puede cambiar debajo.
    movement = MovimientoBancario.objects.select_for_update(of=("self",)).get(pk=bank_movement.pk)
    pagos = list(
        movement.pagos.filter(estado=PagoTesoreria.Estado.REGISTRADO)
        .select_related("cuenta_por_pagar", "cuenta_por_pagar__proveedor")
        .order_by("pk")
    )
    if not pagos:
        raise ValidationError(
            {"__all__": "Este movimiento no paga ninguna factura: corregilo desde Editar."}
        )

    referencia = (referencia or "").strip()
    if (
        medio_pago in {PagoTesoreria.MedioPago.CHEQUE, PagoTesoreria.MedioPago.ECHEQ}
        and not referencia
    ):
        raise ValidationError({"referencia": "La referencia es obligatoria para cheque y ECHEQ."})

    nueva_clase = CLASE_POR_MEDIO_DE_PAGO[medio_pago]
    proveedor = movement.proveedor
    if (
        nueva_clase in {MovimientoBancario.Clase.CHEQUE, MovimientoBancario.Clase.ECHEQ}
        and proveedor is None
    ):
        # Un cheque tiene un unico beneficiario. Si el movimiento no lo tiene
        # cargado se deduce de las facturas que paga; si paga a proveedores
        # distintos, esa tipificacion no existe en la realidad.
        proveedores = {pago.cuenta_por_pagar.proveedor_id for pago in pagos}
        if len(proveedores) > 1:
            raise ValidationError(
                {
                    "medio_pago": (
                        "Este egreso paga facturas de varios proveedores y un cheque tiene un "
                        "solo beneficiario. Anula los pagos que no correspondan antes de "
                        "tipificarlo asi."
                    )
                }
            )
        proveedor = pagos[0].cuenta_por_pagar.proveedor

    clase_anterior = movement.clase
    etiqueta_anterior = movement.get_clase_display()
    # Se compara ANTES de pisar el campo: si la persona no toco la referencia, la
    # de cada pago se respeta (puede ser propia, de cuando el pago se cargo a mano
    # y despues se vinculo) en lugar de pisarla con la del movimiento.
    referencia_cambio = referencia != (movement.referencia or "").strip()

    movement.clase = nueva_clase
    movement.proveedor = proveedor
    movement.referencia = referencia
    if nueva_clase != clase_anterior:
        nota = f"Tipo financiero corregido: {etiqueta_anterior} -> {movement.get_clase_display()}."
        movement.observaciones = f"{movement.observaciones} {nota}".strip()[:255]
    movement.actualizado_por = actor
    _save_instance(movement)

    total = len(pagos)
    for indice, pago in enumerate(pagos, start=1):
        if referencia_cambio or not (pago.referencia or "").strip():
            pago.referencia = _referencia_de_linea(referencia, indice, total)
        pago.medio_pago = medio_pago
        if medio_pago == PagoTesoreria.MedioPago.TRANSFERENCIA:
            # Invariante del modelo: la transferencia no admite fecha diferida.
            # Al volver de cheque a transferencia, el diferimiento deja de existir.
            pago.fecha_diferida = None
        pago.actualizado_por = actor
        pago.save(skip_domain_guard=True)

    return movement


def build_bank_reconciliation_snapshot(
    *,
    cuenta_bancaria: CuentaBancaria,
    date_from: date,
    date_to: date,
) -> dict:
    """
    US-4.6: Simple reconciliation logic.
    """
    from cashops.models import Caja, MovimientoCaja

    # 1. Total sold by Card (from CashOps)
    # We map this to the bank account indirectly if possible, or just global for the period
    # Note: CashOps records don't have account_id directly, but typically one branch uses one account.
    # For now, we take all card sales in the period.
    total_sales = MovimientoCaja.objects.filter(
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        creado_en__date__gte=date_from,
        creado_en__date__lte=date_to,
    ).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
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
        movimiento_bancario__estado=MovimientoBancario.Estado.REGISTRADO,
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


CENTRAL_CASH_IN_TYPES = [
    MovimientoCajaCentral.Tipo.INGRESO_CAJA,
    MovimientoCajaCentral.Tipo.APORTE,
    MovimientoCajaCentral.Tipo.RETIRO_BANCO,
    MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO,
]

CENTRAL_CASH_OUT_TYPES = [
    MovimientoCajaCentral.Tipo.EGRESO_PAGO,
    MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
    MovimientoCajaCentral.Tipo.DEPOSITO_BANCO,
    MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO,
]


def _mapped_bank_treasury_expenses(base_queryset):
    """Bank debits that are safe to read as economic treasury expenses.

    `MANUAL` covers legacy bank expenses loaded before EGRESO_TESORERIA
    existed plus manual debits completed via the imputation worklist.
    `PAGO_TESORERIA` stays out: that expense already entered the economic
    reading as debt (`CuentaPorPagar.importe_total`) when it was loaded.
    """
    return base_queryset.filter(
        estado=MovimientoBancario.Estado.REGISTRADO,
        tipo=MovimientoBancario.Tipo.DEBITO,
        origen__in=[
            MovimientoBancario.Origen.EGRESO_TESORERIA,
            MovimientoBancario.Origen.MANUAL,
        ],
        rubro_operativo__isnull=False,
        sucursal_gasto__isnull=False,
        periodo_pago__isnull=False,
    ).exclude(clase=MovimientoBancario.Clase.RETIRO)


def _pending_bank_treasury_expenses(base_queryset):
    """Treasury bank expenses that still lack economic imputation.

    US-10.13: manual historic debits count as pending too, so the economic
    alert matches the imputation worklist and `reporte_sin_sucursal`. Once
    completed they move into `_mapped_bank_treasury_expenses`.
    """
    return base_queryset.filter(
        estado=MovimientoBancario.Estado.REGISTRADO,
        tipo=MovimientoBancario.Tipo.DEBITO,
        origen__in=[
            MovimientoBancario.Origen.EGRESO_TESORERIA,
            MovimientoBancario.Origen.MANUAL,
        ],
    ).exclude(clase=MovimientoBancario.Clase.RETIRO).filter(
        Q(rubro_operativo__isnull=True)
        | Q(sucursal_gasto__isnull=True)
        | Q(periodo_pago__isnull=True)
    )


def _mapped_central_treasury_expenses(base_queryset):
    """Egresos de la boveda que se pueden leer como gasto economico.

    Espejo de `_mapped_bank_treasury_expenses`. Solo EGRESO_ADMIN: el EGRESO_PAGO
    queda afuera a proposito, igual que del lado bancario queda afuera el origen
    PAGO_TESORERIA, porque ese gasto ya entro a la lectura economica como deuda
    (`CuentaPorPagar.importe_total`). Contarlo tambien aca lo duplicaria.
    """
    return base_queryset.filter(
        estado=MovimientoCajaCentral.Estado.REGISTRADO,
        tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
    )


def _pending_central_treasury_expenses(base_queryset):
    """Egresos de la boveda a los que todavia les falta imputacion economica."""
    return _mapped_central_treasury_expenses(base_queryset).filter(
        Q(rubro_operativo__isnull=True)
        | Q(sucursal_gasto__isnull=True)
        | Q(periodo_pago__isnull=True)
    )


def scope_central_cash_movements(
    movements, *, sucursal=None, empresa_ids=None, incluir_anulados=False
):
    """Acota los movimientos de boveda al alcance pedido.

    Por EMPRESA es un filtro directo, porque la boveda tiene empresa. Antes se
    filtraba por `caja_central.sucursal` y habia una clausula que matcheaba
    cualquier movimiento sin sucursal para CUALQUIER empresa: por eso los mismos
    $21.799.835 se contaban enteros en las dos empresas y la suma de los
    informes por empresa daba mas que el consolidado real.

    Por SUCURSAL, cada movimiento pertenece a la sucursal que lo explica: un
    egreso a la que se lo imputo (`sucursal_gasto`) y un ingreso a la que lo
    aporto (`sucursal_origen`). Antes salia de `caja_central.sucursal`, que con
    una boveda por empresa es siempre None.

    Los movimientos que no son de ninguna sucursal en particular (APORTE,
    RETIRO_BANCO, DEPOSITO_BANCO y los ajustes sin imputar) quedan fuera del
    alcance por sucursal a proposito: son de la empresa, no de un local. Por eso
    la suma de las sucursales puede ser menor que el total de la empresa, y esa
    diferencia es exactamente lo que falta imputar.
    """
    # Los anulados salen de TODO alcance, en forma positiva (que es la
    # convencion del repo para movimientos). Aca cubre de una el saldo
    # acumulado, los snapshots financiero y de disponibilidades, y el libro.
    # `incluir_anulados` existe solo para el LISTADO del libro: los anulados se
    # muestran con su motivo (si se ocultaran, quien anulo no veria que anulo)
    # pero no suman en ningun total.
    if not incluir_anulados:
        movements = movements.filter(estado=MovimientoCajaCentral.Estado.REGISTRADO)
    if sucursal is not None:
        return movements.filter(Q(sucursal_gasto=sucursal) | Q(sucursal_origen=sucursal))
    if empresa_ids is not None:
        if not empresa_ids:
            return movements.none()
        return movements.filter(caja_central__empresa_id__in=empresa_ids)
    return movements


def _central_cash_balance_until(*, reference_date: date, sucursal=None, empresa_ids=None) -> Decimal:
    movements = scope_central_cash_movements(
        MovimientoCajaCentral.objects.filter(fecha__lte=reference_date),
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    sums = movements.aggregate(
        ingresos=Sum(
            "monto",
            filter=Q(
                tipo__in=CENTRAL_CASH_IN_TYPES
            ),
        ),
        egresos=Sum(
            "monto",
            filter=Q(
                tipo__in=CENTRAL_CASH_OUT_TYPES
            ),
        ),
    )
    return (sums["ingresos"] or Decimal("0.00")) - (sums["egresos"] or Decimal("0.00"))


def _collapse_economic_items_by_group(items: list, *, sales_total: Decimal) -> list:
    """Junta en una sola fila los rubros que comparten grupo de lectura.

    Solo agrupa para mostrar: no recalcula ni reimputa nada. Cada importe del
    grupo es la suma exacta de los rubros que quedaron adentro, y los rubros sin
    grupo pasan tal cual. Si no hay ningun grupo activo, devuelve la misma lista.

    Objetivo y desvio del grupo se miden SOLO sobre los rubros que tienen
    objetivo vigente (igual que los totales de la cabecera). Por eso la fila
    lleva `objective_children_count` y `children_count`: sin ese dato, un desvio
    verde sobre 3 de 14 rubros se leeria como si cubriera el grupo entero.
    """
    grouped: dict = {}
    display: list = []
    for item in items:
        rubro = item.get("rubro")
        grupo = rubro.grupo_de_lectura if rubro is not None else None
        if grupo is None:
            display.append(item)
            continue
        row = grouped.get(grupo.pk)
        if row is None:
            row = {
                "rubro": None,
                "grupo": grupo,
                "rubro_nombre": grupo.nombre,
                "sales_total": Decimal("0.00"),
                "cash_expense_total": Decimal("0.00"),
                "treasury_expense_total": Decimal("0.00"),
                "debt_total": Decimal("0.00"),
                "debt_pending": Decimal("0.00"),
                "payables_count": 0,
                "total_expense": Decimal("0.00"),
                "objective_amount": Decimal("0.00"),
                "objective_scope_expense": Decimal("0.00"),
                "children_count": 0,
                "objective_children_count": 0,
                "objective_months": 0,
                "objective_sources": set(),
            }
            grouped[grupo.pk] = row
            display.append(row)
        row["sales_total"] += item["sales_total"]
        row["cash_expense_total"] += item["cash_expense_total"]
        row["treasury_expense_total"] += item["treasury_expense_total"]
        row["debt_total"] += item["debt_total"]
        row["debt_pending"] += item["debt_pending"]
        row["payables_count"] += item["payables_count"]
        row["total_expense"] += item["total_expense"]
        row["children_count"] += 1
        if item["has_objective"]:
            row["objective_amount"] += item["objective_amount"]
            row["objective_scope_expense"] += item["total_expense"]
            row["objective_children_count"] += 1
            row["objective_months"] = max(row["objective_months"], item["objective_months"])
            if item["objective_scope_label"]:
                row["objective_sources"].update(
                    part.strip() for part in item["objective_scope_label"].split("/") if part.strip()
                )

    for row in grouped.values():
        row["expense_ratio_over_sales"] = (
            ((row["total_expense"] * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
            if sales_total > 0
            else Decimal("0.00")
        )
        row["has_objective"] = row["objective_children_count"] > 0
        row["objective_amount"] = row["objective_amount"].quantize(Decimal("0.01"))
        row["objective_ratio_over_sales"] = (
            ((row["objective_amount"] * Decimal("100.00")) / row["sales_total"]).quantize(Decimal("0.01"))
            if row["has_objective"] and row["sales_total"] > 0
            else Decimal("0.00")
        )
        if row["has_objective"]:
            row["deviation_amount"] = (row["objective_scope_expense"] - row["objective_amount"]).quantize(
                Decimal("0.01")
            )
            scope_ratio = (
                ((row["objective_scope_expense"] * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
                if sales_total > 0
                else Decimal("0.00")
            )
            row["deviation_ratio_over_sales"] = (scope_ratio - row["objective_ratio_over_sales"]).quantize(
                Decimal("0.01")
            )
        else:
            row["deviation_amount"] = None
            row["deviation_ratio_over_sales"] = None
        row["objective_covers_all_children"] = (
            row["objective_children_count"] == row["children_count"] and row["children_count"] > 0
        )
        sources = row.pop("objective_sources")
        row["objective_scope_label"] = " / ".join(sorted(sources)) if sources else ""
    return display


def build_economic_period_snapshot(*, date_from: date, date_to: date, sucursal=None, empresa_ids=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    from cashops.models import Caja, CanalIngreso, MovimientoCaja, RubroOperativo
    from cashops.services import get_income_channel_map

    period_from = _first_day_of_month(date_from)
    period_to = _first_day_of_month(date_to)
    period_end_exclusive = _first_day_of_next_month(period_to)
    month_starts = _month_starts_between(period_from, period_to)
    _all_income_codes = list(get_income_channel_map().keys())
    _excluded_income_codes = set(
        CanalIngreso.objects.filter(activo=True, excluir_de_totales=True).values_list("codigo", flat=True)
    )
    _included_income_codes = [code for code in _all_income_codes if code not in _excluded_income_codes]
    _digital_codes = [c for c in _included_income_codes if c != MovimientoCaja.Tipo.INGRESO_EFECTIVO]
    sale_query = Q(sentido=MovimientoCaja.Sentido.INGRESO) & (
        Q(tipo__in=_digital_codes) | Q(
        tipo=MovimientoCaja.Tipo.INGRESO_EFECTIVO,
        rubro_operativo__isnull=False,
        )
    )
    sales = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
        estado=MovimientoCaja.Estado.REGISTRADO,
    ).filter(sale_query)
    expenses = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
        tipo=MovimientoCaja.Tipo.GASTO,
        rubro_operativo__isnull=False,
        estado=MovimientoCaja.Estado.REGISTRADO,
    )
    sales = sales.exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
    expenses = expenses.exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
    if sucursal is not None:
        sales = sales.filter(caja__sucursal=sucursal)
        expenses = expenses.filter(caja__sucursal=sucursal)
    elif empresa_ids is not None:
        sales = sales.filter(caja__sucursal__empresa_id__in=empresa_ids)
        expenses = expenses.filter(caja__sucursal__empresa_id__in=empresa_ids)

    sales_rows = list(
        sales.annotate(
            period_month=TruncMonth("caja__fecha_operativa", output_field=DateField())
        )
        .values("rubro_operativo", "period_month")
        .annotate(total=Sum("monto"))
    )
    sales_total = Decimal("0.00")
    sales_by_rubro = {}
    sales_by_rubro_month = {}
    for row in sales_rows:
        rubro_id = row["rubro_operativo"]
        period_month = row["period_month"]
        total = row["total"] or Decimal("0.00")
        sales_total += total
        sales_by_rubro[rubro_id] = sales_by_rubro.get(rubro_id, Decimal("0.00")) + total
        sales_by_rubro_month[(rubro_id, period_month)] = total

    cash_expense_total = expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    cash_expense_by_rubro = {
        row["rubro_operativo"]: row["total"] or Decimal("0.00")
        for row in expenses.values("rubro_operativo").annotate(total=Sum("monto"))
    }

    central_treasury_expenses = _mapped_central_treasury_expenses(
        MovimientoCajaCentral.objects.filter(
            periodo_pago__gte=period_from,
            periodo_pago__lt=period_end_exclusive,
            rubro_operativo__isnull=False,
            sucursal_gasto__isnull=False,
        )
    )
    bank_treasury_expenses = _mapped_bank_treasury_expenses(
        MovimientoBancario.objects.filter(
            periodo_pago__gte=period_from,
            periodo_pago__lt=period_end_exclusive,
        )
    )
    if sucursal is not None:
        central_treasury_expenses = central_treasury_expenses.filter(sucursal_gasto=sucursal)
        bank_treasury_expenses = bank_treasury_expenses.filter(sucursal_gasto=sucursal)
    elif empresa_ids is not None:
        central_treasury_expenses = central_treasury_expenses.filter(sucursal_gasto__empresa_id__in=empresa_ids)
        bank_treasury_expenses = bank_treasury_expenses.filter(sucursal_gasto__empresa_id__in=empresa_ids)

    treasury_expense_total = (
        (central_treasury_expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00"))
        + (bank_treasury_expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00"))
    )
    treasury_expense_by_rubro = {}
    for row in central_treasury_expenses.values("rubro_operativo").annotate(total=Sum("monto")):
        rubro_id = row["rubro_operativo"]
        treasury_expense_by_rubro[rubro_id] = treasury_expense_by_rubro.get(rubro_id, Decimal("0.00")) + (
            row["total"] or Decimal("0.00")
        )
    for row in bank_treasury_expenses.values("rubro_operativo").annotate(total=Sum("monto")):
        rubro_id = row["rubro_operativo"]
        treasury_expense_by_rubro[rubro_id] = treasury_expense_by_rubro.get(rubro_id, Decimal("0.00")) + (
            row["total"] or Decimal("0.00")
        )

    pending_central_treasury_expenses = _pending_central_treasury_expenses(
        MovimientoCajaCentral.objects.filter(
            fecha__gte=date_from,
            fecha__lte=date_to,
        )
    )
    pending_bank_treasury_expenses = _pending_bank_treasury_expenses(
        MovimientoBancario.objects.filter(
            fecha__gte=date_from,
            fecha__lte=date_to,
        )
    )
    if sucursal is not None:
        pending_central_treasury_expenses = pending_central_treasury_expenses.filter(
            Q(sucursal_gasto=sucursal) | Q(sucursal_gasto__isnull=True)
        )
        pending_bank_treasury_expenses = pending_bank_treasury_expenses.filter(
            Q(sucursal_gasto=sucursal) | Q(sucursal_gasto__isnull=True)
        )
        if sucursal.empresa_id:
            # Los pendientes sin sucursal imputada solo pueden venir de cuentas
            # de la misma empresa; sin este corte, un debito historico de otra
            # empresa apareceria como gasto sin imputar en todas las vistas.
            pending_bank_treasury_expenses = pending_bank_treasury_expenses.filter(
                bank_account_empresa_scope_query([sucursal.empresa_id], prefix="cuenta_bancaria__")
            )
    elif empresa_ids is not None:
        if not empresa_ids:
            pending_central_treasury_expenses = pending_central_treasury_expenses.none()
            pending_bank_treasury_expenses = pending_bank_treasury_expenses.none()
        else:
            pending_central_treasury_expenses = pending_central_treasury_expenses.filter(
                Q(sucursal_gasto__empresa_id__in=empresa_ids) | Q(sucursal_gasto__isnull=True)
            )
            pending_bank_treasury_expenses = pending_bank_treasury_expenses.filter(
                Q(sucursal_gasto__empresa_id__in=empresa_ids) | Q(sucursal_gasto__isnull=True)
            ).filter(
                bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
            )
    treasury_unmapped_expenses_total = (
        (pending_central_treasury_expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00"))
        + (pending_bank_treasury_expenses.aggregate(total=Sum("monto"))["total"] or Decimal("0.00"))
    )
    treasury_unmapped_expenses_count = pending_central_treasury_expenses.count() + pending_bank_treasury_expenses.count()

    period_payables = CuentaPorPagar.objects.exclude(
        estado=CuentaPorPagar.Estado.ANULADA
    ).filter(
        periodo_referencia__gte=period_from,
        periodo_referencia__lt=period_end_exclusive,
    )
    if sucursal is not None:
        period_payables = period_payables.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        if not empresa_ids:
            period_payables = period_payables.none()
        else:
            period_payables = period_payables.filter(
                Q(sucursal__empresa_id__in=empresa_ids)
                | Q(sucursal__isnull=True)
            )

    mapped_period_payables = period_payables.filter(categoria__rubro_operativo__isnull=False)
    debt_period_total = mapped_period_payables.aggregate(total=Sum("importe_total"))["total"] or Decimal("0.00")
    debt_pending_total = mapped_period_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00")
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

    rubro_ids = (
        set(sales_by_rubro.keys())
        | set(cash_expense_by_rubro.keys())
        | set(treasury_expense_by_rubro.keys())
        | set(debt_by_rubro.keys())
    )
    rubros = {
        rubro.pk: rubro
        for rubro in RubroOperativo.objects.select_related("grupo").filter(pk__in=rubro_ids)
    }
    objective_lookup = _resolve_economic_targets(
        period_from=period_from,
        period_to=period_to,
        sucursal=sucursal,
    )
    items = []
    objective_total = Decimal("0.00")
    objective_scope_real_total = Decimal("0.00")
    objective_scope_sales_total = Decimal("0.00")
    deviation_total = Decimal("0.00")
    rubros_without_objective = 0
    for rubro_id in sorted(rubro_ids, key=lambda current: (rubros.get(current).nombre.lower() if rubros.get(current) else "")):
        rubro = rubros.get(rubro_id)
        debt_item = debt_by_rubro.get(rubro_id, {})
        expense_cash = cash_expense_by_rubro.get(rubro_id, Decimal("0.00"))
        expense_treasury = treasury_expense_by_rubro.get(rubro_id, Decimal("0.00"))
        expense_debt = debt_item.get("debt_total", Decimal("0.00"))
        total_expense = expense_cash + expense_treasury + expense_debt
        sales_total_rubro = sales_by_rubro.get(rubro_id, Decimal("0.00"))
        expense_ratio = (
            ((total_expense * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
            if sales_total > 0
            else Decimal("0.00")
        )
        objective_amount = Decimal("0.00")
        objective_sources = set()
        objective_months = 0
        for month_start in month_starts:
            objective = objective_lookup.get((rubro_id, month_start))
            month_sales = sales_by_rubro_month.get((rubro_id, month_start), Decimal("0.00"))
            if objective is None or month_sales <= 0:
                continue
            objective_amount += (month_sales * objective.porcentaje_objetivo) / Decimal("100.00")
            objective_months += 1
            objective_sources.add("Sucursal" if objective.sucursal_id else "Global")
        objective_amount = objective_amount.quantize(Decimal("0.01"))
        has_objective = objective_months > 0
        objective_ratio = (
            ((objective_amount * Decimal("100.00")) / sales_total_rubro).quantize(Decimal("0.01"))
            if has_objective and sales_total_rubro > 0
            else Decimal("0.00")
        )
        deviation_amount = None
        deviation_ratio = None
        if has_objective:
            deviation_amount = (total_expense - objective_amount).quantize(Decimal("0.01"))
            deviation_ratio = (expense_ratio - objective_ratio).quantize(Decimal("0.01"))
            objective_total += objective_amount
            objective_scope_real_total += total_expense
            objective_scope_sales_total += sales_total_rubro
            deviation_total += deviation_amount
        elif rubro is not None and (sales_total_rubro > 0 or total_expense > 0):
            rubros_without_objective += 1
        items.append(
            {
                "rubro": rubro,
                "rubro_nombre": rubro.nombre if rubro is not None else "Sin rubro",
                "sales_total": sales_total_rubro,
                "cash_expense_total": expense_cash,
                "treasury_expense_total": expense_treasury,
                "debt_total": expense_debt,
                "debt_pending": debt_item.get("debt_pending", Decimal("0.00")),
                "payables_count": debt_item.get("count", 0),
                "total_expense": total_expense,
                "expense_ratio_over_sales": expense_ratio,
                "has_objective": has_objective,
                "objective_amount": objective_amount,
                "objective_ratio_over_sales": objective_ratio,
                "deviation_amount": deviation_amount,
                "deviation_ratio_over_sales": deviation_ratio,
                "objective_months": objective_months,
                "objective_scope_label": " / ".join(sorted(objective_sources)) if objective_sources else "",
            }
        )
    items.sort(key=lambda item: (-item["total_expense"], item["rubro_nombre"].lower()))
    # `rubro_items` es la lista plana por rubro: de ahi salen los totales de la
    # cabecera y el desglose de un grupo. `items` es lo que se muestra, con los
    # rubros agrupados colapsados en una sola fila.
    rubro_items = items
    items = _collapse_economic_items_by_group(rubro_items, sales_total=sales_total)
    items.sort(key=lambda item: (-item["total_expense"], item["rubro_nombre"].lower()))

    economic_result = sales_total - cash_expense_total - treasury_expense_total - debt_period_total
    margin_pct = (
        ((economic_result * Decimal("100.00")) / sales_total).quantize(Decimal("0.01"))
        if sales_total > 0
        else Decimal("0.00")
    )
    objective_total = objective_total.quantize(Decimal("0.01"))
    objective_scope_real_total = objective_scope_real_total.quantize(Decimal("0.01"))
    objective_scope_sales_total = objective_scope_sales_total.quantize(Decimal("0.01"))
    deviation_total = deviation_total.quantize(Decimal("0.01"))
    objective_scope_ratio = (
        ((objective_total * Decimal("100.00")) / objective_scope_sales_total).quantize(Decimal("0.01"))
        if objective_scope_sales_total > 0
        else Decimal("0.00")
    )
    real_scope_ratio = (
        ((objective_scope_real_total * Decimal("100.00")) / objective_scope_sales_total).quantize(Decimal("0.01"))
        if objective_scope_sales_total > 0
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
        "treasury_expense_total": treasury_expense_total,
        "debt_period_total": debt_period_total,
        "debt_pending_total": debt_pending_total,
        "economic_result": economic_result,
        "margin_pct": margin_pct,
        "items": items,
        "rubro_items": rubro_items,
        "objective_total": objective_total,
        "objective_scope_real_total": objective_scope_real_total,
        "objective_scope_sales_total": objective_scope_sales_total,
        "objective_scope_ratio": objective_scope_ratio,
        "real_scope_ratio": real_scope_ratio,
        "deviation_total": deviation_total,
        # Se cuenta sobre la lista plana: la cabecera habla de rubros con
        # objetivo, no de filas mostradas.
        "objective_items_count": sum(1 for item in rubro_items if item["has_objective"]),
        "rubros_without_objective": rubros_without_objective,
        "branch_objectives_enabled": sucursal is not None,
        "unmapped_payables_total": unmapped_payables_total,
        "unmapped_payables_pending": unmapped_payables_pending,
        "unmapped_payables_count": unmapped_payables_count,
        "treasury_unmapped_expenses_total": treasury_unmapped_expenses_total,
        "treasury_unmapped_expenses_count": treasury_unmapped_expenses_count,
    }


def build_economic_rubro_detail(*, rubro_id: int, date_from: date, date_to: date, sucursal=None, empresa_ids=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    from cashops.models import Caja, MovimientoCaja, RubroOperativo

    period_from = _first_day_of_month(date_from)
    period_to = _first_day_of_month(date_to)
    period_end_exclusive = _first_day_of_next_month(period_to)
    rubro = RubroOperativo.objects.get(pk=rubro_id)

    cash_expenses = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
        tipo=MovimientoCaja.Tipo.GASTO,
        rubro_operativo=rubro,
        estado=MovimientoCaja.Estado.REGISTRADO,
    ).select_related("caja", "caja__sucursal")
    cash_expenses = cash_expenses.exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
    if sucursal is not None:
        cash_expenses = cash_expenses.filter(caja__sucursal=sucursal)
    elif empresa_ids is not None:
        cash_expenses = cash_expenses.filter(caja__sucursal__empresa_id__in=empresa_ids)

    central_treasury_expenses = _mapped_central_treasury_expenses(
        MovimientoCajaCentral.objects.filter(
            periodo_pago__gte=period_from,
            periodo_pago__lt=period_end_exclusive,
            rubro_operativo=rubro,
            sucursal_gasto__isnull=False,
        )
    ).select_related("sucursal_gasto")
    bank_treasury_expenses = _mapped_bank_treasury_expenses(
        MovimientoBancario.objects.filter(
            periodo_pago__gte=period_from,
            periodo_pago__lt=period_end_exclusive,
            rubro_operativo=rubro,
        )
    ).select_related("sucursal_gasto", "cuenta_bancaria")
    if sucursal is not None:
        central_treasury_expenses = central_treasury_expenses.filter(sucursal_gasto=sucursal)
        bank_treasury_expenses = bank_treasury_expenses.filter(sucursal_gasto=sucursal)
    elif empresa_ids is not None:
        central_treasury_expenses = central_treasury_expenses.filter(sucursal_gasto__empresa_id__in=empresa_ids)
        bank_treasury_expenses = bank_treasury_expenses.filter(sucursal_gasto__empresa_id__in=empresa_ids)

    payables = CuentaPorPagar.objects.exclude(
        estado=CuentaPorPagar.Estado.ANULADA
    ).filter(
        periodo_referencia__gte=period_from,
        periodo_referencia__lt=period_end_exclusive,
        categoria__rubro_operativo=rubro,
    ).select_related("proveedor", "categoria", "sucursal")
    if sucursal is not None:
        payables = payables.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        if not empresa_ids:
            payables = payables.none()
        else:
            payables = payables.filter(
                Q(sucursal__empresa_id__in=empresa_ids)
                | Q(sucursal__isnull=True)
            )

    items = []
    cash_total = Decimal("0.00")
    for movement in cash_expenses.order_by("caja__fecha_operativa", "id"):
        cash_total += movement.monto
        items.append(
            {
                "date": movement.caja.fecha_operativa,
                "origin": "Egreso de caja",
                "reference": movement.observacion or movement.categoria or f"Movimiento #{movement.pk}",
                "sucursal": movement.caja.sucursal,
                "status": "Registrado",
                "amount": movement.monto,
                "source": "cash_expense",
            }
        )

    treasury_total = Decimal("0.00")
    for movement in central_treasury_expenses.order_by("periodo_pago", "fecha", "id"):
        treasury_total += movement.monto
        items.append(
            {
                "date": movement.fecha,
                "origin": "Egreso tesoreria caja central",
                "reference": movement.concepto or f"Movimiento caja central #{movement.pk}",
                "sucursal": movement.sucursal_gasto,
                "status": "Imputado",
                "amount": movement.monto,
                "source": "central_treasury_expense",
            }
        )
    for movement in bank_treasury_expenses.order_by("periodo_pago", "fecha", "id"):
        treasury_total += movement.monto
        reference_bits = [movement.concepto]
        if movement.cuenta_bancaria_id:
            reference_bits.append(movement.cuenta_bancaria.nombre)
        items.append(
            {
                "date": movement.fecha,
                "origin": "Egreso tesoreria banco",
                "reference": " - ".join(bit for bit in reference_bits if bit) or f"Movimiento bancario #{movement.pk}",
                "sucursal": movement.sucursal_gasto,
                "status": "Imputado",
                "amount": movement.monto,
                "source": "bank_treasury_expense",
            }
        )

    debt_total = Decimal("0.00")
    debt_pending_total = Decimal("0.00")
    for payable in payables.order_by("periodo_referencia", "fecha_vencimiento", "id"):
        debt_total += payable.importe_total
        debt_pending_total += payable.saldo_pendiente
        reference_bits = [payable.proveedor.razon_social, payable.concepto]
        if payable.referencia_comprobante:
            reference_bits.append(payable.referencia_comprobante)
        items.append(
            {
                "date": payable.periodo_referencia,
                "origin": "Deuda del periodo",
                "reference": " - ".join(reference_bits),
                "sucursal": payable.sucursal,
                "status": payable.urgency_label if payable.estado_visible == "VENCIDA" else payable.get_estado_display(),
                "amount": payable.importe_total,
                "pending_amount": payable.saldo_pendiente,
                "source": "period_payable",
            }
        )

    items.sort(key=lambda item: (item["date"], item["origin"], item["reference"]))
    total = cash_total + treasury_total + debt_total
    return {
        "rubro": rubro,
        "date_from": date_from,
        "date_to": date_to,
        "period_from": period_from,
        "period_to": period_to,
        "sucursal": sucursal,
        "items": items,
        "cash_expense_total": cash_total,
        "treasury_expense_total": treasury_total,
        "debt_total": debt_total,
        "debt_pending_total": debt_pending_total,
        "total": total,
        "items_count": len(items),
    }


def build_financial_period_snapshot(*, date_from: date, date_to: date, sucursal=None, empresa_ids=None) -> dict:
    if date_to < date_from:
        raise ValidationError({"fecha_hasta": "La fecha hasta no puede ser anterior a la fecha desde."})

    from cashops.models import Caja, MovimientoCaja

    cash_movements = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
        impacta_saldo_caja=True,
        estado=MovimientoCaja.Estado.REGISTRADO,
    ).exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    ).exclude(tipo=MovimientoCaja.Tipo.APERTURA)
    if sucursal is not None:
        cash_movements = cash_movements.filter(caja__sucursal=sucursal)
    elif empresa_ids is not None:
        cash_movements = cash_movements.filter(caja__sucursal__empresa_id__in=empresa_ids)

    cash_totals = cash_movements.aggregate(
        ingresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.INGRESO)),
        egresos=Sum("monto", filter=Q(sentido=MovimientoCaja.Sentido.EGRESO)),
    )
    cash_income = cash_totals["ingresos"] or Decimal("0.00")
    cash_expense = cash_totals["egresos"] or Decimal("0.00")

    bank_movements = MovimientoBancario.objects.filter(
        estado=MovimientoBancario.Estado.REGISTRADO,
        fecha__gte=date_from,
        fecha__lte=date_to,
    )
    if sucursal is not None:
        bank_movements = bank_movements.filter(
            Q(tipo=MovimientoBancario.Tipo.DEBITO, sucursal_gasto=sucursal)
            | Q(
                tipo=MovimientoBancario.Tipo.DEBITO,
                sucursal_gasto__isnull=True,
                cuenta_bancaria__sucursal=sucursal,
            )
        )
    elif empresa_ids is not None:
        bank_movements = bank_movements.filter(_bank_movement_empresa_scope_query(empresa_ids))

    bank_totals = bank_movements.aggregate(
        creditos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.CREDITO)),
        debitos=Sum("monto", filter=Q(tipo=MovimientoBancario.Tipo.DEBITO)),
    )
    bank_credits = bank_totals["creditos"] or Decimal("0.00")
    bank_debits = bank_totals["debitos"] or Decimal("0.00")

    bank_accounts = CuentaBancaria.objects.filter(activa=True)
    if sucursal is not None:
        bank_accounts = bank_accounts.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        bank_accounts = bank_accounts.filter(_bank_account_empresa_scope_query(empresa_ids))

    bank_balances = []
    total_bank_balance = Decimal("0.00")
    for account in bank_accounts.order_by("banco", "nombre"):
        balance_data = _bank_balance_until(account, date_to)
        total_bank_balance += balance_data["balance"]
        bank_balances.append({"account": account, **balance_data})

    pending_payables = CuentaPorPagar.objects.filter(
        estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL]
    )
    if sucursal is not None:
        pending_payables = pending_payables.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        if not empresa_ids:
            pending_payables = pending_payables.none()
        else:
            # Las deudas legacy sin sucursal siguen siendo deuda viva: se
            # incluyen bajo contexto de empresa igual que en la lectura
            # economica, para no sobreestimar la cobertura de deuda en banco.
            pending_payables = pending_payables.filter(
                Q(sucursal__empresa_id__in=empresa_ids) | Q(sucursal__isnull=True)
            )

    reference_date = date_to
    red_threshold = reference_date + timedelta(days=4)
    yellow_threshold = reference_date + timedelta(days=8)
    red_payables = pending_payables.filter(fecha_vencimiento__lt=red_threshold)
    yellow_payables = pending_payables.filter(fecha_vencimiento__gte=red_threshold, fecha_vencimiento__lt=yellow_threshold)
    green_payables = pending_payables.filter(fecha_vencimiento__gte=yellow_threshold)
    all_pending_payables = pending_payables.select_related("proveedor", "categoria", "categoria__rubro_operativo")

    accreditation_empresa_ids = empresa_ids
    if not accreditation_empresa_ids and sucursal is not None and sucursal.empresa_id:
        accreditation_empresa_ids = [sucursal.empresa_id]

    digital_sales = MovimientoCaja.objects.filter(
        caja__fecha_operativa__gte=date_from,
        caja__fecha_operativa__lte=date_to,
        tipo=MovimientoCaja.Tipo.VENTA_TARJETA,
        estado=MovimientoCaja.Estado.REGISTRADO,
    ).exclude(caja__estado=Caja.Estado.ANULADA).exclude(
        caja__validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES
    )
    if accreditation_empresa_ids:
        digital_sales = digital_sales.filter(caja__sucursal__empresa_id__in=accreditation_empresa_ids)
    digital_sales_total = digital_sales.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    bank_accreditation_movements = MovimientoBancario.objects.filter(
        estado=MovimientoBancario.Estado.REGISTRADO,
        tipo=MovimientoBancario.Tipo.CREDITO,
        clase=MovimientoBancario.Clase.ACREDITACION,
    ).filter(_bank_accreditation_movement_scope_query(date_from=date_from, date_to=date_to))
    if accreditation_empresa_ids:
        bank_accreditation_movements = bank_accreditation_movements.filter(
            _bank_movement_empresa_scope_query(accreditation_empresa_ids)
        )

    bank_accreditation_movements = bank_accreditation_movements.distinct()
    accredited_net = bank_accreditation_movements.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    linked_accreditations = AcreditacionTarjeta.objects.filter(
        movimiento_bancario__in=bank_accreditation_movements
    )
    accreditation_discounts = (
        DescuentoAcreditacion.objects.filter(acreditacion__in=linked_accreditations).aggregate(total=Sum("monto"))["total"]
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
    elif empresa_ids is not None:
        recent_batches = recent_batches.filter(
            bank_account_empresa_scope_query(empresa_ids, prefix="cuenta_bancaria__")
        )
    recent_batches = recent_batches.select_related("cuenta_bancaria").order_by("-fecha_lote", "-id")[:5]

    recent_payments = PagoTesoreria.objects.filter(
        estado=PagoTesoreria.Estado.REGISTRADO,
        fecha_pago__gte=date_from,
        fecha_pago__lte=date_to,
    )
    if sucursal is not None:
        recent_payments = recent_payments.filter(cuenta_por_pagar__sucursal=sucursal)
    elif empresa_ids is not None:
        recent_payments = recent_payments.filter(cuenta_por_pagar__sucursal__empresa_id__in=empresa_ids)
    recent_payments = recent_payments.select_related("cuenta_por_pagar__proveedor", "cuenta_bancaria").order_by(
        "-fecha_pago", "-id"
    )[:10]

    central_cash_total = _central_cash_balance_until(
        reference_date=reference_date,
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    central_cash_period_movements = scope_central_cash_movements(
        MovimientoCajaCentral.objects.filter(fecha__gte=date_from, fecha__lte=date_to),
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    central_cash_period_totals = central_cash_period_movements.aggregate(
        ingresos=Sum("monto", filter=Q(tipo__in=CENTRAL_CASH_IN_TYPES)),
        egresos=Sum("monto", filter=Q(tipo__in=CENTRAL_CASH_OUT_TYPES)),
        egresos_admin=Sum("monto", filter=Q(tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN)),
    )
    central_cash_income_period = central_cash_period_totals["ingresos"] or Decimal("0.00")
    central_cash_expense_period = central_cash_period_totals["egresos"] or Decimal("0.00")
    central_cash_admin_expense_period = central_cash_period_totals["egresos_admin"] or Decimal("0.00")

    pending_debt_total = pending_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00")

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
        "show_bank_credit_cards": sucursal is None,
        "central_cash_income_period": central_cash_income_period,
        "central_cash_expense_period": central_cash_expense_period,
        "central_cash_admin_expense_period": central_cash_admin_expense_period,
        "central_cash_other_out_period": central_cash_expense_period - central_cash_admin_expense_period,
        "central_cash_net_period": central_cash_income_period - central_cash_expense_period,
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
        "pending_total": pending_debt_total,
        # US-10.14: cobertura de la deuda pendiente con la disponibilidad real
        # en banco a la fecha de corte. Solo banco (saldo inicial + movimientos
        # reales); no mezcla caja fuerte central ni acreditacion pendiente.
        "debt_vs_bank_difference": total_bank_balance - pending_debt_total,
        "debt_vs_bank_covered": total_bank_balance >= pending_debt_total,
        "red_count": red_payables.count(),
        "red_total": red_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "yellow_count": yellow_payables.count(),
        "yellow_total": yellow_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "green_count": green_payables.count(),
        "green_total": green_payables.aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "all_pending_payables": all_pending_payables,
        "recent_payments": recent_payments,
        "recent_movements": recent_movements,
        "recent_batches": recent_batches,
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
        # Las anuladas se excluyen de los DOS totales: contarlas en el total y no
        # en el pendiente daba un historial inflado e incoherente consigo mismo.
        "historical_total": payables.exclude(estado=CuentaPorPagar.Estado.ANULADA).aggregate(total=Sum("importe_total"))["total"] or Decimal("0.00"),
        "historical_pending": payables.exclude(estado=CuentaPorPagar.Estado.ANULADA).aggregate(total=Sum("saldo_pendiente"))["total"] or Decimal("0.00"),
        "historical_paid": payments.filter(estado=PagoTesoreria.Estado.REGISTRADO).aggregate(total=Sum("monto"))["total"] or Decimal("0.00"),
    }


# --- Flujo de Disponibilidades (EP-05) ---

def get_boveda(empresa) -> CajaCentral:
    """La boveda de efectivo de una empresa; la crea si la empresa es nueva.

    Antes esto era un get_or_create por NOMBRE ("Efectivo Central"), sin empresa
    ni sucursal, y convivia con otro resolvedor en cashops que creaba una caja
    por sucursal al vuelo. Entre los dos dejaron 7 cajas en produccion: los
    egresos salian de una y los ingresos entraban en otras seis.

    Este get_or_create es distinto y si es seguro: la clave es la empresa, que es
    lo que de verdad identifica a una boveda, y el UniqueConstraint de
    `unique_active_boveda_por_empresa` impide que existan dos. Se crea por
    demanda para que dar de alta una empresa nueva no requiera una migracion.
    Solo lo llaman caminos de escritura: ninguna pantalla crea una boveda con un GET.
    """
    from cashops.models import Empresa

    if empresa is None:
        raise ValidationError({"empresa": "Hace falta la empresa para resolver la boveda de efectivo."})
    empresa_id = getattr(empresa, "pk", empresa)
    boveda = CajaCentral.objects.filter(empresa_id=empresa_id, activo=True).order_by("pk").first()
    if boveda is not None:
        return boveda
    empresa_obj = Empresa.objects.filter(pk=empresa_id).first()
    if empresa_obj is None:
        raise ValidationError({"empresa": "La empresa no existe."})
    return CajaCentral.objects.create(
        empresa_id=empresa_id,
        sucursal=None,
        nombre=f"Boveda {empresa_obj.nombre}"[:120],
        activo=True,
    )


def register_central_cash_movement(
    *,
    empresa,
    tipo: MovimientoCajaCentral.Tipo,
    monto: Decimal,
    concepto: str,
    fecha=None,
    pago_tesoreria: PagoTesoreria = None,
    movimiento_bancario: MovimientoBancario = None,
    observaciones: str = "",
    sucursal_origen=None,
    token_alta=None,
    actor=None,
) -> MovimientoCajaCentral:
    _require_actor(actor)
    existing = _existing_by_creation_token(MovimientoCajaCentral.objects, token_alta)
    if existing is not None:
        return existing
    caja = get_boveda(empresa)
    movement = MovimientoCajaCentral(
        caja_central=caja,
        fecha=fecha or timezone.localdate(),
        tipo=tipo,
        monto=monto,
        concepto=concepto,
        pago_tesoreria=pago_tesoreria,
        movimiento_bancario=movimiento_bancario,
        observaciones=observaciones,
        sucursal_origen=sucursal_origen,
        token_alta=token_alta,
        creado_por=actor,
    )
    return _guardar_alta_idempotente(movement, MovimientoCajaCentral.objects, token_alta)


def register_carga_inicial_caja_central(
    *,
    empresa,
    fecha,
    monto: Decimal,
    motivo: str,
    observaciones: str = "",
    token_alta=None,
    actor=None,
) -> MovimientoCajaCentral:
    _require_actor(actor)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValidationError({"motivo": "El motivo es obligatorio para la carga inicial de caja fuerte."})
    if monto <= 0:
        raise ValidationError({"monto": "El importe debe ser mayor que cero."})
    return register_central_cash_movement(
        empresa=empresa,
        tipo=MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO,
        monto=monto,
        concepto=f"Carga inicial: {motivo}",
        fecha=fecha,
        observaciones=observaciones,
        token_alta=token_alta,
        actor=actor,
    )


def register_egreso_tesoreria(
    *,
    empresa,
    fuente: str,
    fecha,
    monto: Decimal,
    concepto: str,
    cuenta_bancaria=None,
    observaciones: str = "",
    rubro=None,
    sucursal=None,
    periodo=None,
    token_alta=None,
    actor=None,
) -> MovimientoCajaCentral | MovimientoBancario:
    _require_actor(actor)
    # El egreso puede terminar en la boveda O en el banco segun la fuente: el
    # reenvio se busca en los dos lados.
    existing = _existing_by_creation_token(
        MovimientoCajaCentral.objects, token_alta
    ) or _existing_by_creation_token(MovimientoBancario.objects, token_alta)
    if existing is not None:
        return existing
    concepto = (concepto or "").strip()
    if not concepto:
        raise ValidationError({"concepto": "El concepto es obligatorio para el egreso administrativo."})
    if monto <= 0:
        raise ValidationError({"monto": "El importe debe ser mayor que cero."})
    imputation_errors = {}
    if rubro is None:
        imputation_errors["rubro"] = "El rubro es obligatorio para el egreso administrativo."
    if sucursal is None:
        imputation_errors["sucursal"] = "La sucursal es obligatoria para el egreso administrativo."
    if periodo is None:
        imputation_errors["periodo"] = "El periodo es obligatorio para el egreso administrativo."
    if imputation_errors:
        raise ValidationError(imputation_errors)
    # El egreso no puede cruzar de empresa: ni imputarse a una sucursal ajena ni
    # salir de una cuenta bancaria ajena. El form acota los querysets, pero la
    # regla vive aca porque el form no es el unico camino.
    empresa_id = getattr(empresa, "pk", empresa)
    if sucursal.empresa_id and sucursal.empresa_id != empresa_id:
        raise ValidationError({"sucursal": "La sucursal no pertenece a la empresa del egreso."})
    if (
        cuenta_bancaria is not None
        and cuenta_bancaria.empresa_id
        and cuenta_bancaria.empresa_id != empresa_id
    ):
        raise ValidationError({"cuenta_bancaria": "La cuenta bancaria no pertenece a la empresa del egreso."})
    periodo = _first_day_of_month(periodo)

    if fuente == "BANCO":
        if cuenta_bancaria is None:
            raise ValidationError({"cuenta_bancaria": "La cuenta bancaria es obligatoria para egresos bancarios."})
        movement = MovimientoBancario(
            cuenta_bancaria=cuenta_bancaria,
            tipo=MovimientoBancario.Tipo.DEBITO,
            clase=MovimientoBancario.Clase.OTRO_EGRESO,
            origen=MovimientoBancario.Origen.EGRESO_TESORERIA,
            fecha=fecha,
            monto=monto,
            concepto=concepto,
            observaciones=observaciones,
            rubro_operativo=rubro,
            sucursal_gasto=sucursal,
            periodo_pago=periodo,
            token_alta=token_alta,
            creado_por=actor,
        )
        return _guardar_alta_idempotente(movement, MovimientoBancario.objects, token_alta)

    caja = get_boveda(empresa)
    movement = MovimientoCajaCentral(
        caja_central=caja,
        fecha=fecha or timezone.localdate(),
        tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
        monto=monto,
        concepto=concepto,
        observaciones=observaciones,
        rubro_operativo=rubro,
        sucursal_gasto=sucursal,
        periodo_pago=periodo,
        token_alta=token_alta,
        creado_por=actor,
    )
    return _guardar_alta_idempotente(movement, MovimientoCajaCentral.objects, token_alta)


def build_disponibilidades_snapshot(year: int, month: int, sucursal=None, empresa_ids=None) -> dict:
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

    # 1. Saldo inicial: sale del cierre anterior, acotado al MISMO alcance que el
    # flujo del periodo. Antes el filtro ignoraba empresa_ids y el sumatorio
    # global ni aplicaba su propio filtro, asi que filtrar por una empresa daba
    # el flujo de esa empresa con el saldo inicial de las dos.
    closing_filter = Q(mes__lt=first_day)
    if sucursal:
        closing_filter &= Q(sucursal=sucursal)
    elif empresa_ids is not None:
        closing_filter &= Q(empresa_id__in=empresa_ids)

    closings_prev = CierreMensualTesoreria.objects.filter(closing_filter).order_by("-mes")

    saldo_inicial_efectivo = Decimal("0.00")
    saldos_iniciales_bancarios = {}  # Dict {str(id): combined_saldo}

    if sucursal:
        cp = closings_prev.first()
        if cp:
            saldo_inicial_efectivo = cp.saldo_final_efectivo
            saldos_iniciales_bancarios = cp.saldos_bancarios_json
    else:
        # Se suman los cierres del ultimo mes disponible DENTRO del alcance: con
        # una fila por empresa, el consolidado es la suma de las dos.
        last_closing_month = closings_prev.values_list("mes", flat=True).first()
        if last_closing_month:
            relevant_closings = closings_prev.filter(mes=last_closing_month)
            for c in relevant_closings:
                saldo_inicial_efectivo += c.saldo_final_efectivo
                for acc_id, balance in c.saldos_bancarios_json.items():
                    saldos_iniciales_bancarios[acc_id] = str(Decimal(saldos_iniciales_bancarios.get(acc_id, "0.00")) + Decimal(balance))

    # 2. Cash Flow in Period
    movements_cash = scope_central_cash_movements(
        MovimientoCajaCentral.objects.filter(fecha__range=(first_day, last_day)),
        sucursal=sucursal,
        empresa_ids=empresa_ids,
    )
    
    cash_in = movements_cash.filter(tipo__in=CENTRAL_CASH_IN_TYPES).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    
    cash_out = movements_cash.filter(tipo__in=CENTRAL_CASH_OUT_TYPES).aggregate(total=Sum("monto"))["total"] or Decimal("0.00")
    
    saldo_final_efectivo = saldo_inicial_efectivo + cash_in - cash_out

    # 3. Bank Flow in Period
    bank_accounts = CuentaBancaria.objects.filter(activa=True)
    if sucursal:
        bank_accounts = bank_accounts.filter(sucursal=sucursal)
    elif empresa_ids is not None:
        bank_accounts = bank_accounts.filter(_bank_account_empresa_scope_query(empresa_ids))
        
    accounts_info = []
    total_bancos_final = Decimal("0.00")

    for acc in bank_accounts:
        initial = Decimal(saldos_iniciales_bancarios.get(str(acc.id), "0.00"))
        
        m_period = MovimientoBancario.objects.filter(
            cuenta_bancaria=acc,
            estado=MovimientoBancario.Estado.REGISTRADO,
            fecha__range=(first_day, last_day),
        )
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
def close_treasury_month(
    year: int, month: int, *, empresa, actor=None
) -> CierreMensualTesoreria:
    """Cierra el mes de UNA empresa.

    Antes era un cierre global: con dos empresas, ninguna podia cerrar hasta que
    la otra tuviera todas sus cajas validadas, y el saldo inicial del mes
    siguiente mezclaba las dos. La administradora pidio que cada empresa cierre
    por separado.
    """
    _require_actor(actor)
    if empresa is None:
        raise ValidationError({"empresa": "Hace falta la empresa para cerrar el mes."})
    empresa_id = getattr(empresa, "pk", empresa)
    # Mutex contra las operaciones de cashops que sacan plata del mes que se
    # esta congelando (revertir una validacion, eliminar una caja validada):
    # ambos lados toman el lock de la Empresa. Sin esto, en Postgres el chequeo
    # de cajas pendientes y el snapshot podian correr con el estado viejo de
    # una reversion concurrente y el mes congelaba plata recien anulada.
    from cashops.models import Empresa

    try:
        Empresa.objects.select_for_update().get(pk=empresa_id)
    except Empresa.DoesNotExist:
        raise ValidationError({"empresa": "La empresa indicada no existe."})
    snapshot = build_disponibilidades_snapshot(year, month, empresa_ids=[empresa_id])

    first_day = snapshot["first_day"]
    if CierreMensualTesoreria.objects.filter(
        mes=first_day, empresa_id=empresa_id, cerrado=True
    ).exists():
        raise ValidationError("Esta empresa ya tiene cerrado este mes.")

    # EP-13: el efectivo de una caja pendiente de validacion todavia no llego
    # a la caja central; cerrar el mes asi congelaria un saldo incompleto que
    # despues no se puede reconciliar.
    from cashops.models import Caja

    # Solo las cajas de ESTA empresa: una caja sin validar de la otra empresa no
    # tiene por que impedirle cerrar el mes a esta.
    boxes_in_month = Caja.objects.filter(
        fecha_operativa__gte=first_day,
        fecha_operativa__lt=_first_day_of_next_month(first_day),
        sucursal__empresa_id=empresa_id,
    )

    # Un mes cerrado es una FOTO congelada: su saldo final pasa a ser el saldo
    # inicial del mes siguiente y no se recalcula. Una caja todavia ABIERTA va
    # a empujar efectivo cuando cierre, asi que dejar cerrar el mes con cajas
    # abiertas congela un saldo que despues cambia. Ojo: una caja ABIERTA nace
    # con validacion_estado=NO_REQUERIDA (el estado de validacion se define al
    # cerrar), por eso NO la detecta el chequeo de pendientes de validacion.
    if boxes_in_month.filter(estado=Caja.Estado.ABIERTA).exists():
        raise ValidationError(
            "No se puede cerrar el mes: hay cajas del periodo todavia abiertas. "
            "Cerra esas cajas antes de cerrar el mes."
        )

    pending_boxes = boxes_in_month.filter(
        validacion_estado__in=Caja.VALIDACION_BLOQUEA_TOTALES,
    )
    if pending_boxes.exists():
        raise ValidationError(
            "No se puede cerrar el mes: hay cajas del periodo pendientes de validacion de efectivo. "
            "Valida o rechaza esas cajas antes de cerrar."
        )

    closing, created = CierreMensualTesoreria.objects.get_or_create(
        mes=first_day, empresa_id=empresa_id
    )
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
