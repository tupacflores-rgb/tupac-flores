"""Tests unitarios para la CLI de decisiones humanas HITL."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from sistema.workflow.cli import construir_mensaje_decision, main, publicar_decision
from sistema.workflow.topicos import (
    TOPICO_DECISION_INSPECTOR,
    TOPICO_DECISION_PLANIFICADOR,
    TOPICO_DECISION_SUPERVISOR,
)


@pytest.mark.parametrize(
    ("rol", "decision", "topico_esperado"),
    [
        ("planificador", "aprobada", TOPICO_DECISION_PLANIFICADOR),
        ("planificador", "rechazada", TOPICO_DECISION_PLANIFICADOR),
        ("supervisor", "cerrar", TOPICO_DECISION_SUPERVISOR),
        ("inspector", "certificar", TOPICO_DECISION_INSPECTOR),
        ("inspector", "observar", TOPICO_DECISION_INSPECTOR),
    ],
)
def test_construir_mensaje_decision_roles_y_decisiones_validas(rol, decision, topico_esperado):
    topico, payload = construir_mensaje_decision(rol, "OT-2026-001", decision)

    assert topico == topico_esperado
    assert json.loads(payload) == {"orden_id": "OT-2026-001", "decision": decision}


def test_publicar_decision_dry_run_imprime_y_no_publica(capsys):
    llamadas = []

    def publicador_falso(*args, **kwargs):
        llamadas.append((args, kwargs))

    topico, payload = publicar_decision(
        "planificador",
        "OT-10",
        "aprobada",
        dry_run=True,
        publicador=publicador_falso,
    )

    salida = capsys.readouterr().out
    assert llamadas == []
    assert topico == TOPICO_DECISION_PLANIFICADOR
    assert topico in salida
    assert payload in salida
    assert json.loads(payload) == {"orden_id": "OT-10", "decision": "aprobada"}


def test_publicar_decision_real_inyectado_verifica_parametros():
    llamadas = []

    def publicador_falso(topico, *, payload, hostname, port, qos):
        llamadas.append((topico, payload, hostname, port, qos))

    topico, payload = publicar_decision(
        "inspector",
        "OT-99",
        "certificar",
        mqtt_host="broker.local",
        mqtt_port=1884,
        dry_run=False,
        publicador=publicador_falso,
    )

    assert topico == TOPICO_DECISION_INSPECTOR
    assert len(llamadas) == 1
    topico_pub, payload_pub, hostname, port, qos = llamadas[0]
    assert topico_pub == TOPICO_DECISION_INSPECTOR
    assert json.loads(payload_pub) == {"orden_id": "OT-99", "decision": "certificar"}
    assert payload_pub == payload
    assert hostname == "broker.local"
    assert port == 1884
    assert qos == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--mqtt-host", "mqtt.interno", "--mqtt-port", "1885", "supervisor", "OT-77", "--decision", "cerrar"],
        ["supervisor", "OT-77", "--decision", "cerrar", "--mqtt-host", "mqtt.interno", "--mqtt-port", "1885"],
    ],
)
def test_main_publica_con_flags_antes_y_despues_del_subcomando(argv):
    with patch("sistema.workflow.cli.mqtt_publish.single") as mock_single:
        rc = main(argv)

    assert rc == 0
    mock_single.assert_called_once()
    args, kwargs = mock_single.call_args
    assert args == (TOPICO_DECISION_SUPERVISOR,)
    assert json.loads(kwargs["payload"]) == {"orden_id": "OT-77", "decision": "cerrar"}
    assert kwargs["hostname"] == "mqtt.interno"
    assert kwargs["port"] == 1885
    assert kwargs["qos"] == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--dry-run", "planificador", "OT-5", "--decision", "rechazada"],
        ["planificador", "OT-5", "--decision", "rechazada", "--dry-run"],
    ],
)
def test_main_dry_run_antes_y_despues_no_llama_broker(argv, capsys):
    with patch("sistema.workflow.cli.mqtt_publish.single") as mock_single:
        rc = main(argv)

    assert rc == 0
    mock_single.assert_not_called()
    salida = capsys.readouterr().out
    assert TOPICO_DECISION_PLANIFICADOR in salida
    assert '"orden_id":"OT-5"' in salida
    assert '"decision":"rechazada"' in salida


def test_main_usa_variables_de_entorno_por_defecto(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "mosquitto-env")
    monkeypatch.setenv("MQTT_PORT", "2883")

    with patch("sistema.workflow.cli.mqtt_publish.single") as mock_single:
        rc = main(["inspector", "OT-88", "--decision", "observar"])

    assert rc == 0
    _, kwargs = mock_single.call_args
    assert kwargs["hostname"] == "mosquitto-env"
    assert kwargs["port"] == 2883
    assert kwargs["qos"] == 1


def test_construir_mensaje_decision_rechaza_rol_orden_vacia_y_decision_invalida():
    with pytest.raises(ValueError, match="Rol desconocido"):
        construir_mensaje_decision("gerente", "OT-1", "aprobada")

    with pytest.raises(ValidationError):
        construir_mensaje_decision("planificador", "", "aprobada")

    with pytest.raises(ValidationError):
        construir_mensaje_decision("planificador", "OT-1", "cerrar")

    with pytest.raises(ValidationError):
        construir_mensaje_decision("supervisor", "OT-1", "aprobada")

    with pytest.raises(ValidationError):
        construir_mensaje_decision("inspector", "OT-1", "rechazada")


@pytest.mark.parametrize(
    "argv",
    [
        ["gerente", "OT-1", "--decision", "aprobada"],
        ["planificador", "OT-1", "--decision", "cerrar"],
        ["supervisor", "OT-1", "--decision", "aprobada"],
        ["inspector", "OT-1", "--decision", "rechazada"],
        ["planificador", "OT-1"],
        [],
    ],
)
def test_main_rechaza_argumentos_invalidos_con_system_exit(argv):
    with pytest.raises(SystemExit) as exc_info:
        main(argv)

    assert exc_info.value.code != 0


def test_main_rechaza_orden_id_vacio(capsys):
    with patch("sistema.workflow.cli.mqtt_publish.single") as mock_single:
        rc = main(["planificador", "", "--decision", "aprobada"])

    assert rc != 0
    mock_single.assert_not_called()
    err = capsys.readouterr().err
    assert "Error de validación" in err
