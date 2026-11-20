from sistema.workflow.reglas import (
    EvaluacionAprobacion,
    OrdenBorrador,
    RepuestoOrden,
    evaluar_aprobacion,
)

CATALOGO = {"estandar": [{"codigo": "T-B-VAL"}]}


def crear_orden(
    *,
    tarea: str = "T-B-VAL",
    detiene_equipo: bool = True,
    ejecutor: str = "propio",
    repuesto: str = "TX-VAL-KIT",
    cantidad: int = 1,
) -> OrdenBorrador:
    return OrdenBorrador(
        orden_id="OT-test",
        tareas=[tarea],
        detiene_equipo=detiene_equipo,
        ejecutor=ejecutor,
        repuestos=[RepuestoOrden(codigo=repuesto, cantidad=cantidad)],
    )


def crear_inventario(*, costo: int = 1900, yacimiento: int = 1) -> dict:
    return {
        "repuestos": [
            {
                "codigo": "TX-VAL-KIT",
                "costo_usd": costo,
                "stock": {"yacimiento": yacimiento, "central": 4},
            }
        ]
    }


def test_aprueba_si_cumple_las_cinco_reglas():
    resultado = evaluar_aprobacion(crear_orden(), crear_inventario(), CATALOGO, True)

    assert resultado == EvaluacionAprobacion(aprobada=True, motivos=[])


def test_rechaza_tarea_fuera_del_catalogo_estandar():
    resultado = evaluar_aprobacion(crear_orden(tarea="T-B-ROD"), crear_inventario(), CATALOGO, True)

    assert not resultado.aprobada
    assert any("T-B-ROD" in motivo for motivo in resultado.motivos)


def test_rechaza_detencion_sin_reserva_disponible():
    resultado = evaluar_aprobacion(crear_orden(), crear_inventario(), CATALOGO, False)

    assert not resultado.aprobada
    assert any("reserva" in motivo for motivo in resultado.motivos)


def test_rechaza_stock_insuficiente_en_yacimiento_aunque_haya_stock_central():
    resultado = evaluar_aprobacion(crear_orden(), crear_inventario(yacimiento=0), CATALOGO, True)

    assert not resultado.aprobada
    assert any("Stock insuficiente" in motivo for motivo in resultado.motivos)


def test_suma_repuestos_repetidos_antes_de_validar_stock():
    orden = crear_orden()
    orden.repuestos.append(RepuestoOrden(codigo="TX-VAL-KIT", cantidad=1))
    resultado = evaluar_aprobacion(orden, crear_inventario(yacimiento=1), CATALOGO, True)

    assert not resultado.aprobada
    assert any("2 requeridos" in motivo for motivo in resultado.motivos)


def test_el_limite_de_costo_es_exclusivo():
    aprobado = evaluar_aprobacion(crear_orden(), crear_inventario(costo=2499), CATALOGO, True)
    limite = evaluar_aprobacion(crear_orden(), crear_inventario(costo=2500), CATALOGO, True)

    assert aprobado.aprobada
    assert not limite.aprobada
    assert any("USD 2500" in motivo for motivo in limite.motivos)


def test_rechaza_contratista():
    resultado = evaluar_aprobacion(crear_orden(ejecutor="contratista"), crear_inventario(), CATALOGO, True)

    assert not resultado.aprobada
    assert any("personal propio" in motivo for motivo in resultado.motivos)


def test_rechaza_repuesto_ausente_y_no_omite_la_falla_de_costo():
    resultado = evaluar_aprobacion(crear_orden(repuesto="NO-EXISTE"), crear_inventario(), CATALOGO, True)

    assert not resultado.aprobada
    assert any("no figura en el inventario" in motivo for motivo in resultado.motivos)
    assert any("costo total" in motivo for motivo in resultado.motivos)
