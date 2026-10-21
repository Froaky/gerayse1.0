from django.core.management.base import BaseCommand, CommandError

from asistencia.respaldo import RETENCION, avisar_falla, destino_configurado, generar_respaldo, nombre_de_respaldo, podar


class Command(BaseCommand):
    help = (
        "Respalda empleados, carnets anulados y marcas de asistencia en RESPALDO_DESTINO "
        "(s3://bucket/prefijo o una carpeta) y deja los ultimos 30. Si falla termina con "
        "error y avisa a RESPALDO_AVISO_URL. Pensado para correr una vez por dia."
    )

    def handle(self, *args, **options):
        try:
            destino = destino_configurado()
            nombre = nombre_de_respaldo()
            contenido = generar_respaldo()
            destino.subir(nombre, contenido)
            borrados = podar(destino, RETENCION)
        except Exception as error:
            mensaje = f"{type(error).__name__}: {error}"
            try:
                avisar_falla(mensaje)
            except Exception as error_aviso:
                mensaje += f" (y no se pudo avisar: {error_aviso})"
            raise CommandError(f"No se pudo respaldar la asistencia. {mensaje}") from error
        self.stdout.write(f"Respaldo {nombre} ({len(contenido)} bytes). Borrados por antiguos: {len(borrados)}.")
