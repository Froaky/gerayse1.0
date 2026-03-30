from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import CuentaBancaria, CuentaPorPagar, PagoTesoreria, Proveedor


class TreasuryNoDeleteAdminMixin:
    def has_delete_permission(self, request, obj=None):
        return False

    def delete_model(self, request, obj):
        raise PermissionDenied("Deletion via admin is disabled for treasury models.")

    def delete_queryset(self, request, queryset):
        raise PermissionDenied("Bulk deletion via admin is disabled for treasury models.")


class TreasuryReadOnlyAdminMixin(TreasuryNoDeleteAdminMixin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Proveedor)
class ProveedorAdmin(TreasuryNoDeleteAdminMixin, admin.ModelAdmin):
    list_display = ("razon_social", "identificador_fiscal", "activo", "creado_en")
    list_filter = ("activo",)
    search_fields = ("razon_social", "identificador_fiscal", "contacto", "email")
    autocomplete_fields = ("creado_por",)


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
        "concepto",
        "fecha_vencimiento",
        "importe_total",
        "saldo_pendiente",
        "estado",
    )
    list_filter = ("estado", "fecha_vencimiento")
    search_fields = ("proveedor__razon_social", "concepto", "referencia_comprobante")
    autocomplete_fields = ("proveedor", "creado_por", "anulada_por")
    inlines = []


@admin.register(PagoTesoreria)
class PagoTesoreriaAdmin(TreasuryReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("cuenta_por_pagar", "cuenta_bancaria", "medio_pago", "fecha_pago", "monto", "estado")
    list_filter = ("estado", "medio_pago", "fecha_pago", "cuenta_bancaria")
    search_fields = ("referencia", "observaciones", "cuenta_por_pagar__concepto")
    autocomplete_fields = ("cuenta_por_pagar", "cuenta_bancaria", "creado_por", "anulado_por")
