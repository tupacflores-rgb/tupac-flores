"""Evalúa el recuperador propio contra la suite de 71 preguntas y lo compara con la línea de base BM25 oficial.

python evaluacion/retrieval.py --docs "Context/Clon del REPO"
python evaluacion/retrieval.py --docs ... --json docs/resultados/retrieval.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from sistema.conocimiento.corpus import cargar  # noqa: E402
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
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(a.docs / "planta"))
    from corpus import evaluar as ev  # type: ignore

    base = a.docs / "datos" / "documentos"
    suite, frags_of = ev.cargar(base)
    ks = (1, 3, 5, 10)
    bm25 = ev.BM25(frags_of).buscar
    base_res = ev.evaluar(bm25, suite, ks)
    r = Recuperador(cargar(base))
    propio = ev.evaluar(lambda q, k: r.citas(q, k), suite, ks)
    c_antes = {"BM25 oficial": _rev_c_antes_que_d(bm25, suite), "propio": _rev_c_antes_que_d(r.citas, suite)}
    print(f"{'métrica':14}{'BM25 oficial':>14}{'propio':>10}")
    for m in propio["total"]:
        print(f"{m:14}{base_res['total'][m]:>14}{propio['total'][m]:>10}")
    print("Rev. C antes que Rev. D (top-5):", c_antes)
    for tipo, m in propio["por_tipo"].items():
        print(f"  {tipo:14} n={m['n']:2}  hit@5={m['hit@5']}  recall@5={m['recall@5']}")
    if a.json:
        a.json.write_text(
            json.dumps(
                {
                    "bm25_oficial": base_res["total"],
                    "propio": propio["total"],
                    "rev_c_antes_que_d": c_antes,
                    "por_tipo": propio["por_tipo"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
