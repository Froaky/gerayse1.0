import re
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

# [0-9] y no \d: \d acepta digitos Unicode (arabigos, etc.) que burlarian la unicidad.
DNI_PATRON = re.compile(r"[0-9]{6,9}")


def normalizar_dni(valor) -> str:
    """El DNI como solo digitos: saca puntos, espacios y guiones ("30.123.456").
    NFKC pasa los digitos de ancho completo a los comunes."""
    return re.sub(r"[\s.\-]", "", unicodedata.normalize("NFKC", str(valor or "")))


class Empleado(models.Model):
    """Persona que ficha. NO es un usuario de Gerayse (EP-14 D3): no entra al
    sistema, solo tiene un carnet que escanea el cajero."""

    nombre = models.CharField("Nombre y apellido", max_length=160)
    dni = models.CharField("DNI", max_length=12)
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
            models.UniqueConstraint(
                fields=["dni"],
                condition=Q(activo=True),
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
        if not DNI_PATRON.fullmatch(self.dni):
            errors["dni"] = "El DNI tiene que tener solo números, entre 6 y 9."
        # Tambien rechaza un local sin empresa (legacy): el local habitual tiene
        # que ser de la empresa del empleado, sin excepcion.
        if self.empresa_id and self.sucursal_id and self.sucursal.empresa_id != self.empresa_id:
            errors["sucursal"] = "El local no pertenece a la empresa del empleado."
        # El mensaje no nombra al otro empleado: puede ser de una empresa que
        # quien carga no tiene permitida (AGENTS.md seccion 8).
        if "dni" not in errors and self.activo and self._dni_ocupado():
            errors["dni"] = "Ya hay un empleado activo con ese DNI."
        if errors:
            raise ValidationError(errors)

    def _dni_ocupado(self) -> bool:
        return Empleado.objects.filter(activo=True, dni=normalizar_dni(self.dni)).exclude(pk=self.pk).exists()

    def __str__(self) -> str:
        return f"{self.nombre} (DNI {self.dni})"
