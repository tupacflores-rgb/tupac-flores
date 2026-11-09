"""Tools de solo lectura: éxito, error, timeout, reintento y cliente MCP real (en memoria)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from planta_falsa import DEFINICION

from sistema.conocimiento.corpus import cargar
from sistema.conocimiento.recuperador import Recuperador
from sistema.herramientas import cargar_materiales, construir
from sistema.herramientas.base import Entrada, ErrorDeDatos, Herramienta
from sistema.herramientas.servidor_mcp import crear_servidor
from sistema.integraciones.planta_api import PlantaApi

DOCS = Path(__file__).resolve().parents[2] / "src" / "sistema" / "datos" / "documentos"


def _planta(handler) -> PlantaApi:
    api = PlantaApi("http://sim")
    api._http = httpx.Client(base_url="http://sim", transport=httpx.MockTransport(handler))
    return api


def _handler(req: httpx.Request) -> httpx.Response:
    ruta = req.url.path
    if ruta == "/api/planta":
        return httpx.Response(200, json=DEFINICION)
    if ruta == "/api/historian/B-101/vibracion":
        return httpx.Response(200, json=[{"ts": f"t{i}", "valor": float(i)} for i in range(10)])
    if ruta.startswith("/api/historian/"):
        return httpx.Response(404)
    if ruta == "/api/historial-ordenes":
        return httpx.Response(
            200,
            json=[{"id": "OT-1", "fecha_aviso": "2025-01-01"}, {"id": "OT-2", "fecha_aviso": "2025-02-01"}],
        )
    return httpx.Response(404)


@pytest.fixture(scope="module")
def recuperador() -> Recuperador:
    return Recuperador(cargar(DOCS))


@pytest.fixture
def tools(recuperador):
    return construir(_planta(_handler), recuperador, cargar_materiales())


def test_seis_tools_con_entrada_estricta(tools):
    assert set(tools) == {
        "equipos",
        "historial_sensores",
        "historial_ordenes",
        "inventario",
        "catalogo_tareas",
        "buscar_manuales",
    }
    for t in tools.values():
        assert t.entrada.model_config["extra"] == "forbid"


def test_equipos_ok_y_error(tools):
    r = tools["equipos"](tag="B-102")
    assert r.ok and r.datos[0]["tag"] == "B-102" and r.datos[0]["sensores"]
    assert not tools["equipos"](tag="B-999").ok


def test_argumento_de_mas_o_tipo_incorrecto_se_rechaza(tools):
    r = tools["equipos"](tag="B-101", color="rojo")
    assert not r.ok and r.intentos == 0
    assert not tools["historial_sensores"](
        tag="B-101", variable="vibracion", ultimas="5"
    ).ok  # strict: no coerciona


def test_historial_sensores_estadisticas_y_404(tools):
    r = tools["historial_sensores"](tag="B-101", variable="vibracion", ultimas=4)
    assert r.ok and r.datos["n"] == 4 and r.datos["max"] == 9.0 and r.datos["min"] == 6.0
    assert "no hay historial" in tools["historial_sensores"](tag="B-101", variable="nada").error


def test_historial_ordenes_mas_reciente_primero(tools):
    r = tools["historial_ordenes"](equipo="B-101")
    assert [o["id"] for o in r.datos["ordenes"]] == ["OT-2", "OT-1"]


def test_inventario_stock_y_lista_de_materiales(tools):
    r = tools["inventario"](codigo="TX-VAL-KIT")
    assert r.datos["repuestos"][0]["stock"] == {"yacimiento": 1, "central": 4}
    lm = tools["inventario"](equipo="F-201A", almacen="yacimiento")
    assert [x["codigo"] for x in lm.datos["repuestos"]] == ["FC-CART-25", "FC-JUNTA-TAPA", "INS-TRANS-P"]
    assert not tools["inventario"](codigo="NO-EXISTE").ok


def test_catalogo_tareas(tools):
    r = tools["catalogo_tareas"](equipo="F-201B")
    assert {t["codigo"] for t in r.datos["estandar"]} == {"T-F-CAR", "T-I-CAL"}
    assert r.datos["no_estandar"] == []
    assert tools["catalogo_tareas"](codigo="T-B-ROD").datos["no_estandar"][0]["ejecutor"] == "contratista"
    assert not tools["catalogo_tareas"](codigo="T-XXX-NO").ok


def test_buscar_manuales_cita_y_no_devuelve_revision_vencida(tools):
    r = tools["buscar_manuales"](
        pregunta="¿A qué valor de vibración dispara por protección la bomba triplex?"
    )
    assert r.ok
    citas = [x["cita"] for x in r.datos["resultados"]]
    assert all(c.split(" §")[0] != "TX350-C" for c in citas)
    assert any(c.startswith("TX350-D §") for c in citas)
    con_vencidos = tools["buscar_manuales"](pregunta="aceite del cárter ISO VG 150", incluir_vencidos=True)
    assert any(not x["vigente"] for x in con_vencidos.datos["resultados"])


def test_reintenta_lo_transitorio_y_termina():
    llamadas = []

    def handler(req):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise httpx.ConnectError("sin red")
        return httpx.Response(200, json=DEFINICION)

    t = construir(_planta(handler), Recuperador([]), cargar_materiales())["equipos"]
    t.espera_s = 0
    r = t(tag="B-101")
    assert r.ok and r.intentos == 3


def test_agota_reintentos():
    def handler(req):
        raise httpx.ConnectError("sin red")

    t = construir(_planta(handler), Recuperador([]), cargar_materiales())["equipos"]
    t.espera_s = 0
    r = t(tag="B-101")
    assert not r.ok and r.intentos == t.reintentos + 1 and "agotó" in r.error


def test_timeout_corta_sin_esperar_al_hilo():
    class Lenta(Herramienta):
        nombre, descripcion, entrada = "lenta", "x", Entrada
        timeout_s, reintentos, espera_s = 0.1, 0, 0

        def ejecutar(self, e):
            time.sleep(1.5)

    t0 = time.monotonic()
    r = Lenta()()
    assert not r.ok and "superó" in r.error
    assert time.monotonic() - t0 < 1.0


def test_error_de_datos_no_se_reintenta():
    class Mala(Herramienta):
        nombre, descripcion, entrada = "mala", "x", Entrada
        reintentos = 3

        def ejecutar(self, e):
            raise ErrorDeDatos("no existe")

    r = Mala()()
    assert not r.ok and r.intentos == 1


def test_servidor_mcp_real_en_memoria(tools):
    from mcp.shared.memory import create_connected_server_and_client_session

    async def correr():
        async with create_connected_server_and_client_session(crear_servidor(tools)) as cliente:
            listado = await cliente.list_tools()
            assert {t.name for t in listado.tools} == set(tools)
            ok = await cliente.call_tool("inventario", {"codigo": "TX-EMP-KIT"})
            assert not ok.isError
            assert json.loads(ok.content[0].text)["repuestos"][0]["costo_usd"] == 420
            mal = await cliente.call_tool("inventario", {"codigo": "TX-EMP-KIT", "extra": 1})
            assert mal.isError

    asyncio.run(correr())
