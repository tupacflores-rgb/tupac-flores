"""A-9: rango histórico de 45 días, caché y recuperación del «mil» perdido por el ASR."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import NotaRecorrida, RespuestaOperador
from planta_falsa import DEFINICION

from sistema.config import Config
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import EstadoParte
from sistema.dominio.rango import RangoHistorico
from sistema.estado import RelojPlanta
from sistema.voz.dialogo import Dialogo
from sistema.voz.parser import extraer
from sistema.voz.texto import reparar_inicio_de_oracion

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)

# B-102 en marcha: 110,9–115,6 kg/cm² (datos reales del historian); el diferencial va de 0 a 1,33.
HISTORIA = {
    ("B-102", "presion_descarga"): [110.9, 113.0, 115.6],
    ("F-201A", "presion_diferencial"): [0.0, 0.8, 1.33],
}


def rango(historia=None):
    r = RangoHistorico(lambda tag, var: (historia or HISTORIA)[(tag, var)])
    r.precalentar(list(HISTORIA))
    return r


def test_el_rango_agrega_un_margen_y_se_cachea():
    llamadas = []

    def historia(tag, var):
        llamadas.append((tag, var))
        return [110.0, 115.0]

    r = RangoHistorico(historia)
    a = r.rango(
        "B-102", "presion_descarga_local"
    )  # la variable dicha en campo usa el historian de la descarga
    assert (a.minimo, a.maximo) == (98.5, 126.5)
    r.rango("B-102", "presion_descarga")
    assert llamadas == [("B-102", "presion_descarga")]  # una sola consulta


def test_el_rango_vence_y_se_vuelve_a_pedir():
    t = [0.0]
    n = []
    r = RangoHistorico(lambda tag, var: n.append(1) or [100.0, 110.0], reloj=lambda: t[0])
    r.rango("B-101", "presion_descarga")
    t[0] = 4000.0
    r.rango("B-101", "presion_descarga")
    assert len(n) == 2


def test_sin_historian_no_se_opina_ni_se_bloquea():
    def cae(tag, var):
        raise TimeoutError("historian caído")

    r = RangoHistorico(cae)
    assert r.rango("B-102", "presion_descarga") is None
    assert r.evaluar("B-102", "presion_descarga_local", 600.0, "psi") is None


def test_evalua_dentro_fuera_y_el_mil_perdido():
    r = rango()
    assert r.evaluar("B-102", "presion_descarga_local", 1640.0, "psi").dentro  # 115,3 kg/cm²
    ev = r.evaluar("B-102", "presion_descarga_local", 600.0, "psi")  # 42 kg/cm²
    assert not ev.dentro and ev.con_mil == 1600.0
    sin = r.evaluar(
        "B-102", "presion_descarga_local", 200.0, "psi"
    )  # con mil serían 1200 psi = 84 kg/cm²: tampoco
    assert not sin.dentro and sin.con_mil is None
    assert r.evaluar("B-102", "temperatura_rodamiento_ir", 59.0, "degC") is None  # sin historian


def test_el_mil_solo_se_recupera_en_psi():
    r = rango()
    ev = r.evaluar("F-201A", "presion_diferencial", 18.0, "kg/cm2")
    assert not ev.dentro and ev.con_mil is None


# -- en el diálogo ------------------------------------------------------------------------------------


class Salida:
    def __init__(self):
        self.mensajes = []

    def emitir(self, id_, topico, modelo):
        self.mensajes.append(modelo)
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


def dialogo(con_rango=True):
    reloj = RelojPlanta()
    reloj.fijar(TS)
    salida, confirmados = Salida(), []
    d = Dialogo(
        Config(), salida, Base(), lambda: CAT, reloj, confirmados.append, rango=rango() if con_rango else None
    )
    nota = NotaRecorrida(id="NOTA-00011", ts=TS, operador="x", audio_url="/a.wav", fotos=[], recorrida_id="R")
    return d, salida, confirmados, nota


def test_el_mil_perdido_se_muestra_provisional_con_aviso_y_sin_opciones():
    d, salida, _, nota = dialogo()
    d.procesar_nota(nota, "La dos está en seiscientos psi de descarga.", None)
    m = salida.mensajes[-1]
    (fila,) = m.pantalla.filas
    assert fila.valor == 1600.0  # se muestra el valor con el mil recuperado
    assert fila.observacion.startswith("provisional")
    assert m.pantalla.opciones == []  # sin opciones: no es una pregunta
    assert "fuera de lo que midió" in m.texto and "mil" in m.texto and "Ojo" in m.texto
    assert m.espera_respuesta


def test_confirmar_el_valor_con_el_mil_genera_el_parte_con_1600_y_no_queda_provisional():
    d, salida, confirmados, nota = dialogo()
    d.procesar_nota(nota, "La dos está en seiscientos psi de descarga.", None)
    d.procesar_respuesta(
        RespuestaOperador(
            id="R1",
            ts=TS,
            mensaje_id=salida.mensajes[-1].id,
            operador="x",
            texto="Sí, confirmo.",
            audio_url=None,
        )
    )
    (est,) = confirmados
    assert est.estado is EstadoParte.confirmado
    assert est.filas[0].valor == 1600.0 and est.filas[0].provisional is False


def test_un_valor_normal_no_lleva_aviso():
    d, salida, _, nota = dialogo()
    d.procesar_nota(nota, "La dos está en mil seiscientos cuarenta psi de descarga.", None)
    m = salida.mensajes[-1]
    assert "Ojo" not in m.texto and m.pantalla.opciones == []


def test_fuera_de_rango_sin_mil_posible_avisa_pero_no_cambia_el_valor():
    d, salida, _, nota = dialogo()
    d.procesar_nota(nota, "El filtro A me dio dos coma nueve de diferencial.", None)
    m = salida.mensajes[-1]
    assert m.pantalla.filas[0].valor == pytest.approx(2.9)
    assert "fuera de lo que midió" in m.texto and "se perdió" not in m.texto


def test_sin_rango_el_dialogo_se_comporta_como_antes():
    d, salida, _, nota = dialogo(con_rango=False)
    d.procesar_nota(nota, "La dos está en seiscientos psi de descarga.", None)
    m = salida.mensajes[-1]
    assert m.pantalla.filas[0].valor == 600.0 and "Ojo" not in m.texto


def test_el_estado_con_el_aviso_se_persiste():
    d, _, _, nota = dialogo()
    d.procesar_nota(nota, "La dos está en seiscientos psi de descarga.", None)
    assert d._base.d["NOTA-00011"]["filas_con_mil"][0]["dicho"] == 600.0


# -- «A uno estar en…» --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("A uno estar en 112 kilogramos de descarga.", "la uno estar en 112 kilogramos de descarga."),
        ("La tres parada. A dos gotea un poco.", "La tres parada. la dos gotea un poco."),
        ("Subió a uno coma ocho de diferencial.", "Subió a uno coma ocho de diferencial."),
        ("Pasó a dos metros del pulmón.", "Pasó a dos metros del pulmón."),
    ],
)
def test_reparar_el_articulo_comido_solo_al_inicio_de_la_oracion(crudo, esperado):
    assert reparar_inicio_de_oracion(crudo) == esperado


def test_a_uno_al_comienzo_ya_no_pierde_el_tramo_de_la_bomba():
    ext = extraer(
        "A uno estar en 112 kilogramos de descarga el rodamiento medio 59 grados con la pistola. Ambient la bomba está en 1600 psi de descarga. El fito a medio 1,3 de diferencial.",
        CAT,
    )
    filas = [(f.tag, f.variable, f.valor) for f in ext.filas]
    assert ("B-101", "presion_descarga_local", 112.0) in filas
    assert ("B-101", "temperatura_rodamiento_ir", 59.0) in filas


def test_decena_con_la_p_de_psi_pegada_se_lee_bien():
    ext = extraer("Este, la del medio está en mil seiscientos diezp psi de descarga.", CAT)
    assert [(f.tag, f.valor, f.unidad) for f in ext.filas] == [("B-102", 1610.0, "psi")]


def test_el_frito_a_es_el_filtro_a_pero_foto_y_falta_siguen_siendo_otra_cosa():
    ext = extraer(
        "Ambienta bomba, está en mil seiscientos psi de descarga, el frito a medio uno coma tres de diferencial.",
        CAT,
    )
    assert ("F-201A", "presion_diferencial", 1.3) in [(f.tag, f.variable, f.valor) for f in ext.filas]
    sin = extraer("Le saqué una foto a la uno que está en ciento doce kilos de descarga.", CAT)
    assert [f.tag for f in sin.filas] == ["B-101"]


def test_estaparada_pegado_se_lee_como_esta_parada_y_la_nota_sigue_su_camino():
    from sistema.voz.texto import normalizar, reparar_asr

    assert reparar_asr(normalizar("La bomba estaparada en reserva.")) == "la bomba esta parada en reserva"
    assert (
        reparar_asr(normalizar("La estacion de bombeo")) == "la estacion de bombeo"
    )  # no toca otras palabras
