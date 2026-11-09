"""Detección determinista de condiciones inseguras S1–S4 sobre el texto de la nota. Sin LLM.

  S1  rotura con salida de agua a presión
  S2  olor a gas
  S3  humo / olor a quemado
  S4  persona lesionada

Reglas (Plan Maestro §10.3, R14):
  - Se buscan **frases**, no palabras sueltas: "humo" o "presión" solas no disparan nada.
  - Una negación en las tres palabras anteriores dentro de la misma oración anula la frase
    ("no hay olor a gas", "sin humo").
  - Cada falsa alarma resta 0,25 en B2: ante la duda razonable de una palabra suelta, no se alerta.
  - Las alertas salen solo de notas, nunca de sensores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..dominio.catalogo import Catalogo
from ..voz.texto import oraciones

NEGADORES = {"no", "sin", "ningun", "ninguna", "ni", "nada", "nadie", "tampoco", "descarto"}

# Patrones sobre texto normalizado (minúsculas, sin acentos). Cada uno es una frase completa de la condición.
PATRONES: dict[str, list[re.Pattern[str]]] = {
    "S1": [
        re.compile(r"\bagua (a )?presion\b"),
        re.compile(
            r"\bse rompi(o|eron)\b.*\b(descarga|manguera|tuberia|caneria|linea|brida|union|flexible)\b"
        ),
        re.compile(
            r"\b(revento|reventaron|estallo|estallaron)\b.*\b(descarga|manguera|tuberia|caneria|linea|brida|flexible)\b"
        ),
        re.compile(r"\b(chorro|pierde|sale) (de )?agua (a )?(alta )?presion\b"),
        re.compile(r"\bse rompi(o|eron) (algo|todo)\b"),
    ],
    "S2": [
        re.compile(r"\bolor (a|al|de) gas\b"),
        re.compile(r"\bhuele (a|al) gas\b"),
        re.compile(r"\b(fuga|perdida|escape|perdiendo|pierde) (de )?gas\b"),
        re.compile(r"\bgas (fuerte|en el aire)\b"),
    ],
    "S3": [
        re.compile(r"\b(sale|salen|hay|sale mucho|sube) humo\b"),
        re.compile(r"\bhumo (del|de la|en el|en la|negro|blanco)\b"),
        re.compile(r"\bolor a quemado\b"),
        re.compile(r"\b(incendio|llamas|se prendio fuego|principio de fuego|fuego en)\b"),
    ],
    "S4": [
        re.compile(r"\bsangr(a|ando|e|o)\b"),
        re.compile(r"\b(esta|quedo|esta muy) (herido|herida|lesionado|lesionada|lastimado|lastimada)\b"),
        re.compile(r"\bse (lastimo|lesiono|desmayo|quemo|corto|cayo y no)\b"),
        re.compile(r"\bse golpeo\b"),
        re.compile(r"\b(accidente|accidentado|persona herida|compañero herido)\b"),
    ],
}

DESCRIPCION = {
    "S1": "rotura con salida de agua a presión",
    "S2": "olor a gas",
    "S3": "humo u olor a quemado",
    "S4": "persona lesionada",
}
# Los equipos solo tienen sentido para roturas y humo; el olor a gas y las lesiones no se asignan a un equipo.
CON_EQUIPO = {"S1", "S3"}


@dataclass(frozen=True)
class Deteccion:
    codigo: str
    equipo: str | None
    frase: str

    @property
    def descripcion(self) -> str:
        return DESCRIPCION[self.codigo]


def _negada(oracion: str, inicio: int) -> bool:
    previas = oracion[:inicio].split()[-3:]
    return any(p in NEGADORES for p in previas)


# Final de oración que deja una negación abierta: «no hay», «sin», «ni», «ningún». Un «no» suelto no cuenta:
# es una respuesta o una muletilla y la oración siguiente puede ser una alerta real (perderla es
# eliminatoria).
_COLGANTES = {"sin", "ni", "ningun", "ninguna", "nada", "nadie"}


# Error medido del ASR sobre «olor a gas»: «Hay hólogas fuertes» (todo pegado) y variantes sin la «a».
# Se repara antes de buscar frases; «gas» tiene que ser palabra completa, así que «hologramas» no entra.
_OLOR_A_GAS = re.compile(r"\b(?:h?olor ?(?:a |al )?|h?olora ?|h?olo ?)gas(?:es)?\b")


def _negacion_colgante(oracion: str) -> bool:
    palabras = oracion.split()
    return bool(palabras) and (palabras[-1] in _COLGANTES or palabras[-2:] == ["no", "hay"])


def _oraciones_unidas(texto: str) -> list[str]:
    """Las oraciones del texto completo; si el ASR cortó una negación en el límite de un segmento (punto o
    puntos suspensivos después de «no hay»), se une con la siguiente y la negación sigue valiendo."""
    salida: list[str] = []
    unir = False
    for o in oraciones(texto):
        o = _OLOR_A_GAS.sub("olor a gas", o)
        if unir and salida:
            salida[-1] = f"{salida[-1]} {o}"
        else:
            salida.append(o)
        unir = _negacion_colgante(salida[-1])
    return salida


def detectar(texto: str, catalogo: Catalogo | None = None) -> Deteccion | None:
    """Devuelve la primera condición insegura del texto completo, o None.

    El texto es la transcripción entera de la nota, nunca un segmento suelto. S1 y S3 llevan el equipo
    si se nombra.
    """
    for oracion in _oraciones_unidas(texto):
        for codigo, patrones in PATRONES.items():
            for patron in patrones:
                m = patron.search(oracion)
                if m and not _negada(oracion, m.start()):
                    equipo = _equipo(oracion, texto, catalogo) if codigo in CON_EQUIPO else None
                    return Deteccion(codigo, equipo, m.group(0))
    return None


def _equipo(oracion: str, texto: str, catalogo: Catalogo | None) -> str | None:
    """El equipo de la rotura o del humo: el nombrado en la misma oración; si no, el primero de la nota."""
    if catalogo is None:
        return None
    for fuente in (oracion, *oraciones(texto)):
        for m in catalogo.menciones(fuente):
            if m.tag is not None:
                return m.tag
    return None
