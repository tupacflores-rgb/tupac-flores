"""Anomalía sostenida de sensores: ventana, histéresis, pico aislado, reserva ignorada y nunca AlertaSeguridad."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from planta_contrato import TipoEvento

from sistema.deteccion.sensores import DetectorSostenido, Parametros

T0 = datetime(2026, 3, 4, 7, 0, tzinfo=UTC)
LIMITES = {("B-101", "presion_descarga"): (95.0, 125.0), ("F-201A", "presion_diferencial"): (0.15, 1.5)}


class Entorno:
    def __init__(self, estados=None, **kw):
        self.estados = estados or {}
        self.emitidos = []
        self.det = DetectorSostenido(
            LIMITES,
            lambda tag: self.estados.get(tag, "marcha"),
            lambda ev: self.emitidos.append(ev) or True,
            Parametros(**kw),
        )

    def lectura(self, minuto, valor, tag="B-101", variable="presion_descarga", calidad="buena"):
        return self.det.procesar(
            {
                "tag": tag,
                "variable": variable,
                "valor": valor,
                "unidad": "kg/cm2",
                "ts": (T0 + timedelta(minutes=minuto)).isoformat(),
                "calidad": calidad,
            }  # fmt: skip
        )

    def serie(self, desde, hasta, valor, **kw):
        for m in range(desde, hasta + 1):
            self.lectura(m, valor, **kw)


def test_un_pico_aislado_no_escala():
    e = Entorno()
    e.serie(0, 4, 110)
    e.lectura(5, 140)  # una sola muestra fuera
    e.serie(6, 20, 110)
    assert e.emitidos == []


def test_un_exceso_corto_no_alcanza_la_ventana():
    e = Entorno()
    e.serie(0, 3, 140)  # 3 min fuera: menos que los 5 de la ventana
    e.serie(4, 10, 110)
    assert e.emitidos == []


def test_anomalia_sostenida_escala_una_sola_vez_con_id_determinista():
    e = Entorno()
    e.serie(0, 2, 110)
    e.serie(3, 12, 140)  # 10 min fuera
    (ev,) = e.emitidos
    assert ev.tipo is TipoEvento.escalado and ev.origen == "sensor" and ev.equipo == "B-101"
    assert ev.nota_ids == []
    assert ev.id == "EV-SEN-B-101-presion_descarga-20260304T070300"
    assert "fuera de límites" in ev.resumen


def test_la_histeresis_evita_repetir_el_evento_si_oscila_pegado_al_limite():
    e = Entorno()
    e.serie(0, 8, 140)  # abre la anomalía
    for m in range(9, 40):  # oscila entre 124 y 126: vuelve «adentro» pero sin margen
        e.lectura(m, 124 if m % 2 else 126)
    assert len(e.emitidos) == 1


def test_se_rearma_cuando_vuelve_adentro_con_margen_y_una_anomalia_nueva_es_otro_evento():
    e = Entorno()
    e.serie(0, 8, 140)
    e.serie(9, 12, 100)  # bien adentro durante varias lecturas: se rearma
    e.serie(13, 25, 140)  # segunda anomalía
    assert [ev.id for ev in e.emitidos] == [
        "EV-SEN-B-101-presion_descarga-20260304T070000",
        "EV-SEN-B-101-presion_descarga-20260304T071300",
    ]


def test_baja_sostenida_tambien_escala():
    e = Entorno()
    e.serie(0, 8, 80)  # < 95
    assert len(e.emitidos) == 1


def test_una_bomba_parada_en_reserva_se_ignora():
    e = Entorno(estados={"B-101": "parada"})
    e.serie(0, 30, 0.0)  # presión cero: es lo normal en una reserva
    assert e.emitidos == []
    e.estados["B-101"] = "mantenimiento"
    e.serie(31, 60, 0.0)
    assert e.emitidos == []


def test_una_bomba_que_arranca_despues_de_parada_empieza_la_cuenta_de_cero():
    e = Entorno(estados={"B-101": "parada"})
    e.serie(0, 10, 0.0)
    e.estados["B-101"] = "marcha"
    e.serie(11, 13, 0.0)  # 3 min con baja presión recién arrancada: todavía no es sostenido
    assert e.emitidos == []


def test_lecturas_de_mala_calidad_reinician_la_cuenta():
    e = Entorno()
    e.serie(0, 3, 140)
    e.lectura(4, 140, calidad="mala")
    e.serie(5, 7, 140)  # la cuenta arranca de nuevo: 2 min
    assert e.emitidos == []


def test_sensor_sin_limites_o_sin_valor_se_ignora():
    e = Entorno()
    assert e.lectura(0, 140, variable="desconocida") is None
    assert e.det.procesar({"tag": "B-101", "variable": "presion_descarga", "valor": None, "ts": T0.isoformat(),
                           "calidad": "buena"}) is None  # fmt: skip


def test_los_sensores_nunca_producen_alerta_de_seguridad():
    e = Entorno()
    e.serie(0, 60, 500)  # absurdamente alto durante una hora
    e.serie(0, 60, 0.01, tag="F-201A", variable="presion_diferencial")
    assert e.emitidos
    assert {ev.tipo for ev in e.emitidos} == {TipoEvento.escalado}
    assert all(ev.origen == "sensor" for ev in e.emitidos)
