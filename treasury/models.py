from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q, Sum
from django.db.models.functions import Lower
from django.utils import timezone


class Proveedor(models.Model):
    razon_social = models.CharField(max_length=160)
    identificador_fiscal = models.CharField(max_length=20, blank=True)
    contacto = models.CharField(max_length=120, blank=True)
    telefono = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    direccion = models.CharField(max_length=200, blank=True)
    sitio_web = models.URLField(blank=True, verbose_name="Sitio Web / Redes")
    alias_bancario = models.CharField(max_length=80, blank=True)
    cbu = models.CharField(max_length=22, blank=True)
    observaciones = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proveedores_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proveedores_actualizados",
    )

    class Meta:
        ordering = ["razon_social"]
        constraints = [
            models.UniqueConstraint(Lower("razon_social"), name="unique_supplier_name_ci"),
            models.UniqueConstraint(
                Lower("identificador_fiscal"),
                condition=~Q(identificador_fiscal=""),
                name="unique_supplier_tax_id_ci",
            ),
        ]
        indexes = [
            models.Index(fields=["activo", "razon_social"]),
        ]

    def clean(self) -> None:
        self.razon_social = (self.razon_social or "").strip()
        self.identificador_fiscal = (self.identificador_fiscal or "").strip()
        self.contacto = (self.contacto or "").strip()
        self.telefono = (self.telefono or "").strip()
        self.direccion = (self.direccion or "").strip()
        self.alias_bancario = (self.alias_bancario or "").strip()
        self.cbu = (self.cbu or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        if not self.razon_social:
            raise ValidationError({"razon_social": "La razon social es obligatoria."})

    def __str__(self) -> str:
        return self.razon_social


class CategoriaCuentaPagar(models.Model):
    nombre = models.CharField(max_length=120)
    rubro_operativo = models.ForeignKey(
        "cashops.RubroOperativo",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="categorias_cuenta_pagar",
    )
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="categorias_cuenta_pagar_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="categorias_cuenta_pagar_actualizadas",
    )

    class Meta:
        ordering = ["nombre"]
        constraints = [
            models.UniqueConstraint(Lower("nombre"), name="unique_payable_category_name_ci"),
        ]
        indexes = [
            models.Index(fields=["activo", "nombre"]),
        ]

    def clean(self) -> None:
        self.nombre = (self.nombre or "").strip()
        errors = {}
        if not self.nombre:
            errors["nombre"] = "El nombre de la categoria es obligatorio."
        if self.rubro_operativo_id and (
            not self.rubro_operativo.activo or self.rubro_operativo.es_sistema
        ):
            errors["rubro_operativo"] = "El rubro operativo debe estar activo y no puede ser de sistema."
        if errors:
            raise ValidationError(errors)

    @property
    def rubro_label(self) -> str:
        if self.rubro_operativo_id:
            return self.rubro_operativo.nombre
        return "Pendiente de migracion"

    def __str__(self) -> str:
        return self.nombre


class ObjetivoRubroEconomico(models.Model):
    rubro_operativo = models.ForeignKey(
        "cashops.RubroOperativo",
        on_delete=models.PROTECT,
        related_name="objetivos_economicos",
    )
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="objetivos_rubro_economico",
    )
    porcentaje_objetivo = models.DecimalField(max_digits=5, decimal_places=2)
    vigencia_desde = models.DateField()
    vigencia_hasta = models.DateField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="objetivos_rubro_economico_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="objetivos_rubro_economico_actualizados",
    )

    class Meta:
        ordering = ["rubro_operativo__nombre", "sucursal__nombre", "-vigencia_desde", "id"]
        constraints = [
            models.CheckConstraint(
                check=Q(porcentaje_objetivo__gt=0),
                name="economic_target_percentage_positive",
            ),
            models.CheckConstraint(
                check=Q(vigencia_hasta__isnull=True) | Q(vigencia_hasta__gte=F("vigencia_desde")),
                name="economic_target_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["rubro_operativo", "sucursal", "vigencia_desde"],
                name="unique_economic_target_start_per_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["activo", "vigencia_desde", "vigencia_hasta"]),
            models.Index(fields=["rubro_operativo", "sucursal", "vigencia_desde"]),
        ]

    @property
    def alcance_label(self) -> str:
        return self.sucursal.nombre if self.sucursal_id else "Global"

    def clean(self) -> None:
        errors = {}
        if self.rubro_operativo_id and (
            not self.rubro_operativo.activo or self.rubro_operativo.es_sistema
        ):
            errors["rubro_operativo"] = "El rubro operativo debe estar activo y no puede ser de sistema."
        if not self.vigencia_desde:
            errors["vigencia_desde"] = "La vigencia desde es obligatoria."
        if self.vigencia_desde:
            self.vigencia_desde = self.vigencia_desde.replace(day=1)
        if self.vigencia_hasta:
            self.vigencia_hasta = self.vigencia_hasta.replace(day=1)
            if self.vigencia_desde and self.vigencia_hasta < self.vigencia_desde:
                errors["vigencia_hasta"] = "La vigencia hasta no puede ser anterior a la vigencia desde."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return (
            f"{self.rubro_operativo.nombre} - {self.alcance_label} - "
            f"{self.porcentaje_objetivo}% desde {self.vigencia_desde:%m/%Y}"
        )


class CuentaBancaria(models.Model):
    class Tipo(models.TextChoices):
        CAJA_AHORRO = "CA", "Caja de ahorro"
        CUENTA_CORRIENTE = "CC", "Cuenta corriente"

    nombre = models.CharField(max_length=120)
    banco = models.CharField(max_length=120)
    tipo_cuenta = models.CharField(max_length=2, choices=Tipo.choices)
    numero_cuenta = models.CharField(max_length=40)
    alias = models.CharField(max_length=80, blank=True)
    cbu = models.CharField(max_length=22, blank=True)
    sucursal_bancaria = models.CharField(max_length=80, blank=True)
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_bancarias",
    )
    activa = models.BooleanField(default=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_bancarias_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_bancarias_actualizadas",
    )

    class Meta:
        ordering = ["banco", "nombre"]
        constraints = [
            models.UniqueConstraint(
                Lower("cbu"),
                condition=~Q(cbu=""),
                name="unique_bank_account_cbu_ci",
            ),
            models.UniqueConstraint(
                Lower("banco"),
                "numero_cuenta",
                name="unique_bank_account_number_per_bank",
            ),
        ]
        indexes = [
            models.Index(fields=["activa", "banco"]),
        ]

    def clean(self) -> None:
        self.nombre = (self.nombre or "").strip()
        self.banco = (self.banco or "").strip()
        self.numero_cuenta = (self.numero_cuenta or "").strip()
        self.alias = (self.alias or "").strip()
        self.cbu = (self.cbu or "").strip()
        self.sucursal_bancaria = (self.sucursal_bancaria or "").strip()
        if not self.nombre:
            raise ValidationError({"nombre": "El nombre interno es obligatorio."})
        if not self.banco:
            raise ValidationError({"banco": "El banco es obligatorio."})
        if not self.numero_cuenta:
            raise ValidationError({"numero_cuenta": "El numero de cuenta es obligatorio."})

    def __str__(self) -> str:
        return f"{self.nombre} - {self.banco}"


