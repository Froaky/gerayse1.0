from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from cashops.models import Sucursal
from users.forms import PersonalForm
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
        self.assertFalse(user.is_cashops_admin())
        self.assertEqual(str(user), "encargado1")

    def test_user_role_detection_marks_admin_roles(self):
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        legacy_admin_role = Role.objects.create(code="administrador", name="Administrador legacy")
        operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")

        admin_user = User.objects.create_user(username="admin", password="secret12345", role=admin_role)
        legacy_admin_user = User.objects.create_user(
            username="admin2",
            password="secret12345",
            role=legacy_admin_role,
        )
        operator_user = User.objects.create_user(
            username="caja1",
            password="secret12345",
            role=operator_role,
        )
        superuser = User.objects.create_superuser(username="root", password="secret12345")

        self.assertTrue(admin_user.is_cashops_admin())
        self.assertTrue(legacy_admin_user.is_cashops_admin())
        self.assertTrue(superuser.is_cashops_admin())
        self.assertFalse(operator_user.is_cashops_admin())

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

    def test_fixed_user_requires_base_branch(self):
        role = Role.objects.create(code="ENCARGADO", name="Encargado")
        user = User(
            username="caja_fija",
            role=role,
            usuario_fijo=True,
        )

        with self.assertRaises(ValidationError) as raised:
            user.full_clean()

        self.assertIn("sucursal_base", raised.exception.message_dict)


class AdminRegistrationTests(TestCase):
    def test_role_and_user_are_registered_in_admin(self):
        self.assertIn(Role, admin.site._registry)
        self.assertIn(User, admin.site._registry)


class UserAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="root_admin",
            password="secret12345",
            email="root@example.com",
        )
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.operator = User.objects.create_user(
            username="operador_admin",
            password="secret12345",
            first_name="Juan",
            last_name="Perez",
            email="operador@example.com",
            role=self.operator_role,
        )
        self.admin_user = User.objects.create_user(
            username="admin_operativo",
            password="secret12345",
            first_name="Ana",
            last_name="Admin",
            email="admin@example.com",
            role=self.admin_role,
        )

    def test_user_admin_declares_role_in_add_and_change_fieldsets(self):
        user_admin = admin.site._registry[User]

        fieldset_fields = [field for _, opts in user_admin.fieldsets for field in opts.get("fields", ())]
        add_fieldset_fields = [field for _, opts in user_admin.add_fieldsets for field in opts.get("fields", ())]

        self.assertIn("role", fieldset_fields)
        self.assertIn("role", add_fieldset_fields)
        self.assertIn("usuario_fijo", fieldset_fields)
        self.assertIn("usuario_fijo", add_fieldset_fields)
        self.assertIn("sucursal_base", fieldset_fields)
        self.assertIn("sucursal_base", add_fieldset_fields)

    def test_user_admin_add_view_allows_setting_role(self):
        sucursal = Sucursal.objects.create(codigo="CASA", nombre="Casa Central", razon_social="Casa Central SA")
        self.client.force_login(self.superuser)

        response = self.client.post(
            reverse("admin:users_user_add"),
            {
                "username": "nuevo_admin",
                "password1": "secret12345",
                "password2": "secret12345",
                "role": self.operator_role.pk,
                "usuario_fijo": "on",
                "sucursal_base": sucursal.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username="nuevo_admin")
        self.assertEqual(created_user.role, self.operator_role)
        self.assertTrue(created_user.usuario_fijo)
        self.assertEqual(created_user.sucursal_base, sucursal)
        self.assertTrue(created_user.check_password("secret12345"))

    def test_user_admin_changelist_searches_by_role_code(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:users_user_changelist"), {"q": "ENCARGADO"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "operador_admin")
        self.assertNotContains(response, "admin_operativo")

    def test_user_admin_changelist_filters_by_role(self):
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse("admin:users_user_changelist"),
            {"role__id__exact": str(self.operator_role.pk)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "operador_admin")
        self.assertNotContains(response, "admin_operativo")


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


class PersonalViewTests(TestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.sucursal_centro = Sucursal.objects.create(
            codigo="CENT",
            nombre="Centro",
            razon_social="Centro SRL",
        )
        self.sucursal_norte = Sucursal.objects.create(
            codigo="NORT",
            nombre="Norte",
            razon_social="Norte SRL",
        )
        self.admin = User.objects.create_user(
            username="admin_personal",
            password="secret12345",
            first_name="Ana",
            last_name="Admin",
            role=self.admin_role,
        )
        self.operator = User.objects.create_user(
            username="operador_personal",
            password="secret12345",
            first_name="Juan",
            last_name="Perez",
            role=self.operator_role,
            dni="12345678",
            legajo="LEG-001",
            telefono="387-111",
        )

    def test_personal_form_hides_legajo_field(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:personal_create"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Nro Legajo")
        self.assertNotContains(response, "LEG-001")

    def test_personal_list_is_minimal_and_hides_extra_fields(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:personal_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juan")
        self.assertContains(response, "Perez")
        self.assertContains(response, "Encargado")
        self.assertNotContains(response, "LEG-001")
        self.assertNotContains(response, "12345678")
        self.assertNotContains(response, "387-111")
        self.assertNotContains(response, "@operador_personal")

    def test_personal_list_searches_by_name_last_name_and_role(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:personal_list"), {"q": "Encargado"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juan")
        self.assertNotContains(response, "Apellido: Admin")
        self.assertNotContains(response, "Rol: Administrador")

    def test_personal_update_supports_existing_users_with_historic_legajo(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:personal_update", args=[self.operator.pk]),
            {
                "username": self.operator.username,
                "first_name": "Juan",
                "last_name": "Perez",
                "dni": self.operator.dni,
                "telefono": self.operator.telefono,
                "email": "",
                "role": self.operator_role.pk,
                "usuario_fijo": "",
                "sucursal_base": "",
                "is_active": "on",
                "password": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.legajo, "LEG-001")
        self.assertTrue(self.operator.check_password("secret12345"))

    def test_personal_form_requires_base_branch_for_fixed_user(self):
        form = PersonalForm(
            data={
                "username": "fijo_sin_sucursal",
                "first_name": "Lia",
                "last_name": "Caja",
                "dni": "",
                "telefono": "",
                "email": "",
                "role": self.operator_role.pk,
                "usuario_fijo": "on",
                "sucursal_base": "",
                "is_active": "on",
                "password": "secret12345",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("sucursal_base", form.errors)

    def test_personal_create_persists_fixed_user_assignment(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:personal_create"),
            {
                "username": "operadora_fija",
                "first_name": "Marta",
                "last_name": "Caja",
                "dni": "",
                "telefono": "",
                "email": "",
                "role": self.operator_role.pk,
                "usuario_fijo": "on",
                "sucursal_base": self.sucursal_centro.pk,
                "is_active": "on",
                "password": "secret12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        created_user = User.objects.get(username="operadora_fija")
        self.assertTrue(created_user.usuario_fijo)
        self.assertEqual(created_user.sucursal_base, self.sucursal_centro)

    def test_personal_update_can_disable_fixed_assignment(self):
        self.operator.usuario_fijo = True
        self.operator.sucursal_base = self.sucursal_centro
        self.operator.save(update_fields=["usuario_fijo", "sucursal_base"])
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:personal_update", args=[self.operator.pk]),
            {
                "username": self.operator.username,
                "first_name": "Juan",
                "last_name": "Perez",
                "dni": self.operator.dni,
                "telefono": self.operator.telefono,
                "email": "",
                "role": self.operator_role.pk,
                "usuario_fijo": "",
                "sucursal_base": "",
                "is_active": "on",
                "password": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.usuario_fijo)
        self.assertIsNone(self.operator.sucursal_base)
