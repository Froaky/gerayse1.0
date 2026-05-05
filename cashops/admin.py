from django.contrib import admin

from .models import (
    AlertaOperativa,
    Caja,
    CierreCaja,
    Empresa,
    Justificacion,
    LimiteRubroOperativo,
    MovimientoCaja,
    RubroOperativo,
    Sucursal,
    Transferencia,
    Turno,
)
from .services import resync_operational_control_for_rubro


@admin.register(Sucursal)
class SucursalAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "activa", "creada_en")
    list_filter = ("activa",)
    search_fields = ("codigo", "nombre")


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("nombre",)
    search_fields = ("nombre",)


@admin.register(Turno)
class TurnoAdmin(admin.ModelAdmin):
    list_display = ("empresa", "tipo", "creado_por", "creado_en")
    list_filter = ("tipo", "empresa")
    search_fields = ("empresa__nombre",)
    autocomplete_fields = ("empresa", "creado_por")


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
        "rubro_operativo",
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
    list_display = ("caja", "tipo", "sentido", "monto", "rubro_operativo", "categoria", "creado_en")
    list_filter = ("tipo", "sentido", "rubro_operativo")
    search_fields = ("observacion", "categoria", "rubro_operativo__nombre")
    autocomplete_fields = ("caja", "rubro_operativo", "transferencia", "creado_por")


@admin.register(RubroOperativo)
class RubroOperativoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "activo", "es_sistema", "creado_en", "actualizado_en")
    list_filter = ("activo", "es_sistema")
    search_fields = ("nombre",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        resync_operational_control_for_rubro(obj)


@admin.register(LimiteRubroOperativo)
class LimiteRubroOperativoAdmin(admin.ModelAdmin):
    list_display = ("rubro", "sucursal", "porcentaje_maximo", "creado_en")
    list_filter = ("sucursal",)
    search_fields = ("rubro__nombre", "sucursal__nombre")
    autocomplete_fields = ("rubro", "sucursal")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        resync_operational_control_for_rubro(obj.rubro)

    def delete_model(self, request, obj):
        rubro = obj.rubro
        super().delete_model(request, obj)
        resync_operational_control_for_rubro(rubro)


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
    list_display = ("tipo", "rubro_operativo", "caja", "sucursal", "periodo_fecha", "resuelta", "creada_en")
    list_filter = ("tipo", "resuelta", "sucursal", "rubro_operativo")
    search_fields = ("mensaje", "rubro_operativo__nombre", "dedupe_key")
    autocomplete_fields = ("cierre", "caja", "turno", "sucursal", "usuario", "rubro_operativo")
