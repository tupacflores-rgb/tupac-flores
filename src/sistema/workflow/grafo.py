"""Grafo del ciclo de vida de órdenes de mantenimiento."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from planta_contrato import NotaCierre
from pydantic import BaseModel, ConfigDict, Field

from ..conocimiento.corpus import cargar as cargar_corpus
from ..conocimiento.recuperador import Recuperador
from ..herramientas.catalogo import (
    CatalogoTareas,
    EntradaCatalogoTareas,
    EntradaInventario,
    Inventario,
    cargar_materiales,
)
from .reglas import EvaluacionAprobacion, OrdenBorrador, RepuestoOrden, evaluar_aprobacion


class WorkflowState(TypedDict, total=False):
    evento: dict[str, object]
    correlation_id: str
    fotos: list[dict[str, object]]
    hallazgos_visuales: list[dict[str, object]]
    evidencia_rag: list[dict[str, object]]
    analisis: dict[str, object]
    orden: dict[str, object]
    revision: dict[str, object]
    resultado_revision: Literal["valida", "invalida"]
    reintentos_revision: int
    catalogo_tareas: dict[str, object]
    inventario: dict[str, object]
    reserva_disponible: bool
    evaluacion_aprobacion: dict[str, object]
    aprobada_por_reglas: bool
    motivo_pendiente: str
    decision_planificador: Literal["aprobada", "rechazada"]
    decision_supervisor: Literal["cerrar"]
    ejecutor: Literal["propio", "contratista"]
    cierre: dict[str, object]
    diferencias_cierre: list[dict[str, object]]
    decision_inspector: Literal["observar", "certificar"]
    habilita: dict[str, object]


class CausaEscalado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descripcion: str
    cita: str
    evidencia: str


class AnalisisEscalado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    causas: list[CausaEscalado] = Field(default_factory=list)
    tareas_sugeridas: list[str] = Field(min_length=1)
    citas: list[str] = Field(default_factory=list)
    repuestos_sugeridos: list[RepuestoOrden] = Field(default_factory=list)


class ResultadoRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valida: bool
    errores: list[str] = Field(default_factory=list)
    citas_validas: list[str] = Field(default_factory=list)
    repuestos_validos: list[str] = Field(default_factory=list)


AnalizadorEscalado = Callable[
    [dict[str, object], list[dict[str, object]]], AnalisisEscalado | dict[str, object]
]
MAX_REINTENTOS_REVISION = 2


def validar_citas(citas: list[str], evidencia_rag: list[dict[str, object]]) -> list[str]:
    """Devuelve las citas que no están entre los fragmentos recuperados."""
    disponibles = {cita for evidencia in evidencia_rag if isinstance((cita := evidencia.get("cita")), str)}
    if not citas:
        return ["El análisis no contiene citas recuperadas."]
    return [
        f"La cita {cita} no está en la evidencia RAG recuperada." for cita in citas if cita not in disponibles
    ]


def validar_repuestos(orden: OrdenBorrador, equipo: str, lista_materiales: dict[str, list[str]]) -> list[str]:
    """Devuelve los repuestos de la orden que no pertenecen a la lista del equipo."""
    permitidos = lista_materiales.get(equipo)
    if permitidos is None:
        return [f"El equipo {equipo} no tiene lista de materiales."]
    if not isinstance(permitidos, list) or any(not isinstance(codigo, str) for codigo in permitidos):
        raise ValueError(f"Lista de materiales inválida para {equipo}")
    return [
        f"El repuesto {repuesto.codigo} no figura en la lista de materiales de {equipo}."
        for repuesto in orden.repuestos
        if repuesto.codigo not in permitidos
    ]


def _evento_dict(evento: object) -> dict[str, object]:
    if isinstance(evento, BaseModel):
        return evento.model_dump(mode="python")
    if isinstance(evento, dict):
        return evento
    raise ValueError("El estado requiere un Evento Escalado")


def _normalizar(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto.casefold())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _tareas_basicas(texto: str, equipo: str) -> list[str]:
    texto = _normalizar(texto)
    if equipo.startswith("B-"):
        if any(palabra in texto for palabra in ("empaquetadura", "goteo", "fuga", "reten")):
            return ["T-B-EMP"]
        if "valv" in texto:
            return ["T-B-VAL"]
        if "correa" in texto:
            return ["T-B-COR"]
        if "aceite" in texto:
            return ["T-B-ACE"]
        if "vibracion" in texto:
            return ["I-VIB"]
    if equipo.startswith("F-") and any(palabra in texto for palabra in ("filtro", "cartucho")):
        return ["T-F-CAR"]
    return []


def _analisis_basico(
    evidencia_rag: list[dict[str, object]], texto_contexto: str, equipo: str
) -> AnalisisEscalado:
    causas = [
        CausaEscalado(
            descripcion=str(evidencia["titulo"]),
            cita=str(evidencia["cita"]),
            evidencia=str(evidencia["texto"]),
        )
        for evidencia in evidencia_rag[:3]
    ]
    return AnalisisEscalado(
        causas=causas,
        tareas_sugeridas=_tareas_basicas(texto_contexto, equipo),
        citas=list(dict.fromkeys(str(evidencia["cita"]) for evidencia in evidencia_rag)),
    )


def _nodo_analizar_escalado(
    recuperador: Recuperador, analizador: AnalizadorEscalado | None
) -> Callable[[WorkflowState], dict[str, object]]:
    def analizar(state: WorkflowState) -> dict[str, object]:
        evento = _evento_dict(state.get("evento"))
        equipo = evento.get("equipo")
        resumen = evento.get("resumen")
        if not isinstance(equipo, str) or not equipo or not isinstance(resumen, str) or not resumen:
            raise ValueError("El Evento Escalado requiere equipo y resumen")

        visuales = state.get("hallazgos_visuales", [])
        detalles_visuales = [
            " ".join(
                str(hallazgo.get(campo, ""))
                for campo in ("objeto", "descripcion", "severidad")
                if hallazgo.get(campo)
            )
            for hallazgo in visuales
            if isinstance(hallazgo, dict)
        ]
        texto_contexto = " ".join([resumen, *detalles_visuales])
        resultados = recuperador.buscar(texto_contexto, k=5, equipo=equipo)
        evidencia = [
            {
                "cita": resultado.cita,
                "titulo": resultado.fragmento.titulo,
                "texto": resultado.fragmento.texto[:1200],
                "puntaje": resultado.puntaje,
            }
            for resultado in resultados
        ]
        propuesta = (
            analizador(evento, evidencia)
            if analizador is not None
            else _analisis_basico(evidencia, texto_contexto, equipo)
        )
        analisis = AnalisisEscalado.model_validate(propuesta)
        return {"analisis": analisis.model_dump(mode="python"), "evidencia_rag": evidencia}

    return analizar


def _nodo_redactar_orden(materiales: dict) -> Callable[[WorkflowState], dict[str, object]]:
    def redactar(state: WorkflowState) -> dict[str, object]:
        evento = _evento_dict(state.get("evento"))
        equipo = evento.get("equipo")
        evento_id = evento.get("id")
        if not isinstance(equipo, str) or not equipo or not isinstance(evento_id, str) or not evento_id:
            raise ValueError("El Evento Escalado requiere id y equipo")
        orden_id = f"OT-{evento_id}"
        correlation_id = state.get("correlation_id")
        if correlation_id is not None and correlation_id != orden_id:
            raise ValueError("correlation_id debe coincidir con el orden_id derivado del evento")
        reserva = state.get("reserva_disponible")
        if not isinstance(reserva, bool):
            raise ValueError("El estado requiere reserva_disponible")

        analisis = AnalisisEscalado.model_validate(state.get("analisis"))
        catalogo = CatalogoTareas(materiales).ejecutar(EntradaCatalogoTareas())
        inventario = Inventario(materiales).ejecutar(EntradaInventario(equipo=equipo))
        estandar = catalogo["estandar"]
        no_estandar = catalogo["no_estandar"]
        tareas: list[dict[str, object]] = []
        for codigo in dict.fromkeys(analisis.tareas_sugeridas):
            candidatas = [
                tarea
                for tarea in estandar
                if tarea["codigo"] == codigo and equipo in tarea.get("equipos", [])
            ]
            if not candidatas:
                candidatas = [tarea for tarea in no_estandar if tarea["codigo"] == codigo]
            if len(candidatas) != 1:
                raise ValueError(f"La tarea {codigo} no existe o no aplica al equipo {equipo}")
            tareas.append(candidatas[0])

        cantidades: dict[str, int] = {}
        for tarea in tareas:
            for repuesto in tarea.get("repuestos", []):
                item = RepuestoOrden.model_validate(repuesto)
                cantidades[item.codigo] = cantidades.get(item.codigo, 0) + item.cantidad
        for item in analisis.repuestos_sugeridos:
            cantidades[item.codigo] = cantidades.get(item.codigo, 0) + item.cantidad

        detiene = False
        ejecutores: set[str] = set()
        for tarea in tareas:
            detencion = tarea.get("detiene")
            ejecutor = tarea.get("ejecutor")
            if not isinstance(detencion, bool) or ejecutor not in {"propio", "contratista"}:
                raise ValueError(f"El catálogo de tareas tiene datos incompletos para {tarea['codigo']}")
            detiene = detiene or detencion
            ejecutores.add(ejecutor)

        orden = OrdenBorrador(
            orden_id=orden_id,
            tareas=[str(tarea["codigo"]) for tarea in tareas],
            detiene_equipo=detiene,
            reduce_inyeccion=detiene and not reserva,
            ejecutor="contratista" if "contratista" in ejecutores else "propio",
            repuestos=[
                RepuestoOrden(codigo=codigo, cantidad=cantidad) for codigo, cantidad in cantidades.items()
            ],
            evento_ids=[evento["id"]] if isinstance(evento.get("id"), str) else [],
        )
        return {
            "orden": orden.model_dump(mode="python"),
            "correlation_id": orden_id,
            "catalogo_tareas": catalogo,
            "inventario": inventario,
            "ejecutor": orden.ejecutor,
        }

    return redactar


def _nodo_revisar_orden(materiales: dict) -> Callable[[WorkflowState], dict[str, object]]:
    def revisar(state: WorkflowState) -> dict[str, object]:
        evento = _evento_dict(state.get("evento"))
        equipo = evento.get("equipo")
        if not isinstance(equipo, str) or not equipo:
            raise ValueError("El Evento Escalado requiere equipo")

        analisis = AnalisisEscalado.model_validate(state.get("analisis"))
        orden = OrdenBorrador.model_validate(state.get("orden"))
        evidencia = state.get("evidencia_rag", [])
        if not isinstance(evidencia, list) or any(not isinstance(item, dict) for item in evidencia):
            raise ValueError("evidencia_rag debe ser una lista de fragmentos")
        errores = validar_citas(analisis.citas, evidencia)
        errores.extend(validar_repuestos(orden, equipo, materiales["lista_materiales"]))
        citas_disponibles = {item.get("cita") for item in evidencia}
        repuestos_permitidos = set(materiales["lista_materiales"].get(equipo, []))
        revision = ResultadoRevision(
            valida=not errores,
            errores=errores,
            citas_validas=[cita for cita in analisis.citas if cita in citas_disponibles],
            repuestos_validos=[
                repuesto.codigo for repuesto in orden.repuestos if repuesto.codigo in repuestos_permitidos
            ],
        )
        reintentos = state.get("reintentos_revision", 0)
        if isinstance(reintentos, bool) or not isinstance(reintentos, int) or reintentos < 0:
            raise ValueError("reintentos_revision debe ser un entero no negativo")
        if not revision.valida:
            reintentos += 1
        return {
            "revision": revision.model_dump(mode="python"),
            "resultado_revision": "valida" if revision.valida else "invalida",
            "reintentos_revision": reintentos,
        }

    return revisar


def _nodo_evaluar_aprobacion(state: WorkflowState) -> dict[str, object]:
    orden = OrdenBorrador.model_validate(state.get("orden"))
    inventario = state.get("inventario")
    catalogo_tareas = state.get("catalogo_tareas")
    reserva = state.get("reserva_disponible")
    if not isinstance(inventario, dict) or not isinstance(catalogo_tareas, dict):
        raise ValueError("El estado requiere inventario y catalogo_tareas")
    if not isinstance(reserva, bool):
        raise ValueError("El estado requiere reserva_disponible")

    evaluacion: EvaluacionAprobacion = evaluar_aprobacion(orden, inventario, catalogo_tareas, reserva)
    return {
        "evaluacion_aprobacion": evaluacion.model_dump(mode="python"),
        "aprobada_por_reglas": evaluacion.aprobada,
        "motivo_pendiente": "; ".join(evaluacion.motivos),
    }


def _nodo_esperar_decision(state: WorkflowState) -> dict[str, object]:
    motivo = state.get("motivo_pendiente")
    if not isinstance(motivo, str) or not motivo:
        raise ValueError("esperar_decision requiere motivo_pendiente")

    respuesta = interrupt({"motivo_pendiente": motivo})
    if not isinstance(respuesta, dict):
        raise ValueError("La decisión del planificador debe ser un objeto")
    decision = respuesta.get("decision")
    if decision not in ("aprobada", "rechazada"):
        raise ValueError("La decisión debe ser 'aprobada' o 'rechazada'")
    return {"decision_planificador": decision}


def _nodo_esperar_supervisor(state: WorkflowState) -> dict[str, object]:
    respuesta = interrupt(
        {
            "orden": state.get("orden"),
            "diferencias_cierre": state.get("diferencias_cierre", []),
        }
    )
    if not isinstance(respuesta, dict) or respuesta.get("decision") != "cerrar":
        raise ValueError("El supervisor debe responder con {'decision': 'cerrar'}")
    return {"decision_supervisor": "cerrar"}


def _nodo_esperar_inspector(state: WorkflowState) -> dict[str, object]:
    respuesta = interrupt(
        {
            "orden": state.get("orden"),
            "diferencias_cierre": state.get("diferencias_cierre", []),
        }
    )
    if not isinstance(respuesta, dict):
        raise ValueError("La decisión del inspector debe ser un objeto")
    decision = respuesta.get("decision")
    if decision not in ("certificar", "observar"):
        raise ValueError("La decisión del inspector debe ser 'certificar' u 'observar'")
    return {"decision_inspector": decision}


def _nodo_esperar_cierre(state: WorkflowState) -> dict[str, object]:
    orden_id = state.get("correlation_id")
    if not isinstance(orden_id, str) or not orden_id:
        raise ValueError("esperar_cierre requiere correlation_id")

    respuesta = interrupt({"orden_id": orden_id})
    cierre = NotaCierre.model_validate(respuesta)
    if cierre.orden_id != orden_id:
        raise ValueError("El NotaCierre recibido no corresponde a esta orden")
    return {"cierre": cierre.model_dump(mode="json")}


def _stub(nombre: str) -> Callable[[WorkflowState], dict[str, object]]:
    def nodo(_: WorkflowState) -> dict[str, object]:
        print(nombre)
        return {}

    return nodo


def _ruta_revision(state: WorkflowState) -> str:
    resultado = state.get("resultado_revision")
    reintentos = state.get("reintentos_revision")
    if resultado == "valida":
        return "evaluar_aprobacion"
    if resultado == "invalida" and type(reintentos) is int:
        if reintentos <= MAX_REINTENTOS_REVISION:
            return "redactar_orden"
        return "rechazar_orden"
    raise ValueError("revisar_orden requiere resultado_revision y reintentos_revision válidos")


def _ruta_aprobacion_reglas(state: WorkflowState) -> str:
    aprobada = state.get("aprobada_por_reglas")
    if aprobada is True:
        return "guardar_aprobada"
    if aprobada is False:
        return "guardar_pendiente"
    raise ValueError("evaluar_aprobacion requiere aprobada_por_reglas")


def _ruta_decision_planificador(state: WorkflowState) -> str:
    decision = state.get("decision_planificador")
    if decision == "aprobada":
        return "publicar_orden"
    if decision == "rechazada":
        return "guardar_rechazo"
    raise ValueError("esperar_decision requiere decision_planificador válida")


def _ruta_ejecutor(state: WorkflowState) -> str:
    ejecutor = state.get("ejecutor")
    if ejecutor == "propio":
        return "esperar_supervisor"
    if ejecutor == "contratista":
        return "esperar_inspector"
    raise ValueError("comparar_cierre requiere ejecutor válido en la orden aprobada")


def _ruta_decision_inspector(state: WorkflowState) -> str:
    decision = state.get("decision_inspector")
    if decision == "observar":
        return "guardar_observada"
    if decision == "certificar":
        return "calcular_habilita"
    raise ValueError("esperar_inspector requiere decision_inspector válida")


def construir_grafo(
    checkpointer: BaseCheckpointSaver | None = None,
    *,
    materiales: dict | None = None,
    recuperador: Recuperador | None = None,
    analizador: AnalizadorEscalado | None = None,
) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
    materiales = cargar_materiales() if materiales is None else materiales
    if recuperador is None:
        directorio_corpus = Path(__file__).resolve().parents[1] / "datos" / "documentos"
        recuperador = Recuperador(cargar_corpus(directorio_corpus))

    builder = StateGraph(WorkflowState)
    nodos_stub = (
        "analizar_fotos",
        "rechazar_orden",
        "guardar_aprobada",
        "guardar_pendiente",
        "guardar_rechazo",
        "publicar_orden",
        "interpretar_cierre",
        "comparar_cierre",
        "cerrar_orden",
        "guardar_observada",
        "calcular_habilita",
        "emitir_habilita",
    )
    for nombre in nodos_stub:
        builder.add_node(nombre, _stub(nombre))

    builder.add_node("analizar_escalado", _nodo_analizar_escalado(recuperador, analizador))
    builder.add_node("redactar_orden", _nodo_redactar_orden(materiales))
    builder.add_node("revisar_orden", _nodo_revisar_orden(materiales))
    builder.add_node("evaluar_aprobacion", _nodo_evaluar_aprobacion)
    builder.add_node("esperar_decision", _nodo_esperar_decision)
    builder.add_node("esperar_cierre", _nodo_esperar_cierre)
    builder.add_node("esperar_nuevo_cierre", _nodo_esperar_cierre)
    builder.add_node("esperar_supervisor", _nodo_esperar_supervisor)
    builder.add_node("esperar_inspector", _nodo_esperar_inspector)

    builder.add_edge(START, "analizar_fotos")
    builder.add_edge("analizar_fotos", "analizar_escalado")
    builder.add_edge("analizar_escalado", "redactar_orden")
    builder.add_edge("redactar_orden", "revisar_orden")
    builder.add_conditional_edges(
        "revisar_orden",
        _ruta_revision,
        {
            "evaluar_aprobacion": "evaluar_aprobacion",
            "redactar_orden": "redactar_orden",
            "rechazar_orden": "rechazar_orden",
        },
    )
    builder.add_edge("rechazar_orden", END)

    builder.add_conditional_edges(
        "evaluar_aprobacion",
        _ruta_aprobacion_reglas,
        {
            "guardar_aprobada": "guardar_aprobada",
            "guardar_pendiente": "guardar_pendiente",
        },
    )
    builder.add_edge("guardar_aprobada", "publicar_orden")
    builder.add_edge("guardar_pendiente", "esperar_decision")
    builder.add_conditional_edges(
        "esperar_decision",
        _ruta_decision_planificador,
        {
            "publicar_orden": "publicar_orden",
            "guardar_rechazo": "guardar_rechazo",
        },
    )
    builder.add_edge("guardar_rechazo", END)

    builder.add_edge("publicar_orden", "esperar_cierre")
    builder.add_edge("esperar_cierre", "interpretar_cierre")
    builder.add_edge("esperar_nuevo_cierre", "interpretar_cierre")
    builder.add_edge("interpretar_cierre", "comparar_cierre")
    builder.add_conditional_edges(
        "comparar_cierre",
        _ruta_ejecutor,
        {
            "esperar_supervisor": "esperar_supervisor",
            "esperar_inspector": "esperar_inspector",
        },
    )
    builder.add_edge("esperar_supervisor", "cerrar_orden")
    builder.add_edge("cerrar_orden", END)

    builder.add_conditional_edges(
        "esperar_inspector",
        _ruta_decision_inspector,
        {
            "guardar_observada": "guardar_observada",
            "calcular_habilita": "calcular_habilita",
        },
    )
    builder.add_edge("guardar_observada", "esperar_nuevo_cierre")
    builder.add_edge("calcular_habilita", "emitir_habilita")
    builder.add_edge("emitir_habilita", END)

    return builder.compile(checkpointer=checkpointer)


@contextmanager
def grafo_con_postgres(
    database_url: str,
) -> Generator[CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState], None, None]:
    """Abre el saver, prepara sus tablas y mantiene vivo el grafo compilado."""
    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()
        yield construir_grafo(checkpointer)
