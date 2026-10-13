"""Regulariza deudas viejas de proveedores (US-3.20). Sin --apply no escribe nada.

Modo general: lo que tesoreria declaro pagado hasta una fecha y no esta pagado
por banco se pago en EFECTIVO desde la caja fuerte. Se registra como pago en
efectivo real (con su egreso de la boveda), a la fecha de cada factura:

    python manage.py regularizar_deudas --hasta 2026-09-23 --usuario tesoreria \\
        --informe plan.csv
    # tesoreria revisa plan.csv (puede borrar filas o cambiarles la accion)
    python manage.py regularizar_deudas --hasta 2026-09-23 --usuario tesoreria \\
        --plan plan.csv --informe aplicado.csv --apply

Modo interno: la panaderia propia nunca se paga. Cancela sus deudas abiertas
con el medio INTERNO, sin mover fondos, y con --apply marca al proveedor. Desde
ahi sus facturas nuevas nacen saldadas solas (alta automatica):

    python manage.py regularizar_deudas --proveedor-interno "OVEJA NEGRA ARMADI SRL" \\
        --usuario tesoreria --informe interno.csv [--plan interno.csv --apply]

Reversion de un lote (anula sus pagos; el efectivo vuelve a la boveda). Con
--desmarcar-interno desmarca al proveedor y anula tambien sus cancelaciones
automaticas:

    python manage.py regularizar_deudas --revertir REG-2026-09-23 --motivo "..." \\
        --usuario tesoreria [--apply]

Ver treasury/regularizacion.py para las reglas.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from cashops.models import Sucursal
from treasury.importacion_texto import dinero, normalizar, parsear_fecha
from treasury.models import Proveedor
from treasury.permissions import ensure_treasury_admin
from treasury.regularizacion import TOP_PROVEEDORES, Accion, Regularizacion, Reversion, leer_plan

MAX_ERRORES_EN_CONSOLA = 20


def _fecha(valor, opcion):
    if valor is None:
        return None
    fecha = parsear_fecha(valor)
    if fecha is None:
        try:
            fecha = date.fromisoformat(str(valor).strip())
        except ValueError:
            fecha = None
    if fecha is None:
        raise CommandError(f"{opcion}: fecha invalida '{valor}' (use AAAA-MM-DD o DD/MM/AAAA).")
    return fecha


class Command(BaseCommand):
    help = (
        "Regulariza deudas viejas de proveedores: pagos en efectivo hasta un corte, o cancelacion "
        "sin movimiento de un proveedor interno (simula salvo --apply)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--usuario", required=True, help="Usuario de tesoreria que queda como autor.")
        parser.add_argument("--informe", help="Ruta del CSV con una fila por deuda (accion, motivo, resultado).")
        parser.add_argument("--apply", action="store_true", help="Escribe. Sin esto solo simula.")
        parser.add_argument(
            "--plan",
            help=(
                "CSV revisado por tesoreria (el informe de la simulacion). Con --apply se aplican solo "
                "sus filas A_REGULARIZAR con el mismo saldo; lo demas se informa. Obligatorio para "
                "aplicar el modo general."
            ),
        )
        parser.add_argument("--hasta", help="Modo general: facturas con fecha de emision hasta esta fecha.")
        parser.add_argument(
            "--cargadas-hasta",
            help="Solo deudas cargadas en Gerayse hasta esta fecha (por defecto, hoy).",
        )
        parser.add_argument("--lote", help="Referencia de los pagos. Por defecto REG-<hasta> o INTERNO-<id>.")
        parser.add_argument(
            "--fecha-pago",
            help="Modo general: una sola fecha para todos los pagos. Por defecto, la fecha de cada factura.",
        )
        parser.add_argument(
            "--sucursal", action="append", default=[], help="Codigo de sucursal (repetible). Por defecto, todas."
        )
        parser.add_argument(
            "--excluir-proveedor",
            action="append",
            default=[],
            help="Texto de la razon social a excluir (repetible). Se suma a las exclusiones fijas.",
        )
        parser.add_argument(
            "--incluir-sin-sucursal",
            action="store_true",
            help="Incluye las deudas sin sucursal (el efectivo sale de la boveda de su caja de origen).",
        )
        parser.add_argument(
            "--incluir-posibles-pagos",
            action="store_true",
            help="Incluye las facturas con debitos del banco del mismo proveedor sin asignar.",
        )
        parser.add_argument(
            "--incluir-posibles-duplicadas",
            action="store_true",
            help="Incluye las facturas que parecen cargadas dos veces.",
        )
        parser.add_argument(
            "--proveedor-interno",
            help="Modo interno: razon social (o id) del proveedor propio. Usa el medio INTERNO, sin plata.",
        )
        parser.add_argument("--revertir", help="Lote a revertir: anula sus pagos y reabre las deudas.")
        parser.add_argument("--motivo", help="Motivo de la anulacion al revertir.")
        parser.add_argument(
            "--desmarcar-interno",
            action="store_true",
            help=(
                "Al revertir un lote del modo interno, desmarca tambien al proveedor como interno y anula "
                "las cancelaciones INTERNO que el sistema registro solo para sus facturas nuevas."
            ),
        )

    def handle(self, *args, **options):
        actor = get_user_model().objects.filter(username=options["usuario"]).first()
        if actor is None:
            raise CommandError(f"No existe el usuario '{options['usuario']}'.")
        try:
            ensure_treasury_admin(actor)
        except PermissionDenied as error:
            raise CommandError(f"El usuario no puede operar tesoreria: {error}") from error

        if options["revertir"]:
            self._revertir(actor, options)
        else:
            self._regularizar(actor, options)

    # --- regularizacion -----------------------------------------------------
    def _regularizar(self, actor, options):
        if options["motivo"] or options["desmarcar_interno"]:
            raise CommandError("--motivo y --desmarcar-interno van solo con --revertir.")
        proveedor = self._proveedor_interno(options["proveedor_interno"])
        if proveedor is None and not options["hasta"]:
            raise CommandError("Indique --hasta (modo general), --proveedor-interno o --revertir.")
        if proveedor is not None and (options["hasta"] or options["fecha_pago"]):
            raise CommandError(
                "El modo interno no lleva --hasta ni --fecha-pago: toma todas las deudas abiertas del "
                "proveedor y las cancela a la fecha de cada factura."
            )
        if options["apply"] and proveedor is None and not options["plan"]:
            # El modo general mueve la caja fuerte: se aplica solo lo que
            # tesoreria reviso, nunca una seleccion recalculada a ciegas.
            raise CommandError(
                "Para aplicar el modo general hace falta --plan con el informe revisado por tesoreria."
            )
        self._validar_sucursales(options["sucursal"])
        try:
            regularizacion = Regularizacion(
                actor=actor,
                hasta=_fecha(options["hasta"], "--hasta"),
                proveedor_interno=proveedor,
                lote=options["lote"],
                fecha_pago=_fecha(options["fecha_pago"], "--fecha-pago"),
                cargadas_hasta=_fecha(options["cargadas_hasta"], "--cargadas-hasta"),
                sucursales=options["sucursal"],
                excluir_proveedores=options["excluir_proveedor"],
                incluir_sin_sucursal=options["incluir_sin_sucursal"],
                incluir_posibles_pagos=options["incluir_posibles_pagos"],
                incluir_posibles_duplicadas=options["incluir_posibles_duplicadas"],
            )
            aprobadas = leer_plan(options["plan"], regularizacion.medio) if options["plan"] else None
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error

        regularizacion.planificar()
        if aprobadas is not None:
            regularizacion.restringir_a_plan(aprobadas)

        self.stdout.write(
            self.style.WARNING("APLICANDO" if options["apply"] else "SIMULACION (no se escribe nada)")
        )
        if regularizacion.interno:
            self.stdout.write(
                f"Modo interno: {proveedor.razon_social} (#{proveedor.pk}), medio INTERNO sin movimiento "
                f"de fondos, lote {regularizacion.lote}."
            )
        else:
            fecha_pago = (
                f"{regularizacion.fecha_pago:%d/%m/%Y}" if regularizacion.fecha_pago else "la de cada factura"
            )
            self.stdout.write(
                f"Pagos en EFECTIVO desde la caja fuerte. Corte {regularizacion.hasta:%d/%m/%Y}, cargadas "
                f"hasta {regularizacion.cargadas_hasta:%d/%m/%Y}, fecha de pago: {fecha_pago}, "
                f"lote {regularizacion.lote}."
            )
        if aprobadas is not None:
            self.stdout.write(f"Plan revisado: {options['plan']} ({len(aprobadas)} deudas aprobadas).")

        if options["apply"]:
            regularizacion.aplicar()

        self._imprimir_resumen(regularizacion)
        if regularizacion.interno:
            self._imprimir_interno(regularizacion, options["apply"])
        else:
            self._imprimir_bovedas(regularizacion.bovedas(), aplicado=options["apply"])
            self._imprimir_avisos_por_mes(regularizacion)
            self._imprimir_top_proveedores(regularizacion)
        self._imprimir_errores(regularizacion)
        self._escribir_informe(regularizacion, options["informe"])

    def _proveedor_interno(self, valor):
        if not valor:
            return None
        valor = valor.strip()
        if valor.isdigit():
            proveedor = Proveedor.objects.filter(pk=int(valor)).first()
            if proveedor is None:
                raise CommandError(f"No existe el proveedor #{valor}.")
            return proveedor
        buscado = normalizar(valor)
        exactos = [p for p in Proveedor.objects.order_by("pk") if normalizar(p.razon_social) == buscado]
        if len(exactos) == 1:
            return exactos[0]
        if len(exactos) > 1:
            ids = ", ".join(f"#{p.pk} {p.razon_social}" for p in exactos)
            raise CommandError(f"Hay varios proveedores con ese nombre: {ids}. Indique el id.")
        parecidos = [
            f"#{p.pk} {p.razon_social}"
            for p in Proveedor.objects.order_by("razon_social")
            if buscado and buscado in normalizar(p.razon_social)
        ]
        sugerencia = f" Parecidos: {', '.join(parecidos[:10])}." if parecidos else ""
        raise CommandError(f"No existe el proveedor '{valor}'.{sugerencia}")

    def _validar_sucursales(self, codigos):
        existentes = {codigo.upper() for codigo in Sucursal.objects.values_list("codigo", flat=True)}
        faltan = [codigo for codigo in codigos if codigo.strip() and codigo.strip().upper() not in existentes]
        if faltan:
            raise CommandError(f"No existen las sucursales: {', '.join(faltan)}.")

    # --- reversion ----------------------------------------------------------
    def _revertir(self, actor, options):
        incompatibles = [
            opcion
            for opcion in (
                "hasta",
                "plan",
                "lote",
                "fecha_pago",
                "cargadas_hasta",
                "proveedor_interno",
            )
            if options[opcion]
        ] + [
            opcion
            for opcion in ("sucursal", "excluir_proveedor")
            if options[opcion]
        ] + [
            opcion
            for opcion in ("incluir_sin_sucursal", "incluir_posibles_pagos", "incluir_posibles_duplicadas")
            if options[opcion]
        ]
        if incompatibles:
            nombres = ", ".join(f"--{opcion.replace('_', '-')}" for opcion in incompatibles)
            raise CommandError(f"--revertir no se combina con {nombres}: revierte el lote entero.")
        try:
            reversion = Reversion(
                actor=actor,
                lote=options["revertir"],
                motivo=options["motivo"] or "",
                desmarcar_interno=options["desmarcar_interno"],
            )
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error

        reversion.planificar()
        self.stdout.write(
            self.style.WARNING("APLICANDO" if options["apply"] else "SIMULACION (no se escribe nada)")
        )
        self.stdout.write(f"Reversion del lote {reversion.lote}. Motivo: {reversion.motivo}")
        if not reversion.filas:
            self.stdout.write("El lote no tiene pagos: no hay nada que revertir.")
        if options["apply"]:
            reversion.aplicar()

        self._imprimir_resumen(reversion)
        self._imprimir_bovedas(reversion.bovedas(), aplicado=options["apply"], reversion=True)
        if reversion.proveedores_desmarcados:
            self.stdout.write("")
            self.stdout.write(
                "Proveedores desmarcados como internos: " + ", ".join(reversion.proveedores_desmarcados)
            )
        else:
            internos = reversion.proveedores_internos()
            if internos:
                self.stdout.write("")
                self.stdout.write(
                    "Siguen marcados como internos (sus facturas nuevas se cancelan sin movimiento): "
                    + ", ".join(internos)
                    + ". Para desmarcarlos, agregue --desmarcar-interno."
                )
        self._imprimir_errores(reversion)
        self._escribir_informe(reversion, options["informe"])

    # --- consola ------------------------------------------------------------
    def _imprimir_resumen(self, proceso):
        self.stdout.write("")
        self.stdout.write(f"{'accion':<26} {'deudas':>7} {'importe':>18}")
        for accion, (cantidad, total) in sorted(proceso.resumen().items()):
            self.stdout.write(f"{accion:<26} {cantidad:>7} {dinero(total):>18}")

    def _imprimir_bovedas(self, bovedas, *, aplicado, reversion=False):
        if not bovedas:
            return
        self.stdout.write("")
        if reversion:
            titulo = "Caja fuerte por empresa (el efectivo de los pagos anulados vuelve a la boveda):"
            columna = "vuelve"
        else:
            titulo = "Caja fuerte por empresa (comparar con el arqueo real):"
            columna = "registrado" if aplicado else "a registrar"
        self.stdout.write(titulo)
        quedaria = "saldo despues" if aplicado else "quedaria"
        self.stdout.write(f"  {'empresa':<34} {'saldo hoy':>18} {columna:>18} {quedaria:>18}")
        for fila in bovedas:
            self.stdout.write(
                f"  {fila['empresa'][:34]:<34} {dinero(fila['antes']):>18} {dinero(fila['efectivo']):>18} "
                f"{dinero(fila['despues']):>18}"
            )

    def _imprimir_avisos_por_mes(self, regularizacion):
        avisos = regularizacion.avisos_por_mes()
        if not avisos:
            return
        self.stdout.write("")
        self.stdout.write(
            "Aviso por empresa y mes del pago: plata que ya salio sin factura atada. Si en el mes hay "
            "debitos sin asignar o egresos de la boveda, alguna factura pudo pagarse con ellos:"
        )
        self.stdout.write(
            f"  {'mes':<8} {'empresa':<28} {'efectivo a registrar':>20} {'debitos sin atar':>18} "
            f"{'egresos boveda':>18}"
        )
        for aviso in avisos:
            self.stdout.write(
                f"  {aviso['mes']:%m/%Y}  {aviso['empresa'][:28]:<28} {dinero(aviso['a_registrar']):>20} "
                f"{dinero(aviso['debitos_sin_atar']):>18} {dinero(aviso['egresos_admin']):>18}"
            )

    def _imprimir_top_proveedores(self, regularizacion):
        top = regularizacion.top_proveedores()
        if not top:
            return
        self.stdout.write("")
        self.stdout.write(
            f"Proveedores con mas importe a registrar (hasta {TOP_PROVEEDORES}; revisarlos antes de aplicar):"
        )
        for nombre, cantidad, total in top:
            self.stdout.write(f"  {nombre[:48]:<48} {cantidad:>5} {dinero(total):>18}")

    def _imprimir_interno(self, regularizacion, aplicado):
        self.stdout.write("")
        avisos_pago = [f for f in regularizacion.filas if f.accion == Accion.AVISO_PAGO_REAL]
        otros = [f for f in regularizacion.filas if f.accion == Accion.AVISO_OTRO_PROVEEDOR]
        if avisos_pago:
            self.stdout.write(
                f"{len(avisos_pago)} pagos con plata ya registrados a este proveedor: ver AVISO_PAGO_REAL "
                "en el informe (decidir si se anulan)."
            )
        for fila in otros:
            self.stdout.write(f"Otro proveedor parecido: {fila.proveedor}. {fila.motivo}")
        if regularizacion.categorias_sin_rubro:
            self.stdout.write(
                f"{regularizacion.categorias_sin_rubro} deudas a cancelar tienen una categoria sin rubro: "
                "no cuentan como gasto en la lectura economica."
            )
        if aplicado and regularizacion.proveedor_marcado:
            self.stdout.write(f"Proveedor marcado como interno: {regularizacion.proveedor_interno.razon_social}.")
        elif not aplicado:
            self.stdout.write("Con --apply el proveedor queda marcado como interno.")

    def _imprimir_errores(self, proceso):
        errores = proceso.errores()
        if not errores:
            return
        self.stdout.write("")
        for fila in errores[:MAX_ERRORES_EN_CONSOLA]:
            detalle = fila.resultado or fila.motivo
            self.stdout.write(self.style.ERROR(f"{fila.accion} deuda #{fila.deuda_id}: {detalle}"))
        if len(errores) > MAX_ERRORES_EN_CONSOLA:
            self.stdout.write(self.style.ERROR(f"... y {len(errores) - MAX_ERRORES_EN_CONSOLA} mas en el informe."))

    def _escribir_informe(self, proceso, ruta):
        if not ruta:
            return
        proceso.escribir_informe(ruta)
        self.stdout.write("")
        self.stdout.write(f"Informe: {ruta}")
