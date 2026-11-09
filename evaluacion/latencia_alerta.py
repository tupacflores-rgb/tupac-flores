"""Latencia de la alerta de seguridad en el peor caso (puerta D18 enmendada: p99 <= 15 s).

Peor caso: CPU de los otros contenedores saturada y una nota normal de ~19 s de audio en cola justo antes de la
nota de seguridad. Dispara N condiciones S1–S4 con /api/control/accion y mide, por disparo, los segundos reales
entre que sale la nota insegura y llega la AlertaSeguridad (y el aviso a la tablet). También cuenta las
falsas alarmas: AlertaSeguridad cuya nota no era insegura (las notas largas traen negaciones a propósito).

    python evaluacion/latencia_alerta.py --disparos 50 --saturar 16 --json docs/resultados/latencia_alerta.json

Requiere la planta y el sistema levantados (docker compose up) y el escenario `libre`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime

import httpx

# Normal a propósito: lleva negaciones del léxico de seguridad («no hay olor a gas», «sin humo», «nadie herido»).
NOTA_LARGA = (
    "Recorrida de las cuatro. La uno en servicio, ciento doce kilos, sin pérdidas. La dos gotea un poco por la "
    "empaquetadura. La tres parada, en reserva. El filtro A uno coma tres. No hay olor a gas, no sale humo de "
    "ningún motor y nadie está herido. Todo normal."
)
CONDICIONES = [("S1", "B-101"), ("S2", None), ("S3", "B-102"), ("S4", None), ("S1", "B-103"), ("S3", "B-101")]


def ts(x: str) -> datetime:
    return datetime.fromisoformat(x)


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * len(xs))) - 1))]


def saturar(n: int) -> list[str]:
    nombres = []
    for i in range(n):
        nombre = f"burn-latencia-{i}"
        subprocess.run(
            ["docker", "run", "-d", "--rm", "--name", nombre, "alpine:3.20", "sh", "-c", "while :; do :; done"],
            check=True, capture_output=True,
        )  # fmt: skip
        nombres.append(nombre)
    return nombres


def liberar(nombres: list[str]) -> None:
    if nombres:
        subprocess.run(["docker", "rm", "-f", *nombres], capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--planta", default="http://localhost:8100")
    ap.add_argument("--disparos", type=int, default=50)
    ap.add_argument("--saturar", type=int, default=0, help="procesos que queman CPU en otros contenedores")
    ap.add_argument("--sin-nota-larga", action="store_true")
    ap.add_argument("--espera-max", type=float, default=60.0)
    ap.add_argument("--json")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    http = httpx.Client(base_url=a.planta, timeout=30)

    def accion(nombre: str, **params):
        r = http.post("/api/control/accion", json={"accion": nombre, "params": params})
        r.raise_for_status()

    def verdad() -> list[dict]:
        return [n for n in http.get("/api/verdad").json() if "tipo" not in n]  # sin los mensajes de diálogo

    def registro() -> list[dict]:
        return http.get("/api/registro").json()

    previas = {n["nota_id"] for n in verdad()}
    quemadores = saturar(a.saturar)
    filas, no_llegaron = [], []
    try:
        time.sleep(3)
        for i in range(a.disparos):
            codigo, equipo = CONDICIONES[i % len(CONDICIONES)]
            antes = {n["nota_id"] for n in verdad()}
            if not a.sin_nota_larga:
                accion("nota", texto=NOTA_LARGA, items=[], fotos=[])
            accion("seguridad", codigo=codigo, **({"equipo": equipo} if equipo else {}))
            nota = None
            t_fin = time.monotonic() + a.espera_max
            while time.monotonic() < t_fin:
                nuevas = [n for n in verdad() if n["nota_id"] not in antes and n.get("seguridad")]
                if nuevas:
                    nota = nuevas[0]
                    break
                time.sleep(0.2)
            if nota is None:
                no_llegaron.append(i)
                continue
            sale = ts(nota["ts_real"])
            alerta = aviso = None
            while time.monotonic() < t_fin and alerta is None:
                for r in registro():
                    if not r["valido"] or nota["nota_id"] not in r["payload"].get("nota_ids", []):
                        continue
                    if r["topico"] == "sistema/eventos" and r["payload"].get("tipo") == "AlertaSeguridad":
                        alerta = ts(r["ts_real"])
                    if r["topico"] == "sistema/tablet" and aviso is None:
                        aviso = ts(r["ts_real"])
                if alerta is None:
                    time.sleep(0.2)
            fila = {"i": i, "codigo": codigo, "nota": nota["nota_id"],
                    "alerta_s": round((alerta - sale).total_seconds(), 2) if alerta else None,
                    "aviso_s": round((aviso - sale).total_seconds(), 2) if aviso else None}  # fmt: skip
            filas.append(fila)
            print(
                f"[{i + 1}/{a.disparos}] {codigo} alerta={fila['alerta_s']} aviso={fila['aviso_s']}",
                flush=True,
            )
            time.sleep(7)  # que el sistema termine la nota larga y el diálogo antes del siguiente disparo
    finally:
        liberar(quemadores)

    time.sleep(8)
    inseguras = {n["nota_id"] for n in verdad() if n.get("seguridad")}
    alertas = [
        r
        for r in registro()
        if r["topico"] == "sistema/eventos" and r["payload"].get("tipo") == "AlertaSeguridad"
    ]
    falsas = [r["payload"] for r in alertas
              if not (set(r["payload"].get("nota_ids", [])) & inseguras) and set(r["payload"].get("nota_ids", [])) - previas]  # fmt: skip
    invalidos = sum(1 for r in registro() if not r["valido"])
    lat = [f["alerta_s"] for f in filas if f["alerta_s"] is not None]
    res = {
        "disparos": a.disparos, "con_alerta": len(lat), "sin_alerta": a.disparos - len(lat),
        "falsas_alarmas": len(falsas), "mensajes_invalidos": invalidos,
        "saturacion_procesos": a.saturar, "nota_larga_antes": not a.sin_nota_larga,
        "alerta_s": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "p99": pct(lat, 0.99), "max": max(lat),
                     "media": round(statistics.mean(lat), 2)} if lat else None,
        "por_codigo": {c: round(max(f["alerta_s"] for f in filas if f["codigo"] == c and f["alerta_s"]), 2)
                       for c in sorted({f["codigo"] for f in filas if f["alerta_s"]})},
        "puerta_p99_15s": bool(lat) and pct(lat, 0.99) <= 15 and not falsas and len(lat) == a.disparos,
        "filas": filas,
    }  # fmt: skip
    print(json.dumps({k: v for k, v in res.items() if k != "filas"}, indent=2, ensure_ascii=False))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
