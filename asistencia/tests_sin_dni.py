"""DNI opcional (26/10/2026): el listado del cliente llego sin DNI.

Riesgos que cubre:
- que un empleado sin DNI no se pueda cargar (a mano o por listado);
- que, sin DNI, la misma persona quede cargada dos veces (doble alta, el mismo
  listado subido dos veces, una reactivacion);
- que dos empleados sin DNI choquen en la restriccion de la base;
- que un DNI cargado deje de controlarse.
"""

from django.core.exceptions import ValidationError
from django.urls import reverse

from .importacion import Estado
from .models import Empleado
from .services import actualizar_empleado, crear_empleado, dar_de_baja_empleado, reactivar_empleado
from .tests_padron import PadronTestCase, _CargaMixin


class EmpleadoSinDniTests(PadronTestCase):
    def test_se_carga_sin_dni(self):
        empleado = self._empleado(nombre="Ana Sin Dni", dni="")
        self.assertEqual(empleado.dni, "")
        self.assertEqual(str(empleado), "Ana Sin Dni")

    def test_dos_sin_dni_con_distinto_nombre_no_chocan_en_la_base(self):
        Empleado.objects.create(nombre="Uno", dni="", empresa=self.armadi, sucursal=self.centro)
        Empleado.objects.create(nombre="Dos", dni="", empresa=self.armadi, sucursal=self.centro)
        self.assertEqual(Empleado.objects.filter(dni="").count(), 2)

    def test_sin_dni_no_se_repite_el_nombre_en_la_empresa(self):
        self._empleado(nombre="Ana Lopez", dni="")
        with self.assertRaises(ValidationError) as ctx:
            self._empleado(nombre="ana  lopez", dni="")
        self.assertIn("nombre", ctx.exception.message_dict)
        self.assertIn("cargale el DNI", ctx.exception.message_dict["nombre"][0])

    def test_con_dni_un_homonimo_se_puede_cargar(self):
        self._empleado(nombre="Ana Lopez", dni="")
        self.assertEqual(self._empleado(nombre="Ana Lopez", dni="30111222").dni, "30111222")

    def test_el_mismo_nombre_en_otra_empresa_no_choca(self):
        self._empleado(nombre="Ana Lopez", dni="")
        self.assertEqual(self._empleado(nombre="Ana Lopez", dni="", sucursal=self.vivre).empresa, self.mapogo)

    def test_un_dni_cargado_se_sigue_controlando(self):
        self._empleado(nombre="Uno", dni="30111222")
        for dni in ("30.111.222", "12ab"):
            with self.subTest(dni=dni), self.assertRaises(ValidationError) as ctx:
                self._empleado(nombre="Otro", dni=dni)
            self.assertIn("dni", ctx.exception.message_dict)

    def test_editar_sin_tocar_el_nombre_no_choca_consigo_mismo(self):
        empleado = self._empleado(nombre="Ana Lopez", dni="")
        actualizado = actualizar_empleado(
            actor=self.admin, empleado=empleado, nombre="Ana Lopez", dni="30111222",
            empresa=self.armadi, sucursal=self.centro,
        )
        self.assertEqual(actualizado.dni, "30111222")

    def test_no_reactiva_sin_dni_si_ya_hay_un_activo_con_ese_nombre(self):
        viejo = self._empleado(nombre="Ana Lopez", dni="")
        dar_de_baja_empleado(actor=self.admin, empleado=viejo)
        self._empleado(nombre="Ana Lopez", dni="")
        with self.assertRaises(ValidationError):
            reactivar_empleado(actor=self.admin, empleado=viejo)

    def test_alta_por_formulario_sin_dni(self):
        self._login(self.admin, [self.armadi])
        response = self.client.post(
            reverse("asistencia:empleado_create"),
            {"nombre": "Por Formulario", "dni": "", "empresa": self.armadi.pk, "sucursal": self.centro.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Empleado.objects.get(nombre="Por Formulario").dni, "")
        self.assertContains(self.client.get(reverse("asistencia:empleado_list")), "DNI sin cargar")


class CargaSinDniTests(_CargaMixin, PadronTestCase):
    def test_un_listado_sin_columna_dni_se_carga(self):
        self._login(self.admin, [self.armadi])
        response = self._subir("Apellido,Nombre,Local\nLopez,Ana,CENT-02\nPerez,Luis,TERM-01\n".encode("utf-8"))
        filas = self._estados(response)
        self.assertEqual({f.estado for f in filas.values()}, {Estado.LISTA})
        self._confirmar()
        self.assertEqual(set(Empleado.objects.filter(dni="").values_list("nombre", flat=True)), {"Ana Lopez", "Luis Perez"})

    def test_subir_dos_veces_el_mismo_listado_sin_dni_no_duplica(self):
        self._login(self.admin, [self.armadi])
        contenido = "Nombre,DNI,Local\nAna Lopez,,CENT-02\nLuis Perez,,TERM-01\n".encode("utf-8")
        self._subir(contenido)
        self._confirmar()
        filas = self._estados(self._subir(contenido))
        for fila in filas.values():
            self.assertEqual(fila.estado, Estado.RECHAZADA)
            self.assertIn("Ya hay un empleado activo con ese nombre", fila.motivo)
        self.assertEqual(Empleado.objects.count(), 2)

    def test_sin_dni_un_nombre_repetido_en_el_archivo_se_rechaza(self):
        self._login(self.admin, [self.armadi])
        contenido = (
            "Nombre,DNI,Local\n"
            "Ana Lopez,,CENT-02\n"        # 2: bien
            "ANA LOPEZ,,TERM-01\n"        # 3: mismo nombre, misma empresa, sin DNI
            "Ana Lopez,30111222,CENT-02\n"  # 4: con DNI, es otra persona
            "Ana Lopez,,VIV-01\n"         # 5: otra empresa, fuera del alcance
            "Pedro,-,CENT-02\n"           # 6: un guion no es un DNI vacio
        )
        filas = self._estados(self._subir(contenido.encode("utf-8")))
        self.assertEqual(filas[2].estado, Estado.LISTA)
        self.assertEqual(filas[3].estado, Estado.RECHAZADA)
        self.assertIn("Nombre repetido: ya está en la fila 2", filas[3].motivo)
        self.assertEqual(filas[4].estado, Estado.LISTA)
        self.assertEqual(filas[5].estado, Estado.RECHAZADA)
        self.assertEqual(filas[6].estado, Estado.RECHAZADA)
        self.assertIn("DNI inválido", filas[6].motivo)

    def test_crear_empleado_por_servicio_sin_dni(self):
        empleado = crear_empleado(actor=self.admin, nombre="Servicio", dni="", empresa=self.armadi, sucursal=self.centro)
        self.assertEqual(empleado.dni, "")
