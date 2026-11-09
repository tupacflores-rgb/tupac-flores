"""Recuperación híbrida: BM25 (léxico) + búsqueda densa en pgvector (e5-small), fusionadas por RRF.

La parte densa vive en el servicio `emb`. Si no responde (caído, lento), la búsqueda sigue solo con el
léxico: el conocimiento nunca es requisito de la alerta ni del parte.

Filtros: la revisión vencida no entra salvo pedido explícito (ya lo filtran las dos partes) y lo que no
aplica a PIA-3 se degrada multiplicando el puntaje fusionado por `PENALIZACION_NO_APLICA`.
"""

from __future__ import annotations

import logging

import httpx

from .recuperador import PENALIZACION_NO_APLICA, Recuperador, Resultado

log = logging.getLogger("sistema.hibrido")

K_RRF = 60  # constante clásica de Reciprocal Rank Fusion
# El léxico solo ya es más fuerte que el denso solo en esta suite (hit@5 0,86 contra 0,79): pesa el doble.
PESO_LEXICO = 2.0
PESO_DENSO = 1.0
CANDIDATOS = 30


class ClienteEmb:
    def __init__(self, url: str, timeout_s: float = 3.0, http: httpx.Client | None = None):
        # Directo por planta-lan, nunca por el proxy del enlace.
        self._http = http or httpx.Client(base_url=url, timeout=timeout_s, trust_env=False)

    def buscar(
        self, pregunta: str, k: int, prefijos: bool = True, incluir_vencidos: bool = False
    ) -> list[dict]:
        r = self._http.post(
            "/buscar",
            json={"pregunta": pregunta, "k": k, "prefijos": prefijos, "incluir_vencidos": incluir_vencidos},
        )
        r.raise_for_status()
        return r.json()["resultados"]


class RecuperadorHibrido:
    def __init__(self, lexico: Recuperador, emb: ClienteEmb | None, prefijos: bool = True):
        self.lexico = lexico
        self.emb = emb
        self.prefijos = prefijos
        self._por_cita = {f.cita: f for f in lexico.fragmentos}

    def denso(self, pregunta: str, k: int, incluir_vencidos: bool = False) -> list[str]:
        if self.emb is None:
            return []
        try:
            return [r["cita"] for r in self.emb.buscar(pregunta, k, self.prefijos, incluir_vencidos)]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("búsqueda densa no disponible (%s): sigo solo con el léxico", e)
            return []

    def buscar(
        self, pregunta: str, k: int = 5, *, incluir_vencidos: bool = False, **filtros
    ) -> list[Resultado]:
        lexico = [
            r.cita
            for r in self.lexico.buscar(pregunta, CANDIDATOS, incluir_vencidos=incluir_vencidos, **filtros)
        ]
        denso = (
            []
            if filtros.get("equipo") or filtros.get("documento")
            else self.denso(pregunta, CANDIDATOS, incluir_vencidos)
        )
        puntaje: dict[str, float] = {}
        for peso, ranking in ((PESO_LEXICO, lexico), (PESO_DENSO, denso)):
            for pos, cita in enumerate(ranking):
                puntaje[cita] = puntaje.get(cita, 0.0) + peso / (K_RRF + pos + 1)
        for cita in puntaje:
            f = self._por_cita.get(cita)
            if f is not None and not f.aplica:
                puntaje[cita] *= PENALIZACION_NO_APLICA
        orden = sorted(puntaje, key=lambda c: -puntaje[c])[:k]
        return [
            Resultado(self._por_cita[c], round(puntaje[c] * 1000, 4)) for c in orden if c in self._por_cita
        ]

    def citas(self, pregunta: str, k: int, **filtros) -> list[str]:
        return [r.cita for r in self.buscar(pregunta, k, **filtros)]
