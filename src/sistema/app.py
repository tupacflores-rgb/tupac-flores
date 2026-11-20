"""Arranque y orquestación del agente.

Workers (cada uno supervisado, ver supervisor.py):
  latido     publica `sistema/estado` cada 30 s; independiente de todo lo demás
  base       aplica el esquema y reintenta hasta que PostgreSQL responda
  reloj      sigue el reloj simulado de la planta y carga la definición de equipos
  enlace     sondea el enlace con la nube
  entrada    consume lo que llega por MQTT, guarda y recién entonces acusa
  recupera   importa a la base lo que quedó en el journal local mientras estaba caída
"""

from __future__ import annotations

import json
import logging
import queue
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import psycopg
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from planta_contrato import EventoSistema, NotaCierre, NotaRecorrida, RespuestaOperador, TipoEvento, Topicos
from pydantic import ValidationError

from .config import Config
from .control import Presupuesto, RegistroConsumo
from .deteccion.eventos import construir as construir_eventos
from .deteccion.sensores import DetectorSostenido
from .dominio.catalogo import Catalogo
from .dominio.rango import RangoHistorico
from .dominio.tendencia import Tendencia
from .estado import EstadoCompartido, RelojPlanta
from .fotos import Fotos
from .integraciones.asr import ClienteAsr
from .integraciones.enlace import enlace_arriba
from .integraciones.mqtt import ClienteMqtt, Entrante
from .integraciones.planta_api import PlantaApi
from .integraciones.vision import ClienteVision
from .latido import Latido
from .persistencia.base import Base
from .persistencia.journal import Journal
from .persistencia.salida import Salida
from .pipeline import Pipeline
from .respaldo import RespaldoLLM
from .sincronizacion import Sincronizacion
from .supervisor import Supervisor
from .voz.dialogo import Dialogo, Estado, ts_de
from .workflow.decisiones import Decision, DecisionInspector, DecisionPlanificador, DecisionSupervisor
from .workflow.grafo import WorkflowState, grafo_con_postgres
from .workflow.topicos import (
    TOPICO_DECISION_INSPECTOR,
    TOPICO_DECISION_PLANIFICADOR,
    TOPICO_DECISION_SUPERVISOR,
)

log = logging.getLogger("sistema.app")

# Used to defer an early MQTT message, while treating messages for later stages as stale.
_ORDEN_WORKFLOW = {
    nombre: posicion
    for posicion, nombre in enumerate(
        (
            "analizar_fotos",
            "analizar_escalado",
            "redactar_orden",
            "revisar_orden",
            "evaluar_aprobacion",
            "rechazar_orden",
            "guardar_pendiente",
            "esperar_decision",
            "guardar_aprobada",
            "esperar_cierre",
            "esperar_nuevo_cierre",
            "marcar_ejecutada",
            "interpretar_cierre",
            "comparar_cierre",
            "esperar_supervisor",
            "cerrar_orden",
            "esperar_inspector",
            "guardar_observada",
            "calcular_habilita",
            "emitir_habilita",
        )
    )
}


class WorkflowAunNoListo(RuntimeError):
    """El hilo aún no llegó a la interrupción MQTT correspondiente."""


