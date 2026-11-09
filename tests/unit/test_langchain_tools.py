"""Tools como StructuredTool de LangChain y tools MCP consumidas con langchain-mcp-adapters."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import ValidationError
from test_herramientas import _handler, _planta, recuperador  # noqa: F401  (fixtures y falsos compartidos)

from sistema.herramientas import cargar_materiales, construir
from sistema.herramientas.langchain import a_structured_tools, herramientas_mcp
from sistema.herramientas.servidor_mcp import crear_servidor

NOMBRES = {
    "equipos",
    "historial_sensores",
    "historial_ordenes",
    "inventario",
    "catalogo_tareas",
    "buscar_manuales",
    "traducir_codigo",
}


@pytest.fixture
def tools(recuperador):  # noqa: F811
    return construir(_planta(_handler), recuperador, cargar_materiales())


def test_las_siete_tools_son_structured_tool_con_esquema_pydantic_estricto(tools):
    lc = a_structured_tools(tools)
    assert {t.name for t in lc} == NOMBRES
    for t in lc:
        assert isinstance(t, StructuredTool)
        assert t.args_schema.model_config["extra"] == "forbid"
        assert t.description


def test_invocar_una_tool_devuelve_el_resultado_como_json(tools):
    inventario = next(t for t in a_structured_tools(tools) if t.name == "inventario")
    salida = json.loads(inventario.invoke({"codigo": "TX-VAL-KIT"}))
    assert salida["repuestos"][0]["stock"] == {"yacimiento": 1, "central": 4}


def test_un_argumento_de_mas_o_de_tipo_incorrecto_es_error_de_validacion(tools):
    por_nombre = {t.name: t for t in a_structured_tools(tools)}
    with pytest.raises(ValidationError):
        por_nombre["inventario"].invoke({"codigo": "TX-VAL-KIT", "color": "rojo"})
    with pytest.raises(ValidationError):
        por_nombre["historial_sensores"].invoke({"tag": "B-101", "variable": "vibracion", "ultimas": "cinco"})


def test_el_error_de_una_tool_vuelve_al_agente_como_texto_y_no_rompe(tools):
    por_nombre = {t.name: t for t in a_structured_tools(tools)}
    assert "no existe el repuesto" in por_nombre["inventario"].invoke({"codigo": "NO-EXISTE"})
    assert "no existe el equipo" in por_nombre["equipos"].invoke({"tag": "B-999"})


def test_las_tools_mcp_se_consumen_con_langchain_mcp_adapters_en_memoria(tools):
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp.shared.memory import create_connected_server_and_client_session

    async def correr():
        async with create_connected_server_and_client_session(crear_servidor(tools)) as sesion:
            lc = await load_mcp_tools(sesion)
            assert {t.name for t in lc} == NOMBRES
            assert all(isinstance(t, BaseTool) for t in lc)
            inventario = next(t for t in lc if t.name == "inventario")
            resultado = await inventario.ainvoke({"codigo": "TX-EMP-KIT"})
            assert "420" in str(resultado)
            return lc

    asyncio.run(correr())


def test_el_servidor_mcp_real_por_stdio_se_consume_con_el_adaptador():
    async def correr():
        lc = await herramientas_mcp()
        assert {t.name for t in lc} == NOMBRES
        catalogo = next(t for t in lc if t.name == "catalogo_tareas")
        resultado = await catalogo.ainvoke({"codigo": "T-B-ROD"})
        assert "contratista" in str(resultado)

    asyncio.run(correr())
