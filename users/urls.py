from django.urls import path

from .views import GerayseLoginView, GerayseLogoutView

app_name = "users"

urlpatterns = [
    path("login/", GerayseLoginView.as_view(), name="login"),
    path("logout/", GerayseLogoutView.as_view(), name="logout"),
]

