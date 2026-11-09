# PostgreSQL 16 con pgvector. La extensión se crea al inicializar el volumen.
FROM pgvector/pgvector:pg16

COPY init.sql /docker-entrypoint-initdb.d/00-extension-vector.sql
