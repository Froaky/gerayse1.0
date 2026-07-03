from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from treasury.models import (
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
)


ANCHO = 74


class Command(BaseCommand):
    help = (
        "Informe de solo lectura, pensado para administracion: lista los registros que hoy "
        "no tienen sucursal asignada y explica en lenguaje simple que consecuencia tiene. "
        "NO modifica ningun dato."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--max",
            type=int,
            default=0,
            help="Maximo de filas por seccion. 0 (default) muestra todas.",
        )

    def handle(self, *args, **options):
        tope = options["max"]

        self._titulo("REGISTROS SIN SUCURSAL ASIGNADA")
        self.stdout.write("Informe de solo lectura. No se modifica ningun dato.\n")
        self.stdout.write(
            "Muestra los registros que hoy NO tienen una sucursal asignada.\n"
            "Esto tiene dos consecuencias:\n"
            "  1) Las cuentas de banco sin empresa se ven desde las dos empresas.\n"
            "  2) Los gastos del banco sin sucursal NO se cuentan en la rentabilidad\n"
            "     de ningun local (aparecen aparte como \"Gasto sin imputar\").\n"
        )

        self._seccion_cuentas(tope)
        self._seccion_gastos_banco(tope)
        self._seccion_correcto()

        self._linea()
        self.stdout.write("Fin del informe. Ningun dato fue modificado.")

    # ------------------------------------------------------------------ secciones

    def _seccion_cuentas(self, tope):
        cuentas = CuentaBancaria.objects.filter(sucursal__isnull=True).order_by("-creado_en")
        total = cuentas.count()
        self._encabezado_seccion(f"CUENTAS DE BANCO SIN EMPRESA ASIGNADA: {total}")
        if total == 0:
            self.stdout.write("  No hay. Todo correcto.\n")
            return
        self.stdout.write("  Estas cuentas hoy se ven desde las dos empresas (MAPOGO y ARMADI).\n")
        self.stdout.write(
            "  "
            + "Cuenta".ljust(22)
            + "Banco".ljust(16)
            + "Estado".ljust(10)
            + "Alta"
        )
        for c in self._acotar(cuentas, tope):
            self.stdout.write(
                "  "
                + (c.nombre or "-")[:20].ljust(22)
                + (c.banco or "-")[:14].ljust(16)
                + ("Activa" if c.activa else "Inactiva").ljust(10)
                + self._fecha(c.creado_en)
            )
        self._nota_acotado(total, tope)
        self.stdout.write("")

    def _seccion_gastos_banco(self, tope):
        # Solo los egresos (debitos) son imputables por sucursal; los ingresos no.
        gastos = MovimientoBancario.objects.filter(
            sucursal_gasto__isnull=True,
            tipo=MovimientoBancario.Tipo.DEBITO,
            estado=MovimientoBancario.Estado.REGISTRADO,
        ).order_by("-fecha")
        total = gastos.count()
        suma = gastos.aggregate(t=Sum("monto"))["t"] or Decimal("0.00")
        self._encabezado_seccion(
            f"GASTOS DEL BANCO SIN SUCURSAL: {total}   Total: {self._pesos(suma)}"
        )
        if total == 0:
            self.stdout.write("  No hay. Todo correcto.\n")
            return
        self.stdout.write(
            "  Estos gastos salieron del banco pero no estan imputados a ninguna sucursal,\n"
            "  asi que no impactan la rentabilidad por local.\n"
        )
        self.stdout.write(
            "  "
            + "Fecha".ljust(12)
            + "Importe".rjust(16)
            + "   "
            + "Tipo de gasto".ljust(26)
            + "Detalle"
        )
        for m in self._acotar(gastos, tope):
            self.stdout.write(
                "  "
                + self._fecha(m.fecha).ljust(12)
                + self._pesos(m.monto).rjust(16)
                + "   "
                + self._tipo_gasto(m).ljust(26)
                + (m.concepto or "")[:28]
            )
        self._nota_acotado(total, tope)
        self.stdout.write("")

    def _seccion_correcto(self):
        self._encabezado_seccion("LO QUE ESTA CORRECTO (no requiere accion)")
        deudas = CuentaPorPagar.objects.filter(sucursal__isnull=True).count()
        compromisos = CompromisoEspecial.objects.filter(sucursal__isnull=True).count()
        caja_egresos = MovimientoCajaCentral.objects.filter(
            sucursal_gasto__isnull=True,
            tipo__in=[
                MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            ],
        ).count()
        creditos = MovimientoBancario.objects.filter(
            sucursal_gasto__isnull=True, tipo=MovimientoBancario.Tipo.CREDITO
        ).count()
        caja_ingresos = MovimientoCajaCentral.objects.filter(sucursal_gasto__isnull=True).exclude(
            tipo__in=[
                MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                MovimientoCajaCentral.Tipo.EGRESO_PAGO,
            ]
        ).count()
        self.stdout.write(f"  Deudas sin sucursal: {deudas}")
        self.stdout.write(f"  Compromisos sin sucursal: {compromisos}")
        self.stdout.write(f"  Egresos de caja fuerte sin sucursal: {caja_egresos}")
        self.stdout.write(
            f"  Ingresos, aportes y creditos sin sucursal: {creditos + caja_ingresos} "
            "(es plata comun, esta bien que no tengan sucursal)"
        )
        self.stdout.write("")

    # ------------------------------------------------------------------ helpers

    def _titulo(self, texto):
        self.stdout.write("=" * ANCHO)
        self.stdout.write("  " + texto)
        self.stdout.write("=" * ANCHO)

    def _encabezado_seccion(self, texto):
        self._linea()
        self.stdout.write(texto)
        self._linea()

    def _linea(self):
        self.stdout.write("-" * ANCHO)

    def _acotar(self, queryset, tope):
        if tope and tope > 0:
            return queryset[:tope]
        return queryset

    def _nota_acotado(self, total, tope):
        if tope and tope > 0 and total > tope:
            self.stdout.write(f"  ... {total - tope} fila(s) mas (correr sin --max para ver todas).")

    def _fecha(self, valor):
        if valor is None:
            return "-"
        return valor.strftime("%d/%m/%Y")

    def _pesos(self, value):
        # Formato argentino: $ 1.234.567,89
        texto = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"$ {texto}"

    def _tipo_gasto(self, movimiento):
        # El nombre interno suele empezar con "Egreso por ..."; lo simplificamos.
        etiqueta = movimiento.get_clase_display()
        prefijo = "Egreso por "
        if etiqueta.startswith(prefijo):
            etiqueta = etiqueta[len(prefijo):]
            etiqueta = etiqueta[:1].upper() + etiqueta[1:]
        return etiqueta[:24]
