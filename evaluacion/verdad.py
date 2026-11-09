"""Compara el parte que armó el sistema con la verdad de campo de la planta (`/api/verdad` y `/api/registro`).

    python evaluacion/verdad.py evaluacion/corridas/e1-<commit>-<hora>           # una corrida del corrector
    python evaluacion/verdad.py evaluacion/corridas/e1-.../martes-tranquilo.json  # un solo escenario
    python evaluacion/verdad.py --url http://localhost:8100                       # la planta en desarrollo
    python evaluacion/verdad.py <corrida> --salida docs/resultados/verdad.md      # además lo guarda (+ .json)

De una corrida del corrector lee `verdad` y `registro` de cada JSON de escenario (son lo que devuelven
`/api/verdad` y `/api/registro`). Con `--url` los pide a la planta; en modo corrección `/api/verdad` está
cerrada y hace falta `PLANTA_CORRECCION` (el corrector la usa; el sistema del cursante no puede).

Qué reporta (solo stdlib):

  - Aciertos por campo de cada fila esperada, contra el **último borrador** que mostró el sistema y contra el
    **primero**: equipo y variable (la fila existe), valor, unidad, observación (la fila trae texto).
  - Falsos negativos (fila esperada que no aparece) y falsos positivos (fila mostrada que la nota no dice).
  - Preguntas al operador (mensajes que esperan respuesta y no traen un borrador) y borradores de más, separando
    las notas **ambiguas** (la verdad marca una ambigüedad) de las **claras**.
  - Seguridad: alertas emitidas contra notas inseguras (falsos negativos) y alertas sobre notas sin condición
    insegura (falsos positivos).
  - Costo (USD en la nube, siempre 0: todo es local) y tokens por tarea, si se pasa `--consumo` con un export de
    la tabla `consumo` (`psql -c "\\copy (select …) to …"` o `select json_agg(c) from consumo c`).
  - Latencia por nota, en segundos reales desde que sale: hasta el primer borrador, hasta la confirmación y, en
    las notas inseguras, hasta la alerta.
  - Las notas `VIVO-` no tienen parte esperado y no entran en las cuentas.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

CAMPOS = ("fila", "valor", "unidad", "observacion")


# -- carga ----------------------------------------------------------------------------------------------


def _pedir(url: str):
    req = urllib.request.Request(url)
    clave = os.environ.get("PLANTA_CORRECCION")
    if clave:
        req.add_header("X-Correccion", clave)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def cargar_url(base: str) -> list[tuple[str, list[dict], list[dict]]]:
    base = base.rstrip("/")
    return [("planta", _pedir(f"{base}/api/verdad"), _pedir(f"{base}/api/registro"))]


def cargar_archivos(ruta: Path) -> list[tuple[str, list[dict], list[dict]]]:
    archivos = [ruta] if ruta.is_file() else sorted(ruta.glob("*.json"))
    salida = []
    for a in archivos:
        if a.name in {"resultado.json", "meta.json"}:
            continue
        datos = json.loads(a.read_text(encoding="utf-8"))
        if isinstance(datos, dict) and "verdad" in datos and "registro" in datos:
            salida.append((a.stem, datos["verdad"], datos["registro"]))
    if not salida:
        raise SystemExit(f"{ruta}: no hay JSON de escenario con «verdad» y «registro»")
    return salida


# -- comparación ----------------------------------------------------------------------------------------


def _igual(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-6)


def _unidad(u) -> str | None:
    return None if u is None else str(u).strip().lower().replace("²", "2").replace("°", "")


def _tablet(registro: list[dict], nota_id: str) -> list[dict]:
    msgs = [
        r["payload"]
        for r in registro
        if r.get("topico") == "sistema/tablet"
        and r.get("valido")
        and nota_id in r["payload"].get("nota_ids", [])
    ]
    return sorted(msgs, key=lambda m: m.get("ts", ""))


def _filas(msg: dict) -> list[dict]:
    return (msg.get("pantalla") or {}).get("filas") or []


def _s(desde: str | None, hasta: str | None) -> float | None:
    if not desde or not hasta:
        return None
    return round((datetime.fromisoformat(hasta) - datetime.fromisoformat(desde)).total_seconds(), 2)


def latencias(v: dict, registro: list[dict], msgs: list[dict]) -> dict:
    """Segundos reales desde que sale la nota (`ts_real` de la verdad) hasta cada hito del sistema.

    - `borrador`: primer mensaje de tablet con filas.
    - `confirmacion`: primer evento del parte (SinNovedad/Escalado), que sale cuando el operador confirma;
      incluye lo que tarda el operador virtual en contestar.
    - `alerta`: AlertaSeguridad de esa nota (solo notas inseguras).
    """
    sale = v.get("ts_real")
    ts_reg = {
        id(m): r["ts_real"] for r in registro if r.get("ts_real") and (m := r.get("payload")) is not None
    }
    borradores = [ts_reg[id(m)] for m in msgs if _filas(m) and id(m) in ts_reg]
    eventos = [
        r
        for r in registro
        if r.get("topico") == "sistema/eventos"
        and r.get("valido")
        and v["nota_id"] in r["payload"].get("nota_ids", [])
    ]
    parte = [
        r["ts_real"] for r in eventos if r.get("ts_real") and r["payload"].get("tipo") != "AlertaSeguridad"
    ]
    alertas = [
        r["ts_real"] for r in eventos if r.get("ts_real") and r["payload"].get("tipo") == "AlertaSeguridad"
    ]
    return {
        "borrador_s": _s(sale, min(borradores)) if borradores else None,
        "confirmacion_s": _s(sale, min(parte)) if parte else None,
        "alerta_s": _s(sale, min(alertas)) if alertas else None,
    }


def comparar_filas(esperadas: list[dict], mostradas: list[dict]) -> dict:
    """Por campo: cuántas filas esperadas lo tienen bien; más las que faltan y las que sobran."""
    por_clave: dict[tuple, list[dict]] = defaultdict(list)
    for f in mostradas:
        por_clave[(f["tag"], f["variable"])].append(f)
    campos = {c: [0, 0] for c in CAMPOS}  # [aciertos, total]
    faltan = []
    usadas = set()
    for e in esperadas:
        clave = (e["tag"], e["variable"])
        candidatas = por_clave.get(clave, [])
        campos["fila"][1] += 1
        if not candidatas:
            faltan.append(clave)
            for c in ("valor", "unidad", "observacion"):
                if c != "observacion" or e.get("observacion"):
                    campos[c][1] += 1
            continue
        campos["fila"][0] += 1
        # la candidata con el valor correcto, si hay; si no, la primera
        m = next((c for c in candidatas if _igual(c.get("valor"), e.get("valor"))), candidatas[0])
        usadas.add(id(m))
        campos["valor"][1] += 1
        campos["valor"][0] += _igual(m.get("valor"), e.get("valor"))
        campos["unidad"][1] += 1
        campos["unidad"][0] += _unidad(m.get("unidad")) == _unidad(e.get("unidad"))
        if e.get("observacion"):  # solo se exige cuando la verdad trae texto
            campos["observacion"][1] += 1
            campos["observacion"][0] += bool(m.get("observacion"))
    sobran = [(f["tag"], f["variable"]) for f in mostradas if id(f) not in usadas]
    return {"campos": campos, "faltan": faltan, "sobran": sobran}


def evaluar(escenario: str, verdad: list[dict], registro: list[dict]) -> dict:
    notas = []
    dialogos = [v for v in verdad if v.get("tipo") == "dialogo"]
    for v in verdad:
        if "nota_id" not in v:  # entradas de diálogo del operador virtual: se cuentan aparte
            continue
        if str(v["nota_id"]).upper().startswith("VIVO-") or v.get("tipo") == "vivo":
            continue
        msgs = _tablet(registro, v["nota_id"])
        borradores = [m for m in msgs if _filas(m)]
        preguntas = [m for m in msgs if m.get("espera_respuesta") and not _filas(m)]
        esperadas = v.get("items") or []
        ambigua = bool(v.get("ambiguedades"))
        alerta = any(
            r.get("topico") == "sistema/eventos"
            and r.get("valido")
            and r["payload"].get("tipo") == "AlertaSeguridad"
            and v["nota_id"] in r["payload"].get("nota_ids", [])
            for r in registro
        )
        notas.append(
            {
                "nota": v["nota_id"],
                "ambigua": ambigua,
                "segura": not v.get("seguridad"),
                "seguridad": v.get("seguridad"),
                "alerta": alerta,
                "esperadas": len(esperadas),
                "borradores": len(borradores),
                "preguntas": len(preguntas),
                "sin_borrador": not borradores and bool(esperadas),
                "primero": comparar_filas(esperadas, _filas(borradores[0])) if borradores else None,
                "ultimo": comparar_filas(esperadas, _filas(borradores[-1])) if borradores else None,
                "vacia": not esperadas,
                "latencia": latencias(v, registro, msgs),
            }
        )
    return {
        "escenario": escenario,
        "notas": notas,
        "dialogos": {
            "total": len(dialogos),
            "confirmados": sum(1 for d in dialogos if d.get("confirmo")),
            "con_problemas": sum(1 for d in dialogos if d.get("problemas")),
        },
    }


# -- agregación ------------------------------------------------------------------------------------------


def _sumar(notas: list[dict], cual: str) -> dict:
    campos = {c: [0, 0] for c in CAMPOS}
    faltan = sobran = 0
    for n in notas:
        r = n[cual]
        if r is None:  # sin borrador: todo lo esperado es un falso negativo
            continue
        for c in CAMPOS:
            campos[c][0] += r["campos"][c][0]
            campos[c][1] += r["campos"][c][1]
        faltan += len(r["faltan"])
        sobran += len(r["sobran"])
    return {"campos": campos, "faltan": faltan, "sobran": sobran}


def _pct(par) -> str:
    a, t = par
    return f"{a}/{t} ({a / t:.1%})" if t else "—"


def _estadisticas(xs: list[float]) -> dict:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {
        "n": len(xs),
        "p50": round(statistics.median(xs), 2),
        "max": xs[-1],
        "media": round(statistics.mean(xs), 2),
    }


def consumo_por_tarea(filas: list[dict]) -> dict:
    """Tokens locales por tarea (carril de `consumo`: seguridad, completa, llm…). Sin filas: None."""
    if not filas:
        return {}
    por: dict[str, dict] = {}
    for f in filas:
        c = por.setdefault(f["carril"], {"llamadas": 0, "tokens_in": 0, "tokens_out": 0, "ms": 0})
        c["llamadas"] += 1
        c["tokens_in"] += int(f.get("tokens_in", 0))
        c["tokens_out"] += int(f.get("tokens_out", 0))
        c["ms"] += int(f.get("ms", 0))
    return por


def resumir(evaluaciones: list[dict]) -> dict:
    notas = [n for e in evaluaciones for n in e["notas"]]
    con_parte = [n for n in notas if n["esperadas"]]
    grupos = {
        "ambiguas": [n for n in con_parte if n["ambigua"]],
        "claras": [n for n in con_parte if not n["ambigua"]],
    }
    sin_borrador = [n["nota"] for n in con_parte if n["sin_borrador"]]
    inseguras = [n for n in notas if n["seguridad"]]
    seguras = [n for n in notas if not n["seguridad"]]
    return {
        "notas": len(notas),
        "con_parte_esperado": len(con_parte),
        "sin_borrador": sin_borrador,
        "ultimo": _sumar(con_parte, "ultimo"),
        "primero": _sumar(con_parte, "primero"),
        "grupos": {
            k: {
                "notas": len(g),
                "preguntas": sum(n["preguntas"] for n in g),
                "borradores_extra": sum(max(n["borradores"] - 1, 0) for n in g),
                "con_pregunta": sum(1 for n in g if n["preguntas"]),
                "ultimo": _sumar(g, "ultimo"),
            }
            for k, g in grupos.items()
        },
        "dialogos": {
            k: sum(e["dialogos"][k] for e in evaluaciones) for k in ("total", "confirmados", "con_problemas")
        },
        "latencia": {
            k: _estadisticas([n["latencia"][k] for n in notas if n["latencia"][k] is not None])
            for k in ("borrador_s", "confirmacion_s", "alerta_s")
        },
        "costo_usd_nube": 0.0,
        "seguridad": {
            "inseguras": len(inseguras),
            "alertas_emitidas": sum(1 for n in inseguras if n["alerta"]),
            "falsos_negativos": [n["nota"] for n in inseguras if not n["alerta"]],
            "falsos_positivos": [n["nota"] for n in seguras if n["alerta"]],
        },
    }


def informe(evaluaciones: list[dict], origen: str, consumo: dict | None = None) -> str:
    r = resumir(evaluaciones)
    L = [
        "# Parte del sistema contra la verdad de campo\n",
        f"Origen: `{origen}` · escenarios: {', '.join(e['escenario'] for e in evaluaciones)}\n",
        f"Notas evaluadas: {r['notas']} ({r['con_parte_esperado']} con parte esperado; las `VIVO-` no entran).\n",
    ]
    for titulo, clave in (("Último borrador mostrado", "ultimo"), ("Primer borrador mostrado", "primero")):
        s = r[clave]
        L += [f"## {titulo}\n", "| Campo | Aciertos |", "|---|---|"]
        nombres = {
            "fila": "equipo + variable",
            "valor": "valor",
            "unidad": "unidad",
            "observacion": "observación",
        }
        L += [f"| {nombres[c]} | {_pct(s['campos'][c])} |" for c in CAMPOS]
        L += [
            "",
            f"Falsos negativos (filas esperadas que no están): {s['faltan']} · falsos positivos (filas que "
            f"la nota no dice): {s['sobran']}\n",
        ]
    if r["sin_borrador"]:
        L.append(f"Notas con parte esperado y **ningún borrador**: {', '.join(r['sin_borrador'])}\n")
    L += [
        "## Preguntas al operador: notas ambiguas vs claras\n",
        "| Grupo | Notas | Con pregunta | Preguntas | Borradores de más | Valor correcto al final |",
        "|---|---|---|---|---|---|",
    ]
    for k, g in r["grupos"].items():
        L.append(
            f"| {k} | {g['notas']} | {g['con_pregunta']} | {g['preguntas']} | {g['borradores_extra']} | "
            f"{_pct(g['ultimo']['campos']['valor'])} |"
        )
    L += [
        "",
        "## Latencia por nota (segundos reales desde que sale la nota)\n",
        "| Hito | Notas | Mediana | Media | Máx |",
        "|---|---|---|---|---|",
    ]
    nombres_l = {
        "borrador_s": "hasta el primer borrador",
        "confirmacion_s": "hasta la confirmación (evento del parte)",
        "alerta_s": "hasta la alerta de seguridad (S1–S4)",
    }
    for k, nom in nombres_l.items():
        s_ = r["latencia"][k]
        L.append(
            f"| {nom} | {s_['n']} | "
            + (f"{s_['p50']} | {s_['media']} | {s_['max']} |" if s_["n"] else "— | — | — |")
        )
    L += [
        "",
        "La confirmación incluye el tiempo que tarda el operador virtual en contestar.",
        "",
        "## Costo\n",
        f"- Nube: **USD {r['costo_usd_nube']:.2f}**. Todos los modelos son locales (sin llamadas a servicios pagos).",
        "- Tokens por tarea:",
    ]
    if consumo:
        for carril, c in sorted(consumo.items()):
            L.append(
                f"  - `{carril}`: {c['llamadas']} llamadas · {c['tokens_in']} tokens de entrada · "
                f"{c['tokens_out']} de salida · {c['ms'] / 1000:.1f} s"
            )
    else:
        L.append(
            "  - sin export de la tabla `consumo` para esta corrida (ver `--consumo`). El ASR y los embeddings "
            "no usan tokens de LLM; solo el respaldo LLM los consume."
        )
    d = r["dialogos"]
    L += [
        "",
        f"Respuestas del operador virtual: {d['total']} · confirmaron el borrador: {d['confirmados']} · "
        f"con problemas señalados: {d['con_problemas']}",
    ]
    s = r["seguridad"]
    L += [
        "",
        "## Seguridad\n",
        f"- Notas inseguras: {s['inseguras']} · con alerta emitida: {s['alertas_emitidas']}",
        f"- Falsos negativos: {', '.join(s['falsos_negativos']) or 'ninguno'}",
        f"- Falsos positivos: {', '.join(s['falsos_positivos']) or 'ninguno'}",
        "",
    ]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("corrida", nargs="?", type=Path, help="carpeta de una corrida o JSON de un escenario")
    ap.add_argument("--url", help="planta en vivo, p. ej. http://localhost:8100")
    ap.add_argument(
        "--consumo",
        type=Path,
        help="JSON con filas de la tabla `consumo` (nota_id, carril, tokens_in, tokens_out, ms) de esa corrida",
    )
    ap.add_argument("--salida", type=Path, help="guarda el informe (.md) y el resumen (.json) acá")
    a = ap.parse_args(argv)
    if bool(a.corrida) == bool(a.url):
        ap.error("indicá una corrida o --url")
    fuentes = cargar_url(a.url) if a.url else cargar_archivos(a.corrida)
    evaluaciones = [evaluar(*f) for f in fuentes]
    consumo = consumo_por_tarea(json.loads(a.consumo.read_text(encoding="utf-8"))) if a.consumo else {}
    texto = informe(evaluaciones, a.url or str(a.corrida), consumo)
    print(texto)
    if a.salida:
        a.salida.parent.mkdir(parents=True, exist_ok=True)
        a.salida.write_text(texto, encoding="utf-8")
        a.salida.with_suffix(".json").write_text(
            json.dumps(
                {"resumen": resumir(evaluaciones), "consumo": consumo, "escenarios": evaluaciones},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
