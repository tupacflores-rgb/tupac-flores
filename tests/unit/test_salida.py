"""Protocolo intención → publicación → marca (D17), con inyección de fallas.

Garantías que se verifican: ninguna alerta se pierde, nunca hay dos ids distintos para la misma nota, y la
base y el journal quedan consistentes después del reinicio.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from planta_contrato import NotaRecorrida, Topicos

from sistema.deteccion import alertas
from sistema.deteccion.seguridad import detectar
from sistema.persistencia.journal import Journal
from sistema.persistencia.salida import Salida


class BaseEnMemoria:
    """Simula PostgreSQL: lo guardado sobrevive al «reinicio del proceso» (otra instancia de Salida)."""

    def __init__(self):
        self.filas: dict[str, dict] = {}
        self.caida = False
        self.falla_marca = False

    def _chequear(self):
        if self.caida:
            raise TimeoutError("canceling statement due to statement timeout")

    def encolar_salida(self, id_, topico, payload, timeout_ms=None):
        self._chequear()
        if id_ in self.filas:
            return False
        self.filas[id_] = {
            "topico": topico,
            "payload": json.loads(json.dumps(payload)),
            "estado": "a_publicar",
        }
        return True

    def estado_salida(self, id_, timeout_ms=None):
        self._chequear()
        return self.filas[id_]["estado"] if id_ in self.filas else None

    def marcar_publicada(self, id_, timeout_ms=None):
        self._chequear()
        if self.falla_marca:
            raise TimeoutError("sin marca")
        self.filas[id_]["estado"] = "publicada"

    def salidas_pendientes(self, limite=50):
        self._chequear()
        return [(i, f["topico"], f["payload"]) for i, f in self.filas.items() if f["estado"] == "a_publicar"]


class Broker:
    def __init__(self):
        self.publicados: list[tuple[str, str]] = []  # (topico, id)
        self.caido = False

    def publicar(self, topico, modelo):
        if self.caido:
            return False
        self.publicados.append((topico, modelo.id))
        return True

    def ids(self, topico=None):
        return [i for t, i in self.publicados if topico in (None, t)]


class Proceso(BaseException):
    """Muerte del proceso en el punto inyectado (no es una Exception: nadie puede atraparla como un error)."""


NOTA = NotaRecorrida(
    id="NOTA-00042",
    ts=datetime(2026, 3, 4, 7, 15, tzinfo=UTC),
    operador="Héctor Llancafil",
    audio_url="/media/audios/NOTA-00042.wav",
    fotos=[],
    recorrida_id="REC-00001",
)
TEXTO = "Atención, sale agua a presión para todos lados en la descarga de la uno"


@pytest.fixture
def entorno(tmp_path):
    base, broker, journal = BaseEnMemoria(), Broker(), Journal(tmp_path / "journal")

    def nuevo_proceso():
        return Salida(base, journal, broker.publicar)

    return base, broker, journal, nuevo_proceso


def emitir_alerta(salida):
    return alertas.emitir(salida, NOTA, detectar(TEXTO))


def test_camino_feliz_intencion_publicacion_y_marca(entorno):
    base, broker, _, nuevo = entorno
    assert emitir_alerta(nuevo())
    assert broker.ids() == ["EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"]
    assert {f["estado"] for f in base.filas.values()} == {"publicada"}


def test_reentrega_de_la_nota_no_duplica_la_alerta(entorno):
    base, broker, _, nuevo = entorno
    salida = nuevo()
    emitir_alerta(salida)
    emitir_alerta(salida)  # el broker volvió a entregar la nota
    assert broker.ids().count("EV-SEG-NOTA-00042") == 1
    assert len(base.filas) == 2


def test_muerte_despues_de_la_intencion_la_recuperacion_publica(entorno):
    base, broker, _, nuevo = entorno

    def muere_al_publicar(topico, modelo):
        raise Proceso

    with pytest.raises(Proceso):
        alertas.emitir(Salida(base, entorno[2], muere_al_publicar), NOTA, detectar(TEXTO))
    assert broker.ids() == []
    assert {f["estado"] for f in base.filas.values()} == {"a_publicar"}  # la intención sobrevivió

    reiniciado = nuevo()
    assert reiniciado.recuperar() == 2
    assert sorted(broker.ids()) == ["EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"]
    assert {f["estado"] for f in base.filas.values()} == {"publicada"}
    assert reiniciado.recuperar() == 0  # y no vuelve a publicar


def test_muerte_despues_de_publicar_y_antes_de_marcar_republica_el_mismo_id(entorno):
    base, broker, _, nuevo = entorno
    base.falla_marca = True
    emitir_alerta(nuevo())  # publicó, pero la marca no se escribió
    assert {f["estado"] for f in base.filas.values()} == {"a_publicar"}

    base.falla_marca = False
    assert nuevo().recuperar() == 2
    # Al menos una vez: se publicó dos veces, siempre con los mismos dos ids. Nunca un id distinto.
    assert set(broker.ids()) == {"EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"}
    assert broker.ids(Topicos.EVENTOS) == ["EV-SEG-NOTA-00042"] * 2
    assert {f["estado"] for f in base.filas.values()} == {"publicada"}


def test_con_la_base_caida_la_alerta_sale_igual_y_queda_en_el_journal(entorno):
    base, broker, journal, nuevo = entorno
    base.caida = True
    assert emitir_alerta(nuevo())  # la alerta nunca espera a la base
    assert sorted(broker.ids()) == ["EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"]
    assert len(journal.leer("salida")) == 2

    base.caida = False
    nuevo().recuperar()  # la base volvió: se importa el journal y se republica lo pendiente
    assert journal.leer("salida") == []
    assert set(base.filas) == {"EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"}
    assert {f["estado"] for f in base.filas.values()} == {"publicada"}
    assert set(broker.ids()) == {"EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"}


def test_con_el_broker_caido_queda_pendiente_y_sale_al_volver(entorno):
    base, broker, _, nuevo = entorno
    broker.caido = True
    assert not emitir_alerta(nuevo())
    assert broker.ids() == []
    assert {f["estado"] for f in base.filas.values()} == {"a_publicar"}

    broker.caido = False
    assert nuevo().recuperar() == 2
    assert sorted(broker.ids()) == ["EV-SEG-NOTA-00042", "TAB-SEG-NOTA-00042"]


def test_recuperacion_con_la_base_todavia_caida_no_pierde_el_journal(entorno):
    base, _, journal, nuevo = entorno
    base.caida = True
    emitir_alerta(nuevo())
    assert nuevo().recuperar() == 0
    assert len(journal.leer("salida")) == 2  # nada se perdió


def test_el_journal_no_pierde_registros_de_otros_tipos(entorno):
    _, _, journal, nuevo = entorno
    journal.agregar("nota", {"id": "NOTA-1"})
    nuevo().recuperar()
    assert [r["datos"]["id"] for r in journal.leer("nota")] == ["NOTA-1"]
