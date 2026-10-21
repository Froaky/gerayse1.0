"""EP-14 US-14.1: permisos de asistencia, entrada en el menu y acceso.

Riesgos que cubre:
- que un rol o usuario existente reciba los permisos nuevos sin que se los
  asignen (solo los administradores del sistema los tienen por defecto);
- que la matriz de usuarios y roles se rompa por un modulo sin metadata;
- que el codigo del modulo no entre en la columna (Postgres corta en 20 y
  SQLite no, asi que los tests locales no lo verian sin este chequeo);
- que alguien sin permiso entre a /asistencia/ conociendo la URL.
"""

from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, Sucursal
from users.models import PermissionModule, Role, RolePermission, User, UserPermission
from users.views import PERMISSION_MODULE_META

FICHAR = PermissionModule.ASISTENCIA_FICHAR
ADMINISTRAR = PermissionModule.ASISTENCIA_ADMIN


class AsistenciaTestCase(TestCase):
    def setUp(self):
        self.empresa = Empresa.objects.create(nombre="Empresa Asistencia")
        self.sucursal = Sucursal.objects.create(
            empresa=self.empresa,
            nombre="Sucursal Asistencia",
            codigo="ASI-01",
            razon_social="Empresa Asistencia SRL",
        )
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-asis", password="test", role=self.admin_role)
        self.operator = User.objects.create_user(username="operador-asis", password="test", role=self.operator_role)
        for user in (self.admin, self.operator):
            user.empresas_permitidas.set([self.empresa])

    def _grant(self, user, module, can_write=True):
        return UserPermission.objects.create(user=user, module=module, can_read=True, can_write=can_write)


class PermisosAsistenciaTests(AsistenciaTestCase):
    def test_admin_tiene_los_dos_y_un_rol_comun_ninguno(self):
        self.assertTrue(self.admin.can_fichar_asistencia())
        self.assertTrue(self.admin.can_administrar_asistencia())
        self.assertFalse(self.operator.can_fichar_asistencia())
        self.assertFalse(self.operator.can_administrar_asistencia())
        self.assertFalse(self.operator.can_access_asistencia())

    def test_superusuario_tiene_los_dos(self):
        superuser = User.objects.create_superuser(username="root-asis", password="test")
        self.assertTrue(superuser.can_fichar_asistencia())
        self.assertTrue(superuser.can_administrar_asistencia())

    def test_el_rol_cajero_sembrado_no_recibe_ninguno(self):
        cajero_role = Role.objects.get(code="CAJERO")
        cajero = User.objects.create_user(username="cajero-asis", password="test", role=cajero_role)
        self.assertFalse(cajero.can_fichar_asistencia())
        self.assertFalse(cajero.can_administrar_asistencia())

    def test_el_rol_concede_fichar_sin_conceder_administrar(self):
        RolePermission.objects.create(role=self.operator_role, module=FICHAR, can_read=True, can_write=True)
        self.assertTrue(self.operator.can_fichar_asistencia())
        self.assertFalse(self.operator.can_administrar_asistencia())
        self.assertTrue(self.operator.can_access_asistencia())

    def test_el_override_del_usuario_manda_sobre_el_rol(self):
        RolePermission.objects.create(role=self.operator_role, module=FICHAR, can_read=True, can_write=True)
        self._grant(self.operator, FICHAR, can_write=False)
        self._grant(self.operator, ADMINISTRAR)
        self.assertFalse(self.operator.can_fichar_asistencia())
        self.assertTrue(self.operator.can_administrar_asistencia())

    def test_lectura_sola_no_habilita(self):
        RolePermission.objects.create(role=self.operator_role, module=FICHAR, can_read=True, can_write=False)
        self._grant(self.operator, ADMINISTRAR, can_write=False)
        self.assertFalse(self.operator.can_fichar_asistencia())
        self.assertFalse(self.operator.can_administrar_asistencia())

    def test_usuario_inactivo_no_tiene_ninguno(self):
        self.admin.is_active = False
        self.admin.save()
        self.assertFalse(self.admin.can_fichar_asistencia())
        self.assertFalse(self.admin.can_administrar_asistencia())

    def test_las_filas_nuevas_de_un_rol_comun_nacen_apagadas(self):
        self.operator_role.ensure_permission_rows()
        self.admin_role.ensure_permission_rows()
        for module in (FICHAR, ADMINISTRAR):
            comun = RolePermission.objects.get(role=self.operator_role, module=module)
            admin = RolePermission.objects.get(role=self.admin_role, module=module)
            self.assertFalse(comun.can_write)
            self.assertTrue(admin.can_write)


