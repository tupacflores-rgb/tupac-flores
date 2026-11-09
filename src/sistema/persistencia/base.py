"""Acceso a PostgreSQL con conexiones cortas y reintentos.

Cada operación abre su conexión (la base está en la misma red y el volumen de mensajes es bajo). Así una
caída o un reinicio de la base no deja conexiones muertas en ningún worker.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime

import psycopg

from .esquema import ESQUEMA

log = logging.getLogger("sistema.db")


class Base:
    def __init__(self, url: str):
        self.url = url
        self.lista = threading.Event()

    def conectar(self, statement_timeout_ms: int | None = None) -> psycopg.Connection:
        opciones = {"autocommit": True, "connect_timeout": 3}
        if statement_timeout_ms:
            opciones["options"] = f"-c statement_timeout={int(statement_timeout_ms)}"
        return psycopg.connect(self.url, **opciones)

    def ejecutar(
        self, sql: str, params: tuple | dict | None = None, timeout_ms: int | None = None
    ) -> list[tuple]:
        with self.conectar(timeout_ms) as con:
            cur = con.execute(sql, params)
            return cur.fetchall() if cur.description else []

    def iniciar(self, parar: threading.Event, reintento_s: float = 1.0) -> None:
        """Aplica el esquema. Reintenta hasta que la base responda (puede arrancar después del agente)."""
        while not parar.is_set():
            try:
                with self.conectar() as con:
                    con.execute(ESQUEMA)
                self.lista.set()
                log.info("base lista")
                return
            except psycopg.Error as e:
                log.warning("base no disponible todavía: %s", str(e).strip().splitlines()[0] if str(e) else e)
                parar.wait(reintento_s)

    # -- notas --------------------------------------------------------------------------------------

    def registrar_nota(
        self, nota_id: str, ts: datetime, recorrida_id: str | None, audio_url: str | None, fotos: list[str]
    ) -> bool:
        """Guarda la nota. Devuelve False si ya estaba (entrega repetida del broker)."""
        filas = self.ejecutar(
            "INSERT INTO nota (nota_id, ts, recorrida_id, audio_url, fotos) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (nota_id) DO NOTHING RETURNING nota_id",
            (nota_id, ts, recorrida_id, audio_url, json.dumps(fotos)),
        )
        return bool(filas)

    # -- cola de salida (protocolo intención -> publicación -> marca) --------------------------------

    def encolar_salida(self, id_: str, topico: str, payload: dict, timeout_ms: int | None = None) -> bool:
        """Registra la intención de publicar. Devuelve False si el id ya existía."""
        filas = self.ejecutar(
            "INSERT INTO salida (id, topico, payload) VALUES (%s, %s, %s::jsonb) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (id_, topico, json.dumps(payload, ensure_ascii=False)),
            timeout_ms,
        )
        return bool(filas)

    def estado_salida(self, id_: str, timeout_ms: int | None = None) -> str | None:
        filas = self.ejecutar("SELECT estado FROM salida WHERE id = %s", (id_,), timeout_ms)
        return filas[0][0] if filas else None

    def marcar_publicada(self, id_: str, timeout_ms: int | None = None) -> None:
        self.ejecutar(
            "UPDATE salida SET estado = 'publicada', publicado = now() WHERE id = %s", (id_,), timeout_ms
        )

    def salidas_pendientes(self, limite: int = 50) -> list[tuple[str, str, dict]]:
        filas = self.ejecutar(
            "SELECT id, topico, payload FROM salida WHERE estado = 'a_publicar' ORDER BY creado LIMIT %s",
            (limite,),
        )
        return [(i, t, p) for i, t, p in filas]

    def registrar_respuesta(self, resp_id: str, mensaje_id: str, ts: datetime, texto: str) -> bool:
        filas = self.ejecutar(
            "INSERT INTO respuesta (id, mensaje_id, ts, texto) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING RETURNING id",
            (resp_id, mensaje_id, ts, texto),
        )
        return bool(filas)
