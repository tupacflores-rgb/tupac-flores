import json

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from planta_contrato import EventoSistema, NotaCierre, TipoEvento

from sistema.app import Agente
from sistema.config import Config
from sistema.integraciones.mqtt import Entrante
from sistema.workflow.grafo import construir_grafo
from sistema.workflow.topicos import TOPICO_DECISION_PLANIFICADOR


def _evento(resumen: str) -> dict:
    return {
        "id": "EV-N-1-B-101",
        "equipo": "B-101",
        "resumen": resumen,
    }


def _iniciar(grafo, *, resumen: str, reserva_disponible: bool, thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    resultado = grafo.invoke(
        {"evento": _evento(resumen), "reserva_disponible": reserva_disponible},
        config=config,
    )
    return config, resultado


def _cierre(orden_id: str) -> dict:
    return NotaCierre(
        id=f"CIERRE-{orden_id}",
        ts="2026-03-04T07:00:00Z",
        orden_id=orden_id,
        tecnico="Técnico de prueba",
        empresa="propio",
        audio_url=None,
        fotos=[],
    ).model_dump(mode="json")


def test_planificador_reanuda_orden_pendiente_y_supervisor_cierra():
    grafo = construir_grafo(checkpointer=MemorySaver())
    orden_id = "OT-EV-N-1-B-101"
    config, interrumpido = _iniciar(
        grafo,
        resumen="B-101: goteo de empaquetadura en la bomba",
        reserva_disponible=False,
        thread_id=orden_id,
    )

    assert grafo.get_state(config).next == ("esperar_decision",)
    pausa = interrumpido["__interrupt__"][0].value
    assert "reserva" in pausa["motivo_pendiente"]

    reanudado = grafo.invoke(Command(resume={"decision": "aprobada"}), config=config)

    assert reanudado["decision_planificador"] == "aprobada"
    assert grafo.get_state(config).next == ("esperar_cierre",)

    grafo.invoke(Command(resume=_cierre(orden_id)), config=config)
    assert grafo.get_state(config).next == ("esperar_supervisor",)

    final = grafo.invoke(Command(resume={"decision": "cerrar"}), config=config)

    assert final["decision_supervisor"] == "cerrar"
    assert grafo.get_state(config).next == ()


def test_inspector_reanuda_y_certifica_orden_de_contratista():
    grafo = construir_grafo(checkpointer=MemorySaver())
    config, _ = _iniciar(
        grafo,
        resumen="B-101: vibración elevada en la bomba",
        reserva_disponible=True,
        thread_id="OT-EV-N-1-B-101",
    )
    assert grafo.get_state(config).next == ("esperar_decision",)

    grafo.invoke(Command(resume={"decision": "aprobada"}), config=config)

    grafo.invoke(Command(resume=_cierre("OT-EV-N-1-B-101")), config=config)
    assert grafo.get_state(config).next == ("esperar_inspector",)
    final = grafo.invoke(Command(resume={"decision": "certificar"}), config=config)

    assert final["decision_inspector"] == "certificar"
    assert grafo.get_state(config).next == ()


def test_decision_mqtt_reanuda_el_thread_persistido(tmp_path):
    grafo = construir_grafo(checkpointer=MemorySaver())
    orden_id = "OT-EV-N-1-B-101"
    config, _ = _iniciar(
        grafo,
        resumen="B-101: goteo de empaquetadura en la bomba",
        reserva_disponible=False,
        thread_id=orden_id,
    )
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente._workflow = grafo
    agente._workflow_listo.set()

    agente._procesar_entrante(
        Entrante(
            TOPICO_DECISION_PLANIFICADOR,
            json.dumps({"orden_id": orden_id, "decision": "aprobada"}).encode(),
            mid=1,
            qos=1,
        )
    )

    assert grafo.get_state(config).values["decision_planificador"] == "aprobada"
    assert grafo.get_state(config).next == ("esperar_cierre",)


def test_escalado_inicia_workflow_con_orden_id_como_thread_id(tmp_path):
    grafo = construir_grafo(checkpointer=MemorySaver())
    agente = Agente(Config(journal_dir=str(tmp_path / "journal")))
    agente._workflow = grafo
    agente._workflow_listo.set()
    agente._reserva_disponible = lambda _: False
    evento = EventoSistema(
        id="EV-N-2-B-101",
        ts="2026-03-04T07:00:00Z",
        tipo=TipoEvento.escalado,
        equipo="B-101",
        origen="parte",
        resumen="B-101: goteo de empaquetadura en la bomba",
    )

    agente._iniciar_workflow(evento)

    orden_id = "OT-EV-N-2-B-101"
    estado = grafo.get_state({"configurable": {"thread_id": orden_id}})
    assert estado.values["correlation_id"] == orden_id
    assert estado.values["orden"]["orden_id"] == orden_id
    assert estado.next == ("esperar_decision",)
