# PostgreSQL 16 con pgvector. La extensión se crea al inicializar el volumen.
FROM pgvector/pgvector:pg16

COPY docker/db/init.sql /docker-entrypoint-initdb.d/00-init.sql
COPY src/sistema/persistencia/esquema.sql /docker-entrypoint-initdb.d/10-esquema.inc
