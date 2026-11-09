"""Recuperador: vigencia, aplicabilidad, citas y que mejore a la línea de base BM25 oficial."""

from __future__ import annotations

from pathlib import Path

import pytest

from sistema.conocimiento.corpus import cargar, referencia
from sistema.conocimiento.recuperador import Recuperador

DOCS = Path(__file__).resolve().parents[2] / "src" / "sistema" / "datos" / "documentos"


@pytest.fixture(scope="module")
def r() -> Recuperador:
    return Recuperador(cargar(DOCS))


def test_metadatos_completos():
    frags = cargar(DOCS)
    d = next(f for f in frags if f.cita == "TX350-D §3.2")
    assert (d.documento, d.seccion, d.vigente, d.aplica, d.fabricante) == (
        "TX350-D",
        "3.2",
        True,
        True,
        "Trimax Industrial S.A.",
    )
    assert "B-101" in d.equipos
    c = next(f for f in frags if f.cita == "TX350-C §3.2")
    assert not c.vigente
    assert next(f for f in frags if f.cita.startswith("historial")).es_orden


def test_secciones_que_no_aplican_se_marcan():
    frags = {f.cita: f for f in cargar(DOCS)}
    assert not frags["TX350-D §3.3"].aplica  # límites de otros modelos
    assert not frags["MBP-4 §17.1"].aplica  # PIA-1
    assert frags["MBP-4 §5"].aplica


def test_revision_vencida_no_aparece_salvo_pedido(r):
    pregunta = "límites de vibración de la bomba"
    assert all(x.fragmento.vigente for x in r.buscar(pregunta, 10))
    assert any(not x.fragmento.vigente for x in r.buscar(pregunta, 20, incluir_vencidos=True))


def test_limite_vigente_antes_que_otros_modelos(r):
    citas = r.citas("¿A qué valor de vibración dispara por protección la bomba triplex?", 5)
    assert "TX350-D §3.2" in citas or "TX350-D §6.1" in citas


def test_referencia():
    assert referencia("TX350-D §3.2") == ("TX350-D", "3.2")
    assert referencia("historial OT-25-0026") == ("historial OT-25-0026", "")


def test_suite_oficial_mejora_la_linea_de_base(r):
    """Con la suite de 71 preguntas: sin Rev. C antes que D y al menos tan bueno como BM25 (hit@5 0,845)."""
    import json

    suite_ruta = (
        DOCS.parents[3]
        / "Context"
        / "Clon del REPO"
        / "datos"
        / "documentos"
        / "evaluacion"
        / "retrieval.json"
    )
    if not suite_ruta.exists():
        pytest.skip("la suite oficial no está en el repo (Context/ no se versiona)")
    suite = json.loads(suite_ruta.read_text(encoding="utf-8"))
    hits = sum(any(c in set(q["relevantes"]) for c in r.citas(q["pregunta"], 5)) for q in suite) / len(suite)
    c_antes = 0
    for q in suite:
        top = r.citas(q["pregunta"], 5)
        c_antes += any(c.startswith("TX350-C") for c in top)
    assert hits >= 0.845
    assert c_antes == 0
