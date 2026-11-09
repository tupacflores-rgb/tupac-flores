"""Catálogo de equipos, a partir de `GET /api/planta`.

Resuelve los nombres informales del operador ("la dos", "el de la izquierda", "la de reserva") a un TAG.
Si el nombre es genérico ("la bomba") la mención queda ambigua: hay que preguntar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..voz.texto import normalizar

# Equivalencias de dígito: Whisper puede escribir "la 2" por "la dos".
_NUMEROS = {"1": "uno", "2": "dos", "3": "tres"}
GENERICOS = {"la bomba": "bomba", "una bomba": "bomba", "bomba": "bomba", "el filtro": "filtro"}


@dataclass(frozen=True)
class Equipo:
    tag: str
    tipo: str
    nombres: tuple[str, ...]
    unidad_manometro: str | None = None


@dataclass(frozen=True)
class Mencion:
    inicio: int
    fin: int
    tag: str | None  # None = genérica, ambigua
    tipo: str  # bomba | filtro | tanque | caudalimetro
    texto: str


@dataclass(frozen=True)
class Limite:
    alta: float | None
    baja: float | None


class Catalogo:
    def __init__(self, definicion: dict):
        self.equipos: dict[str, Equipo] = {}
        for e in definicion["equipos"]:
            self.equipos[e["tag"]] = Equipo(
                e["tag"],
                e["tipo"],
                tuple(e.get("nombres", [])),
                (e.get("manometro_local") or {}).get("unidad"),
            )
        self.limites: dict[tuple[str, str], Limite] = {
            (s["tag"], s["variable"]): Limite(s.get("alarma_alta"), s.get("alarma_baja"))
            for s in definicion.get("sensores", [])
        }
        self._alias: dict[str, str] = {}
        for eq in self.equipos.values():
            for nombre in (*eq.nombres, eq.tag):
                for variante in self._variantes(normalizar(nombre)):
                    self._alias.setdefault(variante, eq.tag)
        # "el filtro a" también se dice "filtro a"; "bomba uno" también "la bomba uno".
        for variante, tag in list(self._alias.items()):
            if variante.startswith("el filtro "):
                self._alias.setdefault(variante.removeprefix("el "), tag)
            if variante.startswith("bomba "):
                self._alias.setdefault("la " + variante, tag)
        genericos = {k: v for k, v in GENERICOS.items() if k not in self._alias}
        todos = sorted([*self._alias, *genericos], key=len, reverse=True)
        self._genericos = genericos
        self._regex = re.compile(r"\b(" + "|".join(re.escape(a) for a in todos) + r")\b")

    @staticmethod
    def _variantes(nombre: str) -> set[str]:
        variantes = {nombre}
        if nombre.endswith(" uno"):
            variantes.add(nombre[:-3] + "una")  # el transcriptor "corrige" la uno -> la una
        for digito, palabra in _NUMEROS.items():
            variantes |= {v.replace(f" {digito}", f" {palabra}") for v in list(variantes)}
            variantes |= {v.replace(f" {palabra}", f" {digito}") for v in list(variantes)}
        return variantes

    def tipo_de(self, tag: str) -> str:
        return self.equipos[tag].tipo

    def menciones(self, texto_normalizado: str) -> list[Mencion]:
        salida = []
        for m in self._regex.finditer(texto_normalizado):
            nombre = m.group(1)
            if nombre in self._alias:
                tag = self._alias[nombre]
                salida.append(Mencion(m.start(), m.end(), tag, self.equipos[tag].tipo, nombre))
            else:
                salida.append(Mencion(m.start(), m.end(), None, self._genericos[nombre], nombre))
        return salida

    def unidad_manometro(self, tag: str) -> str | None:
        return self.equipos[tag].unidad_manometro

    def bombas(self) -> list[str]:
        return [t for t, e in self.equipos.items() if e.tipo == "bomba"]

    def limite(self, tag: str, variable_sensor: str) -> Limite | None:
        return self.limites.get((tag, variable_sensor))
