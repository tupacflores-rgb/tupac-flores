"""Camino de una nota: descarga del audio → transcripción local → seguridad → (parte).

Dos pasadas con el mismo modelo, cada una en su carril del servicio `asr` y con su propio hilo aquí:

  - pasada de seguridad (beam 1, sin marcas de tiempo, prompt de emergencia): cola prioritaria, la nota más
    corta primero. Alerta si el léxico coincide.
  - pasada completa (parámetros normales, cola FIFO): da el texto del parte y vuelve a pasar el léxico.

La alerta sale con el primer acierto de cualquiera de las dos; el id `EV-SEG-{nota_id}` evita duplicados.
Sin LLM en ninguno de los dos caminos (Plan Maestro v1.2 §2).
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


class _Cola:
    """Cola de un carril. `corta_primero`: sale la más corta primero (con tope de espera); si no, FIFO."""

    def __init__(self, api: PlantaApi, corta_primero: bool):
        self._api = api
        self._corta = corta_primero
        self._items: list[_Pendiente] = []
        self._cv = threading.Condition()

    def poner(self, p: _Pendiente) -> None:
        with self._cv:
            self._items.append(p)
            self._cv.notify()

    def __len__(self) -> int:
        with self._cv:
            return len(self._items)

    def siguiente(self, espera_s: float = 0.5) -> _Pendiente | None:
        with self._cv:
            if not self._items and not self._cv.wait(timeout=espera_s):
                return None
            if not self._items:
                return None
            copia = list(self._items)
        if self._corta:
            self._medir(copia)
            ahora = time.monotonic()
            viejas = [p for p in copia if ahora - p.t_recibida > ESPERA_MAX_S]
            elegida = viejas[0] if viejas else min(copia, key=lambda p: (p.duracion, p.t_recibida))
        else:
            elegida = copia[0]
        with self._cv:
            try:
                self._items.remove(elegida)
            except ValueError:
                return None
        return elegida

    def _medir(self, items: list[_Pendiente]) -> None:
        """Bajar el audio es barato (LAN, <0,1 s) y da la duración sin transcribir."""
        for p in items:
            if p.duracion is None:
                try:
                    p.audio = self._api.descargar(p.nota.audio_url) if p.nota.audio_url else b""
                    p.duracion = duracion_wav(p.audio) if p.audio else 0.0
                except Exception as e:  # sin audio bajado se atiende al final
                    log.warning("no se pudo bajar el audio de %s para ordenar la cola: %s", p.nota.id, e)
                    p.duracion = float("inf")


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
        self._colas = {"seguridad": _Cola(api, True), "completa": _Cola(api, False)}
        self._lock = threading.Lock()
        self._alertadas: set[str] = set()  # notas con alerta ya emitida (la salida tampoco duplica por id)
        self._deteccion_rapida: dict[str, Deteccion] = {}

    def encolar(self, nota: NotaRecorrida) -> None:
        t = time.monotonic()
        for cola in self._colas.values():
            cola.poner(_Pendiente(nota, 1, t))

    def pendientes(self) -> int:
        return len(self._colas["completa"])

    def correr(self, parar: threading.Event) -> None:
        """Carril de la pasada completa (FIFO)."""
        self._correr_carril("completa", self.atender, parar)

    def correr_seguridad(self, parar: threading.Event) -> None:
        """Carril de la pasada rápida de seguridad (cola prioritaria, la nota más corta primero)."""
        self._correr_carril("seguridad", self.atender_seguridad, parar)

    def _correr_carril(self, carril: str, atender, parar: threading.Event) -> None:
        cola = self._colas[carril]
        while not parar.is_set():
            p = cola.siguiente()
            if p is None:
                continue
            try:
                atender(p.nota, p.t_recibida, p.audio)
            except Exception as e:
                if p.intento >= self._max_intentos:
                    log.error(
                        "nota %s (%s) sin procesar tras %d intentos: %s", p.nota.id, carril, p.intento, e
                    )
                    continue
                log.warning(
                    "nota %s (%s): intento %d fallido (%s); se reintenta", p.nota.id, carril, p.intento, e
                )
                parar.wait(min(2.0 * p.intento, 10.0))
                nuevo = _Pendiente(p.nota, p.intento + 1, p.t_recibida)
                nuevo.audio, nuevo.duracion = p.audio, p.duracion
                cola.poner(nuevo)

    # --- pasada de seguridad -------------------------------------------------------------------------------

    def atender_seguridad(
        self, nota: NotaRecorrida, t_recibida: float | None = None, audio: bytes | None = None
    ) -> None:
        t0 = t_recibida if t_recibida is not None else time.monotonic()
        texto = self._transcribir(nota, audio, "seguridad")
        d = detectar(texto, self._catalogo())
        if d is not None:
            with self._lock:
                self._deteccion_rapida[nota.id] = d
            self._alertar(nota, d, t0, "pasada rápida")

    # --- pasada completa -----------------------------------------------------------------------------------

    def atender(
        self, nota: NotaRecorrida, t_recibida: float | None = None, audio: bytes | None = None
    ) -> None:
        t0 = t_recibida if t_recibida is not None else time.monotonic()
        texto = self._transcribir(nota, audio, "completa")
        if texto:
            try:
                self._guardar(nota.id, texto)
            except Exception as e:
                log.warning("no se pudo guardar la transcripción de %s: %s", nota.id, e)
        self.procesar_texto(nota, texto, t0)

    def procesar_texto(self, nota: NotaRecorrida, texto: str, t_recibida: float | None = None) -> None:
        deteccion = detectar(texto, self._catalogo())
        with self._lock:
            rapida = self._deteccion_rapida.pop(nota.id, None)
        if deteccion is not None:
            self._alertar(nota, deteccion, t_recibida, "pasada completa")
        deteccion = deteccion or rapida
        if self.posterior is not None:
            self.posterior(nota, texto, deteccion)

    # --- común ---------------------------------------------------------------------------------------------

    def _transcribir(self, nota: NotaRecorrida, audio: bytes | None, carril: str) -> str:
        if not nota.audio_url:
            return ""
        t = time.monotonic()
        if not audio:
            audio = self._api.descargar(nota.audio_url)
        t_descarga = time.monotonic() - t
        r = self._asr.transcribir(audio, carril)
        log.info(
            "%s [%s]: descarga %.2f s · audio %.1f s · asr %.2f s · texto: %s",
            nota.id,
            carril,
            t_descarga,
            r.duracion_s,
            r.tiempo_s,
            r.texto,
        )
        return r.texto

    def _alertar(self, nota: NotaRecorrida, d: Deteccion, t_recibida: float | None, origen: str) -> None:
        with self._lock:
            if nota.id in self._alertadas:
                return
            self._alertadas.add(nota.id)
        ok = alertas.emitir(self._salida, nota, d)
        if not ok:  # que el otro carril lo vuelva a intentar
            with self._lock:
                self._alertadas.discard(nota.id)
        if t_recibida is not None:
            log.warning(
                "latencia de la alerta de %s: %.2f s desde la recepción (publicada=%s, %s)",
                nota.id,
                time.monotonic() - t_recibida,
                ok,
                origen,
            )
