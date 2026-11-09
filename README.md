# tupac-flores · Academia Novit 2026 · TP Planta de inyección

Sistema que se conecta a la planta simulada de Novit: recibe las notas de voz del operador, arma el parte por la
tablet, detecta condiciones inseguras y publica eventos. **Entrega 1:** sistema conectado, parte por voz y detección,
tools y MCP, RAG medido. Resultados y decisiones: [INFORME.md](INFORME.md).

## Cómo levantarlo

Requiere la planta de Novit levantada (crea la red externa `planta-lan`):

```bash
docker compose -p planta -f <repo de la planta>/docker-compose.yml up -d --build
docker compose up -d --build        # agente, asr, emb y db
```

| Servicio | Qué hace | CPU / memoria |
|---|---|---|
| `agente` | MQTT, pipeline de notas, diálogo con la tablet, detección, latido cada 30 s | 0,5 / 1 GB |
| `asr` | faster-whisper `small` local, dos carriles (seguridad rápida y completa) | 2,0 / 2 GB |
| `emb` | embeddings e5-small (ONNX) y búsqueda en pgvector | 0,5 / 1 GB |
| `db` | PostgreSQL 16 con pgvector | 0,5 / 1 GB |

Los cuatro van solo en `planta-lan`, llevan el label `academia.planta: pc`, suman 3,5 de 4 CPU y 5 de 8 GB, y salen a
internet por `HTTP(S)_PROXY=http://enlace:3128`. Los modelos viven dentro de las imágenes; no se descarga nada al
arrancar. El `client_id` MQTT es `<proyecto de compose>-agente`: dos stacks nunca se pisan la sesión.

> Antes de correr el corrector oficial, bajá cualquier stack de prueba propio (`docker compose down`): comparte el
> broker de la planta.

## Dependencia provisional del contrato

`planta_contrato` se instala con pip desde un commit fijo de `NovitSoftware/academia2026-tp`:

```
planta-contrato @ git+https://github.com/NovitSoftware/academia2026-tp@ab65b95e911731351dba395817a74a8cc60da2b2#subdirectory=contrato
```

**Provisional, pendiente de confirmación de Novit.** La URL de `docs/contrato.md` (`NovitSoftware/academia2026-planta`)
devuelve 404. Se eligió este repositorio porque es público y su contenido coincide byte a byte con la copia oficial de la
planta (234 de 234 archivos). Si Novit indica otra URL o commit, se cambia una sola línea de `pyproject.toml`.

## Desarrollo y pruebas

Las herramientas (ruff, pytest, pre-commit) corren dentro de un contenedor, no en el host:

```bash
docker compose -f docker/compose.dev.yml run --rm dev pytest          # unitarios, contrato e infraestructura
docker compose -f docker/compose.dev.yml run --rm dev ruff check .
```

Pruebas contra servicios reales (marcadas `integracion`, necesitan `docker compose up -d db` y la red `planta-lan`):
`tests/integracion/test_base_real.py`. Mediciones:

```bash
sh evaluacion/correr_e1.sh                                  # corrector oficial local + resumen
python evaluacion/latencia_alerta.py --disparos 50 --cargar-propios   # latencia de alerta de seguridad
python evaluacion/retrieval.py --docs <clon oficial> --emb http://emb:8000   # RAG: léxico, denso, híbrido
```

## Estructura

```
src/sistema/
  app.py, config.py, pipeline.py     agente, configuración y camino de una nota (dos pasadas de ASR)
  flujo.py                           cadenas LCEL de la nota y callback de consumo (LangChain)
  voz/                               parser determinista, intérprete de respuestas y diálogo con la tablet
  deteccion/                         seguridad S1–S4, eventos por equipo, anomalía sostenida de sensores
  conocimiento/                      corpus, recuperador léxico y híbrido (RAG)
  herramientas/                      seis tools (StructuredTool de LangChain), servidor MCP y su consumo con langchain-mcp-adapters
  control/                           presupuesto de pasos y tokens, timeout y cortes por nota
  persistencia/, integraciones/      Postgres, cola de salida, MQTT, API de la planta, ASR
asr/, emb/                           servicios de transcripción y de embeddings
tests/                               unit/, infra/ (compose), integracion/ (Postgres real)
evaluacion/                          corrector, latencia de alerta, ASR, RAG, extracción offline
docs/                                spec-parte.md, rag.md, memoria.md, resultados/
```

## Documentos

- [docs/spec-parte.md](docs/spec-parte.md): flujo del parte (spec).
- [docs/rag.md](docs/rag.md): qué se consulta por RAG y qué por API o SQL, medición del recuperador.
- [docs/memoria.md](docs/memoria.md): qué se recuerda y por cuánto tiempo.
- [docs/resultados/linea_base.md](docs/resultados/linea_base.md): corridas del corrector y latencia de la alerta.
