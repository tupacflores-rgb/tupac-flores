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
  - Las notas `VIVO-` no tienen parte esperado y no entran en las cuentas.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
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
        "seguridad": {
            "inseguras": len(inseguras),
            "alertas_emitidas": sum(1 for n in inseguras if n["alerta"]),
            "falsos_negativos": [n["nota"] for n in inseguras if not n["alerta"]],
            "falsos_positivos": [n["nota"] for n in seguras if n["alerta"]],
        },
    }


def informe(evaluaciones: list[dict], origen: str) -> str:
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
    ap.add_argument("--salida", type=Path, help="guarda el informe (.md) y el resumen (.json) acá")
    a = ap.parse_args(argv)
    if bool(a.corrida) == bool(a.url):
        ap.error("indicá una corrida o --url")
    fuentes = cargar_url(a.url) if a.url else cargar_archivos(a.corrida)
    evaluaciones = [evaluar(*f) for f in fuentes]
    texto = informe(evaluaciones, a.url or str(a.corrida))
    print(texto)
    if a.salida:
        a.salida.parent.mkdir(parents=True, exist_ok=True)
        a.salida.write_text(texto, encoding="utf-8")
        a.salida.with_suffix(".json").write_text(
            json.dumps(
                {"resumen": resumir(evaluaciones), "escenarios": evaluaciones}, ensure_ascii=False, indent=1
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
