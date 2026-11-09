# syntax=docker/dockerfile:1.7
# Servicio de embeddings (multilingual-e5-small, ONNX) + carga en pgvector. Se construye desde la raíz del repo:
#   docker build -f docker/emb.Dockerfile .
#
# Etapa 1 (con Internet): baja el modelo con revisión fija y verifica su sha256.
# Etapa 2: calcula los embeddings de los pasajes del corpus (con y sin prefijo `passage: `).
# Etapa 3: runtime offline; carga los pasajes en pgvector al arrancar.

FROM python:3.12-slim AS modelo
ARG MODELO_REPO=intfloat/multilingual-e5-small
ARG MODELO_REV=614241f622f53c4eeff9890bdc4f31cfecc418b3
ARG MODELO_SHA256=ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665
ENV MODELO_REPO=$MODELO_REPO MODELO_REV=$MODELO_REV MODELO_SHA256=$MODELO_SHA256 MODELO_DESTINO=/modelos/e5
RUN pip install --no-cache-dir huggingface_hub==0.26.2
COPY emb/descargar_modelo.py /tmp/descargar_modelo.py
RUN python /tmp/descargar_modelo.py

FROM python:3.12-slim AS base
RUN pip install --no-cache-dir onnxruntime==1.20.1 tokenizers==0.21.0 numpy==2.1.3 \
    "psycopg[binary]==3.2.3" fastapi==0.115.6 uvicorn==0.32.1 pydantic==2.10.3
COPY --from=modelo /modelos /modelos
COPY emb/nucleo.py emb/indexar.py emb/servidor.py /app/
COPY src/sistema/conocimiento/corpus.py /app/corpus.py
COPY src/sistema/datos/documentos /datos/documentos
WORKDIR /app
ENV MODELO_RUTA=/modelos/e5 PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN python indexar.py /datos/documentos/fragmentos.jsonl /modelos/pasajes.npz

FROM base
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PASAJES=/modelos/pasajes.npz DATOS=/datos/documentos
RUN useradd --system --uid 10003 emb
USER emb
EXPOSE 8000
CMD ["uvicorn", "servidor:app", "--host", "0.0.0.0", "--port", "8000"]
