from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from sistema.conocimiento.corpus import Fragmento, cargar
from sistema.conocimiento.recuperador import Recuperador
from sistema.herramientas.catalogo import cargar_materiales
from sistema.workflow.grafo import (
    AnalisisEscalado,
    _nodo_redactar_orden,
    _tareas_basicas,
    construir_grafo,
    validar_citas,
    validar_repuestos,
)
from sistema.workflow.reglas import OrdenBorrador, RepuestoOrden

RAIZ = Path(__file__).resolve().parents[2]
DOCUMENTOS = RAIZ / "src" / "sistema" / "datos" / "documentos"


@pytest.mark.parametrize(
    ("resumen", "equipo", "tarea"),
    [
        ("B-102: vibración elevada", "B-102", "I-VIB"),
        ("B-102: vibración y pulsación en descarga", "B-102", "T-B-VAL"),
        ("B-102: golpeteo metálico rítmico", "B-102", "T-B-VAL"),
        ("B-101: vibración y temperatura alta del rodamiento", "B-101", "T-B-ROD"),
        ("F-201B: lectura anómala", "F-201B", "NO-ESTANDAR"),
    ],
)
def test_analisis_basico_asocia_sintomas_con_tareas(resumen, equipo, tarea):
    assert _tareas_basicas(resumen, equipo) == [tarea]


def test_tarea_no_estandar_se_conserva_para_revision_humana():
    materiales = cargar_materiales()
    nodo = _nodo_redactar_orden(materiales)

    estado = nodo(
        {
            "evento": {
                "id": "EV-N-3-F-201B",
                "equipo": "F-201B",
                "resumen": "F-201B: lectura anómala",
            },
            "analisis": AnalisisEscalado(tareas_sugeridas=["NO-ESTANDAR"]).model_dump(mode="python"),
            "reserva_disponible": True,
        }
    )

    assert estado["orden"]["tareas"] == ["NO-ESTANDAR"]
    assert estado["orden"]["ejecutor"] == "propio"


def test_falla_sin_tarea_mapeada_llega_a_aprobacion_humana_sin_crashear():
    fragmento = Fragmento(
        cita="FC48-3 §2.1",
        documento="FC48-3",
        seccion="2.1",
        titulo="Presión diferencial",
        texto="La presión diferencial del filtro indica su condición.",
        vigente=True,
        aplica=True,
        fabricante="Filtrapat",
        equipos=("F-201B",),
    )
    grafo = construir_grafo(
        checkpointer=MemorySaver(),
        materiales=cargar_materiales(),
        recuperador=Recuperador([fragmento]),
    )
    config = {"configurable": {"thread_id": "OT-EV-N-3-F-201B"}}

    resultado = grafo.invoke(
        {
            "evento": {
                "id": "EV-N-3-F-201B",
                "equipo": "F-201B",
                "resumen": "F-201B: presión diferencial inestable",
            },
            "reserva_disponible": True,
        },
        config=config,
    )

    assert resultado["orden"]["tareas"] == ["NO-ESTANDAR"]
    assert resultado["orden_sistema"]["estado"] == "PendienteAprobacion"
    assert grafo.get_state(config).next == ("esperar_decision",)


def test_camino_feliz_desde_escalado_hasta_evaluacion_en_memoria():
    grafo = construir_grafo(
        materiales=cargar_materiales(),
        recuperador=Recuperador(cargar(DOCUMENTOS)),
    )
    estado = grafo.invoke(
        {
            "evento": {
                "id": "EV-N-1-B-101",
                "equipo": "B-101",
                "resumen": "B-101: goteo de empaquetadura en la bomba",
            },
            "reserva_disponible": True,
        }
    )

    assert estado["analisis"]["tareas_sugeridas"] == ["T-B-EMP"]
    assert estado["orden"]["tareas"] == ["T-B-EMP"]
    assert estado["orden"]["repuestos"] == [{"codigo": "TX-EMP-KIT", "cantidad": 1}]
    assert estado["revision"]["valida"]
    assert estado["evaluacion_aprobacion"]["aprobada"]


def test_el_revisor_rechaza_citas_que_no_salieron_del_recuperador():
    def analizador(evento, evidencia):
        return AnalisisEscalado(tareas_sugeridas=["T-B-EMP"], citas=["TX350-D §9.99"])

    grafo = construir_grafo(
        materiales=cargar_materiales(),
        recuperador=Recuperador([]),
        analizador=analizador,
    )
    estado = grafo.invoke(
        {
            "evento": {"id": "EV-N-2-B-101", "equipo": "B-101", "resumen": "goteo empaquetadura"},
            "reserva_disponible": True,
        }
    )

    assert estado["resultado_revision"] == "invalida"
    assert estado["reintentos_revision"] == 3
    assert "evaluacion_aprobacion" not in estado


def test_validar_citas_contrasta_con_evidencia_recuperada():
    assert validar_citas(["TX350-D §3.2"], [{"cita": "TX350-D §3.2"}]) == []
    assert validar_citas(["TX350-D §9.99"], [{"cita": "TX350-D §3.2"}])


def test_validar_repuestos_contrasta_con_lista_de_materiales_del_equipo():
    orden = OrdenBorrador(
        orden_id="OT-test",
        tareas=["T-B-EMP"],
        detiene_equipo=True,
        ejecutor="propio",
        repuestos=[RepuestoOrden(codigo="TX-ROD-CIG", cantidad=1)],
    )

    errores = validar_repuestos(
        orden,
        "B-101",
        {"B-101": ["TX-EMP-KIT"]},
    )

    assert errores and "TX-ROD-CIG" in errores[0]
