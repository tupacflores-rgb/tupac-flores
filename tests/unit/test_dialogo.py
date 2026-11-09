"""Diálogo con el operador: política de mensajes de la tablet (Plan Maestro §10.1)."""

# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from planta_contrato import NotaRecorrida, RespuestaOperador
from planta_falsa import DEFINICION, S1, S2, S3, S4

from sistema.config import Config
from sistema.deteccion.seguridad import detectar
from sistema.dominio.catalogo import Catalogo
from sistema.dominio.parte import EstadoParte
from sistema.estado import RelojPlanta
from sistema.voz.dialogo import Dialogo

CAT = Catalogo(DEFINICION)
TS = datetime(2026, 3, 4, 7, 15, tzinfo=UTC)


class SalidaFalsa:
    def __init__(self):
        self.mensajes = []

    def emitir(self, id_, topico, modelo):
        self.mensajes.append(modelo)
        return True

    @property
    def tablet(self):
        return self.mensajes


class BaseMemoria:
    def __init__(self):
        self.dialogos, self.mensajes = {}, {}

    def guardar_dialogo(self, nota_id, estado):
        self.dialogos[nota_id] = estado

    def cargar_dialogo(self, nota_id):
        return self.dialogos.get(nota_id)

    def registrar_mensaje(self, mensaje_id, nota_id):
        self.mensajes[mensaje_id] = nota_id

    def nota_de_mensaje(self, mensaje_id):
        return self.mensajes.get(mensaje_id)


def nota(n=1):
    return NotaRecorrida(
        id=f"NOTA-{n:05d}",
        ts=TS,
        operador="Héctor Llancafil",
        audio_url=f"/media/audios/{n}.wav",
        fotos=[],
        recorrida_id="REC-1",
    )


class Entorno:
    def __init__(self, estricta=True, base=None):
        self.salida, self.base = SalidaFalsa(), base or BaseMemoria()
        self.confirmados = []
        reloj = RelojPlanta()
        reloj.fijar(TS)
        self.dialogo = Dialogo(
            Config(confirmacion_estricta=estricta),
            self.salida,
            self.base,
            lambda: CAT,
            reloj,
            self.confirmados.append,
        )

    def nota(self, texto, n=1, seguridad=False):
        self.dialogo.procesar_nota(nota(n), texto, detectar(texto, CAT) if seguridad else None)
        return self.salida.tablet[-1] if self.salida.tablet else None

    def responde(self, texto, a=None):
        ultimo = a or self.salida.tablet[-1]
        self.dialogo.procesar_respuesta(
            RespuestaOperador(
                id=f"RESP-{ultimo.id}",
                ts=TS,
                mensaje_id=ultimo.id,
                operador="Héctor Llancafil",
                texto=texto,
                audio_url=None,
            )
        )
        return self.salida.tablet[-1]


NOTA_CLARA = (
    "La dos está en mil seiscientos cuarenta psi de descarga. El filtro A me dio uno tres de diferencial."
)


@pytest.fixture
def e():
    return Entorno()


def vistas(msg):
    return [(f.tag, f.variable, f.valor, f.unidad) for f in msg.pantalla.filas]


def test_nota_clara_un_borrador_con_filas_sin_opciones_y_espera_respuesta(e):
    m = e.nota(NOTA_CLARA)
    assert m.id == "TAB-NOTA-00001-1"
    assert m.espera_respuesta and m.pantalla.opciones == []
    assert vistas(m) == [
        ("B-102", "presion_descarga_local", 1640.0, "psi"),
        ("F-201A", "presion_diferencial", pytest.approx(1.3), "kg/cm2"),
    ]
    assert len(e.salida.tablet) == 1


def test_confirmacion_manda_aviso_guardado_sin_pedir_respuesta_y_recien_ahi_hay_eventos(e):
    e.nota(NOTA_CLARA)
    assert e.confirmados == []  # todavía no hay eventos
    aviso = e.responde("Sí, confirmo.")
    assert aviso.id == "TAB-NOTA-00001-2"
    assert not aviso.espera_respuesta
    assert aviso.pantalla is None
    (est,) = e.confirmados
    assert est.estado is EstadoParte.confirmado
    assert [f.tag for f in est.filas] == ["B-102", "F-201A"]


def test_correccion_de_valor_genera_borrador_nuevo_con_id_nuevo_y_se_confirma(e):
    e.nota("La dos está en mil seiscientos cuarenta y cinco psi de descarga.")
    m2 = e.responde(
        "No. La dos estaba en mil seiscientos cuarenta psi, no en mil seiscientos cuarenta y cinco. Corregilo y te confirmo."
    )
    assert m2.id == "TAB-NOTA-00001-2" and m2.espera_respuesta
    assert vistas(m2) == [("B-102", "presion_descarga_local", 1640.0, "psi")]
    e.responde("Perfecto, confirmo.")
    assert len(e.confirmados) == 1
    assert e.confirmados[0].filas[0].valor == 1640.0


