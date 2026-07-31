"""Consolida las cajas de efectivo en una sola boveda por empresa.

Produccion tenia 7 cajas: una global (todos los egresos salian de ahi, saldo
-61.826.287,87) y seis creadas solas, una por sucursal, por el cierre de caja
(toda la recaudacion entraba ahi, 135.547.689,07). La suma era correcta
(73.721.401,20) pero ninguna pantalla lo mostraba, y al filtrar por empresa los
movimientos de la caja global sin sucursal se contaban en TODAS las empresas.

Esta migracion no borra ni una fila de movimiento. Reapunta los movimientos de
las cajas de sucursal a la boveda de su empresa, deja esas cajas inactivas y
crea la boveda de las empresas que no tenian.

ORDEN CRITICO: primero se rescata `sucursal_origen`. Hoy la sucursal de un
ingreso se deduce de `caja_central.sucursal`, y despues de reapuntar la caja es
la boveda (sucursal=None), asi que el dato se perderia. `caja_cierre` no sirve
como reemplazo: se agrego el 14/07/2026 (treasury/0025) sin backfill, o sea que
todo lo anterior lo tiene en NULL.

Idempotente: se puede correr de nuevo sin duplicar ni mover nada.
"""

from django.db import migrations

PREFIJO_CONSOLIDADA = "[Consolidada] "
TIPOS_DE_CIERRE = ["INGRESO_CAJA", "AJUSTE_NEGATIVO"]


def consolidar(apps, schema_editor):
    CajaCentral = apps.get_model("treasury", "CajaCentral")
    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")
    ArqueoDisponibilidades = apps.get_model("treasury", "ArqueoDisponibilidades")
    Empresa = apps.get_model("cashops", "Empresa")

    # --- 1. Rescatar la sucursal de origen ANTES de mover nada ---------------
    # COALESCE(caja_cierre.sucursal, caja_central.sucursal). Los movimientos de
    # la caja global sin caja_cierre quedan en NULL a proposito: no tienen
    # sucursal de origen, su imputacion vive en sucursal_gasto.
    pendientes = MovimientoCajaCentral.objects.filter(
        sucursal_origen__isnull=True
    ).select_related("caja_cierre", "caja_central")
    for movimiento in pendientes.iterator():
        sucursal_id = None
        if movimiento.caja_cierre_id:
            sucursal_id = movimiento.caja_cierre.sucursal_id
        if not sucursal_id:
            sucursal_id = movimiento.caja_central.sucursal_id
        if sucursal_id:
            MovimientoCajaCentral.objects.filter(pk=movimiento.pk).update(
                sucursal_origen_id=sucursal_id
            )

    # Red de seguridad: ningun ingreso puede quedar sin sucursal si estaba
    # colgado de una caja de sucursal. Si esto pasa, algo cambio y es mejor
    # abortar que perder la trazabilidad en silencio.
    huerfanos = MovimientoCajaCentral.objects.filter(
        tipo__in=TIPOS_DE_CIERRE,
        sucursal_origen__isnull=True,
        caja_central__sucursal__isnull=False,
    ).count()
    if huerfanos:
        raise RuntimeError(
            f"{huerfanos} movimientos de cierre quedarian sin sucursal de origen. "
            "Abortado para no perder la trazabilidad por sucursal."
        )

    # --- 2. Las cajas de sucursal quedan marcadas e INACTIVAS ---------------
    # Se les pone empresa y se las desactiva en el mismo paso, a proposito: si se
    # les asignara la empresa dejandolas activas, habria 7 cajas activas de la
    # misma empresa a la vez y eso viola el unique que agrega 0032. Haciendolo
    # junto, en ningun momento hay dos bovedas activas por empresa, y la funcion
    # se puede volver a correr aunque el constraint ya exista.
    for caja in CajaCentral.objects.filter(sucursal__isnull=False).select_related("sucursal"):
        nombre = caja.nombre or ""
        if not nombre.startswith(PREFIJO_CONSOLIDADA):
            nombre = f"{PREFIJO_CONSOLIDADA}{nombre}"[:120]
        CajaCentral.objects.filter(pk=caja.pk).update(
            empresa_id=caja.empresa_id or caja.sucursal.empresa_id,
            activo=False,
            nombre=nombre,
        )

    # --- 3. La caja sin sucursal (la global) tambien necesita empresa -------
    empresa_por_defecto = Empresa.objects.filter(activa=True).order_by("pk").first()
    if empresa_por_defecto is None:
        empresa_por_defecto = Empresa.objects.order_by("pk").first()

    for caja in CajaCentral.objects.filter(empresa__isnull=True, sucursal__isnull=True):
        # Se deduce por unanimidad de la empresa de los gastos que tiene
        # imputados; si no hay unanimidad, la empresa mas antigua (en produccion
        # ARMADI, que es lo que confirmo la administradora del saldo inicial).
        empresas = set(
            MovimientoCajaCentral.objects.filter(caja_central=caja)
            .exclude(sucursal_gasto__isnull=True)
            .values_list("sucursal_gasto__empresa_id", flat=True)
        )
        empresas.discard(None)
        empresa_id = empresas.pop() if len(empresas) == 1 else None
        if empresa_id is None and empresa_por_defecto is not None:
            empresa_id = empresa_por_defecto.pk
        if empresa_id is None:
            raise RuntimeError(
                "No hay ninguna empresa cargada: no se puede asignar la boveda. "
                "Cargar las empresas antes de correr esta migracion."
            )
        CajaCentral.objects.filter(pk=caja.pk).update(empresa_id=empresa_id)

    # --- 4. Elegir o crear la boveda de cada empresa ------------------------
    bovedas = {}
    for empresa in Empresa.objects.all().order_by("pk"):
        # Reusar la caja sin sucursal si ya existe: en produccion eso conserva la
        # caja global con sus 157 movimientos sin moverlos, y de paso el codigo
        # viejo la sigue encontrando por nombre durante la ventana del deploy.
        boveda = (
            CajaCentral.objects.filter(
                empresa_id=empresa.pk, sucursal__isnull=True, activo=True
            )
            .order_by("pk")
            .first()
        )
        if boveda is None:
            boveda = CajaCentral.objects.create(
                empresa_id=empresa.pk,
                sucursal=None,
                nombre=f"Boveda {empresa.nombre}"[:120],
                activo=True,
            )
        bovedas[empresa.pk] = boveda

    # --- 5. Reapuntar los movimientos de las cajas ya desactivadas ----------
    for caja in CajaCentral.objects.filter(sucursal__isnull=False):
        boveda = bovedas.get(caja.empresa_id)
        if boveda is None or boveda.pk == caja.pk:
            continue
        MovimientoCajaCentral.objects.filter(caja_central=caja).update(caja_central=boveda)
        # Los arqueos historicos tambien se reapuntan: quedaron medidos contra
        # una caja que deja de existir operativamente. Ademas caja_central es
        # PROTECT, asi que una caja con arqueos colgados no se podria borrar.
        ArqueoDisponibilidades.objects.filter(caja_central=caja).update(caja_central=boveda)


