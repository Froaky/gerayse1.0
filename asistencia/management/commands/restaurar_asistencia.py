from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from asistencia.respaldo import destino_configurado, entorno_actual, respaldos_del_entorno, restaurar_respaldo


class Command(BaseCommand):
    help = (
        "Restaura un respaldo de asistencia: reconstruye empleados, carnets anulados y marcas. "
        "Sin argumentos lista los respaldos de este entorno en RESPALDO_DESTINO. Si ya hay datos "
        "de asistencia no hace nada salvo con --reemplazar. Las empresas y locales tienen que existir; "
        "un usuario que ya no existe queda vacio."
    )

    def add_arguments(self, parser):
        parser.add_argument("respaldo", nargs="?", help="Nombre del respaldo en RESPALDO_DESTINO o ruta a un archivo .json.gz")
        parser.add_argument("--reemplazar", action="store_true", help="Borra los datos de asistencia actuales antes de restaurar")

    def handle(self, *args, respaldo=None, reemplazar=False, **options):
        try:
            if not respaldo:
                for nombre in respaldos_del_entorno(destino_configurado(), entorno_actual()):
                    self.stdout.write(nombre)
                return
            archivo = Path(respaldo)
            contenido = archivo.read_bytes() if archivo.is_file() else destino_configurado().bajar(respaldo)
            resultado = restaurar_respaldo(contenido, reemplazar=reemplazar)
        except Exception as error:
            # Incluye los errores de S3 (sin credenciales, nombre que no existe).
            raise CommandError(f"No se restauro nada. {type(error).__name__}: {error}") from error
        detalle = ", ".join(f"{modelo}: {cantidad}" for modelo, cantidad in resultado.cantidades.items())
        self.stdout.write(f"Restaurado {respaldo}. {detalle}.")
        if resultado.usuarios_vaciados:
            self.stdout.write(
                f"{resultado.usuarios_vaciados} referencias a usuarios que ya no existen quedaron vacias."
            )
