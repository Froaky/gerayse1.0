from django.urls import path

from . import views

app_name = "cashops"

urlpatterns = [
    path("operacion/", views.dashboard, name="dashboard"),
    path("alertas/resolver/<int:alert_id>/", views.resolve_alert, name="resolve_alert"),
    path("alertas/", views.alert_panel, name="alert_panel"),
    path("gestion/matriz/", views.management_matrix, name="management_matrix"),
    path("gestion/matriz/exportar/", views.management_matrix_export, name="management_matrix_export"),
    path("rubros/", views.operational_category_list, name="operational_category_list"),
    path("rubros/nuevo/", views.operational_category_create, name="operational_category_create"),
    path("rubros/<int:category_id>/editar/", views.operational_category_update, name="operational_category_update"),
    path("rubros/<int:category_id>/toggle/", views.operational_category_toggle, name="operational_category_toggle"),
    path("limites-rubros/", views.operational_limit_list, name="operational_limit_list"),
    path("limites-rubros/nuevo/", views.operational_limit_create, name="operational_limit_create"),
    path("limites-rubros/<int:limit_id>/editar/", views.operational_limit_update, name="operational_limit_update"),
    path("sucursales/", views.sucursal_list, name="sucursal_list"),
    path("sucursales/nueva/", views.sucursal_create, name="sucursal_create"),
    path("sucursales/<int:sucursal_id>/editar/", views.sucursal_update, name="sucursal_update"),
    path("sucursales/<int:sucursal_id>/toggle/", views.sucursal_toggle, name="sucursal_toggle"),
    path("empresas/", views.empresa_list, name="empresa_list"),
    path("empresas/nueva/", views.empresa_create, name="empresa_create"),
    path("empresas/<int:empresa_id>/editar/", views.empresa_update, name="empresa_update"),
    path("empresas/<int:empresa_id>/toggle/", views.empresa_toggle, name="empresa_toggle"),
    path("empresas/activar/", views.set_empresa_activa, name="set_empresa_activa"),
    path("turnos/", views.turno_list, name="turno_list"),
    path("turnos/nuevo/", views.turno_create, name="turno_create"),
    path("cajas/nueva/", views.open_box_view, name="box_open"),
    path("cajas/<int:box_id>/gasto/", views.register_expense_view, name="box_expense"),
    path("cajas/<int:box_id>/venta/", views.register_sale_view, name="register_sale"),
    path("cajas/<int:box_id>/ingreso/", views.register_cash_income_view, name="box_income"),
    path("cajas/<int:box_id>/cerrar/preview/", views.close_box_preview, name="box_close_preview"),
    path("cajas/<int:box_id>/cerrar/", views.close_box_view, name="box_close"),
    path("traspasos/cajas/", views.transfer_between_boxes_view, name="transfer_boxes"),
    path("traspasos/sucursales/", views.transfer_between_branches_view, name="transfer_branches"),
]
