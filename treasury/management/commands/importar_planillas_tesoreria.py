"""Importa las planillas de banco y de efectivo central de tesoreria.

Sin --apply no escribe nada: arma el plan y deja el informe para revisar.

    python manage.py importar_planillas_tesoreria \
        --banco banco_armadi_ago26.csv --cuenta 1 \
        --efectivo-dir carpeta_con_efectivo_EC1.csv_etc \
        --usuario admin --informe plan.csv [--apply]

Ver treasury/importacion_planillas.py para las reglas.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from treasury.importacion_planillas import (
    Accion,
    Importador,
    dinero,
    filas_efectivo_de_directorio,
    leer_planilla_banco,
)
from treasury.models import CuentaBancaria, Proveedor
from treasury.permissions import ensure_treasury_admin


class Command(BaseCommand):
    help = "Importa las planillas de banco y efectivo central de tesoreria (dry-run salvo --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--banco", help="CSV de la pestana del mes de la planilla MOVIMIENTOS.")
        parser.add_argument("--cuenta", type=int, help="Id de la cuenta bancaria de la planilla de banco.")
        parser.add_argument("--efectivo-dir", help="Carpeta con los efectivo_<SUCURSAL>.csv.")
        parser.add_argument("--usuario", required=True, help="Usuario de tesoreria que queda como autor.")
        parser.add_argument("--informe", help="Ruta del CSV con el plan fila por fila.")
        parser.add_argument("--apply", action="store_true", help="Escribe. Sin esto solo planifica.")

    def handle(self, *args, **options):
        if not options["banco"] and not options["efectivo_dir"]:
            raise CommandError("Indica al menos --banco o --efectivo-dir.")
        actor = get_user_model().objects.filter(username=options["usuario"]).first()
        if actor is None:
            raise CommandError(f"No existe el usuario '{options['usuario']}'.")
        try:
            ensure_treasury_admin(actor)
        except PermissionDenied as error:
            raise CommandError(f"El usuario no puede operar tesoreria: {error}") from error

        cuenta = None
        if options["banco"]:
            if not options["cuenta"]:
                raise CommandError("La planilla de banco necesita --cuenta.")
            cuenta = CuentaBancaria.objects.filter(pk=options["cuenta"]).first()
            if cuenta is None:
                raise CommandError(f"No existe la cuenta bancaria {options['cuenta']}.")

        importador = Importador(actor=actor, cuenta_banco=cuenta, etiqueta_banco="banco")
        if options["banco"]:
            importador.agregar_banco(leer_planilla_banco(options["banco"]))
        if options["efectivo_dir"]:
            importador.agregar_efectivo(filas_efectivo_de_directorio(options["efectivo_dir"]))

        importador.planificar()
        modo = "APLICANDO" if options["apply"] else "SIMULACION (no se escribe nada)"
        self.stdout.write(self.style.WARNING(modo))
        if options["apply"]:
            importador.aplicar()

        self._imprimir_resumen(importador)
        if options["informe"]:
            importador.escribir_informe(options["informe"])
            self.stdout.write(f"Informe: {options['informe']}")

    def _imprimir_resumen(self, importador):
        self.stdout.write("")
        self.stdout.write(f"{'origen':<9} {'accion':<14} {'ops':>5} {'importe':>18}")
        for (origen, accion), (cantidad, total) in sorted(importador.resumen().items()):
            self.stdout.write(f"{origen:<9} {accion:<14} {cantidad:>5} {dinero(total):>18}")
        errores = [op for op in importador.operaciones if op.accion == Accion.ERROR]
        for op in errores[:20]:
            self.stdout.write(self.style.ERROR(f"ERROR {op.origen} {op.planilla} fila {op.fila}: {op.resultado}"))
        sobrantes = importador.pagos_registrados_sin_fila()
        if sobrantes:
            nombres = dict(Proveedor.objects.filter(pk__in={s[0] for s in sobrantes}).values_list("pk", "razon_social"))
            codigos = {s.pk: s.codigo for s in importador.sucursales.values()}
            self.stdout.write("")
            self.stdout.write("Pagos en efectivo ya registrados en Gerayse que ninguna fila explica:")
            for prov_id, suc_id, mes, restante in sorted(sobrantes, key=lambda s: (s[2], s[1] or 0)):
                self.stdout.write(
                    f"  {mes:%m/%Y} {codigos.get(suc_id, 'sin sucursal'):<9} "
                    f"{nombres.get(prov_id, prov_id)}: {dinero(restante)}"
                )
