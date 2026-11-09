# INFORME · Entrega 1 · Rolando Flores

Sistema conectado a la planta simulada de Novit: parte por voz, detección de condiciones inseguras, eventos, tools y
MCP, RAG medido. Todo lo de esta página está medido o verificable en el repo; lo que no está hecho figura en
«Pendiente».

## Resultado del corrector local

`martes-tranquilo` + `fuga-sin-enlace`, commit `849eb73`: **88,8 / 100**, aprueba, sin eliminatorias
(`sistema_mudo` y `sin_alerta_de_seguridad` no ocurren).

| A1 | A2 | A3 | A4a | A4b | B1 | B2 | E1 | E2 | E3 | F1 | F2 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 % | 69,4 % | 69,4 % | 100 % | 100 % | 84,7 % | 100 % | 100 % | 100 % | 100 % | 100 % | 100 % |

Alerta de seguridad S1 a los 4,0–5,0 s reales (límite de la rúbrica: 30 s). Cuatro corridas finales con los cinco servicios: **88,8 / 88,8 / 88,8 / 88,8**, sin eliminatorias (`docs/resultados/linea_base.md`, sección V7). A2, A3 y B1 tienen techo por D8 estricta: una
nota de solo observaciones («la bomba está parada, en reserva») no se puede confirmar con valores y queda en Borrador.
Con D16 (`CONFIRMACION_ESTRICTA=false`, ya implementada) subirían a 100 %; la decisión es de Novit. Detalle y
repetición: `docs/resultados/linea_base.md`.

## Seguridad (S1–S4)

Detector determinista sobre la transcripción completa, sin LLM y sin nube. Dos pasadas del mismo modelo: una rápida
(beam 1, prompt de emergencia, cola por duración) y una completa; alerta con el primer acierto, id `EV-SEG-{nota}`.
Léxico con frases, negación, coincidencia aproximada y reparaciones de errores medidos del ASR.

| Prueba (50 disparos S1–S4, nota de ~20 s en cola, carga al tope en agente y db) | Resultado |
|---|---|
| Alertas | **50 / 50** |
| Falsas alarmas | **0** (también 0 en las 102 voces de referencia) |
| Latencia p50 / p95 / p99 | 10,4 s / 15,7 s / 18,8 s |
| Requisito de la rúbrica (30 s) | Cumplido |
| Puerta interna D18 (p99 ≤ 15 s) | **No cumplida** (p99 = 18,8 s) |

Una prueba informativa con 12 procesos sin límite en el host dio 45/50 y p99 19,0 s antes de las dos pasadas.

## Modelos por tarea

| Tarea | Modelo | Proveedor | Parámetros | Versión, revisión y digest | Dónde corre |
|---|---|---|---:|---|---|
| Transcripción (ASR) | faster-whisper `small`, int8 (CTranslate2) | OpenAI (modelo original) · conversión de SYSTRAN | 244 M | `Systran/faster-whisper-small` @ `536b0662…`; sha256 de `model.bin` `3e30592150…` verificado en el build; faster-whisper 1.1.0 | Local, contenedor `asr` |
| Embeddings (RAG) | `intfloat/multilingual-e5-small`, ONNX | Microsoft (autor: intfloat) | 118 M | @ `614241f6…`; sha256 de `model.onnx` `ca456c06b3…` verificado; onnxruntime 1.20.1 | Local, contenedor `emb` |
| Respaldo del parser y del intérprete de respuestas | **`qwen3.5:2b-q4_K_M`**, Q4_K_M | Alibaba (Qwen) | 1,9 B | Ollama `0.40.15`; manifest `sha256:2fcba4e039b23928…`, pesos `sha256:20cb277f0967…`; `think=false`, `temperature=0`, `num_ctx=2048`, `num_thread=1`, salida JSON schema → `FilaParte`; `langchain-ollama==1.1.0` | Local, contenedor `llm` |
| Extracción del parte | Parser determinista (sin modelo), orquestado como cadena LCEL de LangChain | — | — | `src/sistema/voz/`, `src/sistema/flujo.py` | Local |
| Interpretación de respuestas del operador | Reglas (sin modelo) | — | — | `src/sistema/voz/respuestas.py` | Local |
| Detección de seguridad y eventos | Reglas (sin modelo) | — | — | `src/sistema/deteccion/` | Local |
| Análisis, visión, órdenes | No hay en la Entrega 1 | — | — | — | — |

Nada se descarga en runtime: los pesos se bajan en el build con revisión fija, su digest se verifica y viven dentro de la
imagen. Los modelos no usan GPU (los contenedores de planta no tienen).

