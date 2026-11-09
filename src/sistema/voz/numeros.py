"""Números dichos en voz alta, en español rioplatense, y cómo los escribe el transcriptor.

Casos que cubre (Plan Maestro R3/R4):
  "ciento quince"            -> 115
  "mil seiscientos cuarenta" -> 1640        "1.640" / "1,640" / "1640" -> 1640 (punto de miles)
  "uno ocho"                 -> 1.8         "uno coma ocho" / "uno con ocho" / "1,8" / "1.8" -> 1.8
  "uno y medio"              -> 1.5
  "dieciocho"                -> 18 y se marca como *entero dicho* (puede ser "uno ocho" mal dicho)

El texto de entrada ya está normalizado (minúsculas, sin acentos; ver texto.normalizar).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29,
}  # fmt: skip
DECENAS = {
    "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
    "ochenta": 80, "noventa": 90,
}  # fmt: skip
CENTENAS = {
    "cien": 100, "ciento": 100, "doscientos": 200, "trescientos": 300, "cuatrocientos": 400,
    "quinientos": 500, "seiscientos": 600, "setecientos": 700, "ochocientos": 800, "novecientos": 900,
}  # fmt: skip
SEPARADORES_DECIMALES = {"coma", "con", "punto"}
RE_DIGITOS = re.compile(r"^\d+(?:[.,]\d+)*$")

PALABRAS_NUMERICAS = (
    set(UNIDADES) | set(DECENAS) | set(CENTENAS) | {"mil", "y", "medio", "media"} | SEPARADORES_DECIMALES
)


@dataclass(frozen=True)
class Numero:
    valor: float
    entero_dicho: bool = False  # se dijo/escribió como un entero >= 10 sin separador decimal ("dieciocho")


def _es_numerico(token: str) -> bool:
    return token in PALABRAS_NUMERICAS or bool(RE_DIGITOS.match(token))


def entero_en_palabras(palabras: list[str]) -> int | None:
    """Valor de un cardinal completo ("mil seiscientos cuarenta"), o None si no es un cardinal válido."""
    if not palabras:
        return None
    total = actual = 0
    ultimo = ""  # clase del token anterior: unidad | decena | centena | mil | y
    for p in palabras:
        if p in UNIDADES:
            v = UNIDADES[p]
            # Dos unidades seguidas ("uno ocho") no forman un cardinal; tras una decena solo si hay "y".
            if ultimo == "unidad" or (ultimo == "decena") or (ultimo == "y" and v >= 10):
                return None
            actual += v
            ultimo = "unidad" if v < 30 else "decena"
        elif p in DECENAS:
            if ultimo in {"unidad", "decena"}:
                return None
            actual += DECENAS[p]
            ultimo = "decena"
        elif p in CENTENAS:
            if ultimo in {"unidad", "decena", "centena"}:
                return None
            actual += CENTENAS[p]
            ultimo = "centena"
        elif p == "mil":
            actual = (actual or 1) * 1000
            total += actual
            actual = 0
            ultimo = "mil"
        elif p == "y":
            if ultimo != "decena":
                return None
            ultimo = "y"
        else:
            return None
    if ultimo == "y":
        return None
    return total + actual


def _digito(palabra: str) -> int | None:
    v = UNIDADES.get(palabra)
    return v if v is not None and v < 10 else None


def _desde_digitos(token: str, decimal: bool) -> Numero | None:
    """Un número escrito con cifras: '115', '1640', '1.640' (miles), '1,8' o '1.8' (decimal)."""
    m = re.fullmatch(r"(\d+)(?:([.,])(\d+))?", token)
    if m:
        entero, sep, resto = m.groups()
        if sep is None:
            if decimal and len(entero) == 2 and entero[0] == "0":
                return Numero(float(f"0.{entero[1]}"))  # "09" = "cero nueve" sin el separador
            return Numero(float(entero), entero_dicho=decimal and int(entero) >= 10)
        if not decimal and len(resto) == 3:  # "1.640": punto de miles
            return Numero(float(entero + resto))
        return Numero(float(f"{entero}.{resto}"))
    # '1.640.000' o '1,640': separadores de miles repetidos
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        return Numero(float(re.sub(r"[.,]", "", token)))
    return None


def leer(palabras: list[str], decimal: bool = False) -> Numero | None:
    """Interpreta una serie de tokens numéricos. `decimal=True` para magnitudes con decimales
    (diferencial, nivel): permite "uno ocho" = 1,8. Con `decimal=False` espera un entero."""
    if not palabras:
        return None
    if len(palabras) == 1 and RE_DIGITOS.match(palabras[0]):
        return _desde_digitos(palabras[0], decimal)
    # Cifras sueltas: "1 8" -> 1,8 (solo en modo decimal).
    if decimal and all(p.isdigit() and len(p) == 1 for p in palabras) and len(palabras) == 2:
        return Numero(float(f"{palabras[0]}.{palabras[1]}"))
    # "uno y medio"
    if palabras[-1] in {"medio", "media"} and len(palabras) >= 2 and palabras[-2] == "y":
        base = entero_en_palabras(palabras[:-2])
        if base is not None and decimal:
            return Numero(base + 0.5)
        return None
    # "uno coma ocho", "seis con dos"
    for i, p in enumerate(palabras):
        if p in SEPARADORES_DECIMALES and 0 < i < len(palabras) - 1:
            izquierda = entero_en_palabras(palabras[:i])
            if izquierda is None and i == 1 and palabras[0].isdigit():
                izquierda = int(palabras[0])
            derecha = palabras[i + 1 :]
            digitos = [_digito(d) if not d.isdigit() else int(d) for d in derecha]
            if (
                izquierda is not None
                and digitos
                and all(d is not None and d < 10 for d in digitos)
                and decimal
            ):
                return Numero(izquierda + int("".join(str(d) for d in digitos)) / 10 ** len(digitos))
            return None
    entero = entero_en_palabras(palabras)
    if entero is not None:
        return Numero(float(entero), entero_dicho=decimal and entero >= 10)
    # "uno ocho", "cero ocho": entero de una cifra seguido de cifras sueltas
    if decimal and len(palabras) >= 2:
        cifras = [_digito(p) for p in palabras]
        if all(c is not None for c in cifras):
            return Numero(cifras[0] + int("".join(str(c) for c in cifras[1:])) / 10 ** (len(cifras) - 1))
    return None


def _sin_o_intercalada(tokens: list[str]) -> list[str]:
    """El transcriptor a veces mete una "o" entre dos cifras dichas ("uno o dos" por "uno dos")."""
    salida = []
    for i, t in enumerate(tokens):
        if (
            t == "o"
            and 0 < i < len(tokens) - 1
            and _digito(tokens[i - 1]) is not None
            and _digito(tokens[i + 1]) is not None
        ):
            continue
        salida.append(t)
    return salida


def numero_antes_de(tokens: list[str], decimal: bool = False, basura: int = 0) -> Numero | None:
    """El número que termina el fragmento: toma la corrida final de tokens numéricos y la interpreta.

    Si la corrida entera no es válida, prueba con sufijos cada vez más cortos ("y dos" -> "dos"), porque
    palabras como "con" o "y" también aparecen en el texto que rodea al número. Con `basura` > 0 tolera hasta
    esa cantidad de tokens no numéricos al final (una unidad mal transcripta: "1610 p si")."""
    if decimal:
        tokens = _sin_o_intercalada(tokens)
    for sobrantes in range(basura + 1):
        omitidos = tokens[len(tokens) - sobrantes :] if sobrantes else []
        if any(_es_numerico(t) for t in omitidos):
            break  # solo se puede ignorar texto que no sea parte de un número
        recorte = tokens[: len(tokens) - sobrantes]
        if not recorte:
            break
        n = _numero_final(recorte, decimal)
        if n is not None:
            return n
    return None


def _numero_final(tokens: list[str], decimal: bool) -> Numero | None:
    i = len(tokens)
    while i > 0 and _es_numerico(tokens[i - 1]):
        i -= 1
    corrida = tokens[i:]
    for inicio in range(len(corrida)):
        n = leer(corrida[inicio:], decimal)
        if n is not None:
            return n
    return None


def formatear(valor: float) -> str:
    """1.8 -> '1,8'; 115.0 -> '115' (como se muestra en la tablet)."""
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:.2f}".rstrip("0").rstrip(".").replace(".", ",")
