from django.db import migrations


def rename_panificacion(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    MovimientoCaja = apps.get_model("cashops", "MovimientoCaja")

    canal = CanalIngreso.objects.filter(codigo="PLANIFICACION").first()
    if canal:
        canal.codigo = "PANIFICACION"
        canal.nombre = "Panificación"
        canal.save()

    MovimientoCaja.objects.filter(tipo="PLANIFICACION").update(tipo="PANIFICACION")


def reverse_rename(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    MovimientoCaja = apps.get_model("cashops", "MovimientoCaja")

    canal = CanalIngreso.objects.filter(codigo="PANIFICACION").first()
    if canal:
        canal.codigo = "PLANIFICACION"
        canal.nombre = "Planificación"
        canal.save()

    MovimientoCaja.objects.filter(tipo="PANIFICACION").update(tipo="PLANIFICACION")


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0016_add_canal_planificacion"),
    ]

    operations = [
        migrations.RunPython(rename_panificacion, reverse_code=reverse_rename),
    ]
