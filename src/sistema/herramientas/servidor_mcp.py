"""Servidor MCP que expone las tools de solo lectura (Anexo 1.3: al menos una por MCP).

    python -m sistema.herramientas.servidor_mcp     # stdio, para un cliente MCP

El esquema de cada tool sale de su modelo Pydantic (`extra="forbid"`): un argumento de más es un error,
no se ignora.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mcp import types
from mcp.server import Server

from ..config import Config
from ..conocimiento.corpus import cargar
from ..conocimiento.recuperador import Recuperador
from ..integraciones.planta_api import PlantaApi
from .base import Herramienta
from .catalogo import construir


def crear_servidor(tools: dict[str, Herramienta]) -> Server:
    async def listar(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t.nombre, description=t.descripcion, inputSchema=t.entrada.model_json_schema()
                )
                for t in tools.values()
            ]
        )

    async def llamar(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool = tools.get(params.name)
        if tool is None:
            return _error(f"tool desconocida: {params.name}")
        # La tool es síncrona (httpx + reintentos): se corre en un hilo para no frenar el loop del servidor.
        res = await asyncio.to_thread(tool, **(params.arguments or {}))
        if not res.ok:
            return _error(res.error or "error")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(res.datos, ensure_ascii=False))]
        )

    return Server("tupac-flores-herramientas", on_list_tools=listar, on_call_tool=llamar)


def _error(mensaje: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=mensaje)], isError=True)


def herramientas_por_defecto(cfg: Config | None = None) -> dict[str, Herramienta]:
    cfg = cfg or Config.desde_entorno()
    base = Path(__file__).resolve().parents[1] / "datos" / "documentos"
    return construir(PlantaApi(cfg.planta_url), Recuperador(cargar(base)))


async def _stdio(server: Server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (lee, escribe):
        await server.run(lee, escribe, server.create_initialization_options())


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    asyncio.run(_stdio(crear_servidor(herramientas_por_defecto())))


if __name__ == "__main__":
    main()
