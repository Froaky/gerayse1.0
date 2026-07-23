from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class PermissionModule(models.TextChoices):
    CASHOPS = "cashops", "Caja operativa"
    CASHOPS_CLOSED_FIX = "cashops_closed_fix", "Corrección de cajas cerradas"
    # EP-13: permiso por accion. Habilita validar o rechazar el efectivo de
    # cajas pendientes; no otorga ningun otro acceso de caja.
    CASHOPS_VALIDATE = "cashops_validate", "Validación de efectivo"
    # Permiso por accion (asignable/removible): habilita cargar un gasto como
    # deuda sobre una caja YA CERRADA. No reabre la caja ni mueve efectivo.
    CASHOPS_DEBT_CLOSED = "cashops_debt_closed", "Cargar deuda en caja cerrada"
    # Permiso por accion (asignable/removible): habilita ELIMINAR (anular con
    # motivo y auditoria) movimientos de caja y gastos cargados como deuda,
    # tanto en cajas abiertas como cerradas. Nada se borra fisico: queda anulado.
    CASHOPS_MOV_DELETE = "cashops_mov_del", "Eliminar movimientos de caja"
    CONFIG = "config", "Configuración"
    TREASURY = "treasury", "Tesorería"
    USERS = "users", "Usuarios"


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

    def ensure_permission_rows(self) -> None:
        for module, _ in PermissionModule.choices:
            can_access = self.code.strip().upper() in User.ADMIN_ROLE_CODES or module == PermissionModule.CASHOPS
            RolePermission.objects.get_or_create(
                role=self,
                module=module,
                defaults={"can_read": can_access, "can_write": can_access},
            )


