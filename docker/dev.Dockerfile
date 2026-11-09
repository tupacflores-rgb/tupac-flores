# syntax=docker/dockerfile:1.7
# Contenedor de desarrollo: ruff, pytest y pre-commit corren acá, no en el host (Plan Maestro §14).
# El código se monta en /work en tiempo de ejecución; en la imagen solo quedan las dependencias.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install ".[dev]" \
    && pip uninstall -y tupac-flores

RUN git config --system --add safe.directory /work

ENV PYTHONPATH=/work/src \
    PYTHONDONTWRITEBYTECODE=1 \
    RUFF_CACHE_DIR=/tmp/ruff \
    PYTEST_ADDOPTS="-p no:cacheprovider"
WORKDIR /work
