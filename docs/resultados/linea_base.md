# Línea base de la Entrega 1 (V1)

Medida el 09/11/2026 con el corrector oficial local (`evaluacion/correr_e1.sh`) sobre el commit `ef5905b`.
La carpeta completa de la corrida (`evaluacion/corridas/e1-ef5905b-*`) no se versiona; acá queda el resumen.

## Corrida del corrector: `martes-tranquilo` + `fuga-sin-enlace`

**Nota: 88,8 / 100 · aprueba · sin faltas eliminatorias** (`sistema_mudo` y `sin_alerta_de_seguridad` no ocurren).
Se midieron criterios por 63 de 93 puntos (D, F3 y G no aplican a estos escenarios); la nota está reescalada.

| Criterio | `martes-tranquilo` | `fuga-sin-enlace` | Promedio |
|---|---:|---:|---:|
| A1 Cobertura | 100 % | 100 % | **100 %** |
| A2 Borrador al primer intento | 89 % | 50 % | 69,4 % |
| A3 Parte final correcto | 89 % | 50 % | 69,4 % |
| A4a Pregunta cuando hace falta | 100 % | 100 % | **100 %** |
| A4b No pregunta de más | 100 % | 100 % | **100 %** |
| B1 Sin novedad / Escalado | 94 % | 75 % | 84,7 % |
| B2 Alerta de seguridad | — | 100 % | **100 %** |
| E1 Sigue andando sin enlace | — | 100 % | **100 %** |
| E2 Pendientes visibles | — | 100 % | **100 %** |
| E3 Sin duplicados | 100 % | 100 % | **100 %** |
| F1 Mensajes válidos | 100 % | 100 % | **100 %** |
| F2 Latido | 100 % | 100 % | **100 %** |

**Latencia de la alerta (nota S1 de `fuga-sin-enlace`):** alerta y aviso en la tablet a los **4,53 s** reales de que sale
la nota (límite de la rúbrica: 30 s). La corrida anterior, antes de corregir E2, dio 5,03 s.

### Qué explica lo que no llega al 100 %

Todo lo que falta es estructural, no un error de lectura:

- **Notas de solo observación** (`"La bomba está parada, en reserva."`, 1 en cada escenario): el operador virtual solo
  compara filas con valor, así que no puede confirmarlas. Con D8 estricta quedan en Borrador y no generan eventos:
  pierden A2, A3 y el `SinNovedad` de B1. Es el techo de **84,7 % en B1** que anticipaba el plan v1.2.
  Pasaría a 100 % si Novit acepta D16 (bandera `CONFIRMACION_ESTRICTA=false`, ya implementada).
- **Nota insegura S1:** sin filas con valor tampoco se puede confirmar (A2/A3). Es inherente a la rúbrica.

## Evaluación offline con el operador virtual real (`evaluacion/extraccion.py`)

796 notas generadas por `OperadorVirtual` sobre plantas con estados al azar, con transcripción perfecta, contestadas por
el mismo `dialogo.responder` que usa la planta:

| Métrica | Todas las notas | Notas con algún valor |
|---|---:|---:|
| A1 cobertura | 100 % | — |
| A2 primer intento | 64,2 % | 68,6 % |
| A3 parte final | 92,5 % | 98,8 % |
| A4a pregunta si hay ambigüedad | 100 % | — |
| A4b no pregunta de más | 100 % | — |
| B1 decisiones por equipo | 94,7 % | — |
| Seguridad S1–S4 detectadas | 16 / 16 | — |
| Falsas alarmas en 796 notas normales | 0 | — |

El A2 bajo se debe al **goteo de la empaquetadura**: el audio no dice cuántas gotas por minuto, pero la verdad de campo
sí trae ese valor. Con la fila sin valor (instrucción vigente), el operador la señala como «falta» en el primer borrador y
el valor se completa en la corrección: se gana A3, no A2. Las notas cuyo único dato es el goteo no tienen ninguna fila con
valor para comparar, y por eso no se pueden confirmar.

