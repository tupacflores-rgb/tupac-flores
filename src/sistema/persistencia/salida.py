"""Cola de salida con el protocolo intención → publicación → marca (D17, Plan Maestro v1.2 §3).

1. **Intención**: `INSERT ... ON CONFLICT (id) DO NOTHING` con estado `a_publicar` y `statement_timeout` de
   300 ms. Si PostgreSQL no responde en ese plazo, la intención va al journal local (fsync) y se sigue: la
   alerta nunca espera a la base.
2. **Publicación** por QoS 1 esperando el acuse del broker (1 s, un reintento).
3. **Marca**: `publicada` con su hora.
4. **Recuperación al arrancar** (y periódica): se importa el journal a la base y se republica todo lo que
   sigue en `a_publicar` **con los mismos ids**. Entrega «al menos una vez»: puede haber dos publicaciones del
   mismo id, nunca dos mensajes distintos para la misma nota.

Los ids son deterministas (dependen solo de la nota, no de la hora ni de contadores), y la planta toma la
primera publicación de cada id.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import Protocol

from planta_contrato import EventoSistema, MensajeTablet, OrdenSistema, Topicos
from pydantic import BaseModel

from .journal import Journal

log = logging.getLogger("sistema.salida")

MODELOS: dict[str, type[BaseModel]] = {
    Topicos.EVENTOS: EventoSistema,
    Topicos.TABLET: MensajeTablet,
    Topicos.ORDENES: OrdenSistema,
}
TIMEOUT_DB_MS = 300


class AlmacenSalida(Protocol):
    def encolar_salida(self, id_: str, topico: str, payload: dict, timeout_ms: int | None = ...) -> bool: ...
    def estado_salida(self, id_: str, timeout_ms: int | None = ...) -> str | None: ...
    def marcar_publicada(self, id_: str, timeout_ms: int | None = ...) -> None: ...
    def salidas_pendientes(self, limite: int = ...) -> list[tuple[str, str, dict]]: ...


class Salida:
    def __init__(
        self,
        base: AlmacenSalida,
        journal: Journal,
        publicar: Callable[[str, BaseModel], bool],
    ):
        self._base = base
        self._journal = journal
        self._publicar = publicar
        self._en_vuelo: set[str] = set()
        self._lock = threading.Lock()

    # -- camino principal ---------------------------------------------------------------------------

    def emitir(self, id_: str, topico: str, modelo: BaseModel) -> bool:
        """Intención + publicación + marca de un mensaje. True si quedó publicado."""
        return self.emitir_lote([(id_, topico, modelo)])

    def emitir_lote(self, mensajes: list[tuple[str, str, BaseModel]]) -> bool:
        """Registra la intención de **todos** los mensajes del lote antes de publicar el primero.

        Si el proceso muere en medio, la recuperación los publica a todos (p. ej. la alerta y su aviso)."""
        ids = {m[0] for m in mensajes}
        with self._lock:
            self._en_vuelo |= ids
        try:
            a_publicar = [
                (id_, topico, modelo)
                for id_, topico, modelo in mensajes
                if self._intencion(id_, topico, json.loads(modelo.model_dump_json()))
            ]
            resultados = [self._publicar_y_marcar(id_, topico, modelo) for id_, topico, modelo in a_publicar]
            return all(resultados)
        finally:
            with self._lock:
                self._en_vuelo -= ids

    def _intencion(self, id_: str, topico: str, payload: dict) -> bool:
        """Registra la intención. Devuelve False si el id ya estaba y figura publicado."""
        try:
            nueva = self._base.encolar_salida(id_, topico, payload, TIMEOUT_DB_MS)
            if nueva:
                return True
            return self._base.estado_salida(id_, TIMEOUT_DB_MS) != "publicada"
        except Exception as e:
            log.warning("base no respondió a tiempo para la intención de %s (%s): va al journal", id_, e)
            self._journal.agregar("salida", {"id": id_, "topico": topico, "payload": payload})
            return True

    def _publicar_y_marcar(self, id_: str, topico: str, modelo: BaseModel) -> bool:
        try:
            publicado = self._publicar(topico, modelo)
        except Exception:
            log.exception("falló la publicación de %s", id_)
            publicado = False
        if not publicado:
            log.warning("%s no se pudo publicar; queda a_publicar para la recuperación", id_)
            return False
        try:
            self._base.marcar_publicada(id_, TIMEOUT_DB_MS)
        except Exception as e:
            # Quedó publicada pero sin marca: la recuperación la republica con el mismo id (al menos una vez).
            log.warning("%s publicada pero sin marca (%s)", id_, e)
        return True

    # -- recuperación -------------------------------------------------------------------------------

    def recuperar(self) -> int:
        """Importa el journal a la base y republica lo pendiente. Devuelve cuántos mensajes republicó."""
        extraidos = self._journal.extraer({"salida"})
        for i, r in enumerate(extraidos):
            d = r["datos"]
            try:
                self._base.encolar_salida(d["id"], d["topico"], d["payload"])
            except Exception:
                # La base sigue caída: lo que falta importar vuelve al journal y se reintenta más tarde.
                for pendiente in extraidos[i:]:
                    self._journal.agregar("salida", pendiente["datos"])
                return 0
        republicados = 0
        for id_, topico, payload in self._base.salidas_pendientes():
            with self._lock:
                if id_ in self._en_vuelo:
                    continue
            try:
                modelo = MODELOS[topico].model_validate(payload)
            except Exception:
                log.exception("la salida %s no valida contra el contrato; se omite", id_)
                continue
            if self._publicar_y_marcar(id_, topico, modelo):
                republicados += 1
        if republicados:
            log.info("recuperación: %d mensajes republicados", republicados)
        return republicados
