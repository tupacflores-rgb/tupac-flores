"""Validación contractual y cálculo de Habilita para cierres de contratista (CT-SVP-2022 / CT-SVP-2025)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

LISTA_CT_SVP_2022 = "CT-SVP-2022"
LISTA_CT_SVP_2025 = "CT-SVP-2025"

MARGEN_REPUESTOS_CONTRATISTA = Decimal("1.10")
RECARGO_FUERA_HORARIO = Decimal("1.50")

# Precios en USD (Anexo II de CT-SVP-2025; I-VIB de planta/3 bombas = 3 * tarifa unitaria).
PRECIOS_CT_SVP_2022: dict[str, Decimal] = {
    "T-B-ROD": Decimal("980"),
    "T-B-ALI": Decimal("650"),
    "I-VIB": Decimal("840"),
    "SVP-04": Decimal("55"),
    "SVP-05": Decimal("38"),
    "SVP-06": Decimal("150"),
    "SVP-07": Decimal("300"),
}

PRECIOS_CT_SVP_2025: dict[str, Decimal] = {
    "T-B-ROD": Decimal("1150"),
    "T-B-ALI": Decimal("780"),
    "I-VIB": Decimal("960"),
    "SVP-04": Decimal("68"),
    "SVP-05": Decimal("45"),
    "SVP-06": Decimal("180"),
    "SVP-07": Decimal("350"),
}

PRECIOS_POR_LISTA: dict[str, dict[str, Decimal]] = {
    LISTA_CT_SVP_2022: PRECIOS_CT_SVP_2022,
    LISTA_CT_SVP_2025: PRECIOS_CT_SVP_2025,
}


@dataclass(frozen=True)
class ResultadoValidacionCierre:
    """Resultado de validar un cierre de contratista contra la orden y el contrato vigente."""

    orden_id: str
    lista_vigente: str
    monto_habilita_usd: Decimal
    estado_esperado: Literal["Certificada", "Observada"]
    motivos: list[str] = field(default_factory=list)


def _a_decimal(valor: float | int | Decimal | str, nombre: str) -> Decimal:
    if isinstance(valor, bool):
        raise ValueError(f"{nombre} no puede ser booleano")
    try:
        dec = Decimal(str(valor))
    except InvalidOperation as exc:
        raise ValueError(f"{nombre} inválido: {valor}") from exc
    if not dec.is_finite() or dec < 0:
        raise ValueError(f"{nombre} inválido: {valor}")
    return dec


def lista_contractual_para_fecha(fecha_inicio: str | datetime) -> str:
    """Devuelve la lista contractual vigente según la fecha de inicio del trabajo."""
    dt = datetime.fromisoformat(fecha_inicio) if isinstance(fecha_inicio, str) else fecha_inicio
    return LISTA_CT_SVP_2022 if dt.year < 2025 else LISTA_CT_SVP_2025


def _contar_repuestos(items: Sequence[Mapping[str, object]]) -> Counter[str]:
    conteo: Counter[str] = Counter()
    for item in items:
        codigo = item.get("codigo")
        cantidad = item.get("cantidad")
        if not isinstance(codigo, str) or not codigo:
            raise ValueError("Cada repuesto requiere un código válido")
        if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
            raise ValueError(f"Cantidad inválida para el repuesto {codigo}")
        conteo[codigo] += cantidad
    return conteo


def _calcular_monto_orden(
    tareas: Sequence[str],
    lista_nombre: str,
    repuestos_provistos_contratista: Sequence[Mapping[str, object]] = (),
    costos_repuestos: Mapping[str, float | int | Decimal] | None = None,
) -> Decimal:
    if not tareas:
        raise ValueError("La orden requiere al menos una tarea aprobada")
    precios = PRECIOS_POR_LISTA[lista_nombre]
    total = Decimal("0")
    for tarea in tareas:
        precio = precios.get(tarea)
        if precio is None:
            raise ValueError(f"No hay precio contractual en {lista_nombre} para la tarea {tarea}")
        total += precio

    if repuestos_provistos_contratista:
        costos = costos_repuestos or {}
        for item in repuestos_provistos_contratista:
            codigo = str(item.get("codigo") or "")
            if codigo not in costos:
                raise ValueError(f"No hay costo de lista para el repuesto {codigo}")
            cantidad = _a_decimal(item.get("cantidad", 0), f"cantidad de {codigo}")
            costo = _a_decimal(costos[codigo], f"costo de {codigo}")
            total += costo * cantidad * MARGEN_REPUESTOS_CONTRATISTA

    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validar_cierre_contratista(
    *,
    orden_id: str,
    fecha_inicio: str | datetime,
    equipo: str,
    tareas: Sequence[str],
    repuestos_orden: Sequence[Mapping[str, object]] = (),
    lista_aplicada: str | None = None,
    horas_registro_ingreso: float | int = 0,
    horas_declaradas: float | int = 0,
    repuestos_declarados: Sequence[Mapping[str, object]] = (),
    monto_declarado_usd: float | int | Decimal = 0,
    repuestos_provistos_contratista: Sequence[Mapping[str, object]] = (),
    costos_repuestos: Mapping[str, float | int | Decimal] | None = None,
) -> ResultadoValidacionCierre:
    """Valida un cierre de contratista contra la orden aprobada y las reglas de CT-SVP-2022/2025."""
    del equipo  # Reservado para trazabilidad/extensión futura por equipo.
    lista_vigente = lista_contractual_para_fecha(fecha_inicio)
    monto_habilita = _calcular_monto_orden(
        tareas,
        lista_vigente,
        repuestos_provistos_contratista=repuestos_provistos_contratista,
        costos_repuestos=costos_repuestos,
    )
    monto_declarado = _a_decimal(monto_declarado_usd, "monto_declarado_usd").quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    motivos: list[str] = []

    if lista_aplicada is not None and lista_aplicada != lista_vigente:
        motivos.append(
            f"Lista aplicada {lista_aplicada} no corresponde a la vigente ({lista_vigente}) "
            f"para fecha de inicio {fecha_inicio}."
        )

    horas_ing = _a_decimal(horas_registro_ingreso, "horas_registro_ingreso")
    horas_decl = _a_decimal(horas_declaradas, "horas_declaradas")
    if horas_decl > horas_ing:
        motivos.append(
            f"Declara {horas_decl} h, pero el registro de ingreso registra {horas_ing} h "
            "(horas excedentes no autorizadas)."
        )

    autorizados = _contar_repuestos(repuestos_orden)
    declarados = _contar_repuestos(repuestos_declarados)
    for codigo, cant_decl in sorted(declarados.items()):
        cant_auth = autorizados.get(codigo, 0)
        if cant_auth == 0:
            motivos.append(f"Declara repuesto no autorizado en la orden: {codigo} (cantidad {cant_decl}).")
        elif cant_decl > cant_auth:
            motivos.append(f"Declara {cant_decl} unidades de {codigo}, pero la orden autorizó {cant_auth}.")

    if monto_declarado != monto_habilita:
        movilizacion = PRECIOS_POR_LISTA[lista_vigente]["SVP-06"]
        monto_lista_2025 = (
            _calcular_monto_orden(
                tareas,
                LISTA_CT_SVP_2025,
                repuestos_provistos_contratista=repuestos_provistos_contratista,
                costos_repuestos=costos_repuestos,
            )
            if lista_vigente == LISTA_CT_SVP_2022
            else None
        )
        if monto_lista_2025 is not None and monto_declarado == monto_lista_2025:
            motivos.append(
                f"Cotiza con precios de {LISTA_CT_SVP_2025} (USD {monto_declarado}) un trabajo "
                f"iniciado en 2024 que corresponde certificar con {LISTA_CT_SVP_2022} (USD {monto_habilita})."
            )
        elif monto_declarado - monto_habilita == movilizacion:
            motivos.append(
                f"Factura movilización SVP-06 (USD {movilizacion}) sobre tarifa cerrada/por equipo."
            )
        elif monto_declarado == (monto_habilita * RECARGO_FUERA_HORARIO).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ):
            motivos.append(
                "Aplica recargo del 50 % por trabajo fuera de horario no procedente sobre tarifa cerrada."
            )
        else:
            motivos.append(
                f"Monto declarado (USD {monto_declarado}) difiere del monto habilita (USD {monto_habilita})."
            )

    estado: Literal["Certificada", "Observada"] = "Observada" if motivos else "Certificada"
    return ResultadoValidacionCierre(
        orden_id=orden_id,
        lista_vigente=lista_vigente,
        monto_habilita_usd=monto_habilita,
        estado_esperado=estado,
        motivos=motivos,
    )


def validar_cierre_desde_dict(caso: Mapping[str, object]) -> ResultadoValidacionCierre:
    """Adapta un registro JSON (p. ej. de `cierres_ejemplo.json`) a `validar_cierre_contratista`."""
    return validar_cierre_contratista(
        orden_id=str(caso["orden_id"]),
        fecha_inicio=str(caso["fecha_inicio"]),
        equipo=str(caso["equipo"]),
        tareas=list(caso["tareas"]),  # type: ignore[arg-type]
        repuestos_orden=list(caso.get("repuestos_orden", [])),  # type: ignore[arg-type]
        lista_aplicada=str(caso["lista_aplicada"]) if caso.get("lista_aplicada") is not None else None,
        horas_registro_ingreso=caso.get("horas_registro_ingreso", 0),  # type: ignore[arg-type]
        horas_declaradas=caso.get("horas_declaradas", 0),  # type: ignore[arg-type]
        repuestos_declarados=list(caso.get("repuestos_declarados", [])),  # type: ignore[arg-type]
        monto_declarado_usd=caso.get("monto_declarado_usd", 0),  # type: ignore[arg-type]
    )
