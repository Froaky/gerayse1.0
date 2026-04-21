from django import forms
from django.contrib.auth import get_user_model
from .models import Role

User = get_user_model()

class PersonalForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Contrasena (obligatoria para nuevos)"}),
        required=False,
        help_text="Dejalo en blanco si solo estas editando y no queres cambiarla."
    )

    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "last_name",
            "dni",
            "telefono",
            "email",
            "role",
            "is_active",
        ]
        labels = {
            "username": "Usuario / Login",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "dni": "DNI",
            "role": "Rol / Permisos",
        }
        widgets = {
            "username": forms.TextInput(attrs={"placeholder": "juan.perez"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Juan"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Perez"}),
            "dni": forms.TextInput(attrs={"placeholder": "12.345.678"}),
            "telefono": forms.TextInput(attrs={"placeholder": "387-..."}),
            "email": forms.EmailInput(attrs={"placeholder": "juan@example.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user
