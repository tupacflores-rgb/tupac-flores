"""Mide el WER del ASR sobre las voces de referencia de la planta y cómo escribe los números.

Se ejecuta dentro de un contenedor conectado a planta-lan (el servicio `asr` no publica puertos):

    docker run --rm --network planta-lan -v <repo>:/work -v <voces>:/voces:ro tupac-flores-dev \
        python evaluacion/asr_wer.py --voces /voces --salida /work/evaluacion/corridas/asr.json

Las voces y sus textos son datos oficiales de la planta: se leen desde Context/ y los resultados quedan en
evaluacion/corridas/ (ignorado por Git).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import unicodedata
from pathlib import Path

import httpx


def normalizar(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return t.split()


def distancia(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voces", type=Path, required=True)
    ap.add_argument("--asr", default="http://asr:8000")
    ap.add_argument("--salida", type=Path, required=True)
    ap.add_argument("--prompt", default=None, help="pisa el prompt inicial ('' = sin prompt)")
    ap.add_argument("--beam", type=int, default=None)
    ap.add_argument("--max", type=int, default=0)
    a = ap.parse_args()

    indice = json.loads((a.voces / "indice.json").read_text(encoding="utf-8"))
    http = httpx.Client(base_url=a.asr, timeout=300, trust_env=False)
    resultados, errores, palabras = [], 0, 0
    params = {k: v for k, v in (("prompt", a.prompt), ("beam", a.beam)) if v is not None}
    archivos = [(i, m) for i, m in indice.items() if (a.voces / f"{i}.flac").exists()]
    if a.max:
        archivos = archivos[: a.max]
    for n, (id_, meta) in enumerate(archivos, 1):
        r = http.post("/transcribir", params=params, content=(a.voces / f"{id_}.flac").read_bytes())
        r.raise_for_status()
        d = r.json()
        ref, hip = normalizar(meta["texto"]), normalizar(d["texto"])
        e = distancia(ref, hip)
        errores += e
        palabras += len(ref)
        resultados.append(
            {
                "id": id_,
                "escenario": meta.get("escenario"),
                "nota_id": meta.get("nota_id"),
                "referencia": meta["texto"],
                "hipotesis": d["texto"],
                "errores": e,
                "palabras": len(ref),
                "duracion_s": d["duracion_s"],
                "tiempo_s": d["tiempo_s"],
                "rtf": d["rtf"],
            }
        )
        print(
            f"[{n}/{len(archivos)}] WER {e / max(len(ref), 1):.0%} RTF {d['rtf']:.2f} | {d['texto'][:90]}",
            flush=True,
        )

    rtf = [r["rtf"] for r in resultados]
    con_digitos = sum(bool(re.search(r"\d", r["hipotesis"])) for r in resultados)
    resumen = {
        "audios": len(resultados),
        "wer_global": round(errores / max(palabras, 1), 4),
        "rtf_medio": round(statistics.mean(rtf), 3),
        "rtf_p95": round(sorted(rtf)[int(len(rtf) * 0.95) - 1], 3),
        "rtf_max": round(max(rtf), 3),
        "hipotesis_con_digitos": con_digitos,
        "duracion_max_s": max(r["duracion_s"] for r in resultados),
        "parametros": params,
    }
    a.salida.parent.mkdir(parents=True, exist_ok=True)
    a.salida.write_text(
        json.dumps({"resumen": resumen, "resultados": resultados}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(json.dumps(resumen, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
