# Spec del flujo del parte (Spec-Driven Development)

Fuente de verdad del comportamiento; los tests de `tests/unit/` la ejecutan. Si el código y esta spec difieren, uno de
los dos tiene un bug.

## Entradas y salidas

| Entrada | Tópico | Contrato |
|---|---|---|
| Nota de voz de la recorrida | `planta/recorridas` | `NotaRecorrida` (audio en la API de la planta) |
| Respuesta del operador en la tablet | `planta/respuestas` | `RespuestaOperador` |
| Lecturas de sensores | `planta/sensores/{TAG}/{variable}` | `LecturaSensor` |

| Salida | Tópico | Cuándo |
|---|---|---|
| `AlertaSeguridad` + aviso de emergencia (`espera_respuesta: false`) | `sistema/eventos`, `sistema/tablet` | Una nota cuyo texto matchea S1–S4. Nunca desde sensores |
| Borrador / pregunta / «guardado» | `sistema/tablet` | Una por paso del diálogo, ids `TAB-{nota}-{n}` |
| `Escalado` o `SinNovedad` por equipo | `sistema/eventos` | Solo de un parte **confirmado** (D8). Ids `EV-{nota}-{TAG}` |
| `Escalado` de sensor | `sistema/eventos` | Anomalía sostenida (V4). Id `EV-SEN-{TAG}-{variable}-{inicio}`, sin `nota_ids` |
| Latido | `sistema/estado` | Cada 30 s y ante cambios de enlace o pendientes |

Todo lo publicado se valida contra `planta_contrato` antes de salir.

## Flujo de una nota

```
nota ─► descargar audio ─► ASR ─┬─ pasada de seguridad (beam 1, prompt de emergencia, cola por duración, 2 plazas)
                                └─ pasada completa (parámetros normales, cola FIFO)
        ambas ─► léxico S1–S4 ─► AlertaSeguridad (primer acierto; id EV-SEG-{nota}, sin duplicados)
        completa ─► parser ─► filas del parte ─► diálogo con el operador ─► (confirmado) ─► eventos por equipo
```

1. **Seguridad primero.** La alerta no espera al parte, al diálogo ni a la base: se publica antes de persistir.
2. **Parser determinista (sin LLM).** Números en palabras y en cifras, unidad por manómetro del equipo, alias del
   catálogo (`/api/planta`), rangos, observaciones. Lo que no se puede resolver se marca, no se inventa.
3. **Diálogo** (`voz/dialogo.py`):

| Situación | Mensaje a la tablet |
|---|---|
| Nota clara | UN borrador con filas, sin opciones |
| Equipo ambiguo («la bomba») | Pregunta con opciones (`la uno`, `la dos`, `la tres`, sin las ya nombradas) y sin filas; el borrador viene después |
| Valor ambiguo («dieciocho de diferencial») | Borrador con el candidato `1,8` marcado *provisional* y las opciones `1,8` / `18` |
| Goteo de la empaquetadura (no trae número) | Fila sin valor con observación; el operador lo completa |
| Corrección | Borrador nuevo con id nuevo (hasta 3 borradores y 6 mensajes por nota; luego, pendiente de validación) |
| Confirmación | Aviso «guardado» (`espera_respuesta: false`) y recién ahí los eventos |
| Respuesta a un mensaje viejo o desconocido | Se ignora |
| Nota insegura | La alerta ya salió; el diálogo solo anota la observación del equipo real (S1, S3). S2 y S4 no inventan equipo (D14) |

4. **D8 estricta** (`CONFIRMACION_ESTRICTA=true`): sin confirmación del operador el parte queda en Borrador y no genera
   eventos. Con `false` (D16, pendiente) una nota solo de observaciones se valida sola.

## Eventos por equipo (B1)

Por cada equipo del parte confirmado: **Escalado** si hay una observación que no sea «parada, en reserva» o «parada por
mantenimiento», o un valor fuera de los límites de alarma del sensor (`/api/planta`, con conversión de psi y bar a
kg/cm²); **SinNovedad** en caso contrario.

## Sensores (V4 mínimo)

`Escalado` solo si la lectura sigue fuera de límites 5 min y 3 muestras seguidas. Un pico aislado o una lectura mala
reinicia la cuenta; la histéresis (3 % del límite, 2 lecturas) evita repetir el evento; una bomba parada o en
mantenimiento se ignora.

## Robustez

- **Enlace caído:** todo sigue funcionando; lo procesado queda pendiente de sincronizar y el latido lo informa
  (`pendientes_sincronizar`); al volver, se vacía sin duplicar (ids deterministas, cola `salida`).
- **Reinicio:** el diálogo de cada nota se recarga de Postgres; la cola de salida reintenta lo no publicado.
- **Control por nota:** 12 pasos, 4.000 tokens, 120 s y 3 repeticiones; la pasada completa se corta y no se reintenta,
  la de seguridad se mide pero no se corta.

## Criterios de aceptación medidos

Corrida local del corrector sobre `martes-tranquilo` + `fuga-sin-enlace`: 88,8 / 100, sin eliminatorias
(`docs/resultados/linea_base.md`).
