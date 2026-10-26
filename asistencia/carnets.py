"""PDF de carnets de asistencia (EP-14 US-14.3).

A4 con 10 carnets tamano tarjeta (85,6 x 54 mm, el de una tarjeta de credito),
con borde para recortar. Cada carnet lleva el nombre, la empresa, el local
habitual y el QR con el codigo del carnet vigente (6 digitos). El codigo tambien
va escrito grande: si la camara no lee, el cajero lo tipea. Nunca el DNI.
"""

from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ANCHO = 85.6 * mm
ALTO = 54 * mm
COLUMNAS = 2
FILAS = 5
POR_PAGINA = COLUMNAS * FILAS
SEPARACION_X = 8 * mm
SEPARACION_Y = 3 * mm
MARGEN_X = (A4[0] - COLUMNAS * ANCHO - (COLUMNAS - 1) * SEPARACION_X) / 2
MARGEN_Y = (A4[1] - FILAS * ALTO - (FILAS - 1) * SEPARACION_Y) / 2
LADO_QR = 36 * mm
FRANJA = 9 * mm
TINTA = colors.HexColor("#1b3a2e")
VERDE = colors.HexColor("#22c47e")
GRIS = colors.HexColor("#6b757a")
BORDE = colors.HexColor("#c9d2ce")


def contenido_qr(empleado) -> str:
    """Lo unico que va adentro del QR: el codigo del carnet vigente."""
    return empleado.codigo_carnet


def _cortar_palabra(palabra: str, fuente: str, tamano: float, ancho: float) -> list[str]:
    """Parte por letras una palabra que sola no entra en el ancho (un apellido
    compuesto con guion, por ejemplo)."""
    pedazos, actual = [], ""
    for letra in palabra:
        if actual and stringWidth(actual + letra, fuente, tamano) > ancho:
            pedazos.append(actual)
            actual = letra
        else:
            actual += letra
    return pedazos + [actual] if actual else pedazos


def _renglones(texto: str, fuente: str, tamano: float, ancho: float, cortar: bool = True) -> list[str] | None:
    """Reparte el texto en renglones de `ancho`. Sin `cortar`, devuelve None si
    alguna palabra sola no entra."""
    renglones, actual = [], ""
    for palabra in texto.split():
        if not cortar and stringWidth(palabra, fuente, tamano) > ancho:
            return None
        for pedazo in _cortar_palabra(palabra, fuente, tamano, ancho):
            candidato = f"{actual} {pedazo}" if actual else pedazo
            if stringWidth(candidato, fuente, tamano) <= ancho:
                actual = candidato
            else:
                if actual:
                    renglones.append(actual)
                actual = pedazo
    return renglones + [actual] if actual else renglones


def ajustar_texto(texto: str, fuente: str, tamanos, ancho: float, maximo: int) -> tuple[float, list[str]]:
    """El tamano mas grande en el que el texto entra en `maximo` renglones de
    `ancho`. Si ni el mas chico alcanza, corta el ultimo renglon con "…": el
    texto nunca se sale de su zona (y nunca pisa el QR). Antes de partir una
    palabra prueba con letra mas chica."""
    for cortar in (False, True):
        for tamano in tamanos:
            renglones = _renglones(texto, fuente, tamano, ancho, cortar=cortar)
            if renglones is not None and len(renglones) <= maximo:
                return tamano, renglones
    renglones = renglones[:maximo]
    ultimo = renglones[-1]
    while ultimo and stringWidth(ultimo + "…", fuente, tamano) > ancho:
        ultimo = ultimo[:-1]
    renglones[-1] = ultimo.rstrip() + "…"
    return tamano, renglones


def generar_carnets_pdf(empleados) -> bytes:
    buffer = BytesIO()
    lienzo = canvas.Canvas(buffer, pagesize=A4)
    lienzo.setTitle("Carnets de asistencia")
    lienzo.setAuthor("Gerayse")
    for indice, empleado in enumerate(empleados):
        posicion = indice % POR_PAGINA
        if indice and posicion == 0:
            lienzo.showPage()
        columna = posicion % COLUMNAS
        fila = posicion // COLUMNAS
        x = MARGEN_X + columna * (ANCHO + SEPARACION_X)
        y = A4[1] - MARGEN_Y - (fila + 1) * ALTO - fila * SEPARACION_Y
        _dibujar_carnet(lienzo, x, y, empleado)
    lienzo.save()
    return buffer.getvalue()


