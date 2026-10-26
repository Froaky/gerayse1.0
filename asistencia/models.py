import base64
import re
import secrets
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

# [0-9] y no \d: \d acepta digitos Unicode (arabigos, etc.) que burlarian la unicidad.
DNI_PATRON = re.compile(r"[0-9]{6,9}")

PREFIJO_CARNET = "GA1"
CODIGO_CARNET_PATRON = re.compile(r"GA1[A-Z2-7]{24}")


def nuevo_codigo_carnet() -> str:
    """Lo que va en el QR del carnet (EP-14): 120 bits al azar en base32, con un
    prefijo para reconocer un carnet de Gerayse. Nunca datos personales. Solo
    mayusculas y digitos 2-7: el QR sale chico y un lector USB lo tipea igual
    con teclado en espanol o en ingles."""
    return PREFIJO_CARNET + base64.b32encode(secrets.token_bytes(15)).decode()


def normalizar_dni(valor) -> str:
    """El DNI como solo digitos: saca puntos, espacios y guiones ("30.123.456").
    NFKC pasa los digitos de ancho completo a los comunes."""
    return re.sub(r"[\s.\-]", "", unicodedata.normalize("NFKC", str(valor or "")))


class Empleado(models.Model):
    """Persona que ficha. NO es un usuario de Gerayse (EP-14 D3): no entra al
    sistema, solo tiene un carnet que escanea el cajero."""

    nombre = models.CharField("Nombre y apellido", max_length=160)
    # Opcional desde el 26/10/2026: el listado del cliente llego sin DNI. Si se
    # carga, no se repite entre activos; si no, no se repite el nombre en la empresa.
    dni = models.CharField("DNI", max_length=12, blank=True)
    empresa = models.ForeignKey(
        "cashops.Empresa",
        on_delete=models.PROTECT,
        related_name="empleados_asistencia",
    )
    sucursal = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.PROTECT,
        related_name="empleados_asistencia",
        verbose_name="Local habitual",
    )
    # El codigo del carnet vigente. Anularlo (US-14.3) lo cambia por uno nuevo y
    # guarda el viejo en CarnetAnulado: el carnet perdido deja de servir.
    codigo_carnet = models.CharField(
        "Código del carnet",
        max_length=32,
        unique=True,
        default=nuevo_codigo_carnet,
        editable=False,
    )
    # Baja logica: el empleado deja de poder fichar pero no se borra, porque sus
    # marcas tienen que seguir saliendo en los Excel de los meses trabajados.
    activo = models.BooleanField(default=True)
    dado_de_baja_en = models.DateTimeField(null=True, blank=True)
    dado_de_baja_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="empleados_asistencia_dados_de_baja",
    )
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="empleados_asistencia_creados",
    )
    creado_en = models.DateTimeField(auto_now_add=True)
    actualizado_en = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre"]
        constraints = [
            # Una persona, un carnet: el DNI no se repite entre activos. Uno dado
            # de baja no bloquea, asi se puede volver a cargar a quien reingresa.
            # Los que no tienen DNI cargado no cuentan.
            models.UniqueConstraint(
                fields=["dni"],
                condition=Q(activo=True) & ~Q(dni=""),
                name="asistencia_dni_unico_entre_activos",
                violation_error_message="Ya hay un empleado activo con ese DNI.",
            ),
        ]
        indexes = [
            models.Index(fields=["empresa", "activo"]),
        ]

    def clean(self) -> None:
        # Sin caracteres de control: un NUL pasa en SQLite pero Postgres lo rechaza.
        self.nombre = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", self.nombre or "").split())
        self.dni = normalizar_dni(self.dni)
        errors = {}
        if not self.nombre:
            errors["nombre"] = "El nombre y apellido es obligatorio."
        if self.dni and not DNI_PATRON.fullmatch(self.dni):
            errors["dni"] = "El DNI tiene que tener solo números, entre 6 y 9."
        # Tambien rechaza un local sin empresa (legacy): el local habitual tiene
        # que ser de la empresa del empleado, sin excepcion.
        if self.empresa_id and self.sucursal_id and self.sucursal.empresa_id != self.empresa_id:
            errors["sucursal"] = "El local no pertenece a la empresa del empleado."
        # El mensaje no nombra al otro empleado: puede ser de una empresa que
        # quien carga no tiene permitida (AGENTS.md seccion 8).
        if "dni" not in errors and self.activo and self._dni_ocupado():
            errors["dni"] = "Ya hay un empleado activo con ese DNI."
        if not errors and self.activo and not self.dni and self._nombre_ocupado():
            errors["nombre"] = (
                "Ya hay un empleado activo con ese nombre en esta empresa. Si es otra persona, cargale el DNI."
            )
        if errors:
            raise ValidationError(errors)

    def _dni_ocupado(self) -> bool:
        dni = normalizar_dni(self.dni)
        if not dni:
            return False
        return Empleado.objects.filter(activo=True, dni=dni).exclude(pk=self.pk).exists()

    def _nombre_ocupado(self) -> bool:
        # Sin DNI, el nombre es lo unico que evita cargar dos veces a la misma
        # persona (por ejemplo, subir el mismo listado dos veces).
        return (
            Empleado.objects.filter(activo=True, empresa_id=self.empresa_id, nombre__iexact=self.nombre)
            .exclude(pk=self.pk)
            .exists()
        )

    def __str__(self) -> str:
        return f"{self.nombre} (DNI {self.dni})" if self.dni else self.nombre


