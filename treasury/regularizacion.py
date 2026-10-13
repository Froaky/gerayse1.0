"""Regularizacion de deudas viejas de proveedores (US-3.20).

Tesoreria declaro el 2026-10-13 que las facturas de proveedores de todas las
sucursales, salvo impuestos y sueldos, estan pagadas hasta el 23/09/2026. Maria
ya cargo el banco de esos meses atando los pagos a las facturas, asi que lo que
sigue abierto se pago en EFECTIVO desde la caja fuerte. Por eso la boveda de
Gerayse da de mas: esos pagos nunca se registraron.

Modos (el comando `regularizar_deudas` es un envoltorio fino de este modulo):

- General: registra PAGOS EN EFECTIVO reales con `register_payment` (medio
  EFECTIVO), el mismo camino que la pantalla de pago en efectivo. Cada pago crea
  su EGRESO_PAGO en la boveda de la empresa de la deuda, asi que la caja fuerte
  baja a la realidad. El gasto economico no se duplica: lo cuenta la factura, y
  EGRESO_PAGO no es gasto. La fecha del pago es la de la factura (o una fija).
- Interno (`proveedor_interno`): la panaderia propia nunca se paga. Cancela
  todas sus deudas abiertas con el medio INTERNO, que no mueve fondos, y con
  `--apply` marca al proveedor como interno.
- Reversion (`Reversion`): anula con `annul_payment` los pagos de un lote, que
  devuelve el efectivo a la boveda y reabre las deudas.

El medio REGULARIZACION no se usa: queda definido en el modelo pero sin uso.

Flujo en dos pasos, como el importador de planillas: `planificar()` no escribe
nada y deja una fila por deuda con su accion y su motivo, que sale en el
informe CSV para revisar; `aplicar()` registra deuda por deuda, cada una en su
propia transaccion y con la deuda bloqueada. Con el plan revisado
(`restringir_a_plan`) se aplica solo lo que tesoreria aprobo y con el mismo
saldo. El token de cada pago sale del lote, el medio y la deuda: correrlo dos
veces no duplica, y un pago del lote que se anulo no se vuelve a registrar solo.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, Exists, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from treasury.importacion_texto import dinero, normalizar, parsear_monto
from treasury.models import (
    CajaCentral,
    CierreMensualTesoreria,
    CompromisoEspecial,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
    PagoTesoreria,
    Proveedor,
)
from treasury.services import (
    _comprobantes_las_distinguen,
    _empresa_de_la_deuda,
    _mes_de_tesoreria_cerrado,
    annul_payment,
    register_payment,
    set_supplier_internal,
)

# Namespace fijo y propio (distinto del del importador): el token de cada pago
# sale de (lote, medio, deuda), asi que la misma deuda en el mismo lote da el
# mismo token en cualquier corrida.
NAMESPACE_REGULARIZACION = uuid.UUID("a5abd050-2c91-4648-b944-dcc177f3456d")

EFECTIVO = PagoTesoreria.MedioPago.EFECTIVO
INTERNO = PagoTesoreria.MedioPago.INTERNO
# Los unicos medios que escribe el comando. REGULARIZACION queda afuera a
# proposito (aclaracion de tesoreria del 2026-10-13).
MEDIOS_DEL_COMANDO = (EFECTIVO, INTERNO)

ESTADOS_ABIERTOS = (CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL)
CERO = Decimal("0.00")

# Ventana en que un debito bancario sin asignar del mismo proveedor puede ser
# el pago de la factura: la factura pudo pagarse por banco y no en efectivo.
VENTANA_ANTES = timedelta(days=30)
VENTANA_DESPUES = timedelta(days=75)
TOP_PROVEEDORES = 30

# Exclusiones por texto sobre la razon social normalizada (normalizar(): sin
# acentos, en mayusculas, solo letras, numeros y espacios). Son obligaciones que
# no son facturas de proveedores o que no se pagaron en efectivo.
PATRON_IMPUESTO = re.compile(
    r"\b(AFIP|ARCA|DGR|RENTAS|IIBB|TISSH|INGRESOS BRUTOS|IMPUEST\w*|MUNICIPAL\w*|JUICIO\w*|EMBARGO\w*|TASAS?)\b"
)
PATRON_SUELDO = re.compile(
    r"\b(SUELDO\w*|SAC|REMUNERAC\w*|SINDICATO|OBRA SOCIAL|UTHGRA|OSUTHGRA|FAECYS|ART|APORTES?"
    r"|CARGAS SOCIALES|SEGURO DE VIDA|F 931|931|ANSES)\b"
)
PATRON_TARJETA = re.compile(r"\b(TARJETA DE CREDITO|VISA|MASTERCARD|AMERICAN EXPRESS|AMEX)\b")
# Rubro de la categoria de la deuda.
PATRON_RUBRO_IMPUESTO = re.compile(r"\b(IMPUEST\w*|EMBARGO\w*|TASAS?)\b")
PATRON_RUBRO_SUELDO = re.compile(r"\b(PERSONAL|SUELDO\w*|CARGAS SOCIALES)\b")
PATRON_RUBRO_TARJETA = re.compile(r"\bTARJETA\b")
# La panaderia propia: aunque todavia no este marcada como interna, nunca entra
# al modo general (sus facturas no se pagan, ni en efectivo).
PATRON_INTERNO = re.compile(r"\bOVEJA NEGRA\b")
PATRON_OTRO_INTERNO = re.compile(r"\bOVEJA\b")


class Accion:
    A_REGULARIZAR = "A_REGULARIZAR"
    YA_PROCESADA = "YA_PROCESADA"
    EXCLUIDA_CARGA_POSTERIOR = "EXCLUIDA_CARGA_POSTERIOR"
    EXCLUIDA_INTERNO = "EXCLUIDA_INTERNO"
    EXCLUIDA_COMPROMISO = "EXCLUIDA_COMPROMISO"
    EXCLUIDA_IMPUESTO = "EXCLUIDA_IMPUESTO"
    EXCLUIDA_SUELDO = "EXCLUIDA_SUELDO"
    EXCLUIDA_TARJETA = "EXCLUIDA_TARJETA"
    EXCLUIDA_PROVEEDOR = "EXCLUIDA_PROVEEDOR"
    EXCLUIDA_SIN_SUCURSAL = "EXCLUIDA_SIN_SUCURSAL"
    EXCLUIDA_MES_CERRADO = "EXCLUIDA_MES_CERRADO"
    POSIBLE_DUPLICADA = "POSIBLE_DUPLICADA"
    POSIBLE_PAGO_EN_GERAYSE = "POSIBLE_PAGO_EN_GERAYSE"
    # Con el plan revisado (--plan):
    FUERA_DEL_PLAN = "FUERA_DEL_PLAN"
    DISTINTA_AL_PLAN = "DISTINTA_AL_PLAN"
    NO_DISPONIBLE = "NO_DISPONIBLE"
    ERROR = "ERROR"
    ERROR_SALDO_INCONSISTENTE = "ERROR_SALDO_INCONSISTENTE"
    # Solo informativas (modo interno): nunca se aplican.
    AVISO_PAGO_REAL = "AVISO_PAGO_REAL"
    AVISO_OTRO_PROVEEDOR = "AVISO_OTRO_PROVEEDOR"
    # Reversion de un lote.
    A_REVERTIR = "A_REVERTIR"
    YA_ANULADO = "YA_ANULADO"
    NO_ES_DEL_LOTE = "NO_ES_DEL_LOTE"


COLUMNAS_INFORME = (
    "deuda_id",
    "empresa",
    "sucursal",
    "proveedor",
    "rubro",
    "concepto",
    "comprobante",
    "fecha_emision",
    "fecha_vencimiento",
    "creado_en",
    "importe_total",
    "saldo",
    "medio",
    "fecha_pago",
    "accion",
    "motivo",
    "gemelos",
    "posibles_pagos",
    "aviso",
    "pago_id",
    "resultado",
)


def token_de_pago(lote: str, medio: str, deuda_id: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE_REGULARIZACION, f"{lote}|{medio}|{deuda_id}")


@dataclass
class Fila:
    deuda_id: int | None
    accion: str
    medio: str = ""
    deuda: CuentaPorPagar | None = None
    proveedor: str = ""
    empresa_id: int | None = None
    saldo: Decimal = CERO
    fecha_pago: date | None = None
    motivo: str = ""
    aviso: str = ""
    gemelos: list = field(default_factory=list)
    posibles_pagos: list = field(default_factory=list)
    pago_id: int | None = None
    resultado: str = ""

    def agregar_aviso(self, texto: str):
        self.aviso = f"{self.aviso} {texto}".strip() if self.aviso else texto


class _FilaRechazada(Exception):
    def __init__(self, accion: str, mensaje: str):
        super().__init__(mensaje)
        self.accion = accion
        self.mensaje = mensaje


def _mensaje_de_error(error) -> str:
    if isinstance(error, ValidationError):
        if hasattr(error, "message_dict"):
            return "; ".join(f"{k}: {' '.join(v)}" for k, v in error.message_dict.items())
        return " ".join(error.messages)
    return f"{type(error).__name__}: {error}"


def _primer_dia(fecha: date) -> date:
    return fecha.replace(day=1)


def _mes_siguiente(fecha: date) -> date:
    fecha = _primer_dia(fecha)
    return date(fecha.year + 1, 1, 1) if fecha.month == 12 else date(fecha.year, fecha.month + 1, 1)


def _empresa_o_none(deuda: CuentaPorPagar) -> int | None:
    """La empresa de cuya boveda sale el efectivo; None si no se puede saber.

    Es la misma regla que usa register_payment (sucursal de la deuda o caja de
    la que nacio), asi el plan proyecta la boveda que despues se mueve.
    """
    try:
        return _empresa_de_la_deuda(deuda)
    except ValidationError:
        return None


def _meses_cerrados() -> set:
    """(empresa_id, mes) de los cierres de tesoreria. empresa_id None es un
    cierre legacy global: bloquea a todas las empresas."""
    return set(
        CierreMensualTesoreria.objects.filter(cerrado=True).values_list("empresa_id", "mes")
    )


def _mes_cerrado(cerrados: set, empresa_id, fecha: date) -> bool:
    mes = _primer_dia(fecha)
    return (empresa_id, mes) in cerrados or (None, mes) in cerrados


def _total_registrado_subquery():
    return Coalesce(
        Subquery(
            PagoTesoreria.objects.filter(
                cuenta_por_pagar=OuterRef("pk"), estado=PagoTesoreria.Estado.REGISTRADO
            )
            .values("cuenta_por_pagar")
            .annotate(total=Sum("monto"))
            .values("total")[:1]
        ),
        Value(CERO),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )


def _saldos_de_boveda(empresa_ids) -> dict:
    """Saldo de la boveda activa de cada empresa. Solo lectura: no crea bovedas
    (get_boveda las crea por demanda y la simulacion no puede escribir)."""
    saldos = {}
    for empresa_id in empresa_ids:
        boveda = (
            CajaCentral.objects.filter(empresa_id=empresa_id, activo=True).order_by("pk").first()
            if empresa_id
            else None
        )
        saldos[empresa_id] = boveda.saldo_actual if boveda else CERO
    return saldos


def _nombres_de_empresa(empresa_ids) -> dict:
    from cashops.models import Empresa

    return dict(Empresa.objects.filter(pk__in=[e for e in empresa_ids if e]).values_list("pk", "nombre"))


def leer_plan(ruta, medio: str) -> dict:
    """Las deudas que tesoreria aprobo en el CSV revisado: {deuda_id: saldo}.

    Solo cuentan las filas con accion A_REGULARIZAR. El plan tiene que ser del
    mismo modo (medio) que la corrida: un plan del modo interno no puede
    terminar registrando efectivo, ni al reves.
    """
    ruta = Path(ruta)
    if not ruta.exists():
        raise ValidationError(f"No existe el plan revisado {ruta}.")
    crudo = ruta.read_bytes()
    try:
        texto = crudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel en Windows guarda el CSV en ANSI: los acentos de la razon social
        # no son UTF-8. Las columnas que importan son ASCII, asi que alcanza.
        texto = crudo.decode("cp1252", errors="replace")
    cabecera = texto.splitlines()[0] if texto else ""
    delimitador = ";" if cabecera.count(";") >= cabecera.count(",") else ","
    lector = csv.DictReader(io.StringIO(texto, newline=""), delimiter=delimitador)
    faltan = {"deuda_id", "accion", "saldo", "medio"} - set(lector.fieldnames or [])
    if faltan:
        raise ValidationError(
            f"El plan revisado no tiene las columnas {', '.join(sorted(faltan))}: "
            "use el informe que genero la simulacion."
        )
    aprobadas = {}
    for numero, fila in enumerate(lector, start=2):
        if (fila.get("accion") or "").strip() != Accion.A_REGULARIZAR:
            continue
        try:
            deuda_id = int((fila.get("deuda_id") or "").strip())
        except ValueError as error:
            raise ValidationError(f"Plan revisado, fila {numero}: deuda_id invalido.") from error
        saldo = parsear_monto(fila.get("saldo"))
        if saldo is None:
            raise ValidationError(f"Plan revisado, fila {numero}: saldo invalido.")
        medio_fila = (fila.get("medio") or "").strip()
        if medio_fila != medio:
            raise ValidationError(
                f"Plan revisado, fila {numero}: es del medio {medio_fila or '(vacio)'} y esta "
                f"corrida registra {medio}. Use el plan de este mismo modo."
            )
        if deuda_id in aprobadas and aprobadas[deuda_id] != saldo:
            raise ValidationError(
                f"Plan revisado, fila {numero}: la deuda #{deuda_id} figura dos veces con saldos distintos."
            )
        aprobadas[deuda_id] = saldo
    return aprobadas


class _Informe:
    """Lo comun a la regularizacion y a la reversion: filas, resumen e informe."""

    filas: list

    def resumen(self) -> dict:
        totales = defaultdict(lambda: [0, CERO])
        for fila in self.filas:
            totales[fila.accion][0] += 1
            totales[fila.accion][1] += fila.saldo
        return {accion: (cantidad, total) for accion, (cantidad, total) in totales.items()}

    def errores(self) -> list:
        return [f for f in self.filas if f.accion in (Accion.ERROR, Accion.ERROR_SALDO_INCONSISTENTE)]

    def escribir_informe(self, ruta):
        empresas = _nombres_de_empresa({f.empresa_id for f in self.filas})
        ruta = Path(ruta)
        with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
            escritor = csv.writer(archivo, delimiter=";")
            escritor.writerow(COLUMNAS_INFORME)
            for fila in self.filas:
                escritor.writerow(_valores_de_fila(fila, empresas))


def _valores_de_fila(fila: Fila, empresas: dict) -> list:
    deuda = fila.deuda
    rubro = ""
    if deuda is not None and deuda.categoria.rubro_operativo_id:
        rubro = deuda.categoria.rubro_operativo.nombre
    return [
        fila.deuda_id or "",
        empresas.get(fila.empresa_id, ""),
        deuda.sucursal.codigo if deuda is not None and deuda.sucursal_id else "",
        fila.proveedor,
        rubro,
        deuda.concepto if deuda is not None else "",
        deuda.referencia_comprobante if deuda is not None else "",
        f"{deuda.fecha_emision:%d/%m/%Y}" if deuda is not None else "",
        f"{deuda.fecha_vencimiento:%d/%m/%Y}" if deuda is not None else "",
        f"{timezone.localtime(deuda.creado_en):%d/%m/%Y %H:%M}" if deuda is not None else "",
        dinero(deuda.importe_total) if deuda is not None else "",
        dinero(fila.saldo),
        fila.medio,
        f"{fila.fecha_pago:%d/%m/%Y}" if fila.fecha_pago else "",
        fila.accion,
        fila.motivo,
        " ".join(f"#{pk}" for pk in fila.gemelos),
        " | ".join(fila.posibles_pagos),
        fila.aviso,
        fila.pago_id or "",
        fila.resultado,
    ]


class Regularizacion(_Informe):
    def __init__(
        self,
        *,
        actor,
        hasta: date | None = None,
        proveedor_interno: Proveedor | None = None,
        lote: str | None = None,
        fecha_pago: date | None = None,
        cargadas_hasta: date | None = None,
        sucursales=(),
        excluir_proveedores=(),
        incluir_sin_sucursal: bool = False,
        incluir_posibles_pagos: bool = False,
        incluir_posibles_duplicadas: bool = False,
    ):
        self.actor = actor
        self.interno = proveedor_interno is not None
        self.proveedor_interno = proveedor_interno
        if self.interno:
            if hasta is not None or fecha_pago is not None:
                raise ValidationError(
                    "El modo interno no lleva corte ni fecha de pago: toma todas las deudas abiertas "
                    "del proveedor y las cancela a la fecha de cada factura."
                )
        elif hasta is None:
            raise ValidationError("Falta la fecha de corte (--hasta).")
        self.medio = INTERNO if self.interno else EFECTIVO
        self.hasta = hasta
        self.fecha_pago = fecha_pago
        self.cargadas_hasta = cargadas_hasta or timezone.localdate()
        if lote is None:
            lote = f"INTERNO-{proveedor_interno.pk}" if self.interno else f"REG-{hasta:%Y-%m-%d}"
        self.lote = (lote or "").strip()
        if not self.lote:
            raise ValidationError("El lote no puede quedar vacio.")
        if len(self.lote) > 80:
            raise ValidationError("El lote no puede superar los 80 caracteres (va como referencia del pago).")
        self.sucursales = [codigo.strip() for codigo in sucursales if codigo.strip()]
        self.excluir_proveedores = [normalizar(p) for p in excluir_proveedores if normalizar(p)]
        self.incluir_sin_sucursal = incluir_sin_sucursal
        self.incluir_posibles_pagos = incluir_posibles_pagos
        self.incluir_posibles_duplicadas = incluir_posibles_duplicadas
        self.filas: list[Fila] = []
        self.plan_revisado: dict | None = None
        self.boveda_antes: dict = {}
        self.boveda_despues: dict | None = None
        self.proveedor_marcado = False
        self.categorias_sin_rubro = 0

    @property
    def observaciones(self) -> str:
        if self.interno:
            texto = f"Proveedor interno: cancelada sin movimiento de fondos, lote {self.lote}"
        else:
            texto = (
                f"Pago en efectivo segun tesoreria, regularizacion {self.lote} "
                f"(corte {self.hasta:%d/%m/%Y})"
            )
        return texto[:255]

    # --- plan ---------------------------------------------------------------
    def _deudas(self):
        deudas = (
            CuentaPorPagar.objects.filter(estado__in=ESTADOS_ABIERTOS, saldo_pendiente__gt=0)
            .select_related(
                "proveedor",
                "sucursal__empresa",
                "caja_origen__sucursal__empresa",
                "categoria__rubro_operativo",
            )
            .annotate(
                total_registrado=_total_registrado_subquery(),
                tiene_compromiso=Exists(
                    CompromisoEspecial.objects.filter(cuenta_por_pagar=OuterRef("pk"))
                ),
                tiene_sin_movimiento=Exists(
                    PagoTesoreria.objects.filter(
                        cuenta_por_pagar=OuterRef("pk"),
                        estado=PagoTesoreria.Estado.REGISTRADO,
                        medio_pago__in=PagoTesoreria.MEDIOS_SIN_MOVIMIENTO,
                    )
                ),
            )
            .order_by("pk")
        )
        if self.interno:
            deudas = deudas.filter(proveedor=self.proveedor_interno)
        else:
            deudas = deudas.filter(fecha_emision__lte=self.hasta)
        if self.sucursales:
            por_codigo = Q()
            for codigo in self.sucursales:
                por_codigo |= Q(sucursal__codigo__iexact=codigo)
            deudas = deudas.filter(por_codigo)
        return list(deudas)

    def planificar(self) -> list[Fila]:
        deudas = self._deudas()
        tokens = self._pagos_por_token(deudas)
        gemelos = self._gemelos(deudas)
        posibles = {} if self.interno else self._posibles_pagos(deudas)
        cerrados = _meses_cerrados()
        limite_carga = timezone.make_aware(
            datetime.combine(self.cargadas_hasta + timedelta(days=1), time.min)
        )
        self.filas = []
        for deuda in deudas:
            fila = Fila(
                deuda_id=deuda.pk,
                accion=Accion.A_REGULARIZAR,
                medio=self.medio,
                deuda=deuda,
                proveedor=deuda.proveedor.razon_social,
                empresa_id=_empresa_o_none(deuda),
                saldo=deuda.saldo_pendiente,
                fecha_pago=self.fecha_pago or deuda.fecha_emision,
                gemelos=gemelos.get(deuda.pk, []),
                posibles_pagos=posibles.get(deuda.pk, []),
            )
            fila.accion, fila.motivo = self._clasificar(
                deuda, fila, tokens=tokens, cerrados=cerrados, limite_carga=limite_carga
            )
            if not self.interno and deuda.fecha_vencimiento > self.hasta:
                fila.agregar_aviso(f"Vence despues del corte ({deuda.fecha_vencimiento:%d/%m/%Y}).")
            if fila.accion == Accion.A_REGULARIZAR and not deuda.categoria.rubro_operativo_id:
                self.categorias_sin_rubro += 1
                fila.agregar_aviso("Categoria sin rubro: no cuenta como gasto en la lectura economica.")
            if self.interno and fila.accion == Accion.A_REGULARIZAR and deuda.total_registrado > 0:
                fila.agregar_aviso(
                    f"Ya tiene pagos por {dinero(deuda.total_registrado)}: se cancela solo el resto "
                    "(ver las filas AVISO_PAGO_REAL)."
                )
            self.filas.append(fila)
        if self.interno:
            self.filas.extend(self._avisos_del_modo_interno())
        empresas = {f.empresa_id for f in self.filas if f.accion == Accion.A_REGULARIZAR and f.empresa_id}
        self.boveda_antes = {} if self.interno else _saldos_de_boveda(empresas)
        return self.filas

    def _clasificar(self, deuda, fila, *, tokens, cerrados, limite_carga) -> tuple[str, str]:
        nombre = normalizar(deuda.proveedor.razon_social)
        rubro = normalizar(deuda.categoria.rubro_operativo.nombre) if deuda.categoria.rubro_operativo_id else ""
        if deuda.creado_en >= limite_carga:
            return (
                Accion.EXCLUIDA_CARGA_POSTERIOR,
                f"Cargada despues del {self.cargadas_hasta:%d/%m/%Y}: no estaba en lo que declaro tesoreria.",
            )
        if not self.interno:
            if deuda.proveedor.es_interno or PATRON_INTERNO.search(nombre):
                return Accion.EXCLUIDA_INTERNO, "Proveedor interno: va por el modo --proveedor-interno."
        if deuda.tiene_compromiso:
            return Accion.EXCLUIDA_COMPROMISO, "Tiene compromiso especial (impuesto, plan o embargo)."
        if not self.interno:
            if PATRON_IMPUESTO.search(nombre) or PATRON_RUBRO_IMPUESTO.search(rubro):
                return Accion.EXCLUIDA_IMPUESTO, "Impuestos, tasas o juicios: no son facturas de proveedores."
            if PATRON_SUELDO.search(nombre) or PATRON_RUBRO_SUELDO.search(rubro):
                return Accion.EXCLUIDA_SUELDO, "Sueldos o cargas sociales."
            if PATRON_TARJETA.search(nombre) or PATRON_RUBRO_TARJETA.search(rubro):
                return Accion.EXCLUIDA_TARJETA, "Tarjeta de credito: se debita del banco."
            for patron in self.excluir_proveedores:
                if patron in nombre:
                    return Accion.EXCLUIDA_PROVEEDOR, f"Excluida con --excluir-proveedor '{patron}'."
            if not deuda.sucursal_id and not self.incluir_sin_sucursal:
                return Accion.EXCLUIDA_SIN_SUCURSAL, "Deuda sin sucursal (incluir con --incluir-sin-sucursal)."
            if fila.empresa_id is None:
                return (
                    Accion.EXCLUIDA_SIN_SUCURSAL,
                    "No se sabe de que boveda sale el efectivo: sin sucursal con empresa ni caja de origen.",
                )
        derivado = deuda.importe_total - deuda.total_registrado
        if derivado != deuda.saldo_pendiente:
            return (
                Accion.ERROR_SALDO_INCONSISTENTE,
                f"El saldo guardado ({dinero(deuda.saldo_pendiente)}) no coincide con el importe menos "
                f"los pagos registrados ({dinero(derivado)}).",
            )
        existente = tokens.get(token_de_pago(self.lote, self.medio, deuda.pk))
        if existente is not None:
            return _clasificar_token(existente, deuda.pk)
        if deuda.tiene_sin_movimiento:
            return (
                Accion.YA_PROCESADA,
                "Ya tiene una cancelacion sin movimiento de fondos registrada; revisarla a mano.",
            )
        if not self.interno and _mes_cerrado(cerrados, fila.empresa_id, fila.fecha_pago):
            return (
                Accion.EXCLUIDA_MES_CERRADO,
                f"El mes {fila.fecha_pago:%m/%Y} esta cerrado en tesoreria para esa empresa: "
                "el pago cambiaria la foto del cierre.",
            )
        if fila.gemelos:
            if not self.incluir_posibles_duplicadas:
                return (
                    Accion.POSIBLE_DUPLICADA,
                    "Mismo proveedor, sucursal, fecha e importe que otra deuda: anular la repetida antes.",
                )
            fila.agregar_aviso("Posible duplicada, incluida con --incluir-posibles-duplicadas.")
        if fila.posibles_pagos:
            if not self.incluir_posibles_pagos:
                return (
                    Accion.POSIBLE_PAGO_EN_GERAYSE,
                    "Hay debitos del banco del mismo proveedor sin asignar: pudo pagarse por banco.",
                )
            fila.agregar_aviso("Posible pago por banco, incluida con --incluir-posibles-pagos.")
        return Accion.A_REGULARIZAR, ""

    def _pagos_por_token(self, deudas) -> dict:
        tokens = [token_de_pago(self.lote, self.medio, deuda.pk) for deuda in deudas]
        encontrados = {}
        for inicio in range(0, len(tokens), 500):
            for pago in PagoTesoreria.objects.filter(token_alta__in=tokens[inicio : inicio + 500]):
                encontrados[pago.token_alta] = pago
        return encontrados

    def _gemelos(self, deudas) -> dict:
        """{deuda_id: [ids de otras deudas no ANULADA con la misma clave]}.

        Misma clave que el aviso de carga (proveedor, sucursal, fecha de
        factura e importe), contra TODAS las no anuladas, incluidas las ya
        pagadas: una factura cargada dos veces, una pagada y otra abierta, es
        justo la que no hay que volver a pagar.
        """
        if not deudas:
            return {}
        proveedores = {d.proveedor_id for d in deudas}
        desde = min(d.fecha_emision for d in deudas)
        hasta = max(d.fecha_emision for d in deudas)
        grupos = defaultdict(list)
        otras = (
            CuentaPorPagar.objects.filter(
                proveedor_id__in=proveedores, fecha_emision__gte=desde, fecha_emision__lte=hasta
            )
            .exclude(estado=CuentaPorPagar.Estado.ANULADA)
            .only("pk", "proveedor_id", "sucursal_id", "fecha_emision", "importe_total", "referencia_comprobante")
        )
        for otra in otras:
            grupos[(otra.proveedor_id, otra.sucursal_id, otra.fecha_emision, otra.importe_total)].append(otra)
        resultado = {}
        for deuda in deudas:
            clave = (deuda.proveedor_id, deuda.sucursal_id, deuda.fecha_emision, deuda.importe_total)
            ids = [
                otra.pk
                for otra in grupos.get(clave, [])
                if otra.pk != deuda.pk and not _comprobantes_las_distinguen([deuda, otra])
            ]
            if ids:
                resultado[deuda.pk] = sorted(ids)
        return resultado

    def _posibles_pagos(self, deudas) -> dict:
        """{deuda_id: ["#id dd/mm/aaaa $"]}: debitos REGISTRADO del mismo
        proveedor y empresa, con importe sin asignar, en la ventana de la factura.

        Si la cuenta del debito no tiene empresa (la de ARMADI en prod la tiene
        en NULL) se toma la de su sucursal o la de la imputacion; si no hay
        ninguna, se lo considera de cualquier empresa: es preferible dejar una
        factura abierta de mas que pagarla dos veces.
        """
        if not deudas:
            return {}
        proveedores = {d.proveedor_id for d in deudas}
        desde = min(d.fecha_emision for d in deudas) - VENTANA_ANTES
        hasta = max(d.fecha_emision for d in deudas) + VENTANA_DESPUES
        asignado = Coalesce(
            Subquery(
                PagoTesoreria.objects.filter(
                    movimiento_bancario=OuterRef("pk"), estado=PagoTesoreria.Estado.REGISTRADO
                )
                .values("movimiento_bancario")
                .annotate(total=Sum("monto"))
                .values("total")[:1]
            ),
            Value(CERO),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        debitos = (
            MovimientoBancario.objects.filter(
                estado=MovimientoBancario.Estado.REGISTRADO,
                tipo=MovimientoBancario.Tipo.DEBITO,
                proveedor_id__in=proveedores,
                fecha__gte=desde,
                fecha__lte=hasta,
            )
            .annotate(asignado=asignado)
            .values(
                "pk",
                "proveedor_id",
                "fecha",
                "monto",
                "asignado",
                "cuenta_bancaria__empresa_id",
                "cuenta_bancaria__sucursal__empresa_id",
                "sucursal_gasto__empresa_id",
            )
            .order_by("fecha", "pk")
        )
        por_proveedor = defaultdict(list)
        for debito in debitos:
            sin_asignar = debito["monto"] - debito["asignado"]
            if sin_asignar <= 0:
                continue
            empresa_id = (
                debito["cuenta_bancaria__empresa_id"]
                or debito["cuenta_bancaria__sucursal__empresa_id"]
                or debito["sucursal_gasto__empresa_id"]
            )
            por_proveedor[debito["proveedor_id"]].append((debito, empresa_id, sin_asignar))
        resultado = {}
        for deuda in deudas:
            empresa_deuda = _empresa_o_none(deuda)
            encontrados = [
                f"#{debito['pk']} {debito['fecha']:%d/%m/%Y} {dinero(sin_asignar)}"
                for debito, empresa_id, sin_asignar in por_proveedor.get(deuda.proveedor_id, [])
                if deuda.fecha_emision - VENTANA_ANTES <= debito["fecha"] <= deuda.fecha_emision + VENTANA_DESPUES
                and (empresa_id is None or empresa_deuda is None or empresa_id == empresa_deuda)
            ]
            if encontrados:
                resultado[deuda.pk] = encontrados
        return resultado

    def _avisos_del_modo_interno(self) -> list[Fila]:
        """Lo que tesoreria tiene que mirar antes de dar por interno al proveedor:
        pagos con plata que ya se le hicieron (la boveda o el banco bajaron por
        algo que no se pago) y otros proveedores que parecen la misma panaderia."""
        filas = []
        pagos = (
            PagoTesoreria.objects.con_movimiento_de_fondos()
            .filter(
                cuenta_por_pagar__proveedor=self.proveedor_interno,
                estado=PagoTesoreria.Estado.REGISTRADO,
            )
            .select_related(
                "cuenta_por_pagar__proveedor",
                "cuenta_por_pagar__sucursal__empresa",
                "cuenta_por_pagar__caja_origen__sucursal__empresa",
                "cuenta_por_pagar__categoria__rubro_operativo",
            )
            .order_by("pk")
        )
        for pago in pagos:
            deuda = pago.cuenta_por_pagar
            filas.append(
                Fila(
                    deuda_id=deuda.pk,
                    accion=Accion.AVISO_PAGO_REAL,
                    medio=pago.medio_pago,
                    deuda=deuda,
                    proveedor=deuda.proveedor.razon_social,
                    empresa_id=_empresa_o_none(deuda),
                    saldo=pago.monto,
                    fecha_pago=pago.fecha_pago,
                    pago_id=pago.pk,
                    motivo=(
                        f"Pago real ({pago.get_medio_pago_display()}) a un proveedor interno: decidir si "
                        "se anula y se cancela como INTERNO."
                    ),
                )
            )
        candidatos = Proveedor.objects.filter(razon_social__icontains="oveja").exclude(
            pk=self.proveedor_interno.pk
        )
        for proveedor in candidatos.order_by("pk"):
            if not PATRON_OTRO_INTERNO.search(normalizar(proveedor.razon_social)):
                continue
            abiertas = CuentaPorPagar.objects.filter(proveedor=proveedor, estado__in=ESTADOS_ABIERTOS)
            saldo = abiertas.aggregate(total=Sum("saldo_pendiente"))["total"] or CERO
            filas.append(
                Fila(
                    deuda_id=None,
                    accion=Accion.AVISO_OTRO_PROVEEDOR,
                    proveedor=proveedor.razon_social,
                    saldo=saldo,
                    motivo=(
                        f"Proveedor #{proveedor.pk} con OVEJA en la razon social: {abiertas.count()} deudas "
                        "abiertas. Si es la misma panaderia, unificarlo o correr tambien el modo interno."
                    ),
                )
            )
        return filas

    def restringir_a_plan(self, aprobadas: dict):
        """Aplica solo lo que tesoreria aprobo en el plan revisado y con el mismo
        saldo. Lo nuevo desde la revision (una factura cargada despues, una que
        cambio de saldo) se informa y no se toca."""
        self.plan_revisado = aprobadas
        vistas = set()
        for fila in self.filas:
            if fila.deuda_id is None or fila.accion in (Accion.AVISO_PAGO_REAL, Accion.AVISO_OTRO_PROVEEDOR):
                continue
            vistas.add(fila.deuda_id)
            saldo_aprobado = aprobadas.get(fila.deuda_id)
            if fila.accion == Accion.A_REGULARIZAR:
                if saldo_aprobado is None:
                    fila.accion = Accion.FUERA_DEL_PLAN
                    fila.motivo = "No figura como A_REGULARIZAR en el plan revisado."
                elif saldo_aprobado != fila.saldo:
                    fila.accion = Accion.DISTINTA_AL_PLAN
                    fila.motivo = (
                        f"El saldo cambio desde la revision: en el plan {dinero(saldo_aprobado)}, "
                        f"hoy {dinero(fila.saldo)}."
                    )
            elif saldo_aprobado is not None:
                fila.agregar_aviso(f"El plan revisado la aprobaba; hoy es {fila.accion}: no se aplica.")
        faltantes = sorted(set(aprobadas) - vistas)
        deudas = CuentaPorPagar.objects.select_related(
            "proveedor", "sucursal__empresa", "caja_origen__sucursal__empresa", "categoria__rubro_operativo"
        ).in_bulk(faltantes)
        for deuda_id in faltantes:
            deuda = deudas.get(deuda_id)
            fila = Fila(
                deuda_id=deuda_id,
                accion=Accion.NO_DISPONIBLE,
                medio=self.medio,
                saldo=aprobadas[deuda_id],
                motivo="Estaba aprobada en el plan revisado pero ya no existe.",
            )
            if deuda is not None:
                fila.deuda = deuda
                fila.proveedor = deuda.proveedor.razon_social
                fila.empresa_id = _empresa_o_none(deuda)
                fila.motivo = (
                    f"Estaba aprobada en el plan revisado; hoy esta {deuda.get_estado_display()} "
                    "o fuera de esta seleccion."
                )
            self.filas.append(fila)

    # --- aplicacion ---------------------------------------------------------
    def aplicar(self) -> list[Fila]:
        for fila in self.filas:
            if fila.accion != Accion.A_REGULARIZAR:
                continue
            try:
                pago = self._aplicar_fila(fila)
            except _FilaRechazada as rechazo:
                fila.accion = rechazo.accion
                fila.resultado = rechazo.mensaje
            except Exception as error:  # noqa: BLE001 - se informa en la fila y la corrida sigue
                fila.accion = Accion.ERROR
                fila.resultado = _mensaje_de_error(error)
            else:
                fila.pago_id = pago.pk
                fila.resultado = f"Pago #{pago.pk} ({pago.get_medio_pago_display()})"
        if self.interno:
            set_supplier_internal(supplier=self.proveedor_interno, es_interno=True, actor=self.actor)
            self.proveedor_marcado = True
        else:
            self.boveda_despues = _saldos_de_boveda(self.boveda_antes.keys())
        return self.filas

    def _aplicar_fila(self, fila: Fila) -> PagoTesoreria:
        token = token_de_pago(self.lote, self.medio, fila.deuda_id)
        with transaction.atomic():
            # Sin select_related: FOR UPDATE sobre un LEFT JOIN falla en Postgres.
            deuda = CuentaPorPagar.objects.select_for_update().get(pk=fila.deuda_id)
            existente = PagoTesoreria.objects.filter(token_alta=token).first()
            if existente is not None:
                raise _FilaRechazada(*_clasificar_token(existente, deuda.pk))
            if deuda.estado not in ESTADOS_ABIERTOS:
                raise _FilaRechazada(
                    Accion.ERROR, f"La deuda cambio desde el plan: hoy esta {deuda.get_estado_display()}."
                )
            # Lo que el plan excluyo puede haber cambiado en el medio: se vuelve a
            # mirar con la deuda bloqueada, antes de mover plata.
            if CompromisoEspecial.objects.filter(cuenta_por_pagar_id=deuda.pk).exists():
                raise _FilaRechazada(
                    Accion.EXCLUIDA_COMPROMISO, "Le cargaron un compromiso especial despues del plan."
                )
            if self.interno:
                if deuda.proveedor_id != self.proveedor_interno.pk:
                    raise _FilaRechazada(Accion.ERROR, "La deuda cambio de proveedor desde el plan.")
            else:
                if deuda.proveedor.es_interno:
                    raise _FilaRechazada(
                        Accion.EXCLUIDA_INTERNO, "El proveedor paso a ser interno: va por el modo interno."
                    )
                if _empresa_o_none(deuda) != fila.empresa_id:
                    raise _FilaRechazada(
                        Accion.ERROR,
                        "La deuda cambio de sucursal desde el plan: el efectivo saldria de otra boveda.",
                    )
                if _mes_de_tesoreria_cerrado(fila.fecha_pago, fila.empresa_id):
                    raise _FilaRechazada(
                        Accion.EXCLUIDA_MES_CERRADO,
                        f"El mes {fila.fecha_pago:%m/%Y} se cerro en tesoreria despues del plan.",
                    )
            registrado = (
                PagoTesoreria.objects.filter(
                    cuenta_por_pagar_id=deuda.pk, estado=PagoTesoreria.Estado.REGISTRADO
                ).aggregate(total=Sum("monto"))["total"]
                or CERO
            )
            # El monto sale del importe menos lo pagado, no del saldo guardado: si
            # una deuda legacy tiene el saldo desincronizado, no se paga nada.
            monto = deuda.importe_total - registrado
            if monto != deuda.saldo_pendiente:
                raise _FilaRechazada(
                    Accion.ERROR_SALDO_INCONSISTENTE,
                    f"El saldo guardado ({dinero(deuda.saldo_pendiente)}) no coincide con el importe menos "
                    f"los pagos registrados ({dinero(monto)}).",
                )
            if monto != fila.saldo:
                raise _FilaRechazada(
                    Accion.ERROR,
                    f"El saldo cambio desde el plan: era {dinero(fila.saldo)}, hoy {dinero(monto)}.",
                )
            if PagoTesoreria.objects.filter(
                cuenta_por_pagar_id=deuda.pk, estado=PagoTesoreria.Estado.REGISTRADO
            ).sin_movimiento_de_fondos().exists():
                raise _FilaRechazada(
                    Accion.YA_PROCESADA,
                    "Ya tiene una cancelacion sin movimiento de fondos registrada; revisarla a mano.",
                )
            return register_payment(
                payable=deuda,
                bank_account=None,
                medio_pago=self.medio,
                fecha_pago=fila.fecha_pago,
                monto=monto,
                referencia=self.lote,
                observaciones=self.observaciones,
                token_alta=token,
                actor=self.actor,
            )

    # --- lecturas para la consola ---------------------------------------------
    def bovedas(self) -> list[dict]:
        """Por empresa: saldo de la boveda antes, efectivo del lote y saldo que
        queda. Es lo que tesoreria compara con el arqueo real."""
        if self.interno:
            return []
        aplicado = self.boveda_despues is not None
        efectivo = defaultdict(lambda: CERO)
        for fila in self.filas:
            if fila.accion != Accion.A_REGULARIZAR or not fila.empresa_id:
                continue
            if aplicado and not fila.pago_id:
                continue
            efectivo[fila.empresa_id] += fila.saldo
        empresas = set(self.boveda_antes) | set(efectivo)
        nombres = _nombres_de_empresa(empresas)
        resultado = []
        for empresa_id in sorted(empresas, key=lambda e: nombres.get(e, "")):
            antes = self.boveda_antes.get(empresa_id, CERO)
            resultado.append(
                {
                    "empresa_id": empresa_id,
                    "empresa": nombres.get(empresa_id, str(empresa_id)),
                    "antes": antes,
                    "efectivo": efectivo.get(empresa_id, CERO),
                    "despues": (
                        self.boveda_despues.get(empresa_id, CERO)
                        if aplicado
                        else antes - efectivo.get(empresa_id, CERO)
                    ),
                }
            )
        return resultado

    def avisos_por_mes(self) -> list[dict]:
        """Por (empresa, mes del pago): el efectivo a registrar contra los debitos
        del banco sin pago atado y los EGRESO_ADMIN de la boveda del mismo mes.

        Es solo un aviso. Si en el mes hay plata que salio sin factura atada,
        alguna de esas facturas pudo pagarse con ella: registrar ademas el
        efectivo bajaria dos veces la disponibilidad.
        """
        filas = [f for f in self.filas if f.accion == Accion.A_REGULARIZAR and f.empresa_id]
        if self.interno or not filas:
            return []
        a_registrar = defaultdict(lambda: CERO)
        for fila in filas:
            a_registrar[(fila.empresa_id, _primer_dia(fila.fecha_pago))] += fila.saldo
        desde = min(mes for _, mes in a_registrar)
        hasta = _mes_siguiente(max(mes for _, mes in a_registrar))
        asignado = Coalesce(
            Subquery(
                PagoTesoreria.objects.filter(
                    movimiento_bancario=OuterRef("pk"), estado=PagoTesoreria.Estado.REGISTRADO
                )
                .values("movimiento_bancario")
                .annotate(total=Sum("monto"))
                .values("total")[:1]
            ),
            Value(CERO),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )
        debitos_sin_atar = defaultdict(lambda: CERO)
        debitos = (
            MovimientoBancario.objects.filter(
                estado=MovimientoBancario.Estado.REGISTRADO,
                tipo=MovimientoBancario.Tipo.DEBITO,
                clase__in=[
                    MovimientoBancario.Clase.TRANSFERENCIA_TERCEROS,
                    MovimientoBancario.Clase.CHEQUE,
                    MovimientoBancario.Clase.ECHEQ,
                    MovimientoBancario.Clase.OTRO_EGRESO,
                ],
                fecha__gte=desde,
                fecha__lt=hasta,
            )
            .annotate(asignado=asignado)
            .values(
                "fecha",
                "monto",
                "asignado",
                "cuenta_bancaria__empresa_id",
                "cuenta_bancaria__sucursal__empresa_id",
                "sucursal_gasto__empresa_id",
            )
        )
        for debito in debitos:
            sin_asignar = debito["monto"] - debito["asignado"]
            if sin_asignar <= 0:
                continue
            empresa_id = (
                debito["cuenta_bancaria__empresa_id"]
                or debito["cuenta_bancaria__sucursal__empresa_id"]
                or debito["sucursal_gasto__empresa_id"]
            )
            debitos_sin_atar[(empresa_id, _primer_dia(debito["fecha"]))] += sin_asignar
        egresos_admin = defaultdict(lambda: CERO)
        for egreso in MovimientoCajaCentral.objects.filter(
            estado=MovimientoCajaCentral.Estado.REGISTRADO,
            tipo=MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
            fecha__gte=desde,
            fecha__lt=hasta,
        ).values("caja_central__empresa_id", "fecha", "monto"):
            egresos_admin[(egreso["caja_central__empresa_id"], _primer_dia(egreso["fecha"]))] += egreso["monto"]
        claves = set(a_registrar) | {clave for clave in debitos_sin_atar if clave[0] is None}
        nombres = _nombres_de_empresa({empresa_id for empresa_id, _ in claves})
        return [
            {
                "empresa": nombres.get(empresa_id, "Cuenta sin empresa") if empresa_id else "Cuenta sin empresa",
                "mes": mes,
                "a_registrar": a_registrar.get((empresa_id, mes), CERO),
                "debitos_sin_atar": debitos_sin_atar.get((empresa_id, mes), CERO),
                "egresos_admin": egresos_admin.get((empresa_id, mes), CERO),
            }
            for empresa_id, mes in sorted(claves, key=lambda c: (c[1], nombres.get(c[0], "")))
        ]

    def top_proveedores(self, cantidad: int = TOP_PROVEEDORES) -> list[tuple[str, int, Decimal]]:
        totales = defaultdict(lambda: [0, CERO])
        for fila in self.filas:
            if fila.accion == Accion.A_REGULARIZAR:
                totales[fila.proveedor][0] += 1
                totales[fila.proveedor][1] += fila.saldo
        ordenados = sorted(totales.items(), key=lambda item: (-item[1][1], item[0]))
        return [(nombre, cantidad_, total) for nombre, (cantidad_, total) in ordenados[:cantidad]]


def _clasificar_token(existente: PagoTesoreria, deuda_id: int) -> tuple[str, str]:
    """Que significa que el token del lote ya exista. Nunca se devuelve un pago
    anulado o de otra deuda como si fuera el de esta (register_payment lo haria)."""
    if existente.cuenta_por_pagar_id != deuda_id:
        return (
            Accion.ERROR,
            f"El token del lote ya lo usa el pago #{existente.pk} de otra deuda (#{existente.cuenta_por_pagar_id}).",
        )
    if existente.estado == PagoTesoreria.Estado.ANULADO:
        return (
            Accion.ERROR,
            f"El pago #{existente.pk} de este lote se anulo y no se vuelve a registrar solo. "
            "Si corresponde pagarla otra vez, use otro --lote.",
        )
    return Accion.YA_PROCESADA, f"Ya registrada en este lote: pago #{existente.pk}."


class Reversion(_Informe):
    """Anula los pagos de un lote del comando con annul_payment: el efectivo
    vuelve a la boveda (se anula su EGRESO_PAGO) y las deudas se reabren.

    Solo toca los pagos que el comando creo: referencia = lote, medio EFECTIVO o
    INTERNO y token del lote. Un pago cargado a mano con la misma referencia se
    informa y no se anula.
    """

    def __init__(self, *, actor, lote: str, motivo: str = "", desmarcar_interno: bool = False):
        self.actor = actor
        self.lote = (lote or "").strip()
        if not self.lote:
            raise ValidationError("Falta el lote a revertir.")
        self.motivo = (motivo or "").strip() or f"Reversion del lote {self.lote}"
        self.desmarcar_interno = desmarcar_interno
        self.filas: list[Fila] = []
        self.boveda_antes: dict = {}
        self.boveda_despues: dict | None = None
        self.proveedores_desmarcados: list[str] = []

    def planificar(self) -> list[Fila]:
        pagos = (
            PagoTesoreria.objects.filter(referencia=self.lote, medio_pago__in=MEDIOS_DEL_COMANDO)
            .select_related(
                "cuenta_por_pagar__proveedor",
                "cuenta_por_pagar__sucursal__empresa",
                "cuenta_por_pagar__caja_origen__sucursal__empresa",
                "cuenta_por_pagar__categoria__rubro_operativo",
            )
            .order_by("pk")
        )
        egresos = {
            movimiento.pago_tesoreria_id: movimiento
            for movimiento in MovimientoCajaCentral.objects.filter(
                pago_tesoreria__in=pagos, estado=MovimientoCajaCentral.Estado.REGISTRADO
            ).select_related("caja_central")
        }
        cerrados = _meses_cerrados()
        self.filas = []
        for pago in pagos:
            deuda = pago.cuenta_por_pagar
            egreso = egresos.get(pago.pk)
            fila = Fila(
                deuda_id=deuda.pk,
                accion=Accion.A_REVERTIR,
                medio=pago.medio_pago,
                deuda=deuda,
                proveedor=deuda.proveedor.razon_social,
                empresa_id=egreso.caja_central.empresa_id if egreso else _empresa_o_none(deuda),
                saldo=pago.monto,
                fecha_pago=pago.fecha_pago,
                pago_id=pago.pk,
            )
            if pago.token_alta != token_de_pago(self.lote, pago.medio_pago, deuda.pk):
                fila.accion = Accion.NO_ES_DEL_LOTE
                fila.motivo = "Tiene la referencia del lote pero no lo creo el comando: no se toca."
            elif pago.estado == PagoTesoreria.Estado.ANULADO:
                fila.accion = Accion.YA_ANULADO
                fila.motivo = f"Ya estaba anulado: {pago.motivo_anulacion}"
            elif egreso is not None and _mes_cerrado(cerrados, egreso.caja_central.empresa_id, egreso.fecha):
                fila.accion = Accion.EXCLUIDA_MES_CERRADO
                fila.motivo = (
                    f"El mes {egreso.fecha:%m/%Y} esta cerrado en tesoreria: anularlo cambiaria la foto del cierre."
                )
            self.filas.append(fila)
        empresas = {
            f.empresa_id for f in self.filas if f.accion == Accion.A_REVERTIR and f.medio == EFECTIVO and f.empresa_id
        }
        self.boveda_antes = _saldos_de_boveda(empresas)
        return self.filas

    def aplicar(self) -> list[Fila]:
        for fila in self.filas:
            if fila.accion != Accion.A_REVERTIR:
                continue
            try:
                annul_payment(
                    payment=PagoTesoreria.objects.get(pk=fila.pago_id), motivo=self.motivo, actor=self.actor
                )
            except Exception as error:  # noqa: BLE001 - se informa en la fila y la corrida sigue
                fila.accion = Accion.ERROR
                fila.resultado = _mensaje_de_error(error)
            else:
                fila.resultado = "Pago anulado"
        if self.desmarcar_interno:
            ids = {f.deuda.proveedor_id for f in self.filas if f.medio == INTERNO and f.deuda is not None}
            for proveedor in Proveedor.objects.filter(pk__in=ids, es_interno=True).order_by("pk"):
                set_supplier_internal(supplier=proveedor, es_interno=False, actor=self.actor)
                self.proveedores_desmarcados.append(proveedor.razon_social)
        self.boveda_despues = _saldos_de_boveda(self.boveda_antes.keys())
        return self.filas

    def proveedores_internos(self) -> list[str]:
        ids = {f.deuda.proveedor_id for f in self.filas if f.medio == INTERNO and f.deuda is not None}
        return list(
            Proveedor.objects.filter(pk__in=ids, es_interno=True).order_by("razon_social").values_list(
                "razon_social", flat=True
            )
        )

    def bovedas(self) -> list[dict]:
        aplicado = self.boveda_despues is not None
        efectivo = defaultdict(lambda: CERO)
        for fila in self.filas:
            if fila.accion != Accion.A_REVERTIR or fila.medio != EFECTIVO or not fila.empresa_id:
                continue
            if aplicado and fila.resultado != "Pago anulado":
                continue
            efectivo[fila.empresa_id] += fila.saldo
        nombres = _nombres_de_empresa(self.boveda_antes.keys())
        return [
            {
                "empresa_id": empresa_id,
                "empresa": nombres.get(empresa_id, str(empresa_id)),
                "antes": antes,
                "efectivo": efectivo.get(empresa_id, CERO),
                "despues": (
                    self.boveda_despues.get(empresa_id, CERO) if aplicado else antes + efectivo.get(empresa_id, CERO)
                ),
            }
            for empresa_id, antes in sorted(self.boveda_antes.items(), key=lambda i: nombres.get(i[0], ""))
        ]


__all__ = [
    "Accion",
    "EFECTIVO",
    "INTERNO",
    "MEDIOS_DEL_COMANDO",
    "NAMESPACE_REGULARIZACION",
    "Fila",
    "Regularizacion",
    "Reversion",
    "leer_plan",
    "token_de_pago",
]