class CuentaPorPagar(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        PARCIAL = "PARCIAL", "Parcial"
        PAGADA = "PAGADA", "Pagada"
        ANULADA = "ANULADA", "Anulada"

    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cuentas_por_pagar",
    )
    proveedor = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        related_name="cuentas_por_pagar",
    )
    categoria = models.ForeignKey(
        CategoriaCuentaPagar,
        on_delete=models.PROTECT,
        related_name="cuentas_por_pagar",
    )
    concepto = models.CharField(max_length=160)
    referencia_comprobante = models.CharField(max_length=60, blank=True)
    fecha_emision = models.DateField()
    fecha_vencimiento = models.DateField()
    periodo_referencia = models.DateField()
    importe_total = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_pendiente = models.DecimalField(max_digits=14, decimal_places=2)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    observaciones = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_por_pagar_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_por_pagar_actualizadas",
    )
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cuentas_por_pagar_anuladas",
    )
    anulada_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["fecha_vencimiento", "proveedor__razon_social", "id"]
        constraints = [
            models.CheckConstraint(check=Q(importe_total__gt=0), name="payable_total_positive"),
            models.CheckConstraint(check=Q(saldo_pendiente__gte=0), name="payable_balance_non_negative"),
            models.CheckConstraint(
                check=Q(saldo_pendiente__lte=F("importe_total")),
                name="payable_balance_lte_total",
            ),
            models.CheckConstraint(
                check=Q(fecha_vencimiento__gte=F("fecha_emision")),
                name="payable_due_after_issue",
            ),
            models.UniqueConstraint(
                fields=["proveedor", "referencia_comprobante"],
                condition=~Q(referencia_comprobante=""),
                name="unique_payable_reference_by_supplier",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "fecha_vencimiento"]),
            models.Index(fields=["proveedor", "fecha_vencimiento"]),
            models.Index(fields=["categoria", "fecha_vencimiento"]),
            models.Index(fields=["periodo_referencia", "sucursal"]),
            models.Index(fields=["periodo_referencia", "categoria"]),
        ]

    @property
    def esta_vencida(self) -> bool:
        return (
            self.estado in {self.Estado.PENDIENTE, self.Estado.PARCIAL}
            and self.fecha_vencimiento < timezone.localdate()
        )

    @property
    def estado_visible(self) -> str:
        return "VENCIDA" if self.esta_vencida else self.estado

    @property
    def total_pagado(self) -> Decimal:
        return self.importe_total - (self.saldo_pendiente or Decimal("0.00"))

    def clean(self) -> None:
        self.concepto = (self.concepto or "").strip()
        self.referencia_comprobante = (self.referencia_comprobante or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        self.motivo_anulacion = (self.motivo_anulacion or "").strip()
        if not self.concepto:
            raise ValidationError({"concepto": "El concepto es obligatorio."})
        if not self.categoria_id:
            raise ValidationError({"categoria": "La categoria es obligatoria."})
        if not self.periodo_referencia:
            raise ValidationError({"periodo_referencia": "El periodo de referencia es obligatorio."})
        self.periodo_referencia = self.periodo_referencia.replace(day=1)
        if self.importe_total is not None and self.importe_total <= 0:
            raise ValidationError({"importe_total": "El importe total debe ser mayor que cero."})
        if self.saldo_pendiente is not None and self.saldo_pendiente < 0:
            raise ValidationError({"saldo_pendiente": "El saldo pendiente no puede ser negativo."})
        if (
            self.importe_total is not None
            and self.saldo_pendiente is not None
            and self.saldo_pendiente > self.importe_total
        ):
            raise ValidationError({"saldo_pendiente": "El saldo pendiente no puede superar el total."})
        if self.fecha_emision and self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_emision:
            raise ValidationError(
                {"fecha_vencimiento": "La fecha de vencimiento no puede ser anterior a la emision."}
            )
        errors = {}
        if self.estado == self.Estado.PENDIENTE and self.saldo_pendiente != self.importe_total:
            errors["estado"] = "Una cuenta pendiente debe conservar el saldo total."
        if self.estado == self.Estado.PARCIAL and (
            self.saldo_pendiente in {Decimal("0.00"), self.importe_total}
        ):
            errors["estado"] = "Una cuenta parcial debe tener un saldo intermedio."
        if self.estado == self.Estado.PAGADA and self.saldo_pendiente != Decimal("0.00"):
            errors["estado"] = "Una cuenta pagada debe quedar en saldo cero."
        if self.estado == self.Estado.ANULADA:
            if not self.motivo_anulacion:
                errors["motivo_anulacion"] = "El motivo es obligatorio para anular."
            if self.saldo_pendiente != Decimal("0.00"):
                errors["saldo_pendiente"] = "Una cuenta anulada debe quedar con saldo cero."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.proveedor} - {self.concepto}"


