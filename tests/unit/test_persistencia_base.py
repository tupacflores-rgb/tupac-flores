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
