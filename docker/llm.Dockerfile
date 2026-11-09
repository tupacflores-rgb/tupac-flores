# syntax=docker/dockerfile:1.7
# Servicio llm (Ollama): respaldo del parser y del intérprete de respuestas. Se construye desde la raíz del repo:
#   docker build -f docker/llm.Dockerfile .                                   # servicio: un solo modelo
#   docker build -f docker/llm.Dockerfile -t tupac-flores-llm:bench \
#       --build-arg MODELOS="qwen3.5:2b qwen3.5:0.8b qwen2.5:1.5b" .         # benchmark: los tres candidatos
#
# Etapa 1 (con Internet): `ollama serve` en segundo plano + `ollama pull`; escribe manifest.json con el digest de
# cada capa, verificado contra el sha256 real. Etapa 2: runtime offline; arranque.sh vuelve a verificar las capas.

ARG OLLAMA_VERSION=0.40.15

FROM ollama/ollama:${OLLAMA_VERSION} AS modelos
ARG OLLAMA_VERSION
# La imagen del servicio lleva SOLO el modelo elegido (el build de Novit baja lo que lleva). El benchmark pasa los
# tres candidatos con --build-arg y los guarda con otro tag (tupac-flores-llm:bench); ver docs/resultados/llm.md.
ARG MODELOS="qwen3.5:2b"
ENV MODELOS=${MODELOS} OLLAMA_VERSION=${OLLAMA_VERSION}
COPY llm/preparar.sh /preparar.sh
RUN mkdir -p /modelos && sh /preparar.sh

FROM ollama/ollama:${OLLAMA_VERSION}
COPY --from=modelos /modelos /modelos
COPY llm/arranque.sh /arranque.sh
ENV OLLAMA_MODELS=/modelos \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_NUM_PARALLEL=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    OLLAMA_KEEP_ALIVE=24h \
    OLLAMA_NOPRUNE=1 \
    OLLAMA_NO_CLOUD=1
EXPOSE 11434
ENTRYPOINT ["/bin/sh", "/arranque.sh"]
