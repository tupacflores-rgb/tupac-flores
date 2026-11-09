"""Tabla de fallas F01–F10 extraída del corpus (MBP-4 §6.1) y la tool de solo lectura `traducir_codigo`."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_herramientas import _handler, _planta  # noqa: F401

from sistema.conocimiento.corpus import cargar
from sistema.conocimiento.fallas import TablaFallas
from sistema.conocimiento.recuperador import Recuperador
from sistema.herramientas import cargar_materiales, construir
from sistema.herramientas.langchain import a_structured_tools
from sistema.herramientas.servidor_mcp import crear_servidor

DOCS = Path(__file__).resolve().parents[2] / "src" / "sistema" / "datos" / "documentos"


@pytest.fixture(scope="module")
def fragmentos():
    return cargar(DOCS)


@pytest.fixture(scope="module")
def tabla(fragmentos):
    return TablaFallas.desde_fragmentos(fragmentos)


@pytest.fixture
def tool(fragmentos):
    return construir(_planta(_handler), Recuperador(fragmentos), cargar_materiales())["traducir_codigo"]


def test_la_tabla_sale_del_corpus_con_diez_fallas(tabla):
    assert sorted(tabla.fallas) == [f"F{i:02d}" for i in range(1, 11)]
    assert all(f.nombre and f.tarea and f.ejecutor for f in tabla.fallas.values())


def test_sin_el_fragmento_de_la_tabla_no_inventa_nada(fragmentos):
    sin = [f for f in fragmentos if not f.titulo.startswith("Resumen del catálogo")]
    assert len(TablaFallas.desde_fragmentos(sin)) == 0


def test_f10_corresponde_a_dos_codigos_de_fabricante(tabla):
    r = tabla.traducir("F10")
    assert r["codigos_fabricante"] == ["TX-T08", "FC-T04"]
    assert r["tarea"] == "T-I-CAL"


def test_codigo_del_fabricante_vuelve_a_f09(tabla):
    r = tabla.traducir("TX-T07")
    assert (r["codigo_catalogo"], r["origen"]) == ("F09", "fabricante")


def test_las_citas_incluyen_tabla_detalle_y_manual_vigente(tabla):
    citas = tabla.traducir("F10")["citas"]
    assert citas[0].startswith("MBP-4") and any(c.startswith("TX350-D") for c in citas)
    assert any(c.startswith("FC48-3") for c in citas)


def test_la_revision_vencida_no_se_cita(fragmentos, tabla):
    vencidas = {f.cita for f in fragmentos if not f.vigente}
    for f in tabla.fallas.values():
        assert not vencidas & set(f.citas)


def test_codigo_del_manual_sin_correspondencia_no_se_inventa(tabla):
    r = tabla.traducir("TX-T09")
    assert r["codigo_catalogo"] is None and r["falla"] is None and r["citas"] and r["nota"]


def test_codigo_desconocido_es_none(tabla):
    assert tabla.traducir("F99") is None


def test_la_tool_normaliza_como_se_dice_en_campo(tool):
    for escrito in ("tx-t07", "TX T07", "tx t07"):
        r = tool(codigo=escrito)
        assert r.ok and r.datos["codigo_catalogo"] == "F09"
    assert tool(codigo="f 03").datos["codigo_catalogo"] == "F03"


def test_la_tool_devuelve_error_si_el_codigo_no_existe(tool):
    r = tool(codigo="F99")
    assert not r.ok


def test_la_tool_rechaza_entrada_invalida_y_argumentos_de_mas(tool):
    with pytest.raises(ValidationError):
        tool.entrada(codigo="bomba")
    with pytest.raises(ValidationError):
        tool.entrada(codigo="F01", otro=1)


def test_la_tool_esta_en_langchain_y_en_mcp(fragmentos):
    tools = construir(_planta(_handler), Recuperador(fragmentos), cargar_materiales())
    lc = next(t for t in a_structured_tools(tools) if t.name == "traducir_codigo")
    assert json.loads(lc.invoke({"codigo": "F10"}))["codigos_fabricante"] == ["TX-T08", "FC-T04"]

    from mcp.shared.memory import create_connected_server_and_client_session as sesion

    async def correr():
        async with sesion(crear_servidor(tools)) as c:
            nombres = {t.name for t in (await c.list_tools()).tools}
            r = await c.call_tool("traducir_codigo", {"codigo": "TX-T07"})
            return nombres, r

    nombres, r = asyncio.run(correr())
    assert "traducir_codigo" in nombres
    assert "F09" in r.content[0].text
