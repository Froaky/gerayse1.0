from django.contrib import admin

from .models import AlertaOperativa, Caja, CierreCaja, Justificacion, MovimientoCaja, Sucursal, Transferencia, Turno


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activa", "creada_en")
    list_filter = ("activa",)
    search_fields = ("codigo", "nombre")


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ("sucursal", "fecha_operativa", "tipo", "estado", "abierto_en", "cerrado_en")
    list_filter = ("estado", "tipo", "sucursal")
    search_fields = ("sucursal__nombre", "sucursal__codigo", "observacion")
    autocomplete_fields = ("sucursal", "creado_por")


class MovimientoCajaInline(admin.TabularInline):
    model = MovimientoCaja
    extra = 0
    can_delete = False
    readonly_fields = (
        "tipo",
        "sentido",
        "monto",
        "categoria",
        "observacion",
        "transferencia",
        "creado_por",
        "creado_en",
    )


@admin.register(Caja)
class CajaAdmin(admin.ModelAdmin):
    list_display = ("id", "sucursal", "turno", "usuario", "estado", "monto_inicial", "saldo_actual", "abierta_en")
    list_filter = ("estado", "sucursal", "turno")
    search_fields = ("usuario__username", "usuario__first_name", "usuario__last_name")
    autocomplete_fields = ("sucursal", "turno", "usuario", "cerrada_por")
    inlines = [MovimientoCajaInline]

    @admin.display(description="Saldo actual")
    def saldo_actual(self, obj: Caja):
        return obj.saldo_esperado


@admin.register(MovimientoCaja)
class MovimientoCajaAdmin(admin.ModelAdmin):
    list_display = ("caja", "tipo", "sentido", "monto", "categoria", "creado_en")
    list_filter = ("tipo", "sentido")
    search_fields = ("observacion", "categoria")
    autocomplete_fields = ("caja", "transferencia", "creado_por")


@admin.register(Transferencia)
class TransferenciaAdmin(admin.ModelAdmin):
    list_display = ("id", "tipo", "clase", "monto", "sucursal_origen", "sucursal_destino", "creado_en")
    list_filter = ("tipo", "clase")
    search_fields = ("observacion",)
    autocomplete_fields = (
        "caja_origen",
        "caja_destino",
        "sucursal_origen",
        "sucursal_destino",
        "creado_por",
    )


@admin.register(CierreCaja)
class CierreCajaAdmin(admin.ModelAdmin):
    list_display = ("caja", "saldo_esperado", "saldo_fisico", "diferencia", "estado", "cerrado_en")
    list_filter = ("estado",)
    search_fields = ("caja__id",)
    autocomplete_fields = ("caja", "ajuste_movimiento", "cerrado_por")


@admin.register(Justificacion)
class JustificacionAdmin(admin.ModelAdmin):
    list_display = ("cierre", "creado_por", "creado_en")
    search_fields = ("motivo",)
    autocomplete_fields = ("cierre", "creado_por")


@admin.register(AlertaOperativa)
class AlertaOperativaAdmin(admin.ModelAdmin):
    list_display = ("tipo", "caja", "sucursal", "resuelta", "creada_en")
    list_filter = ("tipo", "resuelta", "sucursal")
    search_fields = ("mensaje",)
    autocomplete_fields = ("cierre", "caja", "sucursal")
