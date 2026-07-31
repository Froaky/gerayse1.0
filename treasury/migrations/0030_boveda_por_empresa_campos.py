"""Campos nuevos para consolidar el efectivo en una boveda por empresa.

Puramente aditiva y sin constraints: primero existen los campos, despues 0031
rellena los datos y solo al final 0032 pone el unique. Si el unique viniera
aca, las 6 cajas de sucursal activas de la misma empresa lo violarian.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0024_movimientocaja_token_alta_and_more"),
        ("treasury", "0029_movimientobancario_generado_por_pago"),
    ]

    operations = [
        migrations.AddField(
            model_name="cajacentral",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cajas_centrales",
                to="cashops.empresa",
            ),
        ),
        migrations.AddField(
            model_name="movimientocajacentral",
            name="sucursal_origen",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ingresos_caja_central",
                to="cashops.sucursal",
            ),
        ),
    ]
