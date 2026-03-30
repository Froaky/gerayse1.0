from __future__ import annotations

from dataclasses import dataclass, field

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.dateparse import parse_date

from cashops.models import AlertaOperativa, Caja, CierreCaja, Sucursal


@dataclass
class AlertSanitationPlan:
    alert: AlertaOperativa
    scope_kind: str
    updates: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)

    @property
    def has_conflicts(self) -> bool:
        return any(issue.startswith("conflicto_") for issue in self.issues)


class Command(BaseCommand):
    help = "Audita y completa metadata historica segura de alertas legacy."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplica cambios. Sin esto corre en dry-run.")
        parser.add_argument("--sample", type=int, default=20, help="Cantidad maxima de alertas a detallar en salida.")

    def handle(self, *args, **options):
        self.apply_mode = bool(options["apply"])
        self.sample_limit = max(int(options["sample"]), 0)
        self.box_cache: dict[int, Caja | None] = {}
        self.branch_cache: dict[int, Sucursal | None] = {}
        self.closing_cache: dict[int, CierreCaja | None] = {}

        self.stdout.write(
            self.style.SUCCESS("Modo apply: se escriben cambios.")
            if self.apply_mode
            else self.style.WARNING("Modo dry-run: no se escriben cambios.")
        )

        alerts = list(
            AlertaOperativa.objects.select_related(
                "caja__turno",
                "caja__sucursal",
                "caja__usuario",
                "cierre__caja__turno",
                "cierre__caja__sucursal",
                "cierre__caja__usuario",
                "turno",
                "usuario",
                "sucursal",
                "rubro_operativo",
            )
        )
        plans = [self._build_plan(alert) for alert in alerts]
        plans = [plan for plan in plans if self._needs_sanitation(plan.alert, plan.scope_kind)]

        updated = 0
        ready = 0
        pending_review = 0
        conflicts = 0
        detail_lines = []

        for plan in plans:
            remaining_required = self._remaining_required_fields(plan)
            if plan.has_conflicts:
                conflicts += 1
            if plan.updates and not plan.has_conflicts:
                ready += 1
                if self.apply_mode:
                    self._apply_updates(plan)
                    updated += 1
            if remaining_required or plan.has_conflicts:
                pending_review += 1

            if len(detail_lines) < self.sample_limit:
                state = "CONFLICTO" if plan.has_conflicts else "PENDIENTE" if remaining_required else "LISTA"
                detail_lines.append(
                    f"[{state}] alerta={plan.alert.pk} tipo={plan.alert.tipo} alcance={plan.scope_kind} "
                    f"updates={','.join(sorted(plan.updates)) or '-'} "
                    f"motivos={','.join(sorted(set(plan.issues + remaining_required))) or '-'}"
                )

        self.stdout.write(
            "Alertas legacy incompletas: "
            f"{len(plans)} | actualizables={ready} | actualizadas={updated} "
            f"| pendientes_revision={pending_review} | conflictos={conflicts}"
        )
        for line in detail_lines:
            self.stdout.write(line)

    def _apply_updates(self, plan: AlertSanitationPlan) -> None:
        update_fields = []
        for field_name, value in plan.updates.items():
            setattr(plan.alert, field_name, value)
            update_fields.append(field_name)
        if not update_fields:
            return
        with transaction.atomic():
            plan.alert.save(update_fields=update_fields)

    def _needs_sanitation(self, alert: AlertaOperativa, scope_kind: str) -> bool:
        required_fields = self._required_fields(alert, scope_kind)
        return any(self._current_value(alert, field_name) is None for field_name in required_fields)

    def _required_fields(self, alert: AlertaOperativa, scope_kind: str) -> tuple[str, ...]:
        if alert.tipo == AlertaOperativa.Tipo.DIFERENCIA_GRAVE:
            return ("caja", "sucursal", "turno", "usuario", "periodo_fecha")
        if scope_kind == "CAJA":
            return ("caja", "sucursal", "turno", "usuario", "periodo_fecha")
        if scope_kind == "SUCURSAL":
            return ("sucursal", "periodo_fecha")
        return ("periodo_fecha",)

    def _remaining_required_fields(self, plan: AlertSanitationPlan) -> list[str]:
        missing = []
        for field_name in self._required_fields(plan.alert, plan.scope_kind):
            current_value = self._current_value(plan.alert, field_name)
            if current_value is None and field_name not in plan.updates:
                missing.append(f"sin_fuente_{field_name}")
        return missing

    def _current_value(self, alert: AlertaOperativa, field_name: str):
        if field_name == "periodo_fecha":
            return alert.periodo_fecha
        return getattr(alert, f"{field_name}_id")

    def _build_plan(self, alert: AlertaOperativa) -> AlertSanitationPlan:
        if alert.tipo == AlertaOperativa.Tipo.DIFERENCIA_GRAVE:
            return self._build_grave_difference_plan(alert)
        return self._build_expense_alert_plan(alert)

    def _build_grave_difference_plan(self, alert: AlertaOperativa) -> AlertSanitationPlan:
        scope_kind = "CAJA"
        plan = AlertSanitationPlan(alert=alert, scope_kind=scope_kind)
        closing_info = self._parse_closing_alert_key(alert.dedupe_key)

        cierre = alert.cierre
        if closing_info is not None:
            dedupe_cierre = self._get_closing(closing_info["closing_id"])
            if cierre is None:
                cierre = dedupe_cierre
                if cierre is not None:
                    plan.updates["cierre"] = cierre
            elif dedupe_cierre is not None and cierre.pk != dedupe_cierre.pk:
                plan.issues.append("conflicto_cierre")

        source_box = alert.caja
        if cierre is not None:
            if source_box is None:
                source_box = cierre.caja
                if source_box is not None:
                    plan.updates["caja"] = source_box
            elif cierre.caja_id and source_box.id != cierre.caja_id:
                plan.issues.append("conflicto_caja")

        if source_box is not None:
            self._merge_object(plan, "sucursal", source_box.sucursal, "conflicto_sucursal")
            self._merge_object(plan, "turno", source_box.turno, "conflicto_turno")
            self._merge_object(plan, "usuario", source_box.usuario, "conflicto_usuario")
            self._merge_value(plan, "periodo_fecha", source_box.turno.fecha_operativa, "conflicto_periodo_fecha")

        return plan

    def _build_expense_alert_plan(self, alert: AlertaOperativa) -> AlertSanitationPlan:
        key_info = self._parse_expense_alert_key(alert.dedupe_key)
        scope_kind = self._resolve_expense_scope_kind(alert, key_info)
        plan = AlertSanitationPlan(alert=alert, scope_kind=scope_kind)

        source_box = alert.caja
        if key_info and key_info["kind"] == "CAJA":
            dedupe_box = self._get_box(key_info["scope_id"])
            if source_box is None:
                source_box = dedupe_box
                if source_box is not None:
                    plan.updates["caja"] = source_box
            elif dedupe_box is not None and source_box.pk != dedupe_box.pk:
                plan.issues.append("conflicto_caja")

        source_branch = alert.sucursal
        if source_box is not None:
            if source_branch is None:
                source_branch = source_box.sucursal
                if source_branch is not None:
                    plan.updates["sucursal"] = source_branch
            elif source_branch.id != source_box.sucursal_id:
                plan.issues.append("conflicto_sucursal")
        elif key_info and key_info["kind"] == "SUCURSAL":
            dedupe_branch = self._get_branch(key_info["scope_id"])
            if source_branch is None:
                source_branch = dedupe_branch
                if source_branch is not None:
                    plan.updates["sucursal"] = source_branch
            elif dedupe_branch is not None and source_branch.pk != dedupe_branch.pk:
                plan.issues.append("conflicto_sucursal")

        period_source = None
        if source_box is not None:
            period_source = source_box.turno.fecha_operativa
            if key_info is not None and key_info["periodo_fecha"] != source_box.turno.fecha_operativa:
                plan.issues.append("conflicto_periodo_fecha")
        elif alert.turno_id and alert.turno is not None:
            period_source = alert.turno.fecha_operativa
        elif key_info is not None:
            period_source = key_info["periodo_fecha"]
        self._merge_value(plan, "periodo_fecha", period_source, "conflicto_periodo_fecha")

        if source_box is not None:
            self._merge_object(plan, "turno", source_box.turno, "conflicto_turno")
            self._merge_object(plan, "usuario", source_box.usuario, "conflicto_usuario")

        return plan

    def _merge_object(self, plan: AlertSanitationPlan, field_name: str, source_object, conflict_code: str) -> None:
        if source_object is None:
            return
        current_id = getattr(plan.alert, f"{field_name}_id")
        if current_id is None:
            plan.updates[field_name] = source_object
            return
        if current_id != source_object.pk:
            plan.issues.append(conflict_code)

    def _merge_value(self, plan: AlertSanitationPlan, field_name: str, source_value, conflict_code: str) -> None:
        if source_value is None:
            return
        current_value = getattr(plan.alert, field_name)
        if current_value is None:
            plan.updates[field_name] = source_value
            return
        if current_value != source_value:
            plan.issues.append(conflict_code)

    def _resolve_expense_scope_kind(self, alert: AlertaOperativa, key_info) -> str:
        if alert.caja_id:
            return "CAJA"
        if key_info and key_info["kind"] == "CAJA":
            return "CAJA"
        if alert.sucursal_id:
            return "SUCURSAL"
        if key_info and key_info["kind"] == "SUCURSAL":
            return "SUCURSAL"
        return "GLOBAL"

    def _parse_expense_alert_key(self, dedupe_key: str | None):
        if not dedupe_key or not dedupe_key.startswith("RUBRO_EXCEDIDO:"):
            return None
        parts = dedupe_key.split(":")
        if len(parts) != 4:
            return None
        _, raw_scope, raw_period, _ = parts
        period = parse_date(raw_period)
        if period is None:
            return None
        if raw_scope == "global":
            return {"kind": "GLOBAL", "scope_id": None, "periodo_fecha": period}
        if raw_scope.startswith("sucursal-") and raw_scope.split("-", 1)[1].isdigit():
            return {"kind": "SUCURSAL", "scope_id": int(raw_scope.split("-", 1)[1]), "periodo_fecha": period}
        if raw_scope.startswith("caja-") and raw_scope.split("-", 1)[1].isdigit():
            return {"kind": "CAJA", "scope_id": int(raw_scope.split("-", 1)[1]), "periodo_fecha": period}
        return None

    def _parse_closing_alert_key(self, dedupe_key: str | None):
        if not dedupe_key or not dedupe_key.startswith("DIFERENCIA_GRAVE:cierre:"):
            return None
        parts = dedupe_key.split(":")
        if len(parts) != 3 or not parts[2].isdigit():
            return None
        return {"closing_id": int(parts[2])}

    def _get_box(self, box_id: int):
        if box_id not in self.box_cache:
            self.box_cache[box_id] = (
                Caja.objects.select_related("turno", "sucursal", "usuario").filter(pk=box_id).first()
            )
        return self.box_cache[box_id]

    def _get_branch(self, branch_id: int):
        if branch_id not in self.branch_cache:
            self.branch_cache[branch_id] = Sucursal.objects.filter(pk=branch_id).first()
        return self.branch_cache[branch_id]

    def _get_closing(self, closing_id: int):
        if closing_id not in self.closing_cache:
            self.closing_cache[closing_id] = (
                CierreCaja.objects.select_related("caja__turno", "caja__sucursal", "caja__usuario")
                .filter(pk=closing_id)
                .first()
            )
        return self.closing_cache[closing_id]
