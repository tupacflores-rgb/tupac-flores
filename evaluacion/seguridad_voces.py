"""Valida la detección de seguridad contra las voces de referencia de la planta (102 audios).

Cada audio pasa por los dos carriles del ASR (seguridad = beam 1 con prompt de emergencia; completa = parámetros
normales) y por el léxico. Las voces traen una sola nota insegura (S1 de `fuga-sin-enlace`); el resto son notas
normales y no pueden disparar nada: cada alarma ahí es una falsa alarma.

    docker run --rm --network planta-lan -v <repo>:/work -v <voces>:/voces:ro -e PYTHONPATH=/work/src \
        tupac-flores-dev python /work/evaluacion/seguridad_voces.py --voces /voces --salida /work/evaluacion/corridas/seg.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from sistema.deteccion.seguridad import detectar

INSEGURAS = ("agua a presión", "agua a presion", "olor a gas", "sale humo", "sangrando", "olor a quemado")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voces", type=Path, required=True)
    ap.add_argument("--asr", default="http://asr:8000")
    ap.add_argument("--salida", type=Path, required=True)
    a = ap.parse_args()
    indice = json.loads((a.voces / "indice.json").read_text(encoding="utf-8"))
    http = httpx.Client(base_url=a.asr, timeout=300, trust_env=False)
    filas, falsas, perdidas = [], [], []
    for n, (id_, meta) in enumerate(indice.items(), 1):
        ruta = a.voces / f"{id_}.flac"
        if not ruta.exists():
            continue
        audio = ruta.read_bytes()
        insegura = any(p in meta["texto"].lower() for p in INSEGURAS)
        fila = {"id": id_, "esperada_insegura": insegura, "texto": meta["texto"]}
        for carril in ("seguridad", "completa"):
            r = http.post("/transcribir", params={"carril": carril}, content=audio)
            r.raise_for_status()
            d = r.json()
            det = detectar(d["texto"])
            fila[carril] = {
                "texto": d["texto"],
                "tiempo_s": d["tiempo_s"],
                "deteccion": det.codigo if det else None,
            }
        alerta = fila["seguridad"]["deteccion"] or fila["completa"]["deteccion"]
        if alerta and not insegura:
            falsas.append(fila)
        if insegura and not alerta:
            perdidas.append(fila)
        filas.append(fila)
        print(f"[{n}/{len(indice)}] {'INSEGURA' if insegura else 'normal  '} rápida={fila['seguridad']['deteccion']} "
              f"completa={fila['completa']['deteccion']}", flush=True)  # fmt: skip
    res = {"audios": len(filas), "inseguras": sum(f["esperada_insegura"] for f in filas),
           "falsas_alarmas": len(falsas), "alertas_perdidas": len(perdidas),
           "falsas": falsas, "perdidas": perdidas, "filas": filas}  # fmt: skip
    a.salida.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("filas", "falsas", "perdidas")}, indent=2))
    for f in falsas:
        print("FALSA:", f["id"], f["seguridad"]["texto"][:100], "|", f["completa"]["texto"][:100])


if __name__ == "__main__":
    main()
