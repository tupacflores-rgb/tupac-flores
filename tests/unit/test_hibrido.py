"""Recuperación híbrida: fusión RRF, filtros y respaldo léxico cuando el servicio de embeddings no responde."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sistema.conocimiento.corpus import cargar
from sistema.conocimiento.hibrido import ClienteEmb, RecuperadorHibrido
from sistema.conocimiento.recuperador import Recuperador

DOCS = Path(__file__).resolve().parents[2] / "src" / "sistema" / "datos" / "documentos"


@pytest.fixture(scope="module")
def lexico():
    return Recuperador(cargar(DOCS))


class EmbFalso:
    def __init__(self, citas=None, falla=False):
        self.citas, self.falla, self.pedidos = citas or [], falla, []

    def buscar(self, pregunta, k, prefijos=True, incluir_vencidos=False):
        self.pedidos.append((pregunta, k, prefijos, incluir_vencidos))
        if self.falla:
            raise httpx.ConnectError("emb caído")
        return [{"cita": c} for c in self.citas]


def test_sin_servicio_de_embeddings_es_igual_al_lexico(lexico):
    h = RecuperadorHibrido(lexico, None)
    q = "¿A qué valor de vibración dispara por protección la bomba triplex?"
    assert h.citas(q, 5) == lexico.citas(q, 5)


def test_si_el_servicio_cae_sigue_con_el_lexico_sin_levantar_error(lexico):
    h = RecuperadorHibrido(lexico, EmbFalso(falla=True))
    q = "temperatura de aceite de alarma de las bombas"
    assert h.citas(q, 5) == lexico.citas(q, 5)


def test_la_fusion_sube_lo_que_aparece_en_las_dos_listas(lexico):
    q = "temperatura de aceite de alarma de las bombas"
    solo_lexico = lexico.citas(q, 10)
    elegida = solo_lexico[7]  # algo que el léxico pone octavo
    h = RecuperadorHibrido(lexico, EmbFalso(citas=[elegida]))
    fusion = h.citas(q, 10)
    assert fusion.index(elegida) < solo_lexico.index(elegida)


def test_lo_que_no_aplica_se_degrada_tambien_en_la_fusion(lexico):
    por_cita = {f.cita: f for f in lexico.fragmentos}
    no_aplica = next(c for c, f in por_cita.items() if not f.aplica and f.vigente)
    aplica = next(c for c, f in por_cita.items() if f.aplica and f.vigente and c != no_aplica)
    h = RecuperadorHibrido(lexico, EmbFalso(citas=[no_aplica, aplica]))
    fusion = h.citas("qqqqq xxxxx", 5)
    assert fusion.index(aplica) < fusion.index(
        no_aplica
    )  # iban 1.º y 2.º en lo denso; la penalización los invierte


def test_pasa_los_flags_de_prefijos_y_vencidos_al_servicio(lexico):
    emb = EmbFalso(citas=["TX350-D §3.2"])
    h = RecuperadorHibrido(lexico, emb, prefijos=False)
    h.buscar("vibración", 3, incluir_vencidos=True)
    assert emb.pedidos[0][2:] == (False, True)


def test_cliente_emb_http():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/buscar"
        return httpx.Response(200, json={"resultados": [{"cita": "TX350-D §3.2", "coseno": 0.9}]})

    c = ClienteEmb(
        "http://emb", http=httpx.Client(base_url="http://emb", transport=httpx.MockTransport(handler))
    )
    assert c.buscar("vibración", 3)[0]["cita"] == "TX350-D §3.2"
