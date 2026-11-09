"""Descarga multilingual-e5-small (ONNX) **en el build**, con revisión fija y sha256 verificado.

En runtime no se descarga nada: el servicio arranca con HF_HUB_OFFLINE=1.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = os.environ["MODELO_REPO"]
REVISION = os.environ["MODELO_REV"]
SHA256_ONNX = os.environ["MODELO_SHA256"]
DESTINO = Path(os.environ.get("MODELO_DESTINO", "/modelos/e5"))
ARCHIVOS = ["onnx/model.onnx", "onnx/tokenizer.json", "onnx/config.json"]


def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for a in ARCHIVOS:
        hf_hub_download(repo_id=REPO, revision=REVISION, filename=a, local_dir=DESTINO)
    onnx = DESTINO / "onnx" / "model.onnx"
    obtenido = sha256(onnx)
    if obtenido != SHA256_ONNX:
        sys.exit(f"sha256 de model.onnx NO coincide: esperado {SHA256_ONNX}, obtenido {obtenido}")
    (DESTINO / "manifest.json").write_text(
        json.dumps(
            {"id": REPO, "revision": REVISION, "licencia": "mit", "local": True,
             "archivos": {a: {"bytes": (DESTINO / a).stat().st_size} for a in ARCHIVOS},
             "sha256_model_onnx": obtenido},
            indent=2,
        ),
        encoding="utf-8",
    )  # fmt: skip
    print(f"modelo {REPO}@{REVISION} verificado: model.onnx sha256={obtenido}")


if __name__ == "__main__":
    main()