class CompromisoEspecial(models.Model):
    class Tipo(models.TextChoices):
        IMPUESTO = "IMPUESTO", "Impuesto u obligacion fiscal"
        PLAN_PAGO = "PLAN_PAGO", "Plan de pago / cuota"
        REQUERIMIENTO = "REQUERIMIENTO", "Requerimiento pendiente"
        ADELANTO = "ADELANTO", "Adelanto autorizado"
        EMBARGO = "EMBARGO", "Embargo o retencion judicial"
        SUELDO_EXTRAORDINARIO = "SUELDO_EXTRAORDINARIO", "Sueldo extraordinario"

    class Prioridad(models.TextChoices):
        BAJA = "BAJA", "Baja"
        MEDIA = "MEDIA", "Media"
        ALTA = "ALTA", "Alta"

    class Estado(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        APROBACION_PENDIENTE = "APROBACION_PENDIENTE", "Aprobacion pendiente"
        APROBADO = "APROBADO", "Aprobado"
        RECHAZADO = "RECHAZADO", "Rechazado"
        EJECUTADO = "EJECUTADO", "Ejecutado"
        CANCELADO = "CANCELADO", "Cancelado"

    cuenta_por_pagar = models.OneToOneField(
        CuentaPorPagar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compromiso_especial",
    )
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compromisos_especiales",
    )
    tipo = models.CharField(max_length=32, choices=Tipo.choices)
    concepto = models.CharField(max_length=160)
    organismo = models.CharField(max_length=120, blank=True)
    beneficiario = models.CharField(max_length=160, blank=True)
    expediente = models.CharField(max_length=80, blank=True)
    sustento_referencia = models.CharField(max_length=120)
    periodo_fiscal = models.DateField(null=True, blank=True)
    fecha_compromiso = models.DateField(default=timezone.localdate)
    vencimiento = models.DateField(null=True, blank=True)
    monto_estimado = models.DecimalField(max_digits=14, decimal_places=2)
    prioridad = models.CharField(max_length=10, choices=Prioridad.choices, default=Prioridad.MEDIA)
    estado = models.CharField(max_length=24, choices=Estado.choices, default=Estado.PENDIENTE)
    requiere_autorizacion = models.BooleanField(default=False)
    plan_nombre = models.CharField(max_length=120, blank=True)
    numero_cuota = models.PositiveIntegerField(null=True, blank=True)
    total_cuotas = models.PositiveIntegerField(null=True, blank=True)
    capital = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    interes_financiero = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    interes_resarcitorio = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    comentario_autorizacion = models.CharField(max_length=255, blank=True)
    autorizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromisos_especiales_autorizados",
    )
    autorizado_en = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromisos_especiales_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compromisos_especiales_actualizados",
    )

    class Meta:
        ordering = ["vencimiento", "fecha_compromiso", "id"]
        constraints = [
            models.CheckConstraint(check=Q(monto_estimado__gt=0), name="special_commitment_amount_positive"),
            models.CheckConstraint(check=Q(capital__gte=0), name="special_commitment_capital_non_negative"),
            models.CheckConstraint(
                check=Q(interes_financiero__gte=0),
                name="special_commitment_financial_interest_non_negative",
            ),
            models.CheckConstraint(
                check=Q(interes_resarcitorio__gte=0),
                name="special_commitment_resarcitory_interest_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["tipo", "estado", "vencimiento"]),
            models.Index(fields=["sucursal", "vencimiento"]),
            models.Index(fields=["plan_nombre", "numero_cuota"]),
        ]

    def clean(self) -> None:
        self.concepto = (self.concepto or "").strip()
        self.organismo = (self.organismo or "").strip()
        self.beneficiario = (self.beneficiario or "").strip()
        self.expediente = (self.expediente or "").strip()
        self.sustento_referencia = (self.sustento_referencia or "").strip()
        self.plan_nombre = (self.plan_nombre or "").strip()
        self.comentario_autorizacion = (self.comentario_autorizacion or "").strip()
        errors = {}
        if not self.concepto:
            errors["concepto"] = "El concepto es obligatorio."
        if not self.sustento_referencia:
            errors["sustento_referencia"] = "El comprobante, expediente o sustento es obligatorio."
        if self.monto_estimado is not None and self.monto_estimado <= 0:
            errors["monto_estimado"] = "El monto debe ser mayor que cero."

        if self.tipo == self.Tipo.IMPUESTO:
            if not self.organismo:
                errors["organismo"] = "El organismo es obligatorio para impuestos."
            if not self.periodo_fiscal:
                errors["periodo_fiscal"] = "El periodo fiscal es obligatorio."
            if not self.vencimiento:
                errors["vencimiento"] = "El vencimiento es obligatorio."
        if self.tipo == self.Tipo.PLAN_PAGO:
            if not self.plan_nombre:
                errors["plan_nombre"] = "El plan es obligatorio."
            if not self.numero_cuota:
                errors["numero_cuota"] = "El numero de cuota es obligatorio."
            if not self.total_cuotas:
                errors["total_cuotas"] = "La cantidad total de cuotas es obligatoria."
            if self.numero_cuota and self.total_cuotas and self.numero_cuota > self.total_cuotas:
                errors["numero_cuota"] = "La cuota no puede superar el total de cuotas."
            if not self.vencimiento:
                errors["vencimiento"] = "El vencimiento de la cuota es obligatorio."
            component_total = (self.capital or Decimal("0.00")) + (self.interes_financiero or Decimal("0.00")) + (
                self.interes_resarcitorio or Decimal("0.00")
            )
            if self.monto_estimado is not None and component_total != self.monto_estimado:
                errors["monto_estimado"] = "El monto debe coincidir con capital e intereses."
        if self.tipo == self.Tipo.EMBARGO:
            if not self.expediente:
                errors["expediente"] = "El expediente o referencia judicial es obligatorio."
            if not self.vencimiento:
                errors["vencimiento"] = "La fecha de vigencia o vencimiento es obligatoria."
        if self.tipo in {self.Tipo.ADELANTO, self.Tipo.SUELDO_EXTRAORDINARIO}:
            self.requiere_autorizacion = True
            if not self.beneficiario:
                errors["beneficiario"] = "El beneficiario es obligatorio."
        if self.requiere_autorizacion and self.estado == self.Estado.EJECUTADO and not self.autorizado_por_id:
            errors["estado"] = "No se puede ejecutar un compromiso sin autorizacion."
        if self.cuenta_por_pagar_id and self.monto_estimado != self.cuenta_por_pagar.importe_total:
            errors["cuenta_por_pagar"] = "El monto debe coincidir con la cuenta por pagar vinculada."
        if errors:
            raise ValidationError(errors)

    @property
    def aprobado(self) -> bool:
        return self.estado in {self.Estado.APROBADO, self.Estado.EJECUTADO}

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} - {self.concepto}"


