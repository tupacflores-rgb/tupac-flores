"""Detector S1–S4: aciertos, variantes de transcripción y corpus negativo (falsas alarmas restan 0,25)."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import TipoEvento
from planta_falsa import DEFINICION, S1, S2, S3, S4

from sistema.deteccion import alertas
from sistema.deteccion.seguridad import detectar
from sistema.dominio.catalogo import Catalogo
from sistema.voz.texto import normalizar, oraciones

CAT = Catalogo(DEFINICION)


@pytest.mark.parametrize(
    ("texto", "codigo", "equipo"),
    [
        (S1, "S1", "B-101"),
        (S2, "S2", None),
        (S3, "S3", "B-102"),
        (S4, "S4", None),
        # variantes de lo que puede devolver Whisper
        ("Atención se rompió algo en la descarga de la 3 sale agua a presión para todos lados me alejé", "S1", "B-103"),
        ("Se rompio la manguera de la descarga de la del medio", "S1", "B-102"),
        ("hay olor al gas fuerte cerca del separador", "S2", None),
        ("Sale humo del motor de la 1. Hay olor a quemado", "S3", "B-101"),
        ("hay humo del lado del motor de la bomba dos", "S3", "B-102"),
        ("mi compañero esta sangrando de la mano", "S4", None),
        ("se lastimó un compañero con la llave", "S4", None),
    ],
)  # fmt: skip
def test_detecta_las_cuatro_condiciones(texto, codigo, equipo):
    d = detectar(texto, CAT)
    assert d is not None
    assert (d.codigo, d.equipo) == (codigo, equipo)


# Notas normales del operador virtual y casos que comparten vocabulario con las inseguras.
NEGATIVOS = [
    "La dos pierde bastante por la empaquetadura, le saqué foto.",
    "Chorrea por la empaquetadura, la uno, le saqué foto.",
    "La tres sigue goteando por la empaquetadura. Le saqué foto.",
    "En la dos, el rodamiento me dio ochenta grados con la pistola.",
    "La uno hace un golpeteo metálico.",
    "La tres suena como si tuviera piedras adentro.",
    "Las correas están flojas, chillan, saqué foto.",
    "El pulmón está en seis coma dos metros, está rebalsando a la pileta.",
    "El filtro A me dio uno coma ocho de diferencial, le saqué foto al manómetro.",
    "La dos está en mil seiscientos cuarenta psi de descarga.",
    "El visor de aceite está bajo.",
    "Casi no tiene aceite en el visor.",
    "La tres está parada, en reserva.",
    "La uno está disparada, no arranca.",
    "Recorrida sin novedad, todo normal.",
    "Bueno, la bomba está en ciento quince kilos de descarga. Además el primario me dio uno ocho de diferencial.",
    # negación mal oída por el ASR (medida: «Lo hay olor a gas» por «No hay olor a gas»)
    "Recorrida de las cuatro. Lo hay olor a gas, no sale humo de ningún motor y nadie está herido.",
    # negaciones
    "No hay olor a gas, todo normal.",
    "Sin humo en el motor de la dos.",
    "No sale humo de ningún motor.",
    "Nadie está herido, ningún accidente.",
    "No se rompió nada en la descarga.",
    # negación partida entre segmentos del ASR (los segmentos se unen con un espacio, a veces con un punto)
    " ".join(["Revisé la tres, no hay", "olor a gas en el sector."]),
    " ".join(["Revisé el sector del portón y no", "hay olor a gas."]),
    " ".join(["En la motobomba, sin.", "Humo en ningún lado."]),
    " ".join(["En el skid de filtros no hay...", "olor a gas, todo normal."]),
    " ".join(["Está todo bien en la uno, nadie se", "golpeó ni se lastimó."]),
    # palabras sueltas: no alcanzan
    "La presión de la dos está bien.",
    "Hay mucho gas en el tanque de la otra planta pero el nuestro está bien",
    "Humo",
    "Hay agua en la pileta.",
]


@pytest.mark.parametrize("texto", NEGATIVOS)
def test_no_hay_falsas_alarmas(texto):
    assert detectar(texto, CAT) is None, texto


def test_la_negacion_vale_solo_dentro_de_la_oracion():
    d = detectar("No hay nada raro en la uno. Sale humo del motor de la dos.", CAT)
    assert d is not None
    assert (d.codigo, d.equipo) == ("S3", "B-102")


def test_sin_catalogo_no_resuelve_equipo_pero_detecta():
    d = detectar(S1, None)
    assert (d.codigo, d.equipo) == ("S1", None)


def test_normalizacion_conserva_decimales_y_quita_signos():
    assert normalizar("¡Atención! La uno: 1,8 kg/cm². Mil 640.") == "atencion la uno 1,8 kg/cm2 mil 640"
    assert oraciones("Hola. ¿Qué tal? Bien; ok") == ["hola", "que tal", "bien", "ok"]


# -- la alerta ----------------------------------------------------------------------------------


class NotaFalsa:
    id = "NOTA-00007"
    ts = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)


def test_la_alerta_usa_ids_deterministas_y_el_ts_de_la_nota():
    d = detectar(S1, CAT)
    evento, aviso = alertas.construir(NotaFalsa(), d)
    assert evento.id == "EV-SEG-NOTA-00007"
    assert aviso.id == "TAB-SEG-NOTA-00007"
    assert evento.tipo is TipoEvento.alerta_seguridad
    assert evento.equipo == "B-101"
    assert evento.ts == aviso.ts == NotaFalsa.ts
    assert evento.nota_ids == aviso.nota_ids == ["NOTA-00007"]
    assert aviso.espera_respuesta is False


def test_s2_y_s4_no_asignan_equipo():
    """D14 provisional: sin filas ni TAG inventado; el evento lleva equipo=None."""
    for texto in (S2, S4):
        evento, _ = alertas.construir(NotaFalsa(), detectar(texto, CAT))
        assert evento.equipo is None


def test_alerta_real_despues_de_un_no_suelto_no_se_pierde():
    """Un «No.» completo (respuesta o muletilla) no es una negación colgante: la alerta siguiente sale."""
    d = detectar("¿Todo bien? No. Sale humo del motor de la dos.", CAT)
    assert d is not None and d.codigo == "S3"
    d = detectar(" ".join(["Hay algo raro, no.", "Sale humo del motor de la dos."]), CAT)
    assert d is not None and d.codigo == "S3"


@pytest.mark.parametrize(
    "texto",
    [
        "Hay hólogas fuertes del lado del portón, viene del lado del separador.",  # transcripción real del ASR
        "Hay olorgas fuerte en el portón.",
        "Huele a gas del lado del separador.",
        "Hay hologas en el sector del separador.",
        "Hay olor, hagas fuertes del lado del portón, viene del lado del separador.",  # ASR real
        "Sale uno del motor de la arela punta, hay olores quemados.",  # ASR real (S3)
        "Sale uno del motor de la dos, hay olor quemado.",  # ASR real (S3)
    ],
)
def test_s2_tolera_los_errores_medidos_del_asr(texto):
    d = detectar(texto, CAT)
    assert d is not None and d.codigo in {"S2", "S3"}, texto


@pytest.mark.parametrize(
    "texto",
    [
        "No hay hólogas fuertes en el portón.",
        "El visor tiene hologramas de la marca.",
        "Sin olorgas en el sector.",
        "No hay olores quemados en el tablero.",
        "Sale uno del filtro a la pileta.",
    ],
)
def test_la_reparacion_de_s2_no_crea_falsas_alarmas(texto):
    assert detectar(texto, CAT) is None, texto


@pytest.mark.parametrize(
    ("texto", "codigo"),
    [
        (
            "Atención, te rompió algo en la descarga de la de la punta, salí agua a presión para todos lados.",
            "S1",
        ),
        (
            "Atención se rompió algo en la descarga de la del fondo. Saler agua a presión para todos lados",
            "S1",
        ),
        ("Hay olor hagas fuerte del lado del portón.", "S2"),
        ("Sale como del motor de la dos, hay olor a quemao.", "S3"),
        (
            "Ni compañero se evulpeó la mano con la llave en el skip de filtros, está sangrando bastante.",
            "S4",
        ),
        ("Y compañero se golpeó la mano con la llave, está sangran do bastante.", "S4"),
    ],
)
def test_coincidencia_aproximada_de_las_frases_de_las_plantillas(texto, codigo):
    d = detectar(texto, CAT)
    assert d is not None and d.codigo == codigo, texto


@pytest.mark.parametrize(
    "texto",
    [
        "Hay olor a gasoil en el tanque de la otra planta.",
        "La presión del agua en la dos está bien.",
        "No hay olor a quemado ni humo en el motor.",
        "No sale humo del motor de la dos.",
        "Todavía no está sangrando la junta, solo gotea.",
        "Se rompió una bolsa de cartuchos en el depósito.",
        "Sale vapor del motor de la bomba de achique.",
    ],
)
def test_la_coincidencia_aproximada_no_crea_falsas_alarmas(texto):
    assert detectar(texto, CAT) is None, texto
