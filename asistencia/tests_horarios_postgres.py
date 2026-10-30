"""EP-14 D18: dos guardados de la grilla a la vez, contra PostgreSQL real.

En SQLite (los tests de CI) no hay pedidos en paralelo y estos tests se saltean.
Para correrlos: DATABASE_URL=postgres://... python manage.py test asistencia.tests_horarios_postgres

Riesgos que cubre:
- un doble click en "Guardar la semana" o en "Copiar la semana anterior" sobre
  casillas nuevas termina en 500 (los dos INSERT chocan en el unico por dia);
- el historial queda duplicado o un horario se pierde;
- reactivar a alguien (que borra los horarios de su baja) mientras el encargado
  guarda la grilla de su local se traba ("deadlock detected").
"""

from datetime import time, timedelta
from threading import Barrier, Thread
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from cashops.models import Empresa, Sucursal
from users.models import PermissionModule, Role, User, UserPermission

from .horarios import lunes_de
from .models import Empleado, Horario, HorarioCambio
from .services import crear_empleado, dar_de_baja_empleado, reactivar_empleado


@skipUnless(connection.vendor == "postgresql", "Solo PostgreSQL atiende pedidos en paralelo de verdad.")
class DobleEnvioEnPostgresTests(TransactionTestCase):
    def setUp(self):
        self.armadi = Empresa.objects.create(nombre="ARMADI")
        self.centro = Sucursal.objects.create(empresa=self.armadi, nombre="Centro", codigo="CENT-02", razon_social="Armadi SRL")
        admin_role = Role.objects.create(code="ADMIN", name="Administrador")
        rol = Role.objects.create(code="ENCARGADO", name="Encargado")
        self.admin = User.objects.create_user(username="admin-pg", password="test", role=admin_role)
        self.admin.empresas_permitidas.set([self.armadi])
        self.encargado = User.objects.create_user(username="enc-pg", password="test", role=rol, sucursal_base=self.centro)
        self.encargado.empresas_permitidas.set([self.armadi])
        UserPermission.objects.create(
            user=self.encargado, module=PermissionModule.ASISTENCIA_HORARIOS, can_read=True, can_write=True
        )
        self.empleados = [
            crear_empleado(actor=self.admin, nombre=nombre, dni=dni, empresa=self.armadi, sucursal=self.centro)
            for nombre, dni in (("Juan Perez", "30123456"), ("Ana Gomez", "30999888"))
        ]
        self.url = reverse("asistencia:horarios")
        self.proxima = lunes_de(timezone.localdate()) + timedelta(days=7)

    def _cliente(self, user):
        cliente = Client(raise_request_exception=False)
        cliente.force_login(user)
        session = cliente.session
        session["empresa_ids"] = [self.armadi.pk]
        session.save()
        return cliente

    def _semana(self, lunes, texto):
        """Una semana recien abierta (vacia) con todas las casillas en `texto`."""
        datos = {"local": self.centro.pk, "semana": lunes.isoformat()}
        for empleado in self.empleados:
            for n in range(7):
                datos[f"c-{empleado.pk}-{lunes + timedelta(days=n):%Y%m%d}"] = texto
            datos[f"o-{empleado.pk}"] = "|".join([""] * 7)
        return datos

    def _en_paralelo(self, envios):
        barrera = Barrier(len(envios))
        estados = []

        def correr(cliente, datos):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                estados.append(cliente.post(self.url, datos).status_code)
            finally:
                connection.close()

        hilos = [Thread(target=correr, args=envio) for envio in envios]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        return estados

    def _a_la_vez(self, *tareas):
        """Corre las tareas en hilos que arrancan juntos. Devuelve lo que devolvio
        cada una, o la excepcion que tiro."""
        barrera = Barrier(len(tareas))
        resultados = [None] * len(tareas)

        def correr(indice, tarea):
            close_old_connections()
            try:
                barrera.wait(timeout=10)
                resultados[indice] = tarea()
            except Exception as error:  # el test compara el resultado
                resultados[indice] = error
            finally:
                connection.close()

        hilos = [Thread(target=correr, args=(indice, tarea)) for indice, tarea in enumerate(tareas)]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join()
        return resultados

    def test_reactivar_mientras_se_guarda_la_grilla_no_se_traba(self):
        juan, ana = self.empleados
        hoy = timezone.localdate()
        encargado = self._cliente(self.encargado)
        for vuelta in range(4):
            lunes = self.proxima + timedelta(days=7 * vuelta)
            # Juan se fue hace 5 dias y tenia horarios cargados para los dias de la baja.
            for dias in (1, 2, 3):
                Horario.objects.create(empleado=juan, fecha=hoy - timedelta(days=dias), entrada_1=time(8))
            dar_de_baja_empleado(actor=self.admin, empleado=juan)
            Empleado.objects.filter(pk=juan.pk).update(dado_de_baja_en=timezone.now() - timedelta(days=5))
            datos = {"local": self.centro.pk, "semana": lunes.isoformat(), f"o-{ana.pk}": "|".join([""] * 7)}
            for n in range(7):
                datos[f"c-{ana.pk}-{lunes + timedelta(days=n):%Y%m%d}"] = "8"
            resultados = self._a_la_vez(
                lambda: encargado.post(self.url, datos).status_code,
                lambda: reactivar_empleado(actor=self.admin, empleado=Empleado.objects.get(pk=juan.pk)).activo,
            )
            with self.subTest(vuelta=vuelta):
                self.assertEqual(resultados, [302, True])
                self.assertFalse(Horario.objects.filter(empleado=juan, fecha__lt=hoy).exists())
                self.assertEqual(
                    Horario.objects.filter(empleado=ana, fecha__gte=lunes, fecha__lt=lunes + timedelta(days=7)).count(), 7
                )

    def test_doble_guardar_no_da_error_ni_duplica(self):
        for vuelta in range(6):
            lunes = self.proxima + timedelta(days=7 * vuelta)
            datos = self._semana(lunes, "8")
            estados = self._en_paralelo([(self._cliente(self.encargado), datos), (self._cliente(self.encargado), datos)])
            # El primero guarda; el segundo espera el candado y ve que ya esta como lo quiere:
            # no hay cambios, ni error, ni aviso de que otro lo cambio.
            with self.subTest(vuelta=vuelta):
                self.assertEqual(estados, [302, 302])
                self.assertEqual(Horario.objects.filter(fecha__gte=lunes, fecha__lt=lunes + timedelta(days=7)).count(), 14)
                self.assertEqual(
                    HorarioCambio.objects.filter(fecha__gte=lunes, fecha__lt=lunes + timedelta(days=7)).count(), 14
                )

    def test_doble_copiar_no_da_error_ni_duplica(self):
        for vuelta in range(4):
            semana = self.proxima + timedelta(days=14 * vuelta)
            anterior = semana - timedelta(days=7)
            self.assertEqual(self._cliente(self.admin).post(self.url, self._semana(anterior, "9")).status_code, 302)
            datos = {"local": self.centro.pk, "semana": semana.isoformat(), "accion": "copiar"}
            estados = self._en_paralelo([(self._cliente(self.encargado), datos), (self._cliente(self.encargado), datos)])
            with self.subTest(vuelta=vuelta):
                self.assertEqual(estados, [302, 302])
                self.assertEqual(Horario.objects.filter(fecha__gte=semana, fecha__lt=semana + timedelta(days=7)).count(), 14)
                self.assertEqual(
                    HorarioCambio.objects.filter(fecha__gte=semana, fecha__lt=semana + timedelta(days=7)).count(), 14
                )
