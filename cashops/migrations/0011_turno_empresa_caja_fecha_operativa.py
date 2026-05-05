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

    # Step 3: repoint cajas to canonical turno
    # Problem: multiple ABIERTA cajas for the same (usuario, sucursal) can point
    # to different old turno_ids that all map to the SAME canonical turno. Updating
    # them sequentially violates unique_open_box_by_user_turn_branch.
    #
    # Strategy: compute every caja's *future* canonical turno_id first, group the
    # ABIERTA ones, close any extras in bulk, then do the turno_id updates safely.
    from collections import defaultdict

    def canonical_for(turno_id):
        # If the turno is being remapped use the mapping; otherwise keep as-is.
        return old_to_canonical.get(turno_id, turno_id)

    # Group ABIERTA cajas by their future (usuario, canonical_turno, sucursal) key.
    future_open = defaultdict(list)
    for caja in Caja.objects.filter(estado="ABIERTA"):
        future_key = (caja.usuario_id, canonical_for(caja.turno_id), caja.sucursal_id)
        future_open[future_key].append(caja.pk)

    # For any group with more than one ABIERTA caja, close all but the first (lowest pk).
    pks_to_close = []
    for pks in future_open.values():
        if len(pks) > 1:
            pks_to_close.extend(sorted(pks)[1:])
    if pks_to_close:
        Caja.objects.filter(pk__in=pks_to_close).update(estado="CERRADA")

    # Now that duplicates are closed, update turno_ids safely via QuerySet.update()
    # (bypasses ORM save() to go straight to SQL without triggering Python validators).
    for caja in Caja.objects.all():
        new_turno = old_to_canonical.get(caja.turno_id)
        if new_turno and new_turno != caja.turno_id:
            Caja.objects.filter(pk=caja.pk).update(turno_id=new_turno)

    for alerta in AlertaOperativa.objects.filter(turno_id__isnull=False):
        canonical = old_to_canonical.get(alerta.turno_id)
        if canonical and canonical != alerta.turno_id:
            alerta.turno_id = canonical
            alerta.save(update_fields=["turno_id"])

    # Step 4: delete non-canonical turnos
    canonical_ids = set(empresa_tipo_to_canonical.values())
    Turno.objects.exclude(pk__in=canonical_ids).delete()

    # Step 5: flush deferred FK trigger events.
    # Deleting Turno rows above queues deferred constraint-check triggers in
    # PostgreSQL. If those triggers are still "pending" when Django runs the
    # subsequent AlterField operations in this same migration transaction,
    # PostgreSQL raises "cannot ALTER TABLE because it has pending trigger events".
    # SET CONSTRAINTS ALL IMMEDIATE forces those checks to run now so the queue
    # is empty before any DDL executes.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


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
