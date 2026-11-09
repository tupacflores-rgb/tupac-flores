"""De la transcripción de una nota a las filas del parte.

Las notas del operador siguen una gramática conocida (una o varias frases, cada una sobre un equipo); el
parser es determinista y no usa LLM. Cada mención de un equipo abre un tramo de texto hasta la siguiente
mención; dentro del tramo se buscan las magnitudes y observaciones de ese tipo de equipo.

Lo que no se puede resolver solo se marca, no se inventa:
  - "la bomba" sin decir cuál  -> filas con tag=None (se pregunta con opciones)
  - "dieciocho de diferencial"  -> candidato 1,8 provisional con las opciones 1,8 y 18
  - el goteo de la empaquetadura no trae número en el audio -> fila sin valor, solo con observación
"""

from __future__ import annotations

import re

from ..dominio.catalogo import Catalogo, Mencion
from ..dominio.parte import (
    VARIABLE_DIFERENCIAL,
    VARIABLE_GOTEO,
    VARIABLE_NIVEL,
    VARIABLE_PRESION_BOMBA,
    VARIABLE_TEMPERATURA,
    AmbiguedadValor,
    Extraccion,
    Fila,
)
from .numeros import numero_antes_de
from .texto import normalizar, reparar_asr, reparar_inicio_de_oracion

UNIDADES_DE_PRESION = {
    "kilos": "kg/cm2", "kilogramos": "kg/cm2", "kg/cm2": "kg/cm2", "kg": "kg/cm2",
    "psi": "psi", "libras": "psi",
    "bar": "bar", "bares": "bar",
}  # fmt: skip

ESTADOS_BOMBA = [
    (re.compile(r"\bparada en reserva\b"), "parada, en reserva"),
    (re.compile(r"\bparada por mantenimiento\b"), "parada por mantenimiento"),
    (re.compile(r"\bdisparada\b"), "disparada, no arranca"),
]
RUIDOS = [
    (re.compile(r"\bgolpe ?teo metalico\b"), "hace un golpeteo metálico"),
    (re.compile(r"\bpiedras adentro\b"), "suena como si tuviera piedras adentro"),
    (re.compile(r"\bruido raro de rodamiento\b"), "tiene un ruido raro de rodamiento"),
]
# Observaciones del operador virtual (operador.py) que cuentan como anomalía para B1.
LIMITE_DIFERENCIAL_ALTO = 1.5
LIMITE_RODAMIENTO_CALIENTE = 75
DIFERENCIAL_MIN_AMBIGUO, DIFERENCIAL_MAX_AMBIGUO = 10, 39


def extraer(texto: str, catalogo: Catalogo) -> Extraccion:
    norm = reparar_asr(normalizar(reparar_inicio_de_oracion(texto)))
    menciones = catalogo.menciones(norm)
    ext = Extraccion()
    for i, m in enumerate(menciones):
        fin = menciones[i + 1].inicio if i + 1 < len(menciones) else len(norm)
        resto = norm[m.fin : fin]
        if m.tag is not None and m.tag not in ext.equipos_dichos:
            ext.equipos_dichos.append(m.tag)
        {
            "filtro": _filtro,
            "tanque": _tanque,
            "bomba": _bomba,
        }.get(m.tipo, _nada)(m, resto, catalogo, ext)
    return ext


def _nada(*_args) -> None:
    return None


def _filtro(m: Mencion, resto: str, _catalogo: Catalogo, ext: Extraccion) -> None:
    idx = resto.find("diferencial")
    if idx < 0:
        return
    n = numero_antes_de(resto[:idx].replace(" de ", " ").split(), decimal=True)
    if n is None:
        return
    valor, provisional = n.valor, False
    if n.entero_dicho and DIFERENCIAL_MIN_AMBIGUO <= n.valor <= DIFERENCIAL_MAX_AMBIGUO:
        # "dieciocho de diferencial": ¿1,8 o 18? Se muestra 1,8 como candidato provisional y se ofrecen ambos.
        valor, provisional = n.valor / 10, True
        ext.ambiguedades_valor.append(AmbiguedadValor(len(ext.filas), [valor, n.valor]))
    obs = "diferencial alto" if valor >= LIMITE_DIFERENCIAL_ALTO else None
    ext.filas.append(Fila(m.tag, VARIABLE_DIFERENCIAL, valor, "kg/cm2", obs, provisional))


def _tanque(m: Mencion, resto: str, _catalogo: Catalogo, ext: Extraccion) -> None:
    idx = resto.find("metros")
    if idx < 0:
        return
    n = numero_antes_de(resto[:idx].split(), decimal=True)
    if n is None:
        return
    obs = "rebalse a pileta" if "rebals" in resto else None
    ext.filas.append(Fila(m.tag, VARIABLE_NIVEL, n.valor, "m", obs))


def _bomba(m: Mencion, resto: str, catalogo: Catalogo, ext: Extraccion) -> None:
    tag = m.tag  # None si se dijo "la bomba"

    for patron, observacion in ESTADOS_BOMBA:
        if patron.search(resto):
            ext.filas.append(Fila(tag, "estado", None, None, observacion))
            break

    idx = resto.find(" de descarga")
    if idx >= 0:
        tokens = resto[:idx].split()
        unidad = UNIDADES_DE_PRESION.get(tokens[-1]) if tokens else None
        if unidad is not None:
            tokens = tokens[:-1]
        n = numero_antes_de(tokens, decimal=False, basura=2)  # tolera la unidad mal transcripta: "1610 p si"
        if n is not None:
            if unidad is None and tag is not None:
                unidad = catalogo.unidad_manometro(
                    tag
                )  # el operador no siempre dice la unidad: va la del manómetro
            ext.filas.append(Fila(tag, VARIABLE_PRESION_BOMBA, n.valor, unidad))

    if "empaquetadura" in resto:
        fuerte = bool(re.search(r"pierde bastante|chorrea", resto))
        obs = "pérdida importante por empaquetadura" if fuerte else "goteo por empaquetadura"
        # El caudal de goteo no se dicta: la fila lleva la observación y el valor se pregunta/corrige después.
        ext.filas.append(Fila(tag, VARIABLE_GOTEO, None, "gotas/min", obs))

    idx = resto.find(" grados")
    if idx >= 0 and "rodamiento" in resto[:idx]:
        n = numero_antes_de(resto[:idx].split(), decimal=False)
        if n is not None:
            obs = "rodamiento caliente" if n.valor > LIMITE_RODAMIENTO_CALIENTE else None
            ext.filas.append(Fila(tag, VARIABLE_TEMPERATURA, n.valor, "degC", obs))

    if re.search(r"aceite (en el visor|esta bajo)|visor de aceite esta bajo|no tiene aceite", resto):
        ext.filas.append(Fila(tag, "nivel_aceite_visor", None, None, "nivel de aceite bajo"))

    c = re.search(r"correas estan ([a-z ]+?)(?: chillan| saque|$)", resto)
    if c:
        ext.filas.append(Fila(tag, "estado_correas", None, None, f"correas {c.group(1).strip()}"))

    for patron, observacion in RUIDOS:
        if patron.search(resto):
            ext.filas.append(Fila(tag, "ruido", None, None, observacion))
            break


def completar_unidades(filas: list[Fila], catalogo: Catalogo) -> None:
    """Resuelta la bomba de la nota, las presiones sin unidad toman la de su manómetro."""
    for f in filas:
        if f.tag and f.variable == VARIABLE_PRESION_BOMBA and f.unidad is None:
            f.unidad = catalogo.unidad_manometro(f.tag)
