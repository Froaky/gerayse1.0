from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("treasury", "0018_saldo_inicial_cuenta_bancaria"),
    ]

    operations = [
        migrations.AddField(
            model_name="movimientobancario",
            name="estado",
            field=models.CharField(
                choices=[("REGISTRADO", "Registrado"), ("ANULADO", "Anulado")],
                default="REGISTRADO",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="anulado_por",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="movimientos_bancarios_anulados",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="anulado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="movimientobancario",
            name="motivo_anulacion",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
