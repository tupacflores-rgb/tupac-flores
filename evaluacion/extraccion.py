"""Evaluación offline del parte por voz con el operador virtual **real** de la planta.

Genera recorridas con `planta_sim.operador.OperadorVirtual` sobre una planta con estados al azar, le pasa el texto
de cada nota (transcripción perfecta) al parser y al diálogo, y contesta con `planta_sim.dialogo.responder`, el mismo
código que usa la planta en la corrección. Mide lo que mide la rúbrica: A2, A3, A4a, A4b y B1, además de la
detección de seguridad. Sirve para iterar en segundos sin esperar una corrida completa del corrector.

Se ejecuta en la imagen de la planta (trae planta_sim y sus dependencias), con el repo y la copia oficial montados:

    docker run --rm -v <repo>:/work -v "<repo>/Context/Clon del REPO:/oficial:ro" planta-sim \
        python /work/evaluacion/extraccion.py --recorridas 300
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path[:0] = ["/work/src", "/oficial/planta/sim", "/oficial/correccion"]

import yaml  # noqa: E402
from planta_contrato import NotaRecorrida, RespuestaOperador, Topicos  # noqa: E402
from planta_sim.dialogo import responder  # noqa: E402
from planta_sim.modelo import Planta  # noqa: E402
from planta_sim.operador import OperadorVirtual  # noqa: E402
from puntuar import anomalos  # noqa: E402

from sistema.config import Config  # noqa: E402
from sistema.deteccion.eventos import construir  # noqa: E402
from sistema.deteccion.seguridad import detectar  # noqa: E402
from sistema.dominio.catalogo import Catalogo  # noqa: E402
from sistema.estado import RelojPlanta  # noqa: E402
from sistema.voz.dialogo import Dialogo  # noqa: E402

DEFINICION = yaml.safe_load(Path("/oficial/datos/planta.yaml").read_text(encoding="utf-8"))
RUBRICA = yaml.safe_load(Path("/oficial/correccion/rubrica.yaml").read_text(encoding="utf-8"))
PARAMS_B1 = RUBRICA["bloques"]["B"]["criterios"]["B1"]["parametros"]
CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 0, tzinfo=UTC)


class Salida:
    def __init__(self):
        self.tablet = []

    def emitir(self, id_, topico, modelo):
        if topico == Topicos.TABLET:
            self.tablet.append(modelo)
        return True


class Base:
    def __init__(self):
        self.d, self.m = {}, {}

    def guardar_dialogo(self, n, e):
        self.d[n] = e

    def cargar_dialogo(self, n):
        return self.d.get(n)

    def registrar_mensaje(self, m, n):
        self.m[m] = n

    def nota_de_mensaje(self, m):
        return self.m.get(m)


def planta_al_azar(rng: random.Random) -> Planta:
    p = Planta.desde_definicion(DEFINICION, rng, TS)
    for b in p.bombas.values():
        b.estado = rng.choices(["marcha", "parada", "mantenimiento", "disparo"], [8, 2, 1, 1])[0]
        b.pd = rng.uniform(96, 124)
        b.goteo = rng.choice([0, 0, 0, rng.uniform(21, 80), rng.uniform(121, 300)])
        b.t_rodamiento = rng.uniform(45, 95)
        b.nivel_aceite = rng.choice([1.0, 1.0, 0.7, 0.4])
        b.correas = rng.choice(["bien", "bien", "flojas", "muy flojas"])
        b.ruido = rng.choice(["normal", "normal", "golpeteo", "cavitacion", "rodamiento"])
    for f in p.filtros.values():
        f.dp = rng.uniform(0.2, 1.9)
    p.nivel = rng.uniform(3, 7.5)
    p.rebalsando = rng.random() < 0.05
    return p


def correr_nota(nota, dialogo, salida, confirmados, seguridad: str | None = None) -> dict:
    """Juega el diálogo completo de una nota y devuelve las métricas que mide la rúbrica."""
    nota_c = NotaRecorrida(
        id=nota.id, ts=TS, operador=nota.operador, audio_url=None, fotos=[], recorrida_id="R"
    )
    n0 = len(salida.tablet)
    dialogo.procesar_nota(nota_c, nota.texto, detectar(nota.texto, CAT) if seguridad else None)
    evaluaciones = []
    for _ in range(10):
        if len(salida.tablet) == n0 or not salida.tablet[-1].espera_respuesta:
            break
        msg = salida.tablet[-1]
        texto, evaluacion = responder(msg.model_dump(mode="json"), [nota.esperado()], 7)
        evaluaciones.append(evaluacion)
        dialogo.procesar_respuesta(
            RespuestaOperador(
                id=f"R-{msg.id}", ts=TS, mensaje_id=msg.id, operador="Op", texto=texto, audio_url=None
            )
        )
    mensajes = salida.tablet[n0:]
    borradores = [e for e in evaluaciones if e.get("filas_mostradas", 0) > 0]
    preguntas = [
        m for m in mensajes
        if m.espera_respuesta and (not (m.pantalla and m.pantalla.filas) or (m.pantalla and m.pantalla.opciones))
    ]  # fmt: skip
    return {
        "nota": nota, "mensajes": mensajes, "evaluaciones": evaluaciones,
        "primero_ok": bool(borradores and borradores[0].get("confirmo")),
        "final_ok": bool(borradores and borradores[-1].get("confirmo")),
        "pregunto": bool(preguntas), "cobertura": any(m.pantalla and m.pantalla.filas for m in mensajes),
        "confirmado": nota.id in confirmados,
    }  # fmt: skip


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recorridas", type=int, default=200)
    ap.add_argument("--semilla", type=int, default=1)
    ap.add_argument("--ejemplos", type=int, default=8)
    a = ap.parse_args()

    resultados, eventos_por_nota = [], {}
    salida, base, confirmados = Salida(), Base(), {}
    reloj = RelojPlanta()
    reloj.fijar(TS)

    def al_confirmar(est):
        confirmados[est.nota_id] = est
        eventos_por_nota[est.nota_id] = construir(est.nota_id, TS, est.filas, CAT)

    dialogo = Dialogo(Config(), salida, base, lambda: CAT, reloj, al_confirmar)
    for i in range(a.recorridas):
        rng = random.Random(a.semilla * 100000 + i)
        operador = OperadorVirtual(random.Random(a.semilla * 7 + i), random.Random(a.semilla * 13 + i))
        operador.contador = i * 100
        for nota in operador.recorrida(planta_al_azar(rng)):
            resultados.append(correr_nota(nota, dialogo, salida, confirmados))

    # --- métricas como las de la rúbrica -------------------------------------------------------------
    n = len(resultados)
    con_valor = [r for r in resultados if any(i.valor is not None for i in r["nota"].items)]
    ambiguas = [r for r in resultados if r["nota"].ambiguedades]
    claras = [r for r in resultados if not r["nota"].ambiguedades]
    b1_bien = b1_total = 0
    fallos_b1 = []
    for r in resultados:
        nota = r["nota"]
        esperado = {
            tag: "Escalado" if x else "SinNovedad" for tag, x in anomalos(nota.esperado(), PARAMS_B1).items()
        }
        esperado = esperado or {None: "SinNovedad"}
        publicado = {e.equipo: e.tipo.value for e in eventos_por_nota.get(nota.id, [])}
        for tag, tipo in esperado.items():
            b1_total += 1
            if publicado.get(tag) == tipo:
                b1_bien += 1
            else:
                fallos_b1.append((nota, tag, tipo, publicado.get(tag)))
    print(f"Notas: {n} · con algún valor: {len(con_valor)} · ambiguas: {len(ambiguas)}")
    print(f"A1 cobertura        {sum(r['cobertura'] for r in resultados) / n:6.1%}")
    print(f"A2 primer intento   {sum(r['primero_ok'] for r in resultados) / n:6.1%}  (sobre todas las notas)")
    print(f"A3 parte final      {sum(r['final_ok'] for r in resultados) / n:6.1%}  (sobre todas las notas)")
    print(f"A2 sobre notas con valor: {sum(r['primero_ok'] for r in con_valor) / len(con_valor):6.1%}")
    print(f"A3 sobre notas con valor: {sum(r['final_ok'] for r in con_valor) / len(con_valor):6.1%}")
    print(
        f"A4a pregunta si hay ambigüedad {sum(r['pregunto'] for r in ambiguas) / max(len(ambiguas), 1):6.1%}"
    )
    print(
        f"A4b no pregunta de más         {sum(not r['pregunto'] for r in claras) / max(len(claras), 1):6.1%}"
    )
    print(f"B1  decisiones por equipo      {b1_bien / b1_total:6.1%}  ({b1_bien}/{b1_total})")

    # --- por qué se pierde A2/A3 ----------------------------------------------------------------------
    tipos = Counter()
    for r in con_valor:
        if not r["final_ok"]:
            tipos["final_no_ok"] += 1
        for e in r["evaluaciones"][:1]:
            for p in e.get("problemas", []):
                tipos[p["tipo"]] += 1
    print("\nProblemas del primer borrador:", dict(tipos))
    mal = [r for r in con_valor if not r["final_ok"]][: a.ejemplos]
    for r in mal:
        print(f"\n[A3 ✗] {r['nota'].id}: {r['nota'].texto}")
        print(
            "   verdad:",
            [(i.tag, i.variable, i.valor, i.unidad) for i in r["nota"].items if i.valor is not None],
        )
        for e in r["evaluaciones"]:
            print(
                "   operador:",
                e.get("respuesta") or e.get("respuesta_correcta"),
                "| problemas:",
                e.get("problemas"),
            )
    sin_confirmar_con_valor = [r for r in con_valor if not r["confirmado"]]
    print(f"\nNotas con valor sin confirmar: {len(sin_confirmar_con_valor)}")
    for nota, tag, esperado, publicado in fallos_b1[: a.ejemplos]:
        print(f"[B1 ✗] {nota.id} {tag}: esperado {esperado}, publicado {publicado} | {nota.texto[:100]}")

    # --- seguridad -------------------------------------------------------------------------------------
    print("\nSeguridad (notas S1–S4 del operador virtual):")
    planta = planta_al_azar(random.Random(5))
    ov = OperadorVirtual(random.Random(1))
    bien = 0
    for codigo in ("S1", "S2", "S3", "S4"):
        for equipo in (None, "B-101", "B-102", "B-103"):
            nota = ov.nota_seguridad(planta, codigo, equipo if codigo in ("S1", "S3") else None)
            d = detectar(nota.texto, CAT)
            ok = (
                d is not None
                and d.codigo == codigo
                and (d.equipo == equipo if codigo in ("S1", "S3") else True)
            )
            bien += ok
            if not ok:
                print("  ✗", codigo, equipo, "→", d, "|", nota.texto)
    print(f"  detectadas correctamente: {bien}/16")
    negativas = [r["nota"].texto for r in resultados if detectar(r["nota"].texto, CAT)]
    print(f"  falsas alarmas sobre {n} notas normales: {len(negativas)}")
    for t in negativas[:5]:
        print("   ✗", t)


if __name__ == "__main__":
    main()
