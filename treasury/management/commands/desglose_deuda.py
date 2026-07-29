from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Count, Q, Sum
from django.db.models.functions import ExtractYear
from django.utils import timezone

from treasury.models import CuentaPorPagar


ANCHO = 78
VIVAS = [CuentaPorPagar.Estado.PENDIENTE, CuentaPorPagar.Estado.PARCIAL]


def _money(valor) -> str:
    valor = valor or Decimal("0")
    s = f"{valor:,.2f}"  # 400,000,000.00
    # a formato argentino: 400.000.000,00
    return "$" + s.replace(",", "X").replace(".", ",").replace("X", ".")


class Command(BaseCommand):
    help = (
        "Informe de SOLO LECTURA: desglosa la deuda (CuentaPorPagar) para entender de que se "
        "compone el total y descartar sobre-conteo. Muestra el saldo pendiente VIVO (estados "
        "PENDIENTE+PARCIAL, que es lo que realmente se debe) y el devengado por importe de "
        "factura, y los abre por estado, proveedor, rubro, anio, sucursal y origen. NO modifica "
        "ningun dato."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--empresa",
            type=str,
            default="",
            help="Filtra por nombre de empresa (contiene) via sucursal. Ej: --empresa ARMADI. "
            "OJO: excluye las deudas legacy sin sucursal (se listan aparte en el diagnostico).",
        )
        parser.add_argument(
            "--top",
            type=int,
            default=15,
            help="Cuantos proveedores/rubros mostrar en los rankings (default 15).",
        )

    def handle(self, *args, **options):
        filtro_empresa = (options["empresa"] or "").strip()
        top = max(1, int(options["top"] or 15))
        hoy = timezone.localdate()
        anio_actual = hoy.year

        qs = CuentaPorPagar.objects.select_related(
            "proveedor", "categoria", "categoria__rubro_operativo", "sucursal", "sucursal__empresa"
        )
        if filtro_empresa:
            qs = qs.filter(sucursal__empresa__nombre__icontains=filtro_empresa)

        vivas = qs.filter(estado__in=VIVAS)  # lo que realmente se debe hoy
        no_anuladas = qs.exclude(estado=CuentaPorPagar.Estado.ANULADA)

        self._titulo("DESGLOSE DE DEUDA (informe de solo lectura)")
        self.stdout.write("No se modifica ningun dato.")
        if filtro_empresa:
            self.stdout.write(f'Filtro empresa: "{filtro_empresa}" (excluye deudas sin sucursal)')
        self.stdout.write(f"Hoy: {hoy:%d/%m/%Y}\n")

        # ---- Titulares ----
        pend_saldo = vivas.aggregate(s=Sum("saldo_pendiente"))["s"] or Decimal("0")
        pend_importe = vivas.aggregate(s=Sum("importe_total"))["s"] or Decimal("0")
        n_vivas = vivas.count()
        devengado = no_anuladas.aggregate(s=Sum("importe_total"))["s"] or Decimal("0")

        self._linea()
        self.stdout.write("TITULARES")
        self._linea()
        self.stdout.write(
            f"  DEUDA VIVA (lo que realmente se debe hoy)         {_money(pend_saldo)}\n"
            f"    = suma de saldo_pendiente de deudas PENDIENTE + PARCIAL ({n_vivas} deudas).\n"
            f"    Es la tarjeta 'Deuda pendiente' del dashboard. NO tiene filtro de fecha:\n"
            f"    es deuda viva ACUMULADA DE TODA LA HISTORIA.\n"
        )
        self.stdout.write(
            f"  Importe de factura de esas mismas deudas vivas    {_money(pend_importe)}\n"
            f"    (si es mayor que la deuda viva, la diferencia {_money(pend_importe - pend_saldo)} ya\n"
            f"     esta pagada en pagos parciales).\n"
        )
        self.stdout.write(
            f"  DEVENGADO por importe de factura (no anuladas)     {_money(devengado)}\n"
            f"    = suma de importe_total de TODAS las deudas no anuladas (incluye las YA PAGADAS\n"
            f"     a valor completo). Es el criterio de la tarjeta 'Deuda del periodo'.\n"
            f"    Si el numero que ven de '+400M' se parece a este, estan mirando devengado\n"
            f"    (lo comprometido), NO lo que falta pagar.\n"
        )

        # ---- Por estado (todas) ----
        self._seccion("POR ESTADO (todas las deudas)")
        por_estado = (
            qs.values("estado")
            .annotate(n=Count("id"), saldo=Sum("saldo_pendiente"), importe=Sum("importe_total"))
            .order_by("estado")
        )
        self.stdout.write(f"  {'Estado':<12}{'Cant.':>8}   {'Saldo pendiente':>20}   {'Importe factura':>20}")
        for row in por_estado:
            self.stdout.write(
                f"  {row['estado']:<12}{row['n']:>8}   {_money(row['saldo']):>20}   {_money(row['importe']):>20}"
            )

        # ---- Rankings sobre la deuda VIVA ----
        self._ranking(
            "TOP PROVEEDORES por deuda viva (saldo pendiente)",
            vivas.values("proveedor__razon_social"),
            "proveedor__razon_social",
            top,
        )
        self._ranking(
            "POR RUBRO (deuda viva)",
            vivas.values("categoria__rubro_operativo__nombre"),
            "categoria__rubro_operativo__nombre",
            top,
        )
        self._ranking(
            "POR SUCURSAL (deuda viva)  [None = legacy sin sucursal]",
            vivas.values("sucursal__nombre"),
            "sucursal__nombre",
            top,
        )

        # ---- Por anio de emision (deuda viva) ----
        self._seccion("POR ANIO DE EMISION (deuda viva)")
        por_anio = (
            vivas.annotate(anio=ExtractYear("fecha_emision"))
            .values("anio")
            .annotate(n=Count("id"), saldo=Sum("saldo_pendiente"))
            .order_by("anio")
        )
        for row in por_anio:
            self.stdout.write(f"  {row['anio']!s:<8}{row['n']:>8} deudas   {_money(row['saldo']):>20}")

        # ---- Origen: gasto cargado desde caja vs alta manual ----
        self._seccion("POR ORIGEN (deuda viva)")
        de_caja = vivas.filter(caja_origen__isnull=False).aggregate(n=Count("id"), s=Sum("saldo_pendiente"))
        manual = vivas.filter(caja_origen__isnull=True).aggregate(n=Count("id"), s=Sum("saldo_pendiente"))
        self.stdout.write(
            f"  Gasto cargado desde una caja (caja_origen)  {de_caja['n'] or 0:>6} deudas   {_money(de_caja['s']):>20}"
        )
        self.stdout.write(
            f"  Alta manual en tesoreria                    {manual['n'] or 0:>6} deudas   {_money(manual['s']):>20}"
        )

        # ---- Diagnostico de sobre-conteo ----
        self._seccion("DIAGNOSTICO (posibles causas de un total inflado)")
        anuladas = qs.filter(estado=CuentaPorPagar.Estado.ANULADA).aggregate(
            n=Count("id"), s=Sum("importe_total")
        )
        pagadas = qs.filter(estado=CuentaPorPagar.Estado.PAGADA).aggregate(
            n=Count("id"), s=Sum("importe_total")
        )
        sin_suc = vivas.filter(sucursal__isnull=True).aggregate(n=Count("id"), s=Sum("saldo_pendiente"))
        viejas = vivas.filter(fecha_emision__lt=date(anio_actual, 1, 1)).aggregate(
            n=Count("id"), s=Sum("saldo_pendiente")
        )
        self.stdout.write(
            f"  - Deudas ANULADAS (no deberian sumar):        {anuladas['n'] or 0:>6}   importe {_money(anuladas['s'])}"
        )
        self.stdout.write(
            f"  - Deudas PAGADAS (suman en 'devengado', NO en deuda viva): {pagadas['n'] or 0:>6}   importe {_money(pagadas['s'])}"
        )
        self.stdout.write(
            f"  - Deuda viva SIN sucursal (legacy, se cuenta en toda empresa): {sin_suc['n'] or 0:>6}   {_money(sin_suc['s'])}"
        )
        self.stdout.write(
            f"  - Deuda viva emitida ANTES de {anio_actual} (arrastre viejo):  {viejas['n'] or 0:>6}   {_money(viejas['s'])}"
        )

        self.stdout.write("")
        self._linea()
        self.stdout.write(
            "Lectura rapida: la 'DEUDA VIVA' es lo que realmente falta pagar. Si el numero que\n"
            "los preocupa es mucho mayor, casi siempre es porque estan mirando el 'DEVENGADO'\n"
            "(importe de factura, que incluye lo ya pagado) o sumando deuda vieja arrastrada.\n"
            "Revisar tambien anuladas mal contadas y deuda sin sucursal."
        )
        self.stdout.write("Fin del informe. Ningun dato fue modificado.")

    # ---- helpers de salida ----
    def _ranking(self, titulo, values_qs, campo, top):
        self._seccion(titulo)
        filas = (
            values_qs.annotate(saldo=Sum("saldo_pendiente"), n=Count("id"))
            .order_by("-saldo")[:top]
        )
        total_mostrado = Decimal("0")
        hubo = False
        for row in filas:
            hubo = True
            nombre = row[campo] or "(sin dato)"
            saldo = row["saldo"] or Decimal("0")
            total_mostrado += saldo
            self.stdout.write(f"  {str(nombre)[:44]:<44}{row['n']:>5}   {_money(saldo):>20}")
        if not hubo:
            self.stdout.write("  (sin deudas vivas)")
        else:
            self.stdout.write(f"  {'-- subtotal mostrado --':<44}{'':>5}   {_money(total_mostrado):>20}")

    def _seccion(self, texto):
        self.stdout.write("")
        self._linea()
        self.stdout.write(texto)
        self._linea()

    def _titulo(self, texto):
        self._linea()
        self.stdout.write(texto)
        self._linea()

    def _linea(self):
        self.stdout.write("=" * ANCHO)
