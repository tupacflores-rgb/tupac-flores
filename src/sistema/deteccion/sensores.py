"""Anomalía sostenida de sensores (V4 mínimo): ventana + histéresis, sin picos aislados ni equipos parados.

Reglas:
  - Una lectura fuera de los límites de alarma del sensor (`/api/planta`) no es una anomalía: tiene que
    sostenerse `ventana_s` segundos (hora de las lecturas) y `min_muestras` lecturas seguidas. Un pico
    aislado, o una lectura mala (`calidad != buena`), reinicia la cuenta.
  - Histéresis: una anomalía activa se rearma recién cuando el valor vuelve adentro por un margen
    (`histeresis` del límite) durante `rearme_muestras` lecturas. Si oscila pegado al límite, no repite.
  - Una bomba parada o en mantenimiento (reserva) se ignora: su presión baja es lo normal.
  - Sale **un** `Escalado` por episodio (`origen="sensor"`, id determinista, sin `nota_ids`). **Nunca** una
    `AlertaSeguridad`: las alertas de seguridad solo salen de una nota (cada alerta falsa resta 0,25).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from planta_contrato import EventoSistema, TipoEvento

log = logging.getLogger("sistema.sensores")

EQUIPOS_IGNORADOS = {"parada", "mantenimiento"}


@dataclass(frozen=True)
class Parametros:
    ventana_s: float = 300.0  # 5 minutos sostenido
    min_muestras: int = 3
    histeresis: float = 0.03  # 3 % del límite
    rearme_muestras: int = 2


@dataclass
class _Estado:
    desde: datetime | None = None
    muestras: int = 0
    activa: bool = False
    dentro: int = 0
    episodio: str = ""


class DetectorSostenido:
    def __init__(
        self,
        limites: dict[tuple[str, str], tuple[float | None, float | None]],
        estado_equipo: Callable[[str], str | None],
        emitir: Callable[[EventoSistema], bool],
        parametros: Parametros | None = None,
        tendencia: Callable[[str, datetime], str | None] | None = None,
    ):
        self._tendencia = tendencia
        self._limites = limites
        self._estado_equipo = estado_equipo
        self._emitir = emitir
        self.p = parametros or Parametros()
        self._estados: dict[tuple[str, str], _Estado] = {}

    @classmethod
    def desde_definicion(
        cls, definicion: dict, estado_equipo, emitir, parametros: Parametros | None = None, tendencia=None
    ):
        limites = {
            (s["tag"], s["variable"]): (s.get("alarma_baja"), s.get("alarma_alta"))
            for s in definicion.get("sensores", [])
        }
        return cls(limites, estado_equipo, emitir, parametros, tendencia)

    def procesar(self, lectura: dict) -> EventoSistema | None:
        """Una lectura de `planta/sensores/{TAG}/{variable}`. Devuelve el evento si abrió una anomalía."""
        tag, variable = lectura.get("tag"), lectura.get("variable")
        clave = (tag, variable)
        if clave not in self._limites:
            return None
        st = self._estados.setdefault(clave, _Estado())
        valor = lectura.get("valor")
        if lectura.get("calidad") != "buena" or valor is None:
            self._reiniciar(st)
            return None
        if self._estado_equipo(tag) in EQUIPOS_IGNORADOS:
            self._reiniciar(st)  # reserva: su presión baja es lo normal
            return None
        ts = datetime.fromisoformat(str(lectura["ts"]))
        baja, alta = self._limites[clave]
        fuera = (alta is not None and valor > alta) or (baja is not None and valor < baja)
        if st.activa:
            if self._adentro_con_margen(valor, baja, alta):
                st.dentro += 1
                if st.dentro >= self.p.rearme_muestras:
                    self._reiniciar(st)
            else:
                st.dentro = 0
            return None
        if not fuera:
            self._reiniciar(st)  # pico aislado: se olvida
            return None
        if st.desde is None:
            st.desde, st.muestras = ts, 1
        else:
            st.muestras += 1
        if (ts - st.desde).total_seconds() >= self.p.ventana_s and st.muestras >= self.p.min_muestras:
            st.activa, st.dentro = True, 0
            st.episodio = st.desde.strftime("%Y%m%dT%H%M%S")
            evento = EventoSistema(
                id=f"EV-SEN-{tag}-{variable}-{st.episodio}",
                ts=ts,
                tipo=TipoEvento.escalado,
                equipo=tag,
                origen="sensor",
                nota_ids=[],
                resumen=(
                    f"{variable} de {tag} fuera de límites ({valor:g}; baja {baja}, alta {alta}) "
                    f"sostenido {int((ts - st.desde).total_seconds() // 60)} min." + self._contexto(tag, ts)
                ),
            )
            self._emitir(evento)
            log.warning("anomalía sostenida: %s", evento.resumen)
            return evento
        return None

    def _contexto(self, tag: str, ts: datetime) -> str:
        """B-3: la tendencia de las últimas horas del equipo, si el historian la da."""
        if self._tendencia is None:
            return ""
        try:
            t = self._tendencia(tag, ts)
        except Exception:  # contexto opcional
            t = None
        return f" Tendencia ({t})." if t else ""

    def _adentro_con_margen(self, valor: float, baja: float | None, alta: float | None) -> bool:
        h = self.p.histeresis
        if alta is not None and valor > alta - abs(alta) * h:
            return False
        return not (baja is not None and valor < baja + abs(baja) * h)

    @staticmethod
    def _reiniciar(st: _Estado) -> None:
        st.desde, st.muestras, st.activa, st.dentro, st.episodio = None, 0, False, 0, ""
