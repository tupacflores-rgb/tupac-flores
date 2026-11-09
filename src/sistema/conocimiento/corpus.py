"""Fragmentos del corpus con sus metadatos: equipo, fabricante, documento, sección, cita y vigencia.

La fuente es `fragmentos.jsonl` / `indice.json` (los mismos datos que sirve `/api/documentos`). Además de los
metadatos que trae el corpus se deriva `aplica`: False para lo que no corresponde a PIA-3 (otros modelos de la
familia, opcionales que la planta no tiene, instalación, preservación, plantas PIA-1 y PIA-2). Esa marca no
excluye
el fragmento: lo degrada en el ranking, porque una pregunta puede pedirlo explícitamente.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Claves de sección (campo `clave` de fragmentos.jsonl) que no aplican a las bombas/filtros de PIA-3.
_CLAVES_NO_APLICAN = {
    "limites-otros",
    "limites-otras",
    "otras-plantas",
    "pia1",
    "pia1-arena",
    "pia2",
    "familia",
    "instalacion",
    "preservacion",
    "almac-largo",
    "opcionales",
    "transporte",
    "pintura",
    "conexiones",
    "precios-anteriores",
    "codigos-reemplazados",
}
_PREFIJOS_NO_APLICAN = ("pia1-", "pia2-", "bs-", "conv-")

# Respaldo cuando solo se dispone de fragmentos.jsonl (indice.json trae el dato completo).
_FABRICANTES = {
    "TX350-D": "Trimax Industrial S.A.",
    "TX350-C": "Trimax Industrial S.A.",
    "FC48-3": "Filtrapat",
    "MBP-4": "Operadora",
    "CT-SVP-2025": "Servipat",
    "CERT-EJ": "Servipat",
    "historial": "Operadora",
}


@dataclass(frozen=True)
class Fragmento:
    cita: str  # "TX350-D §3.2" o "historial OT-25-0026"
    documento: str  # "TX350-D"
    seccion: str  # "3.2" ("" para órdenes)
    titulo: str
    texto: str
    vigente: bool
    aplica: bool
    fabricante: str
    equipos: tuple[str, ...]
    pagina: int | None = None

    @property
    def es_orden(self) -> bool:
        return self.documento == "historial"


def _aplica(clave: str) -> bool:
    return not (clave in _CLAVES_NO_APLICAN or clave.startswith(_PREFIJOS_NO_APLICAN))


def cargar(base: Path) -> list[Fragmento]:
    """Carga `fragmentos.jsonl` de la carpeta `datos/documentos` (o equivalente)."""
    base = Path(base)
    equipos_doc: dict[str, tuple[str, ...]] = {}
    indice = base / "indice.json"
    if indice.exists():
        for d in json.loads(indice.read_text(encoding="utf-8")).get("documentos", []):
            equipos_doc[d["id"]] = tuple(d.get("equipos") or ())
    salida: list[Fragmento] = []
    for linea in (base / "fragmentos.jsonl").read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        f = json.loads(linea)
        doc = f["doc"]
        salida.append(
            Fragmento(
                cita=f["id"],
                documento=doc,
                seccion=str(f.get("numero", "")),
                titulo=f.get("titulo", ""),
                texto=f["texto"],
                vigente=bool(f.get("vigente", True)),
                aplica=_aplica(f.get("clave") or ""),
                fabricante=_FABRICANTES.get(doc, ""),
                equipos=equipos_doc.get(doc, ()),
                pagina=f.get("pagina"),
            )
        )
    return salida


def referencia(cita: str) -> tuple[str, str]:
    """Parte una cita en (documento, sección): "TX350-D §3.2" -> ("TX350-D", "3.2")."""
    m = re.match(r"^(.+?)\s*§\s*(\S+)$", cita)
    return (m.group(1), m.group(2)) if m else (cita, "")
