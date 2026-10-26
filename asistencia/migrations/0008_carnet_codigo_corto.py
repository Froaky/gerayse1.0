"""Codigo de carnet de 6 digitos (EP-14 D12, 26/10/2026).

Cada empleado con un codigo del formato anterior (GA1 + 24 en base32) recibe uno
de 6 digitos, y el anterior queda como carnet anulado: si alguien escanea un
carnet viejo, el fichaje avisa que esta anulado. Solo datos: sin cambios de
esquema en la misma migracion (Postgres no deja mezclarlos).
"""

import secrets

from django.db import migrations

MOTIVO = "Cambio a código de 6 dígitos"


def acortar(apps, schema_editor):
    Empleado = apps.get_model("asistencia", "Empleado")
    CarnetAnulado = apps.get_model("asistencia", "CarnetAnulado")
    usados = set(Empleado.objects.values_list("codigo_carnet", flat=True))
    usados |= set(CarnetAnulado.objects.values_list("codigo", flat=True))
    for empleado in Empleado.objects.order_by("pk"):
        viejo = empleado.codigo_carnet
        if len(viejo) == 6 and viejo.isdigit():
            continue
        nuevo = str(100000 + secrets.randbelow(900000))
        while nuevo in usados:
            nuevo = str(100000 + secrets.randbelow(900000))
        usados.add(nuevo)
        CarnetAnulado.objects.create(empleado=empleado, codigo=viejo, motivo=MOTIVO, anulado_por=None)
        empleado.codigo_carnet = nuevo
        empleado.save(update_fields=["codigo_carnet"])


class Migration(migrations.Migration):
    dependencies = [
        ("asistencia", "0007_dni_opcional"),
    ]

    operations = [
        migrations.RunPython(acortar, migrations.RunPython.noop),
    ]
