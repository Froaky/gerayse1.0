from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from cashops.models import Empresa
from core.context_processors import app_context

# Se elimino CoreShellFilesTests: verificaba que dos archivos .html existieran y
# contuvieran un texto, sin ejercitar comportamiento. Ademas fijaba codigo muerto
# (core/templates/core/dashboard.html era un mock con datos inventados y las otras
# dos plantillas de core/templates/ estaban tapadas por las de templates/), asi que
# impedia borrarlo. La cobertura real de esas pantallas vive en los tests de vista.


class AppContextCompanyScopeTests(TestCase):
    def test_user_without_allowed_companies_has_no_company_access(self):
        Empresa.objects.create(nombre="ARMADI SRL")
        user = get_user_model().objects.create_user(username="sin_empresas", password="test")
        request = RequestFactory().get("/")
        request.user = user
        request.session = {}

        context = app_context(request)

        self.assertEqual(context["empresas_disponibles"], [])
        self.assertEqual(context["empresas_activas"], [])
        self.assertEqual(context["selected_empresa_ids_set"], set())
        self.assertEqual(request.session["empresa_ids"], [])


class ComentariosDePlantillaNoSeImprimenTests(TestCase):
    """Un comentario corto de Django que cruza de linea deja de ser comentario.

    `{# ... #}` solo tapa UNA linea: si el `#}` de cierre cae en la linea
    siguiente, Django imprime el texto tal cual y la nota interna del
    programador termina a la vista del usuario. Paso de verdad en la pantalla
    "Pagar una deuda con esta transferencia": la administradora vio en pantalla
    un parrafo sobre hx-sync y token de alta. Multilinea va con
    comment/endcomment.
    """

    def test_ninguna_plantilla_tiene_un_comentario_corto_multilinea(self):
        import os

        from django.conf import settings

        filtrados = []
        for directorio in [*settings.TEMPLATES[0]["DIRS"], "cashops", "core", "treasury", "users"]:
            for raiz, _dirs, archivos in os.walk(directorio):
                for archivo in archivos:
                    if not archivo.endswith(".html"):
                        continue
                    ruta = os.path.join(raiz, archivo)
                    with open(ruta, encoding="utf-8") as handle:
                        contenido = handle.read()
                    posicion = 0
                    while True:
                        apertura = contenido.find("{#", posicion)
                        if apertura == -1:
                            break
                        fin_de_linea = contenido.find("\n", apertura)
                        if fin_de_linea == -1:
                            fin_de_linea = len(contenido)
                        if "#}" not in contenido[apertura:fin_de_linea]:
                            linea = contenido.count("\n", 0, apertura) + 1
                            filtrados.append(f"{ruta}:{linea}")
                        posicion = apertura + 2

        self.assertEqual(
            filtrados,
            [],
            "Estos comentarios cruzan de linea y se imprimen en pantalla; "
            f"pasalos a comment/endcomment: {filtrados}",
        )
