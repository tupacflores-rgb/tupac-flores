"""Contratos MQTT para las decisiones humanas del workflow."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orden_id: str = Field(min_length=1)
    decision: str


class DecisionPlanificador(Decision):
    decision: Literal["aprobada", "rechazada"]


class DecisionSupervisor(Decision):
    decision: Literal["cerrar"]


class DecisionInspector(Decision):
    decision: Literal["certificar", "observar"]
