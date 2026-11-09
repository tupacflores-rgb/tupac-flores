"""Configuración del sistema, leída del entorno y validada al arrancar.

Todo lo que cambia entre desarrollo, corrección y pruebas es una variable de entorno (ver `.env.example`).
Nada se descarga en runtime: los modelos viven dentro de las imágenes (Plan Maestro §5.1).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(valor: str | None, defecto: bool) -> bool:
    if valor is None or valor == "":
        return defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # Identidad: aparece en el latido `sistema/estado`.
    sistema: str = "Rolando Flores"

    # Planta (red planta-lan).
    mqtt_host: str = "broker"
    mqtt_puerto: int = 1883
    planta_url: str = "http://sim:8100"

    # Servicios propios.
    database_url: str = "postgresql://sistema:sistema@db:5432/sistema"
    asr_url: str = "http://asr:8000"

    # Latido (F2): cada 30 s, en un hilo propio.
    latido_s: float = 30.0

    # D8 estricta (decisión del usuario): sin confirmación del operador el parte queda en Borrador y no
    # genera eventos. Si Novit permite validar de otra forma (BL-3 / D16), se cambia con esta bandera.
    confirmacion_estricta: bool = True

    @classmethod
    def desde_entorno(cls, env: dict[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        d = cls()
        return cls(
            sistema=e.get("SISTEMA_NOMBRE", d.sistema),
            mqtt_host=e.get("MQTT_HOST", d.mqtt_host),
            mqtt_puerto=int(e.get("MQTT_PUERTO", d.mqtt_puerto)),
            planta_url=e.get("PLANTA_URL", d.planta_url).rstrip("/"),
            database_url=e.get("DATABASE_URL", d.database_url),
            asr_url=e.get("ASR_URL", d.asr_url).rstrip("/"),
            latido_s=float(e.get("LATIDO_S", d.latido_s)),
            confirmacion_estricta=_bool(e.get("CONFIRMACION_ESTRICTA"), d.confirmacion_estricta),
        )
