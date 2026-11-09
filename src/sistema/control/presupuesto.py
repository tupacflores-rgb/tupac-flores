"""Control de una nota: presupuesto de pasos y tokens, timeout global y condiciones de corte.

Cada nota se atiende dentro de una `SesionNota`. Cada paso (descarga, transcripción, tool, llamada a un
modelo) pasa por `paso()`, que cuenta y corta con `CorteDePresupuesto` si se cumple una condición:

  PASOS       más pasos que `max_pasos`
  TOKENS      más tokens (entrada + salida) que `max_tokens`
  TIEMPO      pasó más que `timeout_s` desde que empezó la nota (timeout global, no por paso)
  REPETICION  el mismo paso con los mismos argumentos más de `max_repeticiones` veces (un bucle)

La ruta de seguridad nunca se corta (`exenta=True`): se mide y se registra, pero una alerta no se pierde
por un presupuesto. El consumo de cada nota (pasos, tokens, milisegundos, corte) va a un `Registro` al cerrar.
En la Entrega 1 no hay modelos de lenguaje en el camino: los tokens quedan en cero y el presupuesto de
tokens está listo para el análisis de la Entrega 2.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum

log = logging.getLogger("sistema.control")


class Corte(StrEnum):
    pasos = "pasos"
    tokens = "tokens"
    tiempo = "tiempo"
    repeticion = "repeticion"


class CorteDePresupuesto(Exception):
    def __init__(self, motivo: Corte, detalle: str):
        super().__init__(f"{motivo.value}: {detalle}")
        self.motivo = motivo
        self.detalle = detalle


@dataclass(frozen=True)
class Presupuesto:
    max_pasos: int = 12
    max_tokens: int = 4000
    timeout_s: float = 120.0
    max_repeticiones: int = 3


@dataclass
class Consumo:
    nota_id: str
    carril: str
    pasos: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    ms: int = 0
    corte: str | None = None
    detalle_pasos: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        return asdict(self)


Registro = Callable[[Consumo], None]


class SesionNota:
    def __init__(
        self,
        nota_id: str,
        carril: str,
        presupuesto: Presupuesto,
        registro: Registro | None = None,
        exenta: bool = False,
        reloj: Callable[[], float] = time.monotonic,
    ):
        self.presupuesto = presupuesto
        self.consumo = Consumo(nota_id, carril)
        self._registro = registro
        self._exenta = exenta
        self._reloj = reloj
        self._t0 = reloj()
        self._vistos: dict[str, int] = {}

    def restante_s(self) -> float:
        """Segundos que quedan del timeout global (para acotar los timeouts de cada llamada)."""
        return max(0.0, self.presupuesto.timeout_s - (self._reloj() - self._t0))

    def paso(self, nombre: str, args: object = None, tokens_in: int = 0, tokens_out: int = 0) -> None:
        """Cuenta un paso. Levanta CorteDePresupuesto si se cumple una condición de corte."""
        c = self.consumo
        c.pasos += 1
        c.tokens_in += tokens_in
        c.tokens_out += tokens_out
        c.detalle_pasos.append(nombre)
        clave = nombre + hashlib.sha1(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:8]
        self._vistos[clave] = self._vistos.get(clave, 0) + 1
        corte = self._condicion(nombre)
        if corte is not None:
            c.corte = corte.motivo.value
            if not self._exenta:
                raise corte
            log.warning("nota %s: %s, pero la ruta de seguridad no se corta", c.nota_id, corte)

    def _condicion(self, nombre: str) -> CorteDePresupuesto | None:
        c, p = self.consumo, self.presupuesto
        if c.pasos > p.max_pasos:
            return CorteDePresupuesto(Corte.pasos, f"{c.pasos} pasos > {p.max_pasos} (último: {nombre})")
        if c.tokens_in + c.tokens_out > p.max_tokens:
            return CorteDePresupuesto(Corte.tokens, f"{c.tokens_in + c.tokens_out} tokens > {p.max_tokens}")
        if self.restante_s() <= 0:
            return CorteDePresupuesto(Corte.tiempo, f"más de {p.timeout_s:g} s en la nota (último: {nombre})")
        if max(self._vistos.values()) > p.max_repeticiones:
            return CorteDePresupuesto(Corte.repeticion, f"el paso «{nombre}» se repite en bucle")
        return None

    def cerrar(self) -> Consumo:
        self.consumo.ms = int((self._reloj() - self._t0) * 1000)
        if self._registro is not None:
            try:
                self._registro(self.consumo)
            except Exception as e:  # el registro de consumo nunca rompe la nota
                log.warning("no se pudo registrar el consumo de %s: %s", self.consumo.nota_id, e)
        return self.consumo

    def __enter__(self) -> SesionNota:
        return self

    def __exit__(self, tipo, valor, tb) -> bool:
        if isinstance(valor, CorteDePresupuesto):
            self.consumo.corte = valor.motivo.value
            log.error("nota %s cortada (%s): %s", self.consumo.nota_id, self.consumo.carril, valor)
        self.cerrar()
        return False  # la excepción sigue su camino: quien llama decide qué hacer con la nota cortada


class RegistroConsumo:
    """Acumula el consumo en memoria y lo persiste en la tabla `consumo` si hay base."""

    def __init__(self, base=None):
        self._base = base
        self._lock = threading.Lock()
        self.filas: list[Consumo] = []

    def __call__(self, c: Consumo) -> None:
        with self._lock:
            self.filas.append(c)
        if self._base is not None:
            self._base.registrar_consumo(c.a_dict())

    def totales(self) -> dict:
        with self._lock:
            filas = list(self.filas)
        return {
            "notas": len(filas),
            "pasos": sum(f.pasos for f in filas),
            "tokens_in": sum(f.tokens_in for f in filas),
            "tokens_out": sum(f.tokens_out for f in filas),
            "cortes": sum(1 for f in filas if f.corte),
        }
