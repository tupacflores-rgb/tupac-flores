"""Camino de una nota: descarga del audio → transcripción local → seguridad → (parte).

Un solo worker y sin LLM en el camino de la alerta: con la cuota propia del contenedor `asr`, la latencia de
una condición insegura es descarga + transcripción + léxico + publicación (Plan Maestro v1.2 §2).
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from collections.abc import Callable

from planta_contrato import NotaRecorrida

from .deteccion import alertas
from .deteccion.seguridad import Deteccion, detectar
from .dominio.catalogo import Catalogo
from .integraciones.asr import ClienteAsr
from .integraciones.planta_api import PlantaApi
from .persistencia.salida import Salida

log = logging.getLogger("sistema.pipeline")

# Una nota que espera más que esto pasa primero aunque sea larga: la prioridad por duración no puede dejar sin
# atender a una nota normal.
ESPERA_MAX_S = 90.0


def duracion_wav(audio: bytes) -> float:
    """Segundos de un WAV a partir de su cabecera; infinito si no se puede leer (va al final de la cola)."""
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return float("inf")


class _Pendiente:
    __slots__ = ("nota", "intento", "t_recibida", "audio", "duracion")

    def __init__(self, nota: NotaRecorrida, intento: int, t_recibida: float):
        self.nota, self.intento, self.t_recibida = nota, intento, t_recibida
        self.audio: bytes | None = None
        self.duracion: float | None = None


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
        self._cola: list[_Pendiente] = []
        self._cv = threading.Condition()

    def encolar(self, nota: NotaRecorrida) -> None:
        with self._cv:
            self._cola.append(_Pendiente(nota, 1, time.monotonic()))
            self._cv.notify()

    def pendientes(self) -> int:
        with self._cv:
            return len(self._cola)

    def _siguiente(self, espera_s: float = 0.5) -> _Pendiente | None:
        """La nota más corta de la cola (las inseguras se dictan cortas), salvo que una lleve más de
        ESPERA_MAX_S esperando. Bajar el audio es barato (LAN, <0,1 s) y da la duración sin transcribir."""
        with self._cv:
            if not self._cola and not self._cv.wait(timeout=espera_s):
                return None
            if not self._cola:
                return None
            copia = list(self._cola)
        for p in copia:
            if p.duracion is None:
                try:
                    p.audio = self._api.descargar(p.nota.audio_url) if p.nota.audio_url else b""
                    p.duracion = duracion_wav(p.audio) if p.audio else 0.0
                except Exception as e:  # sin audio bajado se atiende en orden de llegada
                    log.warning("no se pudo bajar el audio de %s para ordenar la cola: %s", p.nota.id, e)
                    p.duracion = float("inf")
        ahora = time.monotonic()
        viejas = [p for p in copia if ahora - p.t_recibida > ESPERA_MAX_S]
        elegida = viejas[0] if viejas else min(copia, key=lambda p: (p.duracion, p.t_recibida))
        with self._cv:
            try:
                self._cola.remove(elegida)
            except ValueError:
                return None
        return elegida

    def correr(self, parar: threading.Event) -> None:
        while not parar.is_set():
            p = self._siguiente()
            if p is None:
                continue
            nota, intento, t_recibida = p.nota, p.intento, p.t_recibida
            try:
                self.atender(nota, t_recibida, p.audio)
            except Exception as e:
                if intento >= self._max_intentos:
                    log.error("nota %s sin procesar tras %d intentos: %s", nota.id, intento, e)
                    continue
                log.warning("nota %s: intento %d fallido (%s); se reintenta", nota.id, intento, e)
                parar.wait(min(2.0 * intento, 10.0))
                with self._cv:
                    reintento = _Pendiente(nota, intento + 1, t_recibida)
                    reintento.audio, reintento.duracion = p.audio, p.duracion
                    self._cola.append(reintento)
                    self._cv.notify()

    def atender(
        self, nota: NotaRecorrida, t_recibida: float | None = None, audio: bytes | None = None
    ) -> None:
        t0 = t_recibida if t_recibida is not None else time.monotonic()
        texto = self.transcribir(nota, audio)
        self.procesar_texto(nota, texto, t0)

    def transcribir(self, nota: NotaRecorrida, audio: bytes | None = None) -> str:
        if not nota.audio_url:
            return ""
        t = time.monotonic()
        if not audio:
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
