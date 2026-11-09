"""Respaldo con modelo de lenguaje local (Ollama) para lo que el parser y el intérprete no entienden.

Reglas que no se negocian:
  - Es **solo respaldo**: se usa cuando el parser no sacó filas de una nota que menciona equipos o
    variables, para los tramos que no interpretó, y para respuestas del operador que el intérprete no
    entiende. Si el servicio no está, tarda o se pasa del presupuesto, la nota sigue por el camino
    determinista.
  - El modelo **nunca confirma un parte ni decide reglas**: propone filas (`FilaParte` por structured
    output) y pasan por las mismas validaciones deterministas que el parser: el equipo existe, la
    variable corresponde a su tipo, la unidad es la del manómetro si no se dijo otra, y cada valor tiene
    que aparecer en lo que se dijo (un número que el modelo inventó se descarta).
  - Cada llamada pasa por el callback de consumo (pasos, tokens, tiempo) como cualquier paso de la nota.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from planta_contrato import FilaParte
from pydantic import BaseModel, ConfigDict, Field

from .control import Presupuesto, SesionNota
from .dominio.catalogo import Catalogo
from .dominio.parte import (
    VARIABLE_DIFERENCIAL,
    VARIABLE_ESTADO,
    VARIABLE_GOTEO,
    VARIABLE_NIVEL,
    VARIABLE_PRESION_BOMBA,
    VARIABLE_TEMPERATURA,
    Fila,
)
from .flujo import CallbackConsumo
from .voz.numeros import leer
from .voz.texto import normalizar

log = logging.getLogger("sistema.respaldo")

# Qué variables admite cada tipo de equipo y en qué unidades (las del parser).
VARIABLES = {
    "bomba": {
        VARIABLE_PRESION_BOMBA,
        VARIABLE_TEMPERATURA,
        VARIABLE_GOTEO,
        VARIABLE_ESTADO,
        "ruido",
        "nivel_aceite_visor",
        "estado_correas",
    },  # fmt: skip
    "filtro": {VARIABLE_DIFERENCIAL},
    "tanque": {VARIABLE_NIVEL},
}
UNIDADES = {
    VARIABLE_PRESION_BOMBA: {"kg/cm2", "psi", "bar"},
    VARIABLE_DIFERENCIAL: {"kg/cm2"},
    VARIABLE_TEMPERATURA: {"degC"},
    VARIABLE_NIVEL: {"m"},
    VARIABLE_GOTEO: {"gotas/min"},
}
PALABRA_DE_UNIDAD = {"kg/cm2": ("kilo", "kg"), "psi": ("psi", "libra"), "bar": ("bar",)}


class ParteLLM(BaseModel):
    """Lo que el modelo devuelve: filas del parte, tal cual las define el contrato."""

    model_config = ConfigDict(extra="forbid")
    filas: list[FilaParte] = Field(default_factory=list)


@dataclass
class Resultado:
    filas: list[Fila]
    descartadas: int  # filas del modelo que no pasaron la validación determinista


def prompt_sistema(cat: Catalogo) -> str:
    equipos = "\n".join(f"- {e.tag} ({e.tipo}): {', '.join(e.nombres)}" for e in cat.equipos.values())
    return (
        "Sos el asistente de una planta de inyección de agua. Recibís la transcripción de una nota de voz "
        "de recorrida, con errores de reconocimiento (palabras pegadas o cambiadas). Devolvé las filas "
        "del parte, una por equipo y variable. Usá solo lo que la nota dice; no inventes equipos, "
        "variables ni números.\n"
        f"Equipos (tag: nombres informales):\n{equipos}\n"
        "Variables: presion_descarga_local (bombas; unidad kg/cm2, psi o bar), "
        "temperatura_rodamiento_ir (degC), "
        "presion_diferencial (filtros; kg/cm2), nivel (tanque; m). Observaciones sin número "
        "(valor null): goteo_empaquetadura, estado (p. ej. 'parada, en reserva'), ruido, nivel_aceite_visor, "
        "estado_correas.\n"
        "«kilos» son kg/cm2. Si no se dijo la unidad de una presión de bomba, dejá la unidad en null. "
        "Si no hay nada que reportar, devolvé una lista vacía."
    )


class RespaldoLLM:
    def __init__(
        self,
        url: str,
        modelo: str,
        timeout_s: float = 45.0,
        max_tokens: int = 1500,
        registro=None,
        chat=None,
    ):
        self.url, self.modelo, self.timeout_s = url, modelo, timeout_s
        self._presupuesto = Presupuesto(max_pasos=4, max_tokens=max_tokens, timeout_s=timeout_s)
        self._registro = registro
        self._chat = chat  # inyectable en los tests

    # -- modelo ------------------------------------------------------------------------------------------

    def _modelo(self):
        if self._chat is not None:
            return self._chat
        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            model=self.modelo,
            base_url=self.url,
            temperature=0,
            num_ctx=2048,
            num_thread=1,
            num_predict=400,
            reasoning=False,  # think=false
            client_kwargs={"timeout": self.timeout_s},
        )
        return llm.with_structured_output(ParteLLM, method="json_schema")

    def _invocar(self, mensajes: list, nota_id: str) -> ParteLLM | None:
        """Una llamada con presupuesto (pasos, tokens, tiempo). Cualquier falla devuelve None: el camino
        determinista sigue."""
        sesion = SesionNota(nota_id, "llm", self._presupuesto, self._registro)
        try:
            salida = self._modelo().invoke(mensajes, config={"callbacks": [CallbackConsumo(sesion)]})
        except Exception as e:  # sin servicio, timeout, JSON inválido o presupuesto: no se frena la nota
            log.warning("respaldo LLM no disponible para %s: %s", nota_id, e)
            sesion.cerrar()
            return None
        sesion.cerrar()
        if isinstance(salida, dict):
            salida = ParteLLM.model_validate(salida)
        return salida

    # -- usos --------------------------------------------------------------------------------------------

    def extraer(self, texto: str, cat: Catalogo, nota_id: str = "?") -> Resultado | None:
        """Filas de un texto (o de un tramo) que el parser no interpretó."""
        salida = self._invocar(
            [("system", prompt_sistema(cat)), ("human", f"Transcripción: {texto}")], nota_id
        )
        if salida is None:
            return None
        return validar(salida.filas, texto, cat)

    def corregir(
        self, respuesta: str, filas: list[Fila], cat: Catalogo, nota_id: str = "?"
    ) -> Resultado | None:
        """Respuesta del operador que el intérprete no entendió: devuelve el parte completo corregido."""
        actual = "; ".join(_describir(f) for f in filas if f.tag)
        salida = self._invocar(
            [
                ("system", prompt_sistema(cat)),
                (
                    "human",
                    f"Parte actual: {actual}\nEl operador respondió: «{respuesta}»\n"
                    "Devolvé el parte completo con lo que el operador corrige. Si solo confirma o no corrige "
                    "nada, devolvé el parte actual sin cambios.",
                ),
            ],
            nota_id,
        )
        if salida is None:
            return None
        return validar(salida.filas, f"{respuesta} " + actual, cat)


def _describir(f: Fila) -> str:
    dato = f.valor if f.valor is not None else (f.observacion or "")
    return f"{f.tag} {f.variable} {dato} {f.unidad or ''}".strip()


# -- validación determinista --------------------------------------------------------------------------


def numeros_dichos(texto: str) -> set[float]:
    """Todos los números que aparecen en el texto, en cifras o en palabras."""
    tokens = normalizar(texto).split()
    valores: set[float] = set()
    for i in range(len(tokens)):
        for j in range(max(0, i - 6), i + 1):
            for decimal in (False, True):
                n = leer(tokens[j : i + 1], decimal)
                if n is not None:
                    valores.add(round(n.valor, 4))
                    if n.entero_dicho:
                        valores.add(round(n.valor / 10, 4))  # «dieciocho» puede ser 1,8
    return valores


def validar(filas: list[FilaParte], texto: str, cat: Catalogo) -> Resultado:
    norm = normalizar(texto)
    dichos = numeros_dichos(texto)
    salida: list[Fila] = []
    vistas: set[tuple[str, str]] = set()
    descartadas = 0
    for f in filas:
        if f.tag not in cat.equipos or f.variable not in VARIABLES.get(cat.tipo_de(f.tag), set()):
            descartadas += 1
            continue
        if (f.tag, f.variable) in vistas:
            descartadas += 1
            continue
        valor, unidad = f.valor, f.unidad
        if valor is not None:
            if not any(abs(valor - d) <= 1e-6 + abs(d) * 1e-6 for d in dichos):
                descartadas += 1  # un número que no se dijo: el modelo lo inventó
                continue
            permitidas = UNIDADES.get(f.variable, set())
            if f.variable == VARIABLE_PRESION_BOMBA:
                # La unidad solo vale si se la nombró en la nota; si no, la del manómetro del equipo.
                nombrada = unidad in permitidas and any(p in norm for p in PALABRA_DE_UNIDAD.get(unidad, ()))
                unidad = unidad if nombrada else cat.unidad_manometro(f.tag)
            elif unidad not in permitidas:
                unidad = next(iter(permitidas), None)
        else:
            unidad = None
            if not f.observacion:
                descartadas += 1
                continue
        vistas.add((f.tag, f.variable))
        salida.append(Fila(f.tag, f.variable, valor, unidad, f.observacion))
    return Resultado(salida, descartadas)


_VARIABLES_CLAVE = re.compile(
    r"\b(descarga|diferencial|grados|metros|empaquetadura|rodamiento|golpe\w*|parad[ao]|reserva|mantenimiento|"
    r"aceite|correas|kilos|kilogramos|psi|libras|bar|disparad[ao])\b"
)


def menciona_algo(texto: str, cat: Catalogo) -> bool:
    """La nota habla de algún equipo o variable: si el parser no sacó filas, hay que usar el respaldo."""
    norm = normalizar(texto)
    return bool(cat.menciones(norm)) or _VARIABLES_CLAVE.search(norm) is not None
