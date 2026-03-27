from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy


class GerayseLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["username"].widget.attrs.update(
            {
                "class": "app-input",
                "placeholder": "Usuario",
                "autocomplete": "username",
            }
        )
        form.fields["password"].widget.attrs.update(
            {
                "class": "app-input",
                "placeholder": "Contrasena",
                "autocomplete": "current-password",
            }
        )
        return form


class GerayseLogoutView(LogoutView):
    next_page = reverse_lazy("users:login")
