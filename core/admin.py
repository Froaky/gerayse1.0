from django.contrib import admin
from django.shortcuts import redirect
from django.urls import reverse

from .models import ConfiguracionSistema


@admin.register(ConfiguracionSistema)
class ConfiguracionSistemaAdmin(admin.ModelAdmin):
    """Una sola fila, solo para superusuarios: se edita, no se agrega ni se borra."""

    fields = ("aviso_vencimiento_activo",)

    def changelist_view(self, request, extra_context=None):
        # Con una sola fila, el listado sobra: se entra directo a editarla.
        if not self.has_change_permission(request):
            return super().changelist_view(request, extra_context)
        configuracion = ConfiguracionSistema.cargar()
        return redirect(reverse("admin:core_configuracionsistema_change", args=[configuracion.pk]))

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return (
            request.user.is_active
            and request.user.is_superuser
            and not ConfiguracionSistema.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False
