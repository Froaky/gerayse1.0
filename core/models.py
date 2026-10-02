from django.db import models


class ConfiguracionSistema(models.Model):
    """Configuracion global del sistema que edita el superusuario desde /admin/.

    Es un singleton (siempre ``pk=1``): hay una sola fila y ``cargar()`` la crea
    con los valores por defecto la primera vez que alguien la necesita.
    """

    aviso_vencimiento_activo = models.BooleanField(
        "Mostrar aviso de vencimiento del servicio",
        default=True,
        help_text=(
            "Si se destilda, el aviso mensual de vencimiento del servicio de alojamiento "
            "no se muestra a ningun administrador. Volver a tildarlo lo reactiva."
        ),
    )

    class Meta:
        verbose_name = "configuración del sistema"
        verbose_name_plural = "configuración del sistema"

    def __str__(self):
        return "Configuración del sistema"

    def save(self, *args, **kwargs):
        self.pk = 1
        kwargs["force_insert"] = False  # una segunda creacion actualiza la fila unica
        super().save(*args, **kwargs)

    @classmethod
    def cargar(cls) -> "ConfiguracionSistema":
        configuracion, _ = cls.objects.get_or_create(pk=1)
        return configuracion

    @classmethod
    def aviso_vencimiento_habilitado(cls) -> bool:
        """Lectura sin escribir: una sola consulta, y sin fila vale el default (activo)."""
        valor = cls.objects.filter(pk=1).values_list("aviso_vencimiento_activo", flat=True).first()
        return True if valor is None else valor