class Agente:
    def __init__(self, config: Config):
        self.config = config
        self.sup = Supervisor()
        self._workflow: CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState] | None = None
        self._workflow_listo = threading.Event()
        self._workflow_reinicio = threading.Event()
        self._workflow_lock = threading.RLock()
        self._workflow_jobs: queue.Queue[tuple[EventoSistema, tuple[str, ...]]] = queue.Queue()
        self.estado = EstadoCompartido()
        self.reloj = RelojPlanta()
        self.mqtt = ClienteMqtt(config)
        self.api = PlantaApi(config.planta_url)
        self.base = Base(config.database_url)
        self.journal = Journal(config.journal_dir)
        self.salida = Salida(self.base, self.journal, lambda t, m: self.mqtt.publicar(t, m, qos=1))
        self.asr = ClienteAsr(config.asr_url)
        self.definicion: dict | None = None
        self._catalogo: Catalogo | None = None
        self.consumo = RegistroConsumo(self.base)
        self.fotos = Fotos(
            lambda url: self.api.descargar(url, timeout_s=3.0, reintentos=2), config.fotos_dir, self.base
        )
        self._detector_sensores: DetectorSostenido | None = None
        self._estados_equipo: tuple[float, dict[str, str]] = (0.0, {})
        self.pipeline = Pipeline(
            self.api,
            self.asr,
            self.base.guardar_transcripcion,
            self.salida,
            lambda: self.catalogo,
            presupuesto=Presupuesto(
                config.presupuesto_pasos, config.presupuesto_tokens, config.timeout_nota_s
            ),
            registro=self.consumo,
        )

        self.tendencia = Tendencia(self.api.historian, lambda: self.catalogo)
        self.rango = RangoHistorico(
            lambda tag, variable: [m.get("valor") for m in self.api.historian(tag, variable)]
        )
        self.respaldo = (
            RespaldoLLM(
                config.llm_url,
                config.llm_modelo,
                config.llm_timeout_s,
                config.llm_max_tokens,
                registro=self.consumo,
            )
            if config.llm_url
            else None
        )
        self.dialogo = Dialogo(
            config,
            self.salida,
            self.base,
            lambda: self.catalogo,
            self.reloj,
            rango=self.rango,
            respaldo=self.respaldo,
        )
        self.pipeline.posterior = self._nota_procesada
        self.sync = Sincronizacion(self.estado)
        self.dialogo.al_confirmar = self.al_confirmar_parte

        # Ganchos de entrada (se reemplazan en los tests).
        self.al_recibir_nota: Callable[[NotaRecorrida], None] = self.pipeline.encolar
        self.al_recibir_respuesta: Callable[[RespuestaOperador], None] = self.dialogo.encolar_respuesta
        self.componentes_listos: list[Callable[[], bool]] = [self.asr.listo]
        self.recuperadores: list[Callable[[], None]] = []

        self.latido = Latido(
            config,
            publicar=lambda m: self.mqtt.publicar(Topicos.ESTADO, m, qos=1),
            estado=self.estado,
            reloj=self.reloj,
            listo=self.listo,
        )

    @property
    def catalogo(self) -> Catalogo | None:
        if self._catalogo is None and self.definicion is not None:
            self._catalogo = Catalogo(self.definicion)
        return self._catalogo

    def _nota_procesada(self, nota: NotaRecorrida, texto: str, deteccion) -> None:
        """Una nota procesada con el enlace caído (alerta, borrador o parte) queda sin sincronizar."""
        self.sync.registrar(nota.id)
        self.dialogo.encolar_nota(nota, texto, deteccion)

    def al_confirmar_parte(self, est: Estado) -> None:
        """El operador confirmó el parte: recién ahora hay eventos (uno por equipo)."""
        cat = self.catalogo
        eventos = construir_eventos(
            est.nota_id,
            ts_de(est),
            est.filas,
            cat,
            nombre_equipo=lambda t: cat.equipos[t].nombres[0],
            tendencia=self.tendencia.describir,
            foto=self.fotos.descripcion,
        )
        if eventos:
            self.salida.emitir_lote([(e.id, Topicos.EVENTOS, e) for e in eventos])
            for evento in eventos:
                fotos = tuple(
                    dict.fromkeys(fila.foto for fila in est.filas if fila.tag == evento.equipo and fila.foto)
                )
                self._encolar_workflow(evento, fotos)
        self.sync.registrar(est.nota_id)

    # -- estado de arranque -------------------------------------------------------------------------

    def listo(self) -> bool:
        return (
            self.base.lista.is_set()
            and self.definicion is not None
            and self.mqtt.conectado.is_set()
            and all(c() for c in self.componentes_listos)
        )

    # -- workers ------------------------------------------------------------------------------------

    def _worker_base(self, parar: threading.Event) -> None:
        self.base.iniciar(parar)
        parar.wait()

    def _worker_workflow(self, parar: threading.Event) -> None:
        while not parar.is_set() and not self.base.lista.wait(0.5):
            pass
        if parar.is_set():
            return

        self._workflow_reinicio.clear()
        cliente_vision = (
            ClienteVision(self.config.nvidia_vision_url) if self.config.nvidia_vision_url else None
        )
        try:
            with grafo_con_postgres(
                self.config.database_url,
                salida=self.salida,
                cliente_asr=self.asr,
                descargar_audio=self.api.descargar,
                cliente_vision=cliente_vision,
                descargar_imagen=self.api.descargar,
                asignar_numero_habilita=self.base.asignar_numero_habilita,
            ) as grafo:
                with self._workflow_lock:
                    self._workflow = grafo
                    self._workflow_listo.set()
                try:
                    while not parar.is_set() or not self._workflow_jobs.empty():
                        if self._workflow_reinicio.is_set():
                            raise RuntimeError(
                                "Se recreará el checkpointer tras perder la conexión a PostgreSQL"
                            )
                        try:
                            evento, fotos = self._workflow_jobs.get(timeout=0.5)
                        except queue.Empty:
                            continue
                        try:
                            self._iniciar_workflow(evento, fotos)
                        except psycopg.OperationalError:
                            self._workflow_reinicio.set()
                            self._workflow_jobs.put((evento, fotos))
                            raise
                        except Exception:
                            log.exception("falló el workflow del evento %s", evento.id)
                        finally:
                            self._workflow_jobs.task_done()
                finally:
                    with self._workflow_lock:
                        self._workflow = None
                        self._workflow_listo.clear()
        finally:
            if cliente_vision is not None:
                cliente_vision.close()

    def _worker_reloj(self, parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                if self.definicion is None:
                    self.definicion = self.api.planta()
                    log.info("definición de la planta cargada (%d equipos)", len(self.definicion["equipos"]))
                    self._precalentar_rango()
                self.reloj.fijar(datetime.fromisoformat(self.api.estado()["ts"]))
            except Exception as e:
                log.warning("planta no disponible todavía: %s", e)
            parar.wait(2.0)

    def _worker_enlace(self, parar: threading.Event) -> None:
        while not parar.is_set():
            ok = enlace_arriba(self.config.enlace_proxy, self.config.enlace_destino)
            if ok != self.estado.enlace_ok:
                log.warning("enlace %s", "ARRIBA" if ok else "CAÍDO")
            self.estado.enlace_ok = ok
            parar.wait(3.0)

    def _worker_entrada(self, parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                e = self.mqtt.entrantes.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._procesar_entrante(e)
            except WorkflowAunNoListo:
                log.debug("workflow aún no listo para %s; se reintentará sin acuse", e.topico)
                if not parar.wait(0.25):
                    self.mqtt.entrantes.put(e)
                continue
            except Exception:
                # Sin acuse: el broker vuelve a entregar el mensaje cuando se reconecte la sesión.
                log.exception("falló el procesamiento de %s; no se acusa", e.topico)
                continue
            self.mqtt.acusar(e)

    def _worker_recuperacion(self, parar: threading.Event) -> None:
        ultima_purga = 0.0
        while not parar.is_set():
            if self.base.lista.is_set() and time.monotonic() - ultima_purga > 3600:
                ultima_purga = time.monotonic()
                try:
                    borradas = self.base.purgar()
                    if any(borradas.values()):
                        log.info("retención: filas borradas %s", borradas)
                except Exception:
                    log.exception("falló la purga de retención")
            if self.base.lista.is_set():
                for recuperar in self.recuperadores:
                    try:
                        recuperar()
                    except Exception:
                        log.exception("falló la recuperación del journal")
            parar.wait(5.0)

    # -- entrada ------------------------------------------------------------------------------------

    def _procesar_entrante(self, e: Entrante) -> None:
        if e.topico.startswith("planta/sensores/"):
            self._procesar_sensor(e)  # anomalía sostenida -> Escalado; nunca una alerta de seguridad
            return
        try:
            datos = json.loads(e.payload)
        except json.JSONDecodeError:
            log.error("payload ilegible en %s; se descarta", e.topico)
            return
        if e.topico == Topicos.RECORRIDAS:
            self._recibir_nota(datos)
        elif e.topico == Topicos.RESPUESTAS:
            self._recibir_respuesta(datos)
        elif e.topico == Topicos.CIERRES:
            self._recibir_cierre(datos)
        elif e.topico == TOPICO_DECISION_PLANIFICADOR:
            self._recibir_decision(datos, DecisionPlanificador, "esperar_decision")
        elif e.topico == TOPICO_DECISION_SUPERVISOR:
            self._recibir_decision(datos, DecisionSupervisor, "esperar_supervisor")
        elif e.topico == TOPICO_DECISION_INSPECTOR:
            self._recibir_decision(datos, DecisionInspector, "esperar_inspector")

    def _esperar_workflow(self) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
        if self.sup.parar.is_set():
            raise WorkflowAunNoListo("La aplicación se está deteniendo")
        if self._workflow_reinicio.is_set():
            raise WorkflowAunNoListo("El checkpointer está reconectando")
        if not self._workflow_listo.is_set():
            raise WorkflowAunNoListo("El workflow está reconectando su checkpointer")
        with self._workflow_lock:
            if self._workflow is None:
                raise WorkflowAunNoListo("El workflow dejó de estar disponible")
            return self._workflow

    def _reserva_disponible(self, equipo: str | None) -> bool:
        if self.definicion is None:
            log.warning("no se pudo verificar la reserva: falta la definición de planta")
            return False
        reservas = [
            item
            for item in self.definicion.get("equipos", [])
            if item.get("tipo") == "bomba"
            and any("reserva" in nombre.casefold() for nombre in item.get("nombres", []))
        ]
        if len(reservas) != 1 or reservas[0].get("tag") == equipo:
            log.warning("no se pudo confirmar una bomba de reserva disponible para %s", equipo)
            return False
        try:
            bombas = self.api.estado().get("bombas")
        except Exception:
            log.exception("no se pudo consultar la disponibilidad de la bomba de reserva")
            return False
        tag_reserva = reservas[0].get("tag")
        bomba = (
            next(
                (item for item in bombas if isinstance(item, dict) and item.get("tag") == tag_reserva),
                None,
            )
            if isinstance(bombas, list)
            else None
        )
        if not isinstance(bomba, dict) or not isinstance(bomba.get("estado"), str):
            log.warning("la API no informó el estado de la bomba de reserva %s", tag_reserva)
            return False
        return bomba["estado"] == "parada"

    def _iniciar_workflow(self, evento: EventoSistema, fotos: tuple[str, ...] = ()) -> None:
        if evento.tipo != TipoEvento.escalado:
            return
        orden_id = f"OT-{evento.id}"
        config = {"configurable": {"thread_id": orden_id}}
        reserva_disponible = self._reserva_disponible(evento.equipo)
        grafo = self._esperar_workflow()
        with self._workflow_lock:
            estado = grafo.get_state(config)
            if estado.metadata is not None:
                log.info("workflow %s ya iniciado; se ignora el Escalado duplicado", orden_id)
                return
            grafo.invoke(
                {
                    "evento": evento.model_dump(mode="json"),
                    "correlation_id": orden_id,
                    "reserva_disponible": reserva_disponible,
                    "fotos": [{"url": url} for url in fotos],
                },
                config=config,
            )

    def _encolar_workflow(self, evento: EventoSistema, fotos: tuple[str, ...] = ()) -> None:
        """Deja la ejecución del grafo al worker dedicado, sin retener el worker de diálogo o MQTT."""
        if evento.tipo == TipoEvento.escalado:
            self._workflow_jobs.put((evento, fotos))

    def _reanudar_workflow(
        self,
        orden_id: str,
        respuesta: dict[str, object],
        nodo_esperado: str | tuple[str, ...],
    ) -> None:
        config = {"configurable": {"thread_id": orden_id}}
        grafo = self._esperar_workflow()
        try:
            with self._workflow_lock:
                estado = grafo.get_state(config)
                if estado.metadata is None:
                    raise WorkflowAunNoListo(f"thread_id {orden_id} todavía no tiene checkpoint")
                if not estado.next:
                    log.info("se descarta decisión para la orden %s: el workflow ya terminó", orden_id)
                    return
                nodos_esperados = (nodo_esperado,) if isinstance(nodo_esperado, str) else nodo_esperado
                if not any(nodo in estado.next for nodo in nodos_esperados):
                    try:
                        posicion_esperada = min(_ORDEN_WORKFLOW[nodo] for nodo in nodos_esperados)
                        posiciones_actuales = [_ORDEN_WORKFLOW[nodo] for nodo in estado.next]
                    except KeyError as error:
                        raise RuntimeError(f"Nodo de workflow no reconocido: {error.args[0]}") from error
                    if min(posiciones_actuales) < posicion_esperada:
                        raise WorkflowAunNoListo(f"thread_id {orden_id} aún no llegó a {nodos_esperados}")
                    log.warning(
                        "se descarta mensaje obsoleto para la orden %s: espera %s, estado actual %s",
                        orden_id,
                        nodos_esperados,
                        estado.next,
                    )
                    return
                grafo.invoke(Command(resume=respuesta), config=config)
        except psycopg.OperationalError as error:
            self._workflow_reinicio.set()
            raise WorkflowAunNoListo(
                f"PostgreSQL desconectó el checkpointer de la orden {orden_id}"
            ) from error

    def _recibir_decision(
        self,
        datos: dict,
        modelo: type[Decision],
        nodo_esperado: str,
    ) -> None:
        try:
            decision = modelo.model_validate(datos)
        except ValidationError as error:
            log.error("decisión inválida para %s: %s", nodo_esperado, error.errors(include_url=False))
            return
        self._reanudar_workflow(
            decision.orden_id,
            {"decision": decision.decision},
            nodo_esperado,
        )

    def _recibir_cierre(self, datos: dict) -> None:
        try:
            cierre = NotaCierre.model_validate(datos)
        except ValidationError as error:
            log.error("NotaCierre inválido: %s", error.errors(include_url=False))
            return
        self._reanudar_workflow(
            cierre.orden_id,
            cierre.model_dump(mode="json"),
            ("esperar_cierre", "esperar_nuevo_cierre"),
        )

    def _emitir_evento(self, evento: EventoSistema) -> bool:
        publicado = self.salida.emitir(evento.id, Topicos.EVENTOS, evento)
        self._encolar_workflow(evento)
        return publicado

    def _precalentar_rango(self) -> None:
        """Trae el rango histórico (45 días) de lo que se dicta en campo, sin frenar el arranque."""
        pares = [
            (e["tag"], "presion_descarga" if e["tipo"] == "bomba" else "presion_diferencial")
            for e in self.definicion["equipos"]
            if e["tipo"] in ("bomba", "filtro")
        ]
        threading.Thread(
            target=self.rango.precalentar, args=(pares,), name="rango-historico", daemon=True
        ).start()

    def _estado_de_equipo(self, tag: str) -> str | None:
        """Estado de la bomba (marcha, parada, mantenimiento, disparo) de /api/estado, con caché de 10 s."""
        t, estados = self._estados_equipo
        if time.monotonic() - t > 10.0:
            try:
                estados = {b["tag"]: b["estado"] for b in self.api.estado().get("bombas", [])}
                self._estados_equipo = (time.monotonic(), estados)
            except Exception:
                log.warning("no se pudo consultar el estado de los equipos; se usa el último conocido")
                self._estados_equipo = (time.monotonic(), estados)
        return estados.get(tag)

    def _procesar_sensor(self, e: Entrante) -> None:
        if self.definicion is None:
            return
        try:
            if self._detector_sensores is None:
                self._detector_sensores = DetectorSostenido.desde_definicion(
                    self.definicion,
                    self._estado_de_equipo,
                    self._emitir_evento,
                    tendencia=self.tendencia.describir,
                )
            self._detector_sensores.procesar(json.loads(e.payload))
        except Exception:
            log.exception("falló el procesamiento de una lectura de sensor")

    def es_vieja(self, ts: datetime) -> bool:
        """Resto de un escenario anterior: más de `nota_vieja_horas` simuladas del reloj de la planta."""
        try:
            self.reloj.fijar(datetime.fromisoformat(self.api.estado()["ts"]))
        except Exception:
            log.warning("no se pudo consultar el reloj de la planta; se usa el último conocido")
        if not self.reloj.conocido():
            return False
        return abs(self.reloj.ahora() - ts) > timedelta(hours=self.config.nota_vieja_horas)

    def _recibir_nota(self, datos: dict) -> None:
        try:
            nota = NotaRecorrida.model_validate(datos)
        except ValidationError as e:
            log.error("nota inválida descartada: %s", e.errors(include_url=False))
            return
        if self.es_vieja(nota.ts):
            log.warning(
                "nota %s descartada: ts=%s está a más de %s h del reloj de la planta (%s)",
                nota.id,
                nota.ts.isoformat(),
                self.config.nota_vieja_horas,
                self.reloj.ahora().isoformat(),
            )
            return
        if self.guardar_nota(nota):
            self.fotos.encolar(nota.id, nota.fotos)  # se descargan en su propio hilo
            self.al_recibir_nota(nota)
        else:
            log.info("nota %s repetida (entrega duplicada del broker); ya estaba guardada", nota.id)

    def guardar_nota(self, nota: NotaRecorrida) -> bool:
        """Guarda la nota. Con la base caída queda en el journal local con fsync (y cuenta como guardada)."""
        try:
            return self.base.registrar_nota(nota.id, nota.ts, nota.recorrida_id, nota.audio_url, nota.fotos)
        except Exception as e:
            log.warning("base caída al guardar %s (%s); queda en el journal", nota.id, e)
            self.journal.agregar("nota", json.loads(nota.model_dump_json()))
            return True

    def _recibir_respuesta(self, datos: dict) -> None:
        try:
            resp = RespuestaOperador.model_validate(datos)
        except ValidationError as e:
            log.error("respuesta inválida descartada: %s", e.errors(include_url=False))
            return
        try:
            nueva = self.base.registrar_respuesta(resp.id, resp.mensaje_id, resp.ts, resp.texto)
        except Exception as e:
            log.warning("base caída al guardar la respuesta %s (%s); queda en el journal", resp.id, e)
            self.journal.agregar("respuesta", json.loads(resp.model_dump_json()))
            nueva = True
        if nueva:
            self.al_recibir_respuesta(resp)

    def recuperar_journal_notas(self) -> None:
        """Importa a la base las notas y respuestas que quedaron en el journal con la base caída."""
        extraidos = self.journal.extraer({"nota", "respuesta"})
        for i, r in enumerate(extraidos):
            try:
                if r["tipo"] == "nota":
                    n = NotaRecorrida.model_validate(r["datos"])
                    self.base.registrar_nota(n.id, n.ts, n.recorrida_id, n.audio_url, n.fotos)
                else:
                    x = RespuestaOperador.model_validate(r["datos"])
                    self.base.registrar_respuesta(x.id, x.mensaje_id, x.ts, x.texto)
            except Exception:
                for pendiente in extraidos[i:]:  # la base sigue caída: se reintenta más tarde
                    self.journal.agregar(pendiente["tipo"], pendiente["datos"])
                return
        if extraidos:
            log.info("journal: %d registros importados a la base", len(extraidos))

    # -- ciclo de vida ------------------------------------------------------------------------------

    def correr(self) -> None:
        self.recuperadores += [self.recuperar_journal_notas, self.salida.recuperar]
        self.mqtt.arrancar()
        # El latido va primero y en su propio hilo: nada de lo demás puede demorarlo.
        self.sup.lanzar("latido", self.latido.correr)
        self.sup.lanzar("base", self._worker_base)
        self.sup.lanzar("workflow", self._worker_workflow)
        self.sup.lanzar("reloj", self._worker_reloj)
        self.sup.lanzar("enlace", self._worker_enlace)
        self.sup.lanzar("entrada", self._worker_entrada)
        for i in (1, 2):  # dos plazas de seguridad en el ASR: una nota insegura no espera a otra en curso
            self.sup.lanzar(f"pipeline-seguridad-{i}", self.pipeline.correr_seguridad)
        self.sup.lanzar("fotos", self.fotos.correr)
        self.sup.lanzar("pipeline", self.pipeline.correr)
        self.sup.lanzar("dialogo", self.dialogo.correr)
        self.sup.lanzar("sincronizacion", self.sync.correr)
        self.sup.lanzar("recuperacion", self._worker_recuperacion)
        self.sup.parar.wait()
        self.sup.detener()
        self.mqtt.detener()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = Config.desde_entorno()
    agente = Agente(config)
    for senal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(senal, lambda *_: agente.sup.parar.set())
    log.info("arrancando el sistema de %s", config.sistema)
    agente.correr()
