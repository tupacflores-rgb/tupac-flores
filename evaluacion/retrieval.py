"""Evalúa el recuperador contra la suite de 71 preguntas: BM25 oficial, léxico propio, denso, híbrido y la ablación
sin prefijos de e5 (`passage: ` / `query: `).

    # léxico solo (no necesita el servicio de embeddings)
    python evaluacion/retrieval.py --docs "Context/Clon del REPO"

    # completo, desde un contenedor en planta-lan con el servicio `emb` levantado
    docker run --rm --network planta-lan -v <repo>:/work -v "<clon oficial>:/oficial:ro" -e PYTHONPATH=/work/src \
        tupac-flores-dev python /work/evaluacion/retrieval.py --docs /oficial --emb http://emb:8000 \
        --json /work/docs/resultados/retrieval.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from sistema.conocimiento.corpus import cargar  # noqa: E402
from sistema.conocimiento.hibrido import ClienteEmb, RecuperadorHibrido  # noqa: E402
from sistema.conocimiento.recuperador import Recuperador  # noqa: E402


def _rev_c_antes_que_d(buscar, suite) -> int:
    n = 0
    for q in suite:
        top = buscar(q["pregunta"], 5)
        c = next((i for i, x in enumerate(top) if x.startswith("TX350-C")), None)
        d = next((i for i, x in enumerate(top) if x.startswith("TX350-D")), None)
        if c is not None and (d is None or c < d):
            n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", type=Path, required=True, help="clon del repo oficial (con planta/ y datos/)")
    ap.add_argument("--emb", help="URL del servicio de embeddings (habilita denso, híbrido y la ablación)")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(a.docs / "planta"))
    from corpus import evaluar as ev  # type: ignore

    base = a.docs / "datos" / "documentos"
    suite, frags_of = ev.cargar(base)
    ks = (1, 3, 5, 10)
    lexico = Recuperador(cargar(base))
    variantes = {"BM25 oficial": ev.BM25(frags_of).buscar, "léxico (propio)": lambda q, k: lexico.citas(q, k)}
    latencia_ms: dict[str, float] = {}
    if a.emb:
        emb = ClienteEmb(a.emb, timeout_s=60)
        for prefijos, etiqueta in ((True, "con prefijos"), (False, "SIN prefijos")):
            hib = RecuperadorHibrido(lexico, emb, prefijos=prefijos)
            variantes[f"denso {etiqueta}"] = lambda q, k, h=hib: h.denso(q, k)
            variantes[f"híbrido {etiqueta}"] = lambda q, k, h=hib: h.citas(q, k)
    resultados = {}
    for nombre, f in variantes.items():
        t0 = time.perf_counter()
        resultados[nombre] = ev.evaluar(f, suite, ks)
        latencia_ms[nombre] = round((time.perf_counter() - t0) / len(suite) * 1000, 1)
        resultados[nombre]["rev_c_antes_que_d"] = _rev_c_antes_que_d(f, suite)
    metricas = ["mrr", "hit@1", "hit@5", "recall@5", "recall@10", "distractor@5"]
    print(f"{'variante':24}" + "".join(f"{m:>13}" for m in metricas) + f"{'Rev.C<D':>9}{'ms/preg':>9}")
    for nombre, r in resultados.items():
        print(
            f"{nombre:24}"
            + "".join(f"{r['total'][m]:>13}" for m in metricas)
            + f"{r['rev_c_antes_que_d']:>9}{latencia_ms[nombre]:>9}"
        )
    if a.json:
        a.json.write_text(
            json.dumps(
                {
                    n: {"total": r["total"], "rev_c_antes_que_d": r["rev_c_antes_que_d"],
                        "por_tipo": r["por_tipo"], "ms_por_pregunta": latencia_ms[n]}
                    for n, r in resultados.items()
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )  # fmt: skip


if __name__ == "__main__":
    main()
