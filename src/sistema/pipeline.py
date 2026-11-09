"""Camino de una nota: descarga del audio → transcripción local → seguridad → (parte).

Un solo worker y sin LLM en el camino de la alerta: con la cuota propia del contenedor `asr`, la latencia de
una condición insegura es descarga + transcripción + léxico + publicación (Plan Maestro v1.2 §2).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from planta_contrato import NotaRecorrida

from .deteccion import alertas
from .deteccion.seguridad import Deteccion, detectar
from .dominio.catalogo import Catalogo
from .integraciones.asr import ClienteAsr
from .integraciones.planta_api import PlantaApi
from .persistencia.salida import Salida

log = logging.getLogger("sistema.pipeline")

Posterior = Callable[[NotaRecorrida, str, Deteccion | None], None]


class Pipeline:
    def __init__(
        self,
        api: PlantaApi,
        asr: ClienteAsr,
        guardar_transcripcion: Callable[[str, str], None],
        salida: Salida,
        catalogo: Callable[[], Catalogo | None],
        posterior: Posterior | None = None,
        max_intentos: int = 6,
    ):
        self._api = api
        self._asr = asr
        self._guardar = guardar_transcripcion
        self._salida = salida
        self._catalogo = catalogo
        self.posterior = posterior
        self._max_intentos = max_intentos
        self._cola: queue.Queue[tuple[NotaRecorrida, int, float]] = queue.Queue()

    def encolar(self, nota: NotaRecorrida) -> None:
        self._cola.put((nota, 1, time.monotonic()))

    def pendientes(self) -> int:
        return self._cola.qsize()

    def correr(self, parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                nota, intento, t_recibida = self._cola.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.atender(nota, t_recibida)
            except Exception as e:
                if intento >= self._max_intentos:
                    log.error("nota %s sin procesar tras %d intentos: %s", nota.id, intento, e)
                    continue
                log.warning("nota %s: intento %d fallido (%s); se reintenta", nota.id, intento, e)
                parar.wait(min(2.0 * intento, 10.0))
                self._cola.put((nota, intento + 1, t_recibida))

    def atender(self, nota: NotaRecorrida, t_recibida: float | None = None) -> None:
        t0 = t_recibida if t_recibida is not None else time.monotonic()
        texto = self.transcribir(nota)
        self.procesar_texto(nota, texto, t0)

    def transcribir(self, nota: NotaRecorrida) -> str:
        if not nota.audio_url:
            return ""
        t = time.monotonic()
        audio = self._api.descargar(nota.audio_url)
        t_descarga = time.monotonic() - t
        r = self._asr.transcribir(audio)
        log.info(
            "%s: descarga %.2f s · audio %.1f s · asr %.2f s · texto: %s",
            nota.id,
            t_descarga,
            r.duracion_s,
            r.tiempo_s,
            r.texto,
        )
        try:
            self._guardar(nota.id, r.texto)
        except Exception as e:
            log.warning("no se pudo guardar la transcripción de %s: %s", nota.id, e)
        return r.texto

    def procesar_texto(self, nota: NotaRecorrida, texto: str, t_recibida: float | None = None) -> None:
        deteccion = detectar(texto, self._catalogo())
        if deteccion is not None:
            ok = alertas.emitir(self._salida, nota, deteccion)
            if t_recibida is not None:
                log.warning(
                    "latencia de la alerta de %s: %.2f s desde la recepción (publicada=%s)",
                    nota.id,
                    time.monotonic() - t_recibida,
                    ok,
                )
        if self.posterior is not None:
            self.posterior(nota, texto, deteccion)
