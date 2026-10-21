# EP-14 US-14.3, paso 2 de 3: cada empleado que ya existe recibe su propio
# codigo de carnet. Datos viejos: los empleados cargados en staging con la
# US-14.2, que todavia no tienen codigo. Reversa: no hace falta deshacer nada
# (el paso 1 al revertir borra la columna entera).

from django.db import migrations


def asignar_codigos(apps, schema_editor):
    from asistencia.models import nuevo_codigo_carnet

    Empleado = apps.get_model("asistencia", "Empleado")
    usados = set(Empleado.objects.exclude(codigo_carnet=None).values_list("codigo_carnet", flat=True))
    for empleado in Empleado.objects.filter(codigo_carnet=None).only("pk"):
        codigo = nuevo_codigo_carnet()
        while codigo in usados:
            codigo = nuevo_codigo_carnet()
        usados.add(codigo)
        Empleado.objects.filter(pk=empleado.pk).update(codigo_carnet=codigo)


class Migration(migrations.Migration):

    dependencies = [
        ("asistencia", "0002_carnet_codigo_y_anulados"),
    ]

    operations = [
        migrations.RunPython(asignar_codigos, migrations.RunPython.noop),
    ]
