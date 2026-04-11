from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("login/", views.GerayseLoginView.as_view(), name="login"),
    path("logout/", views.GerayseLogoutView.as_view(), name="logout"),
    
    # Personal Management
    path("personal/", views.personal_list, name="personal_list"),
    path("personal/nuevo/", views.personal_create, name="personal_create"),
    path("personal/<int:user_id>/editar/", views.personal_update, name="personal_update"),
]

