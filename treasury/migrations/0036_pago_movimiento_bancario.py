"""US-4.10: el vinculo pago <-> extracto se da vuelta.

Antes: `MovimientoBancario.pago_tesoreria` OneToOne. Un movimiento podia pagar
UNA sola factura, y por eso el pago semanal de cuenta corriente (una
transferencia que cubre 6 facturas de proveedores distintos) obligaba a inventar
debitos que no existen en el extracto.

Ahora: `PagoTesoreria.movimiento_bancario` FK. Varios pagos pueden apuntar al
mismo movimiento.

Se escribe a mano para fijar el orden: primero se libera el nombre del accessor
inverso, despues se agrega la columna nueva, despues se copian los vinculos que
ya existen y solo al final se borra la columna vieja. Asi ningun estado
intermedio tiene dos cosas llamadas `movimiento_bancario` y el backfill corre
con las dos columnas presentes.

Impacto de datos: cada vinculo existente se copia tal cual (uno a uno), asi que
la lectura `pago.movimiento_bancario` devuelve lo mismo antes y despues. No hay
reescritura de tabla: AddField nullable y DropColumn son metadata en Postgres.
"""
from django.db import migrations, models
import django.db.models.deletion


def copiar_vinculos_al_pago(apps, schema_editor):
    MovimientoBancario = apps.get_model("treasury", "MovimientoBancario")
    PagoTesoreria = apps.get_model("treasury", "PagoTesoreria")
    vinculados = MovimientoBancario.objects.exclude(pago_tesoreria_id=None).values_list(
        "pk", "pago_tesoreria_id"
    )
    actualizados = []
    for movimiento_id, pago_id in vinculados:
        actualizados.append(PagoTesoreria(pk=pago_id, movimiento_bancario_id=movimiento_id))
    if actualizados:
        PagoTesoreria.objects.bulk_update(actualizados, ["movimiento_bancario_id"], batch_size=500)


def devolver_vinculos_al_movimiento(apps, schema_editor):
    MovimientoBancario = apps.get_model("treasury", "MovimientoBancario")
    PagoTesoreria = apps.get_model("treasury", "PagoTesoreria")
    # La vuelta atras solo puede conservar UN pago por movimiento (era OneToOne).
    # Se toma el mas viejo de cada movimiento y se avisa de los que se pierden.
    vistos = set()
    actualizados = []
    for pago_id, movimiento_id in (
        PagoTesoreria.objects.exclude(movimiento_bancario_id=None)
        .order_by("pk")
        .values_list("pk", "movimiento_bancario_id")
    ):
        if movimiento_id in vistos:
            continue
        vistos.add(movimiento_id)
        actualizados.append(MovimientoBancario(pk=movimiento_id, pago_tesoreria_id=pago_id))
    if actualizados:
        MovimientoBancario.objects.bulk_update(actualizados, ["pago_tesoreria_id"], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0035_movimientobancario_token_alta_and_more"),
    ]

    operations = [
        # Solo estado: libera el nombre `movimiento_bancario` para el campo nuevo.
        migrations.AlterField(
            model_name="movimientobancario",
            name="pago_tesoreria",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="movimiento_bancario_legacy",
                to="treasury.pagotesoreria",
            ),
        ),
        migrations.AddField(
            model_name="pagotesoreria",
            name="movimiento_bancario",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="pagos",
                to="treasury.movimientobancario",
                verbose_name="Movimiento bancario",
            ),
        ),
        migrations.RunPython(copiar_vinculos_al_pago, devolver_vinculos_al_movimiento),
        migrations.RemoveField(
            model_name="movimientobancario",
            name="pago_tesoreria",
        ),
    ]
