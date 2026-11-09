# LLM de respaldo: elección y benchmark

El LLM es **solo respaldo** del parser y del intérprete de respuestas (ver `src/sistema/respaldo.py`): se usa cuando el
parser no saca filas de una nota que habla de equipos o variables, para los tramos que no interpretó y para respuestas
del operador que el intérprete no entiende. Nunca confirma un parte ni decide reglas. Su salida pasa por la misma
validación determinista que el parser (el equipo existe, la variable corresponde a su tipo, la unidad es la del
manómetro si no se dijo otra, y cada número tiene que aparecer en lo que se dijo). Si el servicio falla, tarda más que
`LLM_TIMEOUT_S` o se pasa del presupuesto de tokens, la nota sigue por el camino determinista.

## Modelo elegido

| | |
|---|---|
| Modelo | **`qwen3.5:2b`** (Qwen3.5, 1,9 B de parámetros) |
| Cuantización | Q8_0 |
| Digest del manifest | `sha256:ae1e257587aba653…` (`/modelos/manifest.json` de la imagen) |
| Capas | pesos `e8bf47d40c5b…` (2,01 GB) y proyector de visión `5ddf560e3404…` (0,67 GB, no se usa) |
| Servidor | `ollama/ollama:0.40.15` (versión fijada) |
| Parámetros | `think=false` (`reasoning=False`), `temperature=0`, `num_ctx=2048`, `num_thread=1`, `num_predict=350`, salida con `format` = JSON schema |
| Cliente | `langchain-ollama==1.1.0` (`ChatOllama` + `with_structured_output(method="json_schema")`) hacia `FilaParte` |
| Servicio | `llm`: 0,75 CPU / 2,5 GB, label `academia.planta: pc`, `planta-lan`, sin puertos |

El esquema de salida es una subclase de `FilaParte` con `tag` y `variable` restringidos a los valores del catálogo
(decodificación estructurada: el modelo no puede escribir un equipo que no existe), con un máximo de 12 filas.

La imagen del servicio lleva **solo este modelo**. Los pesos se bajan en el build con `ollama pull` y su digest se
verifica contra el sha256 real del archivo; `arranque.sh` vuelve a verificar las capas contra `manifest.json` antes de
levantar Ollama. No se descarga nada al arrancar. Los tres candidatos se midieron en una imagen aparte
(`tupac-flores-llm:bench`, construida con `--build-arg MODELOS=...`).

## Benchmark

Condiciones: cuota real del servicio (`--cpus 0.75 --memory 2560m`), un modelo cargado a la vez, una llamada de
calentamiento antes de medir. Casos: notas del `OperadorVirtual` real de la planta, sin las ambiguas (esas se resuelven
preguntando), con variantes de ASR vistas en el corrector (`evaluacion/llm_casos.py`): «A uno», «frito», palabras
pegadas, sin tildes ni comas, «golpe teo», «dedescarga». Un tercio de los casos queda sin deformar.
Métricas sobre las filas esperadas, con el prompt y la validación del sistema (`evaluacion/llm_bench.py`).

| Modelo | Casos | JSON válido | Equipo + variable | Valor exacto | Unidad | Precisión | p50 | p95 | RAM pico |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5:1.5b` (Q4_K_M) | 30 | 96,7 % | 49,4 % | 39,8 % | 44,6 % | 78,8 % | 10,7 s | 26,8 s | 2,0 GiB |
| `qwen3.5:0.8b` (Q8_0) | 30 | 100 % | 41,0 % | 21,7 % | 41,0 % | 61,8 % | 17,5 s | 42,0 s | 2,0 GiB |
| **`qwen3.5:2b` (Q8_0)** | 6 | 100 % | **92,3 %** | **84,6 %** | 92,3 % | 92,3 % | 38,7 s | 77,6 s | 2,5 GiB |

Criterio (pedido por el usuario): el más exacto con p95 < 90 s. Gana `qwen3.5:2b`.

**Limitaciones que hay que leer antes de confiar en estos números:**

- **`qwen3.5:2b` se midió con 6 casos**, no 30: a 0,68 tokens/s generados con esta cuota (0,75 CPU) 30 casos no entraban
  en el tope de 40 min del benchmark. El p95 de 77,6 s es el máximo de 6 llamadas; con una muestra tan chica el
  intervalo es ancho. Los otros dos candidatos sí se midieron con 30.
- **La RAM pico de `qwen3.5:2b` llega al tope de 2,5 GiB** (la medición incluye caché de página). No hubo OOM en las
  6 llamadas, pero no hay margen.
- **Latencia:** una llamada de respaldo de `qwen3.5:2b` tarda ~40–80 s. El diálogo con el operador espera ese tiempo en
  los casos (poco frecuentes) en que el parser no entiende una nota. Por eso `LLM_TIMEOUT_S` es 90 s.
- Los 30 casos son una muestra generada, no tráfico real; el corrector usa audios de Piper que cambian entre corridas.
- Una primera medición de `qwen2.5:1.5b` con un esquema sin restricciones dio 0 % de valor exacto (el modelo inventaba
  `tag="tanque"` y valores nulos). Con el esquema restringido al catálogo y ejemplos en el prompt subió a 40 %: la
  restricción de la decodificación pesa más que el tamaño del modelo.

Archivos: `evaluacion/corridas/llm_bench_q25.json`, `llm_bench_q08.json`, `llm_bench_q2b.json` (ignorados por git).
