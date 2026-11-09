"""Cliente del servicio `asr` (transcripción local en su propio contenedor)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("sistema.asr")


@dataclass(frozen=True)
class Transcripcion:
    texto: str
    duracion_s: float
    tiempo_s: float


class ClienteAsr:
    def __init__(self, url: str, timeout_s: float = 90.0, http: httpx.Client | None = None):
        # Directo por planta-lan, nunca por el proxy del enlace.
        self._http = http or httpx.Client(base_url=url, timeout=timeout_s, trust_env=False)
        self._ok = False
        self._ultimo_chequeo = 0.0

    def listo(self) -> bool:
        """True cuando el modelo está cargado y calentado. Una vez listo no se vuelve a consultar."""
        if self._ok:
            return True
        ahora = time.monotonic()
        if ahora - self._ultimo_chequeo < 1.0:
            return False
        self._ultimo_chequeo = ahora
        try:
            self._ok = self._http.get("/salud", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            self._ok = False
        return self._ok

    def transcribir(self, audio: bytes) -> Transcripcion:
        r = self._http.post(
            "/transcribir", content=audio, headers={"Content-Type": "application/octet-stream"}
        )
        r.raise_for_status()
        d = r.json()
        return Transcripcion(d["texto"], d["duracion_s"], d["tiempo_s"])
