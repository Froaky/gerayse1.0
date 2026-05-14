from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Role, RolePermission, User, UserPermission


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    fields = ("module", "can_read", "can_write")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")
    ordering = ("name",)
    inlines = (RolePermissionInline,)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Business", {"fields": ("role", "must_change_password", "usuario_fijo", "sucursal_base")}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Business", {"fields": ("role", "must_change_password", "usuario_fijo", "sucursal_base")}),
    )
    list_display = ("username", "get_full_name", "email", "role", "must_change_password", "is_staff", "is_active")
    list_filter = BaseUserAdmin.list_filter + ("role", "must_change_password", "usuario_fijo", "sucursal_base")
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


@admin.register(UserPermission)
class UserPermissionAdmin(admin.ModelAdmin):
    list_display = ("user", "module", "can_read", "can_write")
    list_filter = ("module", "can_read", "can_write")
    search_fields = ("user__username", "user__first_name", "user__last_name")
