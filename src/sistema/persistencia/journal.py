"""Journal local en disco (JSON por línea, con fsync).

Es el respaldo cuando PostgreSQL no responde: lo que importa (una nota recibida, la intención de publicar una
alerta) se anota acá antes de seguir, y se importa a la base al recuperarla. Vive en un volumen del agente.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("sistema.journal")


class Journal:
    def __init__(self, carpeta: str | Path):
        self.carpeta = Path(carpeta)
        self.ruta = self.carpeta / "journal.jsonl"
        self._lock = threading.Lock()

    def agregar(self, tipo: str, datos: dict) -> None:
        linea = json.dumps({"tipo": tipo, "datos": datos}, ensure_ascii=False, default=str)
        with self._lock:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(linea + "\n")
                f.flush()
                os.fsync(f.fileno())

    def leer(self, tipo: str | None = None) -> list[dict]:
        with self._lock:
            if not self.ruta.exists():
                return []
            registros = []
            for n, linea in enumerate(self.ruta.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    r = json.loads(linea)
                except json.JSONDecodeError:
                    log.warning("línea %d del journal ilegible (escritura cortada); se ignora", n)
                    continue
                if tipo is None or r.get("tipo") == tipo:
                    registros.append(r)
            return registros

    def reemplazar(self, restantes: list[dict]) -> None:
        """Deja solo `restantes` (escritura atómica). Se usa al terminar de importar a la base."""
        with self._lock:
            self.carpeta.mkdir(parents=True, exist_ok=True)
            tmp = self.ruta.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in restantes:
                    f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.ruta)
