from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from asistencia.respaldo import RespaldoError, destino_configurado, restaurar_respaldo


class Command(BaseCommand):
    help = (
        "Restaura un respaldo de asistencia: reconstruye empleados, carnets anulados y marcas. "
        "Sin argumentos lista los respaldos de RESPALDO_DESTINO. Si ya hay datos de asistencia "
        "no hace nada salvo con --reemplazar. Las empresas, locales y usuarios tienen que existir."
    )

    def add_arguments(self, parser):
        parser.add_argument("respaldo", nargs="?", help="Nombre del respaldo en RESPALDO_DESTINO o ruta a un archivo .json.gz")
        parser.add_argument("--reemplazar", action="store_true", help="Borra los datos de asistencia actuales antes de restaurar")

    def handle(self, *args, respaldo=None, reemplazar=False, **options):
        try:
            if not respaldo:
                for nombre in destino_configurado().listar():
                    self.stdout.write(nombre)
                return
            archivo = Path(respaldo)
            contenido = archivo.read_bytes() if archivo.is_file() else destino_configurado().bajar(respaldo)
            cantidades = restaurar_respaldo(contenido, reemplazar=reemplazar)
        except (RespaldoError, OSError) as error:
            raise CommandError(str(error)) from error
        detalle = ", ".join(f"{modelo}: {cantidad}" for modelo, cantidad in cantidades.items())
        self.stdout.write(f"Restaurado {respaldo}. {detalle}.")
