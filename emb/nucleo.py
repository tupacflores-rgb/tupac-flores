"""Motor de embeddings: multilingual-e5-small en ONNX Runtime (CPU).

Se usa como indica su model card: `passage: ` delante de cada fragmento al indexar y `query: ` delante de
cada pregunta, mean pooling con la máscara de atención y vector normalizado (el coseno es el producto
interno). Límite de 512 tokens: los fragmentos largos se parten antes (indexar.py) conservando la cita.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

RUTA = Path(os.environ.get("MODELO_RUTA", "/modelos/e5"))
HILOS = int(os.environ.get("EMB_HILOS", "1"))
DIMENSION = 384
PREFIJOS = {"passage": "passage: ", "query": "query: "}


class Embedder:
    def __init__(self, ruta: Path = RUTA, hilos: int = HILOS):
        self.tok = Tokenizer.from_file(str(ruta / "onnx" / "tokenizer.json"))
        self.tok.enable_truncation(max_length=512)
        self.tok.enable_padding(pad_id=self.tok.token_to_id("<pad>") or 1, pad_token="<pad>")
        opciones = ort.SessionOptions()
        opciones.intra_op_num_threads = hilos
        opciones.inter_op_num_threads = 1
        self.sesion = ort.InferenceSession(
            str(ruta / "onnx" / "model.onnx"), opciones, providers=["CPUExecutionProvider"]
        )
        self._entradas = {i.name for i in self.sesion.get_inputs()}

    def embed(
        self, textos: list[str], tipo: str = "passage", prefijo: bool = True, lote: int = 16
    ) -> np.ndarray:
        """Matriz (n, 384) normalizada. `prefijo=False` solo para la ablación sin prefijos."""
        if tipo not in PREFIJOS:
            raise ValueError(f"tipo desconocido: {tipo}")
        pre = PREFIJOS[tipo] if prefijo else ""
        salida = []
        for i in range(0, len(textos), lote):
            enc = self.tok.encode_batch([pre + t for t in textos[i : i + lote]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mascara = np.array([e.attention_mask for e in enc], dtype=np.int64)
            entradas = {"input_ids": ids, "attention_mask": mascara}
            if "token_type_ids" in self._entradas:
                entradas["token_type_ids"] = np.zeros_like(ids)
            oculto = self.sesion.run(None, entradas)[0]  # (b, t, 384)
            m = mascara[:, :, None].astype(np.float32)
            medio = (oculto * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            salida.append(medio / np.clip(np.linalg.norm(medio, axis=1, keepdims=True), 1e-12, None))
        return np.vstack(salida).astype(np.float32)
