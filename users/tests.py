from django.contrib import admin
from django.urls import reverse
from django.test import TestCase

from users.models import Role, User


class RoleModelTests(TestCase):
    def test_role_string_representation_uses_name(self):
        role = Role.objects.create(code="ADMIN", name="Administrador")

        self.assertEqual(str(role), "Administrador")


class UserModelTests(TestCase):
    def test_user_can_be_created_without_role(self):
        user = User.objects.create_user(
            username="encargado1",
            password="secret12345",
            email="encargado@example.com",
        )

        self.assertIsNone(user.role)
        self.assertEqual(str(user), "encargado1")

    def test_user_can_be_assigned_a_role(self):
        role = Role.objects.create(code="ENCARGADO", name="Encargado")

        user = User.objects.create_user(
            username="caja1",
            password="secret12345",
            first_name="Juan",
            last_name="Perez",
            role=role,
        )

        self.assertEqual(user.role, role)
        self.assertEqual(str(user), "Juan Perez")


class AdminRegistrationTests(TestCase):
    def test_role_and_user_are_registered_in_admin(self):
        self.assertIn(Role, admin.site._registry)
        self.assertIn(User, admin.site._registry)


class AuthFlowTests(TestCase):
    def test_logout_requires_post_and_ends_session(self):
        user = User.objects.create_user(
            username="operador_logout",
            password="secret12345",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("users:logout"))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("users:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
