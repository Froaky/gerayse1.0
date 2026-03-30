from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_date

from cashops.models import AlertaOperativa, MovimientoCaja, RubroOperativo, Sucursal
from cashops.services import (
    build_box_control_scope,
    build_branch_control_scope,
    build_global_control_scope,
    build_operational_control_snapshot,
    get_alerts_for_scope,
    get_uncategorized_operational_category,
    sync_operational_alerts_for_scope,
)


class Command(BaseCommand):
    help = "Sanea gastos sin rubro y resincroniza el control operativo por scope real."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplica cambios. Sin esto corre en dry-run.")
        parser.add_argument("--skip-backfill", action="store_true", help="No sanea gastos sin rubro.")
        parser.add_argument("--skip-resync", action="store_true", help="No ejecuta resync de alertas.")
        parser.add_argument(
            "--scope",
            choices=["global", "sucursal", "periodo"],
            default="periodo",
            help="Scope del resync.",
        )
        parser.add_argument("--fecha-operativa", dest="fecha_operativa", help="Fecha YYYY-MM-DD.")
        parser.add_argument("--sucursal", help="Sucursal por id o codigo cuando el scope sea sucursal.")

    def handle(self, *args, **options):
        self.apply_mode = bool(options["apply"])
        self.skip_backfill = bool(options["skip_backfill"])
        self.skip_resync = bool(options["skip_resync"])
        self.scope = options["scope"]
        self.fecha_operativa = self._parse_fecha(options.get("fecha_operativa"))
        self.sucursal_ref = options.get("sucursal")

        if self.apply_mode:
            self.stdout.write(self.style.SUCCESS("Modo apply: se escriben cambios."))
        else:
            self.stdout.write(self.style.WARNING("Modo dry-run: no se escriben cambios."))

        if not self.skip_backfill:
            self._run_backfill()
        if not self.skip_resync:
            self._run_resync()

    def _parse_fecha(self, raw_value):
        if not raw_value:
            return None
        parsed = parse_date(raw_value)
        if parsed is None:
            raise CommandError("La fecha operativa debe tener formato YYYY-MM-DD.")
        return parsed

    def _ensure_fecha_operativa(self):
        if self.fecha_operativa is None:
            raise CommandError("Debes indicar --fecha-operativa para ejecutar el resync.")

    def _get_sucursal(self) -> Sucursal:
        if not self.sucursal_ref:
            raise CommandError("Debes indicar --sucursal cuando el scope es sucursal.")
        queryset = Sucursal.objects.all()
        if str(self.sucursal_ref).isdigit():
            sucursal = queryset.filter(pk=int(self.sucursal_ref)).first()
        else:
            sucursal = queryset.filter(codigo__iexact=self.sucursal_ref).first()
        if sucursal is None:
            raise CommandError(f"No existe una sucursal con referencia '{self.sucursal_ref}'.")
        return sucursal

    def _run_backfill(self):
        null_expenses = MovimientoCaja.objects.filter(
            tipo=MovimientoCaja.Tipo.GASTO,
            rubro_operativo__isnull=True,
        )
        count = null_expenses.count()
        self.stdout.write(f"Gastos sin rubro detectados: {count}")
        if count == 0:
            return

        if not self.apply_mode:
            self.stdout.write("[dry-run] Se asignaria el rubro de sistema 'Sin clasificar'.")
            return

        with transaction.atomic():
            category = get_uncategorized_operational_category()
            updated = null_expenses.update(rubro_operativo=category)
        self.stdout.write(self.style.SUCCESS(f"Backfill aplicado a {updated} gasto(s)."))

    def _run_resync(self):
        self._ensure_fecha_operativa()
        self.stdout.write(f"Resync scope: {self.scope} | fecha: {self.fecha_operativa}")
        if self.scope == "global":
            self._process_scope(build_global_control_scope(fecha_operativa=self.fecha_operativa))
            return
        if self.scope == "sucursal":
            self._process_scope(
                build_branch_control_scope(
                    fecha_operativa=self.fecha_operativa,
                    sucursal=self._get_sucursal(),
                )
            )
            return

        self._process_scope(build_global_control_scope(fecha_operativa=self.fecha_operativa))
        branch_ids = set(
            MovimientoCaja.objects.filter(
                tipo=MovimientoCaja.Tipo.GASTO,
                caja__turno__fecha_operativa=self.fecha_operativa,
            ).values_list("caja__sucursal_id", flat=True)
        )
        box_ids = set(
            MovimientoCaja.objects.filter(
                tipo=MovimientoCaja.Tipo.GASTO,
                caja__turno__fecha_operativa=self.fecha_operativa,
            ).values_list("caja_id", flat=True)
        )
        branches = Sucursal.objects.in_bulk(branch_ids)
        for branch_id in branch_ids:
            branch = branches.get(branch_id)
            if branch is None:
                continue
            self._process_scope(
                build_branch_control_scope(
                    fecha_operativa=self.fecha_operativa,
                    sucursal=branch,
                )
            )
        from cashops.models import Caja

        boxes = Caja.objects.select_related("turno", "sucursal", "usuario").in_bulk(box_ids)
        for box_id in box_ids:
            box = boxes.get(box_id)
            if box is None:
                continue
            self._process_scope(build_box_control_scope(caja=box))

    def _process_scope(self, scope):
        snapshot = build_operational_control_snapshot(scope, sync_alerts=False)
        desired_rojo = {
            item["rubro"].id
            for item in snapshot["items"]
            if item["alert_should_exist"]
        }
        current_active = set(
            get_alerts_for_scope(scope, resuelta=False)
            .filter(tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO)
            .values_list("rubro_operativo_id", flat=True)
        )
        current_resolved = set(
            get_alerts_for_scope(scope, resuelta=True)
            .filter(tipo=AlertaOperativa.Tipo.RUBRO_EXCEDIDO)
            .values_list("rubro_operativo_id", flat=True)
        )
        to_open = desired_rojo - current_active
        to_reopen = desired_rojo & current_resolved
        to_resolve = current_active - desired_rojo

        self.stdout.write(
            f"[{scope.kind_label} {scope.label}] rojo={len(desired_rojo)} "
            f"abrir={len(to_open)} reabrir={len(to_reopen)} resolver={len(to_resolve)}"
        )
        if self.apply_mode:
            sync_operational_alerts_for_scope(scope, snapshot_items=snapshot["items"])
