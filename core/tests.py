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
