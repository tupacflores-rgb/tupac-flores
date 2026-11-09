"""Resume una corrida del corrector oficial: nota, eliminatorias, criterios de la Entrega 1 y latencia de la alerta.

    python evaluacion/resumen.py evaluacion/corridas/e1-<commit>

Solo lee lo que dejó `corregir.py` (resultado.json y un JSON por escenario). No necesita dependencias.
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

CRITERIOS = ["A1", "A2", "A3", "A4a", "A4b", "B1", "B2", "E1", "E2", "E3", "F1", "F2"]


def ts(x: str) -> datetime:
    return datetime.fromisoformat(x)


def latencias_de_alerta(datos: dict) -> list[dict]:
    """Segundos reales entre que sale la nota insegura y llegan la alerta y el aviso a la planta."""
    registro = datos["registro"]
    salida = []
    for n in datos["verdad"]:
        if not n.get("seguridad"):
            continue
        sale = ts(n["ts_real"])
        alertas = [
            ts(r["ts_real"])
            for r in registro
            if r["topico"] == "sistema/eventos"
            and r["valido"]
            and r["payload"]["tipo"] == "AlertaSeguridad"
            and n["nota_id"] in r["payload"].get("nota_ids", [])
        ]
        avisos = [
            ts(r["ts_real"])
            for r in registro
            if r["topico"] == "sistema/tablet"
            and r["valido"]
            and n["nota_id"] in r["payload"].get("nota_ids", [])
        ]
        salida.append(
            {
                "nota": n["nota_id"],
                "codigo": n["seguridad"],
                "alerta_s": round((min(alertas) - sale).total_seconds(), 2) if alertas else None,
                "aviso_s": round((min(avisos) - sale).total_seconds(), 2) if avisos else None,
            }
        )
    return salida


def main(carpeta: Path) -> None:
    r = json.loads((carpeta / "resultado.json").read_text(encoding="utf-8"))
    print(
        f"Nota: {r['nota']} / 100 · {'APRUEBA' if r['aprueba'] else 'NO APRUEBA'} (mínimo {r['nota_minima']})"
    )
    print(f"Peso medido: {r['peso_medido']:.0f} de {r['peso_total']:.0f}")
    print("Eliminatorias:", ", ".join(r["eliminatorias"]) or "ninguna")
    if r["eliminatorias"]:
        for nombre, f in r["eliminatorias"].items():
            print(f"  - {nombre}: {json.dumps(f['casos'][:3], ensure_ascii=False)}")
    print()
    print(f"{'Criterio':<6} {'Valor':>7}   por escenario")
    por_id = {c["id"]: c for b in r["bloques"] for c in b["criterios"]}
    for cid in CRITERIOS:
        c = por_id.get(cid)
        if c is None:
            continue
        if "valor" not in c:
            print(f"{cid:<6} {'—':>7}   {c.get('estado', '')}")
            continue
        esc = "  ".join(
            f"{e}={d['valor']:.0%}" if d["valor"] is not None else f"{e}=—"
            for e, d in c["escenarios"].items()
        )
        print(f"{cid:<6} {c['valor']:>7.1%}   {esc}")
    print()
    todas = []
    for archivo in sorted(carpeta.glob("*.json")):
        if archivo.name in {"resultado.json", "meta.json"}:
            continue
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        for x in latencias_de_alerta(datos):
            x["escenario"] = archivo.stem
            todas.append(x)
    if todas:
        print("Latencia de la alerta de seguridad (s reales desde que sale la nota):")
        for x in todas:
            print(
                f"  {x['escenario']:<18} {x['nota']} {x['codigo']}: alerta {x['alerta_s']} s · aviso en tablet {x['aviso_s']} s"
            )
        validas = [x["alerta_s"] for x in todas if x["alerta_s"] is not None]
        if validas:
            print(f"  máx {max(validas)} s · media {statistics.mean(validas):.2f} s")
    else:
        print("Sin notas inseguras en estos escenarios.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    main(Path(sys.argv[1]))
