"""Normalización de texto transcripto: minúsculas, sin acentos, sin signos."""

from __future__ import annotations

import re
import unicodedata

_SEPARADORES_DE_ORACION = re.compile(r"[.!?;\n¡¿]+")


def sin_acentos(t: str) -> str:
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def normalizar(t: str) -> str:
    """Minúsculas, sin acentos ni signos, espacios simples. Los números y decimales conservan `,` y `.`
    cuando están entre dígitos (el parser de números los necesita)."""
    t = sin_acentos(t).replace("°", " grados ")  # "61°" = "sesenta y un grados"
    t = re.sub(r"(?<=\d)(?=[a-z])", " ", t)  # "1610psi" / "1610p si" -> "1610 psi" / "1610 p si"
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)  # signos que no están entre dígitos
    t = re.sub(r"[^a-z0-9.,/%° ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_FILTRO_MAL_DICHO = re.compile(r"\b(f[a-z]{2,6}) ([ab])\b")
_NO_ES_FILTRO = {"foto", "fotos", "fuga", "fuera", "falta", "fue", "fino", "fina"}
_LETRA_NUMERO = re.compile(r"\b(?:el|en el|en) a([123])\b")


def distancia_de_edicion(a: str, b: str) -> int:
    previa = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        actual = [i]
        for j, y in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1, previa[j - 1] + (x != y)))
        previa = actual
    return previa[-1]


def reparar_asr(t: str) -> str:
    """Corrige confusiones sistemáticas del transcriptor sobre texto ya normalizado (medidas en las voces de
    referencia): "el fito a" por "el filtro a" (hasta 2 letras de diferencia), "el a1" por "en la 1"."""

    def filtro(m: re.Match[str]) -> str:
        palabra = m.group(1)
        if palabra not in _NO_ES_FILTRO and distancia_de_edicion(palabra, "filtro") <= 2:
            return f"filtro {m.group(2)}"
        return m.group(0)

    t = _FILTRO_MAL_DICHO.sub(filtro, t)
    return _LETRA_NUMERO.sub(lambda m: f"en la {m.group(1)}", t)


def oraciones(t: str) -> list[str]:
    """Parte el texto en oraciones (ya normalizadas) usando . ! ? ; como cortes duros."""
    return [o for o in (normalizar(x) for x in _SEPARADORES_DE_ORACION.split(t)) if o]
