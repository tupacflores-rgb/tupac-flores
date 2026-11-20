"""Cliente HTTP para análisis de fotos con el servicio de visión de NVIDIA."""

from __future__ import annotations

import base64
import logging
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("sistema.vision")
TIMEOUT_S = 15.0


class HallazgoVisual(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estado: Literal["completa", "pendiente"]
    severidad: str
    objeto: str | None = None
    descripcion: str | None = None
    respuesta: dict[str, object] | None = None
    error: str | None = None


class ClienteVision:
    def __init__(self, url: str, http: httpx.Client | None = None):
        if not url.strip():
            raise ValueError("NVIDIA_VISION_URL no puede estar vacía")
        self._url = url.strip()
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def analizar_imagen(self, bytes_imagen: bytes) -> HallazgoVisual:
        if not bytes_imagen:
            raise ValueError("bytes_imagen no puede estar vacío")

        payload = {"image_base64": base64.b64encode(bytes_imagen).decode("ascii")}
        try:
            response = self._http.post(self._url, json=payload, timeout=TIMEOUT_S)
            response.raise_for_status()
            datos = response.json()
            if not isinstance(datos, dict):
                raise ValueError("la respuesta JSON debe ser un objeto")

            severidad = datos.get("severidad", "desconocida")
            objeto = datos.get("objeto")
            descripcion = datos.get("descripcion")
            if not isinstance(severidad, str):
                raise ValueError("'severidad' debe ser texto")
            if objeto is not None and not isinstance(objeto, str):
                raise ValueError("'objeto' debe ser texto")
            if descripcion is not None and not isinstance(descripcion, str):
                raise ValueError("'descripcion' debe ser texto")

            return HallazgoVisual(
                estado="completa",
                severidad=severidad,
                objeto=objeto,
                descripcion=descripcion,
                respuesta=datos,
            )
        except httpx.TimeoutException:
            log.warning("análisis visual pendiente: timeout después de %.1f s", TIMEOUT_S)
            return self._pendiente(f"timeout_{TIMEOUT_S:g}s")
        except httpx.HTTPStatusError as exc:
            codigo = exc.response.status_code
            log.warning("análisis visual pendiente: NVIDIA respondió HTTP %d", codigo)
            return self._pendiente(f"http_{codigo}")
        except httpx.RequestError as exc:
            tipo = type(exc).__name__
            log.warning("análisis visual pendiente: error de transporte (%s)", tipo)
            return self._pendiente(tipo)
        except ValueError as exc:
            log.warning("análisis visual pendiente: respuesta inválida (%s)", exc)
            return self._pendiente("respuesta_json_invalida")

    @staticmethod
    def _pendiente(error: str) -> HallazgoVisual:
        return HallazgoVisual(estado="pendiente", severidad="desconocida", error=error)
