"""Supervisión interna y latido: el latido no se detiene nunca (F2), aunque otros workers caigan."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from planta_contrato import EstadoSistema

from sistema.config import Config
from sistema.estado import EstadoCompartido, RelojPlanta
from sistema.latido import Latido
from sistema.supervisor import Supervisor


def esperar(condicion, timeout=3.0):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(0.01)
    return False


def test_worker_que_cae_se_reinicia():
    sup = Supervisor(espera_reinicio_s=0.01)
    arranques = []

    def inestable(parar):
        arranques.append(1)
        if len(arranques) < 3:
            raise RuntimeError("boom")
        parar.wait()

    sup.lanzar("inestable", inestable)
    assert esperar(lambda: len(arranques) >= 3)
    assert sup.reinicios["inestable"] == 2
    sup.detener()


def test_worker_que_termina_solo_se_reinicia():
    sup = Supervisor(espera_reinicio_s=0.01)
    corridas = []
    sup.lanzar("corto", lambda parar: corridas.append(1))
    assert esperar(lambda: len(corridas) >= 3)
    sup.detener()


def test_detener_para_los_workers():
    sup = Supervisor(espera_reinicio_s=0.01)
    sup.lanzar("espera", lambda parar: parar.wait())
    assert sup.vivos()["espera"]
    sup.detener()
    assert not sup.vivos()["espera"]


def armar_latido(listo=lambda: True, publicar=None, **cfg):
    publicados: list[EstadoSistema] = []
    config = Config(latido_s=0.02, **cfg)
    estado, reloj = EstadoCompartido(), RelojPlanta()
    latido = Latido(config, publicar or publicados.append, estado, reloj, listo)
    return latido, publicados, estado, reloj


def test_el_latido_publica_un_mensaje_valido_del_contrato():
    latido, publicados, estado, reloj = armar_latido()
    reloj.fijar(datetime(2026, 3, 4, 7, 0, tzinfo=UTC))
    estado.enlace_ok = False
    estado.pendientes = 3
    parar = threading.Event()
    hilo = threading.Thread(target=latido.correr, args=(parar,), daemon=True)
    hilo.start()
    assert esperar(lambda: len(publicados) >= 3)
    parar.set()
    m = publicados[0]
    assert (m.sistema, m.enlace_ok, m.pendientes_sincronizar) == ("Rolando Flores", False, 3)
    assert m.ts == datetime(2026, 3, 4, 7, 0, tzinfo=UTC)


def test_el_primer_latido_espera_a_que_el_sistema_este_listo():
    listo = threading.Event()
    latido, publicados, *_ = armar_latido(listo=listo.is_set, latido_arranque_max_s=60)
    parar = threading.Event()
    threading.Thread(target=latido.correr, args=(parar,), daemon=True).start()
    time.sleep(0.3)
    assert publicados == []
    listo.set()
    assert esperar(lambda: len(publicados) >= 1)
    parar.set()


def test_sin_estar_listo_el_latido_arranca_igual_pasado_el_maximo():
    latido, publicados, *_ = armar_latido(listo=lambda: False, latido_arranque_max_s=0.2)
    parar = threading.Event()
    threading.Thread(target=latido.correr, args=(parar,), daemon=True).start()
    assert esperar(lambda: len(publicados) >= 2)
    parar.set()


def test_el_latido_sobrevive_a_un_fallo_de_publicacion():
    llamadas = []

    def publicar(m):
        llamadas.append(m)
        if len(llamadas) <= 2:
            raise ConnectionError("broker caído")

    latido, *_ = armar_latido(publicar=publicar)
    parar = threading.Event()
    threading.Thread(target=latido.correr, args=(parar,), daemon=True).start()
    assert esperar(lambda: len(llamadas) >= 5)
    assert latido.emitidos >= 3
    parar.set()


def test_el_latido_no_se_detiene_cuando_otros_workers_caen():
    """Regla 3: si un worker cae se reinicia; el latido sigue publicando sin interrupción."""
    sup = Supervisor(espera_reinicio_s=0.01)
    latido, publicados, *_ = armar_latido()
    caidas = []

    def siempre_cae(parar):
        caidas.append(1)
        raise RuntimeError("worker roto")

    sup.lanzar("latido", latido.correr)
    sup.lanzar("roto", siempre_cae)
    assert esperar(lambda: len(publicados) >= 3 and len(caidas) >= 5)
    antes = len(publicados)
    assert esperar(lambda: len(publicados) >= antes + 3)
    assert sup.vivos()["latido"]
    assert sup.reinicios["latido"] == 0
    sup.detener()
