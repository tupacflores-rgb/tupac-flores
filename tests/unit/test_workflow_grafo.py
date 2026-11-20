import pytest

from sistema.workflow.grafo import (
    MAX_REINTENTOS_REVISION,
    _ruta_aprobacion_reglas,
    _ruta_decision_inspector,
    _ruta_decision_planificador,
    _ruta_ejecutor,
    _ruta_revision,
    construir_grafo,
)


def test_grafo_compila_con_todos_los_nodos():
    grafo = construir_grafo()

    assert {
        "analizar_fotos",
        "revisar_orden",
        "esperar_decision",
        "esperar_cierre",
        "esperar_inspector",
        "calcular_habilita",
    } <= set(grafo.get_graph().nodes)


@pytest.mark.parametrize(
    ("state", "esperado"),
    [
        ({"resultado_revision": "valida"}, "evaluar_aprobacion"),
        ({"resultado_revision": "invalida", "reintentos_revision": 1}, "redactar_orden"),
        (
            {"resultado_revision": "invalida", "reintentos_revision": MAX_REINTENTOS_REVISION + 1},
            "rechazar_orden",
        ),
    ],
)
def test_ruta_revision(state, esperado):
    assert _ruta_revision(state) == esperado


@pytest.mark.parametrize(
    ("state", "esperado"),
    [
        ({"aprobada_por_reglas": True}, "guardar_aprobada"),
        ({"aprobada_por_reglas": False}, "guardar_pendiente"),
    ],
)
def test_ruta_aprobacion_reglas(state, esperado):
    assert _ruta_aprobacion_reglas(state) == esperado


@pytest.mark.parametrize(
    ("state", "ruta", "esperado"),
    [
        ({"decision_planificador": "aprobada"}, _ruta_decision_planificador, "guardar_aprobada"),
        ({"decision_planificador": "rechazada"}, _ruta_decision_planificador, "rechazar_orden"),
        ({"ejecutor": "propio"}, _ruta_ejecutor, "esperar_supervisor"),
        ({"ejecutor": "contratista"}, _ruta_ejecutor, "esperar_inspector"),
        ({"decision_inspector": "observar"}, _ruta_decision_inspector, "guardar_observada"),
        ({"decision_inspector": "certificar"}, _ruta_decision_inspector, "calcular_habilita"),
    ],
)
def test_rutas_decisiones(state, ruta, esperado):
    assert ruta(state) == esperado


def test_rutas_requieren_decisiones_explicitas():
    with pytest.raises(ValueError):
        _ruta_decision_inspector({})