## Hardware

**Planta (restricción del enunciado, simulada con límites de contenedor):** sin GPU, 4 CPU y 8 GB. Es lo que verifica
`tests/infra/test_compose.py`.

**Desarrollo y pruebas (donde se hicieron todas las mediciones de este informe):** Desktop, AMD Ryzen 7 5700X (8 núcleos,
16 hilos, 3,4 GHz), 15,9 GB de RAM, GPU NVIDIA GeForce GTX 550 Ti (**no se usa**: ningún contenedor ni modelo la usa),
Gigabyte B550M DS3H, Windows 11 IoT Enterprise LTSC. Docker 29.9.2 con una VM de 16 CPU y ~9,7 GB de RAM; la cuota de
planta se aplica por contenedor (`deploy.resources.limits`). Las mediciones de latencia son con esos límites, pero el
procesador del equipo de desarrollo es más rápido que el de una PC de sala de control típica.

Límites por servicio (`docker-compose.yml`):


| Servicio | CPU | Memoria | Notas |
|---|---:|---:|---|
| `agente` | 0,5 | 1 GB | MQTT, pipeline, diálogo, latido, detección |
| `asr` | 2,0 | 2 GB | dos carriles (seguridad: 2 plazas, completa: 1) de un hilo cada uno |
| `emb` | 0,25 | 1 GB | 774 MiB en uso medido |
| `db` | 0,5 | 1 GB | PostgreSQL 16 + pgvector |
| `llm` | 0,75 | 2,5 GB | Ollama con `qwen3.5:2b-q4_K_M`; solo respaldo (RAM pico medida: 2,3 GiB) |
| **Total** | **4,0 de 4** | **7,5 de 8 GB** | verificado por `tests/infra/test_compose.py` |

Costo del ASR medido con la CPU saturada: **4,39 s fijos + 0,132 s por segundo de audio** (p95 de 7,4 s por nota).
La puerta original (RTF p99 ≤ 0,7) no se cumple con este modelo: el costo fijo domina en audios cortos.

## Tokens y costo de nube

| Tarea | Tokens | Cómo se midió |
|---|---:|---|
| Transcripción (ASR) | No usa tokens de LLM; cuesta tiempo: 4,39 s fijos + 0,132 s por segundo de audio | Regresión sobre 104 transcripciones con la CPU saturada |
| Embeddings (consulta RAG) | ≤ 512 tokens del tokenizador por consulta (preguntas de una frase: pocas decenas); 710 pasajes indexados una sola vez en el build | `emb/nucleo.py` (truncado a 512) |
| Parser, diálogo, seguridad, eventos | 0 (reglas) | — |
| Respaldo con LLM (por llamada) | **≈ 690 de entrada + 50–90 de salida = 730–780** | `usage_metadata` de `ChatOllama` en el stack del clon limpio (prompt con catálogo y 4 ejemplos) |
| **Por nota en las corridas del corrector** | **0** | El respaldo no se llamó ni una vez en las 4 corridas finales de V7 |

El presupuesto por llamada es de 2.500 tokens y por nota de 12 pasos y 120 s (`control/presupuesto.py`); el consumo de cada
nota (pasos, tokens, milisegundos, cortes) queda en la tabla `consumo`.

**Costo de nube: USD 0.** No hay modelos en la nube en la Entrega 1: ninguna llamada sale del entorno de planta.

## RAG (medido)

Corpus de la planta (701 secciones, 710 pasajes) en pgvector con metadatos de equipo, fabricante, documento, sección,
cita y vigente. Suite oficial de 71 preguntas (`docs/resultados/retrieval.json`, `docs/rag.md`):

| Variante | MRR | hit@5 | recall@5 | Rev. C antes que D |
|---|---:|---:|---:|---:|
| BM25 oficial (línea base) | 0,573 | 0,845 | 0,664 | 39 de 71 |
| Léxico propio (filtros de vigencia y aplicabilidad) | 0,679 | 0,859 | 0,707 | **0** |
| Denso (e5-small, con prefijos) | 0,660 | 0,789 | 0,631 | 0 |
| **Híbrido** (RRF, léxico ×2) | **0,683** | 0,859 | 0,702 | 0 |
| Denso sin prefijos (ablación) | 0,610 | 0,775 | 0,615 | 0 |

El híbrido mejora a la línea base BM25, pero **no supera con holgura al léxico filtrado**; la mejora real viene de los
filtros de vigencia. Qué va por RAG y qué por API o SQL: `docs/rag.md` (límites y stock nunca salen del RAG).