def test_goteo_sin_valor_se_muestra_con_observacion_y_el_operador_lo_completa(e):
    m = e.nota(
        "La dos está en mil seiscientos cuarenta psi de descarga, sigue goteando por la empaquetadura, le saqué foto."
    )
    assert [(f.variable, f.valor) for f in m.pantalla.filas] == [
        ("presion_descarga_local", 1640.0),
        ("goteo_empaquetadura", None),
    ]
    m2 = e.responde(
        "No. Te faltó la dos, que estaba en cuarenta y cinco gotas por minuto. Corregilo y te confirmo."
    )
    assert [(f.variable, f.valor, f.unidad) for f in m2.pantalla.filas] == [
        ("presion_descarga_local", 1640.0, "psi"),
        ("goteo_empaquetadura", 45.0, "gotas/min"),
    ]
    e.responde("Sí, confirmo.")
    assert len(e.confirmados) == 1


def test_equipo_ambiguo_pregunta_con_opciones_y_sin_filas_y_despues_muestra_el_borrador(e):
    q = e.nota("La bomba está en ciento quince de descarga.")
    assert q.espera_respuesta
    assert q.pantalla.filas == []
    assert q.pantalla.opciones == ["la uno", "la dos", "la tres"]
    borrador = e.responde("Era la uno.")
    assert vistas(borrador) == [
        ("B-101", "presion_descarga_local", 115.0, "kg/cm2")
    ]  # unidad del manómetro de la uno
    assert borrador.pantalla.opciones == []


def test_equipo_ambiguo_excluye_los_equipos_ya_nombrados_en_la_nota(e):
    q = e.nota("La uno está parada, en reserva. La bomba está en noventa y ocho de descarga.")
    assert q.pantalla.opciones == ["la dos", "la tres"]


def test_valor_ambiguo_borrador_con_candidato_provisional_y_dos_opciones(e):
    m = e.nota("El filtro B me dio dieciocho de diferencial, le saqué foto al manómetro.")
    assert m.pantalla.opciones == ["1,8", "18"]
    (fila,) = m.pantalla.filas
    assert fila.valor == pytest.approx(1.8)
    assert fila.observacion.startswith("provisional")
    e.responde("Sí, confirmo.")
    fila = e.confirmados[0].filas[0]
    assert fila.provisional is False
    # «provisional» es solo lo que se mostró: no puede quedar como observación (sería una falsa anomalía en B1).
    # Lo que queda es la observación real de 1,8 kg/cm² (>= 1,5 es diferencial alto).
    assert fila.observacion == "diferencial alto"


def test_nota_solo_de_observaciones_queda_borrador_y_no_genera_eventos_d8(e):
    m = e.nota("La tres está parada, en reserva.")
    assert not m.espera_respuesta
    assert [(f.variable, f.valor, f.observacion) for f in m.pantalla.filas] == [
        ("estado", None, "parada, en reserva")
    ]
    assert e.confirmados == []
    assert e.dialogo._estados["NOTA-00001"].estado is EstadoParte.pendiente_validacion


def test_con_la_bandera_d16_las_observaciones_si_se_pueden_validar():
    e = Entorno(estricta=False)
    m = e.nota("La tres está parada, en reserva.")
    assert m.espera_respuesta
    e.responde("Era la tres.")
    assert len(e.confirmados) == 1


def test_nota_sin_equipos_avisa_sin_inventar_filas(e):
    m = e.nota("Recorrida sin novedad, todo normal.")
    assert not m.espera_respuesta
    assert m.pantalla is None


def test_presupuesto_de_cinco_borradores_por_defecto_y_luego_pendiente_de_validacion(e):
    e.nota(NOTA_CLARA)
    for _ in range(4):
        e.responde("No. Y hay más cosas, revisalo todo.")
    ultimo = e.responde("No. Y hay más cosas, revisalo todo.")
    assert not ultimo.espera_respuesta
    assert "pendiente de validación" in ultimo.texto
    assert e.confirmados == []
    assert sum(1 for m in e.salida.tablet if m.pantalla and m.pantalla.filas) == 5


def test_el_tope_de_borradores_es_configurable():
    e = Entorno()
    e.dialogo._config = Config(max_borradores=2)
    e.nota(NOTA_CLARA)
    e.responde("No. Y hay más cosas, revisalo todo.")
    ultimo = e.responde("No. Y hay más cosas, revisalo todo.")
    assert "pendiente de validación" in ultimo.texto
    assert sum(1 for m in e.salida.tablet if m.pantalla and m.pantalla.filas) == 2


def test_una_correccion_que_llega_al_cuarto_borrador_todavia_se_confirma(e):
    e.nota(NOTA_CLARA)
    for _ in range(3):
        e.responde("No. Y hay más cosas, revisalo todo.")
    e.responde("Sí, confirmo.")
    assert len(e.confirmados) == 1