class User(AbstractUser):
    ADMIN_ROLE_CODES = {"ADMIN", "ADMINISTRADOR"}
    CAJERO_ROLE_CODE = "CAJERO"

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    dni = models.CharField(max_length=20, blank=True, verbose_name="DNI")
    telefono = models.CharField(max_length=40, blank=True, verbose_name="Telefono")
    must_change_password = models.BooleanField(
        default=False,
        verbose_name="Debe cambiar contraseña",
    )
    usuario_fijo = models.BooleanField(default=False, verbose_name="Usuario fijo")
    sucursal_base = models.ForeignKey(
        "cashops.Sucursal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios_base",
    )
    empresa_principal = models.ForeignKey(
        "cashops.Empresa",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usuarios_principales",
        verbose_name="Empresa principal",
    )
    empresas_permitidas = models.ManyToManyField(
        "cashops.Empresa",
        blank=True,
        related_name="usuarios_con_acceso",
        verbose_name="Empresas con acceso",
    )
    # Sucursales EXTRA (ademas de la base) donde el usuario puede imputar un
    # gasto como deuda. Vacio = solo su sucursal base. Lo asigna la admin por
    # usuario; no habilita operar caja en esas sucursales, solo cargar deuda.
    sucursales_deuda = models.ManyToManyField(
        "cashops.Sucursal",
        blank=True,
        related_name="usuarios_carga_deuda",
        verbose_name="Sucursales adicionales para cargar deuda",
    )

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def clean(self) -> None:
        super().clean()
        if self.usuario_fijo and not self.sucursal_base_id:
            raise ValidationError({"sucursal_base": "La sucursal base es obligatoria para un usuario fijo."})
        # EP-13: el alcance por sucursal del cajero depende de ser usuario
        # fijo; sin esto podria abrir cajas en cualquier sucursal.
        if self.normalized_role_code == self.CAJERO_ROLE_CODE and not self.usuario_fijo:
            raise ValidationError(
                {"usuario_fijo": "Un usuario con rol Cajero debe ser usuario fijo con sucursal base."}
            )

    def get_empresas_permitidas_ids(self) -> set[int]:
        return set(self.empresas_permitidas.values_list("pk", flat=True))

    @property
    def normalized_role_code(self) -> str:
        if not self.role_id or not self.role or not self.role.code:
            return ""
        return self.role.code.strip().upper()

    def _legacy_permission_values(self, module: str) -> tuple[bool, bool]:
        if self.is_superuser or self.normalized_role_code in self.ADMIN_ROLE_CODES:
            return True, True
        if module == PermissionModule.CASHOPS:
            return True, True
        return False, False

    def configured_permission_values(self, module: str) -> tuple[bool, bool, str]:
        override = self.permission_overrides.filter(module=module).first()
        if override:
            return override.can_read or override.can_write, override.can_write, "Personalizado"

        if self.role_id:
            role_permission = self.role.permissions.filter(module=module).first()
            if role_permission:
                return (
                    role_permission.can_read or role_permission.can_write,
                    role_permission.can_write,
                    f"Rol: {self.role.name}",
                )

        can_read, can_write = self._legacy_permission_values(module)
        return can_read, can_write, "Compatibilidad"

    def has_module_permission(self, module: str, action: str = "read") -> bool:
        if not self.is_active:
            return False
        if self.is_superuser:
            return True
        can_read, can_write, _ = self.configured_permission_values(module)
        if action == "write":
            return can_write
        return can_read or can_write

    def is_cashops_admin(self) -> bool:
        return self.has_module_permission(PermissionModule.CONFIG, "write")

    def can_read_cashops(self) -> bool:
        return self.has_module_permission(PermissionModule.CASHOPS, "read")

    def can_write_cashops(self) -> bool:
        return self.has_module_permission(PermissionModule.CASHOPS, "write")

    def can_validate_efectivo(self) -> bool:
        return self.has_module_permission(PermissionModule.CASHOPS_VALIDATE, "write")

    def can_load_debt_on_closed_box(self) -> bool:
        return self.has_module_permission(PermissionModule.CASHOPS_DEBT_CLOSED, "write")

    def can_delete_box_movement(self) -> bool:
        return self.has_module_permission(PermissionModule.CASHOPS_MOV_DELETE, "write")

    def sucursales_para_deuda(self):
        """Sucursales activas donde el usuario puede imputar una deuda: su
        sucursal base mas las adicionales asignadas. Se usa para las opciones
        del form y para validar en el servicio."""
        from cashops.models import Sucursal

        ids = set(self.sucursales_deuda.values_list("pk", flat=True))
        if self.sucursal_base_id:
            ids.add(self.sucursal_base_id)
        return Sucursal.objects.filter(pk__in=ids, activa=True).order_by("nombre")

    def can_read_config(self) -> bool:
        return self.has_module_permission(PermissionModule.CONFIG, "read")

    def can_write_config(self) -> bool:
        return self.has_module_permission(PermissionModule.CONFIG, "write")

    def can_read_treasury(self) -> bool:
        return self.has_module_permission(PermissionModule.TREASURY, "read")

    def can_write_treasury(self) -> bool:
        return self.has_module_permission(PermissionModule.TREASURY, "write")

    def can_read_users(self) -> bool:
        return self.has_module_permission(PermissionModule.USERS, "read")

    def can_write_users(self) -> bool:
        return self.has_module_permission(PermissionModule.USERS, "write")

    def __str__(self) -> str:
        if self.get_full_name():
            return self.get_full_name()
        return self.get_username()


class RolePermission(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="permissions")
    module = models.CharField(max_length=20, choices=PermissionModule.choices)
    can_read = models.BooleanField(default=False)
    can_write = models.BooleanField(default=False)

    class Meta:
        ordering = ["role__name", "module"]
        constraints = [
            models.UniqueConstraint(fields=["role", "module"], name="unique_role_permission_per_module"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.can_write:
            self.can_read = True

    def save(self, *args, **kwargs):
        if self.can_write:
            self.can_read = True
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.role} - {self.get_module_display()}"


class UserPermission(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="permission_overrides")
    module = models.CharField(max_length=20, choices=PermissionModule.choices)
    can_read = models.BooleanField(default=False)
    can_write = models.BooleanField(default=False)

    class Meta:
        ordering = ["user__username", "module"]
        constraints = [
            models.UniqueConstraint(fields=["user", "module"], name="unique_user_permission_per_module"),
        ]

    def clean(self) -> None:
        super().clean()
        if self.can_write:
            self.can_read = True

    def save(self, *args, **kwargs):
        if self.can_write:
            self.can_read = True
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.user} - {self.get_module_display()}"
