"""EP-14 US-14.2: padron de empleados y carga desde un listado.

Riesgos que cubre:
- un DNI repetido entre activos (dos carnets para la misma persona);
- un local habitual de otra empresa, o sin empresa;
- ver o tocar empleados de otra empresa por filtro, por la sesion o por URL;
- una baja que borre en vez de marcar, o que no deje quien y cuando;
- una carga masiva que guarde a medias, que se saltee el alcance o que caiga
  con 500 ante un archivo raro.
"""

import tempfile
import zipfile
import zlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa, Sucursal
from treasury.tests_importacion_extracto import crear_xlsx
from users.models import PermissionModule, Role, User, UserPermission

from .importacion import Estado
from .models import Empleado
from .services import actualizar_empleado, crear_empleado, dar_de_baja_empleado, reactivar_empleado
from .views import SESION_LISTADO


class PadronTestCase(TestCase):
    def setUp(self):
        self.armadi = Empresa.objects.create(nombre="ARMADI")
        self.mapogo = Empresa.objects.create(nombre="MAPOGO")
        self.ajena = Empresa.objects.create(nombre="Empresa Ajena")
        self.centro = Sucursal.objects.create(empresa=self.armadi, nombre="Centro", codigo="CENT-02", razon_social="Armadi SRL")
        self.terminal = Sucursal.objects.create(empresa=self.armadi, nombre="Terminal", codigo="TERM-01", razon_social="Armadi SRL")
        self.vivre = Sucursal.objects.create(empresa=self.mapogo, nombre="Vivré", codigo="VIV-01", razon_social="Mapogo SRL")
        self.sucursal_ajena = Sucursal.objects.create(empresa=self.ajena, nombre="Ajena", codigo="AJE-01", razon_social="Ajena SRL")
        self.sin_empresa = Sucursal.objects.create(nombre="Legacy", codigo="LEG-01", razon_social="Legacy SRL")
        self.admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        self.operator_role = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-padron", password="test", role=self.admin_role)
        self.admin.empresas_permitidas.set([self.armadi, self.mapogo])
        self.cajero = User.objects.create_user(username="cajero-padron", password="test", role=self.operator_role)
        self.cajero.empresas_permitidas.set([self.armadi])
        UserPermission.objects.create(
            user=self.cajero, module=PermissionModule.ASISTENCIA_FICHAR, can_read=True, can_write=True
        )

    def _empleado(self, nombre="Juan Perez", dni="30123456", sucursal=None, actor=None):
        sucursal = sucursal or self.centro
        return crear_empleado(
            actor=actor or self.admin, nombre=nombre, dni=dni, empresa=sucursal.empresa, sucursal=sucursal
        )

    def _login(self, user, empresas=None):
        self.client.force_login(user)
        if empresas is not None:
            session = self.client.session
            session["empresa_ids"] = [empresa.pk for empresa in empresas]
            session.save()


class EmpleadoModeloTests(PadronTestCase):
    def test_normaliza_el_dni_y_el_nombre(self):
        empleado = self._empleado(nombre="  Juan   Perez ", dni="30.123.456")
        self.assertEqual(empleado.dni, "30123456")
        self.assertEqual(empleado.nombre, "Juan Perez")

    def test_rechaza_un_dni_con_letras_o_corto(self):
        for dni in ("30A12345", "123"):
            with self.subTest(dni=dni), self.assertRaises(ValidationError) as ctx:
                self._empleado(dni=dni)
            self.assertIn("dni", ctx.exception.message_dict)

    def test_rechaza_un_local_de_otra_empresa(self):
        with self.assertRaises(ValidationError) as ctx:
            crear_empleado(actor=self.admin, nombre="Ana", dni="30111222", empresa=self.armadi, sucursal=self.vivre)
        self.assertIn("sucursal", ctx.exception.message_dict)

    def test_rechaza_un_local_sin_empresa(self):
        with self.assertRaises(ValidationError) as ctx:
            crear_empleado(actor=self.admin, nombre="Ana", dni="30111222", empresa=self.armadi, sucursal=self.sin_empresa)
        self.assertIn("sucursal", ctx.exception.message_dict)

    def test_no_permite_dos_activos_con_el_mismo_dni(self):
        self._empleado()
        with self.assertRaises(ValidationError) as ctx:
            self._empleado(nombre="Otro", dni="30.123.456")
        self.assertIn("dni", ctx.exception.message_dict)

    def test_un_dado_de_baja_no_bloquea_el_dni(self):
        viejo = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=viejo)
        nuevo = self._empleado(nombre="Juan Perez (reingreso)")
        self.assertTrue(nuevo.activo)

    def test_la_base_impide_dos_activos_con_el_mismo_dni_aunque_se_saltee_el_clean(self):
        self._empleado()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Empleado.objects.create(nombre="Saltea", dni="30123456", empresa=self.armadi, sucursal=self.centro)


