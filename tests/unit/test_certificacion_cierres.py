import json
from decimal import Decimal
from pathlib import Path

import pytest

from sistema.workflow.certificacion import (
    LISTA_CT_SVP_2022,
    LISTA_CT_SVP_2025,
    validar_cierre_contratista,
    validar_cierre_desde_dict,
)

RAIZ = Path(__file__).resolve().parents[2]
CIERRES_EJEMPLO = (
    RAIZ / "Context" / "Clon del REPO" / "datos" / "documentos" / "contrato" / "cierres_ejemplo.json"
)


def test_cierre_valido_certificado_ct_svp_2025():
    res = validar_cierre_contratista(
        orden_id="OT-25-0119",
        fecha_inicio="2025-12-03T01:45-03:00",
        equipo="B-101",
        tareas=["T-B-ALI"],
        repuestos_orden=[{"codigo": "ACO-ELAST-90", "cantidad": 1}],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=6.0,
        horas_declaradas=6.0,
        repuestos_declarados=[{"codigo": "ACO-ELAST-90", "cantidad": 1}],
        monto_declarado_usd=780,
    )

    assert res.lista_vigente == LISTA_CT_SVP_2025
    assert res.monto_habilita_usd == Decimal("780.00")
    assert res.estado_esperado == "Certificada"
    assert res.motivos == []


def test_cierre_2024_cotizado_con_lista_2025_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-24-0010",
        fecha_inicio="2024-11-05T04:25-03:00",
        equipo="B-101",
        tareas=["I-VIB"],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=1.5,
        horas_declaradas=1.5,
        monto_declarado_usd=960,
    )

    assert res.lista_vigente == LISTA_CT_SVP_2022
    assert res.monto_habilita_usd == Decimal("840.00")
    assert res.estado_esperado == "Observada"
    assert any("CT-SVP-2022" in m for m in res.motivos)


def test_cierre_con_horas_extra_no_autorizadas_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-24-0015",
        fecha_inicio="2024-11-21T16:30-03:00",
        equipo="B-101",
        tareas=["T-B-ROD"],
        repuestos_orden=[
            {"codigo": "TX-ROD-CIG", "cantidad": 1},
            {"codigo": "TX-SELLO-CAR", "cantidad": 1},
        ],
        lista_aplicada=LISTA_CT_SVP_2022,
        horas_registro_ingreso=17.5,
        horas_declaradas=26.5,
        repuestos_declarados=[
            {"codigo": "TX-ROD-CIG", "cantidad": 1},
            {"codigo": "TX-SELLO-CAR", "cantidad": 1},
        ],
        monto_declarado_usd=1694.0,
    )

    assert res.monto_habilita_usd == Decimal("980.00")
    assert res.estado_esperado == "Observada"
    assert any("registro de ingreso" in m for m in res.motivos)


def test_cierre_con_repuesto_no_autorizado_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-25-0004",
        fecha_inicio="2025-01-12T17:30-03:00",
        equipo="PLANTA",
        tareas=["I-VIB"],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=3.5,
        horas_declaradas=3.5,
        repuestos_declarados=[{"codigo": "TX-SELLO-CAR", "cantidad": 1}],
        monto_declarado_usd=1301.0,
    )

    assert res.monto_habilita_usd == Decimal("960.00")
    assert res.estado_esperado == "Observada"
    assert any("TX-SELLO-CAR" in m for m in res.motivos)


def test_cierre_con_cantidad_doble_de_repuesto_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-25-0026",
        fecha_inicio="2025-03-11T19:35-03:00",
        equipo="B-101",
        tareas=["T-B-ALI"],
        repuestos_orden=[{"codigo": "ACO-ELAST-90", "cantidad": 1}],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=5.5,
        horas_declaradas=5.5,
        repuestos_declarados=[{"codigo": "ACO-ELAST-90", "cantidad": 2}],
        monto_declarado_usd=945.0,
    )

    assert res.monto_habilita_usd == Decimal("780.00")
    assert res.estado_esperado == "Observada"
    assert any("ACO-ELAST-90" in m for m in res.motivos)


def test_cierre_con_movilizacion_svp06_mal_cobrada_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-25-0036",
        fecha_inicio="2025-04-14T08:55-03:00",
        equipo="PLANTA",
        tareas=["I-VIB"],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=3.5,
        horas_declaradas=3.5,
        monto_declarado_usd=1140,
    )

    assert res.monto_habilita_usd == Decimal("960.00")
    assert res.estado_esperado == "Observada"
    assert any("SVP-06" in m for m in res.motivos)


def test_cierre_con_recargo_50_fuera_de_horario_queda_observado():
    res = validar_cierre_contratista(
        orden_id="OT-25-0110",
        fecha_inicio="2025-11-12T13:15-03:00",
        equipo="PLANTA",
        tareas=["I-VIB"],
        lista_aplicada=LISTA_CT_SVP_2025,
        horas_registro_ingreso=3.5,
        horas_declaradas=3.5,
        monto_declarado_usd=1440.0,
    )

    assert res.monto_habilita_usd == Decimal("960.00")
    assert res.estado_esperado == "Observada"
    assert any("50 %" in m for m in res.motivos)


@pytest.mark.skipif(not CIERRES_EJEMPLO.exists(), reason="No está disponible Context/cierres_ejemplo.json")
def test_valida_los_12_casos_reales_de_cierres_ejemplo():
    casos = json.loads(CIERRES_EJEMPLO.read_text(encoding="utf-8"))
    assert len(casos) == 12

    for caso in casos:
        res = validar_cierre_desde_dict(caso)
        assert res.monto_habilita_usd == Decimal(str(caso["monto_habilita_usd"])).quantize(Decimal("0.01")), (
            f"Monto incorrecto en {caso['orden_id']}"
        )
        assert res.estado_esperado == caso["estado"], f"Estado incorrecto en {caso['orden_id']}"
        if res.estado_esperado == "Observada":
            assert res.motivos, f"Se esperaban motivos en {caso['orden_id']}"
        else:
            assert res.motivos == [], f"No se esperaban motivos en {caso['orden_id']}"
