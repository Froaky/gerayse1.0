"""Claves de reparto que confirmo tesoreria el 06/11/2026 para ARMADI.

- Impuestos: Terminal 27, Yo Helados 4, Centro 25, EB1 34, EB2 10 (la misma
  clave unica de impuestos del 06/10).
- Sueldos de estructura: partes iguales entre los cinco locales, con la
  heladeria. La panaderia (PP-OV-06) no es un local: queda afuera, igual que en
  la clave de impuestos.

Solo se crean si existen esas cinco sucursales y son de una misma empresa: en
una base sin ellas (tests, desarrollo) la migracion no hace nada. No pisa una
clave que ya exista con el mismo nombre. Se pueden editar desde el admin.
"""

from decimal import Decimal

from django.db import migrations

CLAVES = {
    "Impuestos": {"TERM-01": "27", "YH-05": "4", "CENT-02": "25", "EB1-03": "34", "EB2-04": "10"},
    "Sueldos de estructura": {"TERM-01": "20", "YH-05": "20", "CENT-02": "20", "EB1-03": "20", "EB2-04": "20"},
}


def crear_claves(apps, schema_editor):
    Sucursal = apps.get_model("cashops", "Sucursal")
    ClaveReparto = apps.get_model("treasury", "ClaveReparto")
    ClaveRepartoSucursal = apps.get_model("treasury", "ClaveRepartoSucursal")
    codigos = set(CLAVES["Impuestos"])
    sucursales = {s.codigo: s for s in Sucursal.objects.filter(codigo__in=codigos)}
    if set(sucursales) != codigos:
        return
    empresas = {s.empresa_id for s in sucursales.values()}
    if len(empresas) != 1 or None in empresas:
        return
    empresa_id = empresas.pop()
    for nombre, porcentajes in CLAVES.items():
        clave, creada = ClaveReparto.objects.get_or_create(empresa_id=empresa_id, nombre=nombre)
        if not creada:
            continue
        for codigo, porcentaje in porcentajes.items():
            ClaveRepartoSucursal.objects.create(
                clave=clave, sucursal=sucursales[codigo], porcentaje=Decimal(porcentaje)
            )


class Migration(migrations.Migration):
    dependencies = [
        ("treasury", "0038_clave_reparto"),
    ]

    operations = [
        migrations.RunPython(crear_claves, migrations.RunPython.noop),
    ]
