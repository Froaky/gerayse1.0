"""Cierra la puerta: una sola boveda activa por empresa.

Va despues del backfill de 0031 a proposito. Si el constraint viniera antes,
las 6 cajas de sucursal activas de la misma empresa lo violarian y la migracion
no podria aplicarse.
"""

from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0031_consolidar_bovedas_por_empresa"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="cajacentral",
            constraint=models.UniqueConstraint(
                condition=Q(activo=True),
                fields=("empresa",),
                name="unique_active_boveda_por_empresa",
                violation_error_message="Ya existe una boveda de efectivo activa para esta empresa.",
            ),
        ),
        # Los nombres son los que autogenera Django a partir de los campos:
        # si se ponen a mano, makemigrations --check pide renombrarlos.
        migrations.AddIndex(
            model_name="movimientocajacentral",
            index=models.Index(
                fields=["caja_central", "fecha"],
                name="treasury_mo_caja_ce_dc7af0_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="movimientocajacentral",
            index=models.Index(
                fields=["tipo", "periodo_pago"],
                name="treasury_mo_tipo_f101ef_idx",
            ),
        ),
    ]
