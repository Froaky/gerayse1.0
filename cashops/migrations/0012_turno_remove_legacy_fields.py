from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0011_turno_empresa_caja_fecha_operativa"),
    ]

    operations = [
        # Remove old constraint before touching fields
        migrations.RemoveConstraint(
            model_name="turno",
            name="unique_turno_by_branch_date_type",
        ),
        # Remove old indexes
        migrations.RemoveIndex(
            model_name="turno",
            name="cashops_tur_sucursa_a075f5_idx",
        ),
        migrations.RemoveIndex(
            model_name="turno",
            name="cashops_tur_estado_b695b6_idx",
        ),
        # Remove old index on Caja
        migrations.RemoveIndex(
            model_name="caja",
            name="cashops_caj_estado_3fb04a_idx",
        ),
        # Remove legacy fields from Turno
        migrations.RemoveField(model_name="turno", name="sucursal"),
        migrations.RemoveField(model_name="turno", name="fecha_operativa"),
        migrations.RemoveField(model_name="turno", name="estado"),
        migrations.RemoveField(model_name="turno", name="observacion"),
        migrations.RemoveField(model_name="turno", name="cerrado_en"),
        migrations.RemoveField(model_name="turno", name="abierto_en"),
        # Add new unique constraint
        migrations.AddConstraint(
            model_name="turno",
            constraint=models.UniqueConstraint(
                fields=["empresa", "tipo"],
                name="unique_turno_per_empresa",
            ),
        ),
        # Add new index
        migrations.AddIndex(
            model_name="turno",
            index=models.Index(fields=["empresa", "tipo"], name="cashops_tur_empresa_tipo_idx"),
        ),
        # Add new index on Caja
        migrations.AddIndex(
            model_name="caja",
            index=models.Index(
                fields=["fecha_operativa", "sucursal"],
                name="cashops_caj_fecha_suc_idx",
            ),
        ),
        # Update ordering (no migration needed, handled by model Meta)
    ]
