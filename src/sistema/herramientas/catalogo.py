"""Las siete tools de solo lectura (Anexo 1.3): equipos, sensores, órdenes, inventario, tareas, manuales y códigos de falla.

Qué sale de dónde (ver docs/rag.md):
- Límites de alarma y estado de los equipos -> API de la planta (`/api/planta`), nunca del RAG.
- Stock, lista de materiales y catálogo de tareas -> `materiales.yaml` (datos estructurados), nunca del RAG.
- Historial de sensores y de órdenes -> API de la planta.
- Procedimientos, causas probables y recomendaciones del fabricante -> manuales (RAG), con cita.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator

from ..conocimiento.fallas import TablaFallas
from ..conocimiento.recuperador import Recuperador
from ..integraciones.planta_api import PlantaApi
from .base import Entrada, ErrorDeDatos, Herramienta

TAG = Field(pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$", description="TAG del equipo, p. ej. B-101")


def cargar_materiales(ruta: Path | None = None) -> dict:
    ruta = ruta or Path(__file__).resolve().parents[1] / "datos" / "materiales.yaml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


# --- equipos -----------------------------------------------------------------------------------------------


class EntradaEquipos(Entrada):
    tag: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$")
    tipo: Literal["bomba", "filtro", "tanque", "caudalimetro"] | None = None


class Equipos(Herramienta):
    nombre = "equipos"
    descripcion = "Equipos con nombres informales, sensores y límites de alarma (fuente: API de la planta)."
    entrada = EntradaEquipos

    def __init__(self, planta: PlantaApi):
        self._planta = planta

    def ejecutar(self, e: EntradaEquipos):
        d = self._planta.planta()
        equipos = [
            q
            for q in d["equipos"]
            if (not e.tag or q["tag"] == e.tag) and (not e.tipo or q["tipo"] == e.tipo)
        ]
        if e.tag and not equipos:
            raise ErrorDeDatos(f"no existe el equipo {e.tag}")
        sensores = {}
        for s in d.get("sensores", []):
            sensores.setdefault(s["tag"], []).append(s)
        return [{**q, "sensores": sensores.get(q["tag"], [])} for q in equipos]


# --- historial de sensores ---------------------------------------------------------------------------------


class EntradaHistorialSensores(Entrada):
    tag: str = TAG
    variable: str = Field(pattern=r"^[a-z_]{3,40}$", description="p. ej. presion_descarga, vibracion")
    desde: str | None = Field(default=None, description="ISO 8601")
    hasta: str | None = None
    ultimas: int = Field(
        default=60, ge=1, le=1440, description="cantidad máxima de muestras (las más recientes)"
    )


class HistorialSensores(Herramienta):
    nombre = "historial_sensores"
    descripcion = "Muestras de un sensor (una por minuto) con mínimo, máximo y promedio del tramo."
    entrada = EntradaHistorialSensores
    timeout_s = 8.0

    def __init__(self, planta: PlantaApi):
        self._planta = planta

    def ejecutar(self, e: EntradaHistorialSensores):
        try:
            filas = self._planta.historian(e.tag, e.variable, e.desde, e.hasta)
        except Exception as ex:  # 404 del historian = sensor inexistente, no es transitorio
            resp = getattr(ex, "response", None)
            if resp is not None and resp.status_code in (404, 422):
                raise ErrorDeDatos(f"no hay historial de {e.tag}/{e.variable}") from ex
            raise
        filas = filas[-e.ultimas :]
        if not filas:
            raise ErrorDeDatos(f"sin muestras de {e.tag}/{e.variable} en el rango pedido")
        valores = [f["valor"] for f in filas if f.get("valor") is not None]
        return {
            "tag": e.tag,
            "variable": e.variable,
            "n": len(filas),
            "muestras": filas,
            "min": min(valores) if valores else None,
            "max": max(valores) if valores else None,
            "promedio": round(sum(valores) / len(valores), 3) if valores else None,
        }


# --- historial de órdenes ----------------------------------------------------------------------------------


class EntradaHistorialOrdenes(Entrada):
    equipo: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$")
    desde: str | None = None
    hasta: str | None = None
    limite: int = Field(default=20, ge=1, le=200)


class HistorialOrdenes(Herramienta):
    nombre = "historial_ordenes"
    descripcion = "Órdenes de trabajo anteriores (texto libre de campo), las más recientes primero."
    entrada = EntradaHistorialOrdenes
    timeout_s = 8.0

    def __init__(self, planta: PlantaApi):
        self._planta = planta

    def ejecutar(self, e: EntradaHistorialOrdenes):
        ordenes = self._planta.historial_ordenes(e.equipo, e.desde, e.hasta)
        ordenes = sorted(ordenes, key=lambda o: o.get("fecha_aviso", ""), reverse=True)[: e.limite]
        return {"n": len(ordenes), "ordenes": ordenes}


# --- inventario, stock y lista de materiales ---------------------------------------------------------------


class EntradaInventario(Entrada):
    codigo: str | None = Field(default=None, pattern=r"^[A-Z0-9-]{3,20}$")
    equipo: str | None = Field(
        default=None, pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$", description="devuelve su lista de materiales"
    )
    almacen: Literal["yacimiento", "central"] | None = None


class Inventario(Herramienta):
    nombre = "inventario"
    descripcion = "Repuestos con costo y stock por almacén; con `equipo`, su lista de materiales."
    entrada = EntradaInventario

    def __init__(self, materiales: dict):
        self._rep = {r["codigo"]: r for r in materiales["repuestos"]}
        self._bom = materiales["lista_materiales"]

    def ejecutar(self, e: EntradaInventario):
        if e.codigo:
            if e.codigo not in self._rep:
                raise ErrorDeDatos(f"no existe el repuesto {e.codigo}")
            codigos = [e.codigo]
        elif e.equipo:
            if e.equipo not in self._bom:
                raise ErrorDeDatos(f"no hay lista de materiales para {e.equipo}")
            codigos = list(self._bom[e.equipo])
        else:
            codigos = list(self._rep)
        filas = []
        for c in codigos:
            r = self._rep[c]
            stock = r["stock"] if not e.almacen else {e.almacen: r["stock"][e.almacen]}
            filas.append(
                {
                    "codigo": c,
                    "nombre": r["nombre"],
                    "unidad": r["unidad"],
                    "costo_usd": r["costo_usd"],
                    "stock": stock,
                }
            )
        return {"repuestos": filas}


# --- catálogo de tareas ------------------------------------------------------------------------------------


class EntradaCatalogoTareas(Entrada):
    codigo: str | None = Field(default=None, pattern=r"^[A-Z]-[A-Z0-9-]{2,8}$")
    equipo: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$")


class CatalogoTareas(Herramienta):
    nombre = "catalogo_tareas"
    descripcion = (
        "Tareas estándar (con horas, repuestos y si detienen el equipo) y no estándar "
        "(siempre requieren aprobación del planificador)."
    )
    entrada = EntradaCatalogoTareas

    def __init__(self, materiales: dict):
        self._std = materiales["tareas_estandar"]
        self._no_std = materiales["tareas_no_estandar"]

    def ejecutar(self, e: EntradaCatalogoTareas):
        std = [
            t
            for t in self._std
            if (not e.codigo or t["codigo"] == e.codigo) and (not e.equipo or e.equipo in t["equipos"])
        ]
        no_std = [t for t in self._no_std if not e.codigo or t["codigo"] == e.codigo]
        if e.codigo and not std and not no_std:
            raise ErrorDeDatos(f"no existe la tarea {e.codigo}")
        return {"estandar": std, "no_estandar": [] if e.equipo else no_std}


# --- búsqueda en manuales ----------------------------------------------------------------------------------


class EntradaBuscarManuales(Entrada):
    pregunta: str = Field(min_length=3, max_length=300)
    k: int = Field(default=5, ge=1, le=10)
    equipo: str | None = Field(default=None, pattern=r"^[A-Z]{1,3}-\d{3}[A-Z]?$")
    incluir_vencidos: bool = Field(default=False, description="incluye revisiones reemplazadas (TX350-C)")


class BuscarManuales(Herramienta):
    nombre = "buscar_manuales"
    descripcion = (
        "Busca en los manuales, el MBP-4, el contrato y las órdenes pasadas. Cada resultado trae su cita "
        "(TX350-D §3.2) y si la revisión está vigente. No usar para límites de alarma ni stock."
    )
    entrada = EntradaBuscarManuales

    def __init__(self, recuperador: Recuperador):
        self._r = recuperador

    def ejecutar(self, e: EntradaBuscarManuales):
        res = self._r.buscar(e.pregunta, e.k, equipo=e.equipo, incluir_vencidos=e.incluir_vencidos)
        return {
            "resultados": [
                {
                    "cita": r.cita,
                    "titulo": r.fragmento.titulo,
                    "vigente": r.fragmento.vigente,
                    "aplica": r.fragmento.aplica,
                    "puntaje": r.puntaje,
                    "texto": r.fragmento.texto[:1200],
                }
                for r in res
            ]
        }


# --- traducción de códigos de falla ------------------------------------------------------------------------


class EntradaTraducirCodigo(Entrada):
    codigo: str = Field(
        pattern=r"^(F\d{2}|[A-Z]{2}-T\d{2})$",
        description="código del catálogo de la planta (F01–F10) o del fabricante (TX-T07, FC-T02…)",
    )

    @field_validator("codigo", mode="before")
    @classmethod
    def _normalizar(cls, v):
        if isinstance(v, str):  # «tx t07», «tx-t07», «f 03» se aceptan como se dicen en campo
            v = re.sub(r"[\s\u2010-\u2015]+", "-", v.strip().upper())
            v = re.sub(r"^([A-Z]{2})-?(T\d{2})$", r"\1-\2", v)
            v = re.sub(r"^F-(\d{2})$", r"F\1", v)
        return v


class TraducirCodigo(Herramienta):
    nombre = "traducir_codigo"
    descripcion = (
        "Traduce un código de falla entre el catálogo de la planta (F01–F10) y los códigos de los fabricantes "
        "(TX-T07, FC-T02…), con la tarea, el ejecutor y las citas (MBP-4 §6.1 y el manual). La tabla sale del corpus."
    )
    entrada = EntradaTraducirCodigo

    def __init__(self, tabla: TablaFallas):
        self._tabla = tabla

    def ejecutar(self, e: EntradaTraducirCodigo):
        r = self._tabla.traducir(e.codigo)
        if r is None:
            raise ErrorDeDatos(f"el código {e.codigo} no figura en MBP-4 §6.1 ni en los manuales")
        return r


def construir(
    planta: PlantaApi, recuperador: Recuperador, materiales: dict | None = None
) -> dict[str, Herramienta]:
    m = materiales or cargar_materiales()
    lexico = getattr(recuperador, "lexico", recuperador)  # el híbrido envuelve al léxico
    tabla = TablaFallas.desde_fragmentos(lexico.fragmentos)
    todas = [
        Equipos(planta),
        HistorialSensores(planta),
        HistorialOrdenes(planta),
        Inventario(m),
        CatalogoTareas(m),
        BuscarManuales(recuperador),
        TraducirCodigo(tabla),
    ]
    return {t.nombre: t for t in todas}
