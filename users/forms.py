from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from cashops.models import Empresa, Sucursal
from .models import Role

User = get_user_model()


def _apply_operational_classes(form: forms.BaseForm) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "input select")
        elif isinstance(field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
            field.widget.attrs.setdefault("class", "")
        else:
            field.widget.attrs.setdefault("class", "input")


def _configure_fixed_user_target(form: forms.BaseForm) -> None:
    form.conditional_checkbox_target = True
    form.checkbox_target_field_name = "sucursal_base"
    form.checkbox_trigger_field_id = form["usuario_fijo"].id_for_label
    form.checkbox_target_field_id = form["sucursal_base"].id_for_label
    form.show_checkbox_target_field = bool(
        form.data.get("usuario_fijo")
        if form.is_bound
        else getattr(form.instance, "usuario_fijo", False)
    )


def _role_queryset_for_instance(instance):
    query = Q(is_active=True)
    if getattr(instance, "role_id", None):
        query |= Q(pk=instance.role_id)
    return Role.objects.filter(query).order_by("name")


class PersonalForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Contrasena (obligatoria para nuevos)"}),
        required=False,
        help_text="En altas funciona como contrasena default. El usuario debera cambiarla al primer ingreso.",
    )
    empresas_permitidas = forms.ModelMultipleChoiceField(
        queryset=Empresa.objects.filter(activa=True).order_by("nombre"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Empresas con acceso",
        help_text="Vacio = sin restriccion (ve todas las empresas).",
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
            "usuario_fijo",
            "sucursal_base",
            "empresa_principal",
            "empresas_permitidas",
            "is_active",
        ]
        labels = {
            "username": "Usuario / Login",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "dni": "DNI",
            "role": "Rol / Permisos",
            "usuario_fijo": "Usuario fijo",
            "sucursal_base": "Sucursal base",
            "empresa_principal": "Empresa principal",
            "is_active": "Usuario activo",
        }
        widgets = {
            "username": forms.TextInput(attrs={"placeholder": "juan.perez"}),
            "first_name": forms.TextInput(attrs={"placeholder": "Juan"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Perez"}),
            "dni": forms.TextInput(attrs={"placeholder": "12.345.678"}),
            "telefono": forms.TextInput(attrs={"placeholder": "387-..."}),
            "email": forms.EmailInput(attrs={"placeholder": "juan@example.com"}),
        }
        help_texts = {
            "empresa_principal": "Se selecciona automaticamente al iniciar sesion.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sucursal_base"].queryset = Sucursal.objects.filter(activa=True).order_by("nombre")
        self.fields["role"].queryset = _role_queryset_for_instance(self.instance)
        self.fields["empresa_principal"].queryset = Empresa.objects.filter(activa=True).order_by("nombre")
        if self.instance.pk:
            self.fields["empresas_permitidas"].initial = self.instance.empresas_permitidas.all()
        _apply_operational_classes(self)
        _configure_fixed_user_target(self)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("usuario_fijo") and not cleaned_data.get("sucursal_base"):
            self.add_error("sucursal_base", "La sucursal base es obligatoria para un usuario fijo.")
        if not cleaned_data.get("usuario_fijo"):
            cleaned_data["sucursal_base"] = None
        if not self.instance.pk and not cleaned_data.get("password"):
            self.add_error("password", "La contrasena es obligatoria para nuevos usuarios.")
        principal = cleaned_data.get("empresa_principal")
        permitidas = cleaned_data.get("empresas_permitidas")
        if principal and permitidas and principal not in permitidas:
            self.add_error("empresa_principal", "La empresa principal debe estar entre las empresas con acceso.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
            user.must_change_password = True
        if commit:
            user.save()
            self.save_m2m()
        return user


class UserAccessForm(forms.ModelForm):
    empresas_permitidas = forms.ModelMultipleChoiceField(
        queryset=Empresa.objects.filter(activa=True).order_by("nombre"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Empresas con acceso",
        help_text="Vacio = sin restriccion (ve todas las empresas).",
    )

    class Meta:
        model = User
        fields = [
            "role",
            "usuario_fijo",
            "sucursal_base",
            "empresa_principal",
            "empresas_permitidas",
            "is_active",
        ]
        labels = {
            "role": "Rol / permisos",
            "usuario_fijo": "Usuario fijo",
            "sucursal_base": "Sucursal base",
            "empresa_principal": "Empresa principal",
            "is_active": "Usuario activo",
        }
        help_texts = {
            "role": "El rol define permisos default; la ficha del usuario puede tener ajustes puntuales.",
            "usuario_fijo": "Si esta activo, el usuario queda asociado a una sucursal base.",
            "empresa_principal": "Se selecciona automaticamente al iniciar sesion.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = _role_queryset_for_instance(self.instance)
        self.fields["sucursal_base"].queryset = Sucursal.objects.filter(activa=True).order_by("nombre")
        self.fields["empresa_principal"].queryset = Empresa.objects.filter(activa=True).order_by("nombre")
        if self.instance.pk:
            self.fields["empresas_permitidas"].initial = self.instance.empresas_permitidas.all()
        _apply_operational_classes(self)
        _configure_fixed_user_target(self)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("usuario_fijo") and not cleaned_data.get("sucursal_base"):
            self.add_error("sucursal_base", "La sucursal base es obligatoria para un usuario fijo.")
        if not cleaned_data.get("usuario_fijo"):
            cleaned_data["sucursal_base"] = None
        principal = cleaned_data.get("empresa_principal")
        permitidas = cleaned_data.get("empresas_permitidas")
        if principal and permitidas and principal not in permitidas:
            self.add_error("empresa_principal", "La empresa principal debe estar entre las empresas con acceso.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if commit:
            user.save()
            self.save_m2m()
        return user


class RoleForm(forms.ModelForm):
    class Meta:
        model = Role
        fields = ["code", "name", "is_active"]
        labels = {
            "code": "Codigo",
            "name": "Nombre",
            "is_active": "Rol activo",
        }
        help_texts = {
            "code": "Usar codigos cortos, por ejemplo ADMIN, ENCARGADO o SOLO_LECTURA.",
        }
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "ENCARGADO"}),
            "name": forms.TextInput(attrs={"placeholder": "Encargado"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_operational_classes(self)

    def clean_code(self):
        return (self.cleaned_data["code"] or "").strip().upper()
