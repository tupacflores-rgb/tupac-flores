#!/bin/sh
# Arranque del servicio llm: verifica que las capas de los modelos coinciden con el manifest.json del build (tamaño y
# sha256) y recién entonces levanta Ollama. Nada se descarga en runtime.
set -eu

export OLLAMA_MODELS=/modelos
for HEX in $(grep -o '"digest": "sha256:[0-9a-f]\{64\}"' /modelos/manifest.json | grep -o '[0-9a-f]\{64\}' | sort -u); do
  BLOB=/modelos/blobs/sha256-$HEX
  [ -f "$BLOB" ] || { echo "llm: falta la capa $HEX" >&2; exit 1; }
  REAL=$(sha256sum "$BLOB" | cut -d' ' -f1)
  [ "$REAL" = "$HEX" ] || { echo "llm: la capa $HEX fue alterada (sha256 real $REAL)" >&2; exit 1; }
done
echo "llm: capas verificadas contra manifest.json"

ollama serve &
PID=$!
for _ in $(seq 1 60); do
  ollama list >/dev/null 2>&1 && break
  sleep 1
done
# Precarga del modelo principal: la primera nota real no paga la carga de los pesos.
if [ -n "${MODELO:-}" ]; then
  # Sin razonamiento (think=false): a esta cuota cada token cuesta segundos y la precarga solo carga los pesos.
  ollama run "$MODELO" --think=false --keepalive 24h "ok" >/dev/null 2>&1 \
    || ollama run "$MODELO" --keepalive 24h "ok" >/dev/null 2>&1 \
    || echo "llm: no se pudo precargar $MODELO" >&2
fi
echo "llm: listo"
wait "$PID"
