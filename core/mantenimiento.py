"""Aviso de mantenimiento programado: un cartel para todos los usuarios, desde que
se configura hasta que termina la ventana. Pasado el final desaparece solo, sin
tocar nada.

Se configura con MANTENIMIENTO_DESDE y MANTENIMIENTO_HASTA en hora local
(por ejemplo "2026-10-07 21:15"). Sin alguno de los dos, o con valores que no se
pueden leer, no hay cartel.
"""

from datetime import datetime

from django.conf import settings
from django.utils import timezone


def aviso_de_mantenimiento(ahora=None):
    desde = _leer(getattr(settings, "MANTENIMIENTO_DESDE", ""))
    hasta = _leer(getattr(settings, "MANTENIMIENTO_HASTA", ""))
    if desde is None or hasta is None or hasta <= desde:
        return None
    ahora = ahora or timezone.now()
    if ahora >= hasta:
        return None
    return {"desde": desde, "hasta": hasta, "en_curso": ahora >= desde}


def _leer(valor):
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(str(valor).strip())
    except ValueError:
        return None
    return timezone.make_aware(fecha) if timezone.is_naive(fecha) else fecha