## LangChain

El núcleo de agentes usa LangChain (versiones exactas en `pyproject.toml`: `langchain-core==1.7.6`,
`langchain-mcp-adapters==0.3.2`, `mcp==1.30.0`; el adaptador exige `mcp<2`):

- **Tools:** las seis son `StructuredTool` con su modelo Pydantic estricto como `args_schema`; un fallo es `ToolException`
  (`herramientas/langchain.py`). El servidor MCP propio se consume con `langchain-mcp-adapters` (en memoria y por stdio).
- **Flujo del parte:** las dos pasadas de una nota son cadenas LCEL (`RunnableSequence`) de pasos con nombre:
  `transcribir_rapido | seguridad` y `transcribir | guardar | parte` (`flujo.py`, `pipeline.py`).
- **Consumo:** `CallbackConsumo` es un `BaseCallbackHandler`: cuenta pasos, tools y tokens, corta por presupuesto y
  registra el consumo al terminar la cadena. La ruta de seguridad se mide pero no se corta.
- No hay modelo de lenguaje en el camino de la nota; con la cadena y el callback ya montados, sumar uno es agregar un paso.

## LLM de respaldo (Ollama)

Solo respaldo: el parser determinista sigue siendo el camino principal. El LLM entra cuando el parser no saca filas de una
nota que habla de equipos o variables, para los tramos que no interpretó y para respuestas del operador que el intérprete
no entiende. **Nunca confirma un parte ni decide reglas**: su salida pasa por las mismas validaciones deterministas (el
equipo existe, la variable corresponde a su tipo, la unidad es la del manómetro si no se dijo otra, y cada número tiene que
haberse dicho). Si el servicio falla, tarda más de 90 s o se pasa de 2.500 tokens (callback de consumo), la nota sigue por
el camino determinista y nunca queda sin respuesta.

Los pesos viven dentro de la imagen (se bajan en el build, con digest verificado y `manifest.json`; el arranque los
vuelve a verificar; nada se descarga en runtime). La imagen lleva solo el modelo elegido.

Benchmark con la cuota real (0,75 CPU / 2,5 GB), 30 casos generados con el `OperadorVirtual` y variantes reales de ASR
(`docs/resultados/llm.md`):

| Modelo | Casos | Valor exacto | Equipo + variable | p50 / p95 | RAM pico |
|---|---:|---:|---:|---|---:|
| `qwen2.5:1.5b` (Q4_K_M) | 30 | 39,8 % | 49,4 % | 10,7 / 26,8 s | 2,0 GiB |
| `qwen3.5:0.8b` (Q8_0) | 30 | 21,7 % | 41,0 % | 17,5 / 42,0 s | 2,0 GiB |
| `qwen3.5:2b` (Q8_0) | 30 | 49,4 % | 63,9 % | 37,0 / 73,6 s | 2,5 GiB (tope) |
| **`qwen3.5:2b-q4_K_M`** | **30** | **53,0 %** | **68,7 %** | **27,9 / 50,1 s** | **2,3 GiB** |

Se eligió Q4_K_M: no queda por debajo de Q8_0 (3,6 puntos por encima en valor exacto, dentro del ruido de 83 filas),
es más rápida y usa menos memoria. Un resultado anterior de este informe (84,6 % para Q8_0) salía de solo 6 casos;
con los 30 completos Q8_0 da 49,4 %. **Aun el mejor modelo acierta ~53 % del valor exacto sobre notas deformadas**:
por eso es solo respaldo (el operador confirma todo y la validación descarta lo que no se dijo). En las cuatro
corridas finales del corrector el respaldo no se llamó ni una vez. Detalle en `docs/resultados/llm.md`.

## Tools, MCP, control y memoria

- Seis tools de solo lectura con Pydantic estricto (`extra="forbid"`), timeout y reintentos solo ante fallas
  transitorias; servidor MCP por stdio. Tests: `tests/unit/test_herramientas.py` (incluye cliente MCP real).
- Control por nota: 12 pasos, 4.000 tokens, 120 s y 3 repeticiones; la pasada completa se corta, la de seguridad se mide
  pero no se corta.
- Memoria: `docs/memoria.md`, verificada contra las tablas reales de Postgres (`tests/integracion/`), con reinicio a
  mitad de diálogo probado.
- **Diálogo:** hasta 5 borradores por nota (`MAX_BORRADORES`); el operador virtual de la planta responde sin tope, el
  corte es nuestro. El timeout global por nota (120 s) se mantiene.
