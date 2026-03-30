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
        self.alias_bancario = (self.alias_bancario or "").strip()
        self.cbu = (self.cbu or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        if not self.razon_social:
            raise ValidationError({"razon_social": "La razon social es obligatoria."})

    def __str__(self) -> str:
        return self.razon_social


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

    proveedor = models.ForeignKey(
        Proveedor,
        on_delete=models.PROTECT,
        related_name="cuentas_por_pagar",
    )
    concepto = models.CharField(max_length=160)
    referencia_comprobante = models.CharField(max_length=60, blank=True)
    fecha_emision = models.DateField()
    fecha_vencimiento = models.DateField()
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

    def clean(self) -> None:
        self.concepto = (self.concepto or "").strip()
        self.referencia_comprobante = (self.referencia_comprobante or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        self.motivo_anulacion = (self.motivo_anulacion or "").strip()
        if not self.concepto:
            raise ValidationError({"concepto": "El concepto es obligatorio."})
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
        if self.estado == self.Estado.ANULADA and not self.motivo_anulacion:
            raise ValidationError({"motivo_anulacion": "El motivo es obligatorio para anular."})

    def __str__(self) -> str:
        return f"{self.proveedor} - {self.concepto}"


class PagoTesoreria(models.Model):
    class MedioPago(models.TextChoices):
        TRANSFERENCIA = "TRANSFERENCIA", "Transferencia"
        CHEQUE = "CHEQUE", "Cheque"
        ECHEQ = "ECHEQ", "ECHEQ"

    class Estado(models.TextChoices):
        REGISTRADO = "REGISTRADO", "Registrado"
        ANULADO = "ANULADO", "Anulado"

    cuenta_por_pagar = models.ForeignKey(
        CuentaPorPagar,
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    cuenta_bancaria = models.ForeignKey(
        CuentaBancaria,
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    medio_pago = models.CharField(max_length=20, choices=MedioPago.choices)
    fecha_pago = models.DateField()
    fecha_diferida = models.DateField(null=True, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    referencia = models.CharField(max_length=80, blank=True)
    observaciones = models.CharField(max_length=255, blank=True)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.REGISTRADO)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pagos_tesoreria_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
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
        ]

    DOMAIN_GUARD_ERROR = (
        "Los pagos de tesoreria deben registrarse o anularse desde los servicios de dominio."
    )

    def clean(self) -> None:
        self.referencia = (self.referencia or "").strip()
        self.observaciones = (self.observaciones or "").strip()
        self.motivo_anulacion = (self.motivo_anulacion or "").strip()
        errors = {}
        if self.monto is not None and self.monto <= 0:
            errors["monto"] = "El monto debe ser mayor que cero."
        if self.medio_pago in {self.MedioPago.CHEQUE, self.MedioPago.ECHEQ} and not self.referencia:
            errors["referencia"] = "La referencia es obligatoria para cheque y ECHEQ."
        if self.estado == self.Estado.ANULADO and not self.motivo_anulacion:
            errors["motivo_anulacion"] = "El motivo es obligatorio para anular."
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
