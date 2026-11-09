"""A-6 (foto asociada a su fila) y B-3 (contexto del Escalado: parte, fotos y tendencia)."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from planta_contrato import NotaRecorrida, TipoEvento
from planta_falsa import DEFINICION, S1

from sistema.config import Config
from sistema.deteccion.eventos import construir
from sistema.deteccion.seguridad import detectar
from sistema.deteccion.sensores import DetectorSostenido, Parametros
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import Fila
from sistema.dominio.tendencia import Tendencia
from sistema.estado import RelojPlanta
from sistema.voz.dialogo import Dialogo
from sistema.voz.parser import extraer

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)
FOTOS = ["/media/fotos/goteo/01.jpg", "/media/fotos/manometro/02.jpg"]


def filas(texto, fotos):
    return [(f.tag, f.variable, f.foto) for f in extraer(texto, CAT, fotos).filas]


def test_cada_le_saque_foto_se_lleva_la_siguiente_foto_de_la_nota_en_orden():
    r = filas(
        "La dos pierde bastante por la empaquetadura, le saqué foto. El filtro A me dio uno coma tres de diferencial, le saqué foto al manómetro.",
        FOTOS,
    )
    foto_dos = {f for tag, _, f in r if tag == "B-102"}
    foto_filtro = {f for tag, _, f in r if tag == "F-201A"}
    assert foto_dos == {FOTOS[0]} and foto_filtro == {FOTOS[1]}


def test_una_fila_sin_foto_en_su_tramo_no_se_lleva_la_foto_de_otra():
    r = filas(
        "La uno está en ciento doce kilos de descarga. La dos pierde por la empaquetadura, le saqué foto.",
        FOTOS[:1],
    )
    assert {tag: f for tag, _, f in r}["B-101"] is None
    assert {tag: f for tag, _, f in r}["B-102"] == FOTOS[0]


def test_sin_fotos_o_sin_la_mencion_de_foto_las_filas_quedan_sin_foto():
    assert all(f is None for *_, f in filas("La uno está en ciento doce kilos de descarga.", FOTOS))
    assert all(f is None for *_, f in filas("La dos pierde por la empaquetadura, le saqué foto.", []))


def test_la_foto_de_la_nota_insegura_queda_en_la_fila_del_equipo():
    class Salida:
        def emitir(self, *a):
            return True

    class Base:
        def guardar_dialogo(self, *a): ...
        def registrar_mensaje(self, *a): ...
        def cargar_dialogo(self, n):
            return None

    reloj = RelojPlanta()
    reloj.fijar(TS)
    d = Dialogo(Config(), Salida(), Base(), lambda: CAT, reloj)
    nota = NotaRecorrida(
        id="N-1",
        ts=TS,
        operador="x",
        audio_url="/a.wav",
        fotos=["/media/fotos/rotura_descarga/04.jpg"],
        recorrida_id="R",
    )
    d.procesar_nota(nota, S1, detectar(S1, CAT))
    (fila,) = d._estados["N-1"].filas
    assert fila.foto == "/media/fotos/rotura_descarga/04.jpg"


def test_la_foto_sobrevive_al_guardado_del_estado():
    from sistema.dominio.parte import filas_desde_dict

    f = Fila("B-102", "goteo", None, None, "pierde", foto=FOTOS[0])
    from dataclasses import asdict

    assert filas_desde_dict([asdict(f)])[0].foto == FOTOS[0]


# -- B-3 ---------------------------------------------------------------------------------------------


def muestras(valores):
    return [{"ts": f"t{i}", "valor": v} for i, v in enumerate(valores)]


def test_tendencia_describe_sube_baja_o_estable():
    t = Tendencia(
        lambda tag, var, d, h: muestras(
            {"presion_descarga": [110, 112, 120], "vibracion": [2.0, 2.0, 2.01]}[var]
        ),
        lambda: CAT,
    )
    texto = t.describir("B-101", TS)
    assert "presión de descarga sube de 110 a 120 kg/cm²" in texto and "vibración estable" in texto
    assert texto.startswith("últimas 3 h:")


def test_tendencia_pide_las_ultimas_3_horas_y_no_opina_sin_datos_o_con_historian_caido():
    pedidos = []
    t = Tendencia(lambda tag, var, d, h: pedidos.append((d, h)) or muestras([1.0]), lambda: CAT)
    assert t.describir("B-101", TS) is None
    assert pedidos[0] == ((TS - timedelta(hours=3)).isoformat(), TS.isoformat())

    def cae(*a):
        raise TimeoutError("historian caído")

    assert Tendencia(cae, lambda: CAT).describir("B-101", TS) is None
    assert Tendencia(cae, lambda: CAT).describir("TK-301", TS) is None  # sin variables para un tanque


def test_el_escalado_lleva_parte_fotos_y_tendencia_y_el_sin_novedad_no():
    f = [
        Fila("B-102", "presion_descarga_local", 1640.0, "psi", None, foto=None),
        Fila("B-102", "goteo", None, None, "pierde bastante por la empaquetadura", foto=FOTOS[0]),
        Fila("F-201A", "presion_diferencial", 1.0, "kg/cm2"),
    ]
    ev = construir("N-1", TS, f, CAT, tendencia=lambda tag, ts: f"últimas 3 h: de {tag}")
    b102, f201 = ev
    assert b102.tipo is TipoEvento.escalado
    assert "Parte del equipo" in b102.resumen and "pierde bastante" in b102.resumen
    assert FOTOS[0] in b102.resumen and "Tendencia (últimas 3 h: de B-102)" in b102.resumen
    assert (
        f201.tipo is TipoEvento.sin_novedad
        and "Tendencia" not in f201.resumen
        and "Fotos" not in f201.resumen
    )


def test_si_la_tendencia_falla_el_escalado_sale_igual():
    def rota(tag, ts):
        raise RuntimeError("sin historian")

    f = [Fila("B-102", "goteo", None, None, "pierde por la empaquetadura")]
    (ev,) = construir("N-1", TS, f, CAT, tendencia=rota)
    assert (
        ev.tipo is TipoEvento.escalado and "Tendencia" not in ev.resumen and "Parte del equipo" in ev.resumen
    )


def test_el_escalado_de_un_sensor_lleva_tambien_la_tendencia():
    emitidos = []
    det = DetectorSostenido(
        {("B-101", "presion_descarga"): (95.0, 125.0)},
        lambda tag: "marcha",
        lambda ev: emitidos.append(ev) or True,
        Parametros(),
        tendencia=lambda tag, ts: "últimas 3 h: presión de descarga sube",
    )
    for m in range(0, 9):
        det.procesar(
            {
                "tag": "B-101",
                "variable": "presion_descarga",
                "valor": 140.0,
                "ts": (TS + timedelta(minutes=m)).isoformat(),
                "calidad": "buena",
            }
        )
    (ev,) = emitidos
    assert "Tendencia (últimas 3 h: presión de descarga sube)" in ev.resumen and ev.origen == "sensor"
