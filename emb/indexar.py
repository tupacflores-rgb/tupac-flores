"""Paso del build: calcula los embeddings de los pasajes y los deja en /modelos/pasajes.npz.

Cada sección del corpus es uno o más pasajes de hasta ~1.400 caracteres (entran en los 512 tokens del
modelo); los pasajes parten por párrafos y llevan el título delante. Todos conservan la cita de la sección.
Se calculan dos juegos: con el prefijo `passage: ` (el de producción) y sin prefijo (solo para la ablación).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from nucleo import Embedder

MAX_CARACTERES = 1400


def partir(titulo: str, texto: str) -> list[str]:
    cuerpo = texto.strip()
    if len(cuerpo) <= MAX_CARACTERES:
        return [cuerpo]
    partes, actual = [], ""
    for parrafo in cuerpo.split("\n"):
        if actual and len(actual) + len(parrafo) + 1 > MAX_CARACTERES:
            partes.append(actual)
            actual = f"{titulo}\n"
        actual += (parrafo + "\n") if actual else parrafo + "\n"
    if actual.strip():
        partes.append(actual)
    return [p.strip() for p in partes]


def main(fragmentos: Path, salida: Path) -> None:
    filas = [
        json.loads(linea) for linea in fragmentos.read_text(encoding="utf-8").splitlines() if linea.strip()
    ]
    pasajes, meta = [], []
    for f in filas:
        for n, parte in enumerate(partir(f.get("titulo", ""), f["texto"])):
            pasajes.append(parte)
            meta.append({"id": f"{f['id']}#{n}", "cita": f["id"]})
    emb = Embedder()
    con = emb.embed(pasajes, "passage", prefijo=True)
    sin = emb.embed(pasajes, "passage", prefijo=False)
    np.savez_compressed(salida, con_prefijo=con, sin_prefijo=sin)
    salida.with_suffix(".json").write_text(
        json.dumps({"pasajes": pasajes, "meta": meta}, ensure_ascii=False), encoding="utf-8"
    )
    print(f"{len(filas)} secciones -> {len(pasajes)} pasajes, dimensión {con.shape[1]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
