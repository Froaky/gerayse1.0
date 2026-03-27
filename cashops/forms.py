from decimal import Decimal

from django import forms

from .models import Caja, Sucursal, Transferencia, Turno
from .services import CLOSING_DIFF_THRESHOLD


class SucursalForm(forms.ModelForm):
    class Meta:
        model = Sucursal
        fields = ["codigo", "nombre", "activa"]
        widgets = {
            "codigo": forms.TextInput(attrs={"placeholder": "SUC-01"}),
            "nombre": forms.TextInput(attrs={"placeholder": "Sucursal Centro"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class TurnoForm(forms.ModelForm):
    class Meta:
        model = Turno
        fields = ["sucursal", "fecha_operativa", "tipo", "observacion"]
        widgets = {
            "fecha_operativa": forms.DateInput(attrs={"type": "date"}),
            "observacion": forms.TextInput(attrs={"placeholder": "Notas del turno"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class CajaAperturaForm(forms.Form):
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.none())
    turno = forms.ModelChoiceField(queryset=Turno.objects.none())
    monto_inicial = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class GastoRapidoForm(forms.Form):
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    categoria = forms.CharField(max_length=80, widget=forms.TextInput(attrs={"placeholder": "Viaticos, insumos..."}))
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"placeholder": "Detalle breve"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            else:
                field.widget.attrs.setdefault("class", "input")


class VentaTarjetaForm(forms.Form):
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Detalle opcional"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


class TransferenciaEntreCajasForm(forms.Form):
    caja_origen = forms.ModelChoiceField(queryset=Caja.objects.none(), label="Caja origen")
    caja_destino = forms.ModelChoiceField(queryset=Caja.objects.none(), label="Caja destino")
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Motivo del traspaso"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        caja_origen = cleaned_data.get("caja_origen")
        caja_destino = cleaned_data.get("caja_destino")
        monto = cleaned_data.get("monto")
        if caja_origen and caja_destino and caja_origen == caja_destino:
            self.add_error("caja_destino", "El origen y el destino no pueden ser la misma caja.")
        if monto is not None and monto <= 0:
            self.add_error("monto", "El monto debe ser mayor que cero.")
        return cleaned_data


class TransferenciaEntreSucursalesForm(forms.Form):
    sucursal_origen = forms.ModelChoiceField(queryset=Sucursal.objects.none(), label="Sucursal origen")
    sucursal_destino = forms.ModelChoiceField(queryset=Sucursal.objects.none(), label="Sucursal destino")
    clase = forms.ChoiceField(choices=Transferencia.Clase.choices)
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Detalle del envio"}),
    )
    caja_origen = forms.ModelChoiceField(queryset=Caja.objects.none(), required=False, label="Caja origen")
    caja_destino = forms.ModelChoiceField(queryset=Caja.objects.none(), required=False, label="Caja destino")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        sucursal_origen = cleaned_data.get("sucursal_origen")
        sucursal_destino = cleaned_data.get("sucursal_destino")
        clase = cleaned_data.get("clase")
        monto = cleaned_data.get("monto")
        observacion = (cleaned_data.get("observacion") or "").strip()
        caja_origen = cleaned_data.get("caja_origen")
        caja_destino = cleaned_data.get("caja_destino")
        if sucursal_origen and sucursal_destino and sucursal_origen == sucursal_destino:
            self.add_error("sucursal_destino", "El origen y el destino no pueden ser la misma sucursal.")
        if clase == Transferencia.Clase.DINERO and (monto is None or monto <= 0):
            self.add_error("monto", "El monto es obligatorio para transferencias de dinero.")
        if clase == Transferencia.Clase.DINERO and not caja_origen:
            self.add_error("caja_origen", "La caja origen es obligatoria para transferencias de dinero.")
        if clase == Transferencia.Clase.DINERO and not caja_destino:
            self.add_error("caja_destino", "La caja destino es obligatoria para transferencias de dinero.")
        if caja_origen and sucursal_origen and caja_origen.sucursal_id != sucursal_origen.id:
            self.add_error("caja_origen", "La caja origen debe pertenecer a la sucursal origen.")
        if caja_destino and sucursal_destino and caja_destino.sucursal_id != sucursal_destino.id:
            self.add_error("caja_destino", "La caja destino debe pertenecer a la sucursal destino.")
        if clase == Transferencia.Clase.MERCADERIA and not observacion:
            self.add_error("observacion", "La observacion es obligatoria para mercaderia.")
        return cleaned_data


class CierreCajaForm(forms.Form):
    saldo_fisico = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    justificacion = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={"placeholder": "Obligatoria si la diferencia supera 10.000"}),
    )

    def __init__(self, *args, **kwargs):
        self.caja = kwargs.pop("caja", None)
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        saldo_fisico = cleaned_data.get("saldo_fisico")
        justificacion = (cleaned_data.get("justificacion") or "").strip()
        if self.caja and saldo_fisico is not None:
            diferencia = saldo_fisico - self.caja.saldo_esperado
            if abs(diferencia) > CLOSING_DIFF_THRESHOLD and not justificacion:
                self.add_error("justificacion", "La diferencia supera 10.000 y requiere justificacion.")
        return cleaned_data
