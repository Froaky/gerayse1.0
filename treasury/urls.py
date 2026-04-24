from django.urls import path

from . import views

app_name = "treasury"

urlpatterns = [
    path("", views.index, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("proveedores/", views.proveedores_list, name="proveedores_list"),
    path("proveedores/nuevo/", views.proveedores_create, name="proveedores_create"),
    path("proveedores/<int:supplier_id>/", views.proveedores_detail, name="proveedores_detail"),
    path("proveedores/<int:supplier_id>/editar/", views.proveedores_update, name="proveedores_update"),
    path("proveedores/<int:supplier_id>/toggle/", views.proveedores_toggle, name="proveedores_toggle"),
    path("categorias/", views.categorias_list, name="categorias_list"),
    path("categorias/nueva/", views.categorias_create, name="categorias_create"),
    path("categorias/<int:category_id>/editar/", views.categorias_update, name="categorias_update"),
    path("categorias/<int:category_id>/toggle/", views.categorias_toggle, name="categorias_toggle"),
    path("cuentas-bancarias/", views.cuentas_bancarias_list, name="cuentas_bancarias_list"),
    path("cuentas-bancarias/nueva/", views.cuentas_bancarias_create, name="cuentas_bancarias_create"),
    path("cuentas-bancarias/<int:bank_account_id>/editar/", views.cuentas_bancarias_update, name="cuentas_bancarias_update"),
    path("cuentas-bancarias/<int:bank_account_id>/toggle/", views.cuentas_bancarias_toggle, name="cuentas_bancarias_toggle"),
    path("cuentas-por-pagar/", views.cuentas_por_pagar_list, name="cuentas_por_pagar_list"),
    path("cuentas-por-pagar/nueva/", views.cuentas_por_pagar_create, name="cuentas_por_pagar_create"),
    path("cuentas-por-pagar/<int:payable_id>/", views.cuentas_por_pagar_detail, name="cuentas_por_pagar_detail"),
    path("cuentas-por-pagar/<int:payable_id>/editar/", views.cuentas_por_pagar_update, name="cuentas_por_pagar_update"),
    path("cuentas-por-pagar/<int:payable_id>/anular/", views.cuentas_por_pagar_annul, name="cuentas_por_pagar_annul"),
    path("compromisos-especiales/", views.compromisos_especiales_list, name="compromisos_especiales_list"),
    path("compromisos-especiales/nuevo/", views.compromisos_especiales_create, name="compromisos_especiales_create"),
    path("compromisos-especiales/<int:commitment_id>/", views.compromisos_especiales_detail, name="compromisos_especiales_detail"),
    path("compromisos-especiales/<int:commitment_id>/autorizar/", views.compromisos_especiales_decide, name="compromisos_especiales_decide"),
    path("pagos/", views.pagos_list, name="pagos_list"),
    path("pagos/transferencia/nuevo/", views.pagos_transferencia_create, name="pagos_transferencia_create"),
    path("pagos/cheque/nuevo/", views.pagos_cheque_create, name="pagos_cheque_create"),
    path("pagos/echeq/nuevo/", views.pagos_echeq_create, name="pagos_echeq_create"),
    path("pagos/efectivo/nuevo/", views.pagos_efectivo_create, name="pagos_efectivo_create"),
    path("pagos/<int:pk>/", views.pagos_detail, name="pagos_detail"),
    path("pagos/<int:payment_id>/annul/", views.pagos_annul, name="pagos_annul"),
    # --- Bank Movements & Conciliation (EP-04) ---
    # EP-04: Bancos y Conciliacion
    path("bancos/", views.bank_movements_list, name="bank_movements_list"),
    path("bancos/nuevo/", views.bank_movements_create, name="bank_movements_create"),
    path("bancos/<int:pk>/", views.bank_movements_detail, name="bank_movements_detail"),
    path("bancos/<int:pk>/vincular/", views.bank_movements_link, name="bank_movements_link"),
    path("lotes-pos/", views.pos_batches_list, name="pos_batches_list"),
    path("lotes-pos/nuevo/", views.pos_batches_create, name="pos_batches_create"),
    path("acreditaciones/", views.card_accreditations_list, name="card_accreditations_list"),
    path("acreditaciones/registrar/", views.card_accreditations_register, name="card_accreditations_register"),
    path("conciliacion/", views.bank_reconciliation, name="bank_reconciliation"),
    # EP-05: Flujo de Disponibilidades
    path("disponibilidades/", views.disponibilidades_report, name="disponibilidades"),
    path("disponibilidades/cerrar/", views.close_month_action, name="close_month"),
    path("efectivo-central/", views.central_cash_movements, name="central_cash_list"),
    path("efectivo-central/nuevo/", views.central_cash_create, name="central_cash_create"),
    path("arqueos/", views.arqueo_list, name="arqueo_list"),
    path("arqueos/nuevo/", views.arqueo_create, name="arqueo_create"),
]
