"""El cierre mensual de tesoreria pasa a ser por empresa.

Antes era una sola fila global: con dos empresas, ninguna podia cerrar el mes
hasta que la otra tuviera todas sus cajas validadas, y el saldo inicial del mes
siguiente mezclaba las dos.

El backfill le pone empresa a los cierres que ya existan. En produccion no hay
ningun mes cerrado, asi que en la practica no toca nada; sirve para los entornos
donde si haya cierres cargados.
"""

import django.db.models.deletion
from django.db import migrations, models


def asignar_empresa_a_los_cierres(apps, schema_editor):
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    Empresa = apps.get_model("cashops", "Empresa")

    por_defecto = Empresa.objects.filter(activa=True).order_by("pk").first()
    if por_defecto is None:
        por_defecto = Empresa.objects.order_by("pk").first()
    if por_defecto is None:
        # Sin empresas cargadas no hay nada que asignar (base recien creada).
        return

    for cierre in CierreMensualTesoreria.objects.filter(empresa__isnull=True).select_related(
        "sucursal"
    ):
        empresa_id = None
        if cierre.sucursal_id and cierre.sucursal.empresa_id:
            empresa_id = cierre.sucursal.empresa_id
        CierreMensualTesoreria.objects.filter(pk=cierre.pk).update(
            empresa_id=empresa_id or por_defecto.pk
        )


def limpiar_empresa_de_los_cierres(apps, schema_editor):
    CierreMensualTesoreria = apps.get_model("treasury", "CierreMensualTesoreria")
    CierreMensualTesoreria.objects.all().update(empresa=None)


class Migration(migrations.Migration):

    dependencies = [
        ("cashops", "0024_movimientocaja_token_alta_and_more"),
        ("treasury", "0033_central_cash_movement_annulment"),
    ]

    operations = [
        migrations.AddField(
            model_name="cierremensualtesoreria",
            name="empresa",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cierres_mensuales_tesoreria",
                to="cashops.empresa",
            ),
        ),
        migrations.RunPython(asignar_empresa_a_los_cierres, limpiar_empresa_de_los_cierres),
        # El unique viejo era solo por mes: la primera empresa que cerraba le
        # bloqueaba el cierre a la otra. Se cambia DESPUES del backfill.
        migrations.RemoveConstraint(
            model_name="cierremensualtesoreria",
            name="unique_monthly_closing_per_month",
        ),
        migrations.AddConstraint(
            model_name="cierremensualtesoreria",
            constraint=models.UniqueConstraint(
                fields=("mes", "empresa"),
                name="unique_monthly_closing_per_company",
                violation_error_message="Esa empresa ya tiene un cierre para ese mes.",
            ),
        ),
    ]
