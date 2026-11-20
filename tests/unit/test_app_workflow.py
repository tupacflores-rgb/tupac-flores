import threading
from contextlib import contextmanager
from types import SimpleNamespace

from planta_contrato import EventoSistema, TipoEvento

from sistema.app import Agente
from sistema.config import Config


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
