from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone


class Sucursal(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    codigo = models.CharField(max_length=20, unique=True)
    activa = models.BooleanField(default=True)
    creada_en = models.DateTimeField(auto_now_add=True)
    actualizada_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self) -> str:
        return f"{self.codigo} - {self.nombre}"


class Turno(models.Model):
    class Tipo(models.TextChoices):
        MANANA = "TM", "T.M."
        TARDE = "TT", "T.T."

    class Estado(models.TextChoices):
        ABIERTO = "ABIERTO", "Abierto"
        CERRADO = "CERRADO", "Cerrado"

    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="turnos")
    fecha_operativa = models.DateField()
    tipo = models.CharField(max_length=2, choices=Tipo.choices)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTO)
    observacion = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turnos_creados",
    )
    abierto_en = models.DateTimeField(auto_now_add=True)
    cerrado_en = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-fecha_operativa", "tipo", "sucursal__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["sucursal", "fecha_operativa", "tipo"],
                name="unique_turno_by_branch_date_type",
            ),
        ]
        indexes = [
            models.Index(fields=["sucursal", "fecha_operativa", "tipo"]),
            models.Index(fields=["estado"]),
        ]

    def clean(self) -> None:
        if self.cerrado_en and self.estado != self.Estado.CERRADO:
            raise ValidationError({"cerrado_en": "Solo un turno cerrado puede tener fecha de cierre."})
        if self.estado == self.Estado.ABIERTO and self.cerrado_en:
            raise ValidationError({"estado": "Un turno abierto no puede tener fecha de cierre."})

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.fecha_operativa} - {self.sucursal}"


class Caja(models.Model):
    class Estado(models.TextChoices):
        ABIERTA = "ABIERTA", "Abierta"
        CERRADA = "CERRADA", "Cerrada"

    sucursal = models.ForeignKey(Sucursal, on_delete=models.PROTECT, related_name="cajas")
    turno = models.ForeignKey(Turno, on_delete=models.PROTECT, related_name="cajas")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cajas",
    )
    monto_inicial = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.ABIERTA)
    abierta_en = models.DateTimeField(auto_now_add=True)
    cerrada_en = models.DateTimeField(null=True, blank=True)
    cerrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cajas_cerradas",
    )

    class Meta:
        ordering = ["-abierta_en", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["usuario", "turno", "sucursal"],
                condition=Q(estado="ABIERTA"),
                name="unique_open_box_by_user_turn_branch",
            ),
            models.CheckConstraint(
                check=Q(monto_inicial__gte=0),
                name="box_initial_amount_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["estado", "turno"]),
            models.Index(fields=["sucursal", "estado"]),
            models.Index(fields=["usuario", "estado"]),
        ]

    @property
    def saldo_esperado(self) -> Decimal:
        movimientos = self.movimientos.exclude(tipo=MovimientoCaja.Tipo.APERTURA)
        ingresos = movimientos.filter(sentido=MovimientoCaja.Sentido.INGRESO).aggregate(
            total=Sum("monto")
        )["total"] or Decimal("0.00")
        egresos = movimientos.filter(sentido=MovimientoCaja.Sentido.EGRESO).aggregate(
            total=Sum("monto")
        )["total"] or Decimal("0.00")
        return self.monto_inicial + ingresos - egresos

    def __str__(self) -> str:
        return f"Caja {self.id} - {self.usuario}"


