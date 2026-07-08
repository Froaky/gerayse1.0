from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q, Sum

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
            "Muestra los registros que hoy NO tienen sucursal (u otro dato) asignado.\n"
            "Esto tiene dos consecuencias:\n"
            "  1) Las cuentas de banco sin empresa se ven desde las dos empresas.\n"
            "  2) Los gastos del banco incompletos NO se cuentan en la rentabilidad\n"
            "     por sucursal (les falta rubro, sucursal o periodo).\n"
        )

        self._seccion_cuentas(tope)
        self._seccion_gastos_banco(tope)
        self._cuadre_debitos_banco()
        self._seccion_correcto()

        self._linea()
        self.stdout.write("Fin del informe. Ningun dato fue modificado.")

    def _cuadre_debitos_banco(self):
        """Cuadre: todo debito del banco cae en exactamente un balde. Nada se pierde."""
        base = MovimientoBancario.objects.filter(tipo=MovimientoBancario.Tipo.DEBITO)
        universo = base.count()
        universo_monto = base.aggregate(t=Sum("monto"))["t"] or Decimal("0.00")

        anulados = base.filter(estado=MovimientoBancario.Estado.ANULADO)
        registrados = base.filter(estado=MovimientoBancario.Estado.REGISTRADO)
        completos = registrados.filter(
            rubro_operativo__isnull=False,
            sucursal_gasto__isnull=False,
            periodo_pago__isnull=False,
        )
        incompletos = registrados.filter(
            Q(rubro_operativo__isnull=True)
            | Q(sucursal_gasto__isnull=True)
            | Q(periodo_pago__isnull=True)
        )

        def par(qs):
            return qs.count(), (qs.aggregate(t=Sum("monto"))["t"] or Decimal("0.00"))

        n_anul, m_anul = par(anulados)
        n_comp, m_comp = par(completos)
        n_inc, m_inc = par(incompletos)

        self._encabezado_seccion("CUADRE DE DEBITOS DEL BANCO (control: nada queda afuera)")
        self.stdout.write(f"  Universo (todos los debitos):        {universo:>4}   {self._pesos(universo_monto)}")
        self.stdout.write(f"  (=) Cuentan en rentabilidad:         {n_comp:>4}   {self._pesos(m_comp)}")
        self.stdout.write(f"  (+) NO cuentan (incompletos):        {n_inc:>4}   {self._pesos(m_inc)}")
        self.stdout.write(f"  (+) Anulados (excluidos por diseno): {n_anul:>4}   {self._pesos(m_anul)}")
        suman = n_comp + n_inc + n_anul
        monto_suma = m_comp + m_inc + m_anul
        ok = (suman == universo) and (monto_suma == universo_monto)
        estado = "CUADRA (no se pierde ningun dato)" if ok else "NO CUADRA - revisar"
        self.stdout.write(f"  ------------------------------------------------------")
        self.stdout.write(f"  Suma de baldes:                      {suman:>4}   {self._pesos(monto_suma)}   -> {estado}")
        self.stdout.write("")

    # ------------------------------------------------------------------ secciones

    def _seccion_cuentas(self, tope):
        cuentas = CuentaBancaria.objects.filter(empresa__isnull=True).order_by("-creado_en")
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
        # Para contar en la rentabilidad economica un debito necesita rubro +
        # sucursal + periodo. Mostramos los que NO cumplen (les falta al menos uno).
        gastos = MovimientoBancario.objects.filter(
            tipo=MovimientoBancario.Tipo.DEBITO,
            estado=MovimientoBancario.Estado.REGISTRADO,
        ).filter(
            Q(rubro_operativo__isnull=True)
            | Q(sucursal_gasto__isnull=True)
            | Q(periodo_pago__isnull=True)
        ).order_by("-fecha")
        total = gastos.count()
        suma = gastos.aggregate(t=Sum("monto"))["t"] or Decimal("0.00")
        self._encabezado_seccion(
            f"GASTOS DEL BANCO QUE NO SE CUENTAN EN LA RENTABILIDAD: {total}"
            f"   Total: {self._pesos(suma)}"
        )
        if total == 0:
            self.stdout.write("  No hay. Todo correcto.\n")
            return

        sin_sucursal = gastos.filter(sucursal_gasto__isnull=True).count()
        sin_periodo = gastos.filter(periodo_pago__isnull=True).count()
        sin_rubro = gastos.filter(rubro_operativo__isnull=True).count()
        self.stdout.write(
            "  Para contar en la rentabilidad por sucursal, cada gasto necesita 3 datos:\n"
            "  rubro, sucursal y periodo. A estos les falta al menos uno:\n"
            f"    - sin SUCURSAL: {sin_sucursal}\n"
            f"    - sin PERIODO:  {sin_periodo}\n"
            f"    - sin RUBRO:    {sin_rubro}\n"
        )
        self.stdout.write(
            "  "
            + "Fecha".ljust(11)
            + "Importe".rjust(15)
            + "  "
            + "Le falta".ljust(26)
            + "Detalle"
        )
        for m in self._acotar(gastos, tope):
            self.stdout.write(
                "  "
                + self._fecha(m.fecha).ljust(11)
                + self._pesos(m.monto).rjust(15)
                + "  "
                + self._que_falta(m).ljust(26)
                + (m.concepto or "")[:26]
            )
        self._nota_acotado(total, tope)
        self.stdout.write("")

    def _que_falta(self, m):
        faltan = []
        if m.rubro_operativo_id is None:
            faltan.append("rubro")
        if m.sucursal_gasto_id is None:
            faltan.append("sucursal")
        if m.periodo_pago is None:
            faltan.append("periodo")
        if len(faltan) == 3:
            return "los 3 (rubro/suc/periodo)"
        return ", ".join(faltan)

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

