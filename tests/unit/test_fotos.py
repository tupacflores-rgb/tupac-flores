"""Descarga de fotos: volumen con sha256, asociación a la fila, timeout/reintento y URL pendiente si falla."""

# ruff: noqa: E501
from __future__ import annotations

import hashlib
import threading
import time
from datetime import UTC, datetime

import httpx
import pytest
from planta_falsa import DEFINICION

from sistema.deteccion.eventos import construir
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import Fila
from sistema.fotos import Fotos
from sistema.integraciones.planta_api import PlantaApi

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)
URL = "/media/fotos/goteo/01.jpg"
BYTES = b"\xff\xd8\xffcontenido-de-la-foto"


class BaseMemoria:
    def __init__(self):
        self.filas = {}

    def guardar_foto(self, url, nota_id, estado, sha256, ruta, tamano, intentos, error):
        self.filas[url] = dict(
            nota_id=nota_id,
            estado=estado,
            sha256=sha256,
            ruta=ruta,
            bytes=tamano,
            intentos=intentos,
            error=error,
        )

    def fotos_pendientes(self, limite=200):
        return [(u, f["nota_id"]) for u, f in self.filas.items() if f["estado"] != "descargada"]


def fotos(tmp_path, descargar, base=None, espera=0.0, reloj=None):
    kw = {"reloj": reloj} if reloj else {}
    return Fotos(descargar, tmp_path / "fotos", base, espera_reintento_s=espera, **kw)


def test_descarga_y_guarda_con_el_sha256_como_nombre(tmp_path):
    base = BaseMemoria()
    f = fotos(tmp_path, lambda url: BYTES, base)
    f.encolar("N-1", [URL])
    assert f.info(URL).estado == "pendiente"  # encolar no descarga
    foto = f.intentar(URL)
    sha = hashlib.sha256(BYTES).hexdigest()
    assert (foto.estado, foto.sha256, foto.bytes) == ("descargada", sha, len(BYTES))
    archivo = tmp_path / "fotos" / sha[:2] / f"{sha}.jpg"
    assert foto.ruta == str(archivo) and archivo.read_bytes() == BYTES
    assert base.filas[URL]["estado"] == "descargada" and base.filas[URL]["sha256"] == sha


def test_la_misma_foto_dos_veces_es_un_solo_archivo(tmp_path):
    f = fotos(tmp_path, lambda url: BYTES)
    f.encolar("N-1", [URL, "/media/fotos/otra/copia.jpg"])
    f.intentar(URL)
    f.intentar("/media/fotos/otra/copia.jpg")
    archivos = [p for p in (tmp_path / "fotos").rglob("*") if p.is_file()]
    assert len(archivos) == 1
    assert f.info(URL).sha256 == f.info("/media/fotos/otra/copia.jpg").sha256


def test_no_quedan_archivos_temporales(tmp_path):
    f = fotos(tmp_path, lambda url: BYTES)
    f.encolar("N-1", [URL])
    f.intentar(URL)
    assert not list((tmp_path / "fotos").rglob("*.tmp"))


def test_si_falla_la_descarga_se_guarda_la_url_y_queda_pendiente(tmp_path):
    base = BaseMemoria()

    def cae(url):
        raise httpx.ConnectError("sim caído")

    f = fotos(tmp_path, cae, base)
    f.encolar("N-1", [URL])
    foto = f.intentar(URL)
    assert (foto.estado, foto.url, foto.sha256) == ("pendiente", URL, None)
    assert "ConnectError" in foto.error and foto.intentos == 1
    assert base.filas[URL]["estado"] == "pendiente" and base.filas[URL]["error"]
    assert [p.url for p in f.pendientes()] == [URL]
    assert not (tmp_path / "fotos").exists() or not list((tmp_path / "fotos").rglob("*.jpg"))


def test_una_respuesta_vacia_cuenta_como_falla(tmp_path):
    f = fotos(tmp_path, lambda url: b"")
    f.encolar("N-1", [URL])
    assert f.intentar(URL).estado == "pendiente"