class Transferencia(models.Model):
    class Tipo(models.TextChoices):
        ENTRE_CAJAS = "ENTRE_CAJAS", "Entre cajas"
        ENTRE_SUCURSALES = "ENTRE_SUCURSALES", "Entre sucursales"

    class Clase(models.TextChoices):
        DINERO = "DINERO", "Dinero"
        MERCADERIA = "MERCADERIA", "Mercaderia"

    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    clase = models.CharField(max_length=20, choices=Clase.choices)
    caja_origen = models.ForeignKey(
        Caja,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferencias_salida",
    )
    caja_destino = models.ForeignKey(
        Caja,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferencias_entrada",
    )
    sucursal_origen = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferencias_origen",
    )
    sucursal_destino = models.ForeignKey(
        Sucursal,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transferencias_destino",
    )
    monto = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    observacion = models.CharField(max_length=255, blank=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transferencias_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        indexes = [
            models.Index(fields=["tipo", "clase"]),
            models.Index(fields=["creado_en"]),
        ]

    def clean(self) -> None:
        errors = {}
        if self.caja_origen and self.caja_destino and self.caja_origen_id == self.caja_destino_id:
            errors["caja_destino"] = "El origen y el destino no pueden ser la misma caja."
        if self.sucursal_origen and self.sucursal_destino and self.sucursal_origen_id == self.sucursal_destino_id:
            errors["sucursal_destino"] = "El origen y el destino no pueden ser la misma sucursal."
        if self.clase == self.Clase.DINERO and (self.monto is None or self.monto <= 0):
            errors["monto"] = "El monto es obligatorio para transferencias de dinero."
        if self.clase == self.Clase.MERCADERIA and not self.observacion:
            errors["observacion"] = "La observacion es obligatoria para mercaderia."
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} - {self.get_clase_display()} #{self.id}"


class MovimientoCaja(models.Model):
    class Tipo(models.TextChoices):
        APERTURA = "APERTURA", "Apertura"
        GASTO = "GASTO", "Gasto"
        VENTA_TARJETA = "VENTA_TARJETA", "Venta tarjeta"
        TRANSFERENCIA_SALIDA = "TRANSFERENCIA_SALIDA", "Transferencia salida"
        TRANSFERENCIA_ENTRADA = "TRANSFERENCIA_ENTRADA", "Transferencia entrada"
        TRANSFERENCIA_SUCURSAL_SALIDA = "TRANSFERENCIA_SUCURSAL_SALIDA", "Transferencia sucursal salida"
        TRANSFERENCIA_SUCURSAL_ENTRADA = "TRANSFERENCIA_SUCURSAL_ENTRADA", "Transferencia sucursal entrada"
        AJUSTE_CIERRE = "AJUSTE_CIERRE", "Ajuste de cierre"

    class Sentido(models.TextChoices):
        INGRESO = "INGRESO", "Ingreso"
        EGRESO = "EGRESO", "Egreso"

    caja = models.ForeignKey(Caja, on_delete=models.PROTECT, related_name="movimientos")
    tipo = models.CharField(max_length=40, choices=Tipo.choices)
    sentido = models.CharField(max_length=10, choices=Sentido.choices)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    categoria = models.CharField(max_length=80, blank=True)
    observacion = models.CharField(max_length=255, blank=True)
    transferencia = models.ForeignKey(
        Transferencia,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movimientos_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]
        indexes = [
            models.Index(fields=["caja", "tipo"]),
            models.Index(fields=["caja", "creado_en"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(monto__gt=0), name="movement_amount_positive"),
        ]

    def clean(self) -> None:
        if self.monto is not None and self.monto <= 0:
            raise ValidationError({"monto": "El monto debe ser mayor que cero."})

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} {self.monto} - Caja {self.caja_id}"


class CierreCaja(models.Model):
    class Estado(models.TextChoices):
        AUTO = "AUTO", "Auto"
        JUSTIFICADO = "JUSTIFICADO", "Justificado"

    caja = models.OneToOneField(Caja, on_delete=models.PROTECT, related_name="cierre")
    saldo_esperado = models.DecimalField(max_digits=14, decimal_places=2)
    saldo_fisico = models.DecimalField(max_digits=14, decimal_places=2)
    diferencia = models.DecimalField(max_digits=14, decimal_places=2)
    estado = models.CharField(max_length=15, choices=Estado.choices)
    ajuste_movimiento = models.ForeignKey(
        MovimientoCaja,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cierres_ajustados",
    )
    cerrado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cierres_realizados",
    )
    cerrado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cerrado_en", "-id"]
        indexes = [
            models.Index(fields=["estado", "cerrado_en"]),
        ]

    def __str__(self) -> str:
        return f"Cierre caja {self.caja_id} - {self.estado}"


class Justificacion(models.Model):
    cierre = models.OneToOneField(CierreCaja, on_delete=models.CASCADE, related_name="justificacion")
    motivo = models.TextField()
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="justificaciones_creadas",
    )
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado_en", "-id"]

    def __str__(self) -> str:
        return f"Justificacion cierre {self.cierre_id}"
