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
from datetime import datetime, timedelta

from planta_contrato import EventoSistema, TipoEvento

log = logging.getLogger("sistema.sensores")

EQUIPOS_IGNORADOS = {"parada", "mantenimiento"}
# Tasa normal de crecimiento de ΔP corregida (kg/cm² por hora, FC48-3 §7.3 / Anexo D).
TASAS_NORMALES_FILTRO = {
    "F-201A": 1.6 * (0.75 / (30 * 24)),
    "F-201B": 1.6 * (0.75 / (45 * 24)),
}


@dataclass(frozen=True)
class Parametros:
    ventana_s: float = 300.0  # 5 minutos sostenido
    min_muestras: int = 3
    histeresis: float = 0.03  # 3 % del límite
    rearme_muestras: int = 2
    colmatacion_ventana_h: float = 6.0
    colmatacion_min_horas: float = 5.0
    colmatacion_min_muestras: int = 240
    colmatacion_tramo: int = 30
    colmatacion_delta_min: float = 0.035
    colmatacion_factor: float = 4.0
    colmatacion_intervalo_s: float = 1800.0
    colmatacion_dp_min: float = 0.35
    colmatacion_caudal_min: float = 1000.0
    colmatacion_caudal_diseno: float = 2400.0


@dataclass
class _Estado:
    desde: datetime | None = None
    muestras: int = 0
    activa: bool = False
    dentro: int = 0
    episodio: str = ""


@dataclass
class _EstadoColmatacion:
    ultimo_chequeo: datetime | None = None
    activa: bool = False
    episodio: str = ""


HistorianCallback = Callable[[str, str, str | None, str | None], list[dict]]


class DetectorSostenido:
    def __init__(
        self,
        limites: dict[tuple[str, str], tuple[float | None, float | None]],
        estado_equipo: Callable[[str], str | None],
        emitir: Callable[[EventoSistema], bool],
        parametros: Parametros | None = None,
        tendencia: Callable[[str, datetime], str | None] | None = None,
        historian: HistorianCallback | None = None,
    ):
        self._tendencia = tendencia
        self._historian = historian
        self._limites = limites
        self._estado_equipo = estado_equipo
        self._emitir = emitir
        self.p = parametros or Parametros()
        self._estados: dict[tuple[str, str], _Estado] = {}
        self._colmatacion: dict[str, _EstadoColmatacion] = {}

    @classmethod
    def desde_definicion(
        cls,
        definicion: dict,
        estado_equipo,
        emitir,
        parametros: Parametros | None = None,
        tendencia=None,
        historian: HistorianCallback | None = None,
    ):
        limites = {
            (s["tag"], s["variable"]): (s.get("alarma_baja"), s.get("alarma_alta"))
            for s in definicion.get("sensores", [])
        }
        return cls(limites, estado_equipo, emitir, parametros, tendencia, historian)

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
            return self._evaluar_colmatacion(tag, variable, float(valor), ts)
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

    def _evaluar_colmatacion(
        self, tag: str | None, variable: str | None, valor: float, ts: datetime
    ) -> EventoSistema | None:
        """Detecta colmatación acelerada en filtros por crecimiento de ΔP corregida por caudal."""
        if (
            self._historian is None
            or not tag
            or not tag.startswith("F-")
            or variable != "presion_diferencial"
        ):
            return None
        st = self._colmatacion.setdefault(tag, _EstadoColmatacion())
        if valor < self.p.colmatacion_dp_min:
            st.activa = False
            return None
        if st.activa:
            return None
        if st.ultimo_chequeo is not None:
            transcurrido = (ts - st.ultimo_chequeo).total_seconds()
            if 0 <= transcurrido < self.p.colmatacion_intervalo_s:
                return None
        st.ultimo_chequeo = ts

        desde = (ts - timedelta(hours=self.p.colmatacion_ventana_h)).isoformat()
        hasta = ts.isoformat()
        try:
            dps = self._historian(tag, "presion_diferencial", desde, hasta)
            caudales = self._historian("FT-401", "caudal", desde, hasta)
        except Exception as exc:
            log.warning("no se pudo consultar el historian para colmatación de %s: %s", tag, exc)
            return None

        caudal_por_ts = {
            m["ts"]: float(m["valor"])
            for m in caudales
            if isinstance(m, dict) and m.get("ts") and m.get("valor") is not None
        }
        pares: list[tuple[datetime, float]] = []
        for m in dps:
            if not isinstance(m, dict):
                continue
            ts_muestra = m.get("ts")
            dp = m.get("valor")
            if not ts_muestra or dp is None:
                continue
            q = caudal_por_ts.get(ts_muestra)
            if q is None or q < self.p.colmatacion_caudal_min:
                continue
            dp_corr = float(dp) * (self.p.colmatacion_caudal_diseno / q) ** 2
            pares.append((datetime.fromisoformat(str(ts_muestra)), dp_corr))

        pares.sort(key=lambda item: item[0])
        tramo = self.p.colmatacion_tramo
        if len(pares) < max(self.p.colmatacion_min_muestras, tramo * 3):
            return None
        horas = (pares[-1][0] - pares[0][0]).total_seconds() / 3600.0
        if horas < self.p.colmatacion_min_horas:
            return None

        valores = [v for _, v in pares]
        inicio = sum(valores[:tramo]) / tramo
        mitad = len(valores) // 2
        desplazamiento = tramo // 2
        medio = sum(valores[mitad - desplazamiento : mitad - desplazamiento + tramo]) / tramo
        fin = sum(valores[-tramo:]) / tramo
        delta = fin - inicio
        tasa = delta / horas
        tasa_normal = TASAS_NORMALES_FILTRO.get(tag, 1.6 * (0.75 / (30 * 24)))

        if delta < self.p.colmatacion_delta_min or not (fin > medio > inicio):
            return None
        if tasa < self.p.colmatacion_factor * tasa_normal:
            return None

        st.activa = True
        st.episodio = ts.strftime("%Y%m%d")
        factor = tasa / tasa_normal
        evento = EventoSistema(
            id=f"EV-SEN-{tag}-colmatacion-{st.episodio}",
            ts=ts,
            tipo=TipoEvento.escalado,
            equipo=tag,
            origen="sensor",
            nota_ids=[],
            resumen=(
                f"F07 colmatación acelerada en filtro {tag}: ΔP corregida subió de "
                f"{inicio:.2f} a {fin:.2f} kg/cm² en {horas:.1f} h ({factor:.1f}x lo normal)."
                + self._contexto(tag, ts)
            ),
        )
        self._emitir(evento)
        log.warning("colmatación acelerada: %s", evento.resumen)
        return evento

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