- **Rango histórico (A-9):** un valor fuera de lo medido en 45 días (`/api/historian`, caché de 1 h) se avisa en el
  borrador; si con el «mil» recuperado cae adentro (600 → 1600 psi) se muestra ese valor como provisional, con aviso y
  sin opciones.
- **Foto por fila (A-6):** cada «le saqué foto» se lleva la siguiente foto de la nota para las filas de su tramo.
- **Contexto del escalado (B-3):** el `Escalado` lleva el parte del equipo, las fotos y la tendencia de las últimas 3 h
  (en `resumen`; el contrato no tiene campos para eso).

## Decisiones vigentes

- **D8** (estricta): sin confirmación del operador el parte queda en Borrador. **D16** (flexibilizarla) **pendiente de Novit**:
  es el techo de A2, A3 y B1 (88,8).
- **D14**: S2 y S4 no llevan equipo ni filas (provisional).
- **D17**: cola de salida con protocolo intención → publicación → marca.
- **D18** enmendada: puerta interna de latencia de alerta p99 ≤ 15 s en el peor caso; **no cumplida** (p99 19,3 s), aceptado el 09/11.
  El requisito de la rúbrica (30 s) se cumple.

## Pendiente y limitaciones declaradas

- **Contrato provisional:** `planta_contrato` se instala desde un commit fijo de `academia2026-tp`; la URL de `docs/contrato.md`
  devuelve 404 y falta confirmación de Novit.
- **Variabilidad de Piper:** el simulador sintetiza con Piper, que no es determinista (cuatro audios distintos con la misma
  semilla). La nota del corrector puede variar entre corridas por errores del ASR sobre audios distintos; antes de los
  arreglos salieron 88,8, 88,0 y 86,8, y las cuatro corridas finales dieron 88,8.
- **Puerta interna de 15 s no cumplida** (p99 19,3 s en el peor caso con carga al tope); causa: un trabajo del ASR en curso no
  se interrumpe.
- **Lista cerrada de modelos:** modelos locales de libre elección según aclaración verbal de Novit; confirmación por escrito pendiente (BL-5).
- **Fotos:** se asocian a su fila por URL pero el sistema no las descarga.
- **Códigos de falla del fabricante → F01–F10:** `MBP-4 §6` se recupera y se cita, pero no hay una función que traduzca.
- **Context builder:** solo mencionado; falta el documento de diseño.
- **Notas `VIVO-`:** sin tratamiento ni test específico (una nota sin equipos termina en `sin_filas`).
- **Evaluación contra `/api/verdad`:** se hace con el corrector oficial y `evaluacion/extraccion.py`; no hay un script propio.
- **Respaldo con LLM:** medido solo sobre 30 casos generados (53 % de valor exacto) y sin ejercitarse en el corrector.
- **WER de `small` contra `turbo`:** solo se midió `small` (102 audios).
- **Repositorio:** el nombre es `tupac-flores` (D11, `tupacflores-rgb/tupac-flores`); falta subir la rama `entrega-1` (sin push, a pedido).

## Conclusión: por qué estos modelos

Se eligió **lo más chico que cumple, todo local**, por tres razones que vienen de las restricciones y de lo medido:

1. **La planta no tiene GPU, 4 CPU y 8 GB, y el enlace puede caerse horas.** Por eso nada depende de la nube y los tres modelos
   corren en CPU dentro de la cuota: `small` en int8 transcribe en ~4 s fijos más ~0,13 s por segundo de audio y deja la
   alerta de seguridad en 4–5 s en el caso normal (la rúbrica pide menos de 30); `multilingual-e5-small` (118 M, ONNX) cabe en
   0,25 CPU y busca en ~35 ms; y el LLM de respaldo es `qwen3.5:2b` en Q4_K_M porque midiéndolo con la cuota real fue tan
   exacto como Q8_0 (53 % contra 49 %), más rápido (p95 50 s contra 74 s) y con menos memoria.
2. **Las decisiones que importan son de código, no de un modelo.** La alerta de seguridad, los eventos, las unidades y los
   límites se deciden con reglas reproducibles; el LLM solo propone filas cuando el parser no entiende y todo lo que propone se
   valida y lo confirma una persona. Eso es lo que sostuvo 88,8 en cuatro corridas sin que el LLM interviniera.
3. **Lo que no se pudo medir bien quedó dicho:** el LLM acierta ~53 % del valor exacto sobre notas deformadas (por eso es solo
   respaldo), el ASR depende de audios que cambian entre corridas, y dos puertas internas (latencia p99 y p95 del LLM) no se
   cumplen con holgura.
