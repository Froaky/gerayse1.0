import os

from django.core.management.base import BaseCommand, CommandError

from asistencia.respaldo import (
    avisar_falla,
    avisar_ok,
    destino_configurado,
    entorno_actual,
    generar_respaldo,
    nombre_de_respaldo,
    podar,
)


class Command(BaseCommand):
    help = (
        "Respalda empleados, carnets anulados y marcas de asistencia en RESPALDO_DESTINO "
        "(s3://bucket/prefijo o una carpeta con ruta absoluta) y borra los de mas de 30 dias, "
        "dejando siempre los ultimos 30. Si falla termina con error y avisa a RESPALDO_AVISO_URL; "
        "si sale bien avisa a RESPALDO_OK_URL. Pensado para correr una vez por dia."
    )

    def _fallar(self, mensaje: str, error: Exception):
        detalle = f"{mensaje}. {type(error).__name__}: {error}"
        try:
            if not avisar_falla(detalle):
                detalle += " (sin RESPALDO_AVISO_URL: no se aviso a nadie)"
        except Exception as error_aviso:
            detalle += f" (y no se pudo avisar: {error_aviso})"
        raise CommandError(detalle) from error

    def handle(self, *args, **options):
        try:
            destino = destino_configurado()
            entorno = entorno_actual()
            nombre = nombre_de_respaldo(entorno)
            contenido = generar_respaldo()
            destino.subir(nombre, contenido)
        except Exception as error:
            self._fallar("No se pudo respaldar la asistencia", error)
        try:
            borrados = podar(destino, entorno)
        except Exception as error:
            self._fallar(f"El respaldo {destino.describir(nombre)} se subio, pero fallo la limpieza de los viejos", error)
        self.stdout.write(
            f"Respaldo {destino.describir(nombre)} ({len(contenido)} bytes). Borrados por antiguos: {len(borrados)}."
        )
        if not (os.environ.get("RESPALDO_AVISO_URL") or "").strip():
            self.stderr.write("Aviso: sin RESPALDO_AVISO_URL, si un dia falla no se avisa a nadie.")
        try:
            avisar_ok()
        except Exception as error:
            # El respaldo esta: el monitor va a alertar porque no le llego el ping.
            self.stderr.write(f"Aviso: no se pudo avisar a RESPALDO_OK_URL ({error}).")
