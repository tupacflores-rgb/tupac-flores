"""Cliente HTTP de la API de la planta (`http://sim:8100`). Va directo por planta-lan, sin proxy."""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger("sistema.planta_api")


class PlantaApi:
    def __init__(self, base_url: str, timeout_s: float = 5.0):
        # trust_env=False: sim y asr están en la misma red de planta; nunca salen por el proxy del enlace.
        self._http = httpx.Client(base_url=base_url, timeout=timeout_s, trust_env=False)

    def planta(self) -> dict:
        """Equipos, nombres informales, sensores y límites de alarma."""
        r = self._http.get("/api/planta")
        r.raise_for_status()
        return r.json()

    def estado(self) -> dict:
        r = self._http.get("/api/estado")
        r.raise_for_status()
        return r.json()

    def historian(
        self, tag: str, variable: str, desde: str | None = None, hasta: str | None = None
    ) -> list[dict]:
        """Historial de un sensor (una muestra por minuto)."""
        params = {k: v for k, v in (("desde", desde), ("hasta", hasta)) if v}
        r = self._http.get(f"/api/historian/{tag}/{variable}", params=params)
        r.raise_for_status()
        return r.json()

    def historial_ordenes(
        self, equipo: str | None = None, desde: str | None = None, hasta: str | None = None
    ) -> list[dict]:
        params = {k: v for k, v in (("equipo", equipo), ("desde", desde), ("hasta", hasta)) if v}
        r = self._http.get("/api/historial-ordenes", params=params)
        r.raise_for_status()
        return r.json()

    def descargar(self, ruta: str, timeout_s: float = 2.0, reintentos: int = 1) -> bytes:
        """Baja el audio o una foto. Timeout corto y un reintento: el camino de seguridad no puede esperar."""
        ultimo: Exception | None = None
        for intento in range(reintentos + 1):
            try:
                r = self._http.get(ruta, timeout=timeout_s)
                r.raise_for_status()
                return r.content
            except httpx.HTTPError as e:
                ultimo = e
                log.warning("descarga fallida de %s (intento %d): %s", ruta, intento + 1, e)
                if intento < reintentos:
                    time.sleep(0.2)
        assert ultimo is not None
        raise ultimo
