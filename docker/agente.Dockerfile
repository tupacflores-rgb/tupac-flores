# syntax=docker/dockerfile:1.7
# Imagen del agente. Se construye desde la raíz del repo: docker build -f docker/agente.Dockerfile .
FROM python:3.12-slim AS construccion

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim

COPY --from=construccion /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --system --uid 10001 sistema \
    && mkdir -p /var/lib/sistema/journal \
    && chown -R sistema /var/lib/sistema
USER sistema

CMD ["python", "-m", "sistema"]
