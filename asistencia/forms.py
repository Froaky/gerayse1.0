import re
from datetime import datetime
from pathlib import Path

from django import forms
from django.utils import timezone

from cashops.models import Sucursal

from .models import Empleado, Marca

TAMANO_MAXIMO_LISTADO = 2 * 1024 * 1024
EXTENSIONES_LISTADO = (".csv", ".xlsx")


def _estilar(form) -> None:
    for field in form.fields.values():
        if isinstance(field.widget, forms.Select):
            field.widget.attrs.setdefault("class", "input select")
        elif isinstance(field.widget, forms.CheckboxInput):
            field.widget.attrs.setdefault("class", "checkbox")
        elif not isinstance(field.widget, (forms.ClearableFileInput, forms.HiddenInput)):
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
        self.fields["dni"].help_text = "Opcional. Solo números; los puntos se sacan solos. Evita cargar dos veces a la misma persona."
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


class CarnetsFiltroForm(forms.Form):
    empresa = forms.ModelChoiceField(label="Empresa", queryset=None, required=False, empty_label="Todas")
    sucursal = forms.ModelChoiceField(label="Local habitual", queryset=None, required=False, empty_label="Todos")

    def __init__(self, *args, empresas, sucursales, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = empresas
        self.fields["sucursal"].queryset = sucursales
        _estilar(self)


class AnularCarnetForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo",
        max_length=200,
        help_text="Por ejemplo: lo perdió, se rompió. Queda registrado con tu usuario y la fecha.",
    )
    # Cuantas anulaciones tenia el carnet al abrir la pantalla (ver anular_carnet).
    anulaciones_previas = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilar(self)


class MarcaManualForm(forms.Form):
    empleado = forms.ModelChoiceField(label="Empleado", queryset=None)
    tipo = forms.ChoiceField(label="Entrada o salida", choices=Marca.Tipo.choices)
    fecha = forms.DateField(label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    hora = forms.TimeField(label="Hora", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"))
    sucursal = forms.ModelChoiceField(label="Local donde fichó", queryset=None)
    motivo = forms.CharField(
        label="Motivo",
        max_length=200,
        help_text="Por ejemplo: se olvidó el carnet, llegó antes que el cajero, no había internet.",
    )

    def __init__(self, *args, empleados, sucursales, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleado"].queryset = empleados
        self.fields["sucursal"].queryset = sucursales
        _estilar(self)

    def clean(self):
        datos = super().clean()
        if datos.get("fecha") and datos.get("hora"):
            datos["momento"] = timezone.make_aware(datetime.combine(datos["fecha"], datos["hora"]))
        return datos


class AnularMarcaForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo",
        max_length=200,
        help_text="Por ejemplo: escaneó el carnet de un compañero, marca repetida. La marca no se borra.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilar(self)


class MarcaFiltroForm(forms.Form):
    empleado = forms.ModelChoiceField(label="Empleado", queryset=None, required=False, empty_label="Todos")
    sucursal = forms.ModelChoiceField(label="Local", queryset=None, required=False, empty_label="Todos")
    desde = forms.DateField(label="Desde", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    hasta = forms.DateField(label="Hasta", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    anuladas = forms.BooleanField(label="Mostrar anuladas", required=False)

    def __init__(self, *args, empleados, sucursales, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empleado"].queryset = empleados
        self.fields["sucursal"].queryset = sucursales
        _estilar(self)


class PorLocalForm(forms.Form):
    fecha = forms.DateField(label="Día", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    local = forms.ModelChoiceField(label="Local", queryset=None, required=False, empty_label="Todos")

    def __init__(self, *args, locales, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["local"].queryset = locales
        _estilar(self)


class ExcelForm(forms.Form):
    mes = forms.CharField(label="Mes", widget=forms.TextInput(attrs={"type": "month"}))
    empresa = forms.ModelChoiceField(label="Empresa", queryset=None, required=False, empty_label="Todas")
    empleado = forms.ModelChoiceField(label="Un solo empleado", queryset=None, required=False, empty_label="Todos")

    def __init__(self, *args, empresas, empleados, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = empresas
        self.fields["empleado"].queryset = empleados
        _estilar(self)

    def clean_mes(self):
        texto = (self.cleaned_data.get("mes") or "").strip()
        # Solo digitos ASCII: "²" pasa isdigit() pero int() no lo convierte.
        partes = re.fullmatch(r"([0-9]{4})-([0-9]{1,2})", texto)
        if not partes:
            raise forms.ValidationError("Elegí un mes.")
        anio, mes = int(partes[1]), int(partes[2])
        if not (2020 <= anio <= 2100 and 1 <= mes <= 12):
            raise forms.ValidationError("Elegí un mes válido.")
        return anio, mes


class ImportarEmpleadosForm(forms.Form):
    archivo = forms.FileField(
        label="Listado de empleados",
        help_text="CSV o Excel (.xlsx) con las columnas nombre y local, y DNI si lo tiene. La empresa sale del local.",
    )

    def clean_archivo(self):
        archivo = self.cleaned_data["archivo"]
        if Path(archivo.name).suffix.lower() not in EXTENSIONES_LISTADO:
            raise forms.ValidationError("El listado tiene que ser un CSV o un Excel (.xlsx).")
        if archivo.size > TAMANO_MAXIMO_LISTADO:
            raise forms.ValidationError("El archivo es muy grande: el máximo es 2 MB.")
        return archivo


class ReglasForm(forms.Form):
    """Reglas de una empresa (US-14.10). La jornada se escribe como en un reloj
    ("8:30"), no en minutos; el modelo la guarda en minutos."""

    jornada = forms.CharField(
        label="Jornada diaria",
        max_length=5,
        help_text="Horas y minutos, por ejemplo 8:30. Lo que un día pase de esto son horas extra.",
    )
    tolerancia = forms.IntegerField(
        label="Tolerancia de llegadas tarde del mes (minutos)",
        min_value=0,
        max_value=24 * 60,
        help_text="Cuántos minutos tarde, sumados en todo el mes, se aceptan antes de marcarlo.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilar(self)

    def clean_jornada(self):
        texto = (self.cleaned_data.get("jornada") or "").strip()
        partes = re.fullmatch(r"([0-9]{1,2})(?:[:.]([0-5][0-9]))?", texto)
        if not partes:
            raise forms.ValidationError("Escribila como horas y minutos, por ejemplo 8:30.")
        minutos = int(partes[1]) * 60 + int(partes[2] or 0)
        if not 0 < minutos <= 24 * 60:
            raise forms.ValidationError("La jornada tiene que ser de más de 0 y hasta 24 horas.")
        return minutos
