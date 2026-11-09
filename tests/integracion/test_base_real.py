"""Tablas reales de Postgres: esquema, consumo, política de retención y diálogo que sobrevive a un reinicio.

    docker compose up -d db
    docker run --rm --network planta-lan -v <repo>:/work -e PYTHONPATH=/work/src:/work/tests/unit \
        -e DATABASE_URL=<url de la base de planta-lan> tupac-flores-dev pytest -m integracion tests/integracion
"""

# ruff: noqa: E501
from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest

from sistema.persistencia.base import Base

pytestmark = pytest.mark.integracion
URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture(scope="module")
def base():
    if not URL:
        pytest.skip("DATABASE_URL no definida")
    b = Base(URL)
    try:
        b.iniciar(threading.Event(), reintento_s=0.1)
        b.ejecutar("SELECT 1")
    except Exception as e:  # sin base no hay prueba de integración
        pytest.skip(f"base no disponible: {e}")
    return b


def _id(prefijo):
    return f"{prefijo}-{uuid.uuid4().hex[:8]}"


def test_las_tablas_de_la_politica_de_memoria_existen_con_sus_columnas(base):
    cols = {
        t: {
            c
            for (c,) in base.ejecutar(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (t,)
            )
        }
        for t in ("nota", "respuesta", "dialogo", "mensaje_nota", "salida", "consumo")
    }
    assert {"nota_id", "recibida_en", "transcripcion"} <= cols["nota"]
    assert {"nota_id", "estado", "actualizado"} <= cols["dialogo"]
    assert {"mensaje_id", "nota_id", "creado"} <= cols["mensaje_nota"]
    assert {"estado", "publicado"} <= cols["salida"]
    assert {"nota_id", "carril", "pasos", "tokens_in", "tokens_out", "ms", "corte", "ts"} <= cols["consumo"]


def test_registro_de_consumo(base):
    nota = _id("NOTA-C")
    base.registrar_consumo(
        {
            "nota_id": nota,
            "carril": "completa",
            "pasos": 3,
            "tokens_in": 0,
            "tokens_out": 0,
            "ms": 1200,
            "corte": None,
        }
    )
    ((pasos, corte),) = base.ejecutar("SELECT pasos, corte FROM consumo WHERE nota_id = %s", (nota,))
    assert (pasos, corte) == (3, None)


def test_dialogo_persistido_se_recupera_con_el_estado_exacto(base):
    nota = _id("NOTA-D")
    estado = {"nota_id": nota, "estado": "borrador", "borradores": 2, "filas": []}
    base.guardar_dialogo(nota, estado)
    assert base.cargar_dialogo(nota)["borradores"] == 2
    base.registrar_mensaje(f"TAB-{nota}-2", nota)
    assert base.nota_de_mensaje(f"TAB-{nota}-2") == nota


def test_purga_aplica_la_politica_y_no_toca_lo_pendiente_ni_lo_reciente(base):
    n_viejo, n_nuevo, n_abierto = _id("NOTA-V"), _id("NOTA-N"), _id("NOTA-A")
    ts = datetime.now(UTC)
    for n in (n_viejo, n_nuevo, n_abierto):
        base.registrar_nota(n, ts, "REC", "/a.wav", [])
    base.guardar_dialogo(n_viejo, {"estado": "confirmado"})
    base.guardar_dialogo(n_nuevo, {"estado": "confirmado"})
    base.guardar_dialogo(n_abierto, {"estado": "borrador"})
    base.registrar_mensaje(_id("TAB"), n_viejo)
    s_pub_vieja, s_pub_nueva, s_pendiente = _id("S"), _id("S"), _id("S")
    for s in (s_pub_vieja, s_pub_nueva, s_pendiente):
        base.encolar_salida(s, "t", {"x": 1})
    base.marcar_publicada(s_pub_vieja)
    base.marcar_publicada(s_pub_nueva)
    # Se envejece lo que corresponde: cerrado hace 2 días, abierto hace 1 día (no llega a 72 h), salida de hace 9 días.
    base.ejecutar("UPDATE dialogo SET actualizado = now() - interval '2 days' WHERE nota_id = %s", (n_viejo,))
    base.ejecutar(
        "UPDATE dialogo SET actualizado = now() - interval '1 day' WHERE nota_id = %s", (n_abierto,)
    )
    base.ejecutar("UPDATE salida SET publicado = now() - interval '9 days' WHERE id = %s", (s_pub_vieja,))
    base.ejecutar("UPDATE salida SET creado = now() - interval '9 days' WHERE id = %s", (s_pendiente,))

    borradas = base.purgar()

    assert borradas["dialogo_cerrado"] >= 1 and borradas["salida"] >= 1
    assert base.cargar_dialogo(n_viejo) is None  # cerrado y viejo: olvidado
    assert base.cargar_dialogo(n_nuevo) is not None  # cerrado hace poco: se conserva
    assert base.cargar_dialogo(n_abierto) is not None  # abierto hace 1 día: se conserva
    assert base.estado_salida(s_pub_vieja) is None  # publicada hace 9 días: borrada
    assert base.estado_salida(s_pub_nueva) == "publicada"
    assert base.estado_salida(s_pendiente) == "a_publicar"  # lo pendiente no se borra nunca


def test_dialogo_con_la_base_real_sobrevive_a_un_reinicio_a_mitad_de_dialogo(base):
    """Un Dialogo nuevo (proceso nuevo) con la misma base retoma la pregunta de equipo sin responder."""
    from datetime import UTC, datetime

    from planta_contrato import NotaRecorrida, RespuestaOperador
    from planta_falsa import DEFINICION

    from sistema.config import Config
    from sistema.dominio.catalogo import Catalogo
    from sistema.dominio.parte import EstadoParte
    from sistema.estado import RelojPlanta
    from sistema.voz.dialogo import Dialogo

    class Salida:
        def __init__(self):
            self.mensajes = []

        def emitir(self, id_, topico, modelo):
            self.mensajes.append(modelo)
            return True

    cat, ts = Catalogo(DEFINICION), datetime(2026, 3, 4, 7, 15, tzinfo=UTC)
    reloj = RelojPlanta()
    reloj.fijar(ts)
    nota_id = _id("NOTA-R")
    nota = NotaRecorrida(id=nota_id, ts=ts, operador="x", audio_url="/a.wav", fotos=[], recorrida_id="R")

    s1 = Salida()
    d1 = Dialogo(Config(), s1, base, lambda: cat, reloj)
    d1.procesar_nota(nota, "La bomba está en ciento quince de descarga.", None)
    pregunta = s1.mensajes[-1]
    assert pregunta.pantalla.opciones == ["la uno", "la dos", "la tres"]

    s2, confirmados = Salida(), []
    d2 = Dialogo(Config(), s2, base, lambda: cat, reloj, confirmados.append)  # «proceso nuevo»
    d2.procesar_respuesta(
        RespuestaOperador(
            id=_id("R"), ts=ts, mensaje_id=pregunta.id, operador="x", texto="Era la uno.", audio_url=None
        )
    )
    borrador = s2.mensajes[-1]
    assert [(f.tag, f.valor) for f in borrador.pantalla.filas] == [("B-101", 115.0)]
    d2.procesar_respuesta(
        RespuestaOperador(
            id=_id("R"), ts=ts, mensaje_id=borrador.id, operador="x", texto="Sí, confirmo.", audio_url=None
        )
    )
    assert len(confirmados) == 1
    assert base.cargar_dialogo(nota_id)["estado"] == EstadoParte.confirmado.value
