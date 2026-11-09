"""Flujo de una nota como cadenas LCEL de LangChain, y el consumo como callback.

Las dos pasadas de una nota son `RunnableSequence` (operador `|`) de pasos con nombre:

  seguridad:  transcribir_rapido | seguridad
  completa:   transcribir | guardar | parte

`CallbackConsumo` es un `BaseCallbackHandler`: cada paso de la cadena (y cada tool o llamada a un modelo,
cuando las haya) pasa por la `SesionNota` del control por nota, que cuenta pasos y tokens y corta con
`CorteDePresupuesto`. Al terminar la cadena, con éxito o con error, cierra la sesión y entrega el consumo al
registro. Es la lógica de `control/presupuesto.py`, enganchada al ciclo de vida de LangChain.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.runnables import Runnable, RunnableLambda

from .control import CorteDePresupuesto, SesionNota


class CallbackConsumo(BaseCallbackHandler):
    # Las excepciones del presupuesto tienen que cortar la cadena, no quedar registradas y perderse.
    raise_error = True
    run_inline = True

    def __init__(self, sesion: SesionNota):
        self.sesion = sesion
        self._raiz: UUID | None = None

    # -- cadena -------------------------------------------------------------------------------------

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        name: str | None = None,
        **kwargs: Any,
    ) -> None:
        if parent_run_id is None:  # la cadena entera: no es un paso
            self._raiz = run_id
            return
        self.sesion.paso(name or (serialized or {}).get("name") or "paso")

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id == self._raiz:
            self.sesion.cerrar()

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        if run_id == self._raiz:
            if isinstance(error, CorteDePresupuesto):
                self.sesion.consumo.corte = error.motivo.value
            self.sesion.cerrar()

    # -- tools y modelos (Entrega 2) ----------------------------------------------------------------

    def on_tool_start(
        self, serialized: dict[str, Any] | None, input_str: str, *, name: str | None = None, **kwargs: Any
    ) -> None:
        self.sesion.paso(f"tool:{name or (serialized or {}).get('name', '?')}", args=input_str)

    def on_llm_start(self, serialized: dict[str, Any] | None, prompts: list[str], **kwargs: Any) -> None:
        self.sesion.paso("llm")

    def on_chat_model_start(self, serialized: dict[str, Any] | None, messages: Any, **kwargs: Any) -> None:
        self.sesion.paso("llm")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        entrada = salida = 0
        for generaciones in response.generations:
            for g in generaciones:
                uso = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                entrada += int(uso.get("input_tokens", 0))
                salida += int(uso.get("output_tokens", 0))
        if not (entrada or salida):
            uso = (response.llm_output or {}).get("token_usage", {})
            entrada, salida = int(uso.get("prompt_tokens", 0)), int(uso.get("completion_tokens", 0))
        self.sesion.tokens(entrada, salida)


def paso(nombre: str, funcion) -> Runnable:
    """Un paso de la cadena: una función `dict -> dict` con nombre (el que ve el callback)."""
    return RunnableLambda(funcion, name=nombre)
