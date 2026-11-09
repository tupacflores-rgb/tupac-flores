"""Eventos por equipo de un parte confirmado (B1) y sincronización de pendientes (E2)."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import TipoEvento
from planta_falsa import DEFINICION

from sistema.deteccion.eventos import construir, es_anomalia, fuera_de_limite
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import Fila
from sistema.estado import EstadoCompartido
from sistema.sincronizacion import Sincronizacion

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("valor", "unidad", "tag", "fuera"),
    [
        (115, "kg/cm2", "B-101", False),
        (126, "kg/cm2", "B-101", True),  # >= 125
        (125, "kg/cm2", "B-101", True),  # el límite inclusive, como puntuar.py
        (94, "kg/cm2", "B-101", True),  # <= 95
        (1640, "psi", "B-102", False),  # 115,3 kg/cm2
        (1800, "psi", "B-102", True),  # 126,6 kg/cm2
        (1300, "psi", "B-102", True),  # 91,4 kg/cm2
        (110, "bar", "B-103", False),  # 112,2 kg/cm2
        (125, "bar", "B-103", True),  # 127,5 kg/cm2
    ],
)
def test_presion_de_descarga_contra_los_limites_con_conversion(valor, unidad, tag, fuera):
    assert fuera_de_limite(Fila(tag, "presion_descarga_local", float(valor), unidad), CAT) is fuera


@pytest.mark.parametrize(
    ("valor", "fuera"), [(1.3, False), (1.5, True), (1.49, False), (0.15, True), (0.9, False)]
)
def test_diferencial_contra_los_limites(valor, fuera):
    assert fuera_de_limite(Fila("F-201A", "presion_diferencial", valor, "kg/cm2"), CAT) is fuera


@pytest.mark.parametrize(
    ("obs", "anomalia"),
    [
        (None, False),
        ("parada, en reserva", False),
        ("parada por mantenimiento", False),
        ("disparada, no arranca", True),
        ("goteo por empaquetadura", True),
        ("rebalse a pileta", True),
        ("rodamiento caliente", True),
        ("correas flojas", True),
    ],
)
def test_observaciones_normales_y_anomalas(obs, anomalia):
    assert es_anomalia(Fila("B-101", "estado", None, None, obs), CAT) is anomalia


def tipos(filas):
    return {e.equipo: e.tipo for e in construir("NOTA-00001", TS, filas, CAT)}


def test_un_evento_por_equipo_con_id_ts_y_nota_deterministas():
    filas = [
        Fila("B-102", "presion_descarga_local", 1640.0, "psi"),
        Fila("B-102", "goteo_empaquetadura", None, "gotas/min", "goteo por empaquetadura"),
        Fila("F-201A", "presion_diferencial", 1.3, "kg/cm2"),
    ]
    eventos = construir("NOTA-00001", TS, filas, CAT)
    assert [e.id for e in eventos] == ["EV-NOTA-00001-B-102", "EV-NOTA-00001-F-201A"]
    assert [e.tipo for e in eventos] == [TipoEvento.escalado, TipoEvento.sin_novedad]
    assert all(e.ts == TS and e.nota_ids == ["NOTA-00001"] and e.origen == "parte" for e in eventos)


def test_escalado_gana_dentro_de_un_mismo_equipo():
    assert tipos(
        [
            Fila("B-101", "presion_descarga_local", 115.0, "kg/cm2"),
            Fila("B-101", "ruido", None, None, "hace un golpeteo metálico"),
        ]
    ) == {"B-101": TipoEvento.escalado}


def test_bomba_en_reserva_es_sin_novedad():
    assert tipos([Fila("B-103", "estado", None, None, "parada, en reserva")]) == {
        "B-103": TipoEvento.sin_novedad
    }


def test_la_condicion_insegura_no_genera_evento_de_b1():
    assert tipos([Fila("B-101", "condicion_insegura", None, None, "se rompió algo")]) == {}


def test_filas_sin_equipo_no_generan_eventos():
    assert tipos([Fila(None, "presion_descarga_local", 115.0, "kg/cm2")]) == {}


# -- sincronización ---------------------------------------------------------------------------------


def test_con_enlace_nada_queda_pendiente():
    estado = EstadoCompartido()
    sync = Sincronizacion(estado)
    sync.registrar("NOTA-1")
    assert estado.pendientes == 0


def test_sin_enlace_los_partes_quedan_pendientes_y_al_volver_se_vacian():
    estado = EstadoCompartido()
    sync = Sincronizacion(estado)
    estado.enlace_ok = False
    sync.registrar("NOTA-1")
    sync.registrar("NOTA-2")
    sync.registrar("NOTA-2")  # el mismo parte no cuenta dos veces
    assert estado.pendientes == 2
    assert sync.sincronizar() == 0 and estado.pendientes == 2  # sigue caído
    estado.enlace_ok = True
    assert sync.sincronizar() == 2
    assert estado.pendientes == 0


def test_una_nota_insegura_procesada_sin_enlace_tambien_queda_pendiente():
    """En fuga-sin-enlace la única nota del corte es la S1, que nunca se confirma: igual hay trabajo sin sincronizar."""
    estado = EstadoCompartido()
    sync = Sincronizacion(estado)
    estado.enlace_ok = False
    sync.registrar("NOTA-S1")
    assert estado.pendientes == 1