def _dibujar_carnet(lienzo, x, y, empleado) -> None:
    # Borde para recortar y franja de arriba.
    lienzo.setStrokeColor(BORDE)
    lienzo.setLineWidth(0.6)
    lienzo.roundRect(x, y, ANCHO, ALTO, 3 * mm, stroke=1, fill=0)
    lienzo.setFillColor(TINTA)
    lienzo.rect(x, y + ALTO - FRANJA, ANCHO, FRANJA, stroke=0, fill=1)
    lienzo.setFillColor(VERDE)
    lienzo.rect(x, y + ALTO - FRANJA - 0.8 * mm, ANCHO, 0.8 * mm, stroke=0, fill=1)
    lienzo.setFillColor(colors.white)
    titulo = "CARNET DE ASISTENCIA"
    lienzo.setFont("Helvetica-Bold", 8.5)
    lienzo.drawString(x + 4 * mm, y + ALTO - 6 * mm, titulo)
    # La empresa ocupa lo que deja el titulo, nunca encima de el.
    ancho_empresa = ANCHO - 8 * mm - stringWidth(titulo, "Helvetica-Bold", 8.5) - 3 * mm
    tamano, renglones = ajustar_texto(empleado.empresa.nombre, "Helvetica", (8, 7, 6), ancho_empresa, 1)
    lienzo.setFont("Helvetica", tamano)
    lienzo.drawRightString(x + ANCHO - 4 * mm, y + ALTO - 6 * mm, renglones[0] if renglones else "")

    # QR a la derecha.
    qr = QrCodeWidget(contenido_qr(empleado), barLevel="M")
    x0, y0, x1, y1 = qr.getBounds()
    dibujo = Drawing(LADO_QR, LADO_QR, transform=[LADO_QR / (x1 - x0), 0, 0, LADO_QR / (y1 - y0), 0, 0])
    dibujo.add(qr)
    renderPDF.draw(dibujo, lienzo, x + ANCHO - LADO_QR - 2.5 * mm, y + 3 * mm)

    # Nombre, local y codigo a la izquierda. El recorte es la garantia de que
    # ningun texto, por largo que sea, pisa el QR (dejaria de leerse).
    ancho_texto = ANCHO - LADO_QR - 9 * mm
    lienzo.saveState()
    zona = lienzo.beginPath()
    zona.rect(x, y, 4 * mm + ancho_texto + 1 * mm, ALTO - FRANJA - 1 * mm)
    lienzo.clipPath(zona, stroke=0, fill=0)
    lienzo.setFillColor(TINTA)
    tamano, renglones = ajustar_texto(empleado.nombre, "Helvetica-Bold", (11, 10, 9, 8, 7), ancho_texto, 3)
    lienzo.setFont("Helvetica-Bold", tamano)
    cursor = y + ALTO - FRANJA - 7 * mm
    for renglon in renglones:
        lienzo.drawString(x + 4 * mm, cursor, renglon)
        cursor -= tamano * 1.18
    if empleado.sucursal.empresa_id == empleado.empresa_id:
        lienzo.setFillColor(GRIS)
        tamano, renglones = ajustar_texto(empleado.sucursal.nombre, "Helvetica", (8, 7, 6), ancho_texto, 2)
        lienzo.setFont("Helvetica", tamano)
        cursor -= 0.6 * mm
        for renglon in renglones:
            lienzo.drawString(x + 4 * mm, cursor, renglon)
            cursor -= tamano * 1.25
    # El codigo de 6 digitos, grande y en dos grupos ("483 920"): se tipea a mano
    # cuando la camara no lee.
    codigo = contenido_qr(empleado)
    lienzo.setFillColor(GRIS)
    lienzo.setFont("Helvetica", 6)
    lienzo.drawString(x + 4 * mm, y + 9 * mm, "Código")
    lienzo.setFillColor(TINTA)
    lienzo.setFont("Courier-Bold", 13)
    lienzo.drawString(x + 4 * mm, y + 4 * mm, f"{codigo[:3]} {codigo[3:]}" if len(codigo) == 6 else codigo)
    lienzo.restoreState()
