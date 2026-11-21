from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from planta_contrato import EstadoOrden, OrdenSistema, Repuesto

from sistema.conocimiento.corpus import Fragmento, cargar
from sistema.conocimiento.recuperador import Recuperador
from sistema.herramientas.catalogo import cargar_materiales
from sistema.integraciones.vision import HallazgoVisual
from sistema.workflow.grafo import (
    AnalisisEscalado,
    CierreValidado,
    _nodo_analizar_fotos,
    _nodo_redactar_orden,
    _tareas_basicas,
    calcular_monto_habilita,
    cargar_precios_contrato,
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
        (
            "B-102: la dos está con pulsación y golpeteo en descarga. "
            "Parte del equipo: temperatura_rodamiento_ir 62 °C",
            "B-102",
            "T-B-VAL",
        ),
        (
            "B-101: goteo en empaquetadura de bomba uno. Parte del equipo: temperatura_rodamiento_ir 64 °C",
            "B-101",
            "T-B-EMP",
        ),
        ("B-102: F03 ruido raro de rodamiento en bomba dos", "B-102", "T-B-ROD"),
        ("B-101: vibración y temperatura alta del rodamiento", "B-101", "T-B-ROD"),
        ("F-201B: lectura anómala", "F-201B", "NO-ESTANDAR"),
        ("F-201B: presión diferencial inestable", "F-201B", "NO-ESTANDAR"),
        (
            "presion_diferencial de F-201A fuera de límites (valor=1.62, rango=[0.1, 1.5])",
            "F-201A",
            "T-F-CAR",
        ),
        ("F07 colmatación acelerada en filtro F-201B: ΔP corregida en aumento", "F-201B", "T-F-CAR"),
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
    assert estado["orden_sistema"]["estado"] == "Rechazada"
    assert "revisión automática" in estado["orden_sistema"]["motivo"]


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


def test_analizar_fotos_descarga_y_envia_imagen_al_cliente_de_vision():
    class VisionFalsa:
        def __init__(self):
            self.imagenes = []

        def analizar_imagen(self, imagen):
            self.imagenes.append(imagen)
            return HallazgoVisual(estado="completa", severidad="grave", objeto="rodamiento")

    cliente = VisionFalsa()
    nodo = _nodo_analizar_fotos(cliente, lambda url: b"imagen:" + url.encode())

    resultado = nodo({"fotos": [{"url": "/fotos/1.jpg"}]})

    assert cliente.imagenes == [b"imagen:/fotos/1.jpg"]
    assert resultado["hallazgos_visuales"][0]["estado"] == "completa"
    assert resultado["hallazgos_visuales"][0]["objeto"] == "rodamiento"


def test_analizar_fotos_sin_cliente_guarda_hallazgo_pendiente():
    resultado = _nodo_analizar_fotos(None, None)({"fotos": [{"url": "/fotos/1.jpg"}]})

    assert resultado["hallazgos_visuales"][0]["estado"] == "pendiente"
    assert resultado["hallazgos_visuales"][0]["severidad"] == "desconocida"


def test_calcular_habilita_suma_tareas_y_repuesto_autorizado_con_margen():
    orden = OrdenSistema(
        id="OT-1",
        ts=datetime.now(UTC),
        estado=EstadoOrden.ejecutada,
        equipo="B-101",
        evento_ids=["EV-1"],
        tareas=["T-B-ROD"],
        detiene_equipo=True,
        reduce_inyeccion=False,
        ejecutor="Servipat",
        repuestos=[Repuesto(codigo="TX-ROD-CIG", cantidad=1)],
        motivo=None,
    )
    cierre = CierreValidado(
        id="CIERRE-1",
        orden_id="OT-1",
        tecnico="Técnico",
        empresa="Servipat",
        transcripcion="declaro un monto de USD 999999",
    )

    monto = calcular_monto_habilita(
        orden,
        cierre,
        {"T-B-ROD": Decimal("1150")},
        {"repuestos": [{"codigo": "TX-ROD-CIG", "costo_usd": 2650}]},
    )

    assert monto == Decimal("4065.00")


def test_precios_contrato_vigentes_se_cargan_desde_el_fragmento():
    precios = cargar_precios_contrato()

    assert precios["T-B-ROD"] == Decimal("1150")
    assert precios["T-B-ALI"] == Decimal("780")
    assert precios["I-VIB"] == Decimal("320")


def test_workflow_omite_duplicado_si_hay_orden_abierta_y_permite_tras_cierre():
    from langgraph.checkpoint.memory import MemorySaver
    from planta_contrato import Topicos

    from sistema.workflow.grafo import _ruta_aprobacion_reglas, construir_grafo

    assert _ruta_aprobacion_reglas({"orden_duplicada_de": "OT-1", "aprobada_por_reglas": True}) == "duplicada"

    class SalidaFake:
        def __init__(self):
            self.ordenes = []

        def emitir(self, id_, topico, modelo):
            if topico == Topicos.ORDENES:
                self.ordenes.append((id_, modelo))
            return True

    abiertas: dict[tuple[str, tuple[str, ...]], str] = {}
    registros: list[tuple[str, str, list[str], str]] = []

    def buscar_orden_abierta(
        equipo: str, tareas: list[str], excluir_orden_id: str | None = None
    ) -> str | None:
        existente = abiertas.get((equipo, tuple(sorted(tareas))))
        if existente and existente != excluir_orden_id:
            return existente
        return None

    def registrar_estado_orden(orden_id: str, equipo: str, tareas: list[str], estado: str) -> None:
        registros.append((orden_id, equipo, list(tareas), estado))
        clave = (equipo, tuple(sorted(tareas)))
        if estado in {"Rechazada", "Cerrada", "Certificada"}:
            if abiertas.get(clave) == orden_id:
                abiertas.pop(clave, None)
        else:
            abiertas[clave] = orden_id

    salida = SalidaFake()
    grafo = construir_grafo(
        checkpointer=MemorySaver(),
        salida=salida,
        buscar_orden_abierta=buscar_orden_abierta,
        registrar_estado_orden=registrar_estado_orden,
    )

    evento_1 = {"id": "EV-DUP-1", "equipo": "B-101", "resumen": "B-101: goteo de empaquetadura"}
    cfg_1 = {"configurable": {"thread_id": "OT-EV-DUP-1"}}
    res_1 = grafo.invoke({"evento": evento_1, "reserva_disponible": True}, config=cfg_1)
    assert "orden_duplicada_de" not in res_1
    assert len(salida.ordenes) == 1
    assert any(r[0] == "OT-EV-DUP-1" and r[3] == "Aprobada" for r in registros)

    # Segundo evento equivalente mientras OT-EV-DUP-1 sigue abierta -> se omite antes de emitir orden.
    evento_2 = {"id": "EV-DUP-2", "equipo": "B-101", "resumen": "B-101: goteo de empaquetadura"}
    cfg_2 = {"configurable": {"thread_id": "OT-EV-DUP-2"}}
    res_2 = grafo.invoke({"evento": evento_2, "reserva_disponible": True}, config=cfg_2)
    assert res_2.get("orden_duplicada_de") == "OT-EV-DUP-1"
    assert len(salida.ordenes) == 1
    assert grafo.get_state(cfg_2).next == ()

    # Tras cerrar la primera orden, un nuevo evento equivalente sí emite una nueva orden.
    registrar_estado_orden("OT-EV-DUP-1", "B-101", ["T-B-EMP"], "Cerrada")
    evento_3 = {"id": "EV-DUP-3", "equipo": "B-101", "resumen": "B-101: goteo de empaquetadura"}
    cfg_3 = {"configurable": {"thread_id": "OT-EV-DUP-3"}}
    res_3 = grafo.invoke({"evento": evento_3, "reserva_disponible": False}, config=cfg_3)
    assert "orden_duplicada_de" not in res_3
    assert len(salida.ordenes) == 2
    assert any(r[0] == "OT-EV-DUP-3" and r[3] == "PendienteAprobacion" for r in registros)


def test_dano_estructural_grave_fuerza_pendiente_con_hallazgo_o_fallback_sin_vision():
    from langgraph.checkpoint.memory import MemorySaver
    from planta_contrato import Topicos

    from sistema.workflow.grafo import _hay_dano_estructural_grave, construir_grafo

    assert _hay_dano_estructural_grave(
        {"hallazgos_visuales": [{"estado": "ok", "severidad": "grave", "descripcion": "fisura"}]}
    )
    assert _hay_dano_estructural_grave(
        {"hallazgos_visuales": [{"estado": "ok", "severidad": "moderada", "descripcion": "rotura en brida"}]}
    )
    assert _hay_dano_estructural_grave({"fotos": [{"url": "/media/fotos/rotura_descarga/04.jpg"}]})
    assert _hay_dano_estructural_grave(
        {"evento": {"resumen": "Condición insegura S1: rotura con salida de agua a presión en B-101"}}
    )
    assert not _hay_dano_estructural_grave(
        {
            "hallazgos_visuales": [{"estado": "pendiente", "severidad": "desconocida"}],
            "fotos": [{"url": "/media/fotos/empaquetadura/01.jpg"}],
            "evento": {"resumen": "B-101: goteo de empaquetadura"},
        }
    )

    class SalidaFake:
        def __init__(self):
            self.ordenes = []

        def emitir(self, id_, topico, modelo):
            if topico == Topicos.ORDENES:
                self.ordenes.append((id_, modelo))
            return True

    salida = SalidaFake()
    grafo = construir_grafo(checkpointer=MemorySaver(), salida=salida)
    cfg = {"configurable": {"thread_id": "OT-EV-DANO-1"}}
    grafo.invoke(
        {
            "evento": {
                "id": "EV-DANO-1",
                "equipo": "B-101",
                "resumen": "B-101: goteo en descarga tras golpe",
            },
            "fotos": [{"url": "/media/fotos/rotura_descarga/04.jpg"}],
            "reserva_disponible": True,
        },
        config=cfg,
    )

    estado = grafo.get_state(cfg)
    assert estado.next == ("esperar_decision",)
    assert estado.values["orden_sistema"]["estado"] == "PendienteAprobacion"
    assert "Daño estructural grave" in estado.values["orden_sistema"]["motivo"]


def test_historial_b101_vibracion_tras_valvulas_sugiere_acople_y_pasa_revision():
    def historial_fake(equipo: str):
        assert equipo == "B-101"
        return [
            {
                "id": "OT-24-0001",
                "equipo": "B-101",
                "tareas": ["T-B-VAL"],
                "aviso": "pulsacion alta en descarga de bomba 1",
                "trabajo_realizado": "se cambio kit completo de valvulas",
            }
        ]

    grafo = construir_grafo(
        checkpointer=MemorySaver(),
        materiales=cargar_materiales(),
        recuperador=Recuperador(cargar(DOCUMENTOS)),
        obtener_historial_ordenes=historial_fake,
    )
    cfg = {"configurable": {"thread_id": "OT-EV-HIST-B101"}}
    resultado = grafo.invoke(
        {
            "evento": {
                "id": "EV-HIST-B101",
                "equipo": "B-101",
                "resumen": "B-101: vibración elevada persistente",
            },
            "reserva_disponible": True,
        },
        config=cfg,
    )

    assert "acople" in resultado["analisis"]["causas"][0]["descripcion"].lower()
    assert resultado["analisis"]["causas"][0]["cita"] == "historial OT-24-0001"
    assert resultado["orden"]["tareas"] == ["I-VIB"]
    assert resultado["revision"]["valida"] is True
    assert resultado["historial_ordenes"][0]["id"] == "OT-24-0001"


def test_historial_b103_empaquetadura_repetida_detecta_embolo_rayado():
    def historial_fake(equipo: str):
        assert equipo == "B-103"
        return [
            {
                "id": "OT-25-0018",
                "equipo": "B-103",
                "tareas": ["T-B-EMP"],
                "aviso": "goteo en empaquetadura",
                "trabajo_realizado": "cambio de empaquetadura",
            }
        ]

    grafo = construir_grafo(
        checkpointer=MemorySaver(),
        materiales=cargar_materiales(),
        recuperador=Recuperador([]),
        obtener_historial_ordenes=historial_fake,
    )
    cfg = {"configurable": {"thread_id": "OT-EV-HIST-B103"}}
    resultado = grafo.invoke(
        {
            "evento": {
                "id": "EV-HIST-B103",
                "equipo": "B-103",
                "resumen": "B-103: goteo en empaquetadura",
            },
            "reserva_disponible": True,
        },
        config=cfg,
    )

    assert "émbolo rayado" in resultado["analisis"]["causas"][0]["descripcion"].lower()
    assert resultado["analisis"]["causas"][0]["cita"] == "historial OT-25-0018"
    assert resultado["orden"]["tareas"] == ["T-B-EMP"]
    assert resultado["revision"]["valida"] is True


def test_historial_b101_tendencia_no_dispara_acople_sobre_rodamiento():
    def historial_fake(equipo: str):
        assert equipo == "B-101"
        return [
            {
                "id": "OT-26-0080",
                "equipo": "B-101",
                "tareas": ["T-B-VAL"],
                "aviso": "pulsacion en descarga",
                "trabajo_realizado": "cambio de valvulas",
            }
        ]

    grafo = construir_grafo(
        checkpointer=MemorySaver(),
        materiales=cargar_materiales(),
        recuperador=Recuperador(cargar(DOCUMENTOS)),
        obtener_historial_ordenes=historial_fake,
    )
    cfg = {"configurable": {"thread_id": "OT-EV-NOTA-00010-B-101"}}
    resultado = grafo.invoke(
        {
            "evento": {
                "id": "EV-NOTA-00010-B-101",
                "equipo": "B-101",
                "resumen": (
                    "la uno: anomalía en el parte de la recorrida (tiene un ruido raro de rodamiento). "
                    "Tendencia (últimas 3 h: presión 108,2 a 107,9 kg/cm2; vibración sube de 3,08 a 4,62 mm/s)"
                ),
            },
            "reserva_disponible": True,
        },
        config=cfg,
    )

    assert resultado["orden"]["tareas"] == ["T-B-ROD"]
    assert resultado["analisis"]["tareas_sugeridas"] == ["T-B-ROD"]
    assert all("acople" not in c["descripcion"].lower() for c in resultado["analisis"]["causas"])
    assert resultado["revision"]["valida"] is True


def test_historial_error_red_degrada_seguro_sin_bloquear_grafo():
    def historial_roto(equipo: str):
        raise RuntimeError(f"timeout consultando {equipo}")

    grafo = construir_grafo(
        materiales=cargar_materiales(),
        recuperador=Recuperador(cargar(DOCUMENTOS)),
        obtener_historial_ordenes=historial_roto,
    )
    resultado = grafo.invoke(
        {
            "evento": {
                "id": "EV-HIST-ERR",
                "equipo": "B-101",
                "resumen": "B-101: goteo de empaquetadura en la bomba",
            },
            "reserva_disponible": True,
        }
    )

    assert resultado["historial_ordenes"] == []
    assert resultado["orden"]["tareas"] == ["T-B-EMP"]
    assert resultado["revision"]["valida"] is True
    assert resultado["evaluacion_aprobacion"]["aprobada"] is True
