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

-- Estado del diálogo con el operador por nota (sobrevive a un reinicio del agente).
CREATE TABLE IF NOT EXISTS dialogo (
    nota_id     text PRIMARY KEY,
    estado      jsonb NOT NULL,
    actualizado timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS mensaje_nota (
    mensaje_id text PRIMARY KEY,
    nota_id    text NOT NULL
);
ALTER TABLE mensaje_nota ADD COLUMN IF NOT EXISTS creado timestamptz NOT NULL DEFAULT now();

-- Consumo por nota y carril (pasos, tokens, milisegundos, motivo de corte). Ver control/presupuesto.py.
CREATE TABLE IF NOT EXISTS consumo (
    id         bigserial PRIMARY KEY,
    nota_id    text NOT NULL,
    carril     text NOT NULL,
    pasos      integer NOT NULL,
    tokens_in  integer NOT NULL DEFAULT 0,
    tokens_out integer NOT NULL DEFAULT 0,
    ms         integer NOT NULL,
    corte      text,
    ts         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS consumo_nota ON consumo (nota_id);

-- Fotos de las notas: descargadas a un volumen con su sha256, o pendientes con la URL guardada. Ver fotos.py.
CREATE TABLE IF NOT EXISTS foto (
    url          text PRIMARY KEY,
    nota_id      text NOT NULL,
    estado       text NOT NULL DEFAULT 'pendiente',
    sha256       text,
    ruta         text,
    bytes        integer,
    intentos     integer NOT NULL DEFAULT 0,
    ultimo_error text,
    creado       timestamptz NOT NULL DEFAULT now(),
    actualizado  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS foto_pendientes ON foto (estado) WHERE estado <> 'descargada';

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
