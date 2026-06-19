from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("treasury", "0017_us59_egreso_rubro_sucursal_periodo"),
    ]

    operations = [
        migrations.CreateModel(
            name="SaldoInicialCuentaBancaria",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha_referencia", models.DateField()),
                ("importe", models.DecimalField(decimal_places=2, max_digits=14)),
                ("motivo", models.CharField(max_length=255)),
                ("importe_anterior", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("motivo_correccion", models.CharField(blank=True, max_length=255)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="saldos_iniciales_bancarios_actualizados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "creado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="saldos_iniciales_bancarios_creados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "cuenta_bancaria",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="saldos_iniciales",
                        to="treasury.cuentabancaria",
                    ),
                ),
            ],
            options={
                "ordering": ["-fecha_referencia", "cuenta_bancaria__banco", "cuenta_bancaria__nombre"],
            },
        ),
        migrations.AddIndex(
            model_name="saldoinicialcuentabancaria",
            index=models.Index(fields=["cuenta_bancaria", "fecha_referencia"], name="treasury_sa_cuenta__028d44_idx"),
        ),
        migrations.AddConstraint(
            model_name="saldoinicialcuentabancaria",
            constraint=models.UniqueConstraint(
                fields=("cuenta_bancaria", "fecha_referencia"),
                name="unique_initial_bank_balance_per_account_date",
            ),
        ),
    ]
