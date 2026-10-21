"""Carga masiva del padron desde un CSV o un Excel (EP-14 US-14.2).

Dos pasos: primero se revisa sin guardar y se informa fila por fila; despues se
confirma y se vuelve a validar todo antes de crear (entre un paso y el otro
pudo cambiar el padron o el alcance del usuario). Cada empleado se crea en su
propia transaccion: una fila mala no frena a las demas ni deja un alta a medias.

La empresa sale del local. Si el listado trae una columna empresa, tiene que
coincidir con la del local; si no, la fila se rechaza en vez de adivinar.

El archivo lo sube un usuario: todo lo que no se pueda leer termina en
ListadoIlegible (un mensaje en pantalla), nunca en un 500.
"""

import csv
import io
import logging
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, transaction

from treasury.importacion_texto import normalizar, texto_de_celda
from treasury.lectura_xlsx import LibroDemasiadoGrande, leer_xlsx

from .models import DNI_PATRON, Empleado, normalizar_dni
from .services import crear_empleado

logger = logging.getLogger(__name__)

MAXIMO_FILAS = 2000
FILAS_PARA_BUSCAR_TITULOS = 20
# Topes para no leer un .xlsx armado para tumbar el servidor. leer_xlsx arma el
# arbol XML de cada parte entera antes de mirar nada, y ElementTree ocupa unas
# 25 veces lo que pesa el XML: con 10 MB un archivo de 26 KB llegaba a ~300 MB
# de RAM. Con 2 MB el pico queda en ~50 MB, y un padron real de 85 empleados no
# llega a 100 KB descomprimido. Ademas leer_xlsx arma cada fila hasta la ultima
# columna con dato: el tope de columnas lo aplica mientras lee, sobre la hoja
# real y solo con celdas con dato.
MAXIMO_DESCOMPRIMIDO = 2 * 1024 * 1024
MAXIMO_COLUMNAS = 50
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Nombres de columna aceptados, ya normalizados (sin acentos, mayusculas, sin
# puntuacion): "D.N.I." llega como "D N I", "N° documento" como "N DOCUMENTO" y
# "Nº documento" como "NO DOCUMENTO" (el ordinal se descompone en una "o").
COLUMNAS = {
    "nombre": {
        "NOMBRE", "NOMBRES", "NOMBRE Y APELLIDO", "NOMBRES Y APELLIDOS", "APELLIDO Y NOMBRE",
        "APELLIDO Y NOMBRES", "APELLIDOS Y NOMBRES", "APELLIDO NOMBRE", "APELLIDOS NOMBRES",
        "NOMBRE COMPLETO", "EMPLEADO",
    },
    "apellido": {"APELLIDO", "APELLIDOS"},
    "dni": {
        "DNI", "D N I", "DOCUMENTO", "NRO DOCUMENTO", "NRO DE DOCUMENTO", "N DOCUMENTO", "N DE DOCUMENTO",
        "NO DOCUMENTO", "NO DE DOCUMENTO", "NUMERO DE DOCUMENTO", "NUMERO DOCUMENTO", "NRO DNI", "N DNI", "NO DNI",
    },
    "local": {"LOCAL", "SUCURSAL", "LOCAL HABITUAL"},
    "empresa": {"EMPRESA"},
}
REQUERIDAS = ("nombre", "dni", "local")
NOMBRE_MAXIMO = Empleado._meta.get_field("nombre").max_length


class ListadoIlegible(Exception):
    """El archivo no se pudo leer o no tiene la fila de titulos esperada."""


class Estado:
    LISTA = "lista"
    CARGADA = "cargada"
    RECHAZADA = "rechazada"


@dataclass
class FilaListado:
    fila: int
    nombre: str
    dni: str
    local: str
    empresa: str = ""
    error_lectura: str = ""
    estado: str = ""
    motivo: str = ""
    local_resuelto: str = ""

    def datos(self) -> dict:
        """Lo que se guarda en sesion entre revisar y confirmar: solo lo leido."""
        leido = ("fila", "nombre", "dni", "local", "empresa", "error_lectura")
        return {k: v for k, v in asdict(self).items() if k in leido}


def leer_listado(archivo) -> list[FilaListado]:
    if Path(archivo.name).suffix.lower() == ".xlsx":
        filas = _filas_xlsx(archivo)
    else:
        filas = _filas_csv(archivo)
    indice, columnas = _buscar_titulos(filas)
    resultado = []
    for numero, valores in filas[indice + 1:]:
        celdas = {}
        errores = []
        for columna, posicion in columnas.items():
            valor = _celda(valores, posicion)
            if isinstance(valor, date):
                # leer_xlsx convierte en fecha todo numero con formato de fecha:
                # un DNI asi se leeria como otro numero sin que nadie lo note.
                errores.append(f"La columna {columna} tiene formato de fecha en el Excel.")
                valor = ""
            celdas[columna] = _CONTROL.sub(" ", texto_de_celda(valor)).strip()
        if not any(celdas.values()) and not errores:
            continue
        nombre = celdas["nombre"]
        if celdas.get("apellido"):
            nombre = f"{nombre} {celdas['apellido']}".strip()
        resultado.append(
            FilaListado(
                fila=numero,
                nombre=nombre,
                dni=celdas["dni"],
                local=celdas["local"],
                empresa=celdas.get("empresa", ""),
                error_lectura=" ".join(errores),
            )
        )
        if len(resultado) > MAXIMO_FILAS:
            raise ListadoIlegible(f"El listado tiene más de {MAXIMO_FILAS} filas.")
    if not resultado:
        raise ListadoIlegible("El listado no tiene empleados debajo de los títulos.")
    return resultado


def _revisar_descomprimido(archivo) -> None:
    with zipfile.ZipFile(archivo) as libro:
        if sum(miembro.file_size for miembro in libro.infolist()) > MAXIMO_DESCOMPRIMIDO:
            raise ListadoIlegible("El Excel es demasiado grande. Guardá solo la hoja del padrón o subilo como CSV.")
    archivo.seek(0)


def _filas_xlsx(archivo) -> list[tuple[int, list]]:
    try:
        _revisar_descomprimido(archivo)
        hojas = leer_xlsx(
            archivo,
            max_hojas=1,
            max_columnas=MAXIMO_COLUMNAS,
            max_filas=MAXIMO_FILAS + FILAS_PARA_BUSCAR_TITULOS,
        )
    except LibroDemasiadoGrande as error:
        if error.motivo == "columnas":
            raise ListadoIlegible(
                "El Excel tiene datos en columnas muy alejadas. Dejá solo las columnas del padrón."
            ) from error
        raise ListadoIlegible(f"El listado tiene más de {MAXIMO_FILAS} filas.") from error
    except ListadoIlegible:
        raise
    except Exception as error:  # noqa: BLE001 - archivo subido: cualquier falla de lectura se informa
        logger.warning("No se pudo leer un .xlsx de padron: %r", error)
        raise ListadoIlegible("No se pudo leer el Excel. Guardalo de nuevo como .xlsx o como CSV.") from error
    if not hojas:
        raise ListadoIlegible("El Excel no tiene hojas.")
    return hojas[0][1]


def _filas_csv(archivo) -> list[tuple[int, list]]:
    crudo = archivo.read()
    try:
        texto = crudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Excel en Windows guarda los CSV en ANSI.
        texto = crudo.decode("cp1252", errors="replace")
    primeras = "\n".join(texto.splitlines()[:FILAS_PARA_BUSCAR_TITULOS])
    delimitador = ";" if primeras.count(";") >= primeras.count(",") else ","
    # strict: una comilla sin cerrar no se traga en silencio las filas que siguen.
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=delimitador, strict=True)
    try:
        return [(numero, fila) for numero, fila in enumerate(lector, start=1) if any(c.strip() for c in fila)]
    except csv.Error as error:
        raise ListadoIlegible(
            f"El CSV está mal formado cerca de la línea {lector.line_num}: revisá las comillas."
        ) from error


