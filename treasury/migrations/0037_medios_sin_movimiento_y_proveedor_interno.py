"""US-3.20: medios de pago sin movimiento de fondos y proveedor interno.

- PagoTesoreria.medio_pago suma REGULARIZACION (pagada fuera de Gerayse segun
  tesoreria) e INTERNO (proveedor propio, nunca se paga). Siguen en max_length 20.
- PagoTesoreria.estado_bancario suma NO_APLICA (sigue en max_length 12).
- Proveedor.es_interno: BooleanField default False.

Impacto de datos: ninguno sobre lo existente. Los AlterField solo cambian las
choices, que viven en Python: en Postgres no generan SQL. El AddField agrega una
columna con default constante, sin reescribir la tabla. Ningun pago ni proveedor
cambia de valor.

Rollback: revertir esta migracion con pagos REGULARIZACION o INTERNO cargados
deja filas que el codigo viejo no puede ni guardar (full_clean rechaza la choice
y exige cuenta bancaria), asi que esas deudas quedarian congeladas. Antes de
cualquier rollback hay que anular esos pagos con el codigo NUEVO (por lote);
no hay otra via salvo SQL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('treasury', '0036_pago_movimiento_bancario'),
    ]

    operations = [
        migrations.AddField(
            model_name='proveedor',
            name='es_interno',
            field=models.BooleanField(default=False, help_text='Proveedor propio: sus facturas cuentan como gasto pero se dan por pagadas sin mover fondos.', verbose_name='Proveedor interno'),
        ),
        migrations.AlterField(
            model_name='pagotesoreria',
            name='estado_bancario',
            field=models.CharField(choices=[('PENDIENTE', 'Pendiente'), ('IMPACTADO', 'Impactado'), ('RECHAZADO', 'Rechazado'), ('ANULADO', 'Anulado'), ('NO_APLICA', 'No aplica')], default='PENDIENTE', max_length=12),
        ),
        migrations.AlterField(
            model_name='pagotesoreria',
            name='medio_pago',
            field=models.CharField(choices=[('TRANSFERENCIA', 'Transferencia'), ('CHEQUE', 'Cheque'), ('ECHEQ', 'ECHEQ'), ('EFECTIVO', 'Efectivo'), ('REGULARIZACION', 'Regularizacion sin movimiento'), ('INTERNO', 'Proveedor interno (sin pago)')], max_length=20),
        ),
    ]
