# EP-14 US-14.3, paso 3 de 3: con todos los empleados ya con codigo (0003), el
# codigo del carnet pasa a obligatorio y unico, y los nuevos lo reciben solos.

import asistencia.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("asistencia", "0003_carnet_codigo_existentes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="empleado",
            name="codigo_carnet",
            field=models.CharField(
                default=asistencia.models.nuevo_codigo_carnet,
                editable=False,
                max_length=32,
                unique=True,
                verbose_name="Código del carnet",
            ),
        ),
    ]
