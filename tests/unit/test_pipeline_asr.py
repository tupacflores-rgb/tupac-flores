"""Cliente del ASR y pipeline de una nota: descarga → transcripción → seguridad."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import httpx
import pytest
from planta_contrato import NotaRecorrida, TipoEvento, Topicos
from planta_falsa import DEFINICION, S1

from sistema.deteccion.seguridad import detectar
from sistema.dominio.catalogo import Catalogo
from sistema.integraciones.asr import ClienteAsr, Transcripcion
from sistema.pipeline import Pipeline

CAT = Catalogo(DEFINICION)
NOTA = NotaRecorrida(
    id="NOTA-00001",
    ts=datetime(2026, 3, 4, 7, 15, tzinfo=UTC),
    operador="Héctor Llancafil",
    audio_url="/media/audios/NOTA-00001.wav",
    fotos=[],
    recorrida_id="REC-1",
)


# -- cliente del ASR --------------------------------------------------------------------------------


def cliente_asr(handler):
    http = httpx.Client(base_url="http://asr:8000", transport=httpx.MockTransport(handler))
    return ClienteAsr("http://asr:8000", http=http)


def test_el_asr_esta_listo_solo_con_salud_200():
    estados = iter([503, 200])
    asr = cliente_asr(lambda req: httpx.Response(next(estados)))
    assert asr.listo() is False
    asr._ultimo_chequeo = 0  # saltea el límite de una consulta por segundo
    assert asr.listo() is True
    assert asr.listo() is True  # ya no se vuelve a consultar


def test_el_asr_no_listo_si_no_responde():
    def caido(req):
        raise httpx.ConnectError("sin conexión")

    assert cliente_asr(caido).listo() is False


def test_transcribir_envia_el_audio_y_lee_el_texto():
    recibido = {}

    def handler(req):
        recibido["cuerpo"] = req.content
        return httpx.Response(
            200, json={"texto": "la dos está parada", "duracion_s": 3.1, "tiempo_s": 1.2, "rtf": 0.39}
        )

    t = cliente_asr(handler).transcribir(b"RIFF....")
    assert recibido["cuerpo"] == b"RIFF...."
    assert t == Transcripcion("la dos está parada", 3.1, 1.2)


# -- pipeline ---------------------------------------------------------------------------------------


class Api:
    def __init__(self, falla=0):
        self.falla = falla
        self.descargas = []

    def descargar(self, ruta, **_):
        self.descargas.append(ruta)
        if self.falla:
            self.falla -= 1
            raise httpx.ConnectError("sin conexión")
        return b"audio"


class Asr:
    def __init__(self, texto):
        self.texto = texto

    def transcribir(self, audio, carril="completa"):
        self.carriles = getattr(self, "carriles", []) + [carril]
        texto = self.texto[carril] if isinstance(self.texto, dict) else self.texto
        return Transcripcion(texto, 9.3, 4.0)


class SalidaFalsa:
    def __init__(self):
        self.lotes = []

    def emitir_lote(self, mensajes):
        self.lotes.append(mensajes)
        return True


def armar(texto, api=None, **kw):
    salida, guardadas, posteriores = SalidaFalsa(), [], []
    pipe = Pipeline(
        api or Api(),
        Asr(texto),
        lambda nota_id, t: guardadas.append((nota_id, t)),
        salida,
        lambda: CAT,
        lambda nota, t, det: posteriores.append((nota.id, t, det)),
        **kw,
    )
    return pipe, salida, guardadas, posteriores


def test_nota_insegura_emite_alerta_y_aviso_y_sigue_al_parte():
    pipe, salida, guardadas, posteriores = armar(S1)
    pipe.atender(NOTA)
    assert guardadas == [("NOTA-00001", S1)]
    (lote,) = salida.lotes
    (id_ev, top_ev, ev), (id_av, top_av, av) = lote
    assert (id_ev, top_ev, id_av, top_av) == (
        "EV-SEG-NOTA-00001",
        Topicos.EVENTOS,
        "TAB-SEG-NOTA-00001",
        Topicos.TABLET,
    )
    assert ev.tipo is TipoEvento.alerta_seguridad
    assert ev.equipo == "B-101"
    assert posteriores[0][2].codigo == "S1"


def test_nota_normal_no_genera_alerta():
    pipe, salida, _, posteriores = armar("La dos pierde bastante por la empaquetadura, le saqué foto.")
    pipe.atender(NOTA)
    assert salida.lotes == []
    assert posteriores[0][2] is None


def test_nota_sin_audio_no_rompe():
    nota = NOTA.model_copy(update={"audio_url": None})
    pipe, salida, _, posteriores = armar("irrelevante")
    pipe.atender(nota)
    assert salida.lotes == []
    assert posteriores[0][1] == ""


def test_reintenta_si_falla_la_descarga_y_termina_procesando():
    api = Api(falla=2)
    pipe, salida, *_ = armar(S1, api=api)
    pipe._max_intentos = 5
    parar = threading.Event()
    pipe.encolar(NOTA)
    hilo = threading.Thread(target=pipe.correr, args=(parar,), daemon=True)
    # sin esperas reales entre reintentos
    parar_wait = parar.wait
    parar.wait = lambda t=None: parar_wait(0.01)
    hilo.start()
    for _ in range(300):
        if salida.lotes:
            break
        threading.Event().wait(0.01)
    parar.set()
    assert len(salida.lotes) == 1
    assert len(api.descargas) == 3


def test_se_rinde_tras_el_maximo_de_intentos_sin_colgar():
    api = Api(falla=99)
    pipe, salida, *_ = armar(S1, api=api, max_intentos=2)
    parar = threading.Event()
    parar_wait = parar.wait
    parar.wait = lambda t=None: parar_wait(0.01)
    pipe.encolar(NOTA)
    hilo = threading.Thread(target=pipe.correr, args=(parar,), daemon=True)
    hilo.start()
    for _ in range(300):
        if len(api.descargas) >= 2 and pipe.pendientes() == 0:
            break
        threading.Event().wait(0.01)
    parar.set()
    assert len(api.descargas) == 2
    assert salida.lotes == []


@pytest.mark.parametrize("texto", [S1])
def test_el_detector_coincide_con_lo_que_publica_el_pipeline(texto):
    assert detectar(texto, CAT).codigo == "S1"


def _wav(segundos: float) -> bytes:
    import io
    import wave

    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * segundos))
    return b.getvalue()


def test_la_cola_de_seguridad_atiende_primero_la_nota_mas_corta_y_respeta_el_tope_de_espera():
    import time

    from sistema import pipeline as mod

    audios = {"/larga.wav": _wav(20), "/corta.wav": _wav(5), "/media.wav": _wav(10)}

    class ApiWav:
        def descargar(self, ruta, **_):
            return audios[ruta]

    def nota(nid, ruta):
        return NotaRecorrida(id=nid, ts=NOTA.ts, operador="x", audio_url=ruta, fotos=[], recorrida_id="R")

    cola = mod._Cola(ApiWav(), corta_primero=True)
    for nid, ruta in (("L", "/larga.wav"), ("C", "/corta.wav"), ("M", "/media.wav")):
        cola.poner(mod._Pendiente(nota(nid, ruta), 1, time.monotonic()))
    assert [cola.siguiente().nota.id for _ in range(3)] == ["C", "M", "L"]

    cola.poner(mod._Pendiente(nota("L2", "/larga.wav"), 1, time.monotonic() - mod.ESPERA_MAX_S - 1))
    cola.poner(mod._Pendiente(nota("C2", "/corta.wav"), 1, time.monotonic()))
    assert cola.siguiente().nota.id == "L2"

    fifo = mod._Cola(ApiWav(), corta_primero=False)
    for nid, ruta in (("L", "/larga.wav"), ("C", "/corta.wav")):
        fifo.poner(mod._Pendiente(nota(nid, ruta), 1, time.monotonic()))
    assert fifo.siguiente().nota.id == "L"


def test_dos_pasadas_alertan_una_sola_vez_y_la_rapida_alcanza_sola():
    # La pasada rápida entiende la frase y la completa no: alerta igual, y no se duplica si ambas la entienden.
    pipe, salida, _, posteriores = armar({"seguridad": S1, "completa": "La uno hace un ruido raro."})
    pipe.atender_seguridad(NOTA)
    pipe.atender(NOTA)
    assert len(salida.lotes) == 1
    assert posteriores[0][2].codigo == "S1"  # el parte conoce la detección de la pasada rápida

    pipe2, salida2, *_ = armar({"seguridad": S1, "completa": S1})
    pipe2.atender_seguridad(NOTA)
    pipe2.atender(NOTA)
    assert len(salida2.lotes) == 1

    pipe3, salida3, *_ = armar({"seguridad": "Se escucha un golpeteo.", "completa": S1})
    pipe3.atender_seguridad(NOTA)
    pipe3.atender(NOTA)
    assert len(salida3.lotes) == 1  # la completa también puede dar la alerta


def test_la_pasada_completa_se_corta_por_presupuesto_y_no_se_reintenta():
    from sistema.control import CorteDePresupuesto, Presupuesto, RegistroConsumo

    reg = RegistroConsumo()
    pipe, salida, *_ = armar("La uno hace un ruido.", presupuesto=Presupuesto(max_pasos=1), registro=reg)
    with pytest.raises(CorteDePresupuesto):
        pipe.atender(NOTA)
    assert reg.totales()["cortes"] == 1


def test_la_pasada_de_seguridad_no_se_corta_aunque_el_presupuesto_sea_minimo():
    from sistema.control import Presupuesto, RegistroConsumo

    reg = RegistroConsumo()
    pipe, salida, *_ = armar(S1, presupuesto=Presupuesto(max_pasos=1, timeout_s=0.0), registro=reg)
    pipe.atender_seguridad(NOTA)
    assert len(salida.lotes) == 1  # la alerta salió igual
    assert reg.filas[0].carril == "seguridad" and reg.filas[0].corte is not None
