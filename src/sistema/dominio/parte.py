"""El parte de una nota: filas, ambigüedades y estado. Sin I/O."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from planta_contrato import FilaParte

from ..voz.numeros import formatear

# Unidades canónicas del contrato y cómo se dicen/escriben.
UNIDAD_CANONICA = {
    "kilos": "kg/cm2", "kilogramos": "kg/cm2", "kg": "kg/cm2", "kg/cm2": "kg/cm2", "kg/cm": "kg/cm2",
    "psi": "psi", "libras": "psi", "lb": "psi",
    "bar": "bar", "bares": "bar",
    "metros": "m", "m": "m",
    "grados": "degC", "gotas por minuto": "gotas/min",
}  # fmt: skip
UNIDAD_MOSTRADA = {
    "kg/cm2": "kg/cm²",
    "psi": "psi",
    "bar": "bar",
    "m": "m",
    "degC": "°C",
    "gotas/min": "gotas/min",
}

VARIABLE_PRESION_BOMBA = "presion_descarga_local"
VARIABLE_DIFERENCIAL = "presion_diferencial"
VARIABLE_NIVEL = "nivel"
VARIABLE_TEMPERATURA = "temperatura_rodamiento_ir"
VARIABLE_GOTEO = "goteo_empaquetadura"
VARIABLE_ESTADO = "estado"
VARIABLE_CONDICION_INSEGURA = "condicion_insegura"


class EstadoParte(StrEnum):
    pregunta_equipo = "pregunta_equipo"  # esperando a qué bomba se refiere
    borrador = "borrador"  # esperando que el operador confirme o corrija
    confirmado = "confirmado"
    pendiente_validacion = "pendiente_validacion"  # D8: queda Borrador, sin eventos
    sin_filas = "sin_filas"  # nota sin nada que mostrar (p. ej. "sin novedad")


@dataclass
class Fila:
    tag: str | None  # None = equipo ambiguo, a resolver con el operador
    variable: str
    valor: float | None = None
    unidad: str | None = None
    observacion: str | None = None
    provisional: bool = False  # candidato ambiguo (p. ej. 1,8 o 18): no es definitivo hasta la respuesta
    foto: str | None = None  # A-6: la foto de la nota que corresponde a esta fila (el contrato no la lleva)

    def a_contrato(self, observacion_visible: str | None = None) -> FilaParte:
        """La fila como se muestra en la tablet. `observacion_visible` reemplaza a la observación real solo en
        pantalla (p. ej. «provisional: ¿1,8 o 18?»): la observación guardada no cambia."""
        assert self.tag is not None, "una fila sin equipo no se muestra"
        return FilaParte(
            tag=self.tag,
            variable=self.variable,
            valor=self.valor,
            unidad=self.unidad,
            observacion=self.observacion if observacion_visible is None else observacion_visible,
        )

    def descripcion(self, nombre: str) -> str:
        if self.valor is not None:
            u = UNIDAD_MOSTRADA.get(self.unidad or "", self.unidad or "")
            return f"{nombre}: {self.variable.replace('_', ' ')} {formatear(self.valor)} {u}".strip()
        return f"{nombre}: {self.observacion or self.variable.replace('_', ' ')}"


@dataclass
class AmbiguedadValor:
    fila: int  # índice en `filas`
    candidatos: list[float]


@dataclass
class Extraccion:
    """Lo que se entendió de la nota, antes de hablar con el operador."""

    filas: list[Fila] = field(default_factory=list)
    equipos_dichos: list[str] = field(default_factory=list)  # TAGs nombrados sin ambigüedad en la nota
    ambiguedades_valor: list[AmbiguedadValor] = field(default_factory=list)

    @property
    def equipo_ambiguo(self) -> bool:
        return any(f.tag is None for f in self.filas)

    @property
    def confirmable(self) -> bool:
        """Hay al menos una fila con valor: es lo único que el operador puede comparar y confirmar."""
        return any(f.valor is not None for f in self.filas)


def filas_a_dict(filas: list[Fila]) -> list[dict]:
    return [asdict(f) for f in filas]


def filas_desde_dict(datos: list[dict]) -> list[Fila]:
    return [Fila(**d) for d in datos]