def _buscar_titulos(filas) -> tuple[int, dict]:
    for indice, (_numero, valores) in enumerate(filas[:FILAS_PARA_BUSCAR_TITULOS]):
        nombres = [normalizar(v) if isinstance(v, str) else "" for v in valores]
        columnas = {}
        for columna, aceptados in COLUMNAS.items():
            for posicion, nombre in enumerate(nombres):
                if nombre in aceptados:
                    columnas[columna] = posicion
                    break
        if all(columna in columnas for columna in REQUERIDAS):
            return indice, columnas
    raise ListadoIlegible("No encuentro la fila de títulos con las columnas nombre, DNI y local.")


def _celda(valores, indice):
    return valores[indice] if indice < len(valores) else ""


def _mensaje(error) -> str:
    if isinstance(error, IntegrityError):
        # La unica restriccion de la tabla: alguien cargo el mismo DNI en paralelo.
        return "Ya hay un empleado activo con ese DNI."
    if isinstance(error, DatabaseError):
        return "No se pudo guardar la fila."
    if isinstance(error, ValidationError):
        if hasattr(error, "message_dict"):
            return " ".join(m for mensajes in error.message_dict.values() for m in mensajes)
        return " ".join(error.messages)
    return str(error) or "No se pudo cargar el empleado."


