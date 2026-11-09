"""Las tools de solo lectura como herramientas de LangChain.

- `a_structured_tools`: cada `Herramienta` pasa a `StructuredTool` con su modelo Pydantic estricto como
  esquema de argumentos (`extra="forbid"`: un argumento de más es un error, no se ignora). Un fallo de
  la tool se convierte en `ToolException`, que LangChain devuelve al agente como texto sin romper la
  ejecución.
- `herramientas_mcp`: las mismas tools expuestas por el servidor MCP (`servidor_mcp.py`), consumidas con
  `langchain-mcp-adapters`. Son `BaseTool` de LangChain, intercambiables con las locales.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from langchain_core.tools import StructuredTool, ToolException

from .base import Herramienta


def _como_structured_tool(h: Herramienta) -> StructuredTool:
    def ejecutar(**argumentos) -> str:
        res = h(**argumentos)
        if not res.ok:
            raise ToolException(res.error or "la tool falló")
        return json.dumps(res.datos, ensure_ascii=False)

    return StructuredTool.from_function(
        func=ejecutar,
        name=h.nombre,
        description=h.descripcion,
        args_schema=h.entrada,
        handle_tool_error=True,
    )


def a_structured_tools(tools: dict[str, Herramienta]) -> list[StructuredTool]:
    return [_como_structured_tool(h) for h in tools.values()]


def parametros_servidor_mcp(env: dict[str, str] | None = None) -> dict:
    """Conexión stdio al servidor MCP propio (`python -m sistema.herramientas.servidor_mcp`)."""
    src = str(Path(__file__).resolve().parents[2])
    entorno = {**os.environ, **(env or {})}
    entorno["PYTHONPATH"] = os.pathsep.join(filter(None, [src, entorno.get("PYTHONPATH", "")]))
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "sistema.herramientas.servidor_mcp"],
        "env": entorno,
    }


async def herramientas_mcp(env: dict[str, str] | None = None) -> list:
    """Las tools del servidor MCP como `BaseTool` de LangChain (vía langchain-mcp-adapters)."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    cliente = MultiServerMCPClient({"herramientas": parametros_servidor_mcp(env)})
    return await cliente.get_tools()
