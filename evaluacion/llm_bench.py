"""Benchmark del LLM de respaldo con la cuota real del servicio (0,75 CPU / 2,5 GB).

Corre cada modelo candidato sobre los casos de `llm_casos.py` usando el mismo código que usa el sistema
(`RespaldoLLM.extraer`: prompt, structured output hacia FilaParte y validación determinista). Mide por modelo:

  - JSON válido: la llamada devolvió un ParteLLM (no hubo timeout ni salida inválida);
  - exactitud por campo, sobre las filas esperadas: equipo+variable encontrados, valor exacto y unidad;
  - precisión: filas devueltas que están en la verdad (sin alucinaciones) y filas descartadas por la validación;
  - latencia p50/p95 por llamada (después de una de calentamiento) y RAM pico del contenedor.

    docker run -d --name llm-bench --network planta-lan --cpus 0.75 --memory 2560m tupac-flores-llm:bench
    docker run --rm --network planta-lan -v <repo>:/work -e PYTHONPATH=/work/src -v /var/run/docker.sock:... \
        tupac-flores-dev python /work/evaluacion/llm_bench.py --url http://llm-bench:11434 --casos ... --salida ...
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
from pathlib import Path

import yaml

from sistema.dominio.catalogo import Catalogo
from sistema.respaldo import RespaldoLLM

DEFINICION = yaml.safe_load(Path("/oficial/datos/planta.yaml").read_text(encoding="utf-8"))
CAT = Catalogo(DEFINICION)


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, round(q * len(xs)) - 1))]


def ram_pico(contenedor: str, parar: threading.Event, salida: list[float]) -> None:
    while not parar.is_set():
        try:
            r = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", contenedor],
                capture_output=True, text=True, timeout=20,
            )  # fmt: skip
            uso = r.stdout.split("/")[0].strip()
            n = float("".join(c for c in uso if c.isdigit() or c == "."))
            salida.append(n * (1024 if "GiB" in uso else 1))
        except Exception:
            pass
        parar.wait(1.0)


def evaluar(modelo: str, url: str, casos: list[dict], contenedor: str | None, timeout_s: float) -> dict:
    llm = RespaldoLLM(url, modelo, timeout_s=timeout_s, max_tokens=100_000)
    llm.extraer("La uno está en ciento doce kilos de descarga.", CAT, "calentamiento")  # carga el modelo
    memoria: list[float] = []
    parar = threading.Event()
    if contenedor:
        threading.Thread(target=ram_pico, args=(contenedor, parar, memoria), daemon=True).start()
    filas = []
    for c in casos:
        t0 = time.monotonic()
        r = llm.extraer(c["texto_asr"], CAT, c["id"])
        seg = time.monotonic() - t0
        esperado = c["esperado"]
        if r is None:
            filas.append(
                {"id": c["id"], "variante": c["variante"], "valido": False, "s": seg, "n_esp": len(esperado)}
            )
            continue
        sal = {(f.tag, f.variable): f for f in r.filas}
        encontrados = valores = unidades = 0
        for e in esperado:
            f = sal.get((e["tag"], e["variable"]))
            if f is None:
                continue
            encontrados += 1
            if e["valor"] is None or (f.valor is not None and abs(f.valor - e["valor"]) < 1e-6):
                valores += 1
            if e["valor"] is None or f.unidad == e["unidad"]:
                unidades += 1
        verdaderas = sum(1 for k in sal if any((e["tag"], e["variable"]) == k for e in esperado))
        filas.append(
            {
                "id": c["id"], "variante": c["variante"], "valido": True, "s": seg, "n_esp": len(esperado),
                "encontrados": encontrados, "valores": valores, "unidades": unidades,
                "devueltas": len(sal), "verdaderas": verdaderas, "descartadas": r.descartadas,
            }
        )  # fmt: skip
    parar.set()
    ok = [f for f in filas if f["valido"]]
    esp = sum(f["n_esp"] for f in filas)
    lat = [f["s"] for f in filas]
    res = {
        "modelo": modelo, "casos": len(filas), "json_valido": round(len(ok) / len(filas), 3),
        "filas_esperadas": esp,
        "equipo_variable": round(sum(f["encontrados"] for f in ok) / esp, 3),
        "valor_exacto": round(sum(f["valores"] for f in ok) / esp, 3),
        "unidad": round(sum(f["unidades"] for f in ok) / esp, 3),
        "precision": round(sum(f["verdaderas"] for f in ok) / max(sum(f["devueltas"] for f in ok), 1), 3),
        "descartadas_por_validacion": sum(f["descartadas"] for f in ok),
        "latencia_s": {"p50": round(statistics.median(lat), 1), "p95": round(pct(lat, 0.95), 1), "max": round(max(lat), 1)},
        "ram_pico_mib": round(max(memoria)) if memoria else None,
        "por_variante": {},
        "filas": filas,
    }  # fmt: skip
    for v in sorted({f["variante"] for f in filas}):
        sub = [f for f in filas if f["variante"] == v and f["valido"]]
        e = sum(f["n_esp"] for f in filas if f["variante"] == v)
        res["por_variante"][v] = {"casos": sum(1 for f in filas if f["variante"] == v),
                                  "valor_exacto": round(sum(f["valores"] for f in sub) / max(e, 1), 2)}  # fmt: skip
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--modelos", nargs="+", required=True)
    ap.add_argument("--casos", type=Path, required=True)
    ap.add_argument("--contenedor", help="nombre del contenedor llm (para medir la RAM pico)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--salida", type=Path, required=True)
    a = ap.parse_args()
    casos = json.loads(a.casos.read_text(encoding="utf-8"))
    if a.max:
        casos = casos[: a.max]
    todos = []
    for m in a.modelos:
        print(f"== {m}", flush=True)
        r = evaluar(m, a.url, casos, a.contenedor, a.timeout)
        todos.append(r)
        print(
            json.dumps({k: v for k, v in r.items() if k not in ("filas",)}, ensure_ascii=False, indent=1),
            flush=True,
        )
        a.salida.write_text(json.dumps(todos, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
