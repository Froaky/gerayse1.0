from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import (
    AcreditacionTarjeta,
    CategoriaCuentaPagar,
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    ObjetivoRubroEconomico,
    PagoTesoreria,
    Proveedor,
)


class TreasuryNoDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionDenied("El borrado fisico esta deshabilitado para tesoreria.")

    def delete_queryset(self, request, queryset):
        raise PermissionDenied("El borrado masivo esta deshabilitado para tesoreria.")


class TreasuryReadOnlyAdminMixin(TreasuryNoDeleteAdminMixin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Proveedor)
class ProveedorAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("razon_social", "identificador_fiscal", "contacto", "activo", "creado_en")
    list_filter = ("activo",)
    search_fields = ("razon_social", "identificador_fiscal", "contacto", "email")
    autocomplete_fields = ("creado_por",)


@admin.register(CategoriaCuentaPagar)
class CategoriaCuentaPagarAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "rubro_operativo", "activo", "creado_en")
    list_filter = ("activo", "rubro_operativo")
    search_fields = ("nombre", "rubro_operativo__nombre")
    autocomplete_fields = ("rubro_operativo", "creado_por")


@admin.register(ObjetivoRubroEconomico)
class ObjetivoRubroEconomicoAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("rubro_operativo", "sucursal", "porcentaje_objetivo", "vigencia_desde", "vigencia_hasta", "activo")
    list_filter = ("activo", "sucursal", "vigencia_desde")
    search_fields = ("rubro_operativo__nombre", "sucursal__nombre", "sucursal__codigo")
    autocomplete_fields = ("rubro_operativo", "sucursal", "creado_por", "actualizado_por")


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("nombre", "banco", "tipo_cuenta", "numero_cuenta", "activa")
    list_filter = ("activa", "tipo_cuenta", "banco")
    search_fields = ("nombre", "banco", "numero_cuenta", "alias", "cbu")
    autocomplete_fields = ("creado_por",)


@admin.register(CuentaPorPagar)
class CuentaPorPagarAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "proveedor",
        "categoria",
        "rubro_operativo",
        "concepto",
        "periodo_referencia",
        "fecha_vencimiento",
        "importe_total",
        "saldo_pendiente",
        "estado",
    )
    list_filter = ("estado", "categoria__rubro_operativo", "categoria", "periodo_referencia", "fecha_vencimiento")
    search_fields = (
        "proveedor__razon_social",
        "concepto",
        "referencia_comprobante",
        "categoria__rubro_operativo__nombre",
    )
    autocomplete_fields = ("proveedor", "categoria", "creado_por", "anulada_por")
    readonly_fields = (
        "proveedor",
        "categoria",
        "concepto",
        "referencia_comprobante",
        "fecha_emision",
        "fecha_vencimiento",
        "periodo_referencia",
        "importe_total",
        "saldo_pendiente",
        "estado",
        "observaciones",
        "creado_por",
        "creado_en",
        "actualizado_en",
        "anulada_por",
        "anulada_en",
        "motivo_anulacion",
    )

    @admin.display(description="Rubro")
    def rubro_operativo(self, obj):
        return obj.categoria.rubro_label


@admin.register(CompromisoEspecial)
class CompromisoEspecialAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("tipo", "concepto", "estado", "monto_estimado", "vencimiento", "sucursal", "requiere_autorizacion")
    list_filter = ("tipo", "estado", "requiere_autorizacion", "sucursal")
    search_fields = ("concepto", "organismo", "beneficiario", "expediente", "sustento_referencia")
    autocomplete_fields = (
        "cuenta_por_pagar",
        "sucursal",
        "autorizado_por",
        "creado_por",
        "actualizado_por",
    )


@admin.register(PagoTesoreria)
class PagoTesoreriaAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "cuenta_por_pagar",
        "cuenta_bancaria",
        "medio_pago",
        "fecha_pago",
        "monto",
        "estado",
        "estado_bancario",
    )
    list_filter = ("estado", "estado_bancario", "medio_pago", "fecha_pago", "cuenta_bancaria")
    search_fields = ("referencia", "cuenta_por_pagar__concepto", "cuenta_por_pagar__proveedor__razon_social")
    autocomplete_fields = ("cuenta_por_pagar", "cuenta_bancaria", "creado_por", "anulado_por")
    readonly_fields = (
        "cuenta_por_pagar",
        "cuenta_bancaria",
        "medio_pago",
        "fecha_pago",
        "fecha_diferida",
        "monto",
        "referencia",
        "observaciones",
        "estado",
        "estado_bancario",
        "observacion_bancaria",
        "creado_por",
        "creado_en",
        "anulado_por",
        "anulado_en",
        "motivo_anulacion",
    )


@admin.register(MovimientoBancario)
class MovimientoBancarioAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("fecha", "cuenta_bancaria", "tipo", "origen", "monto", "concepto", "pago_tesoreria")
    list_filter = ("tipo", "origen", "fecha", "cuenta_bancaria")
    search_fields = ("concepto", "referencia", "observaciones")
    autocomplete_fields = ("cuenta_bancaria", "pago_tesoreria", "creado_por")


@admin.register(LotePOS)
class LotePOSAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("fecha_lote", "operador", "terminal", "cuenta_bancaria", "total_lote")
    list_filter = ("fecha_lote", "operador", "cuenta_bancaria")
    search_fields = ("operador", "terminal", "observaciones")
    autocomplete_fields = ("cuenta_bancaria", "creado_por")


@admin.register(AcreditacionTarjeta)
class AcreditacionTarjetaAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("fecha_acreditacion", "cuenta_bancaria", "canal", "monto_acreditado")
    list_filter = ("movimiento_bancario__fecha", "movimiento_bancario__cuenta_bancaria", "canal")
    search_fields = ("canal", "referencia_externa", "observaciones")
    autocomplete_fields = ("movimiento_bancario", "lote_pos", "creado_por")


@admin.register(DescuentoAcreditacion)
class DescuentoAcreditacionAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("acreditacion", "tipo", "monto", "descripcion")
    list_filter = ("tipo",)
    search_fields = ("descripcion", "acreditacion__referencia_externa", "acreditacion__canal")
    autocomplete_fields = ("acreditacion", "creado_por")
