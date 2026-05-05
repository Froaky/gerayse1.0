from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def consolidate_turnos_and_backfill_cajas(apps, schema_editor):
    Turno = apps.get_model("cashops", "Turno")
    Caja = apps.get_model("cashops", "Caja")
    AlertaOperativa = apps.get_model("cashops", "AlertaOperativa")

    # Step 1: copy fecha_operativa from turno → caja before consolidation
    for caja in Caja.objects.select_related("turno").all():
        if caja.turno_id:
            caja.fecha_operativa = caja.turno.fecha_operativa
            caja.save(update_fields=["fecha_operativa"])

    # Step 2: consolidate multiple turno records into one per (empresa, tipo)
    empresa_tipo_to_canonical = {}  # (empresa_id, tipo) -> canonical turno id
    old_to_canonical = {}           # old turno id -> canonical turno id

    for turno in Turno.objects.select_related("sucursal__empresa").order_by("abierto_en", "id"):
        if not turno.sucursal_id or not turno.sucursal.empresa_id:
            continue
        empresa_id = turno.sucursal.empresa_id
        key = (empresa_id, turno.tipo)
        if key not in empresa_tipo_to_canonical:
            empresa_tipo_to_canonical[key] = turno.id
            Turno.objects.filter(pk=turno.id).update(empresa_id=empresa_id)
        old_to_canonical[turno.id] = empresa_tipo_to_canonical[key]

    # Step 3: repoint cajas and alertas to canonical turno
    for caja in Caja.objects.all():
        canonical = old_to_canonical.get(caja.turno_id)
        if canonical and canonical != caja.turno_id:
            caja.turno_id = canonical
            caja.save(update_fields=["turno_id"])

    for alerta in AlertaOperativa.objects.filter(turno_id__isnull=False):
        canonical = old_to_canonical.get(alerta.turno_id)
        if canonical and canonical != alerta.turno_id:
            alerta.turno_id = canonical
            alerta.save(update_fields=["turno_id"])

    # Step 4: delete non-canonical turnos
    canonical_ids = set(empresa_tipo_to_canonical.values())
    Turno.objects.exclude(pk__in=canonical_ids).delete()


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0010_backfill_empresas_from_razon_social"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Add nullable empresa FK to Turno
        migrations.AddField(
            model_name="turno",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="turnos",
                to="cashops.empresa",
            ),
        ),
        # Add nullable fecha_operativa to Caja
        migrations.AddField(
            model_name="caja",
            name="fecha_operativa",
            field=models.DateField(null=True, blank=True),
        ),
        # Add creado_en to Turno (will replace abierto_en after data migration)
        migrations.AddField(
            model_name="turno",
            name="creado_en",
            field=models.DateTimeField(auto_now_add=True, null=True),
        ),
        # Data migration: consolidate turnos, backfill caja.fecha_operativa
        migrations.RunPython(
            consolidate_turnos_and_backfill_cajas,
            reverse_code=reverse_noop,
        ),
        # Make empresa non-nullable
        migrations.AlterField(
            model_name="turno",
            name="empresa",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="turnos",
                to="cashops.empresa",
            ),
        ),
        # Make fecha_operativa non-nullable
        migrations.AlterField(
            model_name="caja",
            name="fecha_operativa",
            field=models.DateField(),
        ),
    ]
