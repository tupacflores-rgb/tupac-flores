"""Entrada de notas (descarte de viejas, deduplicación, journal) y sonda del enlace."""

from __future__ import annotations

import json
import socket
import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from sistema.app import Agente
from sistema.config import Config
from sistema.integraciones.enlace import enlace_arriba
from sistema.persistencia.journal import Journal

AHORA = datetime(2026, 3, 4, 7, 0, tzinfo=UTC)


class BaseFalsa:
    def __init__(self, caida=False):
        self.caida = caida
        self.notas: dict[str, tuple] = {}
        self.lista = threading.Event()

    def registrar_nota(self, nota_id, ts, recorrida_id, audio_url, fotos):
        if self.caida:
            raise psycopg.OperationalError("base caída")
        if nota_id in self.notas:
            return False
        self.notas[nota_id] = (ts, recorrida_id, audio_url, fotos)
        return True


class ApiFalsa:
    def __init__(self, ts=AHORA):
        self.ts = ts

    def estado(self):
        return {"ts": self.ts.isoformat()}


def nota(id_="NOTA-00001", ts=AHORA):
    return {
        "id": id_,
        "ts": ts.isoformat(),
        "operador": "Héctor Llancafil",
        "audio_url": f"/media/audios/{id_}.wav",
        "fotos": [],
        "recorrida_id": "REC-00001",
    }


@pytest.fixture
def agente(tmp_path):
    a = Agente(Config(journal_dir=str(tmp_path / "journal")))
    a.base = BaseFalsa()
    a.api = ApiFalsa()
    a.recibidas = []
    a.al_recibir_nota = a.recibidas.append
    return a


def test_nota_valida_se_guarda_y_sigue_su_camino(agente):
    agente._recibir_nota(nota())
    assert [n.id for n in agente.recibidas] == ["NOTA-00001"]
    assert "NOTA-00001" in agente.base.notas


def test_entrega_repetida_del_broker_no_se_procesa_dos_veces(agente):
    agente._recibir_nota(nota())
    agente._recibir_nota(nota())
    assert len(agente.recibidas) == 1


def test_nota_de_un_escenario_anterior_se_descarta_y_se_registra(agente, caplog):
    vieja = AHORA - timedelta(hours=6, minutes=1)
    with caplog.at_level("WARNING"):
        agente._recibir_nota(nota("NOTA-VIEJA", vieja))
    assert agente.recibidas == []
    assert agente.base.notas == {}
    assert "NOTA-VIEJA descartada" in caplog.text


def test_nota_justo_dentro_de_las_seis_horas_se_acepta(agente):
    agente._recibir_nota(nota("NOTA-OK", AHORA - timedelta(hours=5, minutes=59)))
    assert len(agente.recibidas) == 1


def test_nota_invalida_se_descarta_sin_romper(agente, caplog):
    datos = nota()
    datos["campo_extra"] = 1  # el contrato es extra="forbid"
    agente._recibir_nota(datos)
    assert agente.recibidas == []


def test_con_la_base_caida_la_nota_va_al_journal_y_sigue_su_camino(agente):
    agente.base = BaseFalsa(caida=True)
    agente._recibir_nota(nota())
    assert len(agente.recibidas) == 1
    en_journal = agente.journal.leer("nota")
    assert [r["datos"]["id"] for r in en_journal] == ["NOTA-00001"]


def test_al_volver_la_base_el_journal_se_importa_y_se_vacia(agente):
    agente.base = BaseFalsa(caida=True)
    agente._recibir_nota(nota())
    agente.base = BaseFalsa()
    agente.recuperar_journal_notas()
    assert "NOTA-00001" in agente.base.notas
    assert agente.journal.leer() == []


def test_procesar_entrante_ignora_sensores_y_payload_ilegible(agente):
    from sistema.integraciones.mqtt import Entrante

    agente._procesar_entrante(Entrante("planta/sensores/B-101/presion_descarga", b"x", 1, 0))
    agente._procesar_entrante(Entrante("planta/recorridas", b"no es json", 2, 1))
    assert agente.recibidas == []


def test_procesar_entrante_de_recorridas(agente):
    from sistema.integraciones.mqtt import Entrante

    agente._procesar_entrante(Entrante("planta/recorridas", json.dumps(nota()).encode(), 3, 1))
    assert len(agente.recibidas) == 1


# -- journal ----------------------------------------------------------------------------------------


def test_journal_ignora_una_linea_cortada_por_un_apagon(tmp_path):
    j = Journal(tmp_path)
    j.agregar("nota", {"id": 1})
    with open(j.ruta, "a", encoding="utf-8") as f:
        f.write('{"tipo": "nota", "datos": {"id"')  # escritura cortada
    assert [r["datos"]["id"] for r in j.leer()] == [1]


# -- sonda del enlace -------------------------------------------------------------------------------


def _servidor(respuesta: bytes | None):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def atender():
        conn, _ = srv.accept()
        conn.recv(1024)
        if respuesta:
            conn.sendall(respuesta)
        conn.close()
        srv.close()

    threading.Thread(target=atender, daemon=True).start()
    return f"127.0.0.1:{srv.getsockname()[1]}"


def test_enlace_arriba_si_el_proxy_responde():
    assert enlace_arriba(_servidor(b"HTTP/1.1 200 Connection Established\r\n\r\n"), "x:443")


def test_enlace_arriba_aunque_el_proxy_no_alcance_el_destino():
    assert enlace_arriba(_servidor(b"HTTP/1.1 502 Bad Gateway\r\n\r\n"), "x:443")


def test_enlace_caido_si_el_proxy_corta_sin_responder():
    assert not enlace_arriba(_servidor(None), "x:443")


def test_enlace_caido_si_no_hay_proxy():
    assert not enlace_arriba("127.0.0.1:9", "x:443", timeout_s=0.5)