class PagoTesoreria(models.Model):
    class MedioPago(models.TextChoices):
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        CHEQUE = "CHEQUE", "Cheque"
        ECHEQ = "ECHEQ", "ECHEQ"
        EFECTIVO = "EFECTIVO", "Efectivo"

    class Estado(models.TextChoices):
        REGISTRADO = "REGISTRADO", "Registrado"
        ANULADO = "ANULADO", "Anulado"

    class EstadoBancario(models.TextChoices):
        PENDIENTE = "PENDIENTE", "Pendiente"
        IMPACTADO = "IMPACTADO", "Impactado"
        RECHAZADO = "RECHAZADO", "Rechazado"
        ANULADO = "ANULADO", "Anulado"

    cuenta_por_pagar = models.ForeignKey(
        CuentaPorPagar,
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    cuenta_bancaria = models.ForeignKey(
        CuentaBancaria,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="pagos",
    )
    medio_pago = models.CharField(max_length=20, choices=MedioPago.choices)
    fecha_pago = models.DateField()
    fecha_diferida = models.DateField(null=True, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    referencia = models.CharField(max_length=80, blank=True)
    observaciones = models.CharField(max_length=255, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.REGISTRADO)
    estado_bancario = models.CharField(
        max_length=12,
        choices=EstadoBancario.choices,
        default=EstadoBancario.PENDIENTE,
    )
    observacion_bancaria = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagos_tesoreria_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagos_tesoreria_actualizados",
    )

    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagos_tesoreria_anulados",
    )
    anulado_en = models.DateTimeField(null=True, blank=True)
    motivo_anulacion = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-fecha_pago", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(monto__gt=0), name="treasury_payment_amount_positive"),
            models.UniqueConstraint(
                fields=["cuenta_bancaria", "medio_pago", "referencia"],
                condition=~Q(referencia=""),
                name="unique_payment_reference_per_bank_method",
            ),
        ]
        indexes = [
            models.Index(fields=["cuenta_por_pagar", "estado"]),
            models.Index(fields=["cuenta_bancaria", "fecha_pago"]),
            models.Index(fields=["fecha_pago", "medio_pago"]),
            models.Index(fields=["estado_bancario", "fecha_pago"]),
        ]

    DOMAIN_GUARD_ERROR = (
        "Los pagos de tesoreria deben registrarse o anularse desde los servicios de dominio."
    )

    def clean(self) -> None:
        self.referencia = (self.referencia or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        self.observacion_bancaria = (self.observacion_bancaria or "").strip()
        self.motivo_anulacion = (self.motivo_anulacion or "").strip()
        errors = {}
        if self.monto is not None and self.monto <= 0:
            errors["monto"] = "El monto debe ser mayor que cero."
        if self.medio_pago in {self.MedioPago.CHEQUE, self.MedioPago.ECHEQ} and not self.referencia:
            errors["referencia"] = "La referencia es obligatoria para cheque y ECHEQ."
        if self.medio_pago == self.MedioPago.TRANSFERENCIA and self.fecha_diferida:
            errors["fecha_diferida"] = "La transferencia no admite fecha diferida."
        if self.fecha_diferida and self.fecha_pago and self.fecha_diferida < self.fecha_pago:
            errors["fecha_diferida"] = "La fecha diferida no puede ser anterior a la fecha de pago."
        if self.estado == self.Estado.ANULADO and not self.motivo_anulacion:
            errors["motivo_anulacion"] = "El motivo es obligatorio para anular."
        if self.estado == self.Estado.ANULADO and self.estado_bancario != self.EstadoBancario.ANULADO:
            errors["estado_bancario"] = "Un pago anulado debe quedar con estado bancario anulado."
        if self.estado != self.Estado.ANULADO and self.estado_bancario == self.EstadoBancario.ANULADO:
            errors["estado_bancario"] = "Solo un pago anulado puede tener estado bancario anulado."
        if self.medio_pago != self.MedioPago.EFECTIVO and not self.cuenta_bancaria_id:
            errors["cuenta_bancaria"] = "La cuenta bancaria es obligatoria para este medio de pago."
        if (
            self.cuenta_bancaria_id
            and self.estado == self.Estado.REGISTRADO
            and not self.cuenta_bancaria.activa
        ):
            errors["cuenta_bancaria"] = "La cuenta bancaria esta inactiva."
        if self.cuenta_por_pagar_id and self.estado == self.Estado.REGISTRADO:
            if self.cuenta_por_pagar.estado == CuentaPorPagar.Estado.ANULADA:
                errors["cuenta_por_pagar"] = "La cuenta por pagar esta anulada."
            elif self.cuenta_por_pagar.estado == CuentaPorPagar.Estado.PAGADA:
                errors["cuenta_por_pagar"] = "La cuenta por pagar ya esta cancelada."
            elif self.monto is not None:
                total_registrado = (
                    PagoTesoreria.objects.filter(
                        cuenta_por_pagar_id=self.cuenta_por_pagar_id,
                        estado=self.Estado.REGISTRADO,
                    )
                    .exclude(pk=self.pk)
                    .aggregate(total=Sum("monto"))["total"]
                    or 0
                )
                saldo_disponible = self.cuenta_por_pagar.importe_total - total_registrado
                if self.monto > saldo_disponible:
                    errors["monto"] = "El pago no puede superar el saldo pendiente."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        skip_domain_guard = kwargs.pop("skip_domain_guard", False)
        self.full_clean()
        if not skip_domain_guard:
            raise ValidationError({"__all__": self.DOMAIN_GUARD_ERROR})
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.get_medio_pago_display()} {self.monto} - {self.cuenta_por_pagar_id}"


