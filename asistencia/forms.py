from pathlib import Path

from django import forms

from cashops.models import Sucursal

from .models import Empleado

TAMANO_MAXIMO_LISTADO = 2 * 1024 * 1024
EXTENSIONES_LISTADO = (".csv", ".xlsx")


def _estilar(form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "input select")
        elif not isinstance(field.widget, forms.ClearableFileInput):
            field.widget.attrs.setdefault("class", "input")


class EmpleadoForm(forms.ModelForm):
    """Los combos solo ofrecen empresas y locales del alcance del usuario; el
    cruce local-empresa lo valida Empleado.clean y el alcance el servicio."""

    class Meta:
        model = Empleado
        fields = ["nombre", "dni", "empresa", "sucursal"]

    def __init__(self, *args, empresas, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = empresas
        sucursales = Sucursal.objects.filter(activa=True, empresa__in=empresas)
        actual = self.instance.sucursal if self.instance.pk and self.instance.sucursal_id else None
        if actual is not None and empresas.filter(pk=actual.empresa_id).exists():
            # Editar no obliga a cambiar el local si despues se desactivo. Un
            # local que paso a una empresa fuera del alcance no se ofrece.
            sucursales = Sucursal.objects.filter(
                pk__in=[*sucursales.values_list("pk", flat=True), actual.pk]
            )
        self.fields["sucursal"].queryset = sucursales.select_related("empresa").order_by("nombre")
        self.fields["dni"].help_text = "Solo números; los puntos se sacan solos."
        _estilar(self)


class EmpleadoFiltroForm(forms.Form):
    ESTADOS = (("activos", "Activos"), ("baja", "Dados de baja"), ("todos", "Todos"))

    q = forms.CharField(label="Buscar", required=False)
    empresa = forms.ModelChoiceField(label="Empresa", queryset=None, required=False, empty_label="Todas")
    sucursal = forms.ModelChoiceField(label="Local", queryset=None, required=False, empty_label="Todos")
    estado = forms.ChoiceField(label="Estado", choices=ESTADOS, required=False)

    def __init__(self, *args, empresas, sucursales, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = empresas
        self.fields["sucursal"].queryset = sucursales
        self.fields["q"].widget.attrs["placeholder"] = "Nombre o DNI"
        _estilar(self)


class ImportarEmpleadosForm(forms.Form):
    archivo = forms.FileField(
        label="Listado de empleados",
        help_text="CSV o Excel (.xlsx) con las columnas nombre, DNI y local. La empresa sale del local.",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if Path(archivo.name).suffix.lower() not in EXTENSIONES_LISTADO:
            raise forms.ValidationError("El listado tiene que ser un CSV o un Excel (.xlsx).")
        if archivo.size > TAMANO_MAXIMO_LISTADO:
            raise forms.ValidationError("El archivo es muy grande: el máximo es 2 MB.")
        return archivo