class CarnetAnulado(models.Model):
    """Carnet que ya no sirve para fichar (perdido, roto). Guarda el codigo viejo
    para que el fichaje diga "carnet anulado" en vez de "desconocido", y quien,
    cuando y por que lo anulo. Nunca se borra."""

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="carnets_anulados")
    codigo = models.CharField(max_length=32, unique=True)
    motivo = models.CharField(max_length=200)
    anulado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="carnets_asistencia_anulados",
    )
    anulado_en = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-anulado_en", "-pk"]

    def __str__(self) -> str:
        return f"Carnet anulado de {self.empleado.nombre} ({self.anulado_en:%d/%m/%Y})"


class Marca(models.Model):
    """Una entrada o una salida de un empleado (EP-14). La hora de una marca
    escaneada es la del servidor al recibirla: nadie la edita."""

    class Tipo(models.TextChoices):
        ENTRADA = "ENTRADA", "Entrada"
        SALIDA = "SALIDA", "Salida"

    class Origen(models.TextChoices):
        ESCANEO = "ESCANEO", "Escaneo del carnet"
        MANUAL = "MANUAL", "Carga manual"

    empleado = models.ForeignKey(Empleado, on_delete=models.PROTECT, related_name="marcas")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    momento = models.DateTimeField()
    # El local donde se ficho: la sucursal base del cajero que escaneo. Un
    # empleado puede fichar en cualquier local.
    sucursal = models.ForeignKey("cashops.Sucursal", on_delete=models.PROTECT, related_name="marcas_asistencia")
    origen = models.CharField(max_length=10, choices=Origen.choices, default=Origen.ESCANEO)
    registrada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marcas_asistencia_registradas",
    )
    registrada_en = models.DateTimeField(auto_now_add=True)
    # Solo para las cargadas a mano (US-14.5): por que se agrego.
    motivo = models.CharField(max_length=200, blank=True, default="")
    # Anular no borra: la marca deja de contar (fichaje y Excel) pero queda con
    # quien, cuando y por que.
    anulada = models.BooleanField(default=False)
    anulada_en = models.DateTimeField(null=True, blank=True)
    anulada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marcas_asistencia_anuladas",
    )
    motivo_anulacion = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        ordering = ["-momento", "-pk"]
        indexes = [
            models.Index(fields=["empleado", "momento"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_tipo_display()} de {self.empleado.nombre} ({self.momento:%d/%m/%Y %H:%M})"
