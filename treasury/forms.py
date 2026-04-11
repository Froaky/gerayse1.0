from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from cashops.models import Sucursal


class TreasuryStyledFormMixin:
    def _apply_input_classes(self):
        for field in self.fields.values():
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs.setdefault("class", "input textarea")
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.setdefault("class", "input select")
            else:
                field.widget.attrs.setdefault("class", "input")


class SupplierForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = Proveedor
        fields = [
            "razon_social",
            "identificador_fiscal",
            "direccion",
            "contacto",
            "telefono",
            "email",
            "sitio_web",
            "alias_bancario",
            "cbu",
            "observaciones",
            "activo",
        ]
        labels = {
            "razon_social": "Nombre / Razon Social",
            "identificador_fiscal": "CUIT / Identificador",
            "sitio_web": "Sitio Web / Redes",
        }
        widgets = {
            "razon_social": forms.TextInput(attrs={"placeholder": "Proveedor SA"}),
            "identificador_fiscal": forms.TextInput(attrs={"placeholder": "30-12345678-9"}),
            "direccion": forms.TextInput(attrs={"placeholder": "Av. Siempre Viva 742"}),
            "contacto": forms.TextInput(attrs={"placeholder": "Nombre del contacto"}),
            "telefono": forms.TextInput(attrs={"placeholder": "387-5555555"}),
            "email": forms.EmailInput(attrs={"placeholder": "compras@proveedor.com"}),
            "sitio_web": forms.URLInput(attrs={"placeholder": "https://..."}),
            "alias_bancario": forms.TextInput(attrs={"placeholder": "Alias bancario"}),
            "cbu": forms.TextInput(attrs={"placeholder": "CBU de 22 digitos"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas internas sobre el proveedor"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class SupplierFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Proveedor, CUIT, contacto..."}))
    activo = forms.ChoiceField(required=False, choices=(("", "Todos"), ("1", "Activos"), ("0", "Inactivos")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class PayableCategoryForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = CategoriaCuentaPagar
        fields = ["nombre", "activo"]
        widgets = {"nombre": forms.TextInput(attrs={"placeholder": "Servicios, impuestos, mercaderia..."})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class PayableCategoryFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Categoria..."}))
    activo = forms.ChoiceField(required=False, choices=(("", "Todas"), ("1", "Activas"), ("0", "Inactivas")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class BankAccountForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = CuentaBancaria
        fields = [
            "nombre",
            "banco",
            "tipo_cuenta",
            "numero_cuenta",
            "alias",
            "cbu",
            "sucursal_bancaria",
            "sucursal",
            "activa",
        ]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Cuenta operativa"}),
            "banco": forms.TextInput(attrs={"placeholder": "Banco Galicia"}),
            "numero_cuenta": forms.TextInput(attrs={"placeholder": "123-456/7"}),
            "alias": forms.TextInput(attrs={"placeholder": "Alias opcional"}),
            "cbu": forms.TextInput(attrs={"placeholder": "CBU opcional"}),
            "sucursal_bancaria": forms.TextInput(attrs={"placeholder": "Sucursal"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class BankAccountFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Cuenta, banco, alias..."}))
    activa = forms.ChoiceField(required=False, choices=(("", "Todas"), ("1", "Activas"), ("0", "Inactivas")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class PayableForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = CuentaPorPagar
        fields = [
            "proveedor",
            "categoria",
            "concepto",
            "referencia_comprobante",
            "fecha_emision",
            "fecha_vencimiento",
            "importe_total",
            "sucursal",
            "observaciones",
        ]
        widgets = {
            "concepto": forms.TextInput(attrs={"placeholder": "Factura de mercaderia"}),
            "referencia_comprobante": forms.TextInput(attrs={"placeholder": "Factura / VEP / referencia"}),
            "fecha_emision": forms.DateInput(attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "importe_total": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas internas"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        suppliers = Proveedor.objects.filter(activo=True).order_by("razon_social")
        categories = CategoriaCuentaPagar.objects.filter(activo=True).order_by("nombre")
        if self.instance.pk:
            suppliers = (Proveedor.objects.filter(pk=self.instance.proveedor_id) | suppliers).distinct()
            categories = (CategoriaCuentaPagar.objects.filter(pk=self.instance.categoria_id) | categories).distinct()
        self.fields["proveedor"].queryset = suppliers
        self.fields["categoria"].queryset = categories
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("importe_total")
        if amount is not None:
            self.instance.saldo_pendiente = amount
            self.instance.estado = CuentaPorPagar.Estado.PENDIENTE
        return cleaned_data


class PayableFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Proveedor o concepto"}))
    proveedor = forms.ModelChoiceField(queryset=Proveedor.objects.none(), required=False, empty_label="Todos los proveedores")
    categoria = forms.ModelChoiceField(queryset=CategoriaCuentaPagar.objects.none(), required=False, empty_label="Todas las categorias")
    estado = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Todos los estados"),
            (CuentaPorPagar.Estado.PENDIENTE, "Pendiente"),
            (CuentaPorPagar.Estado.PARCIAL, "Parcial"),
            (CuentaPorPagar.Estado.PAGADA, "Pagada"),
            (CuentaPorPagar.Estado.ANULADA, "Anulada"),
            ("VENCIDA", "Vencida"),
        ],
    )
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proveedor"].queryset = Proveedor.objects.order_by("razon_social")
        self.fields["categoria"].queryset = CategoriaCuentaPagar.objects.order_by("nombre")
        self._apply_input_classes()


class PayableAnnulForm(TreasuryStyledFormMixin, forms.Form):
    motivo = forms.CharField(label="Motivo de anulacion", max_length=255, widget=forms.Textarea(attrs={"placeholder": "Explica por que se anula la obligacion"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class SupplierHistoryFilterForm(TreasuryStyledFormMixin, forms.Form):
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class PaymentBaseForm(TreasuryStyledFormMixin, forms.Form):
    cuenta_por_pagar = forms.ModelChoiceField(queryset=CuentaPorPagar.objects.none(), label="Cuenta por pagar")
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), label="Cuenta bancaria")
    fecha_pago = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    fecha_diferida = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}))
    referencia = forms.CharField(required=False, max_length=80, widget=forms.TextInput(attrs={"placeholder": "Referencia o comprobante"}))
    observaciones = forms.CharField(required=False, max_length=255, widget=forms.Textarea(attrs={"placeholder": "Observaciones del pago"}))
    medio_pago = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_por_pagar"].queryset = (
            CuentaPorPagar.objects.filter(estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL])
            .select_related("proveedor", "categoria")
            .order_by("fecha_vencimiento", "proveedor__razon_social")
        )
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        if self.medio_pago == PagoTesoreria.MedioPago.TRANSFERENCIA:
            cleaned_data["fecha_diferida"] = None
        return cleaned_data


class TransferPaymentForm(PaymentBaseForm):
    medio_pago = PagoTesoreria.MedioPago.TRANSFERENCIA

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["fecha_diferida"].widget = forms.HiddenInput()
        self.fields["fecha_diferida"].required = False


class ChequePaymentForm(PaymentBaseForm):
    medio_pago = PagoTesoreria.MedioPago.CHEQUE

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get("referencia") or "").strip():
            self.add_error("referencia", "La referencia es obligatoria para cheque.")
        return cleaned_data


class ECheqPaymentForm(PaymentBaseForm):
    medio_pago = PagoTesoreria.MedioPago.ECHEQ

    def clean(self):
        cleaned_data = super().clean()
        if not (cleaned_data.get("referencia") or "").strip():
            self.add_error("referencia", "La referencia es obligatoria para ECHEQ.")
        return cleaned_data


class PaymentFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Proveedor, referencia o concepto"}))
    medio_pago = forms.ChoiceField(required=False, choices=(("", "Todos los medios"),) + tuple(PagoTesoreria.MedioPago.choices))
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False, empty_label="Todas las cuentas")
    estado = forms.ChoiceField(required=False, choices=(("", "Todos"),) + tuple(PagoTesoreria.Estado.choices))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.order_by("banco", "nombre")
        self._apply_input_classes()


class PaymentAnnulForm(TreasuryStyledFormMixin, forms.Form):
    motivo = forms.CharField(label="Motivo de anulacion", max_length=255, widget=forms.Textarea(attrs={"placeholder": "Motivo de anulacion del pago"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class BankMovementForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = MovimientoBancario
        fields = ["cuenta_bancaria", "tipo", "fecha", "monto", "concepto", "referencia", "observaciones"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Comision bancaria / Intereses / etc."}),
            "referencia": forms.TextInput(attrs={"placeholder": "Nro de operacion"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas adicionales"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self._apply_input_classes()


class BankMovementFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Concepto o referencia"}))
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False, empty_label="Todas las cuentas")
    tipo = forms.ChoiceField(required=False, choices=(("", "Todos los tipos"),) + tuple(MovimientoBancario.Tipo.choices))
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.order_by("banco", "nombre")
        self._apply_input_classes()


class PosBatchForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = LotePOS
        fields = ["fecha_lote", "cuenta_bancaria", "total_lote", "terminal", "operador", "observaciones"]
        widgets = {
            "fecha_lote": forms.DateInput(attrs={"type": "date"}),
            "total_lote": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "terminal": forms.TextInput(attrs={"placeholder": "Nro de terminal (opcional)"}),
            "operador": forms.TextInput(attrs={"placeholder": "Visa / Master / etc."}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas del lote"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self._apply_input_classes()


class PosBatchFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Terminal u operador"}))
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False, empty_label="Todas las cuentas")
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.order_by("banco", "nombre")
        self._apply_input_classes()


class CardAccreditationForm(TreasuryStyledFormMixin, forms.Form):
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none())
    fecha_acreditacion = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    monto_neto = forms.DecimalField(max_digits=14, decimal_places=2, widget=forms.NumberInput(attrs={"step": "0.01"}))
    canal = forms.CharField(max_length=80, widget=forms.TextInput(attrs={"placeholder": "Visa / Prisma / etc."}))
    referencia_externa = forms.CharField(required=False, max_length=80)
    lote_pos = forms.ModelChoiceField(queryset=LotePOS.objects.none(), required=False, empty_label="Sin lote vinculado")
    
    # Simple discount fields for the "Easy" form (US-4.4)
    monto_descuentos = forms.DecimalField(required=False, max_digits=14, decimal_places=2, widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}))
    descripcion_descuentos = forms.CharField(required=False, max_length=160, widget=forms.TextInput(attrs={"placeholder": "IIBB / Comisiones"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self.fields["lote_pos"].queryset = LotePOS.objects.all()[:50] # Simplified
        self._apply_input_classes()


class CardAccreditationFilterForm(TreasuryStyledFormMixin, forms.Form):
    canal = forms.CharField(required=False)
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False)
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.all()
        self._apply_input_classes()


class BankReconciliationFilterForm(TreasuryStyledFormMixin, forms.Form):
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), label="Cuenta a conciliar")
    fecha_desde = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self._apply_input_classes()


# --- Flujo de Disponibilidades (EP-05) ---

class CentralCashMovementForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = MovimientoCajaCentral
        fields = ["fecha", "tipo", "monto", "concepto", "observaciones"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Aporte de capital / Ajuste / etc."}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas adicionales"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Exclude types that should be automatic (EGRESO_PAGO, INGRESO_CAJA)
        manual_choices = [
            (MovimientoCajaCentral.Tipo.APORTE, "Aporte de Socios/Capital"),
            (MovimientoCajaCentral.Tipo.RETIRO_BANCO, "Retiro de Banco (Efectivo)"),
            (MovimientoCajaCentral.Tipo.DEPOSITO_BANCO, "Deposito en Banco"),
            (MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO, "Ajuste de Saldo (+)"),
            (MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO, "Ajuste de Saldo (-)"),
        ]
        self.fields["tipo"].choices = manual_choices
        self._apply_input_classes()


class ArqueoForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = ArqueoDisponibilidades
        fields = ["saldo_contado_efectivo", "observaciones"]
        labels = {
            "saldo_contado_efectivo": "Efectivo Contado (Fisico)",
        }
        widgets = {
            "saldo_contado_efectivo": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas sobre el arqueo o diferencias"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class DisponibilidadesFilterForm(TreasuryStyledFormMixin, forms.Form):
    year = forms.IntegerField(label="Año", widget=forms.NumberInput(attrs={"placeholder": "2026"}))
    month = forms.ChoiceField(
        label="Mes",
        choices=[
            (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
            (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
            (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre")
        ]
    )
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.all(),
        required=False,
        label="Sucursal",
        empty_label="Todas las sucursales (Consolidado)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["year"].initial = today.year
        self.fields["month"].initial = today.month
        self._apply_input_classes()


class CashPaymentForm(TreasuryStyledFormMixin, forms.Form):
    cuenta_por_pagar = forms.ModelChoiceField(queryset=CuentaPorPagar.objects.none(), label="Cuenta por pagar")
    fecha_pago = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}))
    observaciones = forms.CharField(required=False, max_length=255, widget=forms.Textarea(attrs={"placeholder": "Observaciones del pago"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_por_pagar"].queryset = (
            CuentaPorPagar.objects.filter(estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL])
            .select_related("proveedor", "categoria")
            .order_by("fecha_vencimiento", "proveedor__razon_social")
        )
        self._apply_input_classes()
