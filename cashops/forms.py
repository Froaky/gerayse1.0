import datetime
import uuid
from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from treasury.models import CategoriaCuentaPagar, Proveedor

from .models import CanalIngreso, Caja, Empresa, LimiteRubroOperativo, MovimientoCaja, RubroOperativo, Sucursal, Transferencia, Turno
from .permissions import can_assign_box_to_user, is_cashops_admin
from .services import CLOSING_DIFF_THRESHOLD, MAX_OPERATIONAL_LIMIT_PERCENTAGE


User = get_user_model()


class AltaIdempotenteForm(forms.Form):
    """Formulario de alta que no duplica cuando se reenvia el mismo envio.

    Lleva un token oculto, nuevo en cada render. El servicio lo recibe y, si ya
    grabo un registro con ese token, devuelve ese en lugar de crear otro: cubre
    el doble click, el reintento despues de un timeout y el volver atras y
    reenviar. Entrar de nuevo al formulario genera un token nuevo, asi que dos
    operaciones iguales de verdad siguen entrando las dos.
    """

    token_alta = forms.UUIDField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # En un form ligado el valor sale de los datos enviados, asi que el token
        # sobrevive a un render con errores de validacion.
        if not self.is_bound:
            self.fields["token_alta"].initial = uuid.uuid4()

    def creation_token(self):
        """Token del envio actual, para pasarle al servicio."""
        return self.cleaned_data.get("token_alta")


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
        fields = ["empresa", "tipo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["empresa"].queryset = Empresa.objects.filter(activa=True)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class CajaAperturaForm(forms.Form):
    usuario = forms.ModelChoiceField(queryset=User.objects.none(), label="Responsable")
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.none())
    turno = forms.ModelChoiceField(queryset=Turno.objects.none(), label="Turno")
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

    def __init__(self, *args, empresa=None, empresa_ids=None, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["fecha_operativa"].initial = datetime.date.today()
        # empresa_ids (multi) tiene prioridad; empresa (single) es fallback de compatibilidad
        if empresa_ids is not None:
            self.fields["turno"].queryset = Turno.objects.filter(empresa_id__in=empresa_ids)
        elif empresa:
            self.fields["turno"].queryset = Turno.objects.filter(empresa=empresa)
        else:
            self.fields["turno"].queryset = Turno.objects.all()
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
            elif empresa_ids is not None:
                self.fields["sucursal"].queryset = Sucursal.objects.filter(
                    activa=True, empresa_id__in=empresa_ids
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


class IngresoEfectivoForm(AltaIdempotenteForm):
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


class GastoRapidoForm(AltaIdempotenteForm):
    rubro_operativo = forms.ModelChoiceField(queryset=RubroOperativo.objects.none(), label="Rubro")
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    observacion = forms.CharField(
        max_length=255,
        label="Observacion",
        required=False,
        widget=forms.Textarea(attrs={"placeholder": "Observacion opcional"}),
    )
    pago_otra_sucursal = forms.BooleanField(
        required=False,
        label="Pago de otra sucursal",
        help_text="Marca si este gasto corresponde a otra sucursal.",
    )
    sucursal_destino = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        required=False,
        label="Sucursal destino",
        help_text="Sucursal que recibe el ingreso de mercaderia.",
    )

    def __init__(self, *args, sucursal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._sucursal = sucursal
        self.fields["rubro_operativo"].queryset = RubroOperativo.objects.filter(
            activo=True,
            es_sistema=False,
        )
        if sucursal and sucursal.empresa_id:
            qs = Sucursal.objects.filter(empresa=sucursal.empresa, activa=True).exclude(pk=sucursal.pk)
        else:
            qs = Sucursal.objects.filter(activa=True)
            if sucursal:
                qs = qs.exclude(pk=sucursal.pk)
        self.fields["sucursal_destino"].queryset = qs
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "input")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("pago_otra_sucursal") and not cleaned_data.get("sucursal_destino"):
            self.add_error("sucursal_destino", "Selecciona la sucursal destino.")
        return cleaned_data

    # --- Propiedades para el template (campo condicional por checkbox) ---
    @property
    def conditional_checkbox_target(self):
        return True

    @property
    def checkbox_trigger_field_id(self):
        return "id_pago_otra_sucursal"

    @property
    def checkbox_target_field_name(self):
        return "sucursal_destino"

    @property
    def checkbox_target_field_id(self):
        return "id_sucursal_destino"

    @property
    def show_checkbox_target_field(self):
        return bool(self.data.get("pago_otra_sucursal"))


class ClosedBoxMovementEditForm(forms.Form):
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Monto corregido",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    rubro_operativo = forms.ModelChoiceField(
        queryset=RubroOperativo.objects.none(),
        required=False,
        label="Rubro",
    )
    categoria = forms.CharField(
        max_length=80,
        required=False,
        label="Detalle",
        widget=forms.TextInput(attrs={"placeholder": "Detalle breve"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        label="Observación",
        widget=forms.Textarea(attrs={"placeholder": "Observación visible del movimiento"}),
    )
    motivo = forms.CharField(
        label="Motivo de corrección",
        widget=forms.Textarea(attrs={"placeholder": "Explica por qué se corrige esta caja cerrada"}),
    )

    def __init__(self, *args, movement=None, **kwargs):
        self.movement = movement
        initial = kwargs.setdefault("initial", {})
        if movement is not None:
            initial.setdefault("monto", movement.monto)
            initial.setdefault("rubro_operativo", movement.rubro_operativo_id)
            initial.setdefault("categoria", movement.categoria)
            initial.setdefault("observacion", movement.observacion)
        super().__init__(*args, **kwargs)
        self.fields["rubro_operativo"].queryset = RubroOperativo.objects.filter(activo=True)
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        if self.movement and self.movement.tipo == MovimientoCaja.Tipo.GASTO and not cleaned_data.get("rubro_operativo"):
            self.add_error("rubro_operativo", "El rubro es obligatorio para gastos operativos.")
        return cleaned_data


class ClosedBoxMovementAnnulForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo de anulación",
        widget=forms.Textarea(attrs={"placeholder": "Explica por qué se elimina este movimiento de una caja cerrada"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input textarea")


class BoxEditForm(forms.Form):
    usuario = forms.ModelChoiceField(queryset=User.objects.none(), label="Responsable")
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.none())
    turno = forms.ModelChoiceField(queryset=Turno.objects.none(), label="Turno")
    fecha_operativa = forms.DateField(
        label="Fecha operativa",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    monto_inicial = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.00"),
        label="Efectivo inicial",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    motivo = forms.CharField(
        label="Motivo de edición",
        widget=forms.Textarea(attrs={"placeholder": "Explica por qué se edita esta caja completa"}),
    )

    def __init__(self, *args, box=None, actor=None, empresa_ids=None, **kwargs):
        self.box = box
        self.actor = actor
        initial = kwargs.setdefault("initial", {})
        if box is not None:
            initial.setdefault("usuario", box.usuario_id)
            initial.setdefault("sucursal", box.sucursal_id)
            initial.setdefault("turno", box.turno_id)
            initial.setdefault("fecha_operativa", box.fecha_operativa)
            initial.setdefault("monto_inicial", box.monto_inicial)
        super().__init__(*args, **kwargs)
        self.fields["usuario"].queryset = User.objects.filter(is_active=True)
        if actor and not is_cashops_admin(actor):
            self.fields["usuario"].queryset = User.objects.filter(pk=actor.pk, is_active=True)
        if empresa_ids is not None:
            self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True, empresa_id__in=empresa_ids)
            self.fields["turno"].queryset = Turno.objects.filter(empresa_id__in=empresa_ids)
        else:
            self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True)
            self.fields["turno"].queryset = Turno.objects.all()
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")

    def clean(self):
        cleaned_data = super().clean()
        usuario = cleaned_data.get("usuario")
        sucursal = cleaned_data.get("sucursal")
        turno = cleaned_data.get("turno")
        if self.actor and usuario and not can_assign_box_to_user(self.actor, usuario):
            self.add_error("usuario", "No podés asignar una caja a otro usuario.")
        if sucursal and turno and sucursal.empresa_id != turno.empresa_id:
            self.add_error("turno", "El turno debe pertenecer a la misma empresa de la sucursal.")
        if (
            self.actor
            and not is_cashops_admin(self.actor)
            and usuario
            and getattr(usuario, "usuario_fijo", False)
        ):
            base_sucursal_id = getattr(usuario, "sucursal_base_id", None)
            if base_sucursal_id and sucursal and sucursal.id != base_sucursal_id:
                self.add_error("sucursal", "El usuario fijo solo puede operar en su sucursal base.")
        return cleaned_data


class BoxAnnulForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo de eliminación",
        widget=forms.Textarea(attrs={"placeholder": "Explica por qué se elimina/anula esta caja completa"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input textarea")


class GastoComoDeudaForm(AltaIdempotenteForm):
    proveedor = forms.ModelChoiceField(queryset=Proveedor.objects.none(), label="Proveedor")
    rubro = forms.ModelChoiceField(queryset=RubroOperativo.objects.none(), label="Rubro")
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        required=False,
        label="Sucursal de la deuda",
        help_text="Para qué sucursal es este gasto.",
    )
    fecha_factura = forms.DateField(
        label="Fecha de factura",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        input_formats=["%Y-%m-%d"],
        help_text="Fecha del comprobante; imputa la deuda a ese período.",
    )
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    concepto = forms.CharField(
        max_length=160,
        label="Concepto",
        widget=forms.TextInput(attrs={"placeholder": "Que se compro o pago"}),
    )
    referencia_comprobante = forms.CharField(
        max_length=60,
        required=False,
        label="Referencia o comprobante",
        widget=forms.TextInput(attrs={"placeholder": "Numero de factura o remito (opcional)"}),
    )
    observacion = forms.CharField(
        max_length=255,
        required=False,
        label="Observacion",
        widget=forms.TextInput(attrs={"placeholder": "Detalle opcional"}),
    )

    def __init__(self, *args, sucursales=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True).order_by("razon_social")
        self.fields["rubro"].queryset = RubroOperativo.objects.filter(
            activo=True, es_sistema=False
        ).order_by("nombre")
        # El selector de sucursal solo aparece si el usuario tiene mas de una
        # sucursal habilitada para deuda; si no, la deuda va a la de la caja.
        if sucursales is not None and len(sucursales) > 1:
            self.fields["sucursal"].queryset = sucursales
            self.fields["sucursal"].required = True
        else:
            self.fields.pop("sucursal", None)


class CajaValidacionRechazoForm(forms.Form):
    motivo = forms.CharField(
        label="Motivo del rechazo",
        widget=forms.Textarea(attrs={"placeholder": "Explica por qué el efectivo entregado no coincide con lo cargado"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input textarea")


class VentaGeneralForm(AltaIdempotenteForm):
    tipo_venta = forms.ChoiceField(choices=[], label="Medio de ingreso")
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
        canales = CanalIngreso.objects.filter(activo=True).order_by("orden")
        self.fields["tipo_venta"].choices = [(c.codigo, c.nombre) for c in canales]
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class TransferenciaEntreCajasForm(AltaIdempotenteForm):
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


class CanalIngresoForm(forms.ModelForm):
    class Meta:
        model = CanalIngreso
        fields = ["nombre", "orden", "impacta_saldo_caja", "excluir_de_totales"]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Ej: Rappi, Depósito, Billetera virtual..."}),
            "orden": forms.NumberInput(attrs={"placeholder": "0"}),
        }
        labels = {
            "impacta_saldo_caja": "Impacta saldo físico de caja",
            "excluir_de_totales": "Excluir de totales de ventas digitales",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault("class", "checkbox")
            else:
                field.widget.attrs.setdefault("class", "input")


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
