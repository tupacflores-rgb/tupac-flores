"""Partes confirmados que esperan sincronizarse con la nube.

La planta trabaja sin enlace (el ASR, el parte y las alertas son locales). Lo que se confirma mientras el
enlace está caído queda pendiente de sincronizar y se informa en el latido (`pendientes_sincronizar`); al
volver el enlace se vacía. Sin enlace caído nada queda pendiente.
"""

from __future__ import annotations

import logging
import threading

from .estado import EstadoCompartido

log = logging.getLogger("sistema.sync")


class Sincronizacion:
    def __init__(self, estado: EstadoCompartido):
        self._estado = estado
        self._pendientes: set[str] = set()
        self._lock = threading.Lock()

    def registrar(self, nota_id: str) -> None:
        """Un parte quedó confirmado. Si no hay enlace, queda pendiente."""
        with self._lock:
            if not self._estado.enlace_ok:
                self._pendientes.add(nota_id)
            self._estado.pendientes = len(self._pendientes)

    def sincronizar(self) -> int:
        """Con enlace, vacía lo pendiente. Devuelve cuántos partes se sincronizaron."""
        with self._lock:
            if not self._estado.enlace_ok or not self._pendientes:
                self._estado.pendientes = len(self._pendientes)
                return 0
            n = len(self._pendientes)
            self._pendientes.clear()
            self._estado.pendientes = 0
        log.info("enlace restablecido: %d partes sincronizados", n)
        return n

    def correr(self, parar: threading.Event) -> None:
        while not parar.is_set():
            self.sincronizar()
            parar.wait(2.0)
