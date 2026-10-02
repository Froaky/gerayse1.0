"""Lectura de planillas .xlsx sin dependencias.

Las planillas de tesoreria llegan en Excel: exportadas de Google, o convertidas
del PDF del banco. El proyecto no tiene openpyxl y para la importacion alcanza
con los valores, que un .xlsx guarda como XML dentro de un zip: textos
compartidos, numeros y el estilo que dice si un numero es una fecha.
"""

from __future__ import annotations

import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

_NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
_M = "{%s}" % _NS["m"]
# Formatos de numero predefinidos de Excel que son fechas.
_FORMATOS_FECHA = {14, 15, 16, 17, 22, 27, 30, 36, 45, 46, 47, 50, 57}
_ORIGEN_EXCEL = date(1899, 12, 30)


def _indice_columna(referencia: str) -> int:
    letras = re.match(r"[A-Z]+", referencia).group(0)
    indice = 0
    for letra in letras:
        indice = indice * 26 + (ord(letra) - 64)
    return indice - 1


def _estilos_fecha(zf: zipfile.ZipFile) -> set[int]:
    if "xl/styles.xml" not in zf.namelist():
        return set()
    raiz = ET.fromstring(zf.read("xl/styles.xml"))
    propios = set()
    formatos = raiz.find("m:numFmts", _NS)
    if formatos is not None:
        for formato in formatos.findall("m:numFmt", _NS):
            codigo = formato.get("formatCode", "").lower()
            sin_literales = re.sub(r'"[^"]*"|\[[^\]]*\]', "", codigo)
            if re.search(r"[dmy]", sin_literales) and not re.search(r"[#0]", sin_literales):
                propios.add(int(formato.get("numFmtId")))
    estilos = set()
    xfs = raiz.find("m:cellXfs", _NS)
    if xfs is not None:
        for indice, xf in enumerate(xfs.findall("m:xf", _NS)):
            id_formato = int(xf.get("numFmtId", "0"))
            if id_formato in _FORMATOS_FECHA or id_formato in propios:
                estilos.add(indice)
    return estilos


def _textos_compartidos(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    raiz = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(_M + "t")) for si in raiz.findall("m:si", _NS)]


def _valor(celda, compartidos, estilos_fecha):
    tipo = celda.get("t")
    v = celda.find("m:v", _NS)
    if tipo == "s":
        return compartidos[int(v.text)].strip() if v is not None else ""
    if tipo == "inlineStr":
        return "".join(t.text or "" for t in celda.iter(_M + "t")).strip()
    if v is None or v.text is None:
        return ""
    if tipo in ("str", "e"):
        return v.text.strip()
    if tipo == "b":
        return v.text == "1"
    try:
        numero = Decimal(v.text)
    except InvalidOperation:
        return v.text.strip()
    if int(celda.get("s", "0")) in estilos_fecha:
        return _ORIGEN_EXCEL + timedelta(days=int(numero))
    return numero


def leer_xlsx(ruta) -> list[tuple[str, list[tuple[int, list]]]]:
    """Devuelve [(nombre_de_hoja, [(numero_de_fila, [valores])])] en el orden del
    libro. Textos como str, numeros como Decimal (exactos, sin pasar por float),
    celdas con formato de fecha como date y vacias como "". Solo vienen las
    filas que tienen algun valor."""
    with zipfile.ZipFile(ruta) as zf:
        compartidos = _textos_compartidos(zf)
        estilos_fecha = _estilos_fecha(zf)
        libro = ET.fromstring(zf.read("xl/workbook.xml"))
        relaciones = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        destino = {r.get("Id"): r.get("Target") for r in relaciones.findall("rel:Relationship", _NS)}
        hojas = []
        for hoja in libro.find("m:sheets", _NS).findall("m:sheet", _NS):
            ruta_hoja = destino[hoja.get("{%s}id" % _NS["r"])].lstrip("/")
            if not ruta_hoja.startswith("xl/"):
                ruta_hoja = "xl/" + ruta_hoja
            raiz = ET.fromstring(zf.read(ruta_hoja))
            filas = []
            for fila in raiz.iter(_M + "row"):
                valores = {}
                for celda in fila.findall("m:c", _NS):
                    valor = _valor(celda, compartidos, estilos_fecha)
                    if valor != "":
                        valores[_indice_columna(celda.get("r"))] = valor
                if valores:
                    ancho = max(valores) + 1
                    filas.append((int(fila.get("r")), [valores.get(i, "") for i in range(ancho)]))
            hojas.append((hoja.get("name"), filas))
    return hojas
