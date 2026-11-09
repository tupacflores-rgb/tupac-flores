"""El contrato oficial se instala con pip y es el que fija `pyproject.toml`."""

from __future__ import annotations

from datetime import UTC, datetime

import planta_contrato as contrato
import pytest
from pydantic import ValidationError

from sistema.config import Config


def test_version_del_contrato():
    assert contrato.VERSION == "0.2.0"


def test_estado_sistema_valida():
    estado = contrato.EstadoSistema(
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        sistema="Rolando Flores",
        enlace_ok=True,
        pendientes_sincronizar=0,
    )
    assert estado.pendientes_sincronizar == 0


def test_contrato_rechaza_campos_extra():
    with pytest.raises(ValidationError):
        contrato.EstadoSistema(
            ts=datetime.now(UTC),
            sistema="x",
            enlace_ok=True,
            pendientes_sincronizar=0,
            otro=1,
        )


def test_config_por_defecto_y_entorno():
    assert Config().confirmacion_estricta is True
    c = Config.desde_entorno({"MQTT_HOST": "otro", "CONFIRMACION_ESTRICTA": "false", "LATIDO_S": "5"})
    assert (c.mqtt_host, c.confirmacion_estricta, c.latido_s) == ("otro", False, 5.0)
