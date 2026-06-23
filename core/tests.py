from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase

from cashops.models import Empresa
from core.context_processors import app_context


class CoreShellFilesTests(SimpleTestCase):
    def test_dashboard_shell_exists(self):
        path = Path(__file__).resolve().parent / "templates" / "core" / "dashboard.html"
        self.assertTrue(path.exists())
        self.assertIn("Gerayse", path.read_text(encoding="utf-8"))

    def test_login_shell_exists(self):
        path = Path(__file__).resolve().parent / "templates" / "registration" / "login.html"
        self.assertTrue(path.exists())
        self.assertIn("Ingresar a Gerayse", path.read_text(encoding="utf-8"))


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
