"""Interfaz CLI para publicar decisiones humanas (HITL) por MQTT."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

import paho.mqtt.publish as mqtt_publish
from pydantic import ValidationError

from sistema.workflow.decisiones import (
    Decision,
    DecisionInspector,
    DecisionPlanificador,
    DecisionSupervisor,
)
from sistema.workflow.topicos import (
    TOPICO_DECISION_INSPECTOR,
    TOPICO_DECISION_PLANIFICADOR,
    TOPICO_DECISION_SUPERVISOR,
)

_CONTRATOS_POR_ROL: dict[str, tuple[str, type[Decision]]] = {
    "planificador": (TOPICO_DECISION_PLANIFICADOR, DecisionPlanificador),
    "supervisor": (TOPICO_DECISION_SUPERVISOR, DecisionSupervisor),
    "inspector": (TOPICO_DECISION_INSPECTOR, DecisionInspector),
}

_OPCIONES_POR_ROL: dict[str, tuple[str, ...]] = {
    "planificador": ("aprobada", "rechazada"),
    "supervisor": ("cerrar",),
    "inspector": ("certificar", "observar"),
}


def construir_mensaje_decision(rol: str, orden_id: str, decision: str) -> tuple[str, str]:
    """Valida una decisión humana con Pydantic y devuelve ``(topico, json_payload)``."""
    contrato = _CONTRATOS_POR_ROL.get(rol)
    if contrato is None:
        raise ValueError(f"Rol desconocido: {rol!r}. Roles válidos: {', '.join(_CONTRATOS_POR_ROL)}")
    topico, modelo_cls = contrato
    modelo = modelo_cls(orden_id=orden_id, decision=decision)
    return topico, modelo.model_dump_json()


def publicar_decision(
    rol: str,
    orden_id: str,
    decision: str,
    *,
    mqtt_host: str = "localhost",
    mqtt_port: int = 1883,
    dry_run: bool = False,
    publicador: Callable[..., None] | None = None,
) -> tuple[str, str]:
    """Construye y publica una decisión humana por MQTT (o la imprime si ``dry_run=True``)."""
    topico, payload = construir_mensaje_decision(rol, orden_id, decision)
    if dry_run:
        print(f"[dry-run] {topico} {payload}")
        return topico, payload

    enviar = publicador if publicador is not None else mqtt_publish.single
    enviar(topico, payload=payload, hostname=mqtt_host, port=mqtt_port, qos=1)
    return topico, payload


def _construir_parser() -> argparse.ArgumentParser:
    comunes = argparse.ArgumentParser(add_help=False)
    comunes.add_argument(
        "--mqtt-host",
        default=argparse.SUPPRESS,
        help="Host del broker MQTT (default: env MQTT_HOST o 'localhost').",
    )
    comunes.add_argument(
        "--mqtt-port",
        type=int,
        default=argparse.SUPPRESS,
        help="Puerto del broker MQTT (default: env MQTT_PORT o 1883).",
    )
    comunes.add_argument(
        "--dry-run",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Imprime tópico y payload JSON sin conectarse al broker.",
    )

    parser = argparse.ArgumentParser(
        prog="python -m sistema.workflow.cli",
        description="Publica decisiones humanas HITL del workflow por MQTT.",
        parents=[comunes],
    )
    subparsers = parser.add_subparsers(dest="rol", required=True, metavar="ROL")

    for rol, opciones in _OPCIONES_POR_ROL.items():
        sub = subparsers.add_parser(
            rol,
            parents=[comunes],
            help=f"Publica una decisión de {rol}.",
        )
        sub.add_argument("orden_id", help="Identificador de la orden de trabajo.")
        sub.add_argument(
            "--decision",
            required=True,
            choices=opciones,
            help=f"Decisión del {rol}: {', '.join(opciones)}.",
        )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint de la CLI de decisiones HITL."""
    parser = _construir_parser()
    args = parser.parse_args(argv)

    mqtt_host = getattr(args, "mqtt_host", os.environ.get("MQTT_HOST", "localhost"))
    mqtt_port = getattr(args, "mqtt_port", int(os.environ.get("MQTT_PORT", "1883")))
    dry_run = getattr(args, "dry_run", False)

    try:
        publicar_decision(
            args.rol,
            args.orden_id,
            args.decision,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            dry_run=dry_run,
        )
    except (ValidationError, ValueError) as exc:
        print(f"Error de validación: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