## ASR (`evaluacion/asr_wer.py`): faster-whisper `small` int8, 1,5 CPU

102 audios de referencia, prompt de planta, `beam_size=5`:

- **Números:** 79 de 102 transcripciones los escriben con cifras (`1630`, `112`, `1,3`, `5,1`); «uno ocho» queda en
  palabras y nunca aparece como `18`; «catorce» sí aparece como `14`. El parser entiende ambas formas.
- El WER literal (21 %) no es una medida útil acá: cuenta como error cada número escrito con cifras en lugar de palabras.
  Los errores relevantes son sistemáticos y están cubiertos por el parser: «medió» por «me dio», `61°`, `1610p si`,
  `09`, «uno o dos», «golpe teo», «fito a», «El A1».
- **Latencia:** ~3,2 s fijos (el codificador procesa una ventana de 30 s) más ~0,1 s por segundo de audio. Medidas:
  hasta 5 s de audio → 3,2 s; 12–20 s → 5,3 s (máximo 6,2 s). RTF medio 0,57; p95 0,95; p99 1,08.
- **D18 (RTF p99 ≤ 0,7):** medido así **no se cumple**, porque el costo fijo domina en audios cortos. La intención de la
  puerta (que la alerta salga antes de 30 s) sí se cumple con holgura: peor caso = una nota larga delante (~6,2 s) + la
  nota insegura (~4,5 s) + descarga y publicación (<1 s) ≈ 12 s, y la medición real fue de 4,5 s. Se propone redefinir la
  puerta en términos absolutos (p99 de latencia de alerta < 15 s) en lugar de RTF; pendiente de autorización.

## Cómo repetirlo

```bash
sh evaluacion/correr_e1.sh        # corrector oficial local + resumen (requiere la planta y el venv en Context/)
docker run --rm -v <repo>:/work -v "<repo>/Context/Clon del REPO:/oficial:ro" planta-sim \
    python /work/evaluacion/extraccion.py --recorridas 300      # evaluación offline
```

## Cierre de V2 (commit `849eb73`, 09/11/2026)

Corrida del corrector local sobre `martes-tranquilo` + `fuga-sin-enlace`: **88,8 / 100**, igual a la línea base, sin
eliminatorias; A1 100 %, A2 y A3 69,4 %, A4a y A4b 100 %, B1 84,7 %, B2 100 %, F1 y F2 100 %. Alerta S1 a los 4,02 s.

Una corrida previa dio 74,8 por un error del arnés, no del código: el stack de pruebas propio (`tupac-flores`) seguía
levantado con el mismo `client_id` MQTT que el del corrector («Session taken over»). Hay que hacer
`docker compose down` antes de correr el corrector.

Pasadas de ASR (Plan B2'): rápida (beam 1, sin marcas de tiempo, prompt de emergencia, 2 plazas, nota más corta
primero) y completa (parámetros normales). `asr` a 2,0 CPU y 2 GB (suma de la planta: 3,5 de 4 CPU).
Costo del ASR con la CPU saturada: 4,39 s fijos + 0,132 s por segundo de audio (p95 7,4 s por nota).

Latencia de alerta, 50 disparos S1–S4 con una nota de ~20 s en cola (`evaluacion/latencia_alerta.py`):

| Prueba | Con alerta | Falsas | p50 | p95 | p99 |
|---|---:|---:|---:|---:|---:|
| 12 procesos sin límite (informativa), 1 pasada | 45/50 | 0 | 13,3 | 18,3 | 19,0 |
| ídem, cola por duración | 48/50 | 0 | 13,6 | 18,0 | 19,3 |
| Carga solo en agente y db, 2 pasadas, 1 plaza | 50/50 | 1 | 13,3 | 16,0 | 16,7 |
| Carga solo en agente y db, 2 pasadas, 2 plazas | **50/50** | **0** | 10,4 | 15,7 | **18,8** |

