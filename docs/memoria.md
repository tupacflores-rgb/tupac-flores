# Memoria: qué se recuerda y por cuánto tiempo

Verificada contra las tablas reales de Postgres (`tests/integracion/test_base_real.py`) y aplicada por
`Base.purgar` (`persistencia/base.py`), que el agente corre cada hora.

| Qué | Tabla | Se escribe | Se conserva | Para qué |
|---|---|---|---|---|
| Estado de la conversación de una nota (mensaje de tablet, nota, borrador, intento, filas, ambigüedades) | `dialogo` (+ `mensaje_nota`) | En cada mensaje que sale a la tablet, antes de publicarlo | Parte cerrado (confirmado, pendiente de validación, sin filas): **24 h**. Sin cerrar (borrador o pregunta sin respuesta): **72 h** | Retomar el diálogo tras un reinicio; ignorar respuestas a mensajes viejos |
| Notas recibidas y su transcripción | `nota` | Al llegar la nota | **7 días**, salvo que su diálogo siga vivo | Deduplicar las reentregas del broker (QoS 1) |
| Respuestas del operador | `respuesta` | Al llegar | **7 días** | Deduplicar |
| Mensajes publicados | `salida` | Antes de publicar (intención → publicación → marca) | Publicados: **7 días**. Pendientes de publicar: **nunca se borran** | Cola D17, sincronización tras el corte de enlace |
| Consumo por nota y carril (pasos, tokens, ms, corte) | `consumo` | Al cerrar cada nota | **30 días** | Informe de consumo y de cortes |

Reglas:

- **Un reinicio no pierde nada que importe:** el diálogo se guarda antes de publicar cada mensaje y el `Dialogo`
  nuevo lo recarga por `nota_id` (o por el id del mensaje al que responde el operador).
- **Lo pendiente de publicar no se purga:** una alerta o un evento que no llegó a salir sigue en la cola hasta publicarse.
- **No se usa memoria de una nota para interpretar otra:** cada diálogo es independiente.
- **Si la base no responde**, el diálogo sigue en memoria y se persiste cuando vuelve; la alerta de seguridad sale antes
  de escribir en la base.
- El reloj de la purga es el de la base de datos (tiempo real), no el simulado de la planta.

Cortes por nota (`control/presupuesto.py`): 12 pasos, 4.000 tokens, 120 s en total y 3 repeticiones del mismo paso;
configurables por `PRESUPUESTO_PASOS`, `PRESUPUESTO_TOKENS` y `TIMEOUT_NOTA_S`. La ruta de seguridad se mide pero no se corta.
