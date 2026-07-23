from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from cashops.models import Caja


User = get_user_model()

ANCHO = 74


class Command(BaseCommand):
    help = (
        "Informe de solo lectura: lista las cajas ABIERTAS (opcionalmente de un usuario) "
        "para diagnosticar por que alguien no puede abrir una caja nueva. La regla del "
        "sistema es que solo puede existir UNA caja abierta por (responsable, turno, "
        "sucursal, fecha operativa); se pueden tener varias abiertas de fechas distintas, "
        "pero para abrir otra del MISMO dia+turno+sucursal hay que cerrar la que ya esta. "
        "NO modifica ningun dato."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--usuario",
            type=str,
            default="",
            help="Filtra por usuario (busca en username, nombre o apellido). Ej: --usuario victor",
        )
        parser.add_argument(
            "--empresa",
            type=str,
            default="",
            help="Filtra por nombre de empresa (contiene). Ej: --empresa ARMADI",
        )

    def handle(self, *args, **options):
        filtro_usuario = (options["usuario"] or "").strip()
        filtro_empresa = (options["empresa"] or "").strip()

        cajas = (
            Caja.objects.filter(estado=Caja.Estado.ABIERTA)
            .select_related(
                "usuario",
                "sucursal",
                "sucursal__empresa",
                "turno",
                "turno__empresa",
            )
            .order_by("usuario__username", "sucursal__nombre", "turno__tipo")
        )
        if filtro_usuario:
            cajas = cajas.filter(
                Q(usuario__username__icontains=filtro_usuario)
                | Q(usuario__first_name__icontains=filtro_usuario)
                | Q(usuario__last_name__icontains=filtro_usuario)
            )
        if filtro_empresa:
            cajas = cajas.filter(sucursal__empresa__nombre__icontains=filtro_empresa)

        self._titulo("CAJAS ABIERTAS (informe de solo lectura)")
        self.stdout.write("No se modifica ningun dato.")
        if filtro_usuario:
            self.stdout.write(f'Filtro usuario: "{filtro_usuario}"')
        if filtro_empresa:
            self.stdout.write(f'Filtro empresa: "{filtro_empresa}"')
        self.stdout.write(f"Hoy: {timezone.localdate():%d/%m/%Y}\n")

        total = cajas.count()
        if total == 0:
            self.stdout.write("No hay cajas ABIERTAS con ese filtro.\n")
            self.stdout.write(
                "Si el cajero NO puede abrir y tampoco tiene ninguna caja abierta, el bloqueo\n"
                "no es por caja duplicada. Revisar dos cosas:\n"
                "  1) que el turno elegido pertenezca a la empresa seleccionada; y\n"
                "  2) si es usuario fijo, que la sucursal elegida sea su sucursal base."
            )
            self._linea()
            self.stdout.write("Fin del informe. Ningun dato fue modificado.")
            return

        usuario_actual = None
        for caja in cajas:
            if caja.usuario_id != usuario_actual:
                usuario_actual = caja.usuario_id
                self.stdout.write("")
                self._encabezado_usuario(caja.usuario)
            self._linea_caja(caja)

        self.stdout.write("")
        self._linea()
        self.stdout.write(f"Total de cajas abiertas listadas: {total}")
        self.stdout.write(
            "Regla: solo UNA caja abierta por (responsable, turno, sucursal, FECHA). Se pueden\n"
            "tener varias abiertas de fechas distintas; para abrir otra del MISMO dia + turno +\n"
            "sucursal, primero hay que CERRAR la que figura arriba."
        )
        self.stdout.write("Fin del informe. Ningun dato fue modificado.")

    def _encabezado_usuario(self, usuario):
        nombre = usuario.get_full_name() or usuario.username
        extra = ""
        if getattr(usuario, "usuario_fijo", False):
            base = usuario.sucursal_base
            base_txt = base.nombre if base else "SIN sucursal base"
            extra = f"  [usuario fijo -> base: {base_txt}]"
        self.stdout.write(f"Responsable: {nombre} ({usuario.username}){extra}")

    def _linea_caja(self, caja):
        suc = caja.sucursal
        suc_txt = f"{suc.codigo} {suc.nombre}".strip() if getattr(suc, "codigo", "") else suc.nombre
        turno_txt = caja.turno.get_tipo_display()
        empresa_txt = caja.turno.empresa.nombre if caja.turno.empresa_id else "sin empresa"
        abierta = timezone.localtime(caja.abierta_en) if caja.abierta_en else None
        abierta_txt = f"{abierta:%d/%m/%Y %H:%M}" if abierta else "s/d"
        self.stdout.write(
            f"  - Caja #{caja.id} | Sucursal {suc_txt} | Turno {turno_txt} ({empresa_txt})"
        )
        self.stdout.write(
            f"      fecha operativa {caja.fecha_operativa:%d/%m/%Y} | abierta {abierta_txt} | "
            f"inicial {caja.monto_inicial} | validacion: {caja.get_validacion_estado_display()}"
        )
        self.stdout.write(
            f"      >> Bloquea abrir OTRA caja del mismo Turno {turno_txt} + Sucursal {suc_txt} "
            f"en la fecha {caja.fecha_operativa:%d/%m/%Y}. Hay que cerrar la #{caja.id} primero "
            f"(otras fechas se pueden abrir igual)."
        )

    def _titulo(self, texto):
        self._linea()
        self.stdout.write(texto)
        self._linea()

    def _linea(self):
        self.stdout.write("=" * ANCHO)
