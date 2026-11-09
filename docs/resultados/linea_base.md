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
