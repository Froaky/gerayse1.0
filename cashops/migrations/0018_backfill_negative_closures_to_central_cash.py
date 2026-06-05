from django.db import migrations


def backfill_negative_closures(apps, schema_editor):
    CierreCaja = apps.get_model("cashops", "CierreCaja")
    CajaCentral = apps.get_model("treasury", "CajaCentral")
    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")

    closures = (
        CierreCaja.objects.select_related("caja", "caja__sucursal")
        .filter(saldo_fisico__lt=0, caja__sucursal__isnull=False)
        .order_by("id")
    )
    for cierre in closures:
        caja = cierre.caja
        caja_central = CajaCentral.objects.filter(sucursal=caja.sucursal, activo=True).first()
        if caja_central is None:
            caja_central = CajaCentral.objects.create(
                sucursal=caja.sucursal,
                nombre=f"Caja Central {caja.sucursal.nombre}",
                activo=True,
            )

        concept = f"Cierre caja #{caja.id} - saldo negativo"
        exists = MovimientoCajaCentral.objects.filter(
            caja_central=caja_central,
            fecha=caja.fecha_operativa,
            tipo="AJUSTE_NEGATIVO",
            concepto=concept,
        ).exists()
        if exists:
            continue

        MovimientoCajaCentral.objects.create(
            caja_central=caja_central,
            fecha=caja.fecha_operativa,
            tipo="AJUSTE_NEGATIVO",
            monto=abs(cierre.saldo_fisico),
            concepto=concept,
            observaciones="Backfill de saldo fisico negativo informado al cierre de caja.",
            creado_por=cierre.cerrado_por,
        )


def reverse_backfill_negative_closures(apps, schema_editor):
    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
    MovimientoCajaCentral.objects.filter(
        tipo="AJUSTE_NEGATIVO",
        concepto__contains=" - saldo negativo",
        observaciones="Backfill de saldo fisico negativo informado al cierre de caja.",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0017_us59_egreso_rubro_sucursal_periodo"),
        ("cashops", "0017_rename_canal_panificacion"),
    ]

    operations = [
        migrations.RunPython(backfill_negative_closures, reverse_backfill_negative_closures),
    ]
