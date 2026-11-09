"""Eventos por equipo de un parte confirmado (B1).

Por cada equipo del parte: **Escalado** si hay una anomalía y **SinNovedad** si no.
Anomalía = una observación que no sea «parada, en reserva» / «parada por mantenimiento», **o** un valor fuera
de los límites de alarma del sensor correspondiente (`planta.yaml`, con conversión de unidades).

Los eventos salen solo de un parte que el operador confirmó (D8). Ids deterministas `EV-{nota_id}-{TAG}`.
"""

from __future__ import annotations

from datetime import datetime

from planta_contrato import EventoSistema, TipoEvento

from ..dominio.catalogo import Catalogo
from ..dominio.parte import VARIABLE_CONDICION_INSEGURA, Fila
from ..voz.numeros import formatear

OBSERVACIONES_NORMALES = {"parada, en reserva", "parada por mantenimiento"}
# Variable dicha en campo -> variable del sensor cuyos límites de alarma le corresponden.
VARIABLE_CON_LIMITE = {
    "presion_descarga_local": "presion_descarga",
    "presion_diferencial": "presion_diferencial",
}
A_KG_CM2 = {"kg/cm2": 1.0, "psi": 1 / 14.2233, "bar": 1.01972}


def id_evento(nota_id: str, tag: str) -> str:
    return f"EV-{nota_id}-{tag}"


def fuera_de_limite(fila: Fila, catalogo: Catalogo) -> bool:
    sensor = VARIABLE_CON_LIMITE.get(fila.variable)
    if sensor is None or fila.valor is None or fila.tag is None:
        return False
    limite = catalogo.limite(fila.tag, sensor)
    if limite is None:
        return False
    valor = fila.valor * A_KG_CM2.get(fila.unidad or "kg/cm2", 1.0)
    return (limite.alta is not None and valor >= limite.alta) or (
        limite.baja is not None and valor <= limite.baja
    )


def es_anomalia(fila: Fila, catalogo: Catalogo) -> bool:
    if fila.observacion is not None and fila.observacion not in OBSERVACIONES_NORMALES:
        return True
    return fuera_de_limite(fila, catalogo)


def construir(
    nota_id: str, ts: datetime, filas: list[Fila], catalogo: Catalogo, nombre_equipo=lambda tag: tag
) -> list[EventoSistema]:
    """Un evento por equipo, en el orden en que aparecen en el parte."""
    por_equipo: dict[str, list[Fila]] = {}
    for f in filas:
        if f.tag is None or f.variable == VARIABLE_CONDICION_INSEGURA:
            continue  # la condición insegura la cubre la alerta de seguridad, no B1
        por_equipo.setdefault(f.tag, []).append(f)
    eventos = []
    for tag, grupo in por_equipo.items():
        anomalas = [f for f in grupo if es_anomalia(f, catalogo)]
        tipo = TipoEvento.escalado if anomalas else TipoEvento.sin_novedad
        detalle = "; ".join(_describir(f) for f in (anomalas or grupo))
        resumen = (
            f"{nombre_equipo(tag)}: anomalía en el parte de la recorrida ({detalle})."
            if anomalas
            else f"{nombre_equipo(tag)}: sin novedad ({detalle})."
        )
        eventos.append(
            EventoSistema(
                id=id_evento(nota_id, tag),
                ts=ts,
                tipo=tipo,
                equipo=tag,
                origen="parte",
                parte_id=nota_id,
                nota_ids=[nota_id],
                resumen=resumen,
            )
        )
    return eventos


def _describir(f: Fila) -> str:
    if f.valor is not None:
        return f"{f.variable.replace('_', ' ')} {formatear(f.valor)} {f.unidad or ''}".strip()
    return f.observacion or f.variable.replace("_", " ")
