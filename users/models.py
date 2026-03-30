from django.contrib.auth.models import AbstractUser
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

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

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
