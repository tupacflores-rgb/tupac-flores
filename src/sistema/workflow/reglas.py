"""Reglas deterministas de aprobación de órdenes."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LIMITE_COSTO_USD = Decimal("2500")


class RepuestoOrden(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codigo: str
    cantidad: int = Field(gt=0)


class OrdenBorrador(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orden_id: str
    tareas: list[str] = Field(min_length=1)
    detiene_equipo: bool
    ejecutor: Literal["propio", "contratista"]
    repuestos: list[RepuestoOrden] = Field(default_factory=list)
    reduce_inyeccion: bool = False
    evento_ids: list[str] = Field(default_factory=list)


class EvaluacionAprobacion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aprobada: bool
    motivos: list[str] = Field(default_factory=list)


def _lista_de_registros(datos: dict, clave: str, fuente: str) -> list[dict]:
    filas = datos.get(clave)
    if not isinstance(filas, list) or any(not isinstance(fila, dict) for fila in filas):
        raise ValueError(f"{fuente} debe contener una lista de registros en '{clave}'")
    return filas


def _indice_inventario(inventario: dict) -> dict[str, dict]:
    indice: dict[str, dict] = {}
    for fila in _lista_de_registros(inventario, "repuestos", "inventario"):
        codigo = fila.get("codigo")
        stock = fila.get("stock")
        if not isinstance(codigo, str) or not codigo:
            raise ValueError("Cada registro de inventario debe tener un código")
        if codigo in indice:
            raise ValueError(f"Código duplicado en inventario: {codigo}")
        if not isinstance(stock, dict):
            raise ValueError(f"El repuesto {codigo} no tiene stock por almacén")

        cantidad = stock.get("yacimiento")
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad < 0:
            raise ValueError(f"Stock inválido para {codigo} en el almacén yacimiento")
        indice[codigo] = fila
    return indice


def _codigos_tareas_estandar(catalogo_tareas: dict, equipo: str | None = None) -> set[str]:
    codigos: set[str] = set()
    for fila in _lista_de_registros(catalogo_tareas, "estandar", "catálogo de tareas"):
        codigo = fila.get("codigo")
        if not isinstance(codigo, str) or not codigo:
            raise ValueError("Cada tarea estándar debe tener un código")
        equipos = fila.get("equipos")
        if equipo is not None and isinstance(equipos, list) and equipo not in equipos:
            continue
        codigos.add(codigo)
    return codigos


def _costo_unitario(fila: dict, codigo: str) -> Decimal:
    try:
        costo = Decimal(str(fila["costo_usd"]))
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"Costo inválido para el repuesto {codigo}") from exc
    if not costo.is_finite() or costo < 0:
        raise ValueError(f"Costo inválido para el repuesto {codigo}")
    return costo


def evaluar_aprobacion(
    orden: OrdenBorrador,
    inventario: dict,
    catalogo_tareas: dict,
    reserva_disponible: bool,
    *,
    dano_estructural_grave: bool = False,
    equipo: str | None = None,
) -> EvaluacionAprobacion:
    """Evalúa las cinco reglas usando catálogo e inventario estructurados.

    El costo y el stock se leen del inventario recibido, nunca de valores
    propuestos dentro de la orden. El stock requerido para aprobar es el de
    yacimiento.
    """
    if not isinstance(reserva_disponible, bool):
        raise TypeError("reserva_disponible debe ser bool")
    if not isinstance(dano_estructural_grave, bool):
        raise TypeError("dano_estructural_grave debe ser bool")

    motivos: list[str] = []
    tareas_estandar = _codigos_tareas_estandar(catalogo_tareas, equipo)
    inventario_por_codigo = _indice_inventario(inventario)

    tareas_no_estandar = list(dict.fromkeys(t for t in orden.tareas if t not in tareas_estandar))
    for codigo in tareas_no_estandar:
        motivos.append(f"La tarea {codigo} no figura en el catálogo de tareas estándar.")

    if orden.detiene_equipo and not reserva_disponible:
        motivos.append("La orden detiene el equipo y la bomba de reserva no está disponible.")

    cantidades_por_codigo: dict[str, int] = {}
    for repuesto in orden.repuestos:
        cantidades_por_codigo[repuesto.codigo] = (
            cantidades_por_codigo.get(repuesto.codigo, 0) + repuesto.cantidad
        )

    costo_total = Decimal("0")
    costo_completo = True
    for codigo, cantidad_requerida in cantidades_por_codigo.items():
        fila = inventario_por_codigo.get(codigo)
        if fila is None:
            motivos.append(f"El repuesto {codigo} no figura en el inventario.")
            costo_completo = False
            continue

        stock_yacimiento = fila["stock"]["yacimiento"]
        if stock_yacimiento < cantidad_requerida:
            motivos.append(
                f"Stock insuficiente de {codigo} en yacimiento: "
                f"{stock_yacimiento} disponibles, {cantidad_requerida} requeridos."
            )
        costo_total += _costo_unitario(fila, codigo) * cantidad_requerida

    if costo_completo and costo_total >= LIMITE_COSTO_USD:
        motivos.append(f"El costo total USD {costo_total} no es menor a USD {LIMITE_COSTO_USD}.")
    elif not costo_completo:
        motivos.append("No se pudo verificar el costo total porque falta un repuesto en el inventario.")

    if orden.ejecutor != "propio":
        motivos.append("La orden debe ser ejecutada por personal propio.")

    if dano_estructural_grave:
        motivos.append("Daño estructural grave detectado en las fotos; requiere revisión humana.")

    return EvaluacionAprobacion(aprobada=not motivos, motivos=motivos)
