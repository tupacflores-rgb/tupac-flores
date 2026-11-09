"""Intérprete de lo que contesta el operador en la tablet.

El operador habla en voz y en texto libre ("No. La dos estaba en mil seiscientos cuarenta psi, no en ..."),
pero `RespuestaOperador.texto` ya llega transcripto. Se entienden estas formas:

  confirma        "Sí, confirmo." · "Correcto, dale." · "Perfecto, confirmo."
  elige equipo    "La dos." · "Era la dos." · "Ninguna de esas, era la dos."
  corrige (empieza con "No") y luego una o dos frases:
     "<equipo> estaba en <n> <unidad>, no en <m>."          cambia un valor
     "Eso de <n> era de <equipo>, no de <otro>."            cambia el equipo de una fila
     "Lo de <equipo> es en <unidad>, no en <otra>."         cambia una unidad
     "Te faltó <equipo>, que estaba en <n> <unidad>."       agrega un valor (p. ej. el goteo, que no se dicta)
     "Lo de <equipo> no lo dije."                           quita una fila
     "Y hay más cosas, revisalo todo."                      no dice qué: se vuelve a mostrar el borrador
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..dominio.catalogo import Catalogo
from ..dominio.parte import (
    VARIABLE_DIFERENCIAL,
    VARIABLE_GOTEO,
    VARIABLE_NIVEL,
    VARIABLE_PRESION_BOMBA,
    VARIABLE_TEMPERATURA,
    Fila,
)
from .numeros import numero_antes_de
from .texto import normalizar, oraciones

UNIDAD_DICHA = {
    "kilos": "kg/cm2", "psi": "psi", "bar": "bar", "metros": "m", "grados": "degC",
}  # fmt: skip
CUES_CONFIRMA = ("confirmo", "dale", "correcto", "esta bien", "asi es", "perfecto")


@dataclass
class Cambio:
    tipo: Literal["valor", "equipo", "unidad", "agregar", "quitar"]
    tag: str | None = None
    variable: str | None = None
    valor: float | None = None
    unidad: str | None = None
    desde_tag: str | None = None  # solo para "equipo"


@dataclass
class Interpretacion:
    tipo: Literal["confirma", "corrige", "equipo", "desconocido"]
    cambios: list[Cambio] = field(default_factory=list)
    tag: str | None = None  # respuesta a "¿cuál bomba?"
    revisar_todo: bool = False


def interpretar(texto: str, catalogo: Catalogo) -> Interpretacion:
    norm = normalizar(texto)
    if re.match(r"^no\b", norm):
        return _corrige(texto, catalogo)
    if any(c in norm for c in CUES_CONFIRMA):
        return Interpretacion("confirma")
    tag = _ultimo_tag(norm, catalogo)
    if tag is not None:
        return Interpretacion("equipo", tag=tag)
    return Interpretacion("desconocido")


def _ultimo_tag(norm: str, catalogo: Catalogo) -> str | None:
    tags = [m.tag for m in catalogo.menciones(norm) if m.tag is not None]
    return tags[-1] if tags else None


def _unidad_y_numero(fragmento: str) -> tuple[float | None, str | None, str | None]:
    """'mil seiscientos cuarenta psi' -> (1640, 'psi', 'psi'); '45 gotas por minuto' -> (45, ..)."""
    tokens = fragmento.split()
    dicha = unidad = None
    if tokens[-3:] == ["gotas", "por", "minuto"]:
        dicha, unidad, tokens = "gotas por minuto", "gotas/min", tokens[:-3]
    elif tokens and tokens[-1] in UNIDAD_DICHA:
        dicha, unidad, tokens = tokens[-1], UNIDAD_DICHA[tokens[-1]], tokens[:-1]
    n = numero_antes_de(tokens, decimal=True)
    return (n.valor if n else None), unidad, dicha


def _variable(tag: str, dicha: str | None, catalogo: Catalogo) -> str | None:
    tipo = catalogo.tipo_de(tag)
    if tipo == "filtro":
        return VARIABLE_DIFERENCIAL
    if tipo == "tanque":
        return VARIABLE_NIVEL
    if tipo == "bomba":
        return {
            "kilos": VARIABLE_PRESION_BOMBA,
            "psi": VARIABLE_PRESION_BOMBA,
            "bar": VARIABLE_PRESION_BOMBA,
            "grados": VARIABLE_TEMPERATURA,
            "gotas por minuto": VARIABLE_GOTEO,
        }.get(dicha or "")
    return None


def _tag_de(fragmento: str, catalogo: Catalogo) -> str | None:
    ms = [m.tag for m in catalogo.menciones(fragmento) if m.tag is not None]
    return ms[0] if ms else None


def _corrige(texto: str, catalogo: Catalogo) -> Interpretacion:
    out = Interpretacion("corrige")
    for frase in oraciones(texto):
        if re.fullmatch(r"no", frase):
            continue
        frase = re.sub(r"^no ", "", frase)
        if "revisalo todo" in frase or "mas cosas" in frase:
            out.revisar_todo = True
            continue
        if m := re.fullmatch(r"(?P<n>.+?) estaba en (?P<a>.+?) no en (?P<b>.+)", frase):
            tag = _tag_de(m["n"], catalogo)
            valor, unidad, dicha = _unidad_y_numero(m["a"])
            variable = _variable(tag, dicha, catalogo) if tag else None
            if tag and variable and valor is not None:
                out.cambios.append(Cambio("valor", tag, variable, valor, unidad))
        elif m := re.fullmatch(r"eso de (?P<a>.+?) era de (?P<n1>.+?) no de (?P<n2>.+)", frase):
            valor, _, _ = _unidad_y_numero(m["a"])
            nuevo, viejo = _tag_de(m["n1"], catalogo), _tag_de(m["n2"], catalogo)
            if nuevo and valor is not None:
                out.cambios.append(Cambio("equipo", nuevo, None, valor, None, desde_tag=viejo))
        elif m := re.fullmatch(r"lo de (?P<n>.+?) es en (?P<u1>.+?) no en (?P<u2>.+)", frase):
            tag, unidad = _tag_de(m["n"], catalogo), UNIDAD_DICHA.get(m["u1"].strip())
            if tag and unidad:
                out.cambios.append(Cambio("unidad", tag, None, None, unidad))
        elif m := re.fullmatch(r"te falto (?P<n>.+?) que estaba en (?P<a>.+)", frase):
            tag = _tag_de(m["n"], catalogo)
            valor, unidad, dicha = _unidad_y_numero(m["a"])
            variable = _variable(tag, dicha, catalogo) if tag else None
            if tag and variable and valor is not None:
                out.cambios.append(Cambio("agregar", tag, variable, valor, unidad))
        elif m := re.fullmatch(r"lo de (?P<n>.+?) no lo dije", frase):
            tag = _tag_de(m["n"], catalogo)
            if tag:
                out.cambios.append(Cambio("quitar", tag))
    return out


def _cerca(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.051, abs(b) * 0.015)


def aplicar(filas: list[Fila], cambios: list[Cambio], catalogo: Catalogo) -> list[Fila]:
    """Devuelve las filas con las correcciones aplicadas (no modifica la lista original)."""
    filas = [Fila(**vars(f)) for f in filas]
    for c in cambios:
        if c.tipo in {"valor", "agregar"}:
            fila = next((f for f in filas if f.tag == c.tag and f.variable == c.variable), None)
            if fila is None:
                fila = Fila(c.tag, c.variable)
                filas.append(fila)
            fila.valor, fila.provisional = c.valor, False
            if c.unidad:
                fila.unidad = c.unidad
        elif c.tipo == "equipo":
            fila = next(
                (
                    f
                    for f in filas
                    if f.valor is not None and _cerca(f.valor, c.valor) and f.tag == c.desde_tag
                ),
                None,
            )
            fila = fila or next((f for f in filas if f.valor is not None and _cerca(f.valor, c.valor)), None)
            if fila is not None:
                fila.tag = c.tag
        elif c.tipo == "unidad":
            for f in filas:
                if f.tag == c.tag and f.valor is not None:
                    f.unidad = c.unidad
        elif c.tipo == "quitar":
            valuadas = [f for f in filas if f.tag == c.tag and f.valor is not None]
            if valuadas:
                filas.remove(valuadas[-1])
    return filas
