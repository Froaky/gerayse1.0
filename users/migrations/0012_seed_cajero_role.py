from django.db import migrations


CAJERO_CODE = "CAJERO"
MODULES = [
    "cashops",
    "cashops_closed_fix",
    "cashops_validate",
    "config",
    "treasury",
    "users",
]


def seed_cajero_role(apps, schema_editor):
    """EP-13 US-13.3: rol operativo cajero con caja como unico modulo habilitado.

    El alcance por sucursal se completa marcando al usuario como fijo con su
    sucursal base; ese enforcement ya existe en apertura/operacion de caja.
    """
    Role = apps.get_model("users", "Role")
    RolePermission = apps.get_model("users", "RolePermission")
    role, _ = Role.objects.get_or_create(code=CAJERO_CODE, defaults={"name": "Cajero"})
    for module in MODULES:
        can_access = module == "cashops"
        RolePermission.objects.get_or_create(
            role=role,
            module=module,
            defaults={"can_read": can_access, "can_write": can_access},
        )


def remove_cajero_role(apps, schema_editor):
    # Reversa no-op: no se borra el rol porque puede estar asignado a usuarios.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0011_alter_rolepermission_module_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_cajero_role, remove_cajero_role),
    ]
