from django.db import migrations


def backfill_empresa(apps, schema_editor):
    """Asigna empresa propietaria a cuentas bancarias existentes.

    Regla 1: si la cuenta tiene sucursal con empresa, hereda esa empresa.
    Regla 2: si no tiene sucursal, se infiere la empresa solo cuando todos los
    movimientos bancarios imputados de la cuenta (sucursal_gasto) apuntan a
    sucursales de una unica empresa. Si es ambiguo o no hay datos, queda NULL
    para completarse desde la edicion de la cuenta.
    """
    CuentaBancaria = apps.get_model("treasury", "CuentaBancaria")

    for cuenta in CuentaBancaria.objects.filter(empresa__isnull=True):
        if cuenta.sucursal_id and cuenta.sucursal.empresa_id:
            cuenta.empresa_id = cuenta.sucursal.empresa_id
            cuenta.save(update_fields=["empresa"])
            continue
        empresa_ids = set(
            cuenta.movimientos_bancarios.filter(
                sucursal_gasto__empresa__isnull=False,
            ).values_list("sucursal_gasto__empresa_id", flat=True)
        )
        if len(empresa_ids) == 1:
            cuenta.empresa_id = empresa_ids.pop()
            cuenta.save(update_fields=["empresa"])


def reverse_backfill_empresa(apps, schema_editor):
    CuentaBancaria = apps.get_model("treasury", "CuentaBancaria")
    CuentaBancaria.objects.update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0022_cuenta_bancaria_empresa"),
    ]

    operations = [
        migrations.RunPython(backfill_empresa, reverse_backfill_empresa),
    ]
