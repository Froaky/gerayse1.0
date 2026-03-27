from pathlib import Path

from django.test import SimpleTestCase


class CoreShellFilesTests(SimpleTestCase):
    def test_dashboard_shell_exists(self):
        path = Path(__file__).resolve().parent / "templates" / "core" / "dashboard.html"
        self.assertTrue(path.exists())
        self.assertIn("Gerayse", path.read_text(encoding="utf-8"))

    def test_login_shell_exists(self):
        path = Path(__file__).resolve().parent / "templates" / "registration" / "login.html"
        self.assertTrue(path.exists())
        self.assertIn("Ingresar a Gerayse", path.read_text(encoding="utf-8"))
