# Parte del sistema contra la verdad de campo

Origen: `evaluacion\corridas\e1-ee74bde-193605` · escenarios: fuga-sin-enlace, martes-tranquilo

Notas evaluadas: 13 (13 con parte esperado; las `VIVO-` no entran).

## Último borrador mostrado

| Campo | Aciertos |
|---|---|
| equipo + variable | 25/25 (100.0%) |
| valor | 25/25 (100.0%) |
| unidad | 25/25 (100.0%) |
| observación | 3/3 (100.0%) |

Falsos negativos (filas esperadas que no están): 0 · falsos positivos (filas que la nota no dice): 0

## Primer borrador mostrado

| Campo | Aciertos |
|---|---|
| equipo + variable | 25/25 (100.0%) |
| valor | 25/25 (100.0%) |
| unidad | 25/25 (100.0%) |
| observación | 3/3 (100.0%) |

Falsos negativos (filas esperadas que no están): 0 · falsos positivos (filas que la nota no dice): 0

## Preguntas al operador: notas ambiguas vs claras

| Grupo | Notas | Con pregunta | Preguntas | Borradores de más | Valor correcto al final |
|---|---|---|---|---|---|
| ambiguas | 3 | 3 | 3 | 0 | 6/6 (100.0%) |
| claras | 10 | 0 | 0 | 0 | 19/19 (100.0%) |

## Latencia por nota (segundos reales desde que sale la nota)

| Hito | Notas | Mediana | Media | Máx |
|---|---|---|---|---|
| hasta el primer borrador | 13 | 9.96 | 9.21 | 14.59 |
| hasta la confirmación (evento del parte) | 10 | 14.62 | 14.47 | 21.04 |
| hasta la alerta de seguridad (S1–S4) | 1 | 4.53 | 4.53 | 4.53 |

La confirmación incluye el tiempo que tarda el operador virtual en contestar.

## Costo

- Nube: **USD 0.00**. Todos los modelos son locales (sin llamadas a servicios pagos).
- Tokens por tarea:
  - `completa`: 13 llamadas · 0 tokens de entrada · 0 de salida · 94.4 s
  - `seguridad`: 13 llamadas · 0 tokens de entrada · 0 de salida · 75.3 s

Respuestas del operador virtual: 13 · confirmaron el borrador: 10 · con problemas señalados: 0

## Seguridad

- Notas inseguras: 1 · con alerta emitida: 1
- Falsos negativos: ninguno
- Falsos positivos: ninguno
