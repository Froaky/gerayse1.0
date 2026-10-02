"""Importa las planillas de banco y de efectivo central de tesoreria.

Sin --apply no escribe nada: arma el plan y deja el informe para revisar.

    python manage.py importar_planillas_tesoreria \
        --extracto extracto_armadi_ago26.xlsx --desglose transferencias_ago26.xlsx --cuenta 1 \
        --efectivo efectivo_tesoreria.xlsx \
        --reparto-impuestos "EC1=32,YO=7,EC2=22,EB=39" \
        --reparto-sueldos "EC1=27,YO=4,EC2=25,EB=34,EB2=10" \
        --usuario admin --informe plan.csv [--apply]

Sin --extracto, el banco se carga desde las filas del desglose (modo anterior).
Ver treasury/importacion_planillas.py para las reglas.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from treasury.extracto_macro import leer_extracto_macro
from treasury.importacion_planillas import (
    Accion,
    Importador,
    dinero,
    filas_efectivo_de_directorio,
    leer_libro_efectivo,
    leer_planilla_banco,
    leer_reparto,
)
from treasury.models import CuentaBancaria, Proveedor
from treasury.permissions import ensure_treasury_admin


class Command(BaseCommand):
    help = "Importa las planillas de banco y efectivo central de tesoreria (simula salvo --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--extracto", help="Extracto del banco (Excel de Ultimos movimientos del Macro).")
        parser.add_argument("--desglose", help="Planilla de transferencias con el reparto por sucursal (Excel o CSV).")
        parser.add_argument("--banco", help="Alias de --desglose (nombre anterior).")
        parser.add_argument("--cuenta", type=int, help="Id de la cuenta bancaria del extracto o del desglose.")
        parser.add_argument("--efectivo", help="Libro de efectivo central con una solapa por sucursal (Excel).")
        parser.add_argument("--efectivo-dir", help="Carpeta con los efectivo_<SUCURSAL>.csv (formato anterior).")
        parser.add_argument("--reparto-impuestos", help='Clave para cargos del banco sin desglose, p.ej. "EC1=32,YO=7".')
        parser.add_argument("--reparto-sueldos", help='Clave para sueldos sin desglose, p.ej. "EC1=27,EB2=10".')
        parser.add_argument("--usuario", required=True, help="Usuario de tesoreria que queda como autor.")
        parser.add_argument("--informe", help="Ruta del CSV con el plan fila por fila.")
        parser.add_argument("--apply", action="store_true", help="Escribe. Sin esto solo planifica.")

    def handle(self, *args, **options):
        desglose = options["desglose"] or options["banco"]
        if not (options["extracto"] or desglose or options["efectivo"] or options["efectivo_dir"]):
            raise CommandError("Indica al menos --extracto, --desglose, --efectivo o --efectivo-dir.")
        actor = get_user_model().objects.filter(username=options["usuario"]).first()
        if actor is None:
            raise CommandError(f"No existe el usuario '{options['usuario']}'.")
        try:
            ensure_treasury_admin(actor)
        except PermissionDenied as error:
            raise CommandError(f"El usuario no puede operar tesoreria: {error}") from error

        cuenta = None
        if options["extracto"] or desglose:
            if not options["cuenta"]:
                raise CommandError("El banco necesita --cuenta.")
            cuenta = CuentaBancaria.objects.filter(pk=options["cuenta"]).first()
            if cuenta is None:
                raise CommandError(f"No existe la cuenta bancaria {options['cuenta']}.")

        try:
            repartos = {
                nombre: leer_reparto(options[f"reparto_{nombre}"])
                for nombre in ("impuestos", "sueldos")
                if options[f"reparto_{nombre}"]
            }
            importador = Importador(actor=actor, cuenta_banco=cuenta, etiqueta_banco="banco", repartos=repartos)
            if options["extracto"]:
                importador.agregar_extracto(leer_extracto_macro(options["extracto"]))
            if desglose:
                importador.agregar_banco(leer_planilla_banco(desglose))
            if options["efectivo"]:
                importador.agregar_efectivo(leer_libro_efectivo(options["efectivo"]))
            if options["efectivo_dir"]:
                importador.agregar_efectivo(filas_efectivo_de_directorio(options["efectivo_dir"]))
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error

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

        conciliacion = importador.conciliacion_extracto()
        if conciliacion:
            self.stdout.write("")
            self.stdout.write(
                f"Extracto: saldo inicial {dinero(conciliacion['saldo_inicial'])}, "
                f"creditos {dinero(conciliacion['creditos'])}, debitos {dinero(conciliacion['debitos'])}, "
                f"saldo final {dinero(conciliacion['saldo_final'])}"
            )
            for accion, valores in sorted(conciliacion["por_accion"].items()):
                self.stdout.write(
                    f"  {accion:<14} creditos {dinero(valores['CREDITO']):>18}  debitos {dinero(valores['DEBITO']):>18}"
                )
            self.stdout.write(
                f"  sin explicar   creditos {dinero(conciliacion['sin_explicar_creditos']):>18}  "
                f"debitos {dinero(conciliacion['sin_explicar_debitos']):>18}"
            )

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
        duplicados = importador.posibles_duplicados_efectivo()
        if duplicados:
            self.stdout.write("")
            self.stdout.write("Filas de efectivo repetidas (misma solapa, fecha e importe; se cargan igual):")
            for (planilla, fecha, monto), filas in duplicados:
                conceptos = " / ".join(f.concepto for f in filas)
                self.stdout.write(f"  {planilla:<5} {fecha:%d/%m/%Y} {dinero(monto):>14}  {conceptos}")
