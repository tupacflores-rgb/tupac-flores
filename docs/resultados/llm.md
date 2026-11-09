# LLM de respaldo: elección y benchmark

El LLM es **solo respaldo** del parser y del intérprete de respuestas (ver `src/sistema/respaldo.py`): se usa cuando el
parser no saca filas de una nota que habla de equipos o variables, para los tramos que no interpretó y para respuestas
del operador que el intérprete no entiende. Nunca confirma un parte ni decide reglas. Su salida pasa por la misma
validación determinista que el parser (el equipo existe, la variable corresponde a su tipo, la unidad es la del
manómetro si no se dijo otra, cada número tiene que aparecer en lo que se dijo y la fila tiene que ser de un equipo que
la nota nombra). Si el servicio falla, tarda más que `LLM_TIMEOUT_S` o se pasa del presupuesto de tokens, la nota sigue
por el camino determinista.

## Modelo elegido

| | |
|---|---|
| Modelo | **`qwen3.5:2b-q4_K_M`** (Qwen3.5 de Alibaba, 1,9 B de parámetros) |
| Cuantización | **Q4_K_M** (la variante Q8_0 se midió y quedó descartada, ver abajo) |
| Digest del manifest | `sha256:2fcba4e039b23928…` (`/modelos/manifest.json` de la imagen) |
| Capas | pesos `20cb277f0967…` (1,27 GB) y proyector de visión `5ddf560e3404…` (0,67 GB, no se usa) |
| Servidor | `ollama/ollama:0.40.15` (versión fijada) |
| Parámetros | `think=false` (`reasoning=False`), `temperature=0`, `num_ctx=2048`, `num_thread=1`, `num_predict=350`, salida con `format` = JSON schema |
| Cliente | `langchain-ollama==1.1.0` (`ChatOllama` + `with_structured_output(method="json_schema")`) hacia `FilaParte` |
| Servicio | `llm`: 0,75 CPU / 2,5 GB, label `academia.planta: pc`, `planta-lan`, sin puertos |
| Tiempo por llamada | `LLM_TIMEOUT_S=90`, presupuesto de 2.500 tokens por llamada |

El esquema de salida es una subclase de `FilaParte` con `tag` y `variable` restringidos a los valores del catálogo
(decodificación estructurada: el modelo no puede escribir un equipo que no existe), con un máximo de 12 filas.

La imagen del servicio lleva **solo este modelo**. Los pesos se bajan en el build con `ollama pull` y su digest se
verifica contra el sha256 real del archivo; `arranque.sh` vuelve a verificar las capas contra `manifest.json` antes de
levantar Ollama. No se descarga nada al arrancar.

## Benchmark (30 casos, cuota real)

Condiciones: cuota real del servicio (`--cpus 0.75 --memory 2560m`), un modelo cargado a la vez, una llamada de
calentamiento antes de medir. Casos: notas del `OperadorVirtual` real de la planta, sin las ambiguas (esas se resuelven
preguntando), con variantes de ASR vistas en el corrector (`evaluacion/llm_casos.py`): «A uno», «frito», palabras
pegadas, sin tildes ni comas, «golpe teo», «dedescarga». Un tercio de los casos queda sin deformar. Métricas sobre las
83 filas esperadas, con el prompt y la validación del sistema (`evaluacion/llm_bench.py`).

| Modelo | Casos | JSON válido | Equipo + variable | **Valor exacto** | Unidad | Precisión | p50 | p95 | RAM pico |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `qwen2.5:1.5b` (Q4_K_M) | 30 | 96,7 % | 49,4 % | 39,8 % | 44,6 % | 78,8 % | 10,7 s | 26,8 s | 2,0 GiB |
| `qwen3.5:0.8b` (Q8_0) | 30 | 100 % | 41,0 % | 21,7 % | 41,0 % | 61,8 % | 17,5 s | 42,0 s | 2,0 GiB |
| `qwen3.5:2b` (Q8_0) | 30 | 100 % | 63,9 % | 49,4 % | 57,8 % | 85,5 % | 37,0 s | 73,6 s | 2,5 GiB (tope) |
| **`qwen3.5:2b-q4_K_M`** | 30 | 100 % | **68,7 %** | **53,0 %** | 60,2 % | **89,1 %** | 27,9 s | 50,1 s | 2,3 GiB |

**Decisión sobre la cuantización** (regla del usuario: Q4_K_M si queda a ≤ 10 puntos de Q8_0): Q4_K_M no queda
10 puntos por debajo sino **3,6 puntos por encima** en valor exacto (53,0 % contra 49,4 %), es más rápido (p95 50 s
contra 74 s) y usa menos memoria (pico 2,3 GiB contra 2,5 GiB, donde Q8_0 tocaba el tope). La diferencia de exactitud
está dentro del ruido de 83 filas; lo que cuenta es que Q4_K_M no es peor. Se usa Q4_K_M. Criterio original del
usuario (el más exacto con p95 < 90 s): lo cumple.

**Corrección de un resultado anterior:** una versión previa de este documento y del INFORME decía que `qwen3.5:2b`
(Q8_0) alcanzaba 84,6 % de valor exacto. Ese número salía de solo 6 casos (30 no entraban en el tope de 40 min). Con los
30 casos completos Q8_0 da **49,4 %**: los 6 primeros eran una muestra optimista. Los 24 casos restantes de Q8_0 se
corrieron después (`--desde 6`) y se combinaron con los 6 originales.

**Limitaciones que hay que leer antes de confiar en estos números:**

- **Aun el mejor modelo acierta poco:** ~53 % de valor exacto y ~69 % de equipo+variable sobre notas deformadas. Por eso
  es solo respaldo: el parser determinista es el camino principal, el operador confirma todo, y la validación descarta lo
  que no se dijo. Con 83 filas, diferencias de unos pocos puntos son ruido.
- **Latencia:** una llamada de respaldo tarda ~28 s (p50) y hasta ~54 s con esta cuota. El diálogo con el operador espera
  ese tiempo en los casos (poco frecuentes) en que el parser no entiende una nota.
- Los 30 casos son una muestra generada, no tráfico real; el corrector usa audios de Piper que cambian entre corridas.
- Una primera medición de `qwen2.5:1.5b` con un esquema sin restricciones dio 0 % de valor exacto (el modelo inventaba
  `tag="tanque"` y valores nulos). Con el esquema restringido al catálogo y ejemplos en el prompt subió a 40 %: la
  restricción de la decodificación pesa más que el tamaño del modelo.
- En las 4 corridas finales del corrector (V7) el respaldo no se llamó ni una vez: estos números describen la red de
  seguridad, no el camino normal.

Archivos (ignorados por git): `evaluacion/corridas/llm_bench_q25.json`, `llm_bench_q08.json`, `llm_bench_q4.json`,
`llm_bench_q8_primeros6.json`, `llm_bench_q8_resto.json` y el resumen `llm_q4_vs_q8.json`.
