"""Descarga el modelo ASR **en el build** con revisión fija y verifica su sha256 (Plan Maestro §5.1).

Genera `manifest.json` (id, revisión, sha256 y tamaño de cada archivo, licencia). En runtime no se descarga
nada: el servidor arranca con HF_HUB_OFFLINE=1 y `local_files_only=True`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = os.environ["MODELO_REPO"]
REVISION = os.environ["MODELO_REV"]
SHA256_MODEL_BIN = os.environ["MODELO_SHA256"]
DESTINO = Path(os.environ.get("MODELO_DESTINO", "/modelos/small"))
LICENCIA = os.environ.get("MODELO_LICENCIA", "mit")


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> None:
    snapshot_download(
        repo_id=REPO,
        revision=REVISION,
        local_dir=DESTINO,
        allow_patterns=["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
    )
    archivos = {
        p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
        for p in sorted(DESTINO.iterdir())
        if p.is_file() and p.name != "manifest.json"
    }
    obtenido = archivos["model.bin"]["sha256"]
    if obtenido != SHA256_MODEL_BIN:
        sys.exit(f"sha256 de model.bin NO coincide: esperado {SHA256_MODEL_BIN}, obtenido {obtenido}")
    (DESTINO / "manifest.json").write_text(
        json.dumps(
            {"id": REPO, "revision": REVISION, "licencia": LICENCIA, "local": True, "archivos": archivos},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"modelo {REPO}@{REVISION} verificado: model.bin sha256={obtenido}")


if __name__ == "__main__":
    main()