Con las 102 voces (`evaluacion/seguridad_voces.py`): 0 falsas alarmas y la nota S1 detectada.
**Puerta interna D18 (p99 ≤ 15 s) no cumplida: p99 = 18,8 s en el peor caso realista. Requisito de la rúbrica (30 s) cumplido; 50/50 alertas, 0 falsas alarmas.** El usuario aceptó cerrar V2 así el 09/11; no se trocean audios. No se pasó a B1 ni a `base`.

## V7 · corridas finales de la Entrega 1 (09/11/2026)

Corrector local sobre `martes-tranquilo` + `fuga-sin-enlace`, stack propio bajado antes de cada corrida (`docker ps` limpio).

| Corrida | Commit | Nota | A1 | A2 | A3 | A4a | A4b | B1 | B2 | F1 | F2 | Alerta S1 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Línea base | `ef5905b` | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,5 s |
| V2 | `849eb73` | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,0 s |
| V7 · 1 | `0a5eabb` | 88,0 | 100 | 63,9 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,0 s |
| V7 · 2 | `0a5eabb` | **85,9** | 100 | 63,9 | 63,9 | 100 | 100 | 76,4 | 100 | 100 | 100 | 5,0 s |

Sin eliminatorias en ninguna. **Hay una caída de hasta 2,9 puntos respecto de 88,8** en A2, A3 y B1 de
`martes-tranquilo`: una o dos notas necesitaron una corrección o no se confirmaron. Qué se sabe:

- El ASR es **determinista**: el mismo audio dio la misma transcripción 6 de 6 veces, con el carril libre y con la
  pasada de seguridad corriendo en paralelo.
- Dos corridas del mismo commit transcriben distinto la misma nota (`NOTA-00011`: «1600 psi» contra «600 psi»). Con un
  ASR determinista, eso indica que el **audio de entrada no es idéntico entre corridas** (inferencia; no se compararon
  los archivos). La puntuación de A2, A3 y B1 depende de qué errores del ASR caen en cada nota.
- Por lo tanto, 88,8 no es un valor estable: en cuatro corridas con el mismo diseño de ASR salió 88,8, 88,8, 88,0 y 85,9.

## Latencia de alerta con `emb` levantado (50 disparos, base limpia)

Carga al tope dentro de agente, db y emb; nota de ~20 s delante: **50/50 alertas, 0 falsas alarmas, p50 8,5 s,
p95 15,3 s, p99 16,0 s** (`latencia_alerta_con_emb.json`). Sin `emb` fue p99 18,8 s: la variación entre corridas es
mayor que la diferencia, o sea que el servicio de embeddings no empeora la latencia. La puerta interna de 15 s sigue sin
cumplirse. Una primera tanda con la base sucia dio 47/50 porque tres notas se descartaron como «repetidas» (ids de
corridas anteriores del simulador); el corrector arranca con `down -v` y no tiene ese problema.

## Prompt del ASR con «mil» y psi en palabras (09/11/2026)

`asr/servidor.py`, pasada completa: el `initial_prompt` suma «Mil seiscientos psi, mil seiscientos cuarenta psi, mil
quinientos psi, ciento doce kilos, ciento quince kilos, noventa y ocho bar». Sobre las 102 voces de referencia
(`evaluacion/asr_wer.py`, mismo servicio, mismos audios):

| Prompt | WER global | Transcripciones con cifras | RTF medio |
|---|---:|---:|---:|
| Anterior | 21,2 % | 79 de 102 | 0,59 |
| Con «mil» y psi en palabras | **6,4 %** | **5 de 102** | 0,63 |

El WER literal de antes contaba como error cada número escrito con cifras en lugar de palabras; con el prompt nuevo
Whisper los escribe como se dictan. Residuo nuevo: «mil seiscientos diezp psi» (la «p» de «psi» pegada), reparado en
`voz/texto.py`.

## Diagnóstico de la variación de la nota entre corridas (V7)

