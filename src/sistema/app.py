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
from collections.abc import Callable
from datetime import datetime, timedelta

from planta_contrato import NotaRecorrida, RespuestaOperador, Topicos
from pydantic import ValidationError

from .config import Config
from .deteccion.eventos import construir as construir_eventos
from .dominio.catalogo import Catalogo
from .estado import EstadoCompartido, RelojPlanta
from .integraciones.asr import ClienteAsr
from .integraciones.enlace import enlace_arriba
from .integraciones.mqtt import ClienteMqtt, Entrante
from .integraciones.planta_api import PlantaApi
from .latido import Latido
from .persistencia.base import Base
from .persistencia.journal import Journal
from .persistencia.salida import Salida
from .pipeline import Pipeline
from .sincronizacion import Sincronizacion
from .supervisor import Supervisor
from .voz.dialogo import Dialogo, Estado, ts_de

log = logging.getLogger("sistema.app")


class Agente:
    def __init__(self, config: Config):
        self.config = config
        self.sup = Supervisor()
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
        self.pipeline = Pipeline(
            self.api, self.asr, self.base.guardar_transcripcion, self.salida, lambda: self.catalogo
        )

        self.dialogo = Dialogo(config, self.salida, self.base, lambda: self.catalogo, self.reloj)
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
            est.nota_id, ts_de(est), est.filas, cat, nombre_equipo=lambda t: cat.equipos[t].nombres[0]
        )
        if eventos:
            self.salida.emitir_lote([(e.id, Topicos.EVENTOS, e) for e in eventos])
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

    def _worker_reloj(self, parar: threading.Event) -> None:
        while not parar.is_set():
            try:
                if self.definicion is None:
                    self.definicion = self.api.planta()
                    log.info("definición de la planta cargada (%d equipos)", len(self.definicion["equipos"]))
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
            except Exception:
                # Sin acuse: el broker vuelve a entregar el mensaje cuando se reconecte la sesión.
                log.exception("falló el procesamiento de %s; no se acusa", e.topico)
                continue
            self.mqtt.acusar(e)

    def _worker_recuperacion(self, parar: threading.Event) -> None:
        while not parar.is_set():
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
            return  # Entrega 1: los sensores no intervienen en el parte ni en la seguridad.
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
            log.info("cierre recibido (fuera del alcance de la Entrega 1): %s", datos.get("id"))

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
        self.sup.lanzar("reloj", self._worker_reloj)
        self.sup.lanzar("enlace", self._worker_enlace)
        self.sup.lanzar("entrada", self._worker_entrada)
        for i in (1, 2):  # dos plazas de seguridad en el ASR: una nota insegura no espera a otra en curso
            self.sup.lanzar(f"pipeline-seguridad-{i}", self.pipeline.correr_seguridad)
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
