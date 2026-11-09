"""Números en español: cardinales, decimales dichos de varias maneras y puntos de miles (R3/R4)."""

from __future__ import annotations

import pytest

from sistema.voz.numeros import entero_en_palabras, formatear, leer, numero_antes_de


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("cero", 0),
        ("quince", 15),
        ("veintiuno", 21),
        ("veintiun", 21),
        ("treinta y cinco", 35),
        ("cincuenta y dos", 52),
        ("cien", 100),
        ("ciento quince", 115),
        ("ciento veinte", 120),
        ("doscientos cuarenta y uno", 241),
        ("mil", 1000),
        ("mil seiscientos cuarenta", 1640),
        ("mil setecientos diez", 1710),
        ("dos mil", 2000),
    ],
)
def test_cardinales(texto, esperado):
    assert entero_en_palabras(texto.split()) == esperado


@pytest.mark.parametrize(
    "texto", ["uno ocho", "treinta cinco", "quince tres", "y cinco", "cuarenta y", "coma"]
)
def test_no_son_cardinales(texto):
    assert entero_en_palabras(texto.split()) is None


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("uno ocho", 1.8),
        ("uno coma ocho", 1.8),
        ("uno con ocho", 1.8),
        ("uno cinco", 1.5),
        ("uno y medio", 1.5),
        ("cero ocho", 0.8),
        ("cero coma uno cinco", 0.15),
        ("seis coma dos", 6.2),
        ("1,8", 1.8),
        ("1.8", 1.8),
        ("5,0", 5.0),
        ("1 8", 1.8),
    ],
)
def test_decimales(texto, esperado):
    n = leer(texto.split(), decimal=True)
    assert n is not None
    assert n.valor == pytest.approx(esperado)
    assert not n.entero_dicho


@pytest.mark.parametrize("texto", ["dieciocho", "18", "doce", "diecisiete"])
def test_un_entero_grande_en_modo_decimal_queda_marcado_como_ambiguo(texto):
    n = leer(texto.split(), decimal=True)
    assert n is not None
    assert n.entero_dicho


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("115", 115),
        ("1640", 1640),
        ("1.640", 1640),  # punto de miles, no decimal
        ("1,640", 1640),
        ("mil seiscientos cuarenta", 1640),
        ("ciento quince", 115),
    ],
)
def test_enteros_de_presion(texto, esperado):
    n = leer(texto.split(), decimal=False)
    assert n is not None
    assert n.valor == esperado
    assert not n.entero_dicho


def test_en_modo_entero_un_decimal_no_se_acepta_como_palabras():
    assert leer("uno ocho".split(), decimal=False) is None


def test_numero_antes_de_toma_la_corrida_final():
    assert numero_antes_de("el rodamiento me dio cincuenta y dos".split()).valor == 52
    assert numero_antes_de("me dio uno ocho".split(), decimal=True).valor == pytest.approx(1.8)
    assert numero_antes_de("la dos esta en mil seiscientos cuarenta".split()).valor == 1640


def test_numero_antes_de_ignora_palabras_sueltas_que_rodean_al_numero():
    # "con" y "y" también son separadores: no deben arruinar el número que está justo antes.
    assert numero_antes_de("bueno y con ciento quince".split()).valor == 115
    assert numero_antes_de("hola sin numeros".split()) is None


@pytest.mark.parametrize(("valor", "texto"), [(1.8, "1,8"), (115.0, "115"), (0.15, "0,15"), (6.2, "6,2")])
def test_formatear(valor, texto):
    assert formatear(valor) == texto
