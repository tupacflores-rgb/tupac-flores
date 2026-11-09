"""Presupuesto de pasos y tokens, timeout global por nota, condiciones de corte y registro de consumo."""

import pytest

from sistema.control import (
    Corte,
    CorteDePresupuesto,
    Presupuesto,
    RegistroConsumo,
    SesionNota,
)


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def sesion(reloj=None, registro=None, exenta=False, **kw):
    return SesionNota("NOTA-1", "completa", Presupuesto(**kw), registro, exenta, reloj or Reloj())


def test_corta_por_pasos():
    with pytest.raises(CorteDePresupuesto) as e, sesion(max_pasos=3) as s:
        for i in range(10):
            s.paso(f"paso-{i}")
    assert e.value.motivo is Corte.pasos
    assert s.consumo.pasos == 4 and s.consumo.corte == "pasos"


def test_corta_por_tokens_sumando_entrada_y_salida():
    with pytest.raises(CorteDePresupuesto) as e, sesion(max_tokens=100) as s:
        s.paso("llm", tokens_in=60, tokens_out=30)
        s.paso("llm-2", tokens_in=5, tokens_out=10)
    assert e.value.motivo is Corte.tokens
    assert (s.consumo.tokens_in, s.consumo.tokens_out) == (65, 40)


def test_timeout_global_cuenta_desde_que_empieza_la_nota_y_no_por_paso():
    reloj = Reloj()
    with pytest.raises(CorteDePresupuesto) as e, sesion(reloj, timeout_s=10) as s:
        for i in range(5):
            reloj.t += 3  # cada paso es corto (3 s) pero la nota ya lleva 12 s
            s.paso(f"p{i}")
    assert e.value.motivo is Corte.tiempo
    assert s.consumo.ms == 12000


def test_restante_acota_el_timeout_de_cada_llamada():
    reloj = Reloj()
    s = sesion(reloj, timeout_s=10)
    reloj.t = 7
    assert s.restante_s() == pytest.approx(3.0)
    reloj.t = 99
    assert s.restante_s() == 0.0


def test_corta_por_repeticion_del_mismo_paso_con_los_mismos_argumentos():
    with pytest.raises(CorteDePresupuesto) as e, sesion(max_repeticiones=2) as s:
        for _ in range(5):
            s.paso("buscar_manuales", {"pregunta": "vibración"})
    assert e.value.motivo is Corte.repeticion


def test_el_mismo_paso_con_argumentos_distintos_no_es_un_bucle():
    with sesion(max_repeticiones=2) as s:
        for i in range(6):
            s.paso("buscar_manuales", {"pregunta": f"q{i}"})
    assert s.consumo.corte is None


def test_la_ruta_de_seguridad_se_mide_pero_no_se_corta():
    with sesion(exenta=True, max_pasos=1) as s:
        for i in range(4):
            s.paso(f"p{i}")  # no levanta
    assert s.consumo.pasos == 4 and s.consumo.corte == "pasos"


def test_registra_el_consumo_aunque_la_nota_se_corte_y_los_totales_suman():
    reg = RegistroConsumo()
    with sesion(registro=reg) as s:
        s.paso("a", tokens_in=3, tokens_out=2)
    with pytest.raises(CorteDePresupuesto), sesion(registro=reg, max_pasos=1) as s2:
        s2.paso("a")
        s2.paso("b")
    assert reg.totales() == {"notas": 2, "pasos": 3, "tokens_in": 3, "tokens_out": 2, "cortes": 1}


def test_un_registro_roto_no_rompe_la_nota():
    def roto(_):
        raise RuntimeError("base caída")

    with sesion(registro=roto) as s:
        s.paso("a")
    assert s.consumo.pasos == 1
