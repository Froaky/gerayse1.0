"""Grilla semanal de horarios (EP-14 D18, US-14.13).

Cada casilla de la grilla es un texto corto que escribe el encargado:
- vacio: sin horario ese dia (no cuenta como falta ni tardanza);
- "F" o "franco": franco;
- una hora de entrada: "8", "8:30", "08.30", "8hs";
- dos horas, con turno cortado: "8 17", "8:00 y 17:00", "8/17", "8, 17".
La salida no se carga. Lo que se puede leer de dos maneras se rechaza, porque
adivinar seria peor que pedir que lo corrijan: un guion ("8-16" parece entrada
y salida), una coma pegada entre dos numeros ("8,15" o "8,15hs": ¿8:15 o dos
entradas?) o solo separadores. Con minutos antes de la coma ("8:00,17:00") o
con un espacio despues ("8, 17") no hay duda: son dos entradas.
"""

import re
from dataclasses import dataclass
from datetime import date, time, timedelta

DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_SEPARADORES = re.compile(r"\s*(?:\s|,|/|\by\b)\s*")
_HORA = re.compile(r"([0-9]{1,2})(?:[:.h]([0-9]{2}))?(?:hs?)?")
# Una coma entre dos numeros, sin minutos antes ("8,15", "8,15hs", "y 8,15").
_COMA_PEGADA = re.compile(r"(?:^|[^0-9:.h])([0-9]{1,2}),([0-9]{1,2})")


class CeldaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Celda:
    franco: bool = False
    entradas: tuple = ()

    @property
    def vacia(self) -> bool:
        return not self.franco and not self.entradas

    @property
    def texto(self) -> str:
        """Como se muestra en la grilla y se guarda en el historial."""
        if self.franco:
            return "Franco"
        return " y ".join(f"{e.hour}:{e.minute:02d}" for e in self.entradas)

    @classmethod
    def de_horario(cls, horario) -> "Celda":
        if horario is None:
            return cls()
        return cls(franco=horario.franco, entradas=horario.entradas)


def _hora(texto: str) -> time:
    partes = _HORA.fullmatch(texto)
    if not partes:
        raise CeldaInvalida(f"No entiendo «{texto}». Escribí la hora de entrada, por ejemplo 8 o 8:30.")
    horas, minutos = int(partes[1]), int(partes[2] or 0)
    if horas > 23 or minutos > 59:
        raise CeldaInvalida(f"«{texto}» no es una hora válida.")
    return time(horas, minutos)


def _aviso_de_coma(hora: str, otro: str) -> str:
    """Propone solo lo que se puede escribir: H:MM si son minutos validos, y
    "H M" si las dos son horas y la segunda es mayor."""
    opciones = []
    if len(otro) == 2:
        if int(hora) <= 23 and int(otro) <= 59:
            opciones.append(f"{int(hora)}:{otro} si es una hora")
        if int(hora) < int(otro) <= 23:
            opciones.append(f"{int(hora)} {int(otro)} si tiene turno cortado")
    if not opciones:
        return (
            f"«{hora},{otro}»: con coma no se entiende. Escribí la hora con dos puntos (8:30), "
            "o las dos entradas separadas por un espacio (8 17)."
        )
    return f"«{hora},{otro}»: escribí " + ", o ".join(opciones) + "."


def leer_celda(texto: str) -> Celda:
    texto = " ".join((texto or "").lower().split())
    if not texto:
        return Celda()
    if texto in ("f", "franco"):
        return Celda(franco=True)
    if "-" in texto:
        raise CeldaInvalida("Poné solo la hora de entrada (8), o dos si tiene turno cortado (8 17): la salida no va.")
    coma = _COMA_PEGADA.search(texto)
    if coma:
        # "8,15": ¿8:15 o dos entradas? Adivinar sale caro en el Excel.
        raise CeldaInvalida(_aviso_de_coma(coma[1], coma[2]))
    partes = [p for p in _SEPARADORES.split(texto) if p]
    if not partes:
        # Solo separadores (",", "/", "y"): no es una casilla vacia, que borraria el horario.
        raise CeldaInvalida(f"No entiendo «{texto}». Escribí la hora de entrada (8 o 8:30), F si tiene franco, o dejala vacía.")
    if len(partes) > 2:
        raise CeldaInvalida("Como mucho dos entradas por día (turno cortado).")
    entradas = tuple(_hora(p) for p in partes)
    if len(entradas) == 2 and entradas[1] <= entradas[0]:
        raise CeldaInvalida("La segunda entrada tiene que ser después de la primera.")
    return Celda(entradas=entradas)


def lunes_de(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def dias_de_la_semana(lunes: date) -> list[date]:
    return [lunes + timedelta(days=n) for n in range(7)]


def dia_editable(dia: date, *, administra: bool, hoy: date) -> bool:
    """El encargado carga y cambia hasta el dia anterior: hoy y lo pasado ya
    cuentan para tardanzas y faltas. Quien administra puede corregir cualquier dia."""
    return administra or dia > hoy
