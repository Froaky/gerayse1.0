from django.db import migrations


def backfill_empresas(apps, schema_editor):
    Sucursal = apps.get_model("cashops", "Sucursal")
    Empresa = apps.get_model("cashops", "Empresa")

    razon_map = {}
    for sucursal in Sucursal.objects.all():
        razon = (sucursal.razon_social or "").strip() or "Sin empresa"
        if razon not in razon_map:
            empresa, _ = Empresa.objects.get_or_create(nombre=razon)
            razon_map[razon] = empresa
        sucursal.empresa = razon_map[razon]
        sucursal.save(update_fields=["empresa"])


def reverse_backfill(apps, schema_editor):
    Sucursal = apps.get_model("cashops", "Sucursal")
    Sucursal.objects.update(empresa=None)


class Migration(migrations.Migration):
    dependencies = [
        ("cashops", "0009_empresa_and_sucursal_empresa_fk"),
    ]

    operations = [
        migrations.RunPython(backfill_empresas, reverse_backfill),
    ]
