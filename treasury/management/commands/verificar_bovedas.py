"""Chequeo de solo lectura del estado de las bovedas de efectivo.

Se corre inmediatamente despues de deployar la consolidacion: si durante la
ventana del deploy el codigo viejo llego a crear una caja, o si quedo algun
movimiento colgado de una caja desactivada, esto lo detecta y devuelve exit code
distinto de cero. No modifica ningun dato.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand

from cashops.models import Empresa
from treasury.models import CajaCentral, MovimientoCajaCentral


class Command(BaseCommand):
    help = "Verifica que haya exactamente una boveda de efectivo activa por empresa."

    def handle(self, *args, **options):
        problemas = []

        sin_empresa = CajaCentral.objects.filter(empresa__isnull=True)
        if sin_empresa.exists():
            for caja in sin_empresa:
                problemas.append(
                    f"caja id={caja.pk} '{caja.nombre}' no tiene empresa "
                    f"(activo={caja.activo}, {caja.movimientos.count()} movimientos)"
                )

        for empresa in Empresa.objects.all().order_by("pk"):
            activas = CajaCentral.objects.filter(empresa=empresa, activo=True).order_by("pk")
            if activas.count() > 1:
                ids = ", ".join(str(c.pk) for c in activas)
                problemas.append(f"{empresa.nombre} tiene {activas.count()} bovedas activas (ids {ids})")
            elif not activas.exists():
                self.stdout.write(f"  {empresa.nombre}: sin boveda todavia (se crea al primer movimiento)")
                continue
            boveda = activas.first()
            if boveda.sucursal_id:
                problemas.append(
                    f"la boveda id={boveda.pk} de {empresa.nombre} cuelga de una sucursal"
                )
            self.stdout.write(
                f"  {empresa.nombre}: boveda id={boveda.pk} '{boveda.nombre}' "
                f"saldo {boveda.saldo_actual} ({boveda.movimientos.count()} movimientos)"
            )

        colgados = MovimientoCajaCentral.objects.filter(caja_central__activo=False).count()
        if colgados:
            problemas.append(f"{colgados} movimientos cuelgan de una caja desactivada")

        sin_sucursal = MovimientoCajaCentral.objects.filter(
            tipo__in=[
                MovimientoCajaCentral.Tipo.INGRESO_CAJA,
                MovimientoCajaCentral.Tipo.AJUSTE_NEGATIVO,
            ],
            sucursal_origen__isnull=True,
        ).count()
        if sin_sucursal:
            # No es un error: un AJUSTE_NEGATIVO cargado a mano puede no tener
            # sucursal. Se informa para que se vea en el arqueo.
            self.stdout.write(
                self.style.WARNING(
                    f"  aviso: {sin_sucursal} ingresos/ajustes sin sucursal de origen"
                )
            )

        total = sum(
            (c.saldo_actual for c in CajaCentral.objects.all()), Decimal("0.00")
        )
        self.stdout.write(f"  efectivo total en todas las cajas: {total}")

        if problemas:
            self.stdout.write(self.style.ERROR("\nPROBLEMAS:"))
            for p in problemas:
                self.stdout.write(self.style.ERROR(f"  - {p}"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("\nOK: una boveda activa por empresa, sin movimientos huerfanos."))
