"""Cliente MQTT v5 con client_id fijo y sesión persistente de expiración corta.

- Notas, respuestas y cierres llegan por QoS 1 con **acuse manual**: se acusan recién cuando el trabajo quedó
  guardado. Si el agente cae antes, el broker vuelve a entregar el mensaje y la clave primaria evita el doble
  proceso.
- Los sensores llegan por QoS 0.
- Lo que se publica se valida contra el contrato antes de salir (F1).
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from planta_contrato import Topicos
from pydantic import BaseModel

from ..config import Config

log = logging.getLogger("sistema.mqtt")

SUSCRIPCIONES: list[tuple[str, int]] = [
    (Topicos.RECORRIDAS, 1),
    (Topicos.RESPUESTAS, 1),
    (Topicos.CIERRES, 1),
    ("planta/sensores/#", 0),
]


@dataclass
class Entrante:
    topico: str
    payload: bytes
    mid: int
    qos: int


class ClienteMqtt:
    def __init__(self, config: Config):
        self.config = config
        self.entrantes: queue.Queue[Entrante] = queue.Queue()
        self.conectado = threading.Event()
        self._cliente = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.mqtt_client_id,
            protocol=mqtt.MQTTv5,
            manual_ack=True,
        )
        self._cliente.on_connect = self._al_conectar
        self._cliente.on_disconnect = self._al_desconectar
        self._cliente.on_message = self._al_recibir

    # -- ciclo de vida ------------------------------------------------------------------------------

    def arrancar(self) -> None:
        props = Properties(PacketTypes.CONNECT)
        props.SessionExpiryInterval = self.config.mqtt_sesion_s
        self._cliente.reconnect_delay_set(1, 10)
        self._cliente.connect_async(
            self.config.mqtt_host, self.config.mqtt_puerto, keepalive=30, clean_start=False, properties=props
        )
        self._cliente.loop_start()

    def detener(self) -> None:
        self._cliente.disconnect()
        self._cliente.loop_stop()

    def _al_conectar(self, cliente, _userdata, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            log.error("conexión MQTT rechazada: %s", reason_code)
            return
        cliente.subscribe(SUSCRIPCIONES)
        self.conectado.set()
        log.info("conectado al broker %s:%s", self.config.mqtt_host, self.config.mqtt_puerto)

    def _al_desconectar(self, _cliente, _userdata, _flags, reason_code, _props) -> None:
        self.conectado.clear()
        log.warning("desconectado del broker: %s", reason_code)

    def _al_recibir(self, _cliente, _userdata, msg) -> None:
        self.entrantes.put(Entrante(msg.topic, bytes(msg.payload), msg.mid, msg.qos))

    # -- entrada ------------------------------------------------------------------------------------

    def acusar(self, entrante: Entrante) -> None:
        """Acuse manual: solo después de guardar. Los mensajes QoS 0 no se acusan."""
        if entrante.qos > 0:
            self._cliente.ack(entrante.mid, entrante.qos)

    # -- salida -------------------------------------------------------------------------------------

    def publicar(self, topico: str, modelo: BaseModel, qos: int = 1, espera_s: float = 1.0) -> bool:
        """Publica un mensaje del contrato. Con QoS 1 espera el acuse del broker (un reintento)."""
        payload = modelo.model_dump_json()
        for _ in range(2 if qos > 0 else 1):
            info = self._cliente.publish(topico, payload, qos=qos)
            if qos == 0:
                return info.rc == mqtt.MQTT_ERR_SUCCESS
            try:
                info.wait_for_publish(timeout=espera_s)
            except (ValueError, RuntimeError):
                continue
            if info.is_published():
                return True
        return False
