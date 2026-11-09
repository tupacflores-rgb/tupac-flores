"""Notas `VIVO-…`: sin parte esperado, pueden no traer nada; nunca rompen el flujo."""

from __future__ import annotations

import pytest
from test_dialogo import BaseMemoria, Entorno, nota

from sistema.dominio.parte import EstadoParte
from sistema.voz.dialogo import es_nota_vivo


class RespaldoEspia:
    def __init__(self):
        self.llamadas = 0

    def extraer(self, *a, **k):
        self.llamadas += 1
        return None


def vivo(n=1):
    return nota(n).model_copy(update={"id": f"VIVO-{n:05d}"})


@pytest.mark.parametrize(
    "id_,esperado", [("VIVO-001", True), ("vivo-7", True), ("NOTA-00001", False), ("", False)]
)
def test_se_reconoce_el_prefijo(id_, esperado):
    assert es_nota_vivo(id_) is esperado


def test_vivo_sin_nada_que_reportar_avisa_y_no_espera_respuesta():
    e = Entorno()
    e.dialogo.procesar_nota(vivo(), "Hola, probando el micrófono, uno dos tres.", None)
    m = e.salida.tablet[-1]
    assert m.espera_respuesta is False and m.pantalla is None
    assert e.dialogo._cargar("VIVO-00001").estado == EstadoParte.sin_filas
    assert e.confirmados == []


def test_vivo_que_menciona_un_equipo_sin_valores_no_pide_repetir():
    e = Entorno()
    e.dialogo.procesar_nota(vivo(), "La bomba uno, eh, no sé, después les cuento.", None)
    assert e.salida.tablet[-1].espera_respuesta is False


def test_la_misma_frase_en_una_nota_normal_si_pide_repetir():
    e = Entorno()
    e.nota("La bomba uno, eh, no sé, después les cuento.")
    assert e.salida.tablet[-1].espera_respuesta is True  # contraste: solo VIVO- queda exento


def test_vivo_con_contenido_se_trata_como_una_nota_normal():
    e = Entorno()
    e.dialogo.procesar_nota(vivo(), "La dos está en mil seiscientos cuarenta psi de descarga.", None)
    m = e.salida.tablet[-1]
    assert m.espera_respuesta is True and m.pantalla and m.pantalla.filas


def test_vivo_no_gasta_el_llm_de_respaldo():
    e = Entorno()
    e.dialogo._respaldo = RespaldoEspia()
    e.dialogo.procesar_nota(vivo(), "La bomba uno, eh, no sé.", None)
    assert e.dialogo._respaldo.llamadas == 0


def test_vivo_no_rompe_el_flujo_de_las_siguientes_notas():
    base = BaseMemoria()
    e = Entorno(base=base)
    e.dialogo.procesar_nota(vivo(), "Nada para reportar.", None)
    e.nota("La dos está en mil seiscientos cuarenta psi de descarga.", n=2)
    assert e.salida.tablet[-1].espera_respuesta is True
