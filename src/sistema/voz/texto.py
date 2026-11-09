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
    t = sin_acentos(t)
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)  # signos que no están entre dígitos
    t = re.sub(r"[^a-z0-9.,/%° ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def oraciones(t: str) -> list[str]:
    """Parte el texto en oraciones (ya normalizadas) usando . ! ? ; como cortes duros."""
    return [o for o in (normalizar(x) for x in _SEPARADORES_DE_ORACION.split(t)) if o]