def test_la_pendiente_se_reintenta_y_baja_cuando_vuelve_el_servicio(tmp_path):
    estado = {"vivo": False}

    def descargar(url):
        if not estado["vivo"]:
            raise TimeoutError("sin respuesta")
        return BYTES

    f = fotos(tmp_path, descargar)
    f.encolar("N-1", [URL])
    assert f.intentar(URL).estado == "pendiente"
    estado["vivo"] = True
    foto = f.intentar(URL)
    assert foto.estado == "descargada" and foto.intentos == 2 and foto.error is None


def test_el_worker_descarga_en_segundo_plano_y_reintenta_lo_que_fallo(tmp_path):
    llamadas = []

    def descargar(url):
        llamadas.append(url)
        if len(llamadas) < 3:
            raise ConnectionError("todavía no")
        return BYTES

    f = fotos(tmp_path, descargar, espera=0.05)
    parar = threading.Event()
    hilo = threading.Thread(target=f.correr, args=(parar,), daemon=True)
    hilo.start()
    f.encolar("N-1", [URL])
    t_fin = time.time() + 15
    while time.time() < t_fin and not f.info(URL).descargada:
        time.sleep(0.05)
    parar.set()
    hilo.join(timeout=3)
    assert f.info(URL).descargada and len(llamadas) == 3


def test_al_arrancar_retoma_las_pendientes_de_la_base(tmp_path):
    base = BaseMemoria()
    base.filas["/media/fotos/vieja.jpg"] = dict(
        nota_id="N-0", estado="pendiente", sha256=None, ruta=None, bytes=None, intentos=1, error="x"
    )
    f = fotos(tmp_path, lambda url: BYTES, base)
    f.recuperar()
    assert f.info("/media/fotos/vieja.jpg").nota_id == "N-0"
    assert f.intentar("/media/fotos/vieja.jpg").estado == "descargada"


def test_una_base_caida_no_frena_la_descarga(tmp_path):
    class Rota:
        def guardar_foto(self, *a):
            raise TimeoutError("db caída")

    f = fotos(tmp_path, lambda url: BYTES, Rota())
    f.encolar("N-1", [URL])
    assert f.intentar(URL).estado == "descargada"


def test_cada_url_se_encola_una_sola_vez(tmp_path):
    n = []
    f = fotos(tmp_path, lambda url: n.append(url) or BYTES)
    f.encolar("N-1", [URL, URL])
    f.encolar("N-1", [URL])
    assert f._cola.qsize() == 1


def test_con_planta_api_el_timeout_y_el_reintento_vienen_de_descargar():
    pedidos = []

    def handler(req: httpx.Request) -> httpx.Response:
        pedidos.append(req.url.path)
        if len(pedidos) < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=BYTES)

    api = PlantaApi("http://sim")
    api._http = httpx.Client(base_url="http://sim", transport=httpx.MockTransport(handler))
    assert api.descargar(URL, timeout_s=3.0, reintentos=2) == BYTES and len(pedidos) == 3


# -- asociación con la fila y con el Escalado -----------------------------------------------------------


def test_el_escalado_cita_la_foto_con_su_hash_o_como_pendiente(tmp_path):
    f = fotos(tmp_path, lambda url: BYTES)
    f.encolar("N-1", [URL])
    filas = [
        Fila(
            "B-102",
            "goteo_empaquetadura",
            None,
            "gotas/min",
            "pierde bastante por la empaquetadura",
            foto=URL,
        )
    ]
    (pendiente,) = construir("N-1", TS, filas, CAT, foto=f.descripcion)
    assert f"{URL} (pendiente de descarga)" in pendiente.resumen
    f.intentar(URL)
    (guardada,) = construir("N-1", TS, filas, CAT, foto=f.descripcion)
    sha = hashlib.sha256(BYTES).hexdigest()[:12]
    assert f"sha256 {sha}" in guardada.resumen and "guardada" in guardada.resumen


@pytest.mark.parametrize("url", [None, ""])
def test_info_sin_url(tmp_path, url):
    assert fotos(tmp_path, lambda u: BYTES).info(url) is None
