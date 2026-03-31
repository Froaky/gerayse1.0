import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


def create_default_category(apps, schema_editor):
    CategoriaCuentaPagar = apps.get_model("treasury", "CategoriaCuentaPagar")
    CategoriaCuentaPagar.objects.update_or_create(
        pk=1,
        defaults={
            "nombre": "Sin clasificar",
            "activo": True,
        },
    )


def backfill_payable_category(apps, schema_editor):
    CuentaPorPagar = apps.get_model("treasury", "CuentaPorPagar")
    CuentaPorPagar.objects.filter(categoria__isnull=True).update(categoria_id=1)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("treasury", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CategoriaCuentaPagar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=120)),
                ("activo", models.BooleanField(default=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "creado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="categorias_cuenta_pagar_creadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["nombre"],
            },
        ),
        migrations.AddIndex(
            model_name="categoriacuentapagar",
            index=models.Index(fields=["activo", "nombre"], name="treasury_ca_activo_5d7d9c_idx"),
        ),
        migrations.AddConstraint(
            model_name="categoriacuentapagar",
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower("nombre"),
                name="unique_payable_category_name_ci",
            ),
        ),
        migrations.RunPython(create_default_category, migrations.RunPython.noop),
        migrations.AddField(
            model_name="cuentaporpagar",
            name="categoria",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cuentas_por_pagar",
                to="treasury.categoriacuentapagar",
                blank=True,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_payable_category, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="cuentaporpagar",
            name="categoria",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cuentas_por_pagar",
                to="treasury.categoriacuentapagar",
            ),
        ),
        migrations.AddIndex(
            model_name="cuentaporpagar",
            index=models.Index(fields=["categoria", "fecha_vencimiento"], name="treasury_cu_categ_6d0c8f_idx"),
        ),
    ]
