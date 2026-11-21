"""Grafo del ciclo de vida de órdenes de mantenimiento."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from planta_contrato import (
    EstadoOrden,
    NotaCierre,
    OrdenSistema,
    Topicos,
)
from planta_contrato import (
    Repuesto as RepuestoSistema,
)
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
from ..integraciones.asr import ClienteAsr
from ..integraciones.vision import ClienteVision, HallazgoVisual
from ..voz.numeros import numero_antes_de
from .reglas import EvaluacionAprobacion, OrdenBorrador, RepuestoOrden, evaluar_aprobacion

log = logging.getLogger("sistema.workflow")


class WorkflowState(TypedDict, total=False):
    evento: dict[str, object]
    correlation_id: str
    fotos: list[dict[str, object]]
    hallazgos_visuales: list[dict[str, object]]
    evidencia_rag: list[dict[str, object]]
    historial_ordenes: list[dict[str, object]]
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
    cierre_validado: dict[str, object]
    diferencias_cierre: list[dict[str, object]]
    decision_inspector: Literal["observar", "certificar"]
    orden_sistema: dict[str, object]
    orden_emitida: bool
    orden_duplicada_de: str
    monto_habilita_usd: float
    numero_habilita: str
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


class RepuestoCierre(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    cantidad: int | None = Field(default=None, gt=0)


class CierreValidado(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    orden_id: str
    tecnico: str
    empresa: str
    transcripcion: str
    tareas_realizadas: list[str] = Field(default_factory=list)
    repuestos_usados: list[RepuestoCierre] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)


class DiferenciaCierre(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tipo: Literal[
        "tarea_faltante",
        "tarea_no_aprobada",
        "repuesto_faltante",
        "repuesto_no_aprobado",
        "cantidad_no_verificable",
    ]
    codigo: str
    cantidad_aprobada: int | None = None
    cantidad_reportada: int | None = None


AnalizadorEscalado = Callable[
    [dict[str, object], list[dict[str, object]]], AnalisisEscalado | dict[str, object]
]
DescargarAudio = Callable[[str], bytes]
DescargarImagen = Callable[[str], bytes]
AsignarNumeroHabilita = Callable[[str, int], str]
BuscarOrdenAbierta = Callable[[str, list[str], str | None], str | None]
RegistrarEstadoOrden = Callable[[str, str, list[str], str], None]
ObtenerHistorialOrdenes = Callable[[str], list[dict[str, object]]]


class SalidaWorkflow(Protocol):
    def emitir(self, id_: str, topico: str, modelo: BaseModel) -> bool: ...


MAX_REINTENTOS_REVISION = 2
TAREA_NO_ESTANDAR = "NO-ESTANDAR"
MARGEN_REPUESTOS_CONTRATISTA = Decimal("1.10")
_FILA_PRECIO = re.compile(
    r"SVP-\d+\s*\|[^|]*\((?P<tarea>[A-Z]-[A-Z0-9-]+)\)\s*\|[^|]*\|"
    r"\s*USD\s*(?P<precio>[\d.,]+)",
    re.IGNORECASE,
)


def cargar_precios_contrato(ruta: Path | None = None) -> dict[str, Decimal]:
    """Lee los precios vigentes del fragmento contractual incluido en el paquete."""
    ruta = ruta or Path(__file__).resolve().parents[1] / "datos" / "documentos" / "fragmentos.jsonl"
    coincidencias: list[str] = []
    with ruta.open(encoding="utf-8") as archivo:
        for linea in archivo:
            fragmento = json.loads(linea)
            if fragmento.get("titulo") == "Precios vigentes 2025-2026":
                texto = fragmento.get("texto")
                if not isinstance(texto, str):
                    raise ValueError("El fragmento de precios vigentes no contiene texto")
                coincidencias.append(texto)
    if len(coincidencias) != 1:
        raise ValueError(f"Se esperaba un fragmento de precios vigentes; se encontraron {len(coincidencias)}")

    precios: dict[str, Decimal] = {}
    for linea in coincidencias[0].splitlines():
        match = _FILA_PRECIO.search(linea)
        if match is None:
            continue
        codigo = match.group("tarea").upper()
        importe = match.group("precio").replace(".", "").replace(",", ".")
        try:
            precio = Decimal(importe)
        except InvalidOperation as exc:
            raise ValueError(f"Precio contractual inválido para {codigo}: {importe}") from exc
        if not precio.is_finite() or precio < 0:
            raise ValueError(f"Precio contractual inválido para {codigo}: {importe}")
        if codigo in precios:
            raise ValueError(f"Precio contractual duplicado para {codigo}")
        precios[codigo] = precio
    if not precios:
        raise ValueError("El fragmento de precios vigentes no contiene tareas con precio")
    return precios


def calcular_monto_habilita(
    orden: OrdenSistema,
    cierre: CierreValidado,
    precios_contrato: Mapping[str, Decimal],
    inventario: Mapping[str, object],
) -> Decimal:
    """Calcula el monto aprobado usando precios contractuales y repuestos autorizados."""
    if orden.id != cierre.orden_id:
        raise ValueError("El cierre no corresponde a la orden certificada")
    if orden.ejecutor == "propio":
        raise ValueError("No corresponde emitir una Habilita para una orden de personal propio")

    filas = inventario.get("repuestos")
    if not isinstance(filas, list) or any(not isinstance(fila, dict) for fila in filas):
        raise ValueError("inventario debe contener la lista de repuestos con sus costos")
    costos: dict[str, Decimal] = {}
    for fila in filas:
        codigo, costo = fila.get("codigo"), fila.get("costo_usd")
        if not isinstance(codigo, str) or not codigo or isinstance(costo, bool):
            raise ValueError("Cada repuesto del inventario requiere código y costo válidos")
        try:
            costo_decimal = Decimal(str(costo))
        except InvalidOperation as exc:
            raise ValueError(f"Costo inválido para el repuesto {codigo}") from exc
        if not costo_decimal.is_finite() or costo_decimal < 0:
            raise ValueError(f"Costo inválido para el repuesto {codigo}")
        if codigo in costos:
            raise ValueError(f"Costo duplicado para el repuesto {codigo}")
        costos[codigo] = costo_decimal

    total = Decimal("0")
    for tarea in orden.tareas:
        precio = precios_contrato.get(tarea)
        if precio is None:
            raise ValueError(f"No hay precio contractual vigente para la tarea aprobada {tarea}")
        precio_decimal = Decimal(str(precio))
        if not precio_decimal.is_finite() or precio_decimal < 0:
            raise ValueError(f"Precio contractual inválido para la tarea {tarea}")
        total += precio_decimal
    for repuesto in orden.repuestos:
        costo = costos.get(repuesto.codigo)
        if costo is None:
            raise ValueError(f"No hay costo de lista para el repuesto autorizado {repuesto.codigo}")
        cantidad = Decimal(str(repuesto.cantidad))
        if not cantidad.is_finite() or cantidad <= 0:
            raise ValueError(f"Cantidad inválida para el repuesto autorizado {repuesto.codigo}")
        total += costo * cantidad * MARGEN_REPUESTOS_CONTRATISTA
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _clasificar_por_texto(fragmento: str, equipo: str) -> list[str] | None:
    if equipo.startswith("B-"):
        if any(palabra in fragmento for palabra in ("empaquetadura", "goteo", "fuga", "reten")):
            return ["T-B-EMP"]
        if "f02" in fragmento or any(
            palabra in fragmento for palabra in ("valv", "golpete", "pulsac", "martilleo")
        ):
            return ["T-B-VAL"]
        if "f03" in fragmento or any(
            palabra in fragmento
            for palabra in (
                "ruido raro de rodamiento",
                "ruido de rodamiento",
                "rodamiento caliente",
                "temperatura alta del rodamiento",
                "ciguenal",
                "cojinete",
            )
        ):
            return ["T-B-ROD"]
        if "f05" in fragmento or any(palabra in fragmento for palabra in ("correa", "chirrido")):
            return ["T-B-COR"]
        if "f06" in fragmento or any(
            palabra in fragmento for palabra in ("acople", "elastomero", "desaline")
        ):
            return ["T-B-ALI"]
        if "f04" in fragmento or any(
            palabra in fragmento
            for palabra in (
                "nivel de aceite",
                "aceite bajo",
                "visor de aceite",
                "sin aceite",
                "temperatura_aceite",
            )
        ):
            return ["T-B-ACE"]
        return None
    if equipo.startswith("F-") and (
        any(
            palabra in fragmento
            for palabra in ("filtro", "cartucho", "colmatac", "f07", "f08", "diferencial alto")
        )
        or (
            "presion_diferencial" in fragmento
            and any(palabra in fragmento for palabra in ("fuera de limites", "alto", "baja"))
        )
    ):
        return ["T-F-CAR"]
    return None


def _tareas_basicas(texto: str, equipo: str) -> list[str]:
    texto_norm = _normalizar(texto)
    porcion_anomalia = texto_norm.split("parte del equipo:", 1)[0].strip()
    if porcion_anomalia and porcion_anomalia != texto_norm:
        coincidencia = _clasificar_por_texto(porcion_anomalia, equipo)
        if coincidencia is not None:
            return coincidencia
    coincidencia = _clasificar_por_texto(texto_norm, equipo)
    if coincidencia is not None:
        return coincidencia
    if equipo.startswith("B-"):
        return ["I-VIB"]
    return [TAREA_NO_ESTANDAR]


def _obtener_historial_seguro(
    obtener_historial_ordenes: ObtenerHistorialOrdenes | None,
    equipo: str,
) -> list[dict[str, object]]:
    if obtener_historial_ordenes is None:
        return []
    try:
        bruto = obtener_historial_ordenes(equipo)
    except Exception as exc:
        log.warning("no se pudo consultar historial de órdenes para %s: %s", equipo, exc)
        return []
    if not isinstance(bruto, list) or any(not isinstance(item, dict) for item in bruto):
        log.warning("historial de órdenes inválido para %s: se esperaba lista de objetos", equipo)
        return []
    return bruto


def _texto_orden_historial(orden: Mapping[str, object]) -> str:
    return _normalizar(
        " ".join(str(orden.get(campo) or "") for campo in ("aviso", "trabajo_realizado", "codigo_falla"))
    )


def _tareas_orden_historial(orden: Mapping[str, object]) -> set[str]:
    tareas = orden.get("tareas")
    if isinstance(tareas, list):
        return {str(t).upper() for t in tareas if isinstance(t, str)}
    return set()


def _evidencia_orden_historial(orden: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    orden_id = str(orden.get("id") or orden.get("orden_id") or "sin-id")
    cita = f"historial {orden_id}"
    texto = " | ".join(
        parte
        for parte in (
            str(orden.get("equipo") or ""),
            str(orden.get("aviso") or ""),
            str(orden.get("trabajo_realizado") or ""),
        )
        if parte
    )
    return cita, {
        "cita": cita,
        "titulo": f"Historial {orden_id}",
        "texto": texto[:1200],
        "puntaje": 1.0,
    }


def _enriquecer_con_historial(
    analisis: AnalisisEscalado,
    evidencia: list[dict[str, object]],
    texto_contexto: str,
    equipo: str,
    historial: list[dict[str, object]],
) -> tuple[AnalisisEscalado, list[dict[str, object]]]:
    if not historial:
        return analisis, evidencia

    sintoma_norm = _normalizar(texto_contexto).split("tendencia (", 1)[0]
    causas = list(analisis.causas)
    tareas_sugeridas = list(analisis.tareas_sugeridas)
    citas = list(analisis.citas)
    repuestos_sugeridos = list(analisis.repuestos_sugeridos)
    evidencia_actualizada = list(evidencia)
    citas_presentes = {item.get("cita") for item in evidencia_actualizada}

    def agregar_causa_historial(descripcion: str, orden_ref: Mapping[str, object]) -> None:
        cita, item_evidencia = _evidencia_orden_historial(orden_ref)
        if cita not in citas_presentes:
            evidencia_actualizada.append(item_evidencia)
            citas_presentes.add(cita)
        if cita not in citas:
            citas.append(cita)
        causas.insert(
            0,
            CausaEscalado(
                descripcion=descripcion,
                cita=cita,
                evidencia=str(item_evidencia["texto"]),
            ),
        )

    ordenes_valv = [
        o for o in historial if "T-B-VAL" in _tareas_orden_historial(o) or "valv" in _texto_orden_historial(o)
    ]
    if equipo == "B-101" and "vibrac" in sintoma_norm and ordenes_valv:
        agregar_causa_historial(
            "Posible desalineación o daño en acople (vibración repetida tras cambio de válvulas)",
            ordenes_valv[0],
        )

    ordenes_emp = [
        o
        for o in historial
        if "T-B-EMP" in _tareas_orden_historial(o) or "empaquetadura" in _texto_orden_historial(o)
    ]
    if (
        equipo == "B-103"
        and any(p in sintoma_norm for p in ("empaquetadura", "goteo", "fuga", "reten"))
        and ordenes_emp
    ):
        agregar_causa_historial(
            "Posible émbolo rayado por cambio repetido de empaquetadura",
            ordenes_emp[0],
        )

    if (
        equipo.startswith("B-")
        and (tareas_sugeridas == ["T-B-VAL"] or "valv" in sintoma_norm)
        and ordenes_valv
    ):
        agregar_causa_historial(
            "Posible cartucho roto en F-201B (desgaste repetido de válvulas)",
            ordenes_valv[0],
        )

    ordenes_cavit = [
        o
        for o in historial
        if any(p in _texto_orden_historial(o) for p in ("f09", "piedras adentro", "cavit"))
    ]
    if equipo == "B-102" and any(p in sintoma_norm for p in ("f09", "piedras adentro", "cavit")):
        agregar_causa_historial(
            "Posible filtro primario F-201A colmatado (cavitación en B-102)",
            ordenes_cavit[0] if ordenes_cavit else historial[0],
        )

    ordenes_ace = [
        o
        for o in historial
        if "T-B-ACE" in _tareas_orden_historial(o) or "aceite" in _texto_orden_historial(o)
    ]
    if (
        equipo == "B-102"
        and ("aceite" in sintoma_norm or "f04" in sintoma_norm or tareas_sugeridas == ["T-B-ACE"])
        and ordenes_ace
    ):
        agregar_causa_historial(
            "Posible fuga por retén del piñón (reposiciones repetidas de aceite en B-102)",
            ordenes_ace[0],
        )

    return (
        AnalisisEscalado(
            causas=causas,
            tareas_sugeridas=tareas_sugeridas,
            citas=citas,
            repuestos_sugeridos=repuestos_sugeridos,
        ),
        evidencia_actualizada,
    )


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
    recuperador: Recuperador,
    analizador: AnalizadorEscalado | None,
    obtener_historial_ordenes: ObtenerHistorialOrdenes | None = None,
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
        historial = _obtener_historial_seguro(obtener_historial_ordenes, equipo)
        propuesta = (
            analizador(evento, evidencia)
            if analizador is not None
            else _analisis_basico(evidencia, texto_contexto, equipo)
        )
        analisis = AnalisisEscalado.model_validate(propuesta)
        if analizador is None and historial:
            analisis, evidencia = _enriquecer_con_historial(
                analisis, evidencia, texto_contexto, equipo, historial
            )
        return {
            "analisis": analisis.model_dump(mode="python"),
            "evidencia_rag": evidencia,
            "historial_ordenes": historial,
        }

    return analizar


def _nodo_analizar_fotos(
    cliente_vision: ClienteVision | None,
    descargar_imagen: DescargarImagen | None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def analizar(state: WorkflowState) -> dict[str, object]:
        fotos = state.get("fotos", [])
        if not isinstance(fotos, list) or any(not isinstance(foto, dict) for foto in fotos):
            raise ValueError("fotos debe ser una lista de objetos con URL")

        hallazgos: list[dict[str, object]] = []
        for foto in fotos:
            url = foto.get("url")
            if not isinstance(url, str) or not url:
                raise ValueError("Cada foto del evento requiere una URL")
            if cliente_vision is None or descargar_imagen is None:
                error = (
                    "cliente_vision_no_configurado"
                    if cliente_vision is None
                    else "descargador_no_configurado"
                )
                log.warning("análisis visual pendiente para %s: %s", url, error)
                hallazgo = HallazgoVisual(estado="pendiente", severidad="desconocida", error=error)
            else:
                try:
                    imagen = descargar_imagen(url)
                    hallazgo = cliente_vision.analizar_imagen(imagen)
                except Exception as exc:
                    log.exception("falló el análisis visual de %s", url)
                    hallazgo = HallazgoVisual(
                        estado="pendiente",
                        severidad="desconocida",
                        error=type(exc).__name__,
                    )
            hallazgos.append(hallazgo.model_dump(mode="json"))
        return {"hallazgos_visuales": hallazgos}

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
            if not candidatas and codigo == TAREA_NO_ESTANDAR:
                candidatas = [
                    {
                        "codigo": TAREA_NO_ESTANDAR,
                        "detiene": False,
                        "ejecutor": "propio",
                        "repuestos": [],
                    }
                ]
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


_MARCADORES_DANO_ESTRUCTURAL = (
    "rotura_descarga",
    "dano_estructural",
    "dano estructural",
    "rotura con salida",
)


def _hay_dano_estructural_grave(state: WorkflowState) -> bool:
    """Detecta daño estructural grave desde hallazgos visuales o metadatos deterministas."""
    hallazgos = state.get("hallazgos_visuales", [])
    if isinstance(hallazgos, list):
        for hallazgo in hallazgos:
            if isinstance(hallazgo, BaseModel):
                hallazgo = hallazgo.model_dump(mode="python")
            if not isinstance(hallazgo, dict):
                continue
            severidad = _normalizar(str(hallazgo.get("severidad") or "")).strip()
            if severidad in {"grave", "critica", "critico", "estructural"}:
                return True
            detalle = _normalizar(f"{hallazgo.get('objeto') or ''} {hallazgo.get('descripcion') or ''}")
            if "dano estructural" in detalle or "rotura" in detalle:
                return True

    textos: list[str] = []
    fotos = state.get("fotos", [])
    if isinstance(fotos, list):
        for foto in fotos:
            url = foto.get("url") if isinstance(foto, dict) else (foto if isinstance(foto, str) else None)
            if isinstance(url, str) and url:
                textos.append(_normalizar(url))

    evento = state.get("evento")
    if isinstance(evento, BaseModel):
        evento = evento.model_dump(mode="python")
    if isinstance(evento, dict):
        resumen = evento.get("resumen")
        if isinstance(resumen, str) and resumen:
            textos.append(_normalizar(resumen))

    return any(marcador in texto for texto in textos for marcador in _MARCADORES_DANO_ESTRUCTURAL)


def _nodo_evaluar_aprobacion(
    buscar_orden_abierta: BuscarOrdenAbierta | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def evaluar(state: WorkflowState) -> dict[str, object]:
        orden = OrdenBorrador.model_validate(state.get("orden"))
        inventario = state.get("inventario")
        catalogo_tareas = state.get("catalogo_tareas")
        reserva = state.get("reserva_disponible")
        if not isinstance(inventario, dict) or not isinstance(catalogo_tareas, dict):
            raise ValueError("El estado requiere inventario y catalogo_tareas")
        if not isinstance(reserva, bool):
            raise ValueError("El estado requiere reserva_disponible")

        raw_evento = state.get("evento")
        evento = _evento_dict(raw_evento) if raw_evento is not None else {}
        equipo_raw = evento.get("equipo")
        equipo = equipo_raw if isinstance(equipo_raw, str) and equipo_raw else None

        evaluacion: EvaluacionAprobacion = evaluar_aprobacion(
            orden,
            inventario,
            catalogo_tareas,
            reserva,
            dano_estructural_grave=_hay_dano_estructural_grave(state),
            equipo=equipo,
        )
        salida_estado: dict[str, object] = {
            "evaluacion_aprobacion": evaluacion.model_dump(mode="python"),
            "aprobada_por_reglas": evaluacion.aprobada,
            "motivo_pendiente": "; ".join(evaluacion.motivos),
        }
        if buscar_orden_abierta is not None and equipo:
            try:
                existente = buscar_orden_abierta(equipo, orden.tareas, orden.orden_id)
            except Exception as exc:
                log.warning("no se pudo verificar órdenes abiertas para %s: %s", orden.orden_id, exc)
                existente = None
            if existente:
                log.info(
                    "se omite orden duplicada %s; ya existe orden abierta %s para %s %s",
                    orden.orden_id,
                    existente,
                    equipo,
                    orden.tareas,
                )
                salida_estado["orden_duplicada_de"] = existente
        return salida_estado

    return evaluar


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


def _cantidad_cierre_antes_de(texto: str, inicio: int) -> int | None:
    prefijo = texto[max(0, inicio - 100) : inicio]
    tramo = re.split(r"[,;]", prefijo)[-1]
    numero = numero_antes_de(tramo.split(), decimal=False)
    if numero is None or not numero.valor.is_integer() or numero.valor <= 0:
        return None
    return int(numero.valor)


def _extraer_cierre(
    nota: NotaCierre,
    transcripcion: str,
    catalogo_tareas: dict[str, object],
    inventario: dict[str, object],
    advertencias: list[str],
) -> CierreValidado:
    texto = _normalizar(transcripcion)
    tareas_reportadas: set[str] = set()
    for clave in ("estandar", "no_estandar"):
        tareas = catalogo_tareas.get(clave)
        if not isinstance(tareas, list) or any(not isinstance(tarea, dict) for tarea in tareas):
            raise ValueError(f"catalogo_tareas['{clave}'] debe ser una lista de tareas")
        for tarea in tareas:
            codigo, nombre = tarea.get("codigo"), tarea.get("nombre")
            if not isinstance(codigo, str) or not isinstance(nombre, str):
                raise ValueError(f"Cada tarea de catalogo_tareas['{clave}'] requiere codigo y nombre")
            if _normalizar(nombre) in texto or _normalizar(codigo) in texto:
                tareas_reportadas.add(codigo)

    filas_repuestos = inventario.get("repuestos")
    if not isinstance(filas_repuestos, list) or any(not isinstance(fila, dict) for fila in filas_repuestos):
        raise ValueError("inventario debe contener la lista de repuestos")

    repuestos_reportados: dict[str, RepuestoCierre] = {}
    codigos_tareas = tareas_reportadas.copy()
    for fila in filas_repuestos:
        codigo, nombre = fila.get("codigo"), fila.get("nombre")
        if not isinstance(codigo, str) or not isinstance(nombre, str):
            raise ValueError("Cada repuesto del inventario requiere codigo y nombre")
        nombre_corto = nombre.split(" (", maxsplit=1)[0]
        inicio = texto.find(_normalizar(nombre_corto))
        if inicio < 0:
            inicio = texto.find(_normalizar(codigo))
        if inicio >= 0:
            repuestos_reportados[codigo] = RepuestoCierre(
                codigo=codigo,
                cantidad=_cantidad_cierre_antes_de(texto, inicio),
            )

    for match in re.finditer(r"\b([A-Z0-9]{2,4}(?:-[A-Z0-9]+){2,})\b", transcripcion, re.IGNORECASE):
        codigo = match.group(1).upper()
        prefijo = codigo.split("-", maxsplit=1)[0]
        if prefijo in {"OT", "EV", "T", "I"} or codigo in codigos_tareas:
            continue
        if codigo not in repuestos_reportados:
            repuestos_reportados[codigo] = RepuestoCierre(
                codigo=codigo,
                cantidad=_cantidad_cierre_antes_de(_normalizar(transcripcion), match.start()),
            )

    return CierreValidado(
        id=nota.id,
        orden_id=nota.orden_id,
        tecnico=nota.tecnico,
        empresa=nota.empresa,
        transcripcion=transcripcion,
        tareas_realizadas=sorted(tareas_reportadas),
        repuestos_usados=list(repuestos_reportados.values()),
        advertencias=advertencias,
    )


def _nodo_interpretar_cierre(
    cliente_asr: ClienteAsr | None,
    descargar_audio: DescargarAudio | None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def interpretar(state: WorkflowState) -> dict[str, object]:
        nota = NotaCierre.model_validate(state.get("cierre"))
        transcripcion = ""
        advertencias: list[str] = []
        if nota.audio_url:
            if cliente_asr is None or descargar_audio is None:
                raise RuntimeError(
                    "interpretar_cierre requiere cliente_asr y descargar_audio "
                    "para procesar NotaCierre.audio_url"
                )
            audio = descargar_audio(nota.audio_url)
            if not audio:
                raise ValueError(f"El audio del cierre {nota.id} está vacío")
            transcripcion = cliente_asr.transcribir(audio).texto
            if not transcripcion.strip():
                advertencias.append(f"El ASR no produjo texto para el cierre {nota.id}.")
        else:
            advertencias.append(
                "NotaCierre no contiene audio_url; no se pudieron extraer tareas ni repuestos."
            )

        catalogo_tareas = state.get("catalogo_tareas")
        inventario = state.get("inventario")
        if not isinstance(catalogo_tareas, dict) or not isinstance(inventario, dict):
            raise ValueError("El estado requiere catalogo_tareas e inventario para interpretar el cierre")
        cierre = _extraer_cierre(nota, transcripcion, catalogo_tareas, inventario, advertencias)
        return {"cierre_validado": cierre.model_dump(mode="json")}

    return interpretar


def _diferencias_cierre(orden: OrdenSistema, cierre: CierreValidado) -> list[DiferenciaCierre]:
    tareas_aprobadas = Counter(orden.tareas)
    tareas_reportadas = Counter(cierre.tareas_realizadas)
    diferencias: list[DiferenciaCierre] = []
    for codigo in sorted(tareas_aprobadas.keys() | tareas_reportadas.keys()):
        aprobada = tareas_aprobadas[codigo]
        reportada = tareas_reportadas[codigo]
        if aprobada > reportada:
            diferencias.append(
                DiferenciaCierre(
                    tipo="tarea_faltante",
                    codigo=codigo,
                    cantidad_aprobada=aprobada,
                    cantidad_reportada=reportada,
                )
            )
        elif reportada > aprobada:
            diferencias.append(
                DiferenciaCierre(
                    tipo="tarea_no_aprobada",
                    codigo=codigo,
                    cantidad_aprobada=aprobada,
                    cantidad_reportada=reportada,
                )
            )

    cantidades_aprobadas: Counter[str] = Counter()
    for repuesto in orden.repuestos:
        if not repuesto.cantidad.is_integer() or repuesto.cantidad <= 0:
            raise ValueError(f"Cantidad aprobada inválida para el repuesto {repuesto.codigo}")
        cantidades_aprobadas[repuesto.codigo] += int(repuesto.cantidad)
    cantidades_reportadas: Counter[str] = Counter()
    cantidades_desconocidas: set[str] = set()
    for repuesto in cierre.repuestos_usados:
        if repuesto.cantidad is None:
            cantidades_desconocidas.add(repuesto.codigo)
        else:
            cantidades_reportadas[repuesto.codigo] += repuesto.cantidad

    codigos_repuestos = cantidades_aprobadas.keys() | cantidades_reportadas.keys() | cantidades_desconocidas
    for codigo in sorted(codigos_repuestos):
        aprobada = cantidades_aprobadas[codigo]
        if codigo in cantidades_desconocidas:
            diferencias.append(
                DiferenciaCierre(
                    tipo="cantidad_no_verificable",
                    codigo=codigo,
                    cantidad_aprobada=aprobada,
                )
            )
            continue
        reportada = cantidades_reportadas[codigo]
        if aprobada > reportada:
            diferencias.append(
                DiferenciaCierre(
                    tipo="repuesto_faltante",
                    codigo=codigo,
                    cantidad_aprobada=aprobada,
                    cantidad_reportada=reportada,
                )
            )
        elif reportada > aprobada:
            diferencias.append(
                DiferenciaCierre(
                    tipo="repuesto_no_aprobado",
                    codigo=codigo,
                    cantidad_aprobada=aprobada,
                    cantidad_reportada=reportada,
                )
            )
    return diferencias


def _nodo_comparar_cierre(state: WorkflowState) -> dict[str, object]:
    orden = OrdenSistema.model_validate(state.get("orden_sistema"))
    cierre = CierreValidado.model_validate(state.get("cierre_validado"))
    if orden.id != cierre.orden_id:
        raise ValueError("El cierre no corresponde a la OrdenSistema aprobada")
    diferencias = _diferencias_cierre(orden, cierre)
    return {"diferencias_cierre": [diferencia.model_dump(mode="python") for diferencia in diferencias]}


def _orden_sistema_desde_borrador(
    state: WorkflowState,
    estado: EstadoOrden,
    motivo: str | None = None,
) -> OrdenSistema:
    orden_borrador = OrdenBorrador.model_validate(state.get("orden"))
    evento = _evento_dict(state.get("evento"))
    equipo = evento.get("equipo")
    if not isinstance(equipo, str) or not equipo:
        raise ValueError("El Evento Escalado requiere equipo para publicar la orden")
    return OrdenSistema(
        id=orden_borrador.orden_id,
        ts=datetime.now(UTC),
        estado=estado,
        equipo=equipo,
        evento_ids=orden_borrador.evento_ids,
        tareas=orden_borrador.tareas,
        detiene_equipo=orden_borrador.detiene_equipo,
        reduce_inyeccion=orden_borrador.reduce_inyeccion,
        ejecutor="propio" if orden_borrador.ejecutor == "propio" else "Servipat",
        repuestos=[
            RepuestoSistema(codigo=repuesto.codigo, cantidad=float(repuesto.cantidad))
            for repuesto in orden_borrador.repuestos
        ],
        motivo=motivo,
    )


def _orden_con_estado(
    orden: OrdenSistema,
    estado: EstadoOrden,
    motivo: str | None = None,
) -> OrdenSistema:
    datos = orden.model_dump(mode="python")
    datos.update({"estado": estado, "motivo": motivo, "ts": datetime.now(UTC)})
    return OrdenSistema.model_validate(datos)


def _emitir_estado_orden(
    salida: SalidaWorkflow | None,
    orden: OrdenSistema,
    clave: str,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> bool:
    if registrar_estado_orden is not None:
        try:
            registrar_estado_orden(orden.id, orden.equipo, orden.tareas, orden.estado.value)
        except Exception as exc:
            log.warning("no se pudo registrar el estado de %s (%s): %s", orden.id, orden.estado.value, exc)
    if salida is None:
        log.warning("No se emite la orden %s (%s): no se inyectó Salida", orden.id, clave)
        return False
    emitida = salida.emitir(f"{orden.id}:{clave}", Topicos.ORDENES, orden)
    if not emitida:
        log.warning("OrdenSistema %s (%s) quedó pendiente en la cola de salida", orden.id, clave)
    return emitida


def _nodo_guardar_pendiente(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def guardar(state: WorkflowState) -> dict[str, object]:
        motivo = state.get("motivo_pendiente")
        if not isinstance(motivo, str) or not motivo:
            raise ValueError("guardar_pendiente requiere motivo_pendiente")
        orden = _orden_sistema_desde_borrador(state, EstadoOrden.pendiente, motivo)
        emitida = _emitir_estado_orden(salida, orden, "pendiente", registrar_estado_orden)
        return {"orden_sistema": orden.model_dump(mode="json"), "orden_emitida": emitida}

    return guardar


def _nodo_guardar_aprobada(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def guardar(state: WorkflowState) -> dict[str, object]:
        if state.get("aprobada_por_reglas") is not True and state.get("decision_planificador") != "aprobada":
            raise ValueError("guardar_aprobada requiere aprobación de reglas o del planificador")
        orden = _orden_sistema_desde_borrador(state, EstadoOrden.aprobada, motivo=None)
        emitida = _emitir_estado_orden(salida, orden, "aprobada", registrar_estado_orden)
        return {"orden_sistema": orden.model_dump(mode="json"), "orden_emitida": emitida}

    return guardar


def _nodo_rechazar_orden(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def guardar(state: WorkflowState) -> dict[str, object]:
        if state.get("decision_planificador") == "rechazada":
            motivo = state.get("motivo_pendiente")
            motivo_rechazo = (
                f"Rechazada por el planificador: {motivo}"
                if isinstance(motivo, str) and motivo
                else "Rechazada por el planificador."
            )
        elif state.get("resultado_revision") == "invalida":
            revision = state.get("revision")
            errores = revision.get("errores") if isinstance(revision, dict) else None
            if not isinstance(errores, list) or any(not isinstance(error, str) for error in errores):
                raise ValueError("La revisión inválida requiere una lista de errores")
            motivo_rechazo = "Rechazada por revisión automática: " + "; ".join(errores)
        else:
            raise ValueError("rechazar_orden requiere rechazo del planificador o revisión inválida")

        datos_orden = state.get("orden_sistema")
        if isinstance(datos_orden, dict):
            orden = OrdenSistema.model_validate(datos_orden)
            rechazada = _orden_con_estado(orden, EstadoOrden.rechazada, motivo_rechazo)
        else:
            rechazada = _orden_sistema_desde_borrador(state, EstadoOrden.rechazada, motivo_rechazo)
        emitida = _emitir_estado_orden(salida, rechazada, "rechazada", registrar_estado_orden)
        return {"orden_sistema": rechazada.model_dump(mode="json"), "orden_emitida": emitida}

    return guardar


def _nodo_marcar_ejecutada(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def marcar(state: WorkflowState) -> dict[str, object]:
        orden = OrdenSistema.model_validate(state.get("orden_sistema"))
        cierre = NotaCierre.model_validate(state.get("cierre"))
        if cierre.orden_id != orden.id:
            raise ValueError("El cierre no corresponde a la OrdenSistema aprobada")
        ejecutada = _orden_con_estado(orden, EstadoOrden.ejecutada)
        emitida = _emitir_estado_orden(salida, ejecutada, f"ejecutada:{cierre.id}", registrar_estado_orden)
        return {"orden_sistema": ejecutada.model_dump(mode="json"), "orden_emitida": emitida}

    return marcar


def _nodo_cerrar_orden(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def cerrar(state: WorkflowState) -> dict[str, object]:
        if state.get("decision_supervisor") != "cerrar":
            raise ValueError("cerrar_orden requiere decision_supervisor cerrar")
        orden = OrdenSistema.model_validate(state.get("orden_sistema"))
        cierre = NotaCierre.model_validate(state.get("cierre"))
        cerrada = _orden_con_estado(orden, EstadoOrden.cerrada)
        emitida = _emitir_estado_orden(salida, cerrada, f"cerrada:{cierre.id}", registrar_estado_orden)
        return {"orden_sistema": cerrada.model_dump(mode="json"), "orden_emitida": emitida}

    return cerrar


def _nodo_guardar_observada(
    salida: SalidaWorkflow | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def guardar(state: WorkflowState) -> dict[str, object]:
        if state.get("decision_inspector") != "observar":
            raise ValueError("guardar_observada requiere decision_inspector observar")
        orden = OrdenSistema.model_validate(state.get("orden_sistema"))
        cierre = CierreValidado.model_validate(state.get("cierre_validado"))
        diferencias = [DiferenciaCierre.model_validate(item) for item in state.get("diferencias_cierre", [])]
        motivo = "; ".join(f"{item.tipo}: {item.codigo}" for item in diferencias)
        if not motivo:
            motivo = "Observación solicitada por el inspector."
        observada = _orden_con_estado(orden, EstadoOrden.observada, motivo)
        emitida = _emitir_estado_orden(salida, observada, f"observada:{cierre.id}", registrar_estado_orden)
        return {"orden_sistema": observada.model_dump(mode="json"), "orden_emitida": emitida}

    return guardar


def _nodo_calcular_habilita(
    precios_contrato: Mapping[str, Decimal],
) -> Callable[[WorkflowState], dict[str, object]]:
    def calcular(state: WorkflowState) -> dict[str, object]:
        orden = OrdenSistema.model_validate(state.get("orden_sistema"))
        cierre = CierreValidado.model_validate(state.get("cierre_validado"))
        inventario = state.get("inventario")
        if not isinstance(inventario, dict):
            raise ValueError("calcular_habilita requiere inventario con costos de repuestos")
        monto = calcular_monto_habilita(orden, cierre, precios_contrato, inventario)
        return {"monto_habilita_usd": float(monto)}

    return calcular


def _nodo_emitir_habilita(
    salida: SalidaWorkflow | None,
    asignar_numero_habilita: AsignarNumeroHabilita | None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
) -> Callable[[WorkflowState], dict[str, object]]:
    def emitir(state: WorkflowState) -> dict[str, object]:
        orden = OrdenSistema.model_validate(state.get("orden_sistema"))
        monto = state.get("monto_habilita_usd")
        if isinstance(monto, bool) or not isinstance(monto, (float, int)) or monto < 0:
            raise ValueError("emitir_habilita requiere un monto no negativo")
        if asignar_numero_habilita is None:
            raise RuntimeError("emitir_habilita requiere numeración persistente por orden")
        numero = asignar_numero_habilita(orden.id, datetime.now(UTC).year)
        if not isinstance(numero, str) or re.fullmatch(r"HAB-\d{4}-\d{4}", numero) is None:
            raise ValueError("El asignador devolvió un número de Habilita inválido")
        datos = orden.model_dump(mode="python")
        datos.update(
            {
                "estado": EstadoOrden.certificada,
                "habilita": numero,
                "monto_habilita_usd": float(monto),
                "ts": datetime.now(UTC),
            }
        )
        certificada = OrdenSistema.model_validate(datos)
        _emitir_estado_orden(salida, certificada, "certificada", registrar_estado_orden)
        payload = certificada.model_dump(mode="json")
        return {
            "orden_sistema": payload,
            "numero_habilita": numero,
            "habilita": payload,
        }

    return emitir


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
    if state.get("orden_duplicada_de"):
        return "duplicada"
    aprobada = state.get("aprobada_por_reglas")
    if aprobada is True:
        return "guardar_aprobada"
    if aprobada is False:
        return "guardar_pendiente"
    raise ValueError("evaluar_aprobacion requiere aprobada_por_reglas")


def _ruta_decision_planificador(state: WorkflowState) -> str:
    decision = state.get("decision_planificador")
    if decision == "aprobada":
        return "guardar_aprobada"
    if decision == "rechazada":
        return "rechazar_orden"
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
    salida: SalidaWorkflow | None = None,
    cliente_asr: ClienteAsr | None = None,
    *,
    descargar_audio: DescargarAudio | None = None,
    cliente_vision: ClienteVision | None = None,
    descargar_imagen: DescargarImagen | None = None,
    asignar_numero_habilita: AsignarNumeroHabilita | None = None,
    buscar_orden_abierta: BuscarOrdenAbierta | None = None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
    precios_contrato: Mapping[str, Decimal] | None = None,
    materiales: dict | None = None,
    recuperador: Recuperador | None = None,
    analizador: AnalizadorEscalado | None = None,
    obtener_historial_ordenes: ObtenerHistorialOrdenes | None = None,
) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
    materiales = cargar_materiales() if materiales is None else materiales
    precios_contrato = cargar_precios_contrato() if precios_contrato is None else precios_contrato
    if recuperador is None:
        directorio_corpus = Path(__file__).resolve().parents[1] / "datos" / "documentos"
        recuperador = Recuperador(cargar_corpus(directorio_corpus))

    builder = StateGraph(WorkflowState)
    builder.add_node("analizar_fotos", _nodo_analizar_fotos(cliente_vision, descargar_imagen))
    builder.add_node(
        "analizar_escalado",
        _nodo_analizar_escalado(recuperador, analizador, obtener_historial_ordenes),
    )
    builder.add_node("redactar_orden", _nodo_redactar_orden(materiales))
    builder.add_node("revisar_orden", _nodo_revisar_orden(materiales))
    builder.add_node("evaluar_aprobacion", _nodo_evaluar_aprobacion(buscar_orden_abierta))
    builder.add_node("guardar_pendiente", _nodo_guardar_pendiente(salida, registrar_estado_orden))
    builder.add_node("rechazar_orden", _nodo_rechazar_orden(salida, registrar_estado_orden))
    builder.add_node("guardar_aprobada", _nodo_guardar_aprobada(salida, registrar_estado_orden))
    builder.add_node("marcar_ejecutada", _nodo_marcar_ejecutada(salida, registrar_estado_orden))
    builder.add_node("interpretar_cierre", _nodo_interpretar_cierre(cliente_asr, descargar_audio))
    builder.add_node("comparar_cierre", _nodo_comparar_cierre)
    builder.add_node("calcular_habilita", _nodo_calcular_habilita(precios_contrato))
    builder.add_node(
        "emitir_habilita",
        _nodo_emitir_habilita(salida, asignar_numero_habilita, registrar_estado_orden),
    )
    builder.add_node("cerrar_orden", _nodo_cerrar_orden(salida, registrar_estado_orden))
    builder.add_node("guardar_observada", _nodo_guardar_observada(salida, registrar_estado_orden))
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
            "duplicada": END,
        },
    )
    builder.add_edge("guardar_aprobada", "esperar_cierre")
    builder.add_edge("guardar_pendiente", "esperar_decision")
    builder.add_conditional_edges(
        "esperar_decision",
        _ruta_decision_planificador,
        {
            "guardar_aprobada": "guardar_aprobada",
            "rechazar_orden": "rechazar_orden",
        },
    )

    builder.add_edge("esperar_cierre", "marcar_ejecutada")
    builder.add_edge("esperar_nuevo_cierre", "marcar_ejecutada")
    builder.add_edge("marcar_ejecutada", "interpretar_cierre")
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
    salida: SalidaWorkflow | None = None,
    cliente_asr: ClienteAsr | None = None,
    *,
    descargar_audio: DescargarAudio | None = None,
    cliente_vision: ClienteVision | None = None,
    descargar_imagen: DescargarImagen | None = None,
    asignar_numero_habilita: AsignarNumeroHabilita | None = None,
    buscar_orden_abierta: BuscarOrdenAbierta | None = None,
    registrar_estado_orden: RegistrarEstadoOrden | None = None,
    precios_contrato: Mapping[str, Decimal] | None = None,
    obtener_historial_ordenes: ObtenerHistorialOrdenes | None = None,
) -> Generator[CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState], None, None]:
    """Abre el saver, prepara sus tablas y mantiene vivo el grafo compilado."""
    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()
        yield construir_grafo(
            checkpointer,
            salida,
            cliente_asr,
            descargar_audio=descargar_audio,
            cliente_vision=cliente_vision,
            descargar_imagen=descargar_imagen,
            asignar_numero_habilita=asignar_numero_habilita,
            buscar_orden_abierta=buscar_orden_abierta,
            registrar_estado_orden=registrar_estado_orden,
            precios_contrato=precios_contrato,
            obtener_historial_ordenes=obtener_historial_ordenes,
        )
