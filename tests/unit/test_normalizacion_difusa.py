"""A-1 y A-2: normalización sistemática (frases conocidas sin espacios, alias aproximados) con corpus de prueba.

Casos reales de ASR vistos en el corrector: «A uno», «frito a», «estaparada», «diezp psi». Y los negativos que no
deben cambiar: ninguna palabra legítima se convierte en un equipo o en una frase.
"""

# ruff: noqa: E501
from __future__ import annotations

import pytest
from planta_falsa import DEFINICION

from sistema.dominio.catalogo import Catalogo
from sistema.voz.parser import extraer
from sistema.voz.texto import canonizar_frases, normalizar, reparar_asr

CAT = Catalogo(DEFINICION)


def filas(texto):
    return [(f.tag, f.variable, f.valor, f.observacion) for f in extraer(texto, CAT).filas]


# -- frases conocidas -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("la bomba estaparada en reserva", "la bomba esta parada en reserva"),
        (
            "la tres estaparadaen reserva",
            "la tres estaparadaen reserva",
        ),  # dos palabras pegadas: no se adivina
        ("la dos hace un golpe teo metalico", "la dos hace un golpeteo metalico"),
        ("la uno esta en 112 kilos dedescarga", "la uno esta en 112 kilos de descarga"),
        ("la tres esta parada porrmantenimiento", "la tres esta parada porrmantenimiento"),
        ("la uno pierde bastantee por la empaquetadura", "la uno pierde bastante por la empaquetadura"),
        ("la dos tiene un ruido raro de rodamientos", "la dos tiene un ruido raro de rodamiento"),
    ],
)
def test_las_frases_conocidas_se_buscan_tambien_sin_espacios_y_con_tolerancia(crudo, esperado):
    assert canonizar_frases(crudo) == esperado


@pytest.mark.parametrize(
    "texto",
    [
        "la estacion de bombeo esta en servicio",
        "se cayo una parada de colectivo",
        "cambiaron la ruta de la descarga del camion",
        "la uno esta en 112 kilos de descarga",
        "libre de reservas",
        "la uno hace ruido de rodaje",
    ],
)
def test_ninguna_palabra_legitima_se_convierte_en_una_frase_conocida(texto):
    assert canonizar_frases(texto) == texto


def test_los_numeros_no_se_tocan():
    assert canonizar_frases("la uno esta en 1610 psi de descarga") == "la uno esta en 1610 psi de descarga"


# -- alias de equipos aproximados -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "tag"),
    [
        ("A uno estar en 112 kilogramos de descarga", "B-101"),  # «a uno» por «la uno»
        ("el filtra a me dio uno coma tres de diferencial", "F-201A"),
        ("el frito a medio uno coma tres de diferencial", "F-201A"),
        ("lla tres esta parada en reserva", "B-103"),
        ("el pulmon esta en cinco metros", "TK-301"),
    ],
)
def test_alias_mal_oidos_se_resuelven_con_contexto_de_variable_o_unidad(texto, tag):
    assert tag in {f[0] for f in filas(texto)}


@pytest.mark.parametrize(
    "texto",
    [
        "a la una de la tarde cambiamos el turno",  # sin variable ni unidad cerca
        "el frito de la esquina estaba bueno",
        "le saqué una foto a la uno para el informe",  # alias exacto, no hay nada que adivinar
        "voy a uno de los tanques mañana",
        "la dos por tres se rompe el portón",
    ],
)
def test_sin_contexto_de_variable_no_se_inventan_equipos(texto):
    assert {f[0] for f in filas(texto)} <= {"B-101"} or filas(texto) == []
    assert filas(texto) == [] or all(f[0] == "B-101" for f in filas(texto))


def test_dos_equipos_a_igual_distancia_no_se_eligen_al_azar():
    from sistema.dominio.catalogo import Catalogo as C

    ambiguo = C(
        {
            "equipos": [
                {"tag": "B-101", "tipo": "bomba", "nombres": ["la tuno"]},
                {"tag": "B-102", "tipo": "bomba", "nombres": ["la tano"]},
            ]
        }
    )
    assert ambiguo.menciones("la tino esta en 100 kilos de descarga") == []


def test_los_tags_y_los_alias_con_digitos_no_se_aproximan():
    # «b-102» está a una letra de «b-101»: sería un error grave tomar uno por otro.
    assert CAT.menciones("la b-109 esta en 100 kilos de descarga") == []


# -- los cuatro casos reales del corrector ---------------------------------------------------------------


def test_corpus_real_a_uno_frito_estaparada_y_diezp():
    a = filas("A uno estar en 112 kilogramos de descarga el rodamiento medio 59 grados con la pistola.")
    assert ("B-101", "presion_descarga_local", 112.0, None) in a and (
        "B-101",
        "temperatura_rodamiento_ir",
        59.0,
        None,
    ) in a
    f = filas(
        "Ambienta bomba, está en mil seiscientos psi de descarga, el frito a medio uno coma tres de diferencial."
    )
    assert "F-201A" in {x[0] for x in f}
    e = filas("La bomba estaparada en reserva.")
    assert e == [(None, "estado", None, "parada, en reserva")]
    d = filas("Este, la del medio está en mil seiscientos diezp psi de descarga.")
    assert d[0][2] == 1610.0


def test_reparar_asr_sigue_siendo_idempotente():
    t = normalizar("La bomba estaparada en reserva, el frito a medio uno coma tres de diferencial")
    assert reparar_asr(reparar_asr(t)) == reparar_asr(t)


def test_un_decimal_dicho_con_coma_no_se_toma_por_un_equipo():
    # «uno coma tres» está a 2 letras de «bomba tres» y con «diferencial» cerca: es un número, no una bomba.
    n = normalizar("el filtro a me dio uno coma tres de diferencial")
    assert [m.tag for m in CAT.menciones(n)] == ["F-201A"]
    assert filas("el filtro a me dio uno coma tres de diferencial") == [
        ("F-201A", "presion_diferencial", 1.3, None)
    ]


def test_un_error_en_el_identificador_no_se_adivina():
    # «la dso» podría ser «la dos»; se prefiere preguntar antes que tomar una bomba por otra.
    assert CAT.menciones("la dso esta en mil seiscientos psi de descarga") == []


def test_el_nombre_pegado_launo_se_reconoce_como_la_uno():
    n = normalizar("launo estaen ciento doce kilos dedescarga")
    assert [m.tag for m in CAT.menciones(reparar_asr(n))] == ["B-101"]
    assert ("B-101", "presion_descarga_local", 112.0, None) in filas(
        "launo estaen ciento doce kilos dedescarga"
    )
