#!/bin/sh
# Verificación de integración de la infraestructura (corre en el host, con la planta ya levantada):
#   docker compose -p planta -f <repo de la planta>/docker-compose.yml up -d --build
#   sh scripts/verificar_infra.sh
# Comprueba que db levanta sana, que tiene la extensión vector y que respeta red, label y límites.
set -e
cd "$(git rev-parse --show-toplevel)"

docker network inspect planta-lan --format 'planta-lan internal={{.Internal}}' | grep -q 'internal=true' \
  || { echo "FALLA: planta-lan no existe o no es interna (levantar la planta primero)" >&2; exit 1; }

docker compose up -d --wait db
db=$(docker compose ps -q db)

ext=$(docker exec "$db" psql -U sistema -d sistema -tAc "select extname from pg_extension where extname='vector'")
[ "$ext" = "vector" ] || { echo "FALLA: falta la extensión vector" >&2; exit 1; }

docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$db" | grep -qx 'planta-lan ' \
  || { echo "FALLA: db debe estar solo en planta-lan" >&2; exit 1; }
[ "$(docker inspect -f '{{index .Config.Labels "academia.planta"}}' "$db")" = "pc" ] \
  || { echo "FALLA: db sin label academia.planta=pc" >&2; exit 1; }

echo "OK: db sana, extensión vector presente, solo en planta-lan, label pc"
