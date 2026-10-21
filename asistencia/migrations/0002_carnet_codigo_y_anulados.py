# EP-14 US-14.3, paso 1 de 3: el codigo del carnet entra vacio (null) para no
# repetir un mismo default en los empleados que ya existen, y nace el registro de
# carnets anulados. El paso 2 (0003) le da a cada empleado su codigo y el 3
# (0004) lo vuelve obligatorio y unico. Van en migraciones separadas porque en
# Postgres no se puede alterar una tabla en la misma transaccion que actualiza
# sus filas ("pending trigger events").

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("asistencia", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="empleado",
            name="codigo_carnet",
            field=models.CharField(editable=False, max_length=32, null=True, verbose_name="Código del carnet"),
        ),
        migrations.CreateModel(
            name="CarnetAnulado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo", models.CharField(max_length=32, unique=True)),
                ("motivo", models.CharField(max_length=200)),
                ("anulado_en", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "anulado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="carnets_asistencia_anulados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "empleado",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="carnets_anulados",
                        to="asistencia.empleado",
                    ),
                ),
            ],
            options={
                "ordering": ["-anulado_en", "-pk"],
            },
        ),
    ]
