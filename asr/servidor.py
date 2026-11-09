"""Servicio de transcripción local (faster-whisper, CPU, int8).

Vive en su propio contenedor con cuota propia de CPU para que la transcripción no le quite tiempo al agente
ni al latido. Dos carriles (seguridad y completa), un trabajo a la vez en cada uno y sin LLM de por medio:
el camino de la alerta de seguridad es descarga → transcripción → léxico.

  GET  /salud        200 solo cuando el modelo está cargado y calentado
  POST /transcribir  cuerpo = audio (WAV/FLAC/...), devuelve {texto, duracion_s, tiempo_s, rtf}
"""

from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s asr: %(message)s")
log = logging.getLogger("asr")

RUTA_MODELO = os.environ.get("MODELO_RUTA", "/modelos/small")
# Dos carriles que corren a la vez (pasada de seguridad y pasada completa), cada uno con sus hilos: la cuota
# del contenedor es la suma. Un trabajo en curso no se puede interrumpir, por eso la nota insegura no espera
# a la larga.
HILOS = int(os.environ.get("ASR_HILOS", "1"))
# Plazas por carril: la pasada de seguridad tiene dos, para que una nota insegura no espere a que termine otra
# que ya está en curso. Tres hilos en total sobre la cuota de 2,0 CPU del contenedor.
PLAZAS = {"seguridad": 2, "completa": 1}
CARRILES = tuple(PLAZAS)
BEAM = int(os.environ.get("ASR_BEAM", "5"))
# Vocabulario de planta: ayuda a Whisper con los nombres de los equipos y a escribir los números de forma
# consistente. Se mide en evaluacion/asr_wer.py.
PROMPT = os.environ.get(
    "ASR_PROMPT",
    "Nota de recorrida de la planta de inyección. La uno, la dos, la tres, el filtro A, el filtro B, "
    "el pulmón. Presión de descarga en kilos, psi y bar. Diferencial uno ocho. "
    "Empaquetadura, rodamiento, correas.",
)
# Vocabulario de emergencia: sesga a Whisper hacia las palabras que no puede errar en la pasada de seguridad.
PROMPT_SEGURIDAD = os.environ.get(
    "ASR_PROMPT_SEGURIDAD",
    "Emergencia en planta. Se rompió algo en la descarga, sale agua a presión. Olor a gas fuerte. "
    "Sale humo del motor, olor a quemado. Compañero herido, se golpeó la mano, está sangrando.",
)

app = FastAPI(title="ASR local")
_modelo: WhisperModel | None = None
_listo = threading.Event()
_carril = {c: threading.BoundedSemaphore(n) for c, n in PLAZAS.items()}


def _transcribir(
    audio: np.ndarray, prompt: str | None = None, beam: int | None = None, rapida: bool = False
) -> str:
    """`prompt` y `beam` solo se pisan para evaluar configuraciones (evaluacion/asr_wer.py).

    La pasada rápida (seguridad) usa beam 1, sin marcas de tiempo y el prompt de emergencia."""
    if prompt is None:
        prompt = PROMPT_SEGURIDAD if rapida else PROMPT
    segmentos, _ = _modelo.transcribe(
        audio,
        language="es",
        beam_size=1 if rapida else (beam or BEAM),
        temperature=0.0,
        condition_on_previous_text=False,
        initial_prompt=prompt or None,
        vad_filter=False,
        without_timestamps=rapida,
    )
    return " ".join(s.text.strip() for s in segmentos).strip()


def _cargar() -> None:
    global _modelo
    t0 = time.perf_counter()
    manifest = Path(RUTA_MODELO) / "manifest.json"
    if manifest.exists():
        m = json.loads(manifest.read_text(encoding="utf-8"))
        log.info(
            "modelo %s revisión %s (sha256 de model.bin %s)",
            m["id"],
            m["revision"][:12],
            m["archivos"]["model.bin"]["sha256"][:12],
        )
    _modelo = WhisperModel(
        RUTA_MODELO,
        device="cpu",
        compute_type="int8",
        cpu_threads=HILOS,
        num_workers=sum(PLAZAS.values()),
        local_files_only=True,
    )
    # Calentamiento: una inferencia corta para que la primera nota real no pague la inicialización.
    _transcribir(np.zeros(16000, dtype=np.float32))
    _listo.set()
    log.info("modelo cargado y calentado en %.1f s", time.perf_counter() - t0)


@app.on_event("startup")
def _arranque() -> None:
    threading.Thread(target=_cargar, name="carga-modelo", daemon=True).start()


@app.get("/salud")
def salud() -> Response:
    if not _listo.is_set():
        raise HTTPException(503, "modelo cargándose")
    return Response(status_code=200, content="ok")


@app.post("/transcribir")
async def transcribir(
    request: Request, prompt: str | None = None, beam: int | None = None, carril: str = "completa"
) -> dict:
    if not _listo.is_set():
        raise HTTPException(503, "modelo cargándose")
    cuerpo = await request.body()
    if not cuerpo:
        raise HTTPException(400, "audio vacío")
    try:
        audio = decode_audio(io.BytesIO(cuerpo), sampling_rate=16000)
    except Exception as e:
        raise HTTPException(422, f"audio ilegible: {e}") from e
    if carril not in _carril:
        raise HTTPException(400, f"carril desconocido: {carril}")
    # Las plazas de cada carril corren en paralelo con el mismo modelo.
    with _carril[carril]:
        t0 = time.perf_counter()
        texto = _transcribir(audio, prompt, beam, rapida=carril == "seguridad")
        tiempo = time.perf_counter() - t0
    duracion = len(audio) / 16000
    log.info(
        "[%s] transcripto %.1f s de audio en %.2f s (RTF %.2f)",
        carril,
        duracion,
        tiempo,
        tiempo / max(duracion, 0.01),
    )
    return {"texto": texto, "duracion_s": round(duracion, 2), "tiempo_s": round(tiempo, 2),
            "rtf": round(tiempo / max(duracion, 0.01), 3)}  # fmt: skip
