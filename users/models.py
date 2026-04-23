from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class Role(models.Model):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "role"
        verbose_name_plural = "roles"

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    ADMIN_ROLE_CODES = {"ADMIN", "ADMINISTRADOR"}

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    dni = models.CharField(max_length=20, blank=True, verbose_name="DNI")
    legajo = models.CharField(max_length=20, blank=True, verbose_name="Nro Legajo")
    telefono = models.CharField(max_length=40, blank=True, verbose_name="Telefono")
    usuario_fijo = models.BooleanField(default=False, verbose_name="Usuario fijo")
    sucursal_base = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios_base",
    )


    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def clean(self) -> None:
        super().clean()
        if self.usuario_fijo and not self.sucursal_base_id:
            raise ValidationError({"sucursal_base": "La sucursal base es obligatoria para un usuario fijo."})

    @property
    def normalized_role_code(self) -> str:
        if not self.role_id or not self.role or not self.role.code:
            return ""
        return self.role.code.strip().upper()

    def is_cashops_admin(self) -> bool:
        return self.is_superuser or self.normalized_role_code in self.ADMIN_ROLE_CODES

    def __str__(self) -> str:
        if self.get_full_name():
            return self.get_full_name()
        return self.get_username()
