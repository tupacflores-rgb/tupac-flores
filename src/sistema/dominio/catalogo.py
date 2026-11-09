"""Catálogo de equipos, a partir de `GET /api/planta`.

Resuelve los nombres informales del operador ("la dos", "el de la izquierda", "la de reserva") a un TAG.
Si el nombre es genérico ("la bomba") la mención queda ambigua: hay que preguntar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..voz.texto import distancia_de_edicion, normalizar

# Equivalencias de dígito: Whisper puede escribir "la 2" por "la dos".
_NUMEROS = {"1": "uno", "2": "dos", "3": "tres"}
# Palabras de variable o unidad que, cerca de un nombre mal oído, confirman que se habla de un equipo.
CONTEXTO_DE_EQUIPO = {
    "descarga", "diferencial", "metros", "kilos", "kilogramos", "psi", "bar", "libras", "grados",
    "rodamiento", "empaquetadura", "golpeteo", "parada", "reserva", "mantenimiento", "disparada", "aceite",
    "correas", "pistola", "pierde", "chorrea", "visor",
}  # fmt: skip
VENTANA_DE_CONTEXTO = 10  # palabras que se miran después del nombre
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
        # Para la coincidencia aproximada: solo nombres hablados (sin dígitos ni tags como «b-101»).
        self._naturales = [(a, t) for a, t in self._alias.items() if a.replace(" ", "").isalpha()]

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
        salida.extend(self._difusas(texto_normalizado, salida))
        return sorted(salida, key=lambda m: m.inicio)

    def _difusas(self, norm: str, exactas: list[Mencion]) -> list[Mencion]:
        """Nombres de equipo mal oídos («a uno» por «la uno»): hasta 2 letras de diferencia sobre el nombre
        sin espacios (1 si es corto), con una variable o unidad cerca y sin pisar una mención exacta.

        Si un mismo tramo queda a igual distancia de equipos distintos, no se elige ninguno."""
        palabras = [(m.start(), m.end(), m.group()) for m in re.finditer(r"\S+", norm)]
        candidatas: list[tuple[int, int, int, str, str]] = []
        for alias, tag in self._naturales:
            plano, k = alias.replace(" ", ""), len(alias.split())
            tolerancia = 2 if len(plano) >= 9 else 1
            for i in range(len(palabras)):
                for largo in (k, k - 1, k + 1):
                    if largo < 1 or i + largo > len(palabras):
                        continue
                    ventana = palabras[i : i + largo]
                    s = "".join(w[2] for w in ventana)
                    if s.isdigit() or abs(len(s) - len(plano)) > tolerancia:
                        continue
                    d = distancia_de_edicion(s, plano)
                    if d == 0 or d > tolerancia or not self._identificador_coincide(ventana, alias):
                        continue
                    inicio, fin = ventana[0][0], ventana[-1][1]
                    if any(inicio < e.fin and e.inicio < fin for e in exactas):
                        continue
                    despues = palabras[i + largo : i + largo + VENTANA_DE_CONTEXTO]
                    if any(w[2] in CONTEXTO_DE_EQUIPO for w in despues):
                        candidatas.append((d, inicio, fin, tag, alias))
        elegidas: list[Mencion] = []
        for d, inicio, fin, tag, alias in sorted(candidatas):
            if any(inicio < e.fin and e.inicio < fin for e in elegidas):
                continue
            rivales = {c[3] for c in candidatas if c[0] == d and c[1] == inicio and c[2] == fin}
            if len(rivales) > 1:
                continue
            elegidas.append(Mencion(inicio, fin, tag, self.equipos[tag].tipo, alias))
        return elegidas

    @staticmethod
    def _identificador_coincide(ventana: list[tuple[int, int, str]], alias: str) -> bool:
        """Lo que distingue un equipo de otro (el número o la letra del final) tiene que estar bien dicho: un
        error ahí sería tomar una bomba por otra. El comienzo («la», «el filtro») tolera una letra, y los
        decimales («uno coma tres») nunca son un nombre."""
        palabras_alias = alias.split()
        if any(w[2] in ("coma", "punto") for w in ventana):
            return False
        ultima_alias, ultima = palabras_alias[-1], ventana[-1][2]
        if ultima != ultima_alias and not (
            len(ultima_alias) >= 6 and distancia_de_edicion(ultima, ultima_alias) <= 1
        ):
            return False
        return distancia_de_edicion(ventana[0][2], palabras_alias[0]) <= 1

    def unidad_manometro(self, tag: str) -> str | None:
        return self.equipos[tag].unidad_manometro

    def bombas(self) -> list[str]:
        return [t for t, e in self.equipos.items() if e.tipo == "bomba"]

    def limite(self, tag: str, variable_sensor: str) -> Limite | None:
        return self.limites.get((tag, variable_sensor))
