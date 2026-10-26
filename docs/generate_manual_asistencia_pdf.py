"""Genera el PDF de un manual de asistencia desde su Markdown (EP-14 US-14.9).

Entiende el Markdown que usan los manuales: titulos (#, ##), parrafos, listas
con guion o numeradas (con sangria de dos espacios por nivel y lineas de
continuacion), tablas de dos columnas con barras, **negrita** y `codigo`.

Uso: python docs/generate_manual_asistencia_pdf.py [origen.md [destino.pdf]]
Sin argumentos: docs/manual-asistencia.md -> docs/manual-asistencia.pdf. Sin
destino, el PDF queda al lado del Markdown con el mismo nombre.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BASE_DIR = Path(__file__).resolve().parent
SOURCE = BASE_DIR / "manual-asistencia.md"
TARGET = BASE_DIR / "manual-asistencia.pdf"

VERDE = HexColor("#17311f")
VERDE_MEDIO = HexColor("#2f5d3a")
VERDE_CLARO = HexColor("#e8f0ea")
GRIS = HexColor("#5b6660")
LINEA = HexColor("#c9d6cd")

ESTILOS = {
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=22, leading=27, textColor=white),
    "bajada": ParagraphStyle("bajada", fontName="Helvetica", fontSize=11, leading=15, textColor=white),
    "h1": ParagraphStyle(
        "h1", fontName="Helvetica-Bold", fontSize=15, leading=19, textColor=VERDE, spaceBefore=14, spaceAfter=6
    ),
    "cuerpo": ParagraphStyle("cuerpo", fontName="Helvetica", fontSize=10.5, leading=15, spaceAfter=6),
    "celda": ParagraphStyle("celda", fontName="Helvetica", fontSize=9.5, leading=13),
    "celda_titulo": ParagraphStyle("celda_titulo", fontName="Helvetica-Bold", fontSize=9.5, leading=13, textColor=white),
}


def _en_linea(texto: str) -> str:
    texto = texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Helvetica no tiene la flecha: se usa el signo que si tiene.
    texto = texto.replace("→", "›")
    texto = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texto)
    return re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', texto)


def _estilo_item(nivel: int) -> ParagraphStyle:
    sangria = 16 + nivel * 16
    return ParagraphStyle(
        f"item{nivel}", fontName="Helvetica", fontSize=10.5, leading=14.5, leftIndent=sangria,
        bulletIndent=sangria - 12, spaceAfter=3,
    )


def _tabla(filas: list[list[str]]) -> Table:
    datos = [[Paragraph(_en_linea(c), ESTILOS["celda_titulo"]) for c in filas[0]]]
    datos += [[Paragraph(_en_linea(c), ESTILOS["celda"]) for c in fila] for fila in filas[1:]]
    ancho = A4[0] - 4 * cm
    tabla = Table(datos, colWidths=[ancho * 0.42, ancho * 0.58], repeatRows=1)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), VERDE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, VERDE_CLARO]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINEA),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return tabla


def _portada(titulo: str, bajada: str) -> Table:
    # `bajada` ya viene armada (sale de un Paragraph del cuerpo).
    portada = Table(
        [[Paragraph(_en_linea(titulo), ESTILOS["titulo"])], [Paragraph(bajada, ESTILOS["bajada"])]],
        colWidths=[A4[0] - 4 * cm],
    )
    portada.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), VERDE),
                ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ("RIGHTPADDING", (0, 0), (-1, -1), 16),
                ("TOPPADDING", (0, 0), (-1, 0), 18),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 18),
            ]
        )
    )
    return portada


def armar(texto: str) -> list:
    story: list = []
    parrafo: list[str] = []
    items: list[tuple[int, str, str]] = []  # (nivel, viñeta, texto)
    tabla: list[list[str]] = []
    titulo = None

    def cerrar_parrafo():
        if parrafo:
            story.append(Paragraph(_en_linea(" ".join(parrafo)), ESTILOS["cuerpo"]))
            parrafo.clear()

    def cerrar_items():
        if items:
            for nivel, vineta, contenido in items:
                story.append(Paragraph(_en_linea(contenido), _estilo_item(nivel), bulletText=vineta))
            story.append(Spacer(1, 4))
            items.clear()

    def cerrar_tabla():
        if tabla:
            story.append(_tabla(tabla))
            story.append(Spacer(1, 8))
            tabla.clear()

    def cerrar_todo():
        cerrar_parrafo()
        cerrar_items()
        cerrar_tabla()

    for crudo in texto.splitlines():
        linea = crudo.strip()
        sangria = len(crudo) - len(crudo.lstrip(" "))
        if not linea:
            cerrar_todo()
            continue
        if linea.startswith("# "):
            cerrar_todo()
            titulo = linea[2:]
            continue
        if linea.startswith("## "):
            cerrar_todo()
            story.append(Paragraph(_en_linea(linea[3:]), ESTILOS["h1"]))
            continue
        if linea.startswith("|"):
            cerrar_parrafo()
            cerrar_items()
            celdas = [c.strip() for c in linea.strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in celdas):
                tabla.append(celdas)
            continue
        numerado = re.match(r"(\d+)\. (.*)", linea)
        if linea.startswith("- ") or numerado:
            cerrar_parrafo()
            nivel = sangria // 2
            if numerado:
                items.append((nivel, f"{numerado.group(1)}.", numerado.group(2)))
            else:
                items.append((nivel, "•" if nivel == 0 else "–", linea[2:]))
            continue
        if items and sangria > 0:
            nivel, vineta, contenido = items[-1]
            items[-1] = (nivel, vineta, f"{contenido} {linea}")
            continue
        cerrar_items()
        parrafo.append(linea)
    cerrar_todo()

    # La portada lleva el titulo y el primer parrafo como bajada.
    bajada = ""
    if story and isinstance(story[0], Paragraph):
        bajada = story.pop(0).text
    return [_portada(titulo or "Manual de usuario", bajada), Spacer(1, 14), *story]


def _presenta_lo_que_sigue(elemento) -> bool:
    if not isinstance(elemento, Paragraph):
        return False
    return elemento.style.name == "h1" or elemento.text.rstrip().endswith(":")


def _agrupar_titulos(story: list) -> list:
    # Un titulo de seccion, o un "Avisos posibles:", no queda solo al pie de una hoja.
    agrupado = []
    for elemento in story:
        if agrupado and _presenta_lo_que_sigue(agrupado[-1]):
            agrupado[-1] = KeepTogether([agrupado[-1], elemento])
        else:
            agrupado.append(elemento)
    return agrupado


def main() -> None:
    origen = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE
    destino = Path(sys.argv[2]) if len(sys.argv) > 2 else (TARGET if origen == SOURCE else origen.with_suffix(".pdf"))
    texto = origen.read_text(encoding="utf-8")
    titulo = next((linea[2:].strip() for linea in texto.splitlines() if linea.startswith("# ")), "Manual de usuario")
    doc = SimpleDocTemplate(
        str(destino),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=1.6 * cm,
        title=titulo,
        author="Gerayse",
    )
    doc.build(_agrupar_titulos(armar(texto)))
    print(f"PDF generado en: {destino}")


if __name__ == "__main__":
    main()
