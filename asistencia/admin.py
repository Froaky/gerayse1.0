from django.contrib import admin

from .models import Empleado


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "dni", "empresa", "sucursal", "activo", "creado_en")
    list_filter = ("activo", "empresa")
    search_fields = ("nombre", "dni")
    autocomplete_fields = ("empresa", "sucursal")
    # activo tambien es de solo lectura: la baja y la reactivacion pasan por los
    # servicios del padron, que dejan quien y cuando.
    readonly_fields = ("activo", "dado_de_baja_en", "dado_de_baja_por", "creado_por", "creado_en", "actualizado_en")

    # Baja logica: borrar un empleado haria desaparecer sus marcas de los Excel.
    def has_delete_permission(self, request, obj=None):
        return False
