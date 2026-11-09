"""Requisitos de infraestructura del Anexo de la Entrega 1, verificados sobre docker-compose.yml."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICIOS = COMPOSE["services"]

LIMITE_CPU = 4.0
LIMITE_MEM_GB = 8.0


def _memoria_gb(valor: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mg])", str(valor).lower())
    assert m, f"memoria con formato no soportado: {valor!r}"
    n = float(m.group(1))
    return n if m.group(2) == "g" else n / 1024


def _limites(servicio: dict) -> dict:
    return servicio["deploy"]["resources"]["limits"]


def test_hay_servicios_de_planta():
    assert {"agente", "db", "asr"} <= set(SERVICIOS)


def test_red_planta_lan_externa():
    assert COMPOSE["networks"]["planta-lan"] == {"external": True}


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_servicio_solo_en_planta_lan(nombre):
    assert SERVICIOS[nombre]["networks"] == ["planta-lan"]


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_servicio_con_label_de_planta(nombre):
    assert SERVICIOS[nombre]["labels"]["academia.planta"] == "pc"


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_servicio_sin_politica_de_reinicio(nombre):
    # El simulador apaga y enciende estos contenedores para el corte de luz.
    assert "restart" not in SERVICIOS[nombre]


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_servicio_no_publica_puertos(nombre):
    assert "ports" not in SERVICIOS[nombre]


@pytest.mark.parametrize("nombre", ["agente", "asr"])
def test_proxy_de_salida_por_el_enlace(nombre):
    env = SERVICIOS[nombre]["environment"]
    for clave in ("HTTP_PROXY", "HTTPS_PROXY"):
        assert env[clave] == "http://enlace:3128"
    no_proxy = set(env["NO_PROXY"].split(","))
    assert {"broker", "sim", "localhost"} <= no_proxy


@pytest.mark.parametrize("nombre", sorted(SERVICIOS))
def test_servicio_con_limites(nombre):
    limites = _limites(SERVICIOS[nombre])
    assert float(limites["cpus"]) > 0
    assert _memoria_gb(limites["memory"]) > 0


def test_suma_de_limites_dentro_de_4_cpu_y_8_gb():
    cpus = sum(float(_limites(s)["cpus"]) for s in SERVICIOS.values())
    mem = sum(_memoria_gb(_limites(s)["memory"]) for s in SERVICIOS.values())
    assert cpus <= LIMITE_CPU, f"{cpus} CPU"
    assert mem <= LIMITE_MEM_GB, f"{mem} GB"


def test_base_de_datos_es_postgres16_con_pgvector():
    dockerfile = (RAIZ / "docker" / "db" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM pgvector/pgvector:pg16\b", dockerfile, re.M)
    init = (RAIZ / "docker" / "db" / "init.sql").read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector" in init


def test_db_tiene_healthcheck_y_el_agente_la_espera():
    assert "healthcheck" in SERVICIOS["db"]
    assert SERVICIOS["agente"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_asr_en_su_propio_servicio_con_cuota_propia():
    """El ASR no comparte los 0,5 CPU del agente: protege los 30 s de la alerta de seguridad (B2)."""
    limites = _limites(SERVICIOS["asr"])
    assert float(limites["cpus"]) == 1.5
    assert _memoria_gb(limites["memory"]) == 1.5
    assert float(_limites(SERVICIOS["agente"])["cpus"]) == 0.5
    assert SERVICIOS["agente"]["depends_on"]["asr"]["condition"] == "service_healthy"
    assert "healthcheck" in SERVICIOS["asr"]


def test_el_modelo_viene_en_la_imagen_con_revision_y_hash_fijos():
    dockerfile = (RAIZ / "docker" / "asr.Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"ARG MODELO_REV=[0-9a-f]{40}", dockerfile)
    assert re.search(r"ARG MODELO_SHA256=[0-9a-f]{64}", dockerfile)
    assert "HF_HUB_OFFLINE=1" in dockerfile
