"""El flujo de una nota como cadena LCEL y el consumo como callback de LangChain."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda, RunnableSequence
from langchain_core.tools import StructuredTool
from planta_contrato import NotaRecorrida
from planta_falsa import DEFINICION, S1
from test_pipeline_asr import Api, Asr, SalidaFalsa

from sistema.control import CorteDePresupuesto, Presupuesto, RegistroConsumo, SesionNota
from sistema.dominio.catalogo import Catalogo
from sistema.flujo import CallbackConsumo, paso
from sistema.pipeline import Pipeline

CAT = Catalogo(DEFINICION)
NOTA = NotaRecorrida(
    id="NOTA-00001",
    ts=datetime(2026, 3, 4, 7, 15, tzinfo=UTC),
    operador="Héctor Llancafil",
    audio_url="/media/audios/1.wav",
    fotos=[],
    recorrida_id="REC-1",
)


def pipeline(texto, **kw):
    salida, posteriores = SalidaFalsa(), []
    pipe = Pipeline(
        Api(),
        Asr(texto),
        lambda *_: None,
        salida,
        lambda: CAT,
        lambda nota, t, det: posteriores.append((nota.id, t, det)),
        **kw,
    )
    return pipe, salida, posteriores


def test_las_dos_pasadas_son_cadenas_lcel_con_pasos_con_nombre():
    pipe, *_ = pipeline("x")
    assert isinstance(pipe._cadena_seguridad, RunnableSequence)
    assert isinstance(pipe._cadena_completa, RunnableSequence)
    assert [s.get_name() for s in pipe._cadena_seguridad.steps] == ["transcribir_rapido", "seguridad"]
    assert [s.get_name() for s in pipe._cadena_completa.steps] == ["transcribir", "guardar", "parte"]


def test_el_callback_cuenta_cada_paso_y_registra_el_consumo_al_terminar():
    reg = RegistroConsumo()
    pipe, salida, posteriores = pipeline(S1, registro=reg)
    pipe.atender(NOTA)
    (fila,) = reg.filas
    assert (fila.nota_id, fila.carril, fila.pasos, fila.corte) == ("NOTA-00001", "completa", 3, None)
    assert fila.detalle_pasos == ["transcribir", "guardar", "parte"]
    assert fila.ms >= 0 and len(salida.lotes) == 1 and posteriores[0][2].codigo == "S1"


def test_el_callback_corta_la_cadena_por_pasos_y_registra_igual():
    reg = RegistroConsumo()
    pipe, salida, posteriores = pipeline(
        "La uno hace un ruido.", registro=reg, presupuesto=Presupuesto(max_pasos=2)
    )
    with pytest.raises(CorteDePresupuesto) as e:
        pipe.atender(NOTA)
    assert e.value.motivo.value == "pasos"
    (fila,) = reg.filas
    assert fila.corte == "pasos" and fila.pasos == 3
    assert posteriores == []  # el paso «parte» nunca corrió


def test_el_callback_corta_por_tiempo_con_el_timeout_global():
    reg = RegistroConsumo()
    pipe, *_ = pipeline("x", registro=reg, presupuesto=Presupuesto(timeout_s=0.0))
    with pytest.raises(CorteDePresupuesto) as e:
        pipe.atender(NOTA)
    assert e.value.motivo.value == "tiempo"


def test_la_ruta_de_seguridad_corre_en_cadena_y_no_se_corta_pero_se_registra():
    reg = RegistroConsumo()
    pipe, salida, _ = pipeline(S1, registro=reg, presupuesto=Presupuesto(max_pasos=1, timeout_s=0.0))
    pipe.atender_seguridad(NOTA)
    assert len(salida.lotes) == 1
    (fila,) = reg.filas
    assert fila.carril == "seguridad" and fila.corte is not None
    assert fila.detalle_pasos == ["transcribir_rapido", "seguridad"]


# -- el callback fuera del pipeline: tools y modelos --------------------------------------------------


def sesion(**kw):
    reg = RegistroConsumo()
    return SesionNota("N-1", "completa", Presupuesto(**kw), reg), reg


def test_el_callback_cuenta_las_tools_y_corta_un_bucle_de_la_misma_llamada():
    s, _ = sesion(max_repeticiones=2)
    tool = StructuredTool.from_function(lambda pregunta: "ok", name="buscar_manuales", description="x")
    cb = CallbackConsumo(s)
    tool.invoke({"pregunta": "vibración"}, config={"callbacks": [cb]})
    tool.invoke({"pregunta": "vibración"}, config={"callbacks": [cb]})
    with pytest.raises(CorteDePresupuesto) as e:
        tool.invoke({"pregunta": "vibración"}, config={"callbacks": [cb]})
    assert e.value.motivo.value == "repeticion"
    assert s.consumo.detalle_pasos == ["tool:buscar_manuales"] * 3


def test_el_callback_suma_tokens_de_un_modelo_y_corta_por_tokens():
    class Modelo(FakeListChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kw):
            res = super()._generate(messages, stop, run_manager, **kw)
            res.generations[0].message.usage_metadata = {
                "input_tokens": 700,
                "output_tokens": 400,
                "total_tokens": 1100,
            }
            return res

    s, _ = sesion(max_tokens=1500)
    llm = Modelo(responses=["a", "b"])
    cb = CallbackConsumo(s)
    llm.invoke("hola", config={"callbacks": [cb]})
    assert (s.consumo.tokens_in, s.consumo.tokens_out, s.consumo.pasos) == (700, 400, 1)
    with pytest.raises(CorteDePresupuesto) as e:
        llm.invoke("otra vez", config={"callbacks": [cb]})
    assert e.value.motivo.value == "tokens"


def test_una_cadena_lcel_cualquiera_cierra_la_sesion_con_el_callback():
    s, reg = sesion()
    cadena = paso("suma", lambda e: {**e, "n": e["n"] + 1}) | RunnableLambda(
        lambda e: e["n"], name="resultado"
    )
    assert cadena.invoke({"n": 1}, config={"callbacks": [CallbackConsumo(s)]}) == 2
    (fila,) = reg.filas
    assert fila.pasos == 2 and fila.detalle_pasos == ["suma", "resultado"]
