"""Detector S1–S4: aciertos, variantes de transcripción y corpus negativo (falsas alarmas restan 0,25)."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import TipoEvento

from sistema.deteccion import alertas
from sistema.deteccion.seguridad import detectar
from sistema.dominio.catalogo import Catalogo
from sistema.voz.texto import normalizar, oraciones

DEFINICION = {
    "equipos": [
        {"tag": "B-101", "tipo": "bomba", "nombres": ["la uno", "la 1", "la de la punta", "la del portón", "bomba uno"],
         "manometro_local": {"unidad": "kg/cm2"}},
        {"tag": "B-102", "tipo": "bomba", "nombres": ["la dos", "la 2", "la del medio", "bomba dos"],
         "manometro_local": {"unidad": "psi"}},
        {"tag": "B-103", "tipo": "bomba", "nombres": ["la tres", "la 3", "la del fondo", "la de reserva", "bomba tres"],
         "manometro_local": {"unidad": "bar"}},
        {"tag": "F-201A", "tipo": "filtro", "nombres": ["el filtro A", "el primario", "el de la izquierda", "el grueso"]},
        {"tag": "F-201B", "tipo": "filtro", "nombres": ["el filtro B", "el secundario", "el de la derecha", "el fino"]},
        {"tag": "TK-301", "tipo": "tanque", "nombres": ["el pulmón", "el tanque", "el tanque pulmón"]},
        {"tag": "FT-401", "tipo": "caudalimetro", "nombres": ["el caudalímetro", "el colector"]},
    ],
    "sensores": [],
}  # fmt: skip
CAT = Catalogo(DEFINICION)

# Los cuatro textos exactos que dicta el operador virtual (operador.py: nota_seguridad).
S1 = ("¡Atención! Se rompió algo en la descarga de la uno, sale agua a presión para todos lados, "
      "me alejé. Saqué foto desde lejos.")  # fmt: skip
S2 = "Hay olor a gas fuerte del lado del portón, viene del lado del separador."
S3 = "Sale humo del motor de la dos, hay olor a quemado."
S4 = "Mi compañero se golpeó la mano con la llave en el skid de filtros, está sangrando bastante."


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
    # negaciones
    "No hay olor a gas, todo normal.",
    "Sin humo en el motor de la dos.",
    "No sale humo de ningún motor.",
    "Nadie está herido, ningún accidente.",
    "No se rompió nada en la descarga.",
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
