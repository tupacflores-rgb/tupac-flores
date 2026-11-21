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


def test_tablas_requeridas_incluye_orden_workflow():
    from sistema.persistencia.base import TABLAS_REQUERIDAS

    assert "orden_workflow" in TABLAS_REQUERIDAS


def test_orden_abierta_igual_y_registrar_estado_orden(monkeypatch):
    import json

    base = Base("postgresql://test")
    ordenes: dict[str, tuple[str, list[str], str]] = {}
    consultas: list[tuple[str, tuple]] = []

    def fake_ejecutar(sql: str, params: tuple = ()):
        consultas.append((sql, params))
        if sql.startswith("INSERT INTO orden_workflow"):
            orden_id, equipo, tareas_json, estado = params
            ordenes[orden_id] = (equipo, json.loads(tareas_json), estado)
            return []
        if sql.startswith("SELECT orden_id FROM orden_workflow"):
            equipo, tareas_json, excluir_check, excluir_id = params
            tareas = json.loads(tareas_json)
            for oid, (eq, tar, est) in ordenes.items():
                if (
                    eq == equipo
                    and tar == tareas
                    and est not in ("Rechazada", "Cerrada", "Certificada")
                    and (excluir_check is None or oid != excluir_id)
                ):
                    return [(oid,)]
            return []
        raise AssertionError(f"SQL inesperado: {sql}")

    monkeypatch.setattr(base, "ejecutar", fake_ejecutar)

    # Registra tareas desordenadas y verifica canonización con sorted(tareas).
    base.registrar_estado_orden("OT-1", "B-101", ["T-B-VAL", "T-B-EMP"], "PendienteAprobacion")
    assert ordenes["OT-1"] == ("B-101", ["T-B-EMP", "T-B-VAL"], "PendienteAprobacion")

    # Coincide aunque las tareas se consulten en distinto orden; excluye su propio orden_id.
    assert base.orden_abierta_igual("B-101", ["T-B-EMP", "T-B-VAL"]) == "OT-1"
    assert base.orden_abierta_igual("B-101", ["T-B-EMP", "T-B-VAL"], excluir_orden_id="OT-1") is None
    assert base.orden_abierta_igual("B-101", ["T-B-EMP", "T-B-VAL"], excluir_orden_id="OT-2") == "OT-1"

    assert any(
        "NOT IN ('Rechazada', 'Cerrada', 'Certificada')" in sql
        for sql, _ in consultas
        if sql.startswith("SELECT")
    )

    # Estados cerrados dejan de considerarse abiertos.
    for estado_final in ("Cerrada", "Rechazada", "Certificada"):
        base.registrar_estado_orden("OT-1", "B-101", ["T-B-VAL", "T-B-EMP"], estado_final)
        assert base.orden_abierta_igual("B-101", ["T-B-EMP", "T-B-VAL"]) is None

    # Una orden en Ejecucion sigue abierta y bloquea duplicados.
    base.registrar_estado_orden("OT-2", "F-201B", ["T-F-CAR"], "Ejecucion")
    assert base.orden_abierta_igual("F-201B", ["T-F-CAR"]) == "OT-2"
