"""Rango histórico de un sensor (A-9): lo que el equipo midió en los últimos 45 días.

Sirve para desconfiar de un valor dictado que cae lejos de todo lo visto. El caso típico: el ASR se come
el «mil» («mil seiscientos psi» llega como «seiscientos psi»). Si el valor dictado cae fuera del rango y el
mismo valor con el mil recuperado cae adentro, el borrador lo muestra así, como provisional y avisándolo.

El rango sale de `GET /api/historian/{tag}/{variable}` (una muestra por minuto) y se guarda en caché una
hora: la consulta trae decenas de miles de muestras. Si el historian no responde, no se opina (nunca se
bloquea un parte).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("sistema.rango")

TTL_S = 3600.0
MARGEN = 0.10  # del máximo en valor absoluto: los manómetros locales no leen igual que el transmisor

# Variable dicha en campo -> variable del historian que le corresponde.
VARIABLE_HISTORIAN = {
    "presion_descarga_local": "presion_descarga",
    "presion_diferencial": "presion_diferencial",
}
A_KG_CM2 = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1.01972}


@dataclass(frozen=True)
class Rango:
    minimo: float
    maximo: float

    def contiene(self, valor: float) -> bool:
        return self.minimo <= valor <= self.maximo


class RangoHistorico:
    def __init__(
        self, historia: Callable[[str, str], list[float]], reloj: Callable[[], float] = time.monotonic
    ):
        """`historia(tag, variable)`: los valores de los últimos 45 días (o levanta si no se puede)."""
        self._historia = historia
        self._reloj = reloj
        self._cache: dict[tuple[str, str], tuple[float, Rango | None]] = {}
        self._lock = threading.Lock()

    def rango(self, tag: str, variable: str, solo_cache: bool = False) -> Rango | None:
        clave = (tag, VARIABLE_HISTORIAN.get(variable, variable))
        with self._lock:
            hit = self._cache.get(clave)
        if hit is not None and self._reloj() - hit[0] < TTL_S:
            return hit[1]
        if solo_cache:
            return hit[1] if hit else None
        try:
            valores = [v for v in self._historia(*clave) if v is not None]
        except Exception as e:  # sin historian no se opina
            log.warning("sin rango histórico de %s/%s: %s", clave[0], clave[1], e)
            valores = []
        r = None
        if valores:
            lo, hi = min(valores), max(valores)
            margen = MARGEN * max(abs(hi), abs(lo))
            r = Rango(round(lo - margen, 4), round(hi + margen, 4))
        with self._lock:
            self._cache[clave] = (self._reloj(), r)
        return r

    def precalentar(self, pares: list[tuple[str, str]]) -> None:
        """Trae los rangos antes de que hagan falta (la primera consulta tarda unos segundos)."""
        for tag, variable in pares:
            self.rango(tag, variable)

    def evaluar(self, tag: str, variable: str, valor: float, unidad: str | None) -> Evaluacion | None:
        """None si no hay rango para opinar o la variable no tiene historian."""
        if variable not in VARIABLE_HISTORIAN:
            return None
        r = self.rango(tag, variable, solo_cache=True)
        if r is None:
            return None
        factor = A_KG_CM2.get(unidad or "kg/cm2", 1.0)
        en_kg = valor * factor
        if r.contiene(en_kg):
            return Evaluacion(True, r, None)
        # ¿Se perdió el «mil»? Solo para magnitudes que se dictan en miles (psi).
        if unidad == "psi" and 0 < valor < 1000 and r.contiene((valor + 1000) * factor):
            return Evaluacion(False, r, valor + 1000)
        return Evaluacion(False, r, None)


@dataclass(frozen=True)
class Evaluacion:
    dentro: bool
    rango: Rango
    con_mil: float | None  # el valor con el «mil» recuperado, si ese sí cae adentro del rango
