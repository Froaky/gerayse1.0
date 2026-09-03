"""Aviso de vencimiento del servicio de alojamiento.

El servicio vence el 9 de cada mes. Este modulo decide, a partir de la fecha de
hoy, si corresponde mostrar el cartel y con que tono:

- faltan mas de 7 dias: nada;
- faltan 7 a 4 dias (del 2 al 5): advertencia (ambar);
- faltan 3 dias o menos, incluido el dia del vencimiento (del 6 al 9): critico (rojo);
- paso el vencimiento: nada hasta la ventana del mes siguiente. No sabemos si el
  saldo se regularizo, asi que no se afirma que "vencio".

Es presentacion pura: no toca dinero, deuda ni datos operativos. No hay nada que
configurar: ``SERVICE_NOTICE_DUE_DAY`` existe solo para apagarlo (0) o correr el
dia si algun mes cambia el vencimiento. Lo ve solo el administrador del sistema:
cajeros y tesoreria no deciden el pago y no tienen por que ver la alarma.

El copy es un aviso administrativo formal (trato de usted, sin nombres propios):
habla del "servicio de alojamiento" y de "regularizar el saldo", nunca de "la
base de datos". Es un aviso sobre la continuidad del servicio, no una amenaza
sobre los datos.
"""
from __future__ import annotations

import calendar
from datetime import date

from django.conf import settings
from django.utils import timezone

DIA_DE_VENCIMIENTO = 9  # default; settings.SERVICE_NOTICE_DUE_DAY lo puede pisar
DIAS_AVISO = 7          # el cartel aparece cuando faltan 7 dias
DIAS_CRITICO = 3        # y se pone rojo cuando faltan 3

# Se arma a mano para que salga "9 de septiembre de 2026" (minuscula, como
# corresponde en castellano) sin depender de la traduccion de Django, que
# capitaliza los meses.
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha_larga(valor: date) -> str:
    return f"{valor.day} de {MESES[valor.month - 1]} de {valor.year}"


def _vencimiento_del_mes(anio: int, mes: int, dia: int) -> date:
    """El dia pedido, o el ultimo del mes si ese mes es mas corto."""
    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, min(dia, ultimo))


def proximo_vencimiento(hoy: date, dia: int = DIA_DE_VENCIMIENTO) -> date:
    """El vencimiento de este mes si todavia no paso (hoy inclusive); si no, el del mes que viene."""
    este_mes = _vencimiento_del_mes(hoy.year, hoy.month, dia)
    if hoy <= este_mes:
        return este_mes
    anio, mes = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month + 1)
    return _vencimiento_del_mes(anio, mes, dia)


def build_service_notice(*, hoy: date | None = None, dia_vencimiento: int | None = None) -> dict | None:
    """Devuelve ``{"nivel", "vence", "dias", "texto"}`` o ``None`` si hoy no corresponde aviso.

    ``nivel`` es ``warning`` / ``danger`` y lo consume el partial para elegir el color.
    """
    if dia_vencimiento is None:
        dia_vencimiento = getattr(settings, "SERVICE_NOTICE_DUE_DAY", DIA_DE_VENCIMIENTO)
    try:
        dia = int(dia_vencimiento)
    except (TypeError, ValueError):
        return None
    if not 1 <= dia <= 31:
        return None  # 0 (o cualquier valor fuera de rango) = apagado

    hoy = hoy or timezone.localdate()
    vence = proximo_vencimiento(hoy, dia)
    dias = (vence - hoy).days
    if dias > DIAS_AVISO:
        return None

    nivel = "danger" if dias <= DIAS_CRITICO else "warning"
    fecha = _fecha_larga(vence)
    if dias == 0:
        texto = (
            f"El servicio de alojamiento de Gerayse vence hoy, {fecha}. "
            f"Para evitar la interrupción del sistema, regularice el saldo del mes."
        )
    else:
        quedan = "Queda 1 día" if dias == 1 else f"Quedan {dias} días"
        texto = (
            f"El servicio de alojamiento de Gerayse vence el {fecha}. {quedan}. "
            f"Para evitar la interrupción del sistema, regularice el saldo del mes antes de esa fecha."
        )

    return {"nivel": nivel, "vence": vence, "dias": dias, "texto": texto}


def service_notice_for(user, *, hoy: date | None = None) -> dict | None:
    """Aviso para este usuario, o ``None`` si no le corresponde verlo."""
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_admin_role", False):
        return None
    return build_service_notice(hoy=hoy)
