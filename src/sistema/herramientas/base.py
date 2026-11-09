"""Base de las tools: entrada y salida con Pydantic estricto, timeout y reintentos.

Una tool nunca levanta excepciones hacia quien la llama: devuelve `ResultadoTool` con `ok=False` y un `error`
legible. Solo se reintenta lo transitorio (red, timeout); un error de validación o de datos no se reintenta.
Todas son de solo lectura.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import time
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger("sistema.tools")


class Entrada(BaseModel):
    """Base de las entradas: ningún campo de más (extra="forbid") y sin coerción de tipos."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResultadoTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    datos: Any = None
    error: str | None = None
    intentos: int = 1
    ms: int = 0


class ErrorDeDatos(Exception):
    """Pedido válido pero sin respuesta posible (equipo inexistente, etc.). No se reintenta."""


_TRANSITORIOS = (httpx.TransportError, httpx.HTTPStatusError, TimeoutError)


class Herramienta:
    nombre: ClassVar[str]
    descripcion: ClassVar[str]
    entrada: ClassVar[type[Entrada]]
    timeout_s: ClassVar[float] = 5.0
    reintentos: ClassVar[int] = 2
    espera_s: ClassVar[float] = 0.2

    def ejecutar(self, entrada: Entrada) -> Any:  # pragma: no cover - lo implementa cada tool
        raise NotImplementedError

    def __call__(self, **kwargs: Any) -> ResultadoTool:
        t0 = time.monotonic()
        try:
            entrada = self.entrada.model_validate(kwargs)
        except ValidationError as e:
            return ResultadoTool(
                tool=self.nombre,
                ok=False,
                error=f"entrada inválida: {e.errors()[0]['msg']} "
                f"({'.'.join(str(x) for x in e.errors()[0]['loc'])})",
                intentos=0,
            )
        intentos, ultimo = 0, "sin intentos"
        for intentos in range(1, self.reintentos + 2):
            try:
                datos = self._con_timeout(self.ejecutar, entrada)
                return ResultadoTool(
                    tool=self.nombre,
                    ok=True,
                    datos=datos,
                    intentos=intentos,
                    ms=int((time.monotonic() - t0) * 1000),
                )
            except ErrorDeDatos as e:
                return ResultadoTool(
                    tool=self.nombre,
                    ok=False,
                    error=str(e),
                    intentos=intentos,
                    ms=int((time.monotonic() - t0) * 1000),
                )
            except _TRANSITORIOS as e:
                ultimo = f"{type(e).__name__}: {e}"
                log.warning("tool %s intento %d falló: %s", self.nombre, intentos, ultimo)
                if intentos <= self.reintentos:
                    time.sleep(self.espera_s)
            except Exception as e:  # noqa: BLE001 - una tool no debe tumbar al llamador
                log.exception("tool %s falló", self.nombre)
                return ResultadoTool(
                    tool=self.nombre,
                    ok=False,
                    error=f"{type(e).__name__}: {e}",
                    intentos=intentos,
                    ms=int((time.monotonic() - t0) * 1000),
                )
        return ResultadoTool(
            tool=self.nombre,
            ok=False,
            error=f"agotó {intentos} intentos: {ultimo}",
            intentos=intentos,
            ms=int((time.monotonic() - t0) * 1000),
        )

    def _con_timeout(self, fn: Callable[[Entrada], Any], entrada: Entrada) -> Any:
        # Sin `with`: al salir del contexto el pool esperaría al hilo y el timeout no cortaría nada.
        pool = cf.ThreadPoolExecutor(max_workers=1)
        futuro = pool.submit(fn, entrada)
        try:
            return futuro.result(timeout=self.timeout_s)
        except cf.TimeoutError as e:
            raise TimeoutError(f"superó {self.timeout_s:g} s") from e
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
