"""Estado compartido entre hilos: reloj de la planta, enlace y pendientes.

Todo se lee sin I/O para que el latido nunca dependa de la base, de la red ni de otro worker.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime


class RelojPlanta:
    """Último instante simulado conocido. La planta tiene su propio reloj: no se usa el de esta máquina."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ts: datetime | None = None

    def observar(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        with self._lock:
            if self._ts is None or ts > self._ts:
                self._ts = ts

    def fijar(self, ts: datetime) -> None:
        """Fija el reloj aunque retroceda (la planta puede cambiar de escenario)."""
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        with self._lock:
            self._ts = ts

    def conocido(self) -> bool:
        with self._lock:
            return self._ts is not None

    def ahora(self) -> datetime:
        with self._lock:
            return self._ts if self._ts is not None else datetime.now(UTC)


class EstadoCompartido:
    """Lo que informa el latido: enlace y trabajo pendiente de sincronizar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enlace_ok = True
        self._pendientes = 0
        # Se activa cuando cambia el enlace o los pendientes: el latido sale enseguida, sin esperar los 30 s.
        self.cambio = threading.Event()

    @property
    def enlace_ok(self) -> bool:
        with self._lock:
            return self._enlace_ok

    @enlace_ok.setter
    def enlace_ok(self, valor: bool) -> None:
        with self._lock:
            cambio = valor != self._enlace_ok
            self._enlace_ok = valor
        if cambio:
            self.cambio.set()

    @property
    def pendientes(self) -> int:
        with self._lock:
            return self._pendientes

    @pendientes.setter
    def pendientes(self, valor: int) -> None:
        with self._lock:
            valor = max(0, valor)
            cambio = valor != self._pendientes
            self._pendientes = valor
        if cambio:
            self.cambio.set()
