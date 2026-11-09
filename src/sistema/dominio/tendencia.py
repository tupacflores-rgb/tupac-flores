"""Tendencia de un equipo en las últimas horas (B-3): el contexto de un Escalado.

Sale de `GET /api/historian/{tag}/{variable}?desde=&hasta=` (una muestra por minuto). Es contexto, no
decisión: si el historian no responde o tarda, el evento sale igual sin la tendencia.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from ..voz.numeros import formatear
from .catalogo import Catalogo

log = logging.getLogger("sistema.tendencia")

# Qué mirar de cada tipo de equipo: (variable del historian, cómo se nombra, unidad).
VARIABLES = {
    "bomba": [("presion_descarga", "presión de descarga", "kg/cm²"), ("vibracion", "vibración", "mm/s")],
    "filtro": [("presion_diferencial", "presión diferencial", "kg/cm²")],
}


class Tendencia:
    def __init__(
        self,
        historian: Callable[[str, str, str, str], list[dict]],
        catalogo: Callable[[], Catalogo | None],
        horas: int = 3,
    ):
        """`historian(tag, variable, desde_iso, hasta_iso)` devuelve las muestras `{"ts", "valor"}`."""
        self._historian = historian
        self._catalogo = catalogo
        self.horas = horas

    def describir(self, tag: str, ts: datetime) -> str | None:
        cat = self._catalogo()
        if cat is None or tag not in cat.equipos:
            return None
        partes = []
        for variable, nombre, unidad in VARIABLES.get(cat.tipo_de(tag), []):
            try:
                muestras = self._historian(
                    tag, variable, (ts - timedelta(hours=self.horas)).isoformat(), ts.isoformat()
                )
            except Exception as e:  # contexto opcional
                log.warning("sin tendencia de %s/%s: %s", tag, variable, e)
                continue
            valores = [m["valor"] for m in muestras if m.get("valor") is not None]
            if len(valores) < 2:
                continue
            primero, ultimo = valores[0], valores[-1]
            sentido = (
                "sube"
                if ultimo - primero > 0.02 * max(abs(primero), 1e-9)
                else ("baja" if primero - ultimo > 0.02 * max(abs(primero), 1e-9) else "estable")
            )
            partes.append(
                f"{nombre} {sentido} de {formatear(round(primero, 2))} a "
                f"{formatear(round(ultimo, 2))} {unidad} "
                f"(mín {formatear(round(min(valores), 2))}, máx {formatear(round(max(valores), 2))})"
            )
        if not partes:
            return None
        return f"últimas {self.horas} h: " + "; ".join(partes)