def test_ids_de_tablet_nunca_se_repiten(e):
    e.nota(NOTA_CLARA)
    e.responde("No. Y hay más cosas, revisalo todo.")
    e.responde("Sí, confirmo.")
    ids = [m.id for m in e.salida.tablet]
    assert ids == ["TAB-NOTA-00001-1", "TAB-NOTA-00001-2", "TAB-NOTA-00001-3"]


def test_una_respuesta_a_un_mensaje_viejo_se_ignora(e):
    primero = e.nota(NOTA_CLARA)
    e.responde("No. Y hay más cosas, revisalo todo.")
    antes = len(e.salida.tablet)
    e.responde("Sí, confirmo.", a=primero)
    assert len(e.salida.tablet) == antes
    assert e.confirmados == []


def test_la_misma_nota_dos_veces_no_duplica_el_borrador(e):
    e.nota(NOTA_CLARA)
    e.nota(NOTA_CLARA)
    assert len(e.salida.tablet) == 1


def test_el_estado_sobrevive_a_un_reinicio_del_agente():
    e1 = Entorno()
    e1.nota(NOTA_CLARA)
    e2 = Entorno(base=e1.base)  # proceso nuevo, misma base
    e2.dialogo._mensaje_nota.clear()
    ultimo = e1.salida.tablet[-1]
    e2.responde("Sí, confirmo.", a=ultimo)
    assert len(e2.confirmados) == 1


# -- notas inseguras -------------------------------------------------------------------------------


def test_s1_deja_una_fila_de_observacion_del_equipo_real_sin_esperar_respuesta(e):
    m = e.nota(S1, seguridad=True)
    assert not m.espera_respuesta
    (fila,) = m.pantalla.filas
    assert (fila.tag, fila.variable, fila.valor) == ("B-101", "condicion_insegura", None)
    assert e.confirmados == []


def test_s3_con_equipo_tambien_deja_la_fila(e):
    m = e.nota(S3, seguridad=True)
    assert m.pantalla.filas[0].tag == "B-102"


@pytest.mark.parametrize("texto", [S2, S4])
def test_s2_y_s4_no_muestran_filas_ni_inventan_equipo_d14(e, texto):
    assert e.nota(texto, seguridad=True) is None
    assert e.dialogo._estados["NOTA-00001"].estado is EstadoParte.sin_filas


def _reiniciar(e1):
    """Proceso nuevo con la misma base: no queda nada en memoria del anterior."""
    e2 = Entorno(base=e1.base)
    e2.salida = e1.salida  # la tablet es la misma
    e2.dialogo._salida = e1.salida
    e2.dialogo._mensaje_nota.clear()
    e2.dialogo._estados.clear()
    return e2


def test_reinicio_con_la_pregunta_de_equipo_sin_responder():
    e1 = Entorno()
    q = e1.nota("La bomba está en ciento quince de descarga.")
    assert q.pantalla.opciones == ["la uno", "la dos", "la tres"]
    e2 = _reiniciar(e1)
    borrador = e2.responde("Era la uno.")
    assert vistas(borrador) == [("B-101", "presion_descarga_local", 115.0, "kg/cm2")]
    e2.responde("Sí, confirmo.")
    assert len(e2.confirmados) == 1


def test_reinicio_con_el_valor_ambiguo_sin_responder():
    e1 = Entorno()
    e1.nota("El filtro B me dio dieciocho de diferencial, le saqué foto al manómetro.")
    e2 = _reiniciar(e1)
    e2.responde("Sí, confirmo.")
    (fila,) = e2.confirmados[0].filas
    assert fila.provisional is False


def test_reinicio_en_el_medio_de_las_correcciones_conserva_el_conteo_de_borradores():
    e1 = Entorno()
    e1.nota("La dos está en mil seiscientos cuarenta y cinco psi de descarga.")
    e1.responde(
        "No. La dos estaba en mil seiscientos cuarenta psi, no en mil seiscientos cuarenta y cinco. Corregilo y te confirmo."
    )
    e2 = _reiniciar(e1)
    est = e2.dialogo._cargar("NOTA-00001")
    assert est.borradores == 2  # el segundo borrador sobrevivió al reinicio
    e2.responde("Perfecto, confirmo.")
    assert e2.confirmados[0].filas[0].valor == 1640.0
    # y no se puede volver a confirmar: el estado ya es confirmado
    assert e2.dialogo._cargar("NOTA-00001").estado is EstadoParte.confirmado


def test_reinicio_despues_de_confirmar_ignora_una_confirmacion_repetida():
    e1 = Entorno()
    e1.nota(NOTA_CLARA)
    ultimo = e1.salida.tablet[-1]
    e1.responde("Sí, confirmo.")
    e2 = _reiniciar(e1)
    e2.responde("Sí, confirmo.", a=ultimo)
    assert len(e1.confirmados) == 1 and e2.confirmados == []  # los eventos no se duplican
