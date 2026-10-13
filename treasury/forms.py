import uuid
from decimal import Decimal

from django import forms
from django.db.models import Q
from django.utils import timezone

from .models import (
    AcreditacionTarjeta,
    ArqueoDisponibilidades,
    CajaCentral,
    CategoriaCuentaPagar,
    CierreMensualTesoreria,
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    DescuentoAcreditacion,
    LotePOS,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
    SaldoInicialCuentaBancaria,
)
from .reportes.alcance import ReportScope, rango_de_meses
from .services import CLASE_POR_MEDIO_DE_PAGO
from cashops.models import Empresa, RubroOperativo, Sucursal


class AltaIdempotenteMixin:
    """Token de alta oculto, unico por render de formulario (mismo contrato que
    cashops.forms.AltaIdempotenteForm): si vuelve el mismo token (doble click,
    reintento tras un timeout, volver atras y reenviar), el servicio devuelve
    lo ya creado en lugar de mover plata de nuevo. El campo se agrega dinamico
    para servir igual en Form y en ModelForm; el partial de treasury ya
    renderiza form.hidden_fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["token_alta"] = forms.UUIDField(required=False, widget=forms.HiddenInput())
        # En un form ligado el valor sale de los datos enviados, asi que el
        # token sobrevive a un render con errores de validacion.
        if not self.is_bound:
            self.fields["token_alta"].initial = uuid.uuid4()

    def creation_token(self):
        return self.cleaned_data.get("token_alta")


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
        fields = ["nombre", "rubro_operativo", "activo"]
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Servicios, impuestos, mercaderia..."}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        rubros = RubroOperativo.objects.filter(activo=True, es_sistema=False).order_by("nombre")
        if self.instance.pk and self.instance.rubro_operativo_id:
            rubros = (RubroOperativo.objects.filter(pk=self.instance.rubro_operativo_id) | rubros).distinct()
        self.fields["rubro_operativo"].queryset = rubros
        self.fields["rubro_operativo"].required = False
        self.fields["rubro_operativo"].label = "Rubro operativo asociado"
        self.fields["rubro_operativo"].help_text = "Obligatorio para categorias activas y nuevas deudas comparables."
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        activo = cleaned_data.get("activo")
        rubro_operativo = cleaned_data.get("rubro_operativo")
        if activo and rubro_operativo is None:
            self.add_error("rubro_operativo", "El rubro operativo es obligatorio para categorias activas.")
        return cleaned_data


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
            "empresa",
            "sucursal",
            "activa",
        ]
        labels = {
            "empresa": "Empresa propietaria",
        }
        help_texts = {
            "empresa": "Duena de la cuenta. Las acreditaciones se leen como fondo comun de esta empresa.",
            "sucursal": "Opcional: solo si la cuenta es exclusiva de un local puntual.",
        }
        widgets = {
            "nombre": forms.TextInput(attrs={"placeholder": "Cuenta operativa"}),
            "banco": forms.TextInput(attrs={"placeholder": "Banco Galicia"}),
            "numero_cuenta": forms.TextInput(attrs={"placeholder": "123-456/7"}),
            "alias": forms.TextInput(attrs={"placeholder": "Alias opcional"}),
            "cbu": forms.TextInput(attrs={"placeholder": "CBU opcional"}),
            "sucursal_bancaria": forms.TextInput(attrs={"placeholder": "Sucursal"}),
        }

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        empresas = Empresa.objects.filter(activa=True).order_by("nombre")
        sucursales = Sucursal.objects.order_by("nombre")
        if empresa_ids is not None:
            empresas = empresas.filter(pk__in=empresa_ids)
            sucursales = sucursales.filter(empresa_id__in=empresa_ids)
        if self.instance.pk and self.instance.empresa_id:
            empresas = Empresa.objects.filter(
                pk__in=[*empresas.values_list("pk", flat=True), self.instance.empresa_id]
            ).order_by("nombre")
        self.fields["empresa"].queryset = empresas
        self.fields["empresa"].required = True
        self.fields["sucursal"].queryset = sucursales
        self._apply_input_classes()


class BankAccountFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Cuenta, banco, alias..."}))
    activa = forms.ChoiceField(required=False, choices=(("", "Todas"), ("1", "Activas"), ("0", "Inactivas")))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class InitialBankBalanceForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = SaldoInicialCuentaBancaria
        fields = ["cuenta_bancaria", "fecha_referencia", "importe", "motivo"]
        widgets = {
            "fecha_referencia": forms.DateInput(attrs={"type": "date"}),
            "importe": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "motivo": forms.Textarea(attrs={"placeholder": "Motivo de carga o correccion"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self.fields["fecha_referencia"].label = "Fecha de referencia"
        self.fields["importe"].label = "Saldo inicial"
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
            "periodo_referencia",
            "importe_total",
            "sucursal",
            "observaciones",
        ]
        widgets = {
            "concepto": forms.TextInput(attrs={"placeholder": "Factura de mercaderia"}),
            "referencia_comprobante": forms.TextInput(attrs={"placeholder": "Factura / VEP / referencia"}),
            "fecha_emision": forms.DateInput(attrs={"type": "date"}),
            "fecha_vencimiento": forms.DateInput(attrs={"type": "date"}),
            "periodo_referencia": forms.DateInput(attrs={"type": "date"}),
            "importe_total": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas internas"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        suppliers = Proveedor.objects.filter(activo=True).order_by("razon_social")
        categories = CategoriaCuentaPagar.objects.filter(
            activo=True,
            rubro_operativo__isnull=False,
        ).order_by("nombre")
        if self.instance.pk:
            suppliers = (Proveedor.objects.filter(pk=self.instance.proveedor_id) | suppliers).distinct()
            categories = (CategoriaCuentaPagar.objects.filter(pk=self.instance.categoria_id) | categories).distinct()
        self.fields["proveedor"].queryset = suppliers
        self.fields["categoria"].queryset = categories
        self.fields["categoria"].label = "Rubro / categoría"
        self.fields["categoria"].help_text = "Solo se pueden registrar deudas nuevas con categorías ya asociadas a rubro."
        self.fields["periodo_referencia"].label = "Período económico"
        self.fields["periodo_referencia"].required = False
        if not self.is_bound and not self.instance.pk:
            today = timezone.localdate()
            self.initial.setdefault("periodo_referencia", today.replace(day=1))
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        amount = cleaned_data.get("importe_total")
        issue_date = cleaned_data.get("fecha_emision")
        category = cleaned_data.get("categoria")
        if category and not category.rubro_operativo_id:
            self.add_error("categoria", "La categoría debe tener un rubro operativo asociado.")
        if issue_date and not cleaned_data.get("periodo_referencia"):
            cleaned_data["periodo_referencia"] = issue_date.replace(day=1)
        if amount is not None:
            self.instance.saldo_pendiente = amount
            self.instance.estado = CuentaPorPagar.Estado.PENDIENTE
        return cleaned_data


class PayableFilterForm(TreasuryStyledFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Proveedor o concepto"}))
    proveedor = forms.ModelChoiceField(queryset=Proveedor.objects.none(), required=False, empty_label="Todos los proveedores")
    categoria = forms.ModelChoiceField(queryset=CategoriaCuentaPagar.objects.none(), required=False, empty_label="Todas las categorías")
    rubro = forms.ModelChoiceField(queryset=RubroOperativo.objects.none(), required=False, empty_label="Todos los rubros")
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
        self.fields["rubro"].queryset = RubroOperativo.objects.filter(activo=True, es_sistema=False).order_by("nombre")
        self._apply_input_classes()


class PayableAnnulForm(TreasuryStyledFormMixin, forms.Form):
    motivo = forms.CharField(label="Motivo de anulación", max_length=255, widget=forms.Textarea(attrs={"placeholder": "Explicá por qué se anula la obligación"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class SupplierHistoryFilterForm(TreasuryStyledFormMixin, forms.Form):
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class TreasuryDashboardFilterForm(TreasuryStyledFormMixin, forms.Form):
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Vista consolidada")
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        fecha_desde = cleaned_data.get("fecha_desde")
        fecha_hasta = cleaned_data.get("fecha_hasta")
        if fecha_desde and fecha_hasta and fecha_hasta < fecha_desde:
            cleaned_data["fecha_desde"], cleaned_data["fecha_hasta"] = fecha_hasta, fecha_desde
        return cleaned_data


class ReportFilterForm(TreasuryStyledFormMixin, forms.Form):
    """Alcance y periodo de un reporte PDF.

    `alcance` junta empresa y sucursal en un solo valor, agrupado por razon social:
    "e<empresa>" general, "t<empresa>" general + cada sucursal en un mismo PDF,
    "s<sucursal>" una sucursal. Asi la sucursal siempre se valida contra la empresa
    (los snapshots ignoran empresa_ids cuando reciben una sucursal).
    """

    FUERA_DE_ALCANCE = "fuera_de_alcance"

    alcance = forms.ChoiceField(label="Empresa y alcance")
    mes_desde = forms.DateField(
        label="Desde",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
    )
    mes_hasta = forms.DateField(
        label="Hasta",
        input_formats=["%Y-%m"],
        widget=forms.DateInput(attrs={"type": "month"}, format="%Y-%m"),
    )

    def __init__(self, *args, empresas, usa_periodo=True, hoy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hoy = hoy or timezone.localdate()
        self.usa_periodo = usa_periodo
        self.empresas = {empresa.pk: empresa for empresa in empresas}
        self.sucursales_activas = {
            empresa_pk: list(
                Sucursal.objects.filter(empresa_id=empresa_pk, activa=True).order_by("nombre")
            )
            for empresa_pk in self.empresas
        }
        grupos = []
        for empresa in sorted(self.empresas.values(), key=lambda item: item.nombre):
            sucursales = self.sucursales_activas[empresa.pk]
            if len(sucursales) == 1:
                opciones = [(f"e{empresa.pk}", f"General · {sucursales[0].nombre} (única sucursal)")]
            else:
                opciones = [(f"e{empresa.pk}", "General (todas las sucursales)")]
                if sucursales:
                    opciones.append((f"t{empresa.pk}", "General y cada sucursal (un solo PDF)"))
                opciones += [(f"s{sucursal.pk}", f"Sucursal {sucursal.nombre}") for sucursal in sucursales]
            grupos.append((empresa.nombre, opciones))
        self.fields["alcance"].choices = grupos[0][1] if len(grupos) == 1 else grupos
        if usa_periodo:
            mes_actual = self.hoy.replace(day=1)
            self.fields["mes_desde"].initial = mes_actual
            self.fields["mes_hasta"].initial = mes_actual
        else:
            self.fields["mes_desde"].required = False
            self.fields["mes_hasta"].required = False
            self.fields["mes_desde"].label = "Desde (opcional)"
            self.fields["mes_hasta"].label = "Hasta (opcional)"
        self._apply_input_classes()

    def clean_alcance(self):
        # ChoiceField ya rechaza valores que no estan entre las opciones. Igual se
        # marca con un codigo propio para que la vista responda 404: una sucursal o
        # empresa ajena por URL no tiene que caer en silencio al general.
        valor = self.cleaned_data["alcance"]
        modo, pk = valor[:1], valor[1:]
        if modo == "s":
            for empresa_pk, sucursales in self.sucursales_activas.items():
                for sucursal in sucursales:
                    if str(sucursal.pk) == pk:
                        return {"modo": modo, "empresa": self.empresas[empresa_pk], "sucursal": sucursal}
        elif modo in {"e", "t"} and pk.isdigit() and int(pk) in self.empresas:
            return {"modo": modo, "empresa": self.empresas[int(pk)], "sucursal": None}
        raise forms.ValidationError("El alcance elegido no está disponible.", code=self.FUERA_DE_ALCANCE)

    def clean(self):
        cleaned_data = super().clean()
        mes_desde = cleaned_data.get("mes_desde")
        mes_hasta = cleaned_data.get("mes_hasta")
        if not self.usa_periodo and bool(mes_desde) != bool(mes_hasta):
            raise forms.ValidationError("Para filtrar por período completá los dos meses, o dejá los dos vacíos.")
        if mes_desde and mes_hasta:
            if mes_hasta < mes_desde:
                raise forms.ValidationError("El mes hasta no puede ser anterior al mes desde.")
            if mes_desde > self.hoy.replace(day=1):
                raise forms.ValidationError("El período no puede empezar en un mes futuro.")
        return cleaned_data

    def fuera_de_alcance(self) -> bool:
        return self.has_error("alcance", code=self.FUERA_DE_ALCANCE) or (
            "alcance" in self.errors and self.data.get(self.add_prefix("alcance")) not in (None, "")
        )

    def build_scopes(self, *, emitido=None):
        """Un ReportScope por documento: uno solo, o el general + cada sucursal."""
        alcance = self.cleaned_data["alcance"]
        empresa = alcance["empresa"]
        mes_desde = self.cleaned_data.get("mes_desde")
        mes_hasta = self.cleaned_data.get("mes_hasta")
        if self.usa_periodo:
            date_from, date_to = rango_de_meses(mes_desde, mes_hasta, hoy=self.hoy)
        else:
            date_from = mes_desde.replace(day=1) if mes_desde else None
            date_to = mes_hasta.replace(day=1) if mes_hasta else None
        sucursales = self.sucursales_activas[empresa.pk]
        comun = {"empresa": empresa, "date_from": date_from, "date_to": date_to, "emitido": emitido}
        if alcance["sucursal"] is not None:
            return [ReportScope(sucursal=alcance["sucursal"], **comun)]
        unica = sucursales[0] if len(sucursales) == 1 else None
        general = ReportScope(sucursal_unica=unica, todas_las_sucursales=alcance["modo"] == "t", **comun)
        if alcance["modo"] == "t":
            return [general] + [ReportScope(sucursal=sucursal, **comun) for sucursal in sucursales]
        return [general]


class SpecialCommitmentForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = CompromisoEspecial
        fields = [
            "tipo",
            "cuenta_por_pagar",
            "sucursal",
            "concepto",
            "organismo",
            "beneficiario",
            "expediente",
            "sustento_referencia",
            "periodo_fiscal",
            "fecha_compromiso",
            "vencimiento",
            "monto_estimado",
            "prioridad",
            "requiere_autorizacion",
            "plan_nombre",
            "numero_cuota",
            "total_cuotas",
            "capital",
            "interes_financiero",
            "interes_resarcitorio",
        ]
        widgets = {
            "periodo_fiscal": forms.DateInput(attrs={"type": "date"}),
            "fecha_compromiso": forms.DateInput(attrs={"type": "date"}),
            "vencimiento": forms.DateInput(attrs={"type": "date"}),
            "monto_estimado": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "capital": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "interes_financiero": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "interes_resarcitorio": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "concepto": forms.TextInput(attrs={"placeholder": "AFIP 931 / Embargo / Adelanto autorizado"}),
            "sustento_referencia": forms.TextInput(attrs={"placeholder": "VEP, expediente, acta o autorizacion"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_por_pagar"].queryset = (
            CuentaPorPagar.objects.filter(
                estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL],
                compromiso_especial__isnull=True,
            )
            .select_related("proveedor", "categoria")
            .order_by("fecha_vencimiento", "proveedor__razon_social")
        )
        self.fields["cuenta_por_pagar"].required = False
        self.fields["cuenta_por_pagar"].empty_label = "Sin deuda vinculada"
        self.fields["sucursal"].queryset = Sucursal.objects.filter(activa=True).order_by("nombre")
        self.fields["sucursal"].required = False
        self.fields["sucursal"].empty_label = "Sin sucursal"
        for field_name in (
            "organismo",
            "beneficiario",
            "expediente",
            "periodo_fiscal",
            "vencimiento",
            "plan_nombre",
            "numero_cuota",
            "total_cuotas",
            "capital",
            "interes_financiero",
            "interes_resarcitorio",
        ):
            self.fields[field_name].required = False
        for field_name in ("capital", "interes_financiero", "interes_resarcitorio"):
            self.fields[field_name].initial = Decimal("0.00")
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo")
        payable = cleaned_data.get("cuenta_por_pagar")
        if tipo != CompromisoEspecial.Tipo.REQUERIMIENTO and payable is None:
            self.add_error("cuenta_por_pagar", "El compromiso debe vincular una cuenta por pagar.")
        if payable is not None:
            amount = cleaned_data.get("monto_estimado")
            if amount is not None and amount != payable.importe_total:
                self.add_error("monto_estimado", "El monto debe coincidir con la cuenta por pagar vinculada.")
        for field_name in ("capital", "interes_financiero", "interes_resarcitorio"):
            if cleaned_data.get(field_name) is None:
                cleaned_data[field_name] = Decimal("0.00")
        return cleaned_data


class SpecialCommitmentFilterForm(TreasuryStyledFormMixin, forms.Form):
    tipo = forms.ChoiceField(required=False, choices=(("", "Todos los tipos"),) + tuple(CompromisoEspecial.Tipo.choices))
    estado = forms.ChoiceField(required=False, choices=(("", "Todos los estados"),) + tuple(CompromisoEspecial.Estado.choices))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class SpecialCommitmentDecisionForm(TreasuryStyledFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=(("approve", "Aprobar"), ("reject", "Rechazar")))
    comentario = forms.CharField(required=False, max_length=255, widget=forms.Textarea(attrs={"placeholder": "Comentario de auditoria"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("decision") == "reject" and not (cleaned_data.get("comentario") or "").strip():
            self.add_error("comentario", "El comentario es obligatorio para rechazar.")
        return cleaned_data


class PayableChoiceField(forms.ModelChoiceField):
    """Muestra el saldo pendiente en cada opcion: con cientos de deudas abiertas,
    'Proveedor - concepto' sin importe no alcanza para elegir bien."""

    def label_from_instance(self, obj):
        etiqueta = f"{obj.proveedor} - {obj.concepto}"
        if obj.referencia_comprobante:
            etiqueta = f"{etiqueta} ({obj.referencia_comprobante})"
        return f"{etiqueta} - saldo ${obj.saldo_pendiente}"


def open_payables_queryset(empresa_ids=None):
    """Deudas que todavia se deben (PENDIENTE/PARCIAL), acotadas a las empresas
    seleccionadas. Se incluyen las deudas legacy sin sucursal, igual que el
    listado de cuentas por pagar. empresa_ids=None significa 'sin filtro';
    una lista vacia significa 'ninguna empresa seleccionada' -> nada."""
    queryset = (
        CuentaPorPagar.objects.filter(
            estado__in=[CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL]
        )
        .select_related("proveedor", "categoria", "sucursal", "caja_origen")
        .order_by("fecha_vencimiento", "proveedor__razon_social")
    )
    if empresa_ids is not None:
        if not empresa_ids:
            return queryset.none()
        queryset = queryset.filter(
            Q(sucursal__empresa_id__in=empresa_ids) | Q(sucursal__isnull=True)
        )
    return queryset


class PaymentBaseForm(AltaIdempotenteMixin, TreasuryStyledFormMixin, forms.Form):
    cuenta_por_pagar = PayableChoiceField(queryset=CuentaPorPagar.objects.none(), label="Cuenta por pagar")
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), label="Cuenta bancaria")
    fecha_pago = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    fecha_diferida = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}))
    referencia = forms.CharField(required=False, max_length=80, widget=forms.TextInput(attrs={"placeholder": "Referencia o comprobante"}))
    observaciones = forms.CharField(required=False, max_length=255, widget=forms.Textarea(attrs={"placeholder": "Observaciones del pago"}))
    medio_pago = ""

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_por_pagar"].queryset = open_payables_queryset(empresa_ids)
        cuentas = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        if empresa_ids is not None:
            # El helper ya devuelve un Q vacio (nada) cuando la lista esta vacia.
            from .services import bank_account_empresa_scope_query

            cuentas = cuentas.filter(bank_account_empresa_scope_query(empresa_ids))
        self.fields["cuenta_bancaria"].queryset = cuentas
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
    # US-3.20: sin elegir medio, el listado muestra solo lo que movio plata; las
    # cancelaciones sin movimiento se ven eligiendo su medio. Las opciones se
    # arman en __init__ (ver _medios_ofrecidos).
    medio_pago = forms.ChoiceField(required=False, choices=())
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False, empty_label="Todas las cuentas")
    estado = forms.ChoiceField(required=False, choices=(("", "Todos"),) + tuple(PagoTesoreria.Estado.choices))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medio_pago"].choices = [
            ("", "Todos los medios con movimiento de fondos"),
            *self._medios_ofrecidos(),
        ]
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.order_by("banco", "nombre")
        self._apply_input_classes()

    @staticmethod
    def _medios_ofrecidos():
        """REGULARIZACION quedo definido en el modelo pero sin uso: lo que no se
        pago por banco se registra como pago en efectivo. No se ofrece, para no
        sugerir un camino que no existe, salvo que haya alguno cargado: asi el
        filtro nunca esconde un pago del listado."""
        regularizacion = PagoTesoreria.MedioPago.REGULARIZACION
        hay_regularizaciones = PagoTesoreria.objects.filter(medio_pago=regularizacion).exists()
        return [
            (valor, etiqueta)
            for valor, etiqueta in PagoTesoreria.MedioPago.choices
            if valor != regularizacion or hay_regularizaciones
        ]


class PaymentAnnulForm(TreasuryStyledFormMixin, forms.Form):
    motivo = forms.CharField(label="Motivo de anulacion", max_length=255, widget=forms.Textarea(attrs={"placeholder": "Motivo de anulacion del pago"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class BankMovementForm(AltaIdempotenteMixin, TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = MovimientoBancario
        fields = [
            "cuenta_bancaria",
            "tipo",
            "clase",
            "rubro_operativo",
            "proveedor",
            "sucursal_gasto",
            "periodo_pago",
            "fecha",
            "monto",
            "concepto",
            "referencia",
            "observaciones",
        ]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "periodo_pago": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Comision bancaria / Intereses / etc."}),
            "referencia": forms.TextInput(attrs={"placeholder": "Nro de operacion"}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas adicionales"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        self.fields["rubro_operativo"].queryset = RubroOperativo.objects.filter(activo=True, es_sistema=False).order_by("nombre")
        self.fields["proveedor"].queryset = Proveedor.objects.filter(activo=True).order_by("razon_social")
        self.fields["sucursal_gasto"].queryset = Sucursal.objects.filter(activa=True).order_by("nombre")
        self.fields["rubro_operativo"].required = False
        self.fields["proveedor"].required = False
        self.fields["sucursal_gasto"].required = False
        self.fields["periodo_pago"].required = False
        self.fields["sucursal_gasto"].label = "Sucursal"
        self.fields["sucursal_gasto"].empty_label = "Sin asignar"
        self.fields["clase"].label = "Tipo financiero"
        self.fields["rubro_operativo"].label = "Rubro"
        self.fields["rubro_operativo"].empty_label = "Sin asignar"
        self.fields["periodo_pago"].label = "Periodo que se esta pagando"
        self.fields["periodo_pago"].help_text = "Obligatorio en egresos: mes al que corresponde el gasto."
        tipo_actual = self.data.get(self.add_prefix("tipo")) if self.is_bound else None
        self.show_sucursal_field = tipo_actual == MovimientoBancario.Tipo.DEBITO or not tipo_actual
        self.conditional_sucursal = True
        self.sucursal_source_field_id = self["tipo"].id_for_label
        self.sucursal_field_id = self["sucursal_gasto"].id_for_label
        self.sucursal_field_name = "sucursal_gasto"
        self.sucursal_required_value = MovimientoBancario.Tipo.DEBITO
        self.conditional_periodo = True
        self.periodo_field_id = self["periodo_pago"].id_for_label
        self.periodo_field_name = "periodo_pago"
        self._apply_input_classes()

    def clean_periodo_pago(self):
        periodo = self.cleaned_data.get("periodo_pago")
        if periodo:
            return periodo.replace(day=1)
        return periodo

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("tipo") != MovimientoBancario.Tipo.DEBITO:
            cleaned_data["sucursal_gasto"] = None
            cleaned_data["periodo_pago"] = None
        return cleaned_data


class BankMovementFilterForm(TreasuryStyledFormMixin, forms.Form):
    IMPUTACION_CHOICES = (
        ("", "Imputacion: todas"),
        ("pendientes", "Egresos pendientes de imputacion"),
        ("imputados", "Egresos imputados completos"),
    )

    q = forms.CharField(required=False, label="Buscar", widget=forms.TextInput(attrs={"placeholder": "Concepto o referencia"}))
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none(), required=False, empty_label="Todas las cuentas")
    tipo = forms.ChoiceField(required=False, choices=(("", "Todos los tipos"),) + tuple(MovimientoBancario.Tipo.choices))
    clase = forms.ChoiceField(required=False, choices=(("", "Todos los tipos financieros"),) + tuple(MovimientoBancario.Clase.choices))
    fecha_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    fecha_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all(), required=False, empty_label="Todas las sucursales")
    imputacion = forms.ChoiceField(required=False, choices=IMPUTACION_CHOICES, label="Imputacion")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_bancaria"].queryset = CuentaBancaria.objects.order_by("banco", "nombre")
        self._apply_input_classes()


class BankMovementImputationForm(TreasuryStyledFormMixin, forms.ModelForm):
    class Meta:
        model = MovimientoBancario
        fields = ["rubro_operativo", "sucursal_gasto", "periodo_pago"]
        widgets = {
            "periodo_pago": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["rubro_operativo"].queryset = RubroOperativo.objects.filter(activo=True, es_sistema=False).order_by("nombre")
        sucursales = Sucursal.objects.filter(activa=True).order_by("nombre")
        account_empresa_id = (
            self.instance.cuenta_bancaria.empresa_id
            if self.instance.pk and self.instance.cuenta_bancaria_id
            else None
        )
        if account_empresa_id:
            sucursales = sucursales.filter(empresa_id=account_empresa_id)
        elif empresa_ids is not None:
            sucursales = sucursales.filter(empresa_id__in=empresa_ids)
        self.fields["sucursal_gasto"].queryset = sucursales
        self.fields["rubro_operativo"].required = True
        self.fields["sucursal_gasto"].required = True
        self.fields["periodo_pago"].required = True
        self.fields["rubro_operativo"].label = "Rubro"
        self.fields["sucursal_gasto"].label = "Sucursal correspondiente"
        self.fields["periodo_pago"].label = "Periodo que se esta pagando"
        self._apply_input_classes()

    def clean_periodo_pago(self):
        periodo = self.cleaned_data.get("periodo_pago")
        if periodo:
            return periodo.replace(day=1)
        return periodo


class BankPaymentMethodCorrectionForm(TreasuryStyledFormMixin, forms.Form):
    """US-4.11: corregir con que instrumento se pago una deuda ya registrada.

    Las opciones se muestran con el texto del tipo financiero del movimiento
    ("Egreso por cheque"), que es lo que la persona lee en la pantalla de donde
    viene, pero el valor que viaja es el medio de pago del PagoTesoreria, que es
    la fuente de verdad de la que se deriva la clase (services.CLASE_POR_MEDIO_DE_PAGO).
    """

    MEDIOS_CON_REFERENCIA = {PagoTesoreria.MedioPago.CHEQUE, PagoTesoreria.MedioPago.ECHEQ}

    medio_pago = forms.ChoiceField(label="Tipo financiero", choices=())
    referencia = forms.CharField(
        label="Referencia",
        max_length=80,
        required=False,
        help_text="Nro de cheque, ECHEQ u operacion. Obligatoria para cheque y ECHEQ.",
        widget=forms.TextInput(attrs={"placeholder": "Nro de cheque / operacion"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medio_pago"].choices = [
            (medio, MovimientoBancario.Clase(clase).label)
            for medio, clase in CLASE_POR_MEDIO_DE_PAGO.items()
        ]
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        medio_pago = cleaned_data.get("medio_pago")
        referencia = (cleaned_data.get("referencia") or "").strip()
        cleaned_data["referencia"] = referencia
        if medio_pago in self.MEDIOS_CON_REFERENCIA and not referencia:
            self.add_error("referencia", "La referencia es obligatoria para cheque y ECHEQ.")
        return cleaned_data


class BankMovementAnnulForm(TreasuryStyledFormMixin, forms.Form):
    motivo = forms.CharField(
        label="Motivo de eliminación",
        max_length=255,
        widget=forms.Textarea(attrs={"placeholder": "Explicá por qué se elimina este movimiento"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._apply_input_classes()


class CentralCashMovementAnnulForm(TreasuryStyledFormMixin, forms.Form):
    # Sin max_length: el campo del modelo es TextField, para no cortarle la
    # explicacion a quien tiene que justificar por que saca plata de la boveda.
    motivo = forms.CharField(
        label="Motivo de la anulacion",
        widget=forms.Textarea(attrs={"placeholder": "Explicá por qué se anula este movimiento"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
    modo_registro = forms.ChoiceField(
        choices=AcreditacionTarjeta.ModoRegistro.choices,
        label="Modo de carga",
    )
    cuenta_bancaria = forms.ModelChoiceField(queryset=CuentaBancaria.objects.none())
    fecha_acreditacion = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    periodo_desde = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    periodo_hasta = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
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
        self.fields["lote_pos"].queryset = LotePOS.objects.all().order_by("-fecha_lote", "-id")[:50]
        self._apply_input_classes()

    def clean(self):
        cleaned_data = super().clean()
        modo_registro = cleaned_data.get("modo_registro")
        periodo_desde = cleaned_data.get("periodo_desde")
        periodo_hasta = cleaned_data.get("periodo_hasta")
        if modo_registro == AcreditacionTarjeta.ModoRegistro.PERIODO:
            if not periodo_desde:
                self.add_error("periodo_desde", "La fecha desde es obligatoria para carga agrupada.")
            if not periodo_hasta:
                self.add_error("periodo_hasta", "La fecha hasta es obligatoria para carga agrupada.")
            if periodo_desde and periodo_hasta and periodo_hasta < periodo_desde:
                self.add_error("periodo_hasta", "La fecha hasta no puede ser anterior a la fecha desde.")
        else:
            cleaned_data["periodo_desde"] = None
            cleaned_data["periodo_hasta"] = None
        return cleaned_data


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

class BovedaEmpresaFieldMixin:
    """Agrega el selector de empresa a los formularios que mueven la boveda.

    Ahora hay una boveda de efectivo por empresa, asi que toda escritura tiene
    que decir de cual se trata: antes todo caia en una caja global sin dueno y
    esos movimientos se contaban en las dos empresas a la vez. Si el usuario
    tiene una sola empresa habilitada se preselecciona y no hay nada que elegir.
    """

    def _setup_empresa_field(self, empresa_ids=None):
        empresas = Empresa.objects.filter(activa=True).order_by("nombre")
        if empresa_ids is not None:
            empresas = empresas.filter(pk__in=empresa_ids)
        campo = self.fields["empresa"]
        campo.queryset = empresas
        campo.required = True
        if len(empresas) == 1:
            unica = empresas[0]
            campo.initial = unica.pk
            campo.empty_label = None
            campo.widget.attrs["data-unica-empresa"] = "1"


class CentralCashMovementForm(AltaIdempotenteMixin, BovedaEmpresaFieldMixin, TreasuryStyledFormMixin, forms.ModelForm):
    empresa = forms.ModelChoiceField(
        queryset=Empresa.objects.none(),
        label="Empresa",
        help_text="De que boveda de efectivo sale o entra este movimiento.",
    )

    class Meta:
        model = MovimientoCajaCentral
        fields = ["fecha", "tipo", "monto", "concepto", "observaciones"]
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "monto": forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
            "concepto": forms.TextInput(attrs={"placeholder": "Aporte de capital / Ajuste / etc."}),
            "observaciones": forms.Textarea(attrs={"placeholder": "Notas adicionales"}),
        }

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Exclude types that should be automatic (EGRESO_PAGO, INGRESO_CAJA)
        manual_choices = [
            (MovimientoCajaCentral.Tipo.APORTE, "Aporte de Socios/Capital"),
            (MovimientoCajaCentral.Tipo.RETIRO_BANCO, "Retiro de Banco (Efectivo)"),
            (MovimientoCajaCentral.Tipo.DEPOSITO_BANCO, "Depósito en Banco"),
            (MovimientoCajaCentral.Tipo.AJUSTE_POSITIVO, "Ajuste de Saldo (+)"),
            (MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO, "Ajuste de Saldo (-)"),
        ]
        self.fields["tipo"].choices = manual_choices
        self._setup_empresa_field(empresa_ids)
        self._apply_input_classes()


class CargaInicialCajaCentralForm(AltaIdempotenteMixin, BovedaEmpresaFieldMixin, TreasuryStyledFormMixin, forms.Form):
    empresa = forms.ModelChoiceField(
        queryset=Empresa.objects.none(),
        label="Empresa",
        help_text="De que boveda de efectivo es este saldo inicial.",
    )
    fecha = forms.DateField(
        label="Fecha",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Importe",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    motivo = forms.CharField(
        max_length=160,
        label="Motivo",
        widget=forms.TextInput(attrs={"placeholder": "Puesta en marcha, ajuste auditado..."}),
        help_text="Queda registrado como concepto del movimiento y es obligatorio.",
    )
    observaciones = forms.CharField(
        max_length=255,
        required=False,
        label="Observaciones adicionales",
        widget=forms.Textarea(attrs={"placeholder": "Contexto adicional (opcional)"}),
    )

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        import datetime
        self.fields["fecha"].initial = datetime.date.today()
        self._setup_empresa_field(empresa_ids)
        self._apply_input_classes()


class EgresoTesoreriaForm(AltaIdempotenteMixin, BovedaEmpresaFieldMixin, TreasuryStyledFormMixin, forms.Form):
    FUENTE_CAJA = "CAJA_CENTRAL"
    FUENTE_BANCO = "BANCO"
    FUENTE_CHOICES = [
        (FUENTE_CAJA, "Caja fuerte central (efectivo)"),
        (FUENTE_BANCO, "Cuenta bancaria"),
    ]

    empresa = forms.ModelChoiceField(
        queryset=Empresa.objects.none(),
        label="Empresa",
        help_text="De que empresa es este gasto. Acota la boveda, las cuentas y las sucursales.",
    )
    fuente = forms.ChoiceField(
        choices=FUENTE_CHOICES,
        label="Origen del egreso",
        help_text="Si sale de caja central reduce el libro de efectivo. Si sale de banco impacta el libro bancario.",
    )
    cuenta_bancaria = forms.ModelChoiceField(
        queryset=CuentaBancaria.objects.none(),
        required=False,
        label="Cuenta bancaria",
        empty_label="Seleccionar cuenta...",
    )
    fecha = forms.DateField(
        label="Fecha",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    monto = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Importe",
        widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}),
    )
    rubro = forms.ModelChoiceField(
        queryset=RubroOperativo.objects.none(),
        label="Rubro",
    )
    concepto = forms.CharField(
        max_length=160,
        label="Concepto",
        widget=forms.TextInput(attrs={"placeholder": "Pago de servicio, honorario, insumo administrativo..."}),
    )
    sucursal = forms.ModelChoiceField(
        queryset=Sucursal.objects.none(),
        label="Sucursal correspondiente",
        help_text="A qué local o unidad corresponde este gasto, aunque el dinero salga de tesorería central.",
    )
    periodo = forms.DateField(
        label="Periodo que se está pagando",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Primer día del mes al que corresponde el gasto.",
    )
    observaciones = forms.CharField(
        max_length=255,
        required=False,
        label="Observaciones",
        widget=forms.Textarea(attrs={"placeholder": "Detalle adicional (opcional)"}),
    )

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        import datetime
        today = datetime.date.today()
        self.fields["fecha"].initial = today
        self.fields["periodo"].initial = today.replace(day=1)
        cuentas = CuentaBancaria.objects.filter(activa=True)
        sucursales = Sucursal.objects.filter(activa=True)
        # Un gasto no puede cruzar de empresa: ni imputarse a una sucursal ajena
        # ni salir de una cuenta ajena. Antes las dos listas venian completas.
        if empresa_ids is not None:
            cuentas = cuentas.filter(empresa_id__in=empresa_ids)
            sucursales = sucursales.filter(empresa_id__in=empresa_ids)
        self.fields["cuenta_bancaria"].queryset = cuentas.order_by("banco", "nombre")
        self.fields["rubro"].queryset = RubroOperativo.objects.filter(activo=True, es_sistema=False).order_by("nombre")
        self.fields["sucursal"].queryset = sucursales.order_by("nombre")
        self._setup_empresa_field(empresa_ids)
        selected_source = self.data.get(self.add_prefix("fuente")) if self.is_bound else self.initial.get("fuente")
        self.show_bank_account_field = selected_source == self.FUENTE_BANCO
        self.conditional_bank_account = True
        self.bank_account_field_name = "cuenta_bancaria"
        self.bank_account_source_field_id = self["fuente"].id_for_label
        self.bank_account_field_id = self["cuenta_bancaria"].id_for_label
        self.bank_account_required_value = self.FUENTE_BANCO
        if not self.show_bank_account_field:
            self.fields["cuenta_bancaria"].widget.attrs["disabled"] = "disabled"
        self._apply_input_classes()

    def clean_periodo(self):
        periodo = self.cleaned_data.get("periodo")
        if periodo:
            return periodo.replace(day=1)
        return periodo

    def clean(self):
        cleaned_data = super().clean()
        fuente = cleaned_data.get("fuente")
        cuenta_bancaria = cleaned_data.get("cuenta_bancaria")
        if fuente == self.FUENTE_BANCO and not cuenta_bancaria:
            self.add_error("cuenta_bancaria", "La cuenta bancaria es obligatoria cuando el egreso sale de banco.")
        elif fuente != self.FUENTE_BANCO:
            cleaned_data["cuenta_bancaria"] = None
        return cleaned_data


class ArqueoForm(BovedaEmpresaFieldMixin, TreasuryStyledFormMixin, forms.ModelForm):
    empresa = forms.ModelChoiceField(
        queryset=Empresa.objects.none(),
        label="Empresa",
        help_text="Que boveda se esta contando. Cada empresa tiene la suya.",
    )

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

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._setup_empresa_field(empresa_ids)
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
    imputacion = forms.ChoiceField(
        label="Imputacion",
        required=False,
        choices=[
            ("", "Todos los movimientos"),
            ("pendientes", "Pendientes de imputacion"),
            ("imputados", "Egresos imputados completos"),
        ],
    )

    def __init__(self, *args, empresa_ids=None, include_imputacion=False, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        self.fields["year"].initial = today.year
        self.fields["month"].initial = today.month
        if not include_imputacion:
            self.fields.pop("imputacion")
        if empresa_ids is not None:
            self.fields["sucursal"].queryset = Sucursal.objects.filter(
                empresa_id__in=empresa_ids
            ).order_by("nombre")
        else:
            self.fields["sucursal"].queryset = Sucursal.objects.all().order_by("nombre")
        self._apply_input_classes()


class CashPaymentForm(AltaIdempotenteMixin, TreasuryStyledFormMixin, forms.Form):
    cuenta_por_pagar = PayableChoiceField(queryset=CuentaPorPagar.objects.none(), label="Cuenta por pagar")
    fecha_pago = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    monto = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0.01"), widget=forms.NumberInput(attrs={"step": "0.01", "placeholder": "0.00"}))
    observaciones = forms.CharField(required=False, max_length=255, widget=forms.Textarea(attrs={"placeholder": "Observaciones del pago"}))

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cuenta_por_pagar"].queryset = open_payables_queryset(empresa_ids)
        self._apply_input_classes()


class SupplierPickerForm(TreasuryStyledFormMixin, forms.Form):
    """Paso 1 del pago por proveedor: solo se ofrecen proveedores que tienen
    facturas impagas dentro de las empresas seleccionadas."""

    proveedor = forms.ModelChoiceField(
        queryset=Proveedor.objects.none(),
        label="Proveedor",
        help_text="Se listan solo los proveedores con facturas impagas.",
    )

    def __init__(self, *args, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        proveedor_ids = open_payables_queryset(empresa_ids).values_list("proveedor_id", flat=True)
        self.fields["proveedor"].queryset = Proveedor.objects.filter(
            pk__in=proveedor_ids
        ).order_by("razon_social")
        self._apply_input_classes()


class SupplierPaymentBatchForm(AltaIdempotenteMixin, TreasuryStyledFormMixin, forms.Form):
    """Paso 2 del pago por proveedor: una linea por factura impaga (tildar +
    importe editable, precargado con el saldo) mas los datos comunes del pago.

    Los campos por factura se crean dinamicamente en __init__ como pagar_<pk> /
    monto_<pk>, y `lineas_seleccionadas()` devuelve [(deuda, monto)] listo para
    register_supplier_payment_batch.

    Solo TRANSFERENCIA y EFECTIVO: cheque y ECHEQ son instrumentos individuales
    (cada uno con su numero de referencia), asi que se siguen cargando de a uno
    en las pantallas existentes.
    """

    MEDIOS_HABILITADOS = (
        (PagoTesoreria.MedioPago.TRANSFERENCIA, "Transferencia"),
        (PagoTesoreria.MedioPago.EFECTIVO, "Efectivo"),
    )

    medio_pago = forms.ChoiceField(choices=MEDIOS_HABILITADOS, label="Medio de pago")
    cuenta_bancaria = forms.ModelChoiceField(
        queryset=CuentaBancaria.objects.none(),
        label="Cuenta bancaria",
        required=False,
        help_text="Obligatoria para transferencia. En efectivo sale de la caja central.",
    )
    fecha_pago = forms.DateField(label="Fecha de pago", widget=forms.DateInput(attrs={"type": "date"}))
    referencia = forms.CharField(
        required=False,
        max_length=60,
        label="Referencia",
        help_text="Opcional. Si pagás varias facturas se numera por factura.",
        widget=forms.TextInput(attrs={"placeholder": "Nro de transferencia o comprobante"}),
    )
    observaciones = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.Textarea(attrs={"placeholder": "Observaciones del pago"}),
    )
    # Solo se muestra cuando el lote trae dos lineas que parecen la misma
    # factura. Se tilda a mano para cobrarlas las dos.
    confirmar_duplicado = forms.BooleanField(
        required=False,
        label="Son facturas distintas, pagar las dos igual",
        widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
    )

    def __init__(self, *args, proveedor=None, empresa_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.proveedor = proveedor
        # Lo llena clean(); el template muestra el tilde de confirmacion solo si
        # hay algo que confirmar.
        self.duplicados_detectados = []
        self.payables = list(
            open_payables_queryset(empresa_ids).filter(proveedor=proveedor)
        ) if proveedor else []

        cuentas = CuentaBancaria.objects.filter(activa=True).order_by("banco", "nombre")
        if empresa_ids is not None:
            from .services import bank_account_empresa_scope_query

            cuentas = cuentas.filter(bank_account_empresa_scope_query(empresa_ids))
        self.fields["cuenta_bancaria"].queryset = cuentas

        for payable in self.payables:
            self.fields[f"pagar_{payable.pk}"] = forms.BooleanField(
                required=False,
                label="Pagar",
                widget=forms.CheckboxInput(attrs={"class": "checkbox"}),
            )
            self.fields[f"monto_{payable.pk}"] = forms.DecimalField(
                required=False,
                max_digits=14,
                decimal_places=2,
                min_value=Decimal("0.01"),
                max_value=payable.saldo_pendiente,
                initial=payable.saldo_pendiente,
                label="Importe a pagar",
                widget=forms.NumberInput(attrs={"step": "0.01", "class": "input"}),
            )
        self._apply_input_classes()

    CAMPOS_COMUNES = ("medio_pago", "cuenta_bancaria", "fecha_pago", "referencia", "observaciones")

    def campos_comunes(self):
        """Los campos del pago (no las lineas por factura), para el template."""
        return [self[nombre] for nombre in self.CAMPOS_COMUNES]

    def filas(self):
        """Filas para el template: la factura con sus dos campos ya ligados."""
        for payable in self.payables:
            yield {
                "payable": payable,
                "check": self[f"pagar_{payable.pk}"],
                "monto": self[f"monto_{payable.pk}"],
            }

    def lineas_seleccionadas(self):
        lineas = []
        for payable in self.payables:
            if not self.cleaned_data.get(f"pagar_{payable.pk}"):
                continue
            monto = self.cleaned_data.get(f"monto_{payable.pk}")
            lineas.append((payable, monto if monto else payable.saldo_pendiente))
        return lineas

    @property
    def total_seleccionado(self):
        if not self.is_bound or not self.is_valid():
            return Decimal("0.00")
        return sum((monto for _, monto in self.lineas_seleccionadas()), Decimal("0.00"))

    def clean(self):
        cleaned_data = super().clean()
        if not self.payables:
            raise forms.ValidationError("El proveedor no tiene facturas impagas.")

        seleccionadas = [p for p in self.payables if cleaned_data.get(f"pagar_{p.pk}")]
        if not seleccionadas:
            raise forms.ValidationError("Tildá al menos una factura para pagar.")

        # Los 10 pagos dobles de produccion salieron asi: dos lineas iguales
        # tildadas en el mismo lote. Se corta salvo que se confirme aparte.
        from .services import lineas_que_parecen_la_misma_factura

        self.duplicados_detectados = lineas_que_parecen_la_misma_factura(seleccionadas)
        if self.duplicados_detectados and not cleaned_data.get("confirmar_duplicado"):
            detalle = "; ".join(
                " y ".join(f"#{p.pk} {p.concepto}" for p in lineas)
                for lineas in self.duplicados_detectados
            )
            raise forms.ValidationError(
                f"Estas tildando facturas que parecen la misma: {detalle}. Misma sucursal, "
                "misma fecha de factura y mismo importe. Dejá una sola tildada, o marcá "
                "«Son facturas distintas» si de verdad son dos."
            )

        for payable in seleccionadas:
            campo = f"monto_{payable.pk}"
            monto = cleaned_data.get(campo)
            if monto is None:
                cleaned_data[campo] = payable.saldo_pendiente
            elif monto > payable.saldo_pendiente:
                self.add_error(
                    campo,
                    f"No podés pagar más que el saldo pendiente (${payable.saldo_pendiente}).",
                )

        if cleaned_data.get("medio_pago") == PagoTesoreria.MedioPago.TRANSFERENCIA and not cleaned_data.get(
            "cuenta_bancaria"
        ):
            self.add_error("cuenta_bancaria", "Elegí la cuenta bancaria de la transferencia.")
        if cleaned_data.get("medio_pago") == PagoTesoreria.MedioPago.EFECTIVO:
            cleaned_data["cuenta_bancaria"] = None
        return cleaned_data
