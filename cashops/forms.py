import datetime
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Caja, Empresa, LimiteRubroOperativo, MovimientoCaja, RubroOperativo, Sucursal, Transferencia, Turno
from .permissions import can_assign_box_to_user, is_cashops_admin
from .services import CLOSING_DIFF_THRESHOLD, MAX_OPERATIONAL_LIMIT_PERCENTAGE


User = get_user_model()


class EmpresaForm(forms.ModelForm):
    class Meta:
        model = Empresa
        fields = ["nombre", "identificador_fiscal", "activa"]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "ARMADI SRL"}),
            "identificador_fiscal": forms.TextInput(attrs={"placeholder": "30-12345678-9"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["identificador_fiscal"].required = False
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")


class SucursalForm(forms.ModelForm):
    class Meta:
        model = Sucursal
        fields = ["empresa", "codigo", "nombre", "razon_social", "activa"]
        widgets = {
            "codigo": forms.TextInput(attrs={"placeholder": "SUC-01"}),
            "nombre": forms.TextInput(attrs={"placeholder": "Sucursal Centro"}),
            "razon_social": forms.TextInput(attrs={"placeholder": "ARMADI SRL"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = Empresa.objects.filter(activa=True)
        self.fields["empresa"].required = False
        self.fields["razon_social"].required = True
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
    usuario = forms.ModelChoiceField(queryset=User.objects.none(), label="Responsable")
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.none())
    tipo_turno = forms.ChoiceField(choices=Turno.Tipo.choices, label="Turno")
    fecha_operativa = forms.DateField(
        label="Fecha operativa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    efectivo_inicial = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Efectivo inicial (caja fisica)",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
        help_text="Solo dinero fisico en la caja. Las ventas por tarjeta, QR o transferencia no van aca.",
    )

    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["fecha_operativa"].initial = datetime.date.today()
        if self.actor:
            self.fields["usuario"].initial = self.actor.pk
            self.fields["usuario"].queryset = (
                User.objects.filter(is_active=True)
                if is_cashops_admin(self.actor)
                else User.objects.filter(pk=self.actor.pk, is_active=True)
            )
            if not is_cashops_admin(self.actor):
                self.fields["usuario"].help_text = "La caja se abre a tu nombre."
            if getattr(self.actor, "usuario_fijo", False) and getattr(self.actor, "sucursal_base_id", None):
                self.fields["sucursal"].initial = self.actor.sucursal_base_id
                self.fields["sucursal"].queryset = Sucursal.objects.filter(
                    pk=self.actor.sucursal_base_id, activa=True
                )
            else:
                self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        usuario = cleaned_data.get("usuario")
        sucursal = cleaned_data.get("sucursal")
        if self.actor and usuario and not can_assign_box_to_user(self.actor, usuario):
            self.add_error("usuario", "No podes asignar una caja a otro usuario.")
        if (
            self.actor
            and not is_cashops_admin(self.actor)
            and usuario
            and getattr(usuario, "usuario_fijo", False)
        ):
            base_sucursal_id = getattr(usuario, "sucursal_base_id", None)
            if base_sucursal_id and sucursal and sucursal.id != base_sucursal_id:
                self.add_error("sucursal", "El usuario fijo solo puede abrir cajas en su sucursal base.")
        return cleaned_data


class IngresoEfectivoForm(forms.Form):
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    categoria = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={"placeholder": "Cobro extra, reintegro, fondo..."}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"placeholder": "Detalle breve del ingreso"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            else:
                field.widget.attrs.setdefault("class", "input")


class GastoRapidoForm(forms.Form):
    rubro_operativo = forms.ModelChoiceField(queryset=RubroOperativo.objects.none(), label="Rubro")
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    categoria = forms.CharField(
        max_length=80,
        label="Detalle corto",
        widget=forms.TextInput(attrs={"placeholder": "Compra menor, viatico, insumo..."}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.Textarea(attrs={"placeholder": "Detalle breve"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rubro_operativo"].queryset = RubroOperativo.objects.filter(
            activo=True,
            es_sistema=False,
        )
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class VentaGeneralForm(forms.Form):
    tipo_venta = forms.ChoiceField(
        choices=[
            (MovimientoCaja.Tipo.INGRESO_EFECTIVO, "Efectivo"),
            (MovimientoCaja.Tipo.VENTA_TARJETA, "Tarjeta (POS)"),
            (MovimientoCaja.Tipo.VENTA_TRANSFERENCIA, "Transferencia"),
            (MovimientoCaja.Tipo.VENTA_PEDIDOSYA, "PedidosYa"),
            (MovimientoCaja.Tipo.VENTA_QR, "QR / MercadoPago"),
        ],
        label="Medio de ingreso",
    )
    rubro = forms.ModelChoiceField(
        queryset=RubroOperativo.objects.filter(activo=True, es_sistema=False),
        label="Rubro",
        required=True,
    )
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Comentario opcional"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
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


class RubroOperativoForm(forms.ModelForm):
    class Meta:
        model = RubroOperativo
        fields = ["nombre", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Insumos, mantenimiento, viaticos..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean_nombre(self):
        nombre = (self.cleaned_data.get("nombre") or "").strip()
        queryset = RubroOperativo.objects.filter(nombre__iexact=nombre)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Ya existe un rubro con ese nombre.")
        return nombre


class LimiteRubroOperativoForm(forms.ModelForm):
    class Meta:
        model = LimiteRubroOperativo
        fields = ["rubro", "sucursal", "porcentaje_maximo"]
        widgets = {
            "porcentaje_maximo": forms.NumberInput(attrs={"step": "0.01", "placeholder": "15.00"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rubro_queryset = RubroOperativo.objects.filter(activo=True, es_sistema=False)
        if self.instance.pk and self.instance.rubro_id:
            rubro_queryset = RubroOperativo.objects.filter(
                (Q(activo=True, es_sistema=False) | Q(pk=self.instance.rubro_id))
            )
        self.fields["rubro"].queryset = rubro_queryset.order_by("nombre")
        self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
        self.fields["sucursal"].required = False
        self.fields["sucursal"].empty_label = "Global"
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean_porcentaje_maximo(self):
        porcentaje = self.cleaned_data["porcentaje_maximo"]
        if porcentaje <= 0:
            raise forms.ValidationError("El porcentaje maximo debe ser mayor que cero.")
        if porcentaje > MAX_OPERATIONAL_LIMIT_PERCENTAGE:
            raise forms.ValidationError("El porcentaje maximo no puede superar 100%.")
        return porcentaje

    def clean(self):
        cleaned_data = super().clean()
        rubro = cleaned_data.get("rubro")
        sucursal = cleaned_data.get("sucursal")
        if not rubro:
            return cleaned_data

        queryset = LimiteRubroOperativo.objects.filter(rubro=rubro)
        if sucursal:
            queryset = queryset.filter(sucursal=sucursal)
        else:
            queryset = queryset.filter(sucursal__isnull=True)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            if sucursal:
                self.add_error("sucursal", "Ese rubro ya tiene un limite configurado para la sucursal.")
            else:
                self.add_error("sucursal", "Ese rubro ya tiene un limite global configurado.")
        return cleaned_data
