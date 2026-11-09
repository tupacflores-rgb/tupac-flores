"""Parser de notas e intérprete de respuestas, con frases de la gramática real del operador virtual."""

# ruff: noqa: E501
from __future__ import annotations

import pytest
from planta_falsa import DEFINICION

from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import Fila
from sistema.voz.parser import extraer
from sistema.voz.respuestas import aplicar, interpretar

CAT = Catalogo(DEFINICION)


def filas(texto):
    return [(f.tag, f.variable, f.valor, f.unidad, f.observacion) for f in extraer(texto, CAT).filas]


# -- parser -----------------------------------------------------------------------------------------


def test_nota_de_bomba_filtro_con_goteo_y_rodamiento():
    texto = (
        "Eh, la del medio está en mil seiscientos cuarenta psi de descarga, sigue goteando por la "
        "empaquetadura, le saqué foto, el rodamiento me dio cincuenta y dos grados con la pistola. "
        "El filtro A me dio uno cinco de diferencial, le saqué foto al manómetro."
    )
    assert filas(texto) == [
        ("B-102", "presion_descarga_local", 1640.0, "psi", None),
        ("B-102", "goteo_empaquetadura", None, "gotas/min", "goteo por empaquetadura"),
        ("B-102", "temperatura_rodamiento_ir", 52.0, "degC", None),
        ("F-201A", "presion_diferencial", 1.5, "kg/cm2", "diferencial alto"),
    ]


def test_la_unidad_se_toma_del_manometro_si_no_se_dice():
    assert filas("La uno está en ciento quince de descarga.") == [
        ("B-101", "presion_descarga_local", 115.0, "kg/cm2", None)
    ]
    assert filas("La tres está en noventa y ocho de descarga.") == [
        ("B-103", "presion_descarga_local", 98.0, "bar", None)
    ]


def test_la_unidad_dicha_manda():
    assert filas("La uno está en ciento quince kilos de descarga.")[0][3] == "kg/cm2"
    assert filas("La dos está en mil seiscientos libras de descarga.")[0][3] == "psi"
    assert filas("La tres está en noventa y ocho bares de descarga.")[0][3] == "bar"


def test_forma_en_que_la_pone_el_transcriptor_con_cifras():
    assert filas("La dos está en 1.640 psi de descarga.") == [
        ("B-102", "presion_descarga_local", 1640.0, "psi", None)
    ]
    assert filas("El filtro B me dio 1,8 de diferencial.")[0][2] == pytest.approx(1.8)
    assert filas("La uno, 115 kilos de descarga.")[0][2] == 115.0


def test_tanque_con_rebalse():
    assert filas("El pulmón está en ocho coma uno metros, está rebalsando a la pileta.") == [
        ("TK-301", "nivel", pytest.approx(8.1), "m", "rebalse a pileta")
    ]
    assert filas("El pulmón está en seis con dos metros")[0][2] == pytest.approx(6.2)


@pytest.mark.parametrize(
    ("texto", "observacion"),
    [
        ("La tres está parada, en reserva.", "parada, en reserva"),
        ("La dos está parada por mantenimiento.", "parada por mantenimiento"),
        ("La uno está disparada, no arranca.", "disparada, no arranca"),
    ],
)
def test_estados_de_bomba(texto, observacion):
    (fila,) = extraer(texto, CAT).filas
    assert (fila.variable, fila.valor, fila.observacion) == ("estado", None, observacion)


def test_correas_aceite_y_ruidos():
    f = filas("En la uno, las correas están muy flojas, chillan, saqué foto.")
    assert f == [("B-101", "estado_correas", None, None, "correas muy flojas")]
    assert filas("La dos hace un golpeteo metálico.")[0][4] == "hace un golpeteo metálico"
    assert (
        filas("La tres suena como si tuviera piedras adentro.")[0][4]
        == "suena como si tuviera piedras adentro"
    )
    assert filas("La uno, el visor de aceite está bajo.")[0][4] == "nivel de aceite bajo"
    assert filas("La dos casi no tiene aceite en el visor.")[0][4] == "nivel de aceite bajo"


def test_goteo_fuerte_y_leve():
    assert (
        filas("La dos pierde bastante por la empaquetadura, le saqué foto.")[0][4]
        == "pérdida importante por empaquetadura"
    )
    assert filas("La uno chorrea por la empaquetadura.")[0][4] == "pérdida importante por empaquetadura"
    assert filas("La tres gotea por la empaquetadura.")[0][4] == "goteo por empaquetadura"


def test_rodamiento_caliente_es_anomalia():
    assert (
        filas("En la dos, el rodamiento me dio ochenta y dos grados con la pistola.")[0][4]
        == "rodamiento caliente"
    )
    assert filas("En la dos, el rodamiento me dio setenta grados con la pistola.")[0][4] is None


