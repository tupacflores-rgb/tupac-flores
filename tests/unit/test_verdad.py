"""evaluacion/verdad.py: aciertos por campo, preguntas en notas ambiguas vs claras, falsos positivos y negativos."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

RUTA = Path(__file__).resolve().parents[2] / "evaluacion" / "verdad.py"
spec = importlib.util.spec_from_file_location("verdad_eval", RUTA)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def fila(tag="B-102", var="presion_descarga_local", valor=1630.0, unidad="psi", obs=None):
    return {"tag": tag, "variable": var, "valor": valor, "unidad": unidad, "observacion": obs}


def nota(id_, items, amb=(), seg=None):
    return {"nota_id": id_, "items": items, "ambiguedades": list(amb), "seguridad": seg}


def tablet(id_, nota_id, filas=None, espera=True):
    return {
        "topico": "sistema/tablet",
        "valido": True,
        "payload": {
            "id": id_,
            "ts": id_,
            "nota_ids": [nota_id],
            "texto": "x",
            "pantalla": {"filas": filas} if filas else None,
            "espera_respuesta": espera,
        },
    }


def alerta(nota_id):
    return {
        "topico": "sistema/eventos",
        "valido": True,
        "payload": {"tipo": "AlertaSeguridad", "nota_ids": [nota_id]},
    }


def test_todo_bien_da_cien_por_ciento_y_sin_falsos():
    ev = v.evaluar("e", [nota("N1", [fila()])], [tablet("1", "N1", [fila()])])
    r = v.resumir([ev])
    assert r["ultimo"]["campos"]["valor"] == [1, 1]
    assert r["ultimo"]["faltan"] == 0 and r["ultimo"]["sobran"] == 0


def test_valor_equivocado_unidad_equivocada_y_fila_que_falta():
    esperadas = [
        fila(),
        fila("B-101", "temperatura_rodamiento_ir", 55.0, "degC"),
        fila("F-201A", "presion_diferencial", 1.3, "kg/cm2"),
    ]
    mostradas = [fila(valor=163.0, unidad="kg/cm2"), fila("B-101", "temperatura_rodamiento_ir", 55.0, "degC")]
    c = v.comparar_filas(esperadas, mostradas)
    assert c["campos"]["fila"] == [2, 3]
    # la fila que falta cuenta como fallo en valor y unidad
    assert c["campos"]["valor"] == [1, 3]
    assert c["campos"]["unidad"] == [1, 3]
    assert c["faltan"] == [("F-201A", "presion_diferencial")]


def test_fila_de_mas_es_falso_positivo():
    c = v.comparar_filas([fila()], [fila(), fila("B-103", "estado", None, None, "parada")])
    assert c["sobran"] == [("B-103", "estado")]


def test_la_unidad_se_compara_sin_mayusculas_ni_superindices():
    c = v.comparar_filas([fila(unidad="kg/cm2")], [fila(unidad="KG/cm²")])
    assert c["campos"]["unidad"] == [1, 1]


def test_observacion_solo_se_exige_si_la_verdad_la_trae():
    c = v.comparar_filas([fila(obs="gotea")], [fila(obs=None)])
    assert c["campos"]["observacion"] == [0, 1]
    c = v.comparar_filas([fila(obs=None)], [fila(obs="algo")])
    assert c["campos"]["observacion"] == [0, 0]


def test_primer_y_ultimo_borrador_pueden_diferir():
    reg = [tablet("1", "N1", [fila(valor=163.0)]), tablet("2", "N1", None), tablet("3", "N1", [fila()])]
    ev = v.evaluar("e", [nota("N1", [fila()])], reg)
    n = ev["notas"][0]
    assert n["primero"]["campos"]["valor"] == [0, 1] and n["ultimo"]["campos"]["valor"] == [1, 1]
    assert n["borradores"] == 2 and n["preguntas"] == 1


def test_preguntas_se_separan_en_ambiguas_y_claras():
    reg = [
        tablet("1", "N1", [fila()]),
        tablet("2", "N2", None, espera=True),
        tablet("3", "N2", [fila()]),
        tablet("4", "N3", None, espera=False),  # un aviso no es pregunta
    ]
    verdad = [
        nota("N1", [fila()]),
        nota("N2", [fila()], amb=[{"tipo": "equipo"}]),
        nota("N3", [fila()]),
    ]
    g = v.resumir([v.evaluar("e", verdad, reg)])["grupos"]
    assert (g["ambiguas"]["notas"], g["ambiguas"]["preguntas"], g["ambiguas"]["con_pregunta"]) == (1, 1, 1)
    assert (g["claras"]["notas"], g["claras"]["preguntas"]) == (2, 0)


def test_seguridad_falso_negativo_y_falso_positivo():
    verdad = [nota("N1", [], seg="S1"), nota("N2", [], seg="S3"), nota("N3", [fila()])]
    reg = [alerta("N1"), alerta("N3")]
    s = v.resumir([v.evaluar("e", verdad, reg)])["seguridad"]
    assert s["inseguras"] == 2 and s["alertas_emitidas"] == 1
    assert s["falsos_negativos"] == ["N2"] and s["falsos_positivos"] == ["N3"]


def test_las_notas_vivo_y_los_dialogos_no_son_notas():
    verdad = [
        {"nota_id": "VIVO-001", "tipo": "vivo", "items": None, "ambiguedades": []},
        {"tipo": "dialogo", "confirmo": True, "problemas": [], "nota_ids": ["N1"]},
        {"tipo": "dialogo", "confirmo": False, "problemas": ["x"], "nota_ids": ["N1"]},
        nota("N1", [fila()]),
    ]
    r = v.resumir([v.evaluar("e", verdad, [tablet("1", "N1", [fila()])])])
    assert r["notas"] == 1
    assert r["dialogos"] == {"total": 2, "confirmados": 1, "con_problemas": 1}


def test_nota_sin_ningun_borrador_se_informa():
    r = v.resumir([v.evaluar("e", [nota("N1", [fila()])], [])])
    assert r["sin_borrador"] == ["N1"]


def test_lee_una_corrida_y_guarda_el_informe(tmp_path, capsys):
    corrida = tmp_path / "corrida"
    corrida.mkdir()
    esc = {"verdad": [nota("N1", [fila()])], "registro": [tablet("1", "N1", [fila()])]}
    (corrida / "esc.json").write_text(json.dumps(esc), encoding="utf-8")
    (corrida / "resultado.json").write_text("{}", encoding="utf-8")
    salida = tmp_path / "out" / "verdad.md"
    v.main([str(corrida), "--salida", str(salida)])
    assert "Último borrador" in salida.read_text(encoding="utf-8")
    assert json.loads(salida.with_suffix(".json").read_text(encoding="utf-8"))["resumen"]["notas"] == 1


def test_sin_origen_es_un_error():
    with pytest.raises(SystemExit):
        v.main([])
