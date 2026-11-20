import threading

import pytest

from sistema.persistencia.base import TABLAS_REQUERIDAS, Base
from sistema.persistencia.esquema import ESQUEMA


class ConexionFalsa:
    def __init__(self, tablas: set[str]):
        self.tablas = tablas
        self.esquema_aplicado = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql, params=None, *, prepare=None):
        if "information_schema.tables" in sql:
            assert params == (sorted(TABLAS_REQUERIDAS),)
            self.resultado = [(tabla,) for tabla in self.tablas]
        else:
            assert sql == ESQUEMA
            assert prepare is False
            self.esquema_aplicado = True
        return self

    def fetchall(self):
        return self.resultado


class ConexionNumerosFalsa:
    def __init__(self):
        self.secuencias = {}
        self.numeros = {}

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def execute(self, sql, params=None):
        if "SELECT numero FROM habilita_orden" in sql:
            numero = self.numeros.get(params[0])
            self.resultado = (numero,) if numero else None
        elif "INSERT INTO habilita_secuencia" in sql:
            anio = params[0]
            secuencia = self.secuencias.get(anio, 0) + 1
            self.secuencias[anio] = secuencia
            self.resultado = (secuencia,)
        elif "INSERT INTO habilita_orden" in sql:
            orden_id, _anio, _secuencia, numero = params
            self.numeros[orden_id] = numero
            self.resultado = None
        else:
            self.resultado = None
        return self

    def fetchone(self):
        return self.resultado


def test_setup_aplica_y_verifica_el_esquema(monkeypatch):
    base = Base("postgresql://test")
    conexion = ConexionFalsa(set(TABLAS_REQUERIDAS))
    monkeypatch.setattr(base, "conectar", lambda *_args, **_kwargs: conexion)

    base.setup()

    assert conexion.esquema_aplicado


def test_setup_falla_explicito_si_falta_una_tabla(monkeypatch):
    base = Base("postgresql://test")
    conexion = ConexionFalsa(set(TABLAS_REQUERIDAS) - {"salida"})
    monkeypatch.setattr(base, "conectar", lambda *_args, **_kwargs: conexion)

    with pytest.raises(RuntimeError, match="salida"):
        base.setup()


def test_iniciar_ejecuta_setup_antes_de_marcar_base_lista(monkeypatch):
    base = Base("postgresql://test")
    llamadas = []
    monkeypatch.setattr(base, "setup", lambda: llamadas.append("setup"))

    base.iniciar(threading.Event())

    assert llamadas == ["setup"]
    assert base.lista.is_set()


def test_asignar_numero_habilita_es_idempotente_y_secuencial(monkeypatch):
    base = Base("postgresql://test")
    conexion = ConexionNumerosFalsa()
    monkeypatch.setattr(base, "conectar", lambda *_args, **_kwargs: conexion)

    primero = base.asignar_numero_habilita("OT-1", 2099)

    assert primero == "HAB-2099-0001"
    assert base.asignar_numero_habilita("OT-1", 2099) == primero
    assert base.asignar_numero_habilita("OT-1", 2100) == primero
    assert base.asignar_numero_habilita("OT-2", 2099) == "HAB-2099-0002"
