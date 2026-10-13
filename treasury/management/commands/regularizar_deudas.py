"""Regulariza deudas viejas de proveedores (US-3.20). Sin --apply no escribe nada.

Modo general: lo que tesoreria declaro pagado hasta una fecha y no esta pagado
por banco se pago en EFECTIVO desde la caja fuerte. Antes del efectivo, las
facturas que tienen un debito del banco del mismo proveedor y empresa sin
asignar se ATAN a ese debito (se pagaron por banco; --sin-atar-banco lo apaga).
Un debito que hoy cuenta como gasto (o esta pendiente de imputar) solo se ata
si sus facturas lo consumen entero y tienen rubro; si no, quedan para revisar
(--atar-con-sobrante acepta el sobrante). El resto se registra como pago en
efectivo real (con su egreso de la boveda), a la fecha de cada factura, salvo
las que pudieron pagarse con un egreso de la boveda ya cargado:

    python manage.py regularizar_deudas --hasta 2026-09-23 --usuario tesoreria \\
        --informe plan.csv
    # tesoreria revisa plan.csv (puede borrar filas o cambiarles la accion)
    python manage.py regularizar_deudas --hasta 2026-09-23 --usuario tesoreria \\
        --plan plan.csv --informe aplicado.csv --apply

Modo interno: la panaderia propia nunca se paga. Cancela sus deudas abiertas
con el medio INTERNO, sin mover fondos, y con --apply marca al proveedor (solo
si cancelo al menos una deuda del plan y ninguna dio error). Desde ahi sus
facturas nuevas nacen saldadas solas (alta automatica). No lleva filtros, y
--apply tambien exige el plan revisado. Si la razon social no dice OVEJA NEGRA,
hay que repetirla con --confirmar:

    python manage.py regularizar_deudas --proveedor-interno "OVEJA NEGRA ARMADI SRL" \\
        --usuario tesoreria --informe interno.csv
    python manage.py regularizar_deudas --proveedor-interno "OVEJA NEGRA ARMADI SRL" \\
        --usuario tesoreria --plan interno.csv --informe interno_aplicado.csv --apply

Reversion de un lote (anula sus pagos; el efectivo vuelve a la boveda y los
debitos atados vuelven a quedar sin asignar e imputados como estaban). Con
--desmarcar-interno desmarca al proveedor y anula tambien sus cancelaciones
automaticas:

    python manage.py regularizar_deudas --revertir REG-2026-09-23 --motivo "..." \\
        --usuario tesoreria [--apply]

Ver treasury/regularizacion.py para las reglas.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError

from cashops.models import Sucursal
from treasury.importacion_texto import dinero, normalizar, parsear_fecha
from treasury.models import Proveedor
from treasury.permissions import ensure_treasury_admin
from treasury.regularizacion import (
    PATRON_INTERNO,
    TOP_PROVEEDORES,
    Accion,
    Regularizacion,
    Reversion,
    leer_ataduras,
    leer_plan,
)

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
                "sus filas A_ATAR_BANCO (al debito del plan, con el mismo saldo) y A_REGULARIZAR que hoy "
                "siguen igual (saldo, importe, proveedor, sucursal, empresa y fecha de pago); lo demas se "
                "informa. Obligatorio para aplicar."
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
            "--incluir-posibles-pagos-en-efectivo",
            action="store_true",
            help=(
                "Incluye las facturas con un egreso de la boveda sin factura (EGRESO_ADMIN) de la misma "
                "sucursal e importe: quedan con aviso para que tesoreria las saque del plan si ya se pagaron."
            ),
        )
        parser.add_argument(
            "--sin-atar-banco",
            action="store_true",
            help=(
                "Modo general: no ata facturas a los debitos del banco sin asignar (comportamiento "
                "anterior: esas facturas quedan como POSIBLE_PAGO_EN_GERAYSE)."
            ),
        )
        parser.add_argument(
            "--atar-con-sobrante",
            action="store_true",
            help=(
                "Modo general: ata tambien un debito que cuenta como gasto (o esta pendiente de imputar) "
                "aunque le sobre importe. Ese sobrante deja de contar como gasto, como al pagar desde el "
                "movimiento en la pantalla. Por defecto esas facturas quedan como POSIBLE_PAGO_EN_GERAYSE."
            ),
        )
        parser.add_argument(
            "--proveedor-interno",
            help="Modo interno: razon social (o id) del proveedor propio. Usa el medio INTERNO, sin plata.",
        )
        parser.add_argument(
            "--confirmar",
            help=(
                "Modo interno con --apply: la razon social del proveedor, repetida, cuando no dice "
                "OVEJA NEGRA (marcarlo cancela sin plata todas sus facturas futuras)."
            ),
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
        if proveedor is None and options["confirmar"]:
            raise CommandError("--confirmar va solo con --proveedor-interno.")
        if proveedor is not None and options["sin_atar_banco"]:
            raise CommandError("--sin-atar-banco es solo del modo general: el modo interno no paga con plata.")
        if options["atar_con_sobrante"] and (proveedor is not None or options["sin_atar_banco"]):
            raise CommandError("--atar-con-sobrante va solo con la atadura al banco del modo general.")
        if options["apply"] and not options["plan"]:
            # El modo general mueve la caja fuerte y el interno cancela deudas y
            # marca al proveedor para siempre: se aplica solo lo que tesoreria
            # reviso, nunca una seleccion recalculada a ciegas.
            raise CommandError("Para aplicar hace falta --plan con el informe revisado por tesoreria.")
        if proveedor is not None and options["apply"]:
            self._confirmar_proveedor_interno(proveedor, options["confirmar"])
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
                incluir_posibles_pagos_en_efectivo=options["incluir_posibles_pagos_en_efectivo"],
                atar_banco=not options["sin_atar_banco"],
                atar_con_sobrante=options["atar_con_sobrante"],
            )
            aprobadas = leer_plan(options["plan"], regularizacion.medio) if options["plan"] else None
            ataduras = leer_ataduras(options["plan"]) if options["plan"] else None
            if ataduras and not regularizacion.atar_banco:
                raise ValidationError(
                    "El plan revisado tiene filas A_ATAR_BANCO y esta corrida no ata al banco "
                    "(modo interno o --sin-atar-banco): use el plan de este mismo modo."
                )
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error

        regularizacion.planificar()
        if aprobadas is not None:
            try:
                regularizacion.restringir_a_plan(aprobadas, ataduras)
            except ValidationError as error:
                raise CommandError(" ".join(error.messages)) from error

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
            if regularizacion.atar_banco:
                self.stdout.write(
                    "Antes del efectivo se atan al banco las facturas con un debito del mismo proveedor y "
                    "empresa sin asignar (A_ATAR_BANCO); las que no se pueden atar quedan como "
                    "POSIBLE_PAGO_EN_GERAYSE."
                )
                if regularizacion.atar_con_sobrante:
                    self.stdout.write(
                        "Con --atar-con-sobrante: un debito que cuenta como gasto se ata aunque le sobre "
                        "importe, y ese sobrante deja de contar como gasto."
                    )
            else:
                self.stdout.write("Sin atar al banco (--sin-atar-banco).")
        if aprobadas is not None:
            detalle = f"{len(aprobadas)} deudas aprobadas"
            if not regularizacion.interno:
                detalle += f", {len(ataduras)} ataduras al banco"
            self.stdout.write(f"Plan revisado: {options['plan']} ({detalle}).")

        if options["apply"]:
            regularizacion.aplicar()

        self._imprimir_resumen(regularizacion)
        if regularizacion.interno:
            self._imprimir_interno(regularizacion, options["apply"])
        else:
            self._imprimir_bovedas(regularizacion.bovedas(), aplicado=options["apply"])
            self._imprimir_por_mes(regularizacion, aplicado=options["apply"])
            self._imprimir_sobrantes(regularizacion, aplicado=options["apply"])
            self._imprimir_no_atados(regularizacion)
            self._imprimir_avisos_por_mes(regularizacion)
            self._imprimir_top_proveedores(regularizacion)
            self._imprimir_sin_rubro(regularizacion)
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

    def _confirmar_proveedor_interno(self, proveedor, confirmacion):
        """Un id mal tipeado marcaria como interno a un proveedor real: todas sus
        facturas futuras nacerian pagadas sin plata. Si la razon social no es la
        de la panaderia, se pide repetirla."""
        nombre = normalizar(proveedor.razon_social)
        if PATRON_INTERNO.search(nombre):
            return
        if confirmacion and normalizar(confirmacion) == nombre:
            return
        raise CommandError(
            f"'{proveedor.razon_social}' (#{proveedor.pk}) no parece el proveedor propio (la razon social no "
            "dice OVEJA NEGRA). Marcarlo como interno cancela sin plata sus deudas y todas sus facturas "
            f"futuras. Si es correcto, repita la razon social con --confirmar \"{proveedor.razon_social}\"."
        )

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
                "confirmar",
            )
            if options[opcion]
        ] + [
            opcion
            for opcion in ("sucursal", "excluir_proveedor")
            if options[opcion]
        ] + [
            opcion
            for opcion in (
                "incluir_sin_sucursal",
                "incluir_posibles_pagos",
                "incluir_posibles_duplicadas",
                "incluir_posibles_pagos_en_efectivo",
                "sin_atar_banco",
                "atar_con_sobrante",
            )
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
        self._imprimir_ataduras_revertidas(reversion, aplicado=options["apply"])
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
        negativas = []
        for fila in bovedas:
            linea = (
                f"  {fila['empresa'][:34]:<34} {dinero(fila['antes']):>18} {dinero(fila['efectivo']):>18} "
                f"{dinero(fila['despues']):>18}"
            )
            if fila["despues"] < 0:
                negativas.append(fila["empresa"])
                linea = self.style.ERROR(linea)
            self.stdout.write(linea)
        if negativas and not reversion:
            self.stdout.write(
                self.style.ERROR(
                    f"Caja fuerte en negativo: {', '.join(negativas)}. Mientras siga negativa, el sistema no "
                    "deja revertir la validacion de ninguna caja de esa empresa (solo anular la caja entera). "
                    "Antes de aplicar, decidir con tesoreria si se registra el prestamo entre empresas (un "
                    "egreso en la boveda que presta y un aporte en la que recibe) para que ninguna quede negativa."
                )
            )

    def _imprimir_por_mes(self, regularizacion, *, aplicado):
        filas = regularizacion.por_mes()
        if not filas:
            return
        self.stdout.write("")
        self.stdout.write(
            "Por empresa y mes del pago: lo que se ata al banco, lo que va a efectivo y la caja fuerte al "
            "cierre del mes (comparar con el arqueo de ese mes):"
        )
        atado = "atado al banco" if aplicado else "a atar al banco"
        efectivo = "efectivo" if aplicado else "a efectivo"
        proyectada = "despues" if aplicado else "proyectada"
        self.stdout.write(
            f"  {'mes':<8} {'empresa':<28} {atado:>18} {efectivo:>18} {'caja fuerte hoy':>18} {proyectada:>18}"
        )
        for fila in filas:
            self.stdout.write(
                f"  {fila['mes']:%m/%Y}  {fila['empresa'][:28]:<28} {dinero(fila['atado_banco']):>18} "
                f"{dinero(fila['efectivo']):>18} {dinero(fila['boveda_hoy']):>18} "
                f"{dinero(fila['boveda_proyectada']):>18}"
            )
        total_atado = sum((fila["atado_banco"] for fila in filas), Decimal("0.00"))
        total_efectivo = sum((fila["efectivo"] for fila in filas), Decimal("0.00"))
        self.stdout.write(f"  {'total':<37} {dinero(total_atado):>18} {dinero(total_efectivo):>18}")

    def _imprimir_sobrantes(self, regularizacion, *, aplicado):
        sobrantes = regularizacion.sobrantes_de_debitos()
        if not sobrantes["debitos"]:
            return
        self.stdout.write("")
        verbo = "quedaron" if aplicado else "quedan"
        self.stdout.write(
            f"{sobrantes['debitos']} debitos atados {verbo} con importe sin asignar: "
            f"{dinero(sobrantes['sobrante'])} en total."
        )
        if sobrantes["debitos_de_gasto"]:
            self.stdout.write(
                f"  De esos, {sobrantes['debitos_de_gasto']} contaban como gasto o estaban pendientes de "
                f"imputar (--atar-con-sobrante): sus {dinero(sobrantes['sobrante_de_gasto'])} sobrantes "
                "dejan de contar como gasto al atarlos (revisar si pagan facturas que faltan cargar; ver el "
                "aviso en el informe)."
            )

    def _imprimir_no_atados(self, regularizacion):
        no_atados = regularizacion.debitos_no_atados
        if not no_atados:
            return
        sobrante = sum((d["sobrante"] for d in no_atados), Decimal("0.00"))
        facturas = sum(d["facturas"] for d in no_atados)
        importe = sum((d["importe"] for d in no_atados), Decimal("0.00"))
        self.stdout.write("")
        self.stdout.write(
            f"{len(no_atados)} debitos que cuentan como gasto (o estan pendientes de imputar) no se atan: "
            f"les sobrarian {dinero(sobrante)} sin asignar, que dejarian de contar como gasto. Sus "
            f"{facturas} facturas ({dinero(importe)}) quedan como POSIBLE_PAGO_EN_GERAYSE para revisar "
            "(falta cargar alguna factura?). Con --atar-con-sobrante se atan igual."
        )

    def _imprimir_sin_rubro(self, regularizacion):
        if regularizacion.categorias_sin_rubro:
            self.stdout.write("")
            self.stdout.write(
                f"{regularizacion.categorias_sin_rubro} deudas a pagar tienen una categoria sin rubro: no "
                "cuentan como gasto en la lectura economica."
            )
        if regularizacion.sin_rubro_sin_atar:
            self.stdout.write("")
            self.stdout.write(
                f"{regularizacion.sin_rubro_sin_atar} facturas con categoria sin rubro no se atan a su debito "
                "de gasto (el gasto desapareceria de la lectura economica): asigneles rubro y vuelva a "
                "simular. Quedan como POSIBLE_PAGO_EN_GERAYSE."
            )

    def _imprimir_ataduras_revertidas(self, reversion, *, aplicado):
        ataduras = [f for f in reversion.filas if f.debito_id and f.accion == Accion.A_REVERTIR]
        if not ataduras:
            return
        debitos = {f.debito_id for f in ataduras}
        self.stdout.write("")
        if aplicado:
            self.stdout.write(
                f"Ataduras al banco anuladas: {len(ataduras)} pagos en {len(debitos)} debitos; "
                f"{len(reversion.debitos_restaurados)} debitos volvieron a su imputacion anterior."
            )
        else:
            self.stdout.write(
                f"Ataduras al banco a anular: {len(ataduras)} pagos en {len(debitos)} debitos. Los debitos "
                "vuelven a quedar sin asignar e imputados como estaban (el saldo del banco no cambia)."
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
        nombre = regularizacion.proveedor_interno.razon_social
        if not aplicado:
            self.stdout.write(
                "Con --apply el proveedor queda marcado como interno si se cancela al menos una deuda del plan "
                "y ninguna da error."
            )
            return
        if regularizacion.proveedor_marcado:
            self.stdout.write(f"Proveedor marcado como interno: {nombre}.")
        else:
            estado = (
                "Sigue marcado como interno de antes"
                if regularizacion.ya_era_interno
                else "Queda SIN marcar como interno"
            )
            self.stdout.write(
                self.style.ERROR(f"No se marco al proveedor {nombre}: {regularizacion.motivo_sin_marcar} {estado}.")
            )
        cantidad, total = regularizacion.abiertas_despues
        if cantidad:
            self.stdout.write(
                self.style.WARNING(
                    f"Siguen abiertas {cantidad} deudas de {nombre} por {dinero(total)}: no se cancelan solas "
                    "(ver POSIBLE_DUPLICADA, FUERA_DEL_PLAN, DISTINTA_AL_PLAN y errores en el informe; "
                    "corregirlas y volver a simular)."
                )
            )
        else:
            self.stdout.write(f"No quedan deudas abiertas de {nombre}.")

    def _imprimir_errores(self, proceso):
        errores = proceso.errores()
        if not errores:
            return
        self.stdout.write("")
        for fila in errores[:MAX_ERRORES_EN_CONSOLA]:
            detalle = fila.resultado or fila.motivo
            debito = f" (debito #{fila.debito_id})" if fila.debito_id else ""
            self.stdout.write(self.style.ERROR(f"{fila.accion} deuda #{fila.deuda_id}{debito}: {detalle}"))
        if len(errores) > MAX_ERRORES_EN_CONSOLA:
            self.stdout.write(self.style.ERROR(f"... y {len(errores) - MAX_ERRORES_EN_CONSOLA} mas en el informe."))

    def _escribir_informe(self, proceso, ruta):
        if not ruta:
            return
        proceso.escribir_informe(ruta)
        self.stdout.write("")
        self.stdout.write(f"Informe: {ruta}")