def procesar_listado(filas: list[FilaListado], *, actor, sucursales, guardar: bool) -> list[FilaListado]:
    """Valida cada fila y, si `guardar`, crea los empleados validos. `sucursales`
    es el alcance del usuario: un local fuera de el no existe para la carga, y
    de un empleado de otra empresa nunca se muestra el nombre."""
    por_codigo = {}
    por_nombre = {}
    empresas_visibles = set()
    for sucursal in sucursales.select_related("empresa"):
        por_codigo[normalizar(sucursal.codigo)] = sucursal
        por_nombre[normalizar(sucursal.nombre)] = sucursal
        empresas_visibles.add(sucursal.empresa_id)

    dnis = {normalizar_dni(f.dni) for f in filas}
    activos = {}
    dados_de_baja = {}
    for empleado in Empleado.objects.filter(dni__in=dnis):
        if empleado.activo:
            activos[empleado.dni] = empleado
        elif empleado.empresa_id in empresas_visibles:
            dados_de_baja.setdefault(empleado.dni, empleado)

    vistos = {}
    for fila in filas:
        motivos = [fila.error_lectura] if fila.error_lectura else []
        nombre = " ".join(_CONTROL.sub(" ", fila.nombre).split())
        dni = normalizar_dni(fila.dni)
        if not nombre:
            motivos.append("Falta el nombre.")
        elif len(nombre) > NOMBRE_MAXIMO:
            motivos.append(f"El nombre tiene más de {NOMBRE_MAXIMO} caracteres.")
        if not DNI_PATRON.fullmatch(dni):
            motivos.append(f"DNI inválido: '{fila.dni}'." if fila.dni else "Falta el DNI.")
        sucursal = None
        if not fila.local.strip():
            motivos.append("Falta el local.")
        else:
            clave = normalizar(fila.local)
            sucursal = por_codigo.get(clave) or por_nombre.get(clave)
            if sucursal is None:
                motivos.append(f"No hay un local '{fila.local}' en tus empresas.")
            else:
                fila.local_resuelto = f"{sucursal.codigo} - {sucursal.nombre} ({sucursal.empresa.nombre})"
        if sucursal is not None and fila.empresa.strip() and normalizar(fila.empresa) != normalizar(sucursal.empresa.nombre):
            motivos.append(f"La empresa '{fila.empresa}' no coincide con la del local ({sucursal.empresa.nombre}).")
        if not motivos:
            activo = activos.get(dni)
            dado_de_baja = dados_de_baja.get(dni)
            if dni in vistos:
                motivos.append(f"DNI repetido: ya está en la fila {vistos[dni]}.")
            elif activo is not None and activo.empresa_id in empresas_visibles:
                motivos.append(f"Ya hay un empleado activo con ese DNI: {activo.nombre}.")
            elif activo is not None:
                motivos.append("Ya hay un empleado activo con ese DNI en otra empresa.")
            elif dado_de_baja is not None:
                # Un dado de baja de otra empresa no bloquea, igual que en el alta
                # a mano: el DNI solo es unico entre activos.
                motivos.append(
                    f"Hay un empleado dado de baja con ese DNI ({dado_de_baja.nombre}): reactivalo desde el padrón."
                )
        if motivos:
            fila.estado = Estado.RECHAZADA
            fila.motivo = " ".join(motivos)
            continue
        vistos[dni] = fila.fila
        if not guardar:
            fila.estado = Estado.LISTA
            continue
        try:
            with transaction.atomic():
                crear_empleado(actor=actor, nombre=nombre, dni=dni, empresa=sucursal.empresa, sucursal=sucursal)
        except (ValidationError, DatabaseError, PermissionDenied) as error:
            fila.estado = Estado.RECHAZADA
            fila.motivo = _mensaje(error)
        else:
            fila.estado = Estado.CARGADA
    return filas
