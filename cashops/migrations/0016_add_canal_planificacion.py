from django.db import migrations


def add_planificacion(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    CanalIngreso.objects.get_or_create(
        codigo="PLANIFICACION",
        defaults={
            "nombre": "Planificación",
            "impacta_saldo_caja": False,
            "excluir_de_totales": True,
            "es_sistema": False,
            "activo": True,
            "orden": 10,
        },
    )


def remove_planificacion(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    CanalIngreso.objects.filter(codigo="PLANIFICACION").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0015_canalingreso_excluir_de_totales"),
    ]

    operations = [
        migrations.RunPython(add_planificacion, reverse_code=remove_planificacion),
    ]