- `NOTA-00011` tiene el **mismo texto, los mismos valores y la voz sintética** en las tres corridas comparadas
  (`/api/verdad`). No está en el banco de voces pregrabadas (`banco.buscar` es por texto exacto), así que la planta la
  sintetiza con Piper.
- **Piper no es determinista:** cuatro generaciones del mismo texto con la misma semilla (`crc32(nota_id)`) dieron
  cuatro WAV distintos. La semilla fija solo gobierna el ruido y las pausas. Por eso el audio, y lo que oye el ASR,
  cambia entre corridas. El ASR, en cambio, sí es determinista sobre un mismo audio.
- **Qué se perdió en la corrida 2 (85,9):** el ASR oyó «**A** uno estar en 112 kilogramos…» en lugar de «La uno…». El
  parser perdía todo el tramo de B-101 (112 kg y 59 °C) y dejaba solo una fila de «la bomba» ambigua. El operador
  virtual corrigió tres veces, se agotaron los borradores y la nota quedó sin confirmar (D8): se perdieron las decisiones
  `SinNovedad` de B-101, B-102 y F-201A (B1 de `martes-tranquilo` 94 % → 78 %; A3 89 % → 78 %).
- **Arreglos:** «A uno/dos/tres» al comienzo de una oración se repara a «La…» (`reparar_inicio_de_oracion`); rango
  histórico de 45 días (A-9) para recuperar el «mil» perdido, y el prompt del ASR de arriba.

## V7 final · corridas con el código completo de la entrega (09/11/2026)

Corrector local, `martes-tranquilo` + `fuga-sin-enlace`, stack propio bajado y `docker ps` limpio antes de cada corrida.

| Commit | Nota | A1 | A2 | A3 | A4a | B1 | Qué falló |
|---|---:|---:|---:|---:|---:|---:|---|
| `96bae90` · 1 | **88,8** | 100 | 69,4 | 69,4 | 100 | 84,7 | — |
| `96bae90` · 2 | 88,0 | 100 | 63,9 | 69,4 | 100 | 84,7 | `NOTA-00011`: el ASR oyó «el **frito** a medio…» y el operador tuvo que agregar la fila (A2) |
| `2cf8013` · 1 | 86,8 | 94,4 | 69,4 | 69,4 | 75 | 84,7 | `NOTA-00008`: el ASR escribió «la bomba **estaparada** en reserva»; sin filas no hubo pregunta ni borrador (A1, A4a) |
| `2cf8013` · 2 | **88,8** | 100 | 69,4 | 69,4 | 100 | 84,7 | — |

Sin eliminatorias en ninguna; B2, F1 y F2 en 100 % y la alerta S1 entre 4,0 y 4,5 s en todas. **Criterio «ninguna corrida
por debajo de 88,8»: no se cumple** (2 de 4 corridas, a 0,8 y 2,0 puntos). 88,8 es el techo con D8 estricta; las otras
caídas son errores del ASR sobre audios de Piper que cambian entre corridas. Cada una se reparó por separado
(`2cf8013` «frito», `2f9bac5` «estaparada», más «A uno» y «diezp» antes), pero cada corrida nueva puede traer otra
variante: es un juego sin fin si el parser depende de acertar la forma exacta que escribe el ASR.

50 disparos finales de seguridad (`latencia_alerta_final.json`): 50/50 alertas, 0 falsas alarmas, p50 8,0 s, p95 15,8 s,
p99 16,4 s. Validación con las 102 voces y el prompt nuevo: 0 falsas alarmas, S1 detectada.

## V7 · criterio nuevo, resultados finales (commit `dc80586`, 09/11/2026)

Criterio aprobado: 0 eliminatorias, promedio ≥ 88,0 en 4 corridas, ninguna < 87,0, 50/50 alertas y 0 falsas alarmas.
Stack propio bajado y `docker ps` limpio antes de empezar; las cuatro corridas del corrector local, en cadena, con los
cinco servicios (agente, asr, emb, llm, db).

