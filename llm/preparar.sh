#!/bin/sh
# Paso del build (con Internet): baja los modelos de Ollama y escribe /modelos/manifest.json con el digest de cada capa.
# El digest sale del manifest del registro y se verifica contra el sha256 real del archivo bajado. En runtime no se
# descarga nada (ver arranque.sh).
set -eu

export OLLAMA_MODELS=/modelos
ollama serve >/tmp/ollama-build.log 2>&1 &
PID=$!
for _ in $(seq 1 60); do
  ollama list >/dev/null 2>&1 && break
  sleep 1
done

for M in $MODELOS; do
  echo "== pull $M"
  ollama pull "$M"
done
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

OUT=/modelos/manifest.json
printf '{\n  "ollama": "%s",\n  "modelos": {\n' "${OLLAMA_VERSION:-desconocida}" > "$OUT"
PRIMERO=1
for M in $MODELOS; do
  NOMBRE=${M%%:*}
  TAG=${M##*:}
  MAN=/modelos/manifests/registry.ollama.ai/library/$NOMBRE/$TAG
  [ -f "$MAN" ] || { echo "falta el manifest de $M" >&2; exit 1; }
  [ $PRIMERO -eq 1 ] || printf ',\n' >> "$OUT"
  PRIMERO=0
  printf '    "%s": {"manifest_sha256": "%s", "capas": [' "$M" "$(sha256sum "$MAN" | cut -d' ' -f1)" >> "$OUT"
  SEP=""
  for D in $(grep -o 'sha256:[0-9a-f]\{64\}' "$MAN" | sort -u); do
    HEX=${D#sha256:}
    BLOB=/modelos/blobs/sha256-$HEX
    [ -f "$BLOB" ] || { echo "falta la capa $D de $M" >&2; exit 1; }
    REAL=$(sha256sum "$BLOB" | cut -d' ' -f1)
    [ "$REAL" = "$HEX" ] || { echo "sha256 NO coincide en $M: esperado $HEX, real $REAL" >&2; exit 1; }
    printf '%s{"digest": "%s", "bytes": %s}' "$SEP" "$D" "$(wc -c < "$BLOB")" >> "$OUT"
    SEP=", "
  done
  printf ']}' >> "$OUT"
done
printf '\n  }\n}\n' >> "$OUT"
cat "$OUT"
