"""Latido `sistema/estado` cada 30 s.

Corre en su propio hilo. Solo lee estado en memoria y publica: no toca la base, ni el ASR, ni la red de
planta más allá de MQTT. Si publicar falla, lo intenta de nuevo en el próximo ciclo; nunca termina.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from planta_contrato import EstadoSistema

from .config import Config
from .estado import EstadoCompartido, RelojPlanta

log = logging.getLogger("sistema.latido")


class Latido:
    def __init__(
        self,
        config: Config,
        publicar: Callable[[EstadoSistema], None],
        estado: EstadoCompartido,
        reloj: RelojPlanta,
        listo: Callable[[], bool],
        monotonico: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self._publicar = publicar
        self._estado = estado
        self._reloj = reloj
        self._listo = listo
        self._mono = monotonico
        self.emitidos = 0

    def mensaje(self) -> EstadoSistema:
        return EstadoSistema(
            ts=self._reloj.ahora(),
            sistema=self.config.sistema,
            enlace_ok=self._estado.enlace_ok,
            pendientes_sincronizar=self._estado.pendientes,
        )

    def emitir(self) -> bool:
        try:
            self._publicar(self.mensaje())
        except Exception:
            log.exception("no se pudo publicar el latido")
            return False
        self.emitidos += 1
        return True

    def correr(self, parar: threading.Event) -> None:
        # El primer latido sale cuando el sistema está listo, o igual pasado el máximo de arranque.
        inicio = self._mono()
        while not parar.is_set():
            if self._listo():
                break
            if self._mono() - inicio >= self.config.latido_arranque_max_s:
                log.warning(
                    "el sistema no está listo tras %.0f s: el latido arranca igual",
                    self.config.latido_arranque_max_s,
                )
                break
            parar.wait(0.25)
        while not parar.is_set():
            self.emitir()
            parar.wait(self.config.latido_s)
