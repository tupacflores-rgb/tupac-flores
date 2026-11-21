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


def test_materiales_reales_rechaza_tx_rod_cig_y_aprueba_t_f_car_f201a():
    from sistema.herramientas.catalogo import cargar_materiales
    from sistema.workflow.reglas import OrdenBorrador, RepuestoOrden

    materiales = cargar_materiales()
    inventario = {"repuestos": materiales["repuestos"]}
    catalogo = {"estandar": materiales["tareas_estandar"]}

    orden_rod = OrdenBorrador(
        orden_id="OT-ROD",
        tareas=["T-B-ROD"],
        detiene_equipo=True,
        ejecutor="propio",
        repuestos=[RepuestoOrden(codigo="TX-ROD-CIG", cantidad=1)],
    )
    res_rod = evaluar_aprobacion(orden_rod, inventario, catalogo, True, equipo="B-102")
    assert not res_rod.aprobada
    assert any("Stock insuficiente de TX-ROD-CIG" in m for m in res_rod.motivos)
    assert any("costo total" in m for m in res_rod.motivos)

    orden_f201b = OrdenBorrador(
        orden_id="OT-F201B",
        tareas=["T-F-CAR"],
        detiene_equipo=False,
        ejecutor="propio",
        repuestos=[RepuestoOrden(codigo="FC-CART-10", cantidad=48)],
    )
    res_f201b = evaluar_aprobacion(orden_f201b, inventario, catalogo, True, equipo="F-201B")
    assert not res_f201b.aprobada
    assert any("Stock insuficiente de FC-CART-10" in m for m in res_f201b.motivos)
    assert not any("costo total" in m for m in res_f201b.motivos)

    orden_f201a = OrdenBorrador(
        orden_id="OT-F201A",
        tareas=["T-F-CAR"],
        detiene_equipo=False,
        ejecutor="propio",
        repuestos=[RepuestoOrden(codigo="FC-CART-25", cantidad=48)],
    )
    res_f201a = evaluar_aprobacion(orden_f201a, inventario, catalogo, True, equipo="F-201A")
    assert res_f201a.aprobada


def test_rechaza_dano_estructural_grave():
    resultado = evaluar_aprobacion(
        crear_orden(),
        crear_inventario(),
        CATALOGO,
        True,
        dano_estructural_grave=True,
        equipo="B-101",
    )

    assert not resultado.aprobada
    assert any("Daño estructural grave" in motivo for motivo in resultado.motivos)


def test_rechaza_tarea_estandar_en_equipo_equivocado():
    from sistema.herramientas.catalogo import cargar_materiales
    from sistema.workflow.reglas import OrdenBorrador, RepuestoOrden

    materiales = cargar_materiales()
    inventario = {"repuestos": materiales["repuestos"]}
    catalogo = {"estandar": materiales["tareas_estandar"]}

    orden = OrdenBorrador(
        orden_id="OT-EQ-ERR",
        tareas=["T-F-CAR"],
        detiene_equipo=False,
        ejecutor="propio",
        repuestos=[RepuestoOrden(codigo="TX-EMP-KIT", cantidad=1)],
    )
    resultado = evaluar_aprobacion(orden, inventario, catalogo, True, equipo="B-101")

    assert not resultado.aprobada
    assert any("T-F-CAR" in motivo for motivo in resultado.motivos)
