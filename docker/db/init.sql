-- Se ejecuta al crear el volumen. Python reutiliza el mismo esquema en cada arranque del agente.
CREATE EXTENSION IF NOT EXISTS vector;
\ir /docker-entrypoint-initdb.d/10-esquema.inc
