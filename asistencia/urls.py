from django.urls import path

from . import views

app_name = "asistencia"

urlpatterns = [
    path("", views.inicio, name="inicio"),
]
