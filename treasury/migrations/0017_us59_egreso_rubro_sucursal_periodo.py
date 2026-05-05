from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0012_turno_remove_legacy_fields"),
        ("treasury", "0016_ep07_special_commitments"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientocajacentral",
            name="rubro_operativo",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="egresos_caja_central",
                to="cashops.rubrooperativo",
            ),
        ),
        migrations.AddField(
            model_name="movimientocajacentral",
            name="sucursal_gasto",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="egresos_caja_central",
                to="cashops.sucursal",
            ),
        ),
        migrations.AddField(
            model_name="movimientocajacentral",
            name="periodo_pago",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="rubro_operativo",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="egresos_bancarios",
                to="cashops.rubrooperativo",
            ),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="sucursal_gasto",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="egresos_bancarios",
                to="cashops.sucursal",
            ),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="periodo_pago",
            field=models.DateField(blank=True, null=True),
        ),
    ]
