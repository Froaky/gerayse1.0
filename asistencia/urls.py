from django.urls import path

from . import views

app_name = "asistencia"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("fichar/", views.fichar_view, name="fichar"),
    path("empleados/", views.empleado_list, name="empleado_list"),
    path("empleados/nuevo/", views.empleado_create, name="empleado_create"),
    path("empleados/importar/", views.empleado_importar, name="empleado_importar"),
    path("empleados/importar/confirmar/", views.empleado_importar_confirmar, name="empleado_importar_confirmar"),
    path("empleados/<int:empleado_id>/editar/", views.empleado_update, name="empleado_update"),
    path("empleados/<int:empleado_id>/baja/", views.empleado_baja, name="empleado_baja"),
    path("empleados/<int:empleado_id>/reactivar/", views.empleado_reactivar, name="empleado_reactivar"),
    path("empleados/<int:empleado_id>/carnet/", views.carnet_empleado, name="carnet_empleado"),
    path("empleados/<int:empleado_id>/carnet/celular/", views.carnet_celular, name="carnet_celular"),
    path("empleados/<int:empleado_id>/carnet/anular/", views.carnet_anular, name="carnet_anular"),
    path("carnets/", views.carnets, name="carnets"),
    path("carnets/celular/", views.carnets_celular, name="carnets_celular"),
    path("por-local/", views.por_local, name="por_local"),
    path("excel/", views.excel, name="excel"),
    path("marcas/", views.marca_list, name="marca_list"),
    path("marcas/nueva/", views.marca_create, name="marca_create"),
    path("marcas/<int:marca_id>/anular/", views.marca_anular, name="marca_anular"),
]