def test_muletillas_y_conectores_no_molestan():
    f = filas(
        "Bueno, la uno está en ciento diez de descarga. Después el primario me dio uno coma tres de diferencial. Ah, y la tres está parada, en reserva."
    )
    assert [x[:2] for x in f] == [
        ("B-101", "presion_descarga_local"),
        ("F-201A", "presion_diferencial"),
        ("B-103", "estado"),
    ]


def test_la_bomba_a_secas_es_equipo_ambiguo():
    ext = extraer("La bomba está en ciento quince kilos de descarga.", CAT)
    assert ext.equipo_ambiguo
    assert [(f.tag, f.valor, f.unidad) for f in ext.filas] == [(None, 115.0, "kg/cm2")]


def test_equipos_dichos_se_registran_para_acotar_las_opciones():
    ext = extraer("La uno está parada, en reserva. La bomba está en ciento quince kilos de descarga.", CAT)
    assert ext.equipos_dichos == ["B-101"]


def test_dieciocho_de_diferencial_es_valor_ambiguo_con_candidato_provisional():
    ext = extraer("El filtro B me dio dieciocho de diferencial, le saqué foto al manómetro.", CAT)
    (f,) = ext.filas
    assert (f.valor, f.provisional) == (pytest.approx(1.8), True)
    assert ext.ambiguedades_valor[0].candidatos == [pytest.approx(1.8), 18.0]


def test_uno_ocho_no_es_ambiguo():
    ext = extraer("El filtro B me dio uno ocho de diferencial.", CAT)
    assert ext.filas[0].valor == pytest.approx(1.8)
    assert not ext.filas[0].provisional
    assert ext.ambiguedades_valor == []


def test_nota_sin_equipos_no_inventa_filas():
    assert extraer("Recorrida sin novedad, todo normal.", CAT).filas == []


# -- intérprete de respuestas -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Sí, confirmo.",
        "Está bien, confirmo.",
        "Correcto, dale.",
        "Sí, así es. Confirmo.",
        "Perfecto, confirmo.",
    ],
)
def test_confirmaciones(texto):
    assert interpretar(texto, CAT).tipo == "confirma"


@pytest.mark.parametrize(
    ("texto", "tag"),
    [
        ("La dos.", "B-102"),
        ("Era la dos.", "B-102"),
        ("La correcta es la tres.", "B-103"),
        ("Ninguna de esas, era la uno.", "B-101"),
    ],
)
def test_eleccion_de_equipo(texto, tag):
    it = interpretar(texto, CAT)
    assert (it.tipo, it.tag) == ("equipo", tag)


def test_correccion_de_valor_con_unidad():
    it = interpretar(
        "No. La dos estaba en mil seiscientos cuarenta psi, no en mil seiscientos cuarenta y cinco. Corregilo y te confirmo.",
        CAT,
    )
    assert it.tipo == "corrige"
    (c,) = it.cambios
    assert (c.tipo, c.tag, c.variable, c.valor, c.unidad) == (
        "valor",
        "B-102",
        "presion_descarga_local",
        1640.0,
        "psi",
    )


def test_correccion_del_diferencial_de_un_filtro():
    it = interpretar("No. El filtro A estaba en uno coma ocho kilos, no en dieciocho. Arreglá eso.", CAT)
    (c,) = it.cambios
    assert (c.tag, c.variable, c.valor, c.unidad) == (
        "F-201A",
        "presion_diferencial",
        pytest.approx(1.8),
        "kg/cm2",
    )


def test_te_falto_el_goteo_que_no_se_dicta():
    it = interpretar(
        "No. Te faltó la dos, que estaba en cuarenta y cinco gotas por minuto. Corregilo y te confirmo.", CAT
    )
    (c,) = it.cambios
    assert (c.tipo, c.tag, c.variable, c.valor, c.unidad) == (
        "agregar",
        "B-102",
        "goteo_empaquetadura",
        45.0,
        "gotas/min",
    )


def test_te_falto_una_temperatura_y_una_presion():
    it = interpretar(
        "No. Te faltó la uno, que estaba en sesenta y siete grados. Te faltó la tres, que estaba en noventa y ocho bar. Con eso está.",
        CAT,
    )
    assert [(c.tag, c.variable, c.valor) for c in it.cambios] == [
        ("B-101", "temperatura_rodamiento_ir", 67.0),
        ("B-103", "presion_descarga_local", 98.0),
    ]


def test_correccion_de_equipo_unidad_y_sobra():
    it = interpretar(
        "No. Eso de ciento quince era de la uno, no de la dos. Lo de la tres es en bar, no en psi.", CAT
    )
    assert [(c.tipo, c.tag) for c in it.cambios] == [("equipo", "B-101"), ("unidad", "B-103")]
    it = interpretar("No. Lo de la dos no lo dije. Corregilo y te confirmo.", CAT)
    assert [(c.tipo, c.tag) for c in it.cambios] == [("quitar", "B-102")]


def test_hay_mas_cosas_pide_revisar_todo_sin_cambios_concretos():
    it = interpretar("No. Y hay más cosas, revisalo todo.", CAT)
    assert it.tipo == "corrige"
    assert it.revisar_todo and it.cambios == []


