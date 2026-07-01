from django.contrib import admin
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from cashops.models import Empresa, Sucursal
from users.forms import PersonalForm, UserCreateForm
from users.models import PermissionModule, Role, RolePermission, User, UserPermission


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

    def test_module_permissions_use_role_defaults_and_user_overrides(self):
        read_only_role = Role.objects.create(code="LECTURA", name="Solo lectura")
        RolePermission.objects.create(
            role=read_only_role,
            module=PermissionModule.TREASURY,
            can_read=True,
            can_write=False,
        )
        user = User.objects.create_user(username="tesoreria_lectura", password="secret12345", role=read_only_role)

        self.assertTrue(user.has_module_permission(PermissionModule.TREASURY, "read"))
        self.assertFalse(user.has_module_permission(PermissionModule.TREASURY, "write"))

        UserPermission.objects.create(
            user=user,
            module=PermissionModule.TREASURY,
            can_read=True,
            can_write=True,
        )

        self.assertTrue(user.has_module_permission(PermissionModule.TREASURY, "write"))

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


class UserCompanyAccessFormTests(TestCase):
    def test_principal_company_requires_explicit_company_access(self):
        empresa = Empresa.objects.create(nombre="ARMADI SRL")
        form = PersonalForm(
            data={
                "username": "nuevo",
                "first_name": "Nuevo",
                "last_name": "Usuario",
                "password": "secret12345",
                "empresa_principal": empresa.pk,
                "empresas_permitidas": [],
                "is_active": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("empresa_principal", form.errors)

    def test_user_create_form_only_requires_minimal_identity_and_temporary_password(self):
        form = UserCreateForm(
            data={
                "username": "nuevo_minimo",
                "first_name": "Nuevo",
                "last_name": "Minimo",
                "password": "secret12345",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.fields), ["username", "first_name", "last_name", "password"])


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
        self.assertIn("must_change_password", fieldset_fields)
        self.assertIn("must_change_password", add_fieldset_fields)
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


class AccountSettingsTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.user = User.objects.create_user(
            username="mi_cuenta",
            password="secret12345",
            first_name="Nombre",
            last_name="Original",
            email="original@example.com",
            telefono="387-000",
            role=self.role,
        )
        self.client.force_login(self.user)

    def test_account_settings_shows_profile_and_password_forms(self):
        response = self.client.get(reverse("users:account_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mi cuenta")
        self.assertContains(response, "Datos propios")
        self.assertContains(response, "Cambiar contraseña")
        self.assertContains(response, "Contraseña actual")
        self.assertContains(response, "Confirmar contraseña nueva")

    def test_account_settings_updates_own_profile_data(self):
        response = self.client.post(
            reverse("users:account_settings"),
            {
                "form_kind": "profile",
                "first_name": "Nombre Nuevo",
                "last_name": "Apellido Nuevo",
                "email": "nuevo@example.com",
                "telefono": "387-111",
            },
        )

        self.assertRedirects(response, reverse("users:account_settings"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Nombre Nuevo")
        self.assertEqual(self.user.last_name, "Apellido Nuevo")
        self.assertEqual(self.user.email, "nuevo@example.com")
        self.assertEqual(self.user.telefono, "387-111")

    def test_account_settings_password_requires_current_password(self):
        response = self.client.post(
            reverse("users:account_settings"),
            {
                "form_kind": "password",
                "old_password": "incorrecta",
                "new_password1": "nueva12345",
                "new_password2": "nueva12345",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("secret12345"))
        self.assertFalse(self.user.check_password("nueva12345"))

    def test_account_settings_password_changes_password_and_keeps_session(self):
        response = self.client.post(
            reverse("users:account_settings"),
            {
                "form_kind": "password",
                "old_password": "secret12345",
                "new_password1": "nueva12345",
                "new_password2": "nueva12345",
            },
        )

        self.assertRedirects(response, reverse("users:account_settings"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("nueva12345"))
        self.assertFalse(self.user.must_change_password)
        follow_up = self.client.get(reverse("users:account_settings"))
        self.assertEqual(follow_up.status_code, 200)


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
            telefono="387-111",
        )

    def test_personal_list_is_minimal_and_hides_extra_fields(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:personal_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Juan")
        self.assertContains(response, "Perez")
        self.assertContains(response, "Encargado")
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

    def test_personal_update_preserves_password_when_left_blank(self):
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

    def test_personal_create_uses_minimal_fields_and_redirects_to_user_list(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:personal_create"),
            {
                "username": "operadora_minima",
                "first_name": "Marta",
                "last_name": "Caja",
                "password": "secret12345",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("users:user_list"))
        self.assertContains(response, "usuario creado correctamente")
        created_user = User.objects.get(username="operadora_minima")
        self.assertEqual(created_user.first_name, "Marta")
        self.assertEqual(created_user.last_name, "Caja")
        self.assertIsNone(created_user.role)
        self.assertFalse(created_user.usuario_fijo)
        self.assertIsNone(created_user.sucursal_base)
        self.assertTrue(created_user.must_change_password)

    def test_personal_create_page_hides_non_minimal_fields(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:personal_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usuario")
        self.assertContains(response, "Nombre")
        self.assertContains(response, "Apellido")
        self.assertContains(response, "Contraseña temporal")
        self.assertNotContains(response, "DNI")
        self.assertNotContains(response, "Rol / Permisos")
        self.assertNotContains(response, "Usuario fijo")
        self.assertNotContains(response, "Sucursal base")

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

    def test_user_detail_shows_first_access_link_when_password_change_is_pending(self):
        self.operator.must_change_password = True
        self.operator.save(update_fields=["must_change_password"])
        self.client.force_login(self.admin)

        response = self.client.get(reverse("users:user_detail", args=[self.operator.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Link de primer ingreso")
        self.assertContains(response, "/primer-ingreso/")

    def test_user_detail_updates_role_and_effective_access(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:user_detail", args=[self.operator.pk]),
            {
                "role": self.admin_role.pk,
                "usuario_fijo": "",
                "sucursal_base": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertEqual(self.operator.role, self.admin_role)
        self.assertTrue(self.operator.is_cashops_admin())

    def test_user_permission_badge_toggle_creates_user_override(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                "users:user_permission_toggle",
                args=[self.operator.pk, PermissionModule.TREASURY, "read"],
            )
        )

        self.assertEqual(response.status_code, 302)
        override = UserPermission.objects.get(user=self.operator, module=PermissionModule.TREASURY)
        self.assertTrue(override.can_read)
        self.assertFalse(override.can_write)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.has_module_permission(PermissionModule.TREASURY, "read"))

    def test_write_permission_toggle_also_enables_read(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                "users:user_permission_toggle",
                args=[self.operator.pk, PermissionModule.USERS, "write"],
            )
        )

        self.assertEqual(response.status_code, 302)
        override = UserPermission.objects.get(user=self.operator, module=PermissionModule.USERS)
        self.assertTrue(override.can_read)
        self.assertTrue(override.can_write)

    def test_role_permissions_are_managed_from_roles_submenu(self):
        self.client.force_login(self.admin)

        create_response = self.client.post(
            reverse("users:role_create"),
            {
                "code": "LECTURA",
                "name": "Solo lectura usuarios",
                "is_active": "on",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        role = Role.objects.get(code="LECTURA")
        self.assertEqual(role.permissions.count(), 5)

        toggle_response = self.client.post(
            reverse(
                "users:role_permission_toggle",
                args=[role.pk, PermissionModule.USERS, "read"],
            )
        )

        self.assertEqual(toggle_response.status_code, 302)
        permission = RolePermission.objects.get(role=role, module=PermissionModule.USERS)
        self.assertTrue(permission.can_read)
        self.assertFalse(permission.can_write)

    def test_user_with_users_read_can_view_but_not_write_users(self):
        read_role = Role.objects.create(code="USERS_READ", name="Usuarios lectura")
        RolePermission.objects.create(role=read_role, module=PermissionModule.USERS, can_read=True)
        read_user = User.objects.create_user(
            username="usuarios_lectura",
            password="secret12345",
            role=read_role,
        )
        self.client.force_login(read_user)

        list_response = self.client.get(reverse("users:user_list"))
        create_response = self.client.get(reverse("users:user_create"))

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)

    def test_treasury_read_permission_allows_dashboard_but_blocks_create_forms(self):
        treasury_role = Role.objects.create(code="TESO_READ", name="Tesoreria lectura")
        RolePermission.objects.create(role=treasury_role, module=PermissionModule.TREASURY, can_read=True)
        read_user = User.objects.create_user(
            username="tesoreria_lectura_view",
            password="secret12345",
            role=treasury_role,
        )
        self.client.force_login(read_user)

        dashboard_response = self.client.get(reverse("treasury:dashboard"))
        create_response = self.client.get(reverse("treasury:proveedores_create"))

        self.assertEqual(dashboard_response.status_code, 200)
        self.assertEqual(create_response.status_code, 403)

    def test_cashops_read_permission_is_required_for_dashboard(self):
        no_cash_role = Role.objects.create(code="SIN_CAJA", name="Sin caja")
        RolePermission.objects.create(role=no_cash_role, module=PermissionModule.CASHOPS, can_read=False, can_write=False)
        read_user = User.objects.create_user(
            username="sin_caja",
            password="secret12345",
            role=no_cash_role,
        )
        self.client.force_login(read_user)

        response = self.client.get(reverse("cashops:dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_user_archive_disables_login(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("users:user_archive", args=[self.operator.pk]))

        self.assertEqual(response.status_code, 302)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.is_active)
        self.client.logout()
        self.assertFalse(self.client.login(username=self.operator.username, password="secret12345"))

    def test_user_delete_removes_user_without_operational_history(self):
        disposable = User.objects.create_user(
            username="usuario_descartable",
            password="secret12345",
            role=self.operator_role,
        )
        self.client.force_login(self.admin)

        response = self.client.post(reverse("users:user_delete", args=[disposable.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(pk=disposable.pk).exists())

    def test_admin_cannot_archive_self_from_detail_form(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("users:user_detail", args=[self.admin.pk]),
            {
                "role": self.admin_role.pk,
                "usuario_fijo": "",
                "sucursal_base": "",
                "is_active": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class FirstAccessPasswordTests(TestCase):
    def setUp(self):
        self.role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.user = User.objects.create_user(
            username="primer_ingreso",
            password="default12345",
            first_name="Luz",
            last_name="Caja",
            role=self.role,
            must_change_password=True,
        )

    def test_default_password_login_redirects_to_mandatory_change(self):
        response = self.client.post(
            reverse("users:login"),
            {"username": self.user.username, "password": "default12345"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:password_change_required"))

        dashboard_response = self.client.get(reverse("cashops:dashboard"))
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertEqual(dashboard_response.url, reverse("users:password_change_required"))

    def test_mandatory_change_clears_flag_and_keeps_session(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("users:password_change_required"),
            {
                "old_password": "default12345",
                "new_password1": "propia12345",
                "new_password2": "propia12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("propia12345"))

    def test_first_access_link_sets_password_without_default_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)

        response = self.client.post(
            reverse("users:first_access", args=[uid, token]),
            {
                "new_password1": "linkpropia12345",
                "new_password2": "linkpropia12345",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:login"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("linkpropia12345"))
        self.assertFalse(self.user.check_password("default12345"))

    def test_first_access_link_is_invalid_after_password_change(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = default_token_generator.make_token(self.user)
        self.user.set_password("alreadychanged12345")
        self.user.must_change_password = False
        self.user.save(update_fields=["password", "must_change_password"])

        response = self.client.get(reverse("users:first_access", args=[uid, token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El link no está vigente")
