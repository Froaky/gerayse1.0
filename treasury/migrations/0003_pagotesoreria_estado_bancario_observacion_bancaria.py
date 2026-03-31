from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0002_categoria_cuentapagar_cuenta_por_pagar_categoria"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagotesoreria",
            name="estado_bancario",
            field=models.CharField(
                choices=[
                    ("PENDIENTE", "Pendiente"),
                    ("IMPACTADO", "Impactado"),
                    ("RECHAZADO", "Rechazado"),
                    ("ANULADO", "Anulado"),
                ],
                default="PENDIENTE",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="pagotesoreria",
            name="observacion_bancaria",
            field=models.CharField(blank=True, default="", max_length=255),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="pagotesoreria",
            index=models.Index(fields=["estado_bancario", "fecha_pago"], name="treasury_pa_estado_c_f8d0c3_idx"),
        ),
    ]
