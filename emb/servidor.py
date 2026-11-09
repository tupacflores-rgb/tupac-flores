"""Servicio de embeddings y búsqueda densa (multilingual-e5-small + pgvector).

  GET  /salud    200 solo con el modelo cargado y los pasajes cargados en pgvector
  POST /embed    {"textos": [...], "tipo": "query"|"passage", "prefijo": true} -> vectores normalizados
  POST /buscar   {"pregunta": "...", "k": 10, "prefijos": true, "incluir_vencidos": false}
                 -> citas únicas ordenadas por similitud coseno, con vigencia y aplicabilidad

Los embeddings de los pasajes se calculan en el build (indexar.py) y se cargan en pgvector al arrancar. Esta
carga es idempotente: si la tabla ya tiene el mismo juego (misma huella), no se vuelve a escribir.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np
import psycopg
from corpus import cargar
from fastapi import FastAPI, HTTPException
from nucleo import Embedder
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s emb: %(message)s")
log = logging.getLogger("emb")

DATABASE_URL = os.environ["DATABASE_URL"]
PASAJES = Path(os.environ.get("PASAJES", "/modelos/pasajes.npz"))
DATOS = Path(os.environ.get("DATOS", "/datos/documentos"))

app = FastAPI(title="Embeddings")
_emb: Embedder | None = None
_listo = threading.Event()
_un_trabajo = threading.Lock()

ESQUEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS fragmento_vec (
    id           text PRIMARY KEY,
    cita         text NOT NULL,
    documento    text NOT NULL,
    seccion      text NOT NULL,
    titulo       text NOT NULL,
    fabricante   text NOT NULL,
    equipos      text[] NOT NULL DEFAULT '{}',
    vigente      boolean NOT NULL,
    aplica       boolean NOT NULL,
    huella       text NOT NULL,
    embedding    vector(384) NOT NULL,
    embedding_sp vector(384) NOT NULL
);
CREATE INDEX IF NOT EXISTS fragmento_vec_cita ON fragmento_vec (cita);
"""

INSERTAR = (
    "INSERT INTO fragmento_vec (id, cita, documento, seccion, titulo, fabricante, equipos, vigente, aplica, "
    "huella, embedding, embedding_sp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s::vector)"
)


def _vector(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def _cargar_pgvector() -> None:
    datos = np.load(PASAJES)
    pasajes = json.loads(PASAJES.with_suffix(".json").read_text(encoding="utf-8"))
    huella = hashlib.sha256(PASAJES.read_bytes()).hexdigest()[:16]
    por_cita = {f.cita: f for f in cargar(DATOS)}
    while True:
        try:
            with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=3) as con:
                con.execute(ESQUEMA)
                n, h = con.execute("SELECT count(*), min(huella) FROM fragmento_vec").fetchone()
                if n == len(pasajes["meta"]) and h == huella:
                    log.info("pgvector ya tiene los %d pasajes (huella %s)", n, huella)
                    return
                con.execute("TRUNCATE fragmento_vec")
                with con.cursor() as cur:
                    for i, m in enumerate(pasajes["meta"]):
                        f = por_cita[m["cita"]]
                        cur.execute(
                            INSERTAR,
                            (
                                m["id"], f.cita, f.documento, f.seccion, f.titulo,
                                f.fabricante, list(f.equipos), f.vigente, f.aplica, huella,
                                _vector(datos["con_prefijo"][i]), _vector(datos["sin_prefijo"][i]),
                            ),
                        )  # fmt: skip
                log.info("cargados %d pasajes en pgvector (huella %s)", len(pasajes["meta"]), huella)
                return
        except psycopg.Error as e:
            log.warning("base no disponible todavía: %s", str(e).strip().splitlines()[0] if str(e) else e)
            time.sleep(1.0)


def _arrancar() -> None:
    global _emb
    t0 = time.perf_counter()
    _emb = Embedder()
    _emb.embed(["calentamiento"], "query")
    _cargar_pgvector()
    _listo.set()
    log.info("modelo cargado y pasajes en pgvector en %.1f s", time.perf_counter() - t0)


@app.on_event("startup")
def _inicio() -> None:
    threading.Thread(target=_arrancar, name="arranque", daemon=True).start()


class PedidoEmbed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    textos: list[str] = Field(min_length=1, max_length=64)
    tipo: str = "query"
    prefijo: bool = True


class PedidoBuscar(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pregunta: str = Field(min_length=2, max_length=500)
    k: int = Field(default=10, ge=1, le=50)
    prefijos: bool = True
    incluir_vencidos: bool = False


@app.get("/salud")
def salud() -> dict:
    if not _listo.is_set():
        raise HTTPException(503, "cargando")
    return {"ok": True}


@app.post("/embed")
def embed(p: PedidoEmbed) -> dict:
    if not _listo.is_set():
        raise HTTPException(503, "cargando")
    with _un_trabajo:
        v = _emb.embed(p.textos, p.tipo, p.prefijo)
    return {"vectores": v.tolist()}


@app.post("/buscar")
def buscar(p: PedidoBuscar) -> dict:
    if not _listo.is_set():
        raise HTTPException(503, "cargando")
    t0 = time.perf_counter()
    with _un_trabajo:
        q = _emb.embed([p.pregunta], "query", p.prefijos)[0]
    columna = "embedding" if p.prefijos else "embedding_sp"
    vec = _vector(q)
    with psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=3) as con:
        filas = con.execute(
            f"SELECT cita, 1 - ({columna} <=> %s::vector) AS coseno, vigente, aplica FROM fragmento_vec "
            f"WHERE (%s OR vigente) ORDER BY {columna} <=> %s::vector LIMIT %s",
            (vec, p.incluir_vencidos, vec, p.k * 4),
        ).fetchall()
    vistas, resultados = set(), []
    for cita, coseno, vigente, aplica in filas:  # una cita puede tener varios pasajes: queda el mejor
        if cita in vistas:
            continue
        vistas.add(cita)
        resultados.append(
            {"cita": cita, "coseno": round(float(coseno), 4), "vigente": vigente, "aplica": aplica}
        )
        if len(resultados) == p.k:
            break
    return {"resultados": resultados, "ms": round((time.perf_counter() - t0) * 1000)}