class LotePOS(models.Model):
    fecha_lote = models.DateField()
    terminal = models.CharField(max_length=80, blank=True)
    operador = models.CharField(max_length=80, blank=True)
    cuenta_bancaria = models.ForeignKey(
        CuentaBancaria,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lotes_pos",
    )
    total_lote = models.DecimalField(max_digits=14, decimal_places=2)
    observaciones = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lotes_pos_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lotes_pos_actualizados",
    )


    class Meta:
        ordering = ["-fecha_lote", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(total_lote__gt=0), name="pos_batch_total_positive"),
        ]
        indexes = [
            models.Index(fields=["fecha_lote", "operador"]),
            models.Index(fields=["terminal", "fecha_lote"]),
            models.Index(fields=["fecha_lote", "cuenta_bancaria"]),
        ]

    def clean(self) -> None:
        self.terminal = (self.terminal or "").strip()
        self.operador = (self.operador or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        errors = {}
        if self.total_lote is not None and self.total_lote <= 0:
            errors["total_lote"] = "El total del lote debe ser mayor que cero."
        if self.cuenta_bancaria_id and not self.cuenta_bancaria.activa:
            errors["cuenta_bancaria"] = "La cuenta bancaria seleccionada esta inactiva."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        base = self.operador or self.terminal or "Lote POS"
        return f"{base} - {self.fecha_lote}"


class MovimientoBancario(models.Model):
    class Tipo(models.TextChoices):
        DEBITO = "DEBITO", "Debito"
        CREDITO = "CREDITO", "Credito"

    class Clase(models.TextChoices):
        ACREDITACION = "ACREDITACION", "Ingreso por acreditacion"
        OTRO_INGRESO = "OTRO_INGRESO", "Otro ingreso"
        CHEQUE = "CHEQUE", "Egreso por cheque"
        ECHEQ = "ECHEQ", "Egreso por ECHEQ"
        IMPUESTO = "IMPUESTO", "Egreso por impuestos"
        COMISION_BANCARIA = "COMISION_BANCARIA", "Egreso por comision bancaria"
        RETIRO = "RETIRO", "Egreso por retiro"
        TRANSFERENCIA_TERCEROS = "TRANSFERENCIA_TERCEROS", "Egreso por transferencia a terceros"
        OTRO_EGRESO = "OTRO_EGRESO", "Otro egreso"

    class Origen(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        ACREDITACION_TARJETA = "ACREDITACION_TARJETA", "Acreditacion tarjeta"
        PAGO_TESORERIA = "PAGO_TESORERIA", "Pago tesoreria"

    cuenta_bancaria = models.ForeignKey(
        CuentaBancaria,
        on_delete=models.PROTECT,
        related_name="movimientos_bancarios",
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    clase = models.CharField(max_length=32, choices=Clase.choices, default=Clase.OTRO_INGRESO)
    origen = models.CharField(max_length=24, choices=Origen.choices, default=Origen.MANUAL)
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    concepto = models.CharField(max_length=160)
    referencia = models.CharField(max_length=80, blank=True)
    observaciones = models.CharField(max_length=255, blank=True)
    categoria = models.ForeignKey(
        CategoriaCuentaPagar,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_bancarios",
    )
    proveedor = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimientos_bancarios",
    )
    pago_tesoreria = models.OneToOneField(
        PagoTesoreria,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="movimiento_bancario",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_bancarios_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_bancarios_actualizados",
    )


    class Meta:
        ordering = ["-fecha", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(monto__gt=0), name="bank_movement_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["cuenta_bancaria", "fecha"]),
            models.Index(fields=["tipo", "fecha"]),
            models.Index(fields=["clase", "fecha"]),
            models.Index(fields=["origen", "fecha"]),
        ]

    def clean(self) -> None:
        self.concepto = (self.concepto or "").strip()
        self.referencia = (self.referencia or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        errors = {}
        if self.monto is not None and self.monto <= 0:
            errors["monto"] = "El monto debe ser mayor que cero."
        if not self.concepto:
            errors["concepto"] = "El concepto es obligatorio."
        credit_classes = {self.Clase.ACREDITACION, self.Clase.OTRO_INGRESO}
        debit_classes = {
            self.Clase.CHEQUE,
            self.Clase.ECHEQ,
            self.Clase.IMPUESTO,
            self.Clase.COMISION_BANCARIA,
            self.Clase.RETIRO,
            self.Clase.TRANSFERENCIA_TERCEROS,
            self.Clase.OTRO_EGRESO,
        }
        if self.tipo == self.Tipo.CREDITO and self.clase not in credit_classes:
            errors["clase"] = "La clase elegida no corresponde a un credito bancario."
        if self.tipo == self.Tipo.DEBITO and self.clase not in debit_classes:
            errors["clase"] = "La clase elegida no corresponde a un debito bancario."
        if self.clase in {
            self.Clase.CHEQUE,
            self.Clase.ECHEQ,
            self.Clase.IMPUESTO,
            self.Clase.COMISION_BANCARIA,
            self.Clase.TRANSFERENCIA_TERCEROS,
        } and not self.categoria_id:
            errors["categoria"] = "El rubro o categoria es obligatorio para este tipo de movimiento."
        if self.clase in {
            self.Clase.CHEQUE,
            self.Clase.ECHEQ,
            self.Clase.TRANSFERENCIA_TERCEROS,
        } and not self.proveedor_id:
            errors["proveedor"] = "El proveedor es obligatorio para este tipo de movimiento."
        if self.cuenta_bancaria_id and not self.cuenta_bancaria.activa:
            errors["cuenta_bancaria"] = "La cuenta bancaria esta inactiva."
        if self.pago_tesoreria_id:
            if self.tipo != self.Tipo.DEBITO:
                errors["tipo"] = "Un pago de tesoreria solo puede vincularse a un debito bancario."
            if self.origen != self.Origen.PAGO_TESORERIA:
                errors["origen"] = "El origen debe ser pago de tesoreria cuando existe un pago vinculado."
            expected_class = {
                PagoTesoreria.MedioPago.CHEQUE: self.Clase.CHEQUE,
                PagoTesoreria.MedioPago.ECHEQ: self.Clase.ECHEQ,
            }.get(self.pago_tesoreria.medio_pago, self.Clase.TRANSFERENCIA_TERCEROS)
            if self.clase != expected_class:
                errors["clase"] = "La clase no coincide con el medio de pago vinculado."
            if self.cuenta_bancaria_id and self.cuenta_bancaria_id != self.pago_tesoreria.cuenta_bancaria_id:
                errors["cuenta_bancaria"] = "El movimiento debe usar la misma cuenta bancaria del pago."
            if self.pago_tesoreria.estado != PagoTesoreria.Estado.REGISTRADO:
                errors["pago_tesoreria"] = "Solo podes vincular pagos registrados."
            payable = self.pago_tesoreria.cuenta_por_pagar
            if self.proveedor_id and self.proveedor_id != payable.proveedor_id:
                errors["proveedor"] = "El proveedor debe coincidir con la obligacion pagada."
            if self.categoria_id and self.categoria_id != payable.categoria_id:
                errors["categoria"] = "La categoria debe coincidir con la obligacion pagada."
        elif self.origen == self.Origen.PAGO_TESORERIA:
            errors["pago_tesoreria"] = "El origen pago de tesoreria requiere un pago vinculado."
        if self.origen == self.Origen.ACREDITACION_TARJETA and self.clase != self.Clase.ACREDITACION:
            errors["clase"] = "Las acreditaciones de tarjeta deben quedar tipificadas como acreditacion."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.monto} - {self.cuenta_bancaria}"


class AcreditacionTarjeta(models.Model):
    class ModoRegistro(models.TextChoices):
        DIARIA = "DIARIA", "Carga diaria"
        PERIODO = "PERIODO", "Carga agrupada por periodo"

    movimiento_bancario = models.OneToOneField(
        MovimientoBancario,
        on_delete=models.PROTECT,
        related_name="acreditacion_tarjeta",
    )
    modo_registro = models.CharField(max_length=10, choices=ModoRegistro.choices, default=ModoRegistro.DIARIA)
    canal = models.CharField(max_length=80)
    lote_pos = models.ForeignKey(
        LotePOS,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="acreditaciones",
    )
    periodo_desde = models.DateField(null=True, blank=True)
    periodo_hasta = models.DateField(null=True, blank=True)
    referencia_externa = models.CharField(max_length=80, blank=True)
    observaciones = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acreditaciones_tarjeta_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acreditaciones_tarjeta_actualizadas",
    )


    class Meta:
        ordering = ["-movimiento_bancario__fecha", "-id"]
        indexes = [
            models.Index(fields=["canal", "creado_en"]),
            models.Index(fields=["modo_registro", "periodo_desde", "periodo_hasta"]),
        ]

    @property
    def fecha_acreditacion(self):
        return self.movimiento_bancario.fecha

    @property
    def cuenta_bancaria(self):
        return self.movimiento_bancario.cuenta_bancaria

    @property
    def monto_acreditado(self):
        return self.movimiento_bancario.monto

    @property
    def operador_canal(self) -> str:
        return self.canal

    @property
    def referencia(self) -> str:
        return self.referencia_externa

    @property
    def total_descuentos(self) -> Decimal:
        return self.descuentos.aggregate(total=Sum("monto"))["total"] or Decimal("0.00")

    @property
    def monto_bruto_estimado(self) -> Decimal:
        return self.monto_acreditado + self.total_descuentos

    def clean(self) -> None:
        self.canal = (self.canal or "").strip()
        self.referencia_externa = (self.referencia_externa or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        self.modo_registro = self.modo_registro or self.ModoRegistro.DIARIA
        errors = {}
        if not self.canal:
            errors["canal"] = "El canal u operador es obligatorio."
        if self.modo_registro == self.ModoRegistro.PERIODO:
            if not self.periodo_desde:
                errors["periodo_desde"] = "La fecha desde es obligatoria para cargas agrupadas."
            if not self.periodo_hasta:
                errors["periodo_hasta"] = "La fecha hasta es obligatoria para cargas agrupadas."
            if self.periodo_desde and self.periodo_hasta and self.periodo_hasta < self.periodo_desde:
                errors["periodo_hasta"] = "La fecha hasta no puede ser anterior a la fecha desde."
        elif self.periodo_desde or self.periodo_hasta:
            errors["modo_registro"] = "Las fechas de periodo solo aplican a cargas agrupadas."
        if not self.lote_pos_id and not self.referencia_externa:
            errors["referencia_externa"] = "Informá un lote POS o una referencia de liquidacion."
        if self.movimiento_bancario_id:
            if self.movimiento_bancario.tipo != MovimientoBancario.Tipo.CREDITO:
                errors["movimiento_bancario"] = "La acreditacion debe vincularse a un credito bancario."
            if self.movimiento_bancario.origen != MovimientoBancario.Origen.ACREDITACION_TARJETA:
                errors["movimiento_bancario"] = "El movimiento debe estar marcado como acreditacion de tarjeta."
            if self.movimiento_bancario.clase != MovimientoBancario.Clase.ACREDITACION:
                errors["movimiento_bancario"] = "El movimiento debe quedar tipificado como acreditacion."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.canal} - {self.fecha_acreditacion}"


class DescuentoAcreditacion(models.Model):
    class Tipo(models.TextChoices):
        IIBB = "IIBB", "IIBB"
        COMISION = "COMISION", "Comision"
        OTRO = "OTRO", "Otro"

    acreditacion = models.ForeignKey(
        AcreditacionTarjeta,
        on_delete=models.PROTECT,
        related_name="descuentos",
    )
    tipo = models.CharField(max_length=16, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    descripcion = models.CharField(max_length=160)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="descuentos_acreditacion_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)
    actualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="descuentos_acreditacion_actualizados",
    )


    class Meta:
        ordering = ["acreditacion_id", "id"]
        constraints = [
            models.CheckConstraint(check=Q(monto__gt=0), name="accreditation_discount_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["tipo", "creado_en"]),
        ]

    def clean(self) -> None:
        self.descripcion = (self.descripcion or "").strip()
        if self.monto is not None and self.monto <= 0:
            raise ValidationError({"monto": "El descuento debe ser mayor que cero."})
        if not self.descripcion:
            raise ValidationError({"descripcion": "La descripcion del descuento es obligatoria."})

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.monto} - {self.acreditacion_id}"


LotePos = LotePOS
DescuentoBancario = DescuentoAcreditacion


# --- Flujo de Disponibilidades (EP-05) ---

class CajaCentral(models.Model):
    nombre = models.CharField(max_length=120, default="Efectivo Central")
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cajas_centrales",
    )
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Cajas Centrales"

    def __str__(self) -> str:
        return self.nombre

    @property
    def saldo_actual(self) -> Decimal:
        sums = self.movimientos.aggregate(
            ingresos=Sum("monto", filter=Q(tipo__in=[
                MovimientoCajaCentral.Tipo.INGRESO_CAJA,
                MovimientoCajaCentral.Tipo.APORTE,
                MovimientoCajaCentral.Tipo.RETIRO_BANCO,
                MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO
            ])),
            egresos=Sum("monto", filter=Q(tipo__in=[
                MovimientoCajaCentral.Tipo.EGRESO_PAGO,
                MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                MovimientoCajaCentral.Tipo.DEPOSITO_BANCO,
                MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO
            ]))
        )
        ingresos = sums["ingresos"] or Decimal("0.00")
        egresos = sums["egresos"] or Decimal("0.00")
        return ingresos - egresos


