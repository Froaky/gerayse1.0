from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Role, User


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("name",)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Business", {"fields": ("role", "usuario_fijo", "sucursal_base")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Business", {"fields": ("role", "usuario_fijo", "sucursal_base")}),
    )
    list_display = ("username", "get_full_name", "email", "role", "is_staff", "is_active")
    list_filter = BaseUserAdmin.list_filter + ("role", "usuario_fijo", "sucursal_base")
    search_fields = (
        "username",
        "first_name",
        "last_name",
        "email",
        "role__name",
        "role__code",
        "sucursal_base__nombre",
        "sucursal_base__codigo",
    )
    ordering = ("username",)
