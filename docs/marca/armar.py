"""Arma la entrega de un documento de Gerayse: un HTML autocontenido y su PDF.

    python docs/marca/armar.py docs/mi-informe.html --destino "C:/Users/mcoca/Desktop/gerayse/informes"

Qué hace:
  1. Toma el HTML (hecho con docs/marca/gerayse-documento.css) y mete la hoja de
     estilos adentro de un <style>, así el archivo viaja solo (WhatsApp, mail).
  2. Lo guarda en --destino con el nombre del <title> (o --nombre).
  3. Lo imprime a PDF con Edge o Chrome sin cabeza, esperando a que carguen las
     fuentes de Google Fonts. Sin ese tiempo de espera el PDF sale con Georgia y
     Segoe UI en vez de Fraunces y Public Sans.

Solo usa la biblioteca estándar. Hace falta Edge o Chrome instalado.
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
HOJA = AQUI / "gerayse-documento.css"
NAVEGADORES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
LINK_HOJA = re.compile(r"""<link[^>]+href=["'][^"']*gerayse-documento\.css["'][^>]*>""", re.IGNORECASE)
TITULO = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
COMENTARIO = re.compile(r"<!--.*?-->", re.DOTALL)


def navegador() -> str:
    for ruta in NAVEGADORES:
        if Path(ruta).exists():
            return ruta
    for nombre in ("msedge", "chrome"):
        encontrado = shutil.which(nombre)
        if encontrado:
            return encontrado
    sys.exit("No encuentro Edge ni Chrome para imprimir el PDF.")


def nombre_de_archivo(texto: str) -> str:
    limpio = re.sub(r'[\\/:*?"<>|]+', "-", texto).strip(" .")
    return limpio or "documento"


def autocontenido(origen: Path) -> str:
    contenido = origen.read_text(encoding="utf-8")
    if LINK_HOJA.search(contenido):
        estilo = "<style>\n" + HOJA.read_text(encoding="utf-8") + "\n</style>"
        contenido = LINK_HOJA.sub(lambda _m: estilo, contenido, count=1)
    return contenido


def imprimir(exe: str, html_path: Path, pdf_path: Path) -> None:
    orden = [
        exe,
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=10000",
        f"--print-to-pdf={pdf_path}",
        html_path.resolve().as_uri(),
    ]
    subprocess.run(orden, check=True, capture_output=True, timeout=120)
    if not pdf_path.exists():
        sys.exit("El navegador no escribió el PDF.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", type=Path, help="documento HTML armado con gerayse-documento.css")
    parser.add_argument("--destino", type=Path, default=None, help="carpeta de salida (por defecto, la del HTML)")
    parser.add_argument("--nombre", default=None, help="nombre de los archivos de salida (por defecto, el <title>)")
    parser.add_argument("--sin-pdf", action="store_true", help="solo el HTML autocontenido")
    args = parser.parse_args()

    origen: Path = args.html
    if not origen.exists():
        sys.exit(f"No existe {origen}")
    destino: Path = args.destino or origen.parent
    destino.mkdir(parents=True, exist_ok=True)

    contenido = autocontenido(origen)
    # El <title> se busca sin los comentarios: las plantillas lo mencionan en uno.
    titulo = TITULO.search(COMENTARIO.sub("", contenido))
    nombre = nombre_de_archivo(args.nombre or (html.unescape(titulo.group(1).strip()) if titulo else origen.stem))

    salida_html = destino / f"{nombre}.html"
    if salida_html.resolve() == origen.resolve():
        sys.exit("El destino pisaría el original: elegí otra carpeta o otro --nombre.")
    salida_html.write_text(contenido, encoding="utf-8")
    print(f"HTML  {salida_html}")

    if not args.sin_pdf:
        salida_pdf = destino / f"{nombre}.pdf"
        imprimir(navegador(), salida_html, salida_pdf)
        print(f"PDF   {salida_pdf}")


if __name__ == "__main__":
    main()