class MovimientoCajaCentral(models.Model):
    class Tipo(models.TextChoices):
        INGRESO_CAJA = "INGRESO_CAJA", "Ingreso desde Caja Operativa"
        APORTE = "APORTE", "Aporte de Socios/Capital"
        RETIRO_BANCO = "RETIRO_BANCO", "Retiro de Banco (Efectivo)"
        EGRESO_PAGO = "EGRESO_PAGO", "Egreso por Pago Administrativo"
        EGRESO_ADMIN = "EGRESO_ADMIN", "Egreso Administrativo de Tesoreria"
        DEPOSITO_BANCO = "DEPOSITO_BANCO", "Deposito en Banco"
        AJUSTE_POSITIVO = "AJUSTE_POSITIVO", "Ajuste de Saldo (+)"
        AJUSTE_NEGATIVO = "AJUSTE_NEGATIVO", "Ajuste de Saldo (-)"

    caja_central = models.ForeignKey(CajaCentral, on_delete=models.PROTECT, related_name="movimientos")
    fecha = models.DateField(default=timezone.localdate)
    tipo = models.CharField(max_length=40, choices=Tipo.choices)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    concepto = models.CharField(max_length=160)
    
    # Optional links
    pago_tesoreria = models.ForeignKey(
        PagoTesoreria, 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name="movimientos_caja_central"
    )
    movimiento_bancario = models.ForeignKey(
        MovimientoBancario, 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name="movimientos_caja_central"
    )
    
    observaciones = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_caja_central_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        constraints = [
            models.CheckConstraint(check=Q(monto__gt=0), name="central_cash_movement_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} - {self.fecha} - {self.monto}"


class CierreMensualTesoreria(models.Model):
    mes = models.DateField(help_text="Primer dia del mes que se cierra")
    saldo_inicial_efectivo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    saldo_final_efectivo = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cierres_mensuales_tesoreria",
    )
    
    # Snapshot of bank balances at EOF
    saldos_bancarios_json = models.JSONField(
        default=dict, 
        help_text="Dict {cuenta_id: saldo_final}"
    )
    
    cerrado = models.BooleanField(default=False)
    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cierres_mensuales_realizados",
    )
    cerrado_en = models.DateTimeField(null=True, blank=True)
    
    observaciones = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-mes"]
        constraints = [
            models.UniqueConstraint(fields=["mes"], name="unique_monthly_closing_per_month"),
        ]

    def __str__(self) -> str:
        return f"Cierre {self.mes:%m/%Y}"


class ArqueoDisponibilidades(models.Model):
    fecha = models.DateTimeField(default=timezone.now)
    caja_central = models.ForeignKey(CajaCentral, on_delete=models.PROTECT)
    
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="arqueos_disponibilidades",
    )
    
    saldo_sistema_efectivo = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_contado_efectivo = models.DecimalField(max_digits=14, decimal_places=2)
    
    # Store bank reconciliations in this arqueo too?
    # Simple summary for now
    observaciones = models.TextField(blank=True)
    
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="arqueos_realizados",
    )

    class Meta:
        ordering = ["-fecha"]

    @property
    def diferencia(self) -> Decimal:
        return self.saldo_contado_efectivo - self.saldo_sistema_efectivo

    def __str__(self) -> str:
        return f"Arqueo {self.fecha:%d/%m/%Y %H:%M}"
