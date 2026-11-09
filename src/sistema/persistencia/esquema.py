"""Esquema de la base. Es idempotente: el agente lo aplica en cada arranque."""

ESQUEMA = """
-- Notas de recorrida recibidas. La clave primaria deduplica las entregas repetidas del broker (QoS 1).
CREATE TABLE IF NOT EXISTS nota (
    nota_id       text PRIMARY KEY,
    ts            timestamptz NOT NULL,
    recorrida_id  text,
    audio_url     text,
    fotos         jsonb NOT NULL DEFAULT '[]',
    recibida_en   timestamptz NOT NULL DEFAULT now(),
    transcripcion text,
    estado        text NOT NULL DEFAULT 'recibida'
);

-- Respuestas del operador en la tablet, también deduplicadas.
CREATE TABLE IF NOT EXISTS respuesta (
    id          text PRIMARY KEY,
    mensaje_id  text NOT NULL,
    ts          timestamptz NOT NULL,
    texto       text NOT NULL,
    recibida_en timestamptz NOT NULL DEFAULT now()
);

-- Cola de salida (intención -> publicación -> marca). Ver persistencia/salida.py.
CREATE TABLE IF NOT EXISTS salida (
    id        text PRIMARY KEY,
    topico    text NOT NULL,
    payload   jsonb NOT NULL,
    estado    text NOT NULL DEFAULT 'a_publicar',
    creado    timestamptz NOT NULL DEFAULT now(),
    publicado timestamptz
);
CREATE INDEX IF NOT EXISTS salida_pendientes ON salida (estado) WHERE estado <> 'publicada';
"""