class MatrizDePermisosTests(AsistenciaTestCase):
    def test_todo_modulo_tiene_metadata_para_la_matriz(self):
        self.assertEqual(set(PERMISSION_MODULE_META), set(PermissionModule.values))

    def test_los_codigos_entran_en_la_columna(self):
        for model in (RolePermission, UserPermission):
            max_length = model._meta.get_field("module").max_length
            for value in PermissionModule.values:
                self.assertLessEqual(len(value), max_length, f"{value} no entra en {model.__name__}.module")

    def test_la_ficha_del_usuario_muestra_los_dos_permisos(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("users:user_detail", args=[self.operator.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fichar asistencia")
        self.assertContains(response, "Administrar asistencia")

    def test_la_ficha_del_rol_muestra_los_dos_permisos(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("users:role_detail", args=[self.operator_role.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fichar asistencia")
        self.assertContains(response, "Administrar asistencia")

    def test_el_toggle_prende_y_apaga_fichar(self):
        self.client.force_login(self.admin)
        url = reverse("users:user_permission_toggle", args=[self.operator.pk, FICHAR, "write"])

        self.assertEqual(self.client.post(url).status_code, 302)
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.can_fichar_asistencia())

        self.assertEqual(self.client.post(url).status_code, 302)
        self.operator.refresh_from_db()
        self.assertFalse(self.operator.can_fichar_asistencia())


class AccesoAsistenciaTests(AsistenciaTestCase):
    def test_anonimo_va_al_login(self):
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:login"), response["Location"])

    def test_sin_permiso_da_403_aunque_conozca_la_url(self):
        self.client.force_login(self.operator)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 403)

    def test_lectura_sola_da_403(self):
        self._grant(self.operator, FICHAR, can_write=False)
        self._grant(self.operator, ADMINISTRAR, can_write=False)
        self.client.force_login(self.operator)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 403)

    def test_quien_ficha_ve_solo_fichar(self):
        self._grant(self.operator, FICHAR)
        self.client.force_login(self.operator)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="asistencia-fichar"')
        self.assertNotContains(response, 'id="asistencia-administrar"')

    def test_quien_administra_ve_solo_administrar(self):
        self._grant(self.operator, ADMINISTRAR)
        self.client.force_login(self.operator)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="asistencia-administrar"')
        self.assertNotContains(response, 'id="asistencia-fichar"')

    def test_el_admin_ve_las_dos(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="asistencia-fichar"')
        self.assertContains(response, 'id="asistencia-administrar"')


class MenuAsistenciaTests(AsistenciaTestCase):
    def test_el_menu_de_caja_muestra_asistencia_solo_con_permiso(self):
        link = reverse("asistencia:inicio")
        self.client.force_login(self.operator)
        self.assertNotContains(self.client.get(reverse("cashops:box_tracking")), link)

        self._grant(self.operator, FICHAR)
        self.assertContains(self.client.get(reverse("cashops:box_tracking")), link)

    def test_el_menu_de_tesoreria_muestra_asistencia_solo_con_permiso(self):
        link = reverse("asistencia:inicio")
        tesorero = User.objects.create_user(username="tesorero-asis", password="test", role=self.operator_role)
        tesorero.empresas_permitidas.set([self.empresa])
        self._grant(tesorero, PermissionModule.TREASURY)
        self.client.force_login(tesorero)
        self.assertNotContains(self.client.get(reverse("treasury:dashboard")), link)

        self._grant(tesorero, ADMINISTRAR)
        self.assertContains(self.client.get(reverse("treasury:dashboard")), link)
