"""Respaldo con LLM: validación determinista de lo que propone el modelo, integración en el diálogo y caídas."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import FilaParte, NotaRecorrida, RespuestaOperador
from planta_falsa import DEFINICION

from sistema.config import Config
from sistema.control import RegistroConsumo
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import EstadoParte
from sistema.estado import RelojPlanta
from sistema.respaldo import ParteLLM, RespaldoLLM, menciona_algo, numeros_dichos, validar
from sistema.voz.dialogo import Dialogo

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)


def fp(tag, variable, valor=None, unidad=None, obs=None):
    return FilaParte(tag=tag, variable=variable, valor=valor, unidad=unidad, observacion=obs)


# -- validación determinista --------------------------------------------------------------------------


def test_numeros_dichos_en_cifras_y_en_palabras():
    d = numeros_dichos("la dos esta en mil seiscientos psi, el filtro a medio uno coma tres y 112 kilos")
    assert {1600.0, 112.0, 1.3} <= d


def test_un_valor_inventado_por_el_modelo_se_descarta():
    r = validar(
        [fp("B-102", "presion_descarga_local", 1700.0, "psi")], "la dos esta en mil seiscientos psi", CAT
    )
    assert r.filas == [] and r.descartadas == 1


def test_un_valor_que_si_se_dijo_pasa_y_la_unidad_no_nombrada_es_la_del_manometro():
    r = validar(
        [fp("B-102", "presion_descarga_local", 1640.0, "kg/cm2")],
        "la dos esta en mil seiscientos cuarenta de descarga",
        CAT,
    )
    (f,) = r.filas
    assert (f.tag, f.valor, f.unidad) == ("B-102", 1640.0, "psi")  # «kilos» no se dijo: manda el manómetro


def test_la_unidad_nombrada_en_la_nota_se_respeta():
    r = validar(
        [fp("B-102", "presion_descarga_local", 112.0, "kg/cm2")],
        "la dos esta en ciento doce kilos de descarga",
        CAT,
    )
    assert r.filas[0].unidad == "kg/cm2"


@pytest.mark.parametrize(
    "fila",
    [
        fp("B-999", "presion_descarga_local", 112.0, "kg/cm2"),  # equipo que no existe
        fp("F-201A", "presion_descarga_local", 112.0, "kg/cm2"),  # variable que no es de un filtro
        fp("B-101", "color", None, None, "rojo"),  # variable inventada
        fp("B-101", "estado", None, None, None),  # fila vacía: sin valor ni observación
    ],
)
def test_filas_que_no_pasan_la_validacion_se_descartan(fila):
    r = validar([fila], "la uno esta en ciento doce kilos de descarga", CAT)
    assert r.filas == [] and r.descartadas == 1


def test_una_observacion_sin_valor_pasa_y_no_se_duplica_el_equipo_variable():
    r = validar(
        [
            fp("B-103", "estado", None, None, "parada, en reserva"),
            fp("B-103", "estado", None, None, "parada, en reserva"),
        ],
        "la tres esta parada en reserva",
        CAT,
    )
    assert len(r.filas) == 1 and r.descartadas == 1


def test_el_diferencial_dieciocho_puede_ser_1_8():
    assert validar(
        [fp("F-201A", "presion_diferencial", 1.8, "kg/cm2")],
        "el filtro a medio dieciocho de diferencial",
        CAT,
    ).filas


def test_menciona_algo():
    assert menciona_algo("la uno esta en 112 kilos", CAT) and menciona_algo(
        "perdida por la empaquetadura", CAT
    )
    assert not menciona_algo("recorrida sin novedad, todo normal", CAT)


# -- el modelo (falso) --------------------------------------------------------------------------------


class ChatFalso:
    """Imita `with_structured_output(...)`: invoke devuelve un ParteLLM, o levanta."""

    def __init__(self, filas=None, falla=None):
        self.filas, self.falla, self.pedidos = filas or [], falla, []

    def invoke(self, mensajes, config=None):
        self.pedidos.append(mensajes)
        if self.falla:
            raise self.falla
        return ParteLLM(filas=self.filas)


def respaldo(chat, **kw):
    return RespaldoLLM("http://llm", "modelo-falso", chat=chat, **kw)


def test_extraer_valida_lo_que_propone_el_modelo_y_registra_el_consumo():
    reg = RegistroConsumo()
    chat = ChatFalso(
        [
            fp("B-101", "presion_descarga_local", 112.0, "kg/cm2"),
            fp("B-101", "presion_descarga_local", 999.0, "kg/cm2"),
        ]
    )
    r = respaldo(chat, registro=reg).extraer("la uno esta en ciento doce kilos de descarga", CAT, "N-1")
    assert [(f.tag, f.valor) for f in r.filas] == [("B-101", 112.0)] and r.descartadas == 1
    assert (
        reg.filas[0].carril == "llm" and reg.filas[0].nota_id == "N-1"
    )  # los pasos y tokens: ver test_flujo_lcel
    sistema, humano = chat.pedidos[0]
    assert "B-101" in sistema[1] and "ciento doce" in humano[1]  # el prompt lleva el catálogo y la nota


@pytest.mark.parametrize(
    "error", [TimeoutError("lento"), ConnectionError("llm caído"), ValueError("JSON inválido")]
)
def test_si_el_servicio_falla_devuelve_none_y_no_levanta(error):
    reg = RegistroConsumo()
    assert (
        respaldo(ChatFalso(falla=error), registro=reg).extraer("la uno esta en 112 kilos de descarga", CAT)
        is None
    )
    assert reg.filas[0].carril == "llm"  # igual se registra


# -- en el diálogo ------------------------------------------------------------------------------------


class Salida:
    def __init__(self):
        self.mensajes = []

    def emitir(self, id_, topico, modelo):
        self.mensajes.append(modelo)
        return True


class Base:
    def __init__(self):
        self.d, self.m = {}, {}

    def guardar_dialogo(self, n, e):
        self.d[n] = e

    def cargar_dialogo(self, n):
        return self.d.get(n)

    def registrar_mensaje(self, m, n):
        self.m[m] = n

    def nota_de_mensaje(self, m):
        return self.m.get(m)


def dialogo(chat=None):
    reloj = RelojPlanta()
    reloj.fijar(TS)
    salida, confirmados = Salida(), []
    d = Dialogo(
        Config(),
        salida,
        Base(),
        lambda: CAT,
        reloj,
        confirmados.append,
        respaldo=respaldo(chat) if chat else None,
    )
    nota = NotaRecorrida(id="N-1", ts=TS, operador="x", audio_url="/a.wav", fotos=[], recorrida_id="R")
    return d, salida, confirmados, nota


GARABATO = "la uno esta en ciento doce kilos de descarga con un ruido tssss brrr"


def test_una_nota_que_el_parser_no_entiende_va_al_llm_y_el_operador_la_confirma():
    texto = "la uno ciento doce kilos descarga"  # sin «de descarga» el parser no la entiende; el LLM sí
    chat = ChatFalso([fp("B-101", "presion_descarga_local", 112.0, "kg/cm2")])
    d, salida, confirmados, nota = dialogo(chat)
    d.procesar_nota(nota, texto, None)
    m = salida.mensajes[-1]
    assert [(f.tag, f.valor) for f in m.pantalla.filas] == [("B-101", 112.0)] and m.espera_respuesta
    assert confirmados == []  # el LLM no confirma: falta la respuesta del operador
    d.procesar_respuesta(
        RespuestaOperador(
            id="R1", ts=TS, mensaje_id=m.id, operador="x", texto="Sí, confirmo.", audio_url=None
        )
    )
    assert len(confirmados) == 1 and confirmados[0].estado is EstadoParte.confirmado


def test_si_el_llm_esta_caido_la_nota_igual_recibe_una_respuesta():
    chat = ChatFalso(falla=ConnectionError("llm caído"))
    d, salida, confirmados, nota = dialogo(chat)
    d.procesar_nota(nota, "la uno ciento doce kilos descarga", None)
    m = salida.mensajes[-1]
    assert m.espera_respuesta and "repetís" in m.texto  # nunca una nota sin respuesta por falla del parser
    assert confirmados == []


def test_sin_llm_configurado_una_nota_que_menciona_equipos_pero_no_se_entiende_igual_se_responde():
    d, salida, *_, nota = dialogo(None)
    d.procesar_nota(nota, "la uno ciento doce kilos descarga", None)
    assert salida.mensajes[-1].espera_respuesta


def test_una_nota_sin_equipos_ni_variables_sigue_siendo_sin_filas_y_no_llama_al_llm():
    chat = ChatFalso([fp("B-101", "estado", None, None, "inventado")])
    d, salida, *_, nota = dialogo(chat)
    d.procesar_nota(nota, "Recorrida sin novedad, todo normal.", None)
    assert chat.pedidos == []
    assert not salida.mensajes[-1].espera_respuesta


def test_un_tramo_sin_interpretar_se_completa_y_no_pisa_lo_que_el_parser_ya_saco():
    # La uno la entiende el parser; «la dos» viene deformada en su tramo y el LLM la completa.
    chat = ChatFalso(
        [
            fp("B-102", "presion_descarga_local", 1640.0, "psi"),
            fp("B-101", "presion_descarga_local", 999.0, "kg/cm2"),
        ]
    )
    d, salida, _, nota = dialogo(chat)
    d.procesar_nota(
        nota,
        "La uno esta en ciento doce kilos de descarga. La dos esta en mil seiscientos cuarenta wsi descargaa.",
        None,
    )
    filas = {(f.tag, f.valor) for f in salida.mensajes[-1].pantalla.filas}
    assert ("B-101", 112.0) in filas and ("B-101", 999.0) not in filas


def test_el_llm_responde_una_correccion_que_el_interprete_no_entiende_y_no_confirma():
    chat = ChatFalso()
    d, salida, confirmados, nota = dialogo(chat)
    d.procesar_nota(nota, "La dos esta en mil seiscientos cuarenta psi de descarga.", None)
    m1 = salida.mensajes[-1]
    chat.filas = [fp("B-102", "presion_descarga_local", 1600.0, "psi")]
    d.procesar_respuesta(
        RespuestaOperador(
            id="R1",
            ts=TS,
            mensaje_id=m1.id,
            operador="x",
            texto="mmm eso era mil seiscientos justo",
            audio_url=None,
        )
    )
    m2 = salida.mensajes[-1]
    assert m2.id != m1.id and [(f.tag, f.valor) for f in m2.pantalla.filas] == [("B-102", 1600.0)]
    assert confirmados == []  # el LLM corrige el borrador pero nunca confirma


def test_revisalo_todo_no_dispara_el_llm():
    chat = ChatFalso([fp("B-102", "presion_descarga_local", 1.0, "psi")])
    d, salida, *_, nota = dialogo(chat)
    d.procesar_nota(nota, "La dos esta en mil seiscientos cuarenta psi de descarga.", None)
    pedidos = len(chat.pedidos)
    d.procesar_respuesta(
        RespuestaOperador(
            id="R1",
            ts=TS,
            mensaje_id=salida.mensajes[-1].id,
            operador="x",
            texto="No. Y hay más cosas, revisalo todo.",
            audio_url=None,
        )
    )
    assert len(chat.pedidos) == pedidos


def test_una_fila_de_un_equipo_que_la_nota_no_nombra_se_descarta():
    # El modelo contestó B-102 para «la uno»: es un equipo que existe pero la nota no menciona.
    r = validar(
        [fp("B-102", "presion_descarga_local", 112.0, "kg/cm2")],
        "la uno esta en ciento doce kilos de descarga",
        CAT,
    )
    assert r.filas == [] and r.descartadas == 1
