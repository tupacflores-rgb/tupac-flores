"""Genera los casos del benchmark del LLM de respaldo: notas del `OperadorVirtual` real + variantes de ASR reales.

Cada caso lleva el texto limpio, el texto «como lo oye el ASR» y las filas esperadas (la verdad de campo). Las
deformaciones son las que se vieron en el corrector y en las voces de referencia: «A uno» por «la uno», «frito» por
«filtro», palabras pegadas («estaparada», «dedescarga»), «diezp psi», comillas del transcriptor, artículo comido.

Se ejecuta en la imagen de la planta (trae planta_sim):

    docker run --rm -v <repo>:/work -v "<repo>/Context/Clon del REPO:/oficial:ro" planta-sim \
        python /work/evaluacion/llm_casos.py --casos 30 --salida /work/evaluacion/corridas/llm_casos.json
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path[:0] = ["/oficial/planta/sim"]

import yaml  # noqa: E402
from planta_sim.modelo import Planta  # noqa: E402
from planta_sim.operador import OperadorVirtual  # noqa: E402

DEFINICION = yaml.safe_load(Path("/oficial/datos/planta.yaml").read_text(encoding="utf-8"))
TS = datetime(2026, 3, 4, 7, 0, tzinfo=UTC)

# (nombre, función): cada una devuelve el texto deformado o el mismo si no aplica.
DEFORMACIONES = [
    ("a_uno", lambda t: re.sub(r"(^|[.] )La (uno|dos|tres)\b", r"\1A \2", t, count=1)),
    ("frito", lambda t: re.sub(r"[Ff]iltro", "frito", t)),
    ("estaparada", lambda t: t.replace("está parada", "estaparada").replace("esta parada", "estaparada")),
    ("dedescarga", lambda t: t.replace("de descarga", "dedescarga")),
    ("sin_tildes_ni_comas", lambda t: re.sub(r"[,.]", "", t).lower()),
    ("diezp", lambda t: re.sub(r"\b(diez|veinte|treinta) psi", r"\1p psi", t)),
    ("pegadas", lambda t: re.sub(r"\b([Ll]a|[Ee]l) (uno|dos|tres|filtro)\b", r"\1\2", t, count=1)),
    ("golpe_teo", lambda t: t.replace("golpeteo", "golpe teo")),
]


def planta_al_azar(rng: random.Random) -> Planta:
    p = Planta.desde_definicion(DEFINICION, rng, TS)
    for b in p.bombas.values():
        b.estado = rng.choices(["marcha", "parada", "mantenimiento"], [8, 2, 1])[0]
        b.pd = rng.uniform(96, 124)
        b.goteo = rng.choice([0, 0, rng.uniform(21, 80), rng.uniform(121, 300)])
        b.t_rodamiento = rng.uniform(45, 95)
        b.ruido = rng.choice(["normal", "normal", "golpeteo", "rodamiento"])
    for f in p.filtros.values():
        f.dp = rng.uniform(0.2, 1.9)
    p.nivel = rng.uniform(3, 7.5)
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--casos", type=int, default=30)
    ap.add_argument("--semilla", type=int, default=11)
    ap.add_argument("--salida", type=Path, required=True)
    a = ap.parse_args()
    casos, i = [], 0
    while len(casos) < a.casos:
        rng = random.Random(a.semilla * 1000 + i)
        operador = OperadorVirtual(random.Random(a.semilla * 7 + i), random.Random(a.semilla * 13 + i))
        operador.contador = i * 100
        i += 1
        for nota in operador.recorrida(planta_al_azar(rng)):
            esperado = nota.esperado()["items"]
            if (
                not esperado
                or nota.ambiguedades
                or any(it.get("variable") == "condicion_insegura" for it in esperado)
            ):
                continue  # las ambiguas («la bomba») se resuelven preguntando, no con el LLM
            # Un tercio queda sin deformar (el LLM no tiene que estropear lo que está bien); al resto se le aplica
            # la primera deformación (rotando) que efectivamente cambie el texto.
            deformada, nombre = nota.texto, "limpio"
            if len(casos) % 3 != 2:
                for k in range(len(DEFORMACIONES)):
                    n_, f_ = DEFORMACIONES[(len(casos) + k) % len(DEFORMACIONES)]
                    if f_(nota.texto) != nota.texto:
                        deformada, nombre = f_(nota.texto), n_
                        break
            casos.append(
                {
                    "id": nota.id,
                    "variante": nombre,
                    "texto_limpio": nota.texto,
                    "texto_asr": deformada,
                    "esperado": [
                        {k: it.get(k) for k in ("tag", "variable", "valor", "unidad", "observacion")}
                        for it in esperado
                    ],
                }
            )
            if len(casos) >= a.casos:
                break
    a.salida.write_text(json.dumps(casos, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(casos)} casos -> {a.salida}")
    for c in casos[:6]:
        print(f"  [{c['variante']}] {c['texto_asr'][:110]}")


if __name__ == "__main__":
    main()
