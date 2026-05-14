from django.urls import path

from . import views

app_name = "users"

urlpatterns = [
    path("login/", views.GerayseLoginView.as_view(), name="login"),
    path("logout/", views.GerayseLogoutView.as_view(), name="logout"),
    path("password/cambio-obligatorio/", views.password_change_required, name="password_change_required"),
    path("primer-ingreso/<uidb64>/<token>/", views.first_access, name="first_access"),

    # User management
    path("usuarios/", views.user_list, name="user_list"),
    path("usuarios/nuevo/", views.user_create, name="user_create"),
    path("usuarios/<int:user_id>/", views.user_detail, name="user_detail"),
    path("usuarios/<int:user_id>/editar/", views.user_update, name="user_update"),
    path("usuarios/<int:user_id>/archivar/", views.user_archive, name="user_archive"),
    path("usuarios/<int:user_id>/reactivar/", views.user_restore, name="user_restore"),
    path("usuarios/<int:user_id>/eliminar/", views.user_delete, name="user_delete"),
    path("usuarios/<int:user_id>/permisos/<str:module>/<str:action>/", views.user_permission_toggle, name="user_permission_toggle"),
    path("usuarios/roles/", views.role_list, name="role_list"),
    path("usuarios/roles/nuevo/", views.role_create, name="role_create"),
    path("usuarios/roles/<int:role_id>/", views.role_detail, name="role_detail"),
    path("usuarios/roles/<int:role_id>/permisos/<str:module>/<str:action>/", views.role_permission_toggle, name="role_permission_toggle"),

    # Backward-compatible personal URLs
    path("personal/", views.user_list, name="personal_list"),
    path("personal/nuevo/", views.user_create, name="personal_create"),
    path("personal/<int:user_id>/editar/", views.user_update, name="personal_update"),
]