| Corrida | Nota | A1 | A2 | A3 | A4a | A4b | B1 | B2 | F1 | F2 | Alerta S1 | Eliminatorias |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,5 s | ninguna |
| 2 | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,5 s | ninguna |
| 3 | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 4,0 s | ninguna |
| 4 | 88,8 | 100 | 69,4 | 69,4 | 100 | 100 | 84,7 | 100 | 100 | 100 | 5,0 s | ninguna |

**Promedio 88,8; mínimo 88,8; criterio cumplido.** 88,8 es el techo con D8 estricta (A2, A3 y B1 por las notas de solo
observación): sin D16 no se puede subir.

50 disparos de seguridad (`latencia_alerta_final_llm.json`), con carga al tope en agente, db, emb y llm: **50/50 alertas,
0 falsas alarmas**, p50 8,6 s, p95 17,1 s, p99 19,3 s (el máximo es el primer disparo, en frío). La puerta interna
p99 ≤ 15 s sigue sin cumplirse (aceptado por el usuario el 09/11); el requisito de la rúbrica (30 s) se cumple.

**Qué produjo la mejora y qué no (honesto):**

- En las cuatro corridas **el respaldo con LLM no se usó ni una vez** (0 llamadas): el parser, con la normalización
  sistemática (frases conocidas sin espacios, alias aproximados contra `/api/planta`, «A uno», «frito», «estaparada»,
  «diezp», el nombre pegado «launo»), resolvió todas las notas por sí solo. El LLM quedó como red de seguridad para lo
  que el parser no entienda; su valor real está medido solo en el benchmark (`docs/resultados/llm.md`) y en una prueba
  de punta a punta, no en las corridas del corrector.
- Con cuatro corridas idénticas no se puede descartar que el corrector tenga variación de audio: antes del arreglo
  salieron 88,8, 88,0 y 86,8 con el mismo diseño. Que las cuatro finales den 88,8 es un buen indicio, no una garantía.

## Verificación con clon limpio (09/11/2026, commit `af1d990`)

Prueba de que la entrega no depende de nada de la carpeta de trabajo.

1. `git clone --branch entrega-1` del repo local a una carpeta temporal fuera de `Tupac-Flores`, llamada `rolando-flores`
   (128 archivos rastreados; sin `Context/` ni `.env`).
2. `docker compose build --no-cache` desde el clon: **construyó las 5 imágenes sin errores** (agente, asr, emb, llm, db) en
   ~10 min. Todo se obtuvo de internet y de lo versionado: `planta_contrato` por pip desde GitHub, el modelo del ASR con
   revisión y sha256, el de embeddings con revisión y sha256, y el LLM con `ollama pull` y digest verificado. Tamaño: asr 1,7 GB,
   emb 1,3 GB, llm 12,2 GB (la imagen base de Ollama pesa 3,2 GB), db 0,6 GB, agente 0,35 GB.
3. **Arranque en frío** (sin imágenes en uso, sin volúmenes, `docker compose up -d`): `up -d` devolvió a los 16,3 s y el
   **primer latido `sistema/estado` llegó a los 24,4 s** (límite: 180 s), válido contra el contrato
   (`sistema: Rolando Flores`, `enlace_ok: true`, `pendientes_sincronizar: 0`).
4. Corrector oficial apuntando al clon (`martes-tranquilo` + `fuga-sin-enlace`): **88,8 / 100**, sin eliminatorias; A1 100 %,
   A2 y A3 69,4 %, A4a y A4b 100 %, B1 84,7 %, B2 100 %, F1 y F2 100 %; alerta S1 a los **4,0 s**. Esta corrida es además la de
   control del cambio de modelo del LLM (Q8_0 → Q4_K_M).
5. Se borró la carpeta del clon y el stack de compose del proyecto `alumno-rolando-flores` (contenedores, red y volúmenes).
   Las cinco imágenes construidas quedan en el almacén de Docker (≈ 16 GB) hasta que se autorice borrarlas.