class ServiciosPadronTests(PadronTestCase):
    def test_sin_permiso_de_administrar_no_crea(self):
        with self.assertRaises(PermissionDenied):
            self._empleado(actor=self.cajero)
        self.assertFalse(Empleado.objects.exists())

    def test_no_crea_en_una_empresa_no_permitida(self):
        with self.assertRaises(ValidationError):
            self._empleado(sucursal=self.sucursal_ajena)
        self.assertFalse(Empleado.objects.exists())

    def test_la_baja_no_borra_y_deja_quien_y_cuando(self):
        empleado = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        empleado.refresh_from_db()
        self.assertFalse(empleado.activo)
        self.assertEqual(empleado.dado_de_baja_por, self.admin)
        self.assertIsNotNone(empleado.dado_de_baja_en)
        self.assertEqual(Empleado.objects.count(), 1)

    def test_no_se_da_de_baja_dos_veces(self):
        empleado = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        with self.assertRaises(ValidationError):
            dar_de_baja_empleado(actor=self.admin, empleado=empleado)

    def test_reactivar_vuelve_a_activo_y_limpia_la_baja(self):
        empleado = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        reactivar_empleado(actor=self.admin, empleado=empleado)
        empleado.refresh_from_db()
        self.assertTrue(empleado.activo)
        self.assertIsNone(empleado.dado_de_baja_en)
        self.assertIsNone(empleado.dado_de_baja_por)

    def test_no_reactiva_si_el_dni_ya_esta_activo_en_otro(self):
        viejo = self._empleado()
        dar_de_baja_empleado(actor=self.admin, empleado=viejo)
        self._empleado(nombre="Juan Perez (reingreso)")
        with self.assertRaises(ValidationError):
            reactivar_empleado(actor=self.admin, empleado=viejo)
        viejo.refresh_from_db()
        self.assertFalse(viejo.activo)

    def test_actualizar_cambia_el_local_y_sigue_validando_la_empresa(self):
        empleado = self._empleado()
        actualizar_empleado(
            actor=self.admin, empleado=empleado, nombre="Juan Perez", dni="30123456",
            empresa=self.armadi, sucursal=self.terminal,
        )
        empleado.refresh_from_db()
        self.assertEqual(empleado.sucursal, self.terminal)
        with self.assertRaises(ValidationError):
            actualizar_empleado(
                actor=self.admin, empleado=empleado, nombre="Juan Perez", dni="30123456",
                empresa=self.armadi, sucursal=self.vivre,
            )


