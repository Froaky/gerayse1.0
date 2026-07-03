from __future__ import annotations

from django.core.management.base import BaseCommand

from treasury.models import (
    CompromisoEspecial,
    CuentaBancaria,
    CuentaPorPagar,
    MovimientoBancario,
    MovimientoCajaCentral,
)


class Command(BaseCommand):
    help = (
        "Reporte de solo lectura: lista los registros de tesoreria que tienen sucursal "
        "(o sucursal_gasto) en NULL. Un registro sin sucursal es visible para todas las "
        "empresas. Este comando NO modifica ningun dato; sirve para decidir si esos "
        "registros son gastos compartidos a proposito o cargas a las que les falto la sucursal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--detalle",
            type=int,
            default=20,
            help="Cantidad maxima de filas de detalle a mostrar por grupo (default 20).",
        )

    def handle(self, *args, **options):
        limite = options["detalle"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            "Registros de tesoreria SIN sucursal (visibles para todas las empresas)"
        ))
        self.stdout.write("Modo solo lectura: no se modifica ningun dato.\n")

        # --- Deudas ---
        self._reportar(
            "CuentaPorPagar (deudas) sin sucursal",
            CuentaPorPagar.objects.filter(sucursal__isnull=True).order_by("-creado_en"),
            limite,
            lambda o: [
                f"#{o.pk}",
                f"{o.fecha_emision}",
                f"${o.importe_total}",
                f"pend ${o.saldo_pendiente}",
                o.get_estado_display(),
                (o.proveedor.razon_social if o.proveedor_id else "-"),
                (o.concepto or "")[:40],
                f"creado {o.creado_en:%Y-%m-%d}",
            ],
        )

        # --- Compromisos especiales ---
        self._reportar(
            "CompromisoEspecial sin sucursal",
            CompromisoEspecial.objects.filter(sucursal__isnull=True).order_by("-id"),
            limite,
            lambda o: [
                f"#{o.pk}",
                o.get_tipo_display(),
                o.get_estado_display(),
                (o.concepto or "")[:40],
                (o.organismo or o.beneficiario or "-")[:30],
            ],
        )

        # --- Cuentas bancarias ---
        self._reportar(
            "CuentaBancaria sin sucursal",
            CuentaBancaria.objects.filter(sucursal__isnull=True).order_by("-creado_en"),
            limite,
            lambda o: [
                f"#{o.pk}",
                o.nombre,
                o.banco,
                ("activa" if o.activa else "inactiva"),
                f"creado {o.creado_en:%Y-%m-%d}",
            ],
        )

        # --- Movimientos bancarios: solo los EGRESOS (debitos) son imputables por sucursal ---
        # Los creditos/acreditaciones sin sucursal_gasto son plata comun por diseno.
        self._reportar(
            "MovimientoBancario DEBITO (egreso) sin sucursal_gasto [potencialmente imputable]",
            MovimientoBancario.objects.filter(
                sucursal_gasto__isnull=True,
                tipo=MovimientoBancario.Tipo.DEBITO,
                estado=MovimientoBancario.Estado.REGISTRADO,
            ).select_related("cuenta_bancaria").order_by("-fecha"),
            limite,
            lambda o: [
                f"#{o.pk}",
                f"{o.fecha}",
                f"${o.monto}",
                o.get_clase_display(),
                o.get_origen_display(),
                (o.concepto or "")[:40],
            ],
        )
        self._contar_por_diseno(
            "MovimientoBancario CREDITO sin sucursal_gasto (comun por diseno, no requiere accion)",
            MovimientoBancario.objects.filter(
                sucursal_gasto__isnull=True,
                tipo=MovimientoBancario.Tipo.CREDITO,
            ),
        )

        # --- Caja central: solo los egresos administrativos son imputables por sucursal ---
        self._reportar(
            "MovimientoCajaCentral egreso admin sin sucursal_gasto [potencialmente imputable]",
            MovimientoCajaCentral.objects.filter(
                sucursal_gasto__isnull=True,
                tipo__in=[
                    MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                    MovimientoCajaCentral.Tipo.EGRESO_PAGO,
                ],
            ).order_by("-fecha"),
            limite,
            lambda o: [
                f"#{o.pk}",
                f"{o.fecha}",
                f"${o.monto}",
                o.get_tipo_display(),
                (o.concepto or "")[:40],
            ],
        )
        self._contar_por_diseno(
            "MovimientoCajaCentral otros tipos sin sucursal_gasto (ingresos/aportes/ajustes, no imputables)",
            MovimientoCajaCentral.objects.filter(sucursal_gasto__isnull=True).exclude(
                tipo__in=[
                    MovimientoCajaCentral.Tipo.EGRESO_ADMIN,
                    MovimientoCajaCentral.Tipo.EGRESO_PAGO,
                ]
            ),
        )

        self.stdout.write("\n" + self.style.SUCCESS(
            "Fin del reporte. Ningun dato fue modificado."
        ))

    def _reportar(self, titulo, queryset, limite, fila_fn):
        total = queryset.count()
        self.stdout.write("\n" + self.style.MIGRATE_LABEL(f"{titulo}: {total}"))
        if total == 0:
            return
        for obj in queryset[:limite]:
            self.stdout.write("  " + " | ".join(str(c) for c in fila_fn(obj)))
        if total > limite:
            self.stdout.write(self.style.WARNING(
                f"  ... {total - limite} fila(s) mas no mostradas (usar --detalle {total})."
            ))

    def _contar_por_diseno(self, titulo, queryset):
        self.stdout.write("  " + self.style.HTTP_INFO(f"{titulo}: {queryset.count()}"))