def desconsolidar(apps, schema_editor):
    """Vuelve los movimientos a la caja de su sucursal de origen.

    Solo reconstruye lo que esta migracion movio. Los movimientos cargados
    despues de consolidar no tienen caja de sucursal a la que volver y se
    quedan en la boveda: la reversa sirve para deshacer el deploy, no para
    convivir con datos nuevos.
    """
    CajaCentral = apps.get_model("treasury", "CajaCentral")
    MovimientoCajaCentral = apps.get_model("treasury", "MovimientoCajaCentral")

    for caja in CajaCentral.objects.filter(sucursal__isnull=False):
        nombre = caja.nombre or ""
        if nombre.startswith(PREFIJO_CONSOLIDADA):
            nombre = nombre[len(PREFIJO_CONSOLIDADA):]
        CajaCentral.objects.filter(pk=caja.pk).update(activo=True, nombre=nombre)
        MovimientoCajaCentral.objects.filter(
            sucursal_origen_id=caja.sucursal_id,
            caja_central__sucursal__isnull=True,
        ).update(caja_central=caja)

    # Las bovedas creadas por la migracion (sin movimientos) se van.
    for boveda in CajaCentral.objects.filter(sucursal__isnull=True):
        if not MovimientoCajaCentral.objects.filter(caja_central=boveda).exists():
            if (boveda.nombre or "").startswith("Boveda "):
                CajaCentral.objects.filter(pk=boveda.pk).delete()

    CajaCentral.objects.all().update(empresa=None)
    MovimientoCajaCentral.objects.all().update(sucursal_origen=None)


class Migration(migrations.Migration):

    dependencies = [
        ("treasury", "0030_boveda_por_empresa_campos"),
    ]

    operations = [
        migrations.RunPython(consolidar, desconsolidar),
    ]
