from django.db import migrations


CANALES_INICIALES = [
    ("INGRESO_EFECTIVO", "Efectivo", True, 1),
    ("VENTA_TARJETA", "Tarjeta (POS)", False, 2),
    ("VENTA_TRANSFERENCIA", "Transferencia", False, 3),
    ("VENTA_PEDIDOSYA", "PedidosYa", False, 4),
    ("VENTA_QR", "QR / MercadoPago", False, 5),
]


def seed_canales(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    for codigo, nombre, impacta_saldo_caja, orden in CANALES_INICIALES:
        CanalIngreso.objects.get_or_create(
            codigo=codigo,
            defaults={
                "nombre": nombre,
                "impacta_saldo_caja": impacta_saldo_caja,
                "es_sistema": True,
                "activo": True,
                "orden": orden,
            },
        )


def unseed_canales(apps, schema_editor):
    CanalIngreso = apps.get_model("cashops", "CanalIngreso")
    codigos = [c[0] for c in CANALES_INICIALES]
    CanalIngreso.objects.filter(codigo__in=codigos).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0013_add_canalingreso_model"),
    ]

    operations = [
        migrations.RunPython(seed_canales, reverse_code=unseed_canales),
    ]
