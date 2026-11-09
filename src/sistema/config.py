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


def _client_id(e: dict[str, str], defecto: str) -> str:
    """`<proyecto de compose>-agente`: dos stacks del mismo código (pruebas y corrector) no comparten sesión
    MQTT ni se expulsan entre sí. `MQTT_CLIENT_ID` lo pisa si se define."""
    if e.get("MQTT_CLIENT_ID"):
        return e["MQTT_CLIENT_ID"]
    proyecto = e.get("COMPOSE_PROJECT_NAME", "").strip()
    return f"{proyecto}-agente" if proyecto else defecto


@dataclass(frozen=True)
class Config:
    # Identidad: aparece en el latido `sistema/estado`.
    sistema: str = "Rolando Flores"

    # Planta (red planta-lan).
    mqtt_host: str = "broker"
    mqtt_puerto: int = 1883
    mqtt_client_id: str = "tupac-flores-agente"
    mqtt_sesion_s: int = 600
    planta_url: str = "http://sim:8100"

    # Servicios propios.
    # Valor por defecto de desarrollo para la base interna de planta-lan (sin puertos publicados).
    database_url: str = "postgresql://sistema:sistema@db:5432/sistema"  # pragma: allowlist secret
    asr_url: str = "http://asr:8000"
    emb_url: str = "http://emb:8000"  # búsqueda densa (pgvector); vacío = solo léxico
    journal_dir: str = "/var/lib/sistema/journal"

    # Enlace: se sondea con un CONNECT a través del proxy. Caído = el proxy corta sin responder.
    enlace_proxy: str = "enlace:3128"
    enlace_destino: str = "integrate.api.nvidia.com:443"

    # Latido (F2): cada 30 s, en un hilo propio. Si los servicios no están listos, el primero sale igual
    # pasados `latido_arranque_max_s` (el corrector espera 180 s).
    latido_s: float = 30.0
    latido_arranque_max_s: float = 150.0

    # Las notas con más de esta diferencia (en horas simuladas) respecto del reloj de la planta son restos
    # de un escenario anterior: se descartan y se registran.
    nota_vieja_horas: float = 6.0

    # D8 estricta (decisión del usuario): sin confirmación del operador el parte queda en Borrador y no
    # genera eventos. Si Novit permite validar de otra forma (BL-3 / D16), se cambia con esta bandera.
    confirmacion_estricta: bool = True

    # Diálogo: borradores por nota antes de dejar el parte pendiente de validación. El operador virtual de la
    # planta responde a cada mensaje sin tope, así que el corte es nuestro. Los mensajes admiten además la
    # pregunta de equipo, el aviso «guardado» y un margen. El timeout global por nota sigue valiendo.
    max_borradores: int = 5

    # Respaldo con modelo de lenguaje local (Ollama). `llm_url` vacío lo desactiva. Solo respaldo.
    llm_url: str = "http://llm:11434"
    llm_modelo: str = "qwen3.5:2b"
    llm_timeout_s: float = 90.0  # qwen3.5:2b a 0,75 CPU: p50 39 s, p95 78 s (docs/resultados/llm.md)
    llm_max_tokens: int = 2500  # el prompt con ejemplos ronda los 1.000 tokens

    @property
    def max_mensajes(self) -> int:
        return self.max_borradores + 3

    # Control por nota (V6): pasos, tokens y timeout global. La ruta de seguridad está exenta de los cortes.
    presupuesto_pasos: int = 12
    presupuesto_tokens: int = 4000
    timeout_nota_s: float = 120.0

    @classmethod
    def desde_entorno(cls, env: dict[str, str] | None = None) -> Config:
        e = os.environ if env is None else env
        d = cls()
        return cls(
            sistema=e.get("SISTEMA_NOMBRE", d.sistema),
            mqtt_host=e.get("MQTT_HOST", d.mqtt_host),
            mqtt_puerto=int(e.get("MQTT_PUERTO", d.mqtt_puerto)),
            mqtt_client_id=_client_id(e, d.mqtt_client_id),
            mqtt_sesion_s=int(e.get("MQTT_SESION_S", d.mqtt_sesion_s)),
            planta_url=e.get("PLANTA_URL", d.planta_url).rstrip("/"),
            database_url=e.get("DATABASE_URL", d.database_url),
            asr_url=e.get("ASR_URL", d.asr_url).rstrip("/"),
            emb_url=e.get("EMB_URL", d.emb_url).rstrip("/"),
            journal_dir=e.get("JOURNAL_DIR", d.journal_dir),
            enlace_proxy=e.get("ENLACE_PROXY", d.enlace_proxy),
            enlace_destino=e.get("ENLACE_DESTINO", d.enlace_destino),
            latido_s=float(e.get("LATIDO_S", d.latido_s)),
            latido_arranque_max_s=float(e.get("LATIDO_ARRANQUE_MAX_S", d.latido_arranque_max_s)),
            nota_vieja_horas=float(e.get("NOTA_VIEJA_HORAS", d.nota_vieja_horas)),
            confirmacion_estricta=_bool(e.get("CONFIRMACION_ESTRICTA"), d.confirmacion_estricta),
            llm_url=e.get("LLM_URL", d.llm_url).rstrip("/"),
            llm_modelo=e.get("LLM_MODELO", d.llm_modelo),
            llm_timeout_s=float(e.get("LLM_TIMEOUT_S", d.llm_timeout_s)),
            llm_max_tokens=int(e.get("LLM_MAX_TOKENS", d.llm_max_tokens)),
            max_borradores=int(e.get("MAX_BORRADORES", d.max_borradores)),
            presupuesto_pasos=int(e.get("PRESUPUESTO_PASOS", d.presupuesto_pasos)),
            presupuesto_tokens=int(e.get("PRESUPUESTO_TOKENS", d.presupuesto_tokens)),
            timeout_nota_s=float(e.get("TIMEOUT_NOTA_S", d.timeout_nota_s)),
        )
