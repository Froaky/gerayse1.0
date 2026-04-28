import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cashops", "0008_sucursal_razon_social"),
    ]

    operations = [
        migrations.CreateModel(
            name="Empresa",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=160, unique=True)),
                ("identificador_fiscal", models.CharField(blank=True, max_length=40, null=True, unique=True)),
                ("activa", models.BooleanField(default=True)),
                ("creada_en", models.DateTimeField(auto_now_add=True)),
                ("actualizada_en", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["nombre"]},
        ),
        migrations.AddField(
            model_name="sucursal",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sucursales",
                to="cashops.empresa",
            ),
        ),
    ]
