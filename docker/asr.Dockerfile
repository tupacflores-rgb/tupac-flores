# syntax=docker/dockerfile:1.7
# Servicio ASR. Se construye desde la raíz del repo: docker build -f docker/asr.Dockerfile .
#
# Etapa 1 (con Internet): baja el modelo con revisión fija y verifica su sha256; genera manifest.json.
# Etapa 2: runtime offline. Nunca se descarga nada al arrancar.

FROM python:3.12-slim AS modelo
# Systran/faster-whisper-small (licencia MIT). El hash es el LFS oid (sha256) de model.bin en esa revisión.
ARG MODELO_REPO=Systran/faster-whisper-small
ARG MODELO_REV=536b0662742c02347bc0e980a01041f333bce120
ARG MODELO_SHA256=3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671
ENV MODELO_REPO=$MODELO_REPO MODELO_REV=$MODELO_REV MODELO_SHA256=$MODELO_SHA256 MODELO_DESTINO=/modelos/small
RUN pip install --no-cache-dir huggingface_hub==0.26.2
COPY asr/descargar_modelo.py /tmp/descargar_modelo.py
RUN python /tmp/descargar_modelo.py

FROM python:3.12-slim
# Dependencias fijadas: faster-whisper 1.1.0 declara `av>=11` sin tope (PyAV 19 ya no acepta lo que usa) y las
# versiones nuevas de huggingface_hub dejaron de traer `requests`, que importa.
RUN pip install --no-cache-dir av==12.3.0 huggingface_hub==0.26.2 faster-whisper==1.1.0 fastapi==0.115.6 uvicorn==0.32.1
COPY --from=modelo /modelos /modelos
COPY asr/servidor.py /app/servidor.py

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MODELO_RUTA=/modelos/small

RUN useradd --system --uid 10002 asr
USER asr
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "servidor:app", "--host", "0.0.0.0", "--port", "8000"]
