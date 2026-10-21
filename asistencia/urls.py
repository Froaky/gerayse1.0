from django.urls import path

from . import views

app_name = "asistencia"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("empleados/", views.empleado_list, name="empleado_list"),
    path("empleados/nuevo/", views.empleado_create, name="empleado_create"),
    path("empleados/importar/", views.empleado_importar, name="empleado_importar"),
    path("empleados/importar/confirmar/", views.empleado_importar_confirmar, name="empleado_importar_confirmar"),
    path("empleados/<int:empleado_id>/editar/", views.empleado_update, name="empleado_update"),
    path("empleados/<int:empleado_id>/baja/", views.empleado_baja, name="empleado_baja"),
    path("empleados/<int:empleado_id>/reactivar/", views.empleado_reactivar, name="empleado_reactivar"),
]