class VistasPadronTests(PadronTestCase):
    def test_anonimo_va_al_login(self):
        response = self.client.get(reverse("asistencia:empleado_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("users:login"), response["Location"])

    def test_quien_solo_ficha_no_entra_al_padron(self):
        self._login(self.cajero)
        for url in (
            reverse("asistencia:empleado_list"),
            reverse("asistencia:empleado_create"),
            reverse("asistencia:empleado_importar"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_el_inicio_lleva_al_padron_a_quien_administra(self):
        self._login(self.admin)
        response = self.client.get(reverse("asistencia:inicio"))
        self.assertContains(response, reverse("asistencia:empleado_list"))

    def test_el_listado_respeta_las_empresas_seleccionadas(self):
        de_armadi = self._empleado(nombre="Empleado de Armadi")
        de_mapogo = self._empleado(nombre="Empleado de Mapogo", dni="31000111", sucursal=self.vivre)

        self._login(self.admin, [self.armadi])
        response = self.client.get(reverse("asistencia:empleado_list"))
        self.assertContains(response, de_armadi.nombre)
        self.assertNotContains(response, de_mapogo.nombre)

        self._login(self.admin, [self.armadi, self.mapogo])
        response = self.client.get(reverse("asistencia:empleado_list"))
        self.assertContains(response, de_armadi.nombre)
        self.assertContains(response, de_mapogo.nombre)

    def test_una_empresa_no_permitida_puesta_en_la_sesion_no_abre_nada(self):
        Empleado.objects.create(nombre="Empleado Ajeno", dni="32000111", empresa=self.ajena, sucursal=self.sucursal_ajena)
        self._login(self.admin, [self.ajena])
        response = self.client.get(reverse("asistencia:empleado_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Empleado Ajeno")

    def test_filtros_de_estado_local_y_busqueda(self):
        activo = self._empleado(nombre="Activo Centro")
        otro = self._empleado(nombre="Otro Activo", dni="35666777")
        de_baja = self._empleado(nombre="Baja Terminal", dni="31222333", sucursal=self.terminal)
        dar_de_baja_empleado(actor=self.admin, empleado=de_baja)
        self._login(self.admin, [self.armadi])
        url = reverse("asistencia:empleado_list")

        por_defecto = self.client.get(url)
        self.assertContains(por_defecto, activo.nombre)
        self.assertNotContains(por_defecto, de_baja.nombre)

        bajas = self.client.get(url, {"estado": "baja"})
        self.assertContains(bajas, de_baja.nombre)
        self.assertNotContains(bajas, activo.nombre)

        por_local = self.client.get(url, {"estado": "todos", "sucursal": self.terminal.pk})
        self.assertContains(por_local, de_baja.nombre)
        self.assertNotContains(por_local, activo.nombre)

        por_dni = self.client.get(url, {"q": "30.123.456"})
        self.assertContains(por_dni, activo.nombre)
        self.assertNotContains(por_dni, otro.nombre)

        por_nombre = self.client.get(url, {"q": "otro"})
        self.assertContains(por_nombre, otro.nombre)
        self.assertNotContains(por_nombre, activo.nombre)

        de_mapogo = self._empleado(nombre="Empleado Vivre", dni="36777888", sucursal=self.vivre)
        self._login(self.admin, [self.armadi, self.mapogo])
        por_empresa = self.client.get(url, {"empresa": self.mapogo.pk})
        self.assertContains(por_empresa, de_mapogo.nombre)
        self.assertNotContains(por_empresa, activo.nombre)

    def test_alta_por_formulario(self):
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_create"),
            {"nombre": "Nuevo Empleado", "dni": "33.444.555", "empresa": self.armadi.pk, "sucursal": self.centro.pk},
        )
        self.assertEqual(response.status_code, 302)
        empleado = Empleado.objects.get(dni="33444555")
        self.assertEqual(empleado.creado_por, self.admin)

    def test_el_formulario_solo_ofrece_empresas_y_locales_del_alcance(self):
        self._login(self.admin, [self.armadi])
        form = self.client.get(reverse("asistencia:empleado_create")).context["form"]
        self.assertEqual(set(form.fields["empresa"].queryset), {self.armadi})
        self.assertEqual(set(form.fields["sucursal"].queryset), {self.centro, self.terminal})

    def test_un_local_ajeno_por_post_no_crea(self):
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_create"),
            {"nombre": "Colado", "dni": "33444555", "empresa": self.ajena.pk, "sucursal": self.sucursal_ajena.pk},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Empleado.objects.exists())

    def test_un_dni_repetido_por_formulario_muestra_el_error(self):
        self._empleado()
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_create"),
            {"nombre": "Repetido", "dni": "30123456", "empresa": self.armadi.pk, "sucursal": self.centro.pk},
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Ya hay un empleado activo con ese DNI", status_code=400)
        self.assertEqual(Empleado.objects.count(), 1)

    def test_un_empleado_de_otra_empresa_da_404_por_url(self):
        de_mapogo = self._empleado(nombre="Empleado de Mapogo", dni="31000111", sucursal=self.vivre)
        self._login(self.admin, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:empleado_update", args=[de_mapogo.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("asistencia:empleado_baja", args=[de_mapogo.pk])).status_code, 404)
        de_mapogo.refresh_from_db()
        self.assertTrue(de_mapogo.activo)

    def test_baja_y_reactivacion_por_post(self):
        empleado = self._empleado()
        self._login(self.admin, [self.armadi])
        self.assertEqual(self.client.get(reverse("asistencia:empleado_baja", args=[empleado.pk])).status_code, 405)

        self.assertEqual(self.client.post(reverse("asistencia:empleado_baja", args=[empleado.pk])).status_code, 302)
        empleado.refresh_from_db()
        self.assertFalse(empleado.activo)

        self.assertEqual(self.client.post(reverse("asistencia:empleado_reactivar", args=[empleado.pk])).status_code, 302)
        empleado.refresh_from_db()
        self.assertTrue(empleado.activo)

    def test_editar_por_formulario(self):
        empleado = self._empleado()
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_update", args=[empleado.pk]),
            {"nombre": "Juan Perez Editado", "dni": "30123456", "empresa": self.armadi.pk, "sucursal": self.terminal.pk},
        )
        self.assertEqual(response.status_code, 302)
        empleado.refresh_from_db()
        self.assertEqual(empleado.nombre, "Juan Perez Editado")
        self.assertEqual(empleado.sucursal, self.terminal)


class _CargaMixin:
    def _subir(self, contenido: bytes, nombre="empleados.csv"):
        return self.client.post(
            reverse("asistencia:empleado_importar"),
            {"archivo": SimpleUploadedFile(nombre, contenido)},
        )

    def _confirmar(self, token=None):
        if token is None:
            token = self.client.session.get(SESION_LISTADO, {}).get("token", "")
        return self.client.post(reverse("asistencia:empleado_importar_confirmar"), {"token": token})

    @staticmethod
    def _estados(response):
        return {fila.fila: fila for fila in response.context["filas"]}


class ImportacionPadronTests(_CargaMixin, PadronTestCase):

    def test_un_csv_con_coma_y_bom_se_revisa_sin_guardar(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        contenido = "﻿Nombre y apellido,DNI,Local\nJuan Perez,30.123.456,CENT-02\nAna Gomez,28999888,Terminal\n"
        response = self._subir(contenido.encode("utf-8"))
        self.assertEqual(response.status_code, 200)
        filas = self._estados(response)
        self.assertEqual({f.estado for f in filas.values()}, {Estado.LISTA})
        self.assertFalse(Empleado.objects.exists())
        self.assertIn(SESION_LISTADO, self.client.session)

    def test_confirmar_carga_las_filas_revisadas(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        self._subir(b"Nombre,DNI,Local\nJuan Perez,30123456,CENT-02\nAna Gomez,28999888,Terminal\n")
        response = self._confirmar()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Empleado.objects.count(), 2)
        juan = Empleado.objects.get(dni="30123456")
        self.assertEqual(juan.empresa, self.armadi)
        self.assertEqual(juan.sucursal, self.centro)
        self.assertEqual(juan.creado_por, self.admin)
        self.assertNotIn(SESION_LISTADO, self.client.session)

    def test_un_csv_de_excel_en_ansi_con_punto_y_coma_y_titulo_arriba(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        contenido = "PADRON OCTUBRE;;;\nApellido y nombre;Documento;Sucursal;Empresa\nJosé Ruíz;31222333;Vivré;mapogo\n"
        response = self._subir(contenido.encode("cp1252"))
        fila = self._estados(response)[3]
        self.assertEqual(fila.estado, Estado.LISTA, fila.motivo)
        self._confirmar()
        empleado = Empleado.objects.get(dni="31222333")
        self.assertEqual(empleado.nombre, "José Ruíz")
        self.assertEqual(empleado.sucursal, self.vivre)
        self.assertEqual(empleado.empresa, self.mapogo)

    def test_un_xlsx_con_el_dni_como_numero(self):
        self._login(self.admin, [self.armadi])
        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "padron.xlsx"
            crear_xlsx(ruta, {"Hoja1": [["Nombre", "DNI", "Local"], ["Carla Diaz", Decimal("33444555"), "term-01"]]})
            contenido = ruta.read_bytes()
        response = self._subir(contenido, nombre="padron.xlsx")
        self.assertEqual(self._estados(response)[2].estado, Estado.LISTA)
        self._confirmar()
        empleado = Empleado.objects.get(dni="33444555")
        self.assertEqual(empleado.sucursal, self.terminal)

    def test_informa_fila_por_fila_que_se_rechaza_y_por_que(self):
        self._empleado(nombre="Ya Cargado", dni="30123456")
        de_baja = self._empleado(nombre="Se Fue", dni="30999000")
        dar_de_baja_empleado(actor=self.admin, empleado=de_baja)
        self._login(self.admin, [self.armadi])
        contenido = (
            "Nombre,DNI,Local,Empresa\n"
            ",30111222,CENT-02,\n"                  # 2: sin nombre
            "Pedro,12ab,CENT-02,\n"                 # 3: dni invalido
            "Luis,30222333,Inexistente,\n"          # 4: local que no existe
            "Marta,30333444,AJE-01,\n"              # 5: local de una empresa ajena
            "Sofia,30444555,CENT-02,MAPOGO\n"       # 6: empresa que no es la del local
            "Lucas,30555666,CENT-02,\n"             # 7: bien
            "Lucas Bis,30.555.666,TERM-01,\n"       # 8: dni repetido en el archivo
            "Otro,30123456,CENT-02,\n"              # 9: dni de un activo
            "Volvio,30999000,CENT-02,\n"            # 10: dni de uno dado de baja
        )
        response = self._subir(contenido.encode("utf-8"))
        filas = self._estados(response)
        esperados = {
            2: "Falta el nombre",
            3: "DNI inválido",
            4: "No hay un local 'Inexistente'",
            5: "No hay un local 'AJE-01'",
            6: "no coincide con la del local",
            8: "DNI repetido: ya está en la fila 7",
            9: "Ya hay un empleado activo con ese DNI",
            10: "dado de baja con ese DNI",
        }
        for numero, motivo in esperados.items():
            with self.subTest(fila=numero):
                self.assertEqual(filas[numero].estado, Estado.RECHAZADA)
                self.assertIn(motivo, filas[numero].motivo)
        self.assertEqual(filas[7].estado, Estado.LISTA)

        antes = Empleado.objects.count()
        self._confirmar()
        self.assertEqual(Empleado.objects.count(), antes + 1)
        self.assertTrue(Empleado.objects.filter(dni="30555666", activo=True).exists())

    def test_confirmar_vuelve_a_validar(self):
        self._login(self.admin, [self.armadi])
        self._subir(b"Nombre,DNI,Local\nJuan Perez,30123456,CENT-02\n")
        self._empleado(nombre="Cargado en el medio", dni="30123456")
        response = self._confirmar()
        self.assertEqual(self._estados(response)[2].estado, Estado.RECHAZADA)
        self.assertEqual(Empleado.objects.filter(dni="30123456").count(), 1)

    def test_confirmar_usa_el_alcance_del_momento(self):
        self._login(self.admin, [self.armadi, self.mapogo])
        self._subir("Nombre,DNI,Local\nJosé Ruíz,31222333,VIV-01\n".encode("utf-8"))
        session = self.client.session
        session["empresa_ids"] = [self.armadi.pk]
        session.save()
        response = self._confirmar()
        self.assertEqual(self._estados(response)[2].estado, Estado.RECHAZADA)
        self.assertFalse(Empleado.objects.exists())

    def test_un_excel_roto_no_da_500(self):
        self._login(self.admin, [self.armadi])
        response = self._subir(b"no soy un zip", nombre="padron.xlsx")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No se pudo leer el Excel", status_code=400)

    def test_un_listado_sin_titulos_se_informa(self):
        self._login(self.admin, [self.armadi])
        response = self._subir(b"a,b,c\n1,2,3\n")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "No encuentro la fila de títulos", status_code=400)

    def test_solo_acepta_csv_o_xlsx(self):
        # El contenido es un listado valido: el 400 tiene que venir de la extension.
        self._login(self.admin, [self.armadi])
        response = self._subir(b"Nombre,DNI,Local\nJuan,30123456,CENT-02\n", nombre="padron.txt")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "tiene que ser un CSV o un Excel", status_code=400)
        self.assertNotIn(SESION_LISTADO, self.client.session)

    def test_confirmar_sin_revisar_vuelve_a_la_carga(self):
        self._login(self.admin, [self.armadi])
        response = self._confirmar()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("asistencia:empleado_importar"))

    def test_quien_solo_ficha_no_importa(self):
        self._login(self.cajero)
        response = self._subir(b"Nombre,DNI,Local\nJuan,30123456,CENT-02\n")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._confirmar().status_code, 403)
        self.assertFalse(Empleado.objects.exists())


def _xlsx(filas, reemplazar=None, ruta_hoja=None, otras_hojas=None, comprimir=False) -> bytes:
    """Un .xlsx cuya primera hoja es `filas`. `reemplazar` cambia texto crudo del
    XML de esa hoja y `ruta_hoja` la guarda en otra ruta del zip (con su rel),
    para armar archivos raros como los que puede subir cualquiera."""
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = Path(carpeta) / "libro.xlsx"
        crear_xlsx(ruta, {"Hoja1": filas, **(otras_hojas or {})})
        if not reemplazar and not ruta_hoja:
            return ruta.read_bytes()
        salida = Path(carpeta) / "raro.xlsx"
        compresion = zipfile.ZIP_DEFLATED if comprimir else zipfile.ZIP_STORED
        with zipfile.ZipFile(ruta) as origen, zipfile.ZipFile(salida, "w", compresion) as destino:
            for miembro in origen.infolist():
                nombre, datos = miembro.filename, origen.read(miembro)
                if nombre == "xl/worksheets/sheet1.xml":
                    for viejo, nuevo in (reemplazar or {}).items():
                        datos = datos.replace(viejo.encode(), nuevo.encode())
                    if ruta_hoja:
                        nombre = f"xl/{ruta_hoja}"
                if ruta_hoja and nombre == "xl/_rels/workbook.xml.rels":
                    datos = datos.replace(b'Target="worksheets/sheet1.xml"', f'Target="{ruta_hoja}"'.encode())
                destino.writestr(nombre, datos)
        return salida.read_bytes()


class PadronAislamientoYArchivosRarosTests(_CargaMixin, PadronTestCase):
    """Casos que encontro la revision adversarial de la US-14.2."""

    def setUp(self):
        super().setUp()
        self.admin_armadi = User.objects.create_user(username="admin-solo-armadi", password="test", role=self.admin_role)
        self.admin_armadi.empresas_permitidas.set([self.armadi])
        self.secreto = self._empleado(nombre="Secreto De Mapogo", dni="31000111", sucursal=self.vivre)

    def test_el_alta_con_un_dni_de_otra_empresa_no_muestra_su_nombre(self):
        self._login(self.admin_armadi, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_create"),
            {"nombre": "Nuevo", "dni": "31.000.111", "empresa": self.armadi.pk, "sucursal": self.centro.pk},
        )
        self.assertContains(response, "Ya hay un empleado activo con ese DNI", status_code=400)
        self.assertNotContains(response, self.secreto.nombre, status_code=400)

    def test_reactivar_con_un_dni_de_otra_empresa_no_muestra_su_nombre(self):
        propio = self._empleado(nombre="Propio", dni="31999000")
        dar_de_baja_empleado(actor=self.admin, empleado=propio)
        Empleado.objects.filter(pk=propio.pk).update(dni="31000111")
        self._login(self.admin_armadi, [self.armadi])
        response = self.client.post(reverse("asistencia:empleado_reactivar", args=[propio.pk]), follow=True)
        self.assertContains(response, "Ya hay un empleado activo con ese DNI")
        self.assertNotContains(response, self.secreto.nombre)

    def test_la_carga_no_muestra_nombres_de_otra_empresa_y_un_dado_de_baja_ajeno_no_bloquea(self):
        se_fue = self._empleado(nombre="Se Fue De Mapogo", dni="32000222", sucursal=self.vivre)
        dar_de_baja_empleado(actor=self.admin, empleado=se_fue)
        self._login(self.admin_armadi, [self.armadi])
        response = self._subir(b"Nombre,DNI,Local\nUno,31000111,CENT-02\nVuelve,32000222,CENT-02\n")
        filas = self._estados(response)
        self.assertEqual(filas[2].estado, Estado.RECHAZADA)
        self.assertIn("en otra empresa", filas[2].motivo)
        self.assertEqual(filas[3].estado, Estado.LISTA, filas[3].motivo)
        self.assertNotContains(response, self.secreto.nombre)
        self.assertNotContains(response, se_fue.nombre)
        self._confirmar()
        self.assertTrue(Empleado.objects.filter(dni="32000222", activo=True, empresa=self.armadi).exists())

    def test_la_baja_funciona_aunque_el_local_haya_cambiado_de_empresa(self):
        empleado = self._empleado()
        Sucursal.objects.filter(pk=self.centro.pk).update(empresa=None)
        dar_de_baja_empleado(actor=self.admin, empleado=empleado)
        empleado.refresh_from_db()
        self.assertFalse(empleado.activo)
        reactivar_empleado(actor=self.admin, empleado=empleado)
        empleado.refresh_from_db()
        self.assertTrue(empleado.activo)

    def test_el_admin_no_deja_tocar_activo_a_mano(self):
        from .admin import EmpleadoAdmin

        self.assertIn("activo", EmpleadoAdmin.readonly_fields)

    def test_un_excel_con_columnas_lejanas_se_rechaza_sin_armar_la_fila(self):
        # Sin el tope, esta celda sola le pide a leer_xlsx una lista de 12 millones.
        self._login(self.admin, [self.armadi])
        contenido = _xlsx([["Nombre", "DNI", "Local"], ["X", "30123456", "CENT-02"]], {'r="A2"': 'r="AAAAAA2"'})
        response = self._subir(contenido, nombre="padron.xlsx")
        self.assertContains(response, "columnas muy alejadas", status_code=400)

    def test_un_excel_chico_que_descomprimido_es_enorme_se_rechaza_sin_leerlo(self):
        # ElementTree ocupa ~25 veces el XML: 2,1 MB de celdas vacias comprimen a
        # pocos KB (pasan el tope del form) y sin este control pedirian ~50 MB.
        self._login(self.admin, [self.armadi])
        relleno = "<row><c/><c/><c/><c/><c/><c/><c/><c/><c/><c/></row>" * 42000
        contenido = _xlsx(
            [["Nombre", "DNI", "Local"], ["Juan", "33111222", "CENT-02"]],
            {"</sheetData>": f"{relleno}</sheetData>"},
            comprimir=True,
        )
        self.assertLess(len(contenido), 100 * 1024)
        with mock.patch("asistencia.importacion.leer_xlsx") as leer:
            response = self._subir(contenido, nombre="padron.xlsx")
        leer.assert_not_called()
        self.assertContains(response, "demasiado grande", status_code=400)

    def test_el_tope_de_columnas_no_se_saltea_moviendo_la_hoja_ni_con_comillas_simples(self):
        self._login(self.admin, [self.armadi])
        filas = [["Nombre", "DNI", "Local"], ["X", "30123456", "CENT-02"]]
        variantes = {
            "hoja en otra ruta": _xlsx(filas, {'r="A2"': 'r="ZZZ2"'}, ruta_hoja="datos/hoja1.xml"),
            "comillas simples": _xlsx(filas, {'r="A2"': "r='ZZZ2'"}),
            "extension en mayuscula": _xlsx(filas, {'r="A2"': 'r="ZZZ2"'}, ruta_hoja="worksheets/Sheet1.XML"),
        }
        for nombre, contenido in variantes.items():
            with self.subTest(nombre):
                response = self._subir(contenido, nombre="padron.xlsx")
                self.assertContains(response, "columnas muy alejadas", status_code=400)

    def test_un_excel_normal_con_formato_no_se_rechaza_por_error(self):
        self._login(self.admin, [self.armadi])
        filas = [["Nombre", "DNI", "Local"], ["Juan", "33111222", "CENT-02"]]
        vacias = "".join(f'<row r="{n}"/>' for n in range(10, 2200))
        variantes = {
            "celda vacia con formato lejos": _xlsx(filas, {'<c r="A1"': '<c r="BZ1" s="1"/><c r="A1"'}),
            "filas vacias de formato": _xlsx(filas, {"</sheetData>": f"{vacias}</sheetData>"}),
            "segunda hoja grande": _xlsx(filas, otras_hojas={"Historial": [["x"]] * 2100}),
        }
        for nombre, contenido in variantes.items():
            with self.subTest(nombre):
                response = self._subir(contenido, nombre="padron.xlsx")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._estados(response)[2].estado, Estado.LISTA)

    def test_cualquier_falla_al_leer_el_excel_se_informa_sin_500(self):
        self._login(self.admin, [self.armadi])
        contenido = _xlsx([["Nombre", "DNI", "Local"], ["Juan", "30123456", "CENT-02"]])
        errores = (zlib.error("invalid code lengths set"), OverflowError("date value out of range"), RuntimeError("password required"))
        for error in errores:
            with self.subTest(error=type(error).__name__), mock.patch("asistencia.importacion.leer_xlsx", side_effect=error):
                response = self._subir(contenido, nombre="padron.xlsx")
                self.assertContains(response, "No se pudo leer el Excel", status_code=400)

    def test_un_dni_con_formato_de_fecha_se_rechaza(self):
        self._login(self.admin, [self.armadi])
        contenido = _xlsx([["Nombre", "DNI", "Local"], ["Abuela", date(1903, 5, 1), "CENT-02"]])
        response = self._subir(contenido, nombre="padron.xlsx")
        fila = self._estados(response)[2]
        self.assertEqual(fila.estado, Estado.RECHAZADA)
        self.assertIn("formato de fecha", fila.motivo)

    def test_un_csv_con_una_comilla_sin_cerrar_se_informa(self):
        self._login(self.admin, [self.armadi])
        contenido = 'Nombre,DNI,Local\n"Juan,30123456,CENT-02\nAna,28999888,TERM-01\n' + "x" * 140000
        response = self._subir(contenido.encode("utf-8"))
        self.assertContains(response, "revisá las comillas", status_code=400)
        self.assertNotIn(SESION_LISTADO, self.client.session)

    def test_apellido_y_nombre_en_columnas_separadas(self):
        self._login(self.admin, [self.armadi])
        self._subir("Apellido;Nombre;DNI;Local\nPerez;Juan;33111222;CENT-02\n".encode("utf-8"))
        self._confirmar()
        self.assertEqual(Empleado.objects.get(dni="33111222").nombre, "Juan Perez")

    def test_el_titulo_con_n_ordinal_se_reconoce(self):
        self._login(self.admin, [self.armadi])
        response = self._subir("Nombre;Nº documento;Local\nJuan;33111222;CENT-02\n".encode("utf-8"))
        self.assertEqual(self._estados(response)[2].estado, Estado.LISTA)

    def test_los_caracteres_de_control_se_limpian(self):
        self._login(self.admin, [self.armadi])
        self._subir("Nombre,DNI,Local\nJuan\x00Perez,33111222,CENT-02\n".encode("utf-8"))
        self._confirmar()
        self.assertEqual(Empleado.objects.get(dni="33111222").nombre, "Juan Perez")

    def test_un_nombre_demasiado_largo_se_rechaza_ya_en_la_revision(self):
        self._login(self.admin, [self.armadi])
        response = self._subir(f"Nombre,DNI,Local\n{'A' * 170},33111222,CENT-02\n".encode("utf-8"))
        fila = self._estados(response)[2]
        self.assertEqual(fila.estado, Estado.RECHAZADA)
        self.assertIn("más de 160 caracteres", fila.motivo)

    def test_confirmar_una_revision_vieja_no_carga_la_nueva_ni_la_descarta(self):
        self._login(self.admin, [self.armadi])
        self._subir(b"Nombre,DNI,Local\nPrimero,33111222,CENT-02\n")
        token_viejo = self.client.session[SESION_LISTADO]["token"]
        self._subir(b"Nombre,DNI,Local\nSegundo,33222333,CENT-02\n")
        # follow: el navegador sigue la redireccion, y esa pagina no tiene que
        # borrar la revision vigente de la otra pestana.
        url = reverse("asistencia:empleado_importar_confirmar")
        response = self.client.post(url, {"token": token_viejo}, follow=True)
        self.assertContains(response, "ya no es el último")
        self.assertEqual(Empleado.objects.filter(dni__in=["33111222", "33222333"]).count(), 0)
        self._confirmar()
        self.assertTrue(Empleado.objects.filter(dni="33222333").exists())
        self.assertFalse(Empleado.objects.filter(dni="33111222").exists())

    def test_abrir_la_carga_en_otra_pestana_no_borra_la_revision(self):
        self._login(self.admin, [self.armadi])
        self._subir(b"Nombre,DNI,Local\nJuan,33111222,CENT-02\n")
        self.client.get(reverse("asistencia:empleado_importar"))
        self._confirmar()
        self.assertTrue(Empleado.objects.filter(dni="33111222").exists())

    def test_un_token_con_caracteres_raros_no_da_500(self):
        self._login(self.admin, [self.armadi])
        self._subir(b"Nombre,DNI,Local\nJuan,33111222,CENT-02\n")
        response = self._confirmar(token="ñandú")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Empleado.objects.filter(dni="33111222").exists())
        self.assertIn(SESION_LISTADO, self.client.session)

    def test_un_dni_con_digitos_unicode_no_burla_la_unicidad(self):
        ancho_completo = self._empleado(nombre="Ancho Completo", dni="３３１１１２２２")
        self.assertEqual(ancho_completo.dni, "33111222")
        with self.assertRaises(ValidationError):
            self._empleado(nombre="Mismo DNI", dni="33111222")
        with self.assertRaises(ValidationError):
            self._empleado(nombre="Arabigo", dni="٣٣١١١٢٢٣")

    def test_un_local_que_paso_a_otra_empresa_no_se_muestra_ni_se_ofrece(self):
        empleado = self._empleado(nombre="Del Centro", dni="33111222")
        Sucursal.objects.filter(pk=self.centro.pk).update(empresa=self.ajena, nombre="Local Secreto Ajeno")
        self._login(self.admin_armadi, [self.armadi])
        listado = self.client.get(reverse("asistencia:empleado_list"))
        self.assertContains(listado, "local de otra empresa")
        self.assertNotContains(listado, "Local Secreto Ajeno")
        form = self.client.get(reverse("asistencia:empleado_update", args=[empleado.pk])).context["form"]
        self.assertNotIn(self.centro.pk, set(form.fields["sucursal"].queryset.values_list("pk", flat=True)))
