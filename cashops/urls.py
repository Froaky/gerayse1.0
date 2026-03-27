from django.urls import path

from . import views

app_name = "cashops"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sucursales/", views.sucursal_list, name="sucursal_list"),
    path("sucursales/nueva/", views.sucursal_create, name="sucursal_create"),
    path("turnos/", views.turno_list, name="turno_list"),
    path("turnos/nuevo/", views.turno_create, name="turno_create"),
    path("cajas/nueva/", views.open_box_view, name="box_open"),
    path("cajas/<int:box_id>/gasto/", views.register_expense_view, name="box_expense"),
    path("cajas/<int:box_id>/pos/", views.register_card_sale_view, name="box_pos"),
    path("cajas/<int:box_id>/cerrar/", views.close_box_view, name="box_close"),
    path("traspasos/cajas/", views.transfer_between_boxes_view, name="transfer_boxes"),
    path("traspasos/sucursales/", views.transfer_between_branches_view, name="transfer_branches"),
]

