"""Recuperación de fragmentos: BM25 con filtros de vigencia y aplicabilidad.

Principios (ver docs/rag.md):
- Una revisión reemplazada (TX350-C) no se devuelve salvo pedido explícito (`incluir_vencidos`).
- Lo que no aplica a PIA-3 se degrada (`PENALIZACION_NO_APLICA`), no se borra.
- El título de la sección pesa más que el cuerpo: las preguntas de campo nombran el tema, no el número.
- Cada resultado trae la cita lista para pegar en una conclusión.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .corpus import Fragmento

_PARADA = set(
    (
        "a al algo como con cual cuales cuando de del el ella en es esta este esto hay la las le lo los mas "
        "me mi muy no o otra otras para pero por que se si sin sobre su sus un una uno y ya ante hace hacer "
        "tiene son fue ser puede qué cuál cuáles cómo cuánto cuántos dónde"
    ).split()
)
PENALIZACION_NO_APLICA = 0.5
PESO_TITULO = 3


def tokens(texto: str) -> list[str]:
    texto = unicodedata.normalize("NFD", texto.lower())
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return [t[:6] for t in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", texto) if t not in _PARADA]


@dataclass(frozen=True)
class Resultado:
    fragmento: Fragmento
    puntaje: float

    @property
    def cita(self) -> str:
        return self.fragmento.cita


class Recuperador:
    def __init__(self, fragmentos: list[Fragmento], k1: float = 1.4, b: float = 0.75):
        self.fragmentos = fragmentos
        self.k1, self.b = k1, b
        self._tf: list[Counter] = []
        for f in fragmentos:
            c = Counter(tokens(f.texto))
            for t in tokens(f.titulo):
                c[t] += PESO_TITULO
            self._tf.append(c)
        self._largo = [sum(c.values()) for c in self._tf]
        self._prom = sum(self._largo) / max(len(self._largo), 1)
        df = Counter(t for c in self._tf for t in c)
        n = len(self._tf)
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def buscar(
        self,
        pregunta: str,
        k: int = 5,
        *,
        incluir_vencidos: bool = False,
        equipo: str | None = None,
        documento: str | None = None,
    ) -> list[Resultado]:
        q = tokens(pregunta)
        puntajes: list[tuple[float, int]] = []
        for i, (f, tf) in enumerate(zip(self.fragmentos, self._tf, strict=True)):
            if not f.vigente and not incluir_vencidos:
                continue
            if documento and f.documento != documento:
                continue
            if equipo and f.equipos and equipo not in f.equipos:
                continue
            s = 0.0
            for t in q:
                if t in tf:
                    freq = tf[t]
                    s += (
                        self._idf[t]
                        * freq
                        * (self.k1 + 1)
                        / (freq + self.k1 * (1 - self.b + self.b * self._largo[i] / self._prom))
                    )
            if s <= 0:
                continue
            if not f.aplica:
                s *= PENALIZACION_NO_APLICA
            puntajes.append((s, i))
        puntajes.sort(reverse=True)
        return [Resultado(self.fragmentos[i], round(s, 4)) for s, i in puntajes[:k]]

    def citas(self, pregunta: str, k: int, **filtros) -> list[str]:
        """Interfaz de `corpus.evaluar.evaluar`: citas únicas en orden de ranking."""
        return [r.cita for r in self.buscar(pregunta, k, **filtros)]
