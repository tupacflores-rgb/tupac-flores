"""Supervisión interna de workers.

Docker no reinicia nuestros contenedores (el simulador los apaga y enciende para el corte de luz), así que
el proceso del agente se vigila por dentro: si un worker cae con una excepción o termina antes de tiempo,
se vuelve a lanzar. El latido corre en su propio hilo y nunca depende de otro worker.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable

log = logging.getLogger("sistema.supervisor")

Worker = Callable[[threading.Event], None]


class Supervisor:
    def __init__(self, espera_reinicio_s: float = 1.0):
        self.parar = threading.Event()
        self.reinicios: Counter[str] = Counter()
        self._espera = espera_reinicio_s
        self._hilos: dict[str, threading.Thread] = {}

    def lanzar(self, nombre: str, worker: Worker) -> None:
        """Lanza `worker(parar)` en un hilo y lo reinicia si cae o termina mientras no se pida parar."""

        def vigilado() -> None:
            while not self.parar.is_set():
                try:
                    worker(self.parar)
                    if not self.parar.is_set():
                        log.warning("worker %s terminó sin que se pidiera parar; se reinicia", nombre)
                except Exception:
                    log.exception("worker %s cayó; se reinicia", nombre)
                if self.parar.is_set():
                    break
                self.reinicios[nombre] += 1
                self.parar.wait(self._espera)

        hilo = threading.Thread(target=vigilado, name=nombre, daemon=True)
        self._hilos[nombre] = hilo
        hilo.start()

    def vivos(self) -> dict[str, bool]:
        return {n: h.is_alive() for n, h in self._hilos.items()}

    def detener(self, timeout: float = 5.0) -> None:
        self.parar.set()
        for hilo in self._hilos.values():
            hilo.join(timeout)