def test_el_no_de_la_correccion_gana_aunque_diga_te_confirmo():
    assert interpretar("No. Lo de la dos no lo dije. Corregilo y te confirmo.", CAT).tipo == "corrige"


def test_respuesta_incomprensible():
    assert interpretar("No te entendí, ¿me lo repetís?", CAT).tipo == "corrige"  # empieza con "No"
    assert interpretar("Hmm bueno", CAT).tipo == "desconocido"


# -- aplicar correcciones ---------------------------------------------------------------------------


def test_aplicar_agrega_el_goteo_a_la_fila_existente():
    base = [
        Fila("B-102", "presion_descarga_local", 1640.0, "psi"),
        Fila("B-102", "goteo_empaquetadura", None, "gotas/min", "goteo por empaquetadura"),
    ]
    it = interpretar("No. Te faltó la dos, que estaba en cuarenta y cinco gotas por minuto.", CAT)
    nuevas = aplicar(base, it.cambios, CAT)
    assert [(f.variable, f.valor, f.observacion) for f in nuevas] == [
        ("presion_descarga_local", 1640.0, None),
        ("goteo_empaquetadura", 45.0, "goteo por empaquetadura"),
    ]
    assert base[1].valor is None  # no modifica la original


def test_aplicar_corrige_valor_y_equipo_y_quita():
    base = [
        Fila("B-102", "presion_descarga_local", 1645.0, "psi"),
        Fila("B-101", "presion_descarga_local", 115.0, "kg/cm2"),
    ]
    v = aplicar(
        base,
        interpretar(
            "No. La dos estaba en mil seiscientos cuarenta psi, no en mil seiscientos cuarenta y cinco.", CAT
        ).cambios,
        CAT,
    )
    assert v[0].valor == 1640.0
    e = aplicar(base, interpretar("No. Eso de ciento quince era de la tres, no de la uno.", CAT).cambios, CAT)
    assert e[1].tag == "B-103"
    q = aplicar(base, interpretar("No. Lo de la uno no lo dije.", CAT).cambios, CAT)
    assert [f.tag for f in q] == ["B-102"]


# -- errores sistemáticos del transcriptor (medidos sobre las voces de referencia) -------------------


def test_me_dio_mal_oido_como_medio_o_medi():
    f = filas(
        "El filtro A medio uno tres de diferencial. En la uno, el rodamiento medió 49 grados con la pistola."
    )
    assert [(x[0], x[1], x[2]) for x in f] == [
        ("F-201A", "presion_diferencial", pytest.approx(1.3)),
        ("B-101", "temperatura_rodamiento_ir", 49.0),
    ]


def test_grados_escritos_con_el_simbolo():
    assert filas("En la dos, el rodamiento me dio 61° con la pistola.")[0][2] == 61.0
    assert filas("En la dos, el rodamiento me dio 61 °C con la pistola.")[0][2] == 61.0


def test_unidad_de_presion_mal_transcripta_no_pierde_el_valor():
    assert filas("La del medio está en 1610p si de descarga.")[0][2:4] == (
        1610.0,
        "psi",
    )  # unidad por el manómetro
    assert filas("La dos está en 1640 p.c. de descarga.")[0][2:4] == (1640.0, "psi")
    assert filas("La del medio está en 1590, psi de descarga.")[0][2:4] == (1590.0, "psi")


def test_cero_nueve_escrito_como_09_es_0_9():
    assert filas("El de la derecha me dio 09 de diferencial.")[0][2] == pytest.approx(0.9)


def test_uno_o_dos_es_uno_dos():
    assert filas("El filtro A medió uno o dos de diferencial.")[0][2] == pytest.approx(1.2)


def test_catorce_o_14_de_diferencial_es_ambiguo_pero_1_4_no():
    assert extraer("Ah, y el de la izquierda me dio 14 de diferencial.", CAT).ambiguedades_valor
    assert not extraer("El de la izquierda me dio 1,4 de diferencial.", CAT).ambiguedades_valor
    assert not extraer("El de la izquierda me dio uno con cuatro de diferencial.", CAT).ambiguedades_valor


def test_golpeteo_partido_en_dos_palabras():
    assert filas("Hace un golpe teo metálico. En la dos.")[0:0] == []  # sin equipo antes: no inventa fila
    assert filas("La dos hace un golpe teo metálico.")[0][4] == "hace un golpeteo metálico"


def test_nivel_del_pulmon_en_cifras():
    assert filas("El pulmón está en 5 metros.")[0][2] == 5.0
    assert filas("El pulmón está en 5,2 metros.")[0][2] == pytest.approx(5.2)


def test_filtro_sin_el_articulo_correcto():
    assert filas("En filtro A me dio uno cinco de diferencial.")[0][:3] == (
        "F-201A",
        "presion_diferencial",
        1.5,
    )
