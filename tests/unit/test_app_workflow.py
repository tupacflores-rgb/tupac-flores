import json
import queue
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from planta_contrato import EventoSistema, TipoEvento

from sistema.app import Agente, WorkflowAunNoListo
from sistema.config import Config
from sistema.integraciones.mqtt import Entrante
from sistema.workflow.topicos import TOPICO_DECISION_PLANIFICADOR


def test_worker_workflow_procesa_en_background_el_evento_encolado(tmp_path, monkeypatch):
    invocado = threading.Event()
    threads = []

    class GrafoFalso:
        def get_state(self, _config):
            return SimpleNamespace(metadata=None)

        def invoke(self, _estado, config):
            threads.append(threading.current_thread())
            assert config["configurable"]["thread_id"] == "OT-EV-N-3-B-101"
            invocado.set()

    grafo = GrafoFalso()

    @contextmanager
    def grafo_con_postgres_falso(*_args, **_kwargs):
        yield grafo

    monkeypatch.setattr("sistema.app.grafo_con_postgres", grafo_con_postgres_falso)
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente.base.lista.set()
    agente._reserva_disponible = lambda _equipo: True

    parar = threading.Event()
    worker = threading.Thread(target=agente._worker_workflow, args=(parar,), daemon=True)
    worker.start()
    try:
        assert agente._workflow_listo.wait(2)
        evento = EventoSistema(
            id="EV-N-3-B-101",
            ts="2026-03-04T07:00:00Z",
            tipo=TipoEvento.escalado,
            equipo="B-101",
            origen="parte",
            resumen="vibración elevada",
        )

        agente._encolar_workflow(evento)

        assert invocado.wait(2)
        assert threads[0] is worker
    finally:
        parar.set()
        worker.join(timeout=2)

    assert not worker.is_alive()


def test_worker_entrada_reintenta_decision_sin_acusarla_hasta_que_exista_el_checkpoint(tmp_path):
    class GrafoFalso:
        def __init__(self):
            self.consultas = 0
            self.reanudaciones = []

        def get_state(self, _config):
            self.consultas += 1
            if self.consultas == 1:
                return SimpleNamespace(metadata=None, next=())
            if self.consultas == 2:
                return SimpleNamespace(metadata={"source": "loop"}, next=("analizar_escalado",))
            return SimpleNamespace(metadata={"source": "loop"}, next=("esperar_decision",))

        def invoke(self, comando, config):
            self.reanudaciones.append((comando, config))

    class MqttFalso:
        def __init__(self):
            self.entrantes = queue.Queue()
            self.acusados = []

        def acusar(self, mensaje):
            self.acusados.append(mensaje)
            parar.set()

    grafo = GrafoFalso()
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente._workflow = grafo
    agente._workflow_listo.set()
    mqtt = MqttFalso()
    agente.mqtt = mqtt
    parar = threading.Event()
    mensaje = Entrante(
        TOPICO_DECISION_PLANIFICADOR,
        json.dumps({"orden_id": "OT-EV-N-1-B-101", "decision": "aprobada"}).encode(),
        mid=1,
        qos=1,
    )
    mqtt.entrantes.put(mensaje)

    worker = threading.Thread(target=agente._worker_entrada, args=(parar,), daemon=True)
    worker.start()
    worker.join(timeout=3)

    assert not worker.is_alive()
    assert grafo.consultas >= 3
    assert len(grafo.reanudaciones) == 1
    assert mqtt.acusados == [mensaje]


def test_esperar_workflow_no_bloquea_entrada_mqtt_mientras_el_grafo_no_esta_listo(tmp_path):
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))

    with pytest.raises(WorkflowAunNoListo):
        agente._esperar_workflow()


def test_error_del_checkpointer_pide_reconexion_y_reintenta_el_mensaje(tmp_path):
    class GrafoFalso:
        def get_state(self, _config):
            raise psycopg.OperationalError("the connection is closed")

    grafo = GrafoFalso()
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente._workflow = grafo
    agente._workflow_listo.set()

    with pytest.raises(WorkflowAunNoListo):
        agente._reanudar_workflow(
            "OT-EV-N-1-B-101",
            {"decision": "aprobada"},
            "esperar_decision",
        )

    assert agente._workflow_reinicio.is_set()


def test_worker_workflow_reabre_postgres_y_no_pierde_evento_en_error_del_checkpointer(tmp_path, monkeypatch):
    class GrafoFalso:
        def get_state(self, _config):
            raise psycopg.OperationalError("the connection is closed")

    grafo = GrafoFalso()

    @contextmanager
    def grafo_con_postgres_falso(*_args, **_kwargs):
        yield grafo

    monkeypatch.setattr("sistema.app.grafo_con_postgres", grafo_con_postgres_falso)
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente.base.lista.set()
    agente._reserva_disponible = lambda _equipo: True
    evento = EventoSistema(
        id="EV-N-4-B-101",
        ts="2026-03-04T07:00:00Z",
        tipo=TipoEvento.escalado,
        equipo="B-101",
        origen="parte",
        resumen="vibración elevada",
    )
    agente._encolar_workflow(evento)

    with pytest.raises(psycopg.OperationalError):
        agente._worker_workflow(threading.Event())

    assert not agente._workflow_listo.is_set()
    assert agente._workflow_jobs.get_nowait() == (evento, ())
