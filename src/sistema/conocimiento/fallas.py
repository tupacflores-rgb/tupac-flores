"""Tabla de fallas F01–F10 ↔ códigos de los fabricantes, **extraída del corpus** (MBP-4 §6.1), no escrita a mano.

Los manuales de los fabricantes usan sus propios códigos (`TX-T07`, `FC-T02`); el catálogo de la planta usa F01–F10. La
correspondencia está en la tabla de `MBP-4 §6.1` (`Código | Falla | Fabricante | Tarea | Ejecutor`). Este módulo la lee del
corpus indexado cada vez que se carga: si el manual cambia de revisión, la tabla cambia con él.

Cada traducción trae sus citas: la tabla (`MBP-4 §6.1`), el detalle de la falla (`MBP-4 §6.x`) y la sección del manual del
fabricante que describe ese código (`TX350-D §8.5`), solo de revisiones vigentes (la Rev. C del manual de la bomba no cuenta).
"""

# ruff: noqa: E501
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import Fragmento

DOCUMENTO = "MBP-4"
FILA = re.compile(r"^(F\d{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*$")
CODIGO_FABRICANTE = re.compile(r"^[A-Z]{2}-T\d{2}$")


@dataclass(frozen=True)
class Falla:
    codigo: str  # F01
    nombre: str
    codigos_fabricante: tuple[str, ...]  # ("TX-T08", "FC-T04")
    tarea: str
    ejecutor: str
    citas: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class CodigoManual:
    """Un código que aparece como título de sección en un manual de fabricante (vigente)."""

    codigo: str
    titulo: str
    cita: str


class TablaFallas:
    def __init__(self, fallas: list[Falla], manuales: list[CodigoManual] | None = None):
        self.manuales = {c.codigo: c for c in (manuales or [])}
        self.fallas = {f.codigo: f for f in fallas}
        self._por_fabricante: dict[str, str] = {}
        for f in fallas:
            for c in f.codigos_fabricante:
                self._por_fabricante[c] = f.codigo

    @classmethod
    def desde_fragmentos(cls, fragmentos: list[Fragmento]) -> TablaFallas:
        vigentes = [f for f in fragmentos if f.vigente]
        manuales = [
            CodigoManual(m.group(1), m.group(2).strip(), f.cita)
            for f in vigentes
            if f.documento != DOCUMENTO and (m := re.match(r"^([A-Z]{2}-T\d{2})\s+(.+)$", f.titulo))
        ]
        tabla = next(
            (f for f in vigentes if f.documento == DOCUMENTO and f.titulo.startswith("Resumen del catálogo")),
            None,
        )
        if tabla is None:
            return cls([], manuales)
        por_codigo = {
            m.group(1): f
            for f in vigentes
            if f.documento == DOCUMENTO and (m := re.match(r"^(F\d{2})\b", f.titulo))
        }  # el detalle de cada falla: «6.3 F01 Pérdida por empaquetadura»
        fallas = []
        for linea in tabla.texto.splitlines():
            m = FILA.match(linea.strip())
            if not m:
                continue
            codigo, nombre, fabricante, tarea, ejecutor = m.groups()
            codigos = tuple(c.strip() for c in fabricante.split("/") if CODIGO_FABRICANTE.match(c.strip()))
            citas = [tabla.cita]
            if codigo in por_codigo:
                citas.append(por_codigo[codigo].cita)
            for c in codigos:  # la sección del manual del fabricante que lleva ese código en el título
                citas += [
                    f.cita
                    for f in vigentes
                    if f.documento != DOCUMENTO and f.titulo.startswith(c) and f.cita not in citas
                ]
            fallas.append(Falla(codigo, nombre, codigos, tarea, ejecutor, tuple(citas)))
        return cls(fallas, manuales)

    def traducir(self, codigo: str) -> dict | None:
        """La traducción de un código (F01–F10 o del fabricante), con sus citas. None si no existe en ningún lado.

        Un código del fabricante que está en su manual pero **no** en la tabla de MBP-4 §6.1 (p. ej. TX-T09) vuelve
        sin correspondencia, con la cita del manual: no se inventa una."""
        if codigo in self.fallas:
            return self._resultado(self.fallas[codigo], "catalogo", codigo)
        if codigo in self._por_fabricante:
            return self._resultado(self.fallas[self._por_fabricante[codigo]], "fabricante", codigo)
        if codigo in self.manuales:
            m = self.manuales[codigo]
            return {
                "codigo": codigo, "origen": "fabricante", "codigo_catalogo": None, "falla": None,
                "titulo_en_manual": m.titulo, "codigos_fabricante": [], "tarea": None, "ejecutor": None,
                "citas": [m.cita],
                "nota": f"{codigo} figura en el manual del fabricante pero no tiene correspondencia F01–F10 en MBP-4 §6.1",
            }  # fmt: skip
        return None

    def _resultado(self, f: Falla, origen: str, pedido: str) -> dict:
        manual = self.manuales.get(pedido)
        return {
            "codigo": pedido, "origen": origen, "codigo_catalogo": f.codigo, "falla": f.nombre,
            "titulo_en_manual": manual.titulo if manual else None,
            "codigos_fabricante": list(f.codigos_fabricante), "tarea": f.tarea, "ejecutor": f.ejecutor,
            "citas": list(f.citas), "nota": None,
        }  # fmt: skip

    def __len__(self) -> int:
        return len(self.fallas)
