"""Fotos de las notas: se descargan a un volumen local, con hash sha256, y quedan asociadas a su fila.

Cada nota trae `fotos` (rutas de la planta, `http://sim:8100{ruta}`). El anexo pide bajarlas, no solo
guardar la URL:

  - Se descargan en un hilo propio (`Fotos.correr`), nunca en el camino de la alerta ni del diálogo.
  - El archivo se guarda con el sha256 de su contenido como nombre (`<dir>/<ab>/<sha256>.<ext>`): la misma
    foto dos veces es un solo archivo, y el hash permite comprobar que no cambió. La escritura es atómica
    (archivo temporal + rename).
  - Cada descarga tiene timeout y reintentos. Si falla, **se guarda la URL y la foto queda `pendiente`**: se
    vuelve a intentar cada `espera_reintento_s` hasta que baje. Nunca se pierde la referencia ni se rompe
    la nota.
  - El estado de cada foto (url, nota, estado, sha256, ruta, intentos, último error) se persiste en la tabla
    `foto`; si la base no responde, sigue en memoria y se reintenta.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

log = logging.getLogger("sistema.fotos")

EXTENSIONES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


@dataclass(frozen=True)
class Foto:
    url: str
    nota_id: str
    estado: str = "pendiente"  # pendiente | descargada
    sha256: str | None = None
    ruta: str | None = None
    bytes: int | None = None
    intentos: int = 0
    error: str | None = None

    @property
    def descargada(self) -> bool:
        return self.estado == "descargada"

    def describir(self) -> str:
        """Cómo se muestra en el contexto de un Escalado."""
        if self.descargada and self.sha256:
            return f"{self.url} (sha256 {self.sha256[:12]}…, guardada)"
        return f"{self.url} (pendiente de descarga)"


class Fotos:
    def __init__(
        self,
        descargar: Callable[[str], bytes],
        directorio: str | Path,
        base=None,
        espera_reintento_s: float = 30.0,
        reloj: Callable[[], float] = time.monotonic,
    ):
        """`descargar(url)`: los bytes o levanta; trae su timeout y reintentos (PlantaApi.descargar)."""
        self._descargar = descargar
        self._dir = Path(directorio)
        self._base = base
        self._espera = espera_reintento_s
        self._reloj = reloj
        self._lock = threading.Lock()
        self._fotos: dict[str, Foto] = {}
        self._proximo: dict[str, float] = {}  # url -> cuándo se puede reintentar
        self._cola: queue.Queue[str] = queue.Queue()
        self._recuperado = False

    # -- alta ---------------------------------------------------------------------------------------

    def encolar(self, nota_id: str, urls: list[str]) -> None:
        """Registra las fotos de una nota como pendientes y las encola. No descarga: vuelve enseguida."""
        for url in dict.fromkeys(urls):
            with self._lock:
                if url in self._fotos:
                    continue
                self._fotos[url] = Foto(url, nota_id)
            self._persistir(self._fotos[url])
            self._cola.put(url)

    def info(self, url: str | None) -> Foto | None:
        if not url:
            return None
        with self._lock:
            return self._fotos.get(url)

    def descripcion(self, url: str | None) -> str | None:
        f = self.info(url)
        return f.describir() if f is not None else url

    def pendientes(self) -> list[Foto]:
        with self._lock:
            return [f for f in self._fotos.values() if not f.descargada]

    # -- descarga -----------------------------------------------------------------------------------

    def intentar(self, url: str) -> Foto:
        """Una pasada de descarga de una foto (con timeout y reintentos de `descargar`). Nunca levanta."""
        with self._lock:
            foto = self._fotos.get(url)
        if foto is None or foto.descargada:
            return foto if foto is not None else Foto(url, "?")
        try:
            contenido = self._descargar(url)
            if not contenido:
                raise ValueError("respuesta vacía")
            sha = hashlib.sha256(contenido).hexdigest()
            ruta = self._guardar(sha, url, contenido)
            nueva = replace(
                foto, estado="descargada", sha256=sha, ruta=str(ruta), bytes=len(contenido),
                intentos=foto.intentos + 1, error=None,
            )  # fmt: skip
            log.info("foto %s guardada (%d bytes, sha256 %s)", url, len(contenido), sha[:12])
        except Exception as e:  # sin red, timeout, disco: se guarda la URL y queda pendiente
            nueva = replace(
                foto, estado="pendiente", intentos=foto.intentos + 1, error=f"{type(e).__name__}: {e}"
            )
            log.warning("foto %s pendiente (intento %d): %s", url, nueva.intentos, nueva.error)
            with self._lock:
                self._proximo[url] = self._reloj() + self._espera
        with self._lock:
            self._fotos[url] = nueva
        self._persistir(nueva)
        return nueva

    def _guardar(self, sha: str, url: str, contenido: bytes) -> Path:
        ext = PurePosixPath(url.split("?")[0]).suffix.lower()
        ext = ext if ext in EXTENSIONES else ".bin"
        destino = self._dir / sha[:2] / f"{sha}{ext}"
        if destino.exists() and destino.stat().st_size == len(contenido):
            return destino  # la misma foto otra vez: un solo archivo
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_suffix(destino.suffix + f".{os.getpid()}.tmp")
        with open(temporal, "wb") as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporal, destino)
        return destino

    # -- worker -------------------------------------------------------------------------------------

    def recuperar(self) -> None:
        """Al arrancar (o cuando vuelve la base): retoma las fotos que quedaron pendientes."""
        if self._recuperado or self._base is None:
            return
        try:
            filas = self._base.fotos_pendientes()
        except Exception as e:
            log.warning("no se pudieron leer las fotos pendientes (%s); se reintenta", e)
            return
        self._recuperado = True
        for url, nota_id in filas:
            with self._lock:
                if url in self._fotos:
                    continue
                self._fotos[url] = Foto(url, nota_id)
            self._cola.put(url)

    def correr(self, parar: threading.Event) -> None:
        while not parar.is_set():
            self.recuperar()
            try:
                url = self._cola.get(timeout=1.0)
            except queue.Empty:
                self._reencolar_vencidas()
                continue
            with self._lock:
                espera = self._proximo.get(url, 0.0) - self._reloj()
            if espera > 0:
                self._cola.put(url)  # todavía no toca; vuelve a la cola sin descargar
                parar.wait(min(espera, 1.0))
                continue
            self.intentar(url)

    def _reencolar_vencidas(self) -> None:
        ahora = self._reloj()
        with self._lock:
            vencidas = [
                u for u, f in self._fotos.items() if not f.descargada and self._proximo.get(u, 0.0) <= ahora
            ]
        enlistadas = set(self._cola.queue)
        for u in vencidas:
            if u not in enlistadas:
                self._cola.put(u)

    # -- persistencia -------------------------------------------------------------------------------

    def _persistir(self, f: Foto) -> None:
        if self._base is None:
            return
        try:
            self._base.guardar_foto(
                f.url, f.nota_id, f.estado, f.sha256, f.ruta, f.bytes, f.intentos, f.error
            )
        except Exception as e:  # la base caída no frena la descarga: queda en memoria
            log.warning("no se pudo persistir la foto %s (%s)", f.url, e)
