# Armado del contexto (context builder)

Qué le damos a cada consumidor de contexto, de dónde sale, en qué orden y cuánto pesa. El sistema tiene **tres
armados** distintos; ninguno pasa por un modelo para decidir qué entra. Lo que entra lo decide código, y
cada dato viene con su fuente.

> Regla de fondo (ver `docs/rag.md`): **límites de alarma, stock, estado de equipos y catálogo de tareas salen
> de la API o de datos estructurados (SQL/YAML), nunca del RAG.** El RAG aporta procedimientos, causas
> probables y recomendaciones, siempre con cita y solo de la revisión vigente.

## 1. Contexto de una consulta a las tools (agente que razona)

Lo arma quien llama a las tools (`herramientas/langchain.py`, o un cliente MCP). Cada tool devuelve un
`ResultadoTool` acotado:

| Qué | Fuente | Tool | Tope |
|---|---|---|---|
| Equipos, nombres informales, límites de alarma, estado | API de la planta (`/api/planta`) | `equipos` | todos los equipos de la planta |
| Muestras de un sensor | API (`/api/historian/{tag}/{variable}`) | `historial_sensores` | `ultimas` ≤ 1440, por defecto 60 |
| Órdenes anteriores | API (`/api/historial-ordenes`) | `historial_ordenes` | `limite` ≤ 200, por defecto 20 |
| Stock, costo y lista de materiales | `datos/materiales.yaml` | `inventario` | — |
| Tareas estándar / no estándar | `datos/materiales.yaml` | `catalogo_tareas` | — |
| Procedimiento, causa probable, recomendación | RAG (léxico + e5-small, pgvector) | `buscar_manuales` | `k` ≤ 10, texto recortado a 1200 caracteres |
| Código de falla F01–F10 ↔ fabricante | tabla extraída de `MBP-4 §6.1` del corpus | `traducir_codigo` | un código por llamada |

**Orden y desempate.** Primero lo estructurado (API/SQL/YAML: exacto y actual), después lo documental. Una
respuesta que mezcla ambos cita cada dato con su origen: `equipos` y `inventario` no llevan cita (son la fuente),
`buscar_manuales` y `traducir_codigo` sí (`TX350-D §3.2`, `MBP-4 §6.1`). Si un manual y un dato estructurado
discrepan en un número (p. ej. un límite), manda el dato estructurado.

**Vigencia.** `buscar_manuales` excluye por defecto las revisiones reemplazadas (TX350-C); `incluir_vencidos=true`
las trae marcadas `vigente: false`. Medido: la Rev. C aparece antes que la D en 39 de 71 preguntas si no se
filtra, y en 0 si se filtra (`docs/resultados/retrieval.json`).

**Presupuesto.** Cada sesión de nota (`control/presupuesto.py`) corta en 12 pasos, 4000 tokens, 120 s o 3
repeticiones idénticas del mismo paso. Una respuesta de `buscar_manuales` pesa ≈ 5 × 300 tokens: cabe 2–3
veces antes del tope de tokens, que es lo que se busca: consultar, no acumular.

## 2. Contexto del respaldo LLM (`respaldo.py`)

Solo se usa cuando el parser determinista **no** interpretó un tramo que sí menciona un equipo o variable, o
cuando una respuesta del operador no se entendió. El modelo (`qwen3.5:2b`, Q4_K_M, `num_ctx=2048`) nunca confirma un
parte ni decide una regla: devuelve filas candidatas que pasan por la misma validación determinista.

Orden del prompt y por qué:

1. **Instrucción fija** (≈ 120 tokens): qué es la nota, que tiene errores de reconocimiento, una fila por equipo y variable,
   no inventar números.
2. **Catálogo** (de `/api/planta`, ≈ 250 tokens): `TAG (tipo): 4 nombres informales` por equipo. Es lo único que el
   modelo puede nombrar; el esquema JSON está **restringido a esos TAGs y variables** (`esquema_para`).
3. **Variables y unidades** (≈ 120 tokens): las que tiene cada tipo de equipo, «kilos» = kg/cm².
4. **Ejemplos** (≈ 150 tokens): 4 pares nota → filas, fijos.
5. **El texto del tramo** (o, para una corrección: el parte actual + la respuesta del operador).

Total medido: ≈ 690 tokens de entrada y 50–90 de salida. `num_ctx=2048` deja ≈ 1300 de margen, `num_predict=350`
corta una salida desbocada. Presupuesto propio por llamada: 4 pasos, 1500 tokens, 45 s (`RespaldoLLM`).

**Qué no entra:** ni el RAG, ni stock, ni límites de alarma, ni historial. El modelo solo transcribe; no opina.
**Después de la respuesta:** `validar()` descarta filas de equipos que la nota no menciona, números que no
aparecen en el texto y variables fuera del catálogo del equipo.

## 3. Contexto de un Escalado (`deteccion/eventos.py`)

Un Escalado (evento a `plant/events`) lleva en `resumen` todo lo que quien lo recibe necesita para actuar sin
volver a preguntar. Se arma **por código, sin modelo**, en este orden:

1. **Qué pasó:** la condición que lo disparó y el equipo (campo `tipo`/`equipo`).
2. **Parte del equipo:** las filas de esa nota para ese equipo (`variable valor unidad`, o la observación).
3. **Fotos:** cada foto de las filas, con su sha256 si ya bajó (`/media/... (sha256 ab12…, guardada)`) o
   «pendiente de descarga» si no. Fuente: `Fotos` (volumen local + tabla `foto`).
4. **Tendencia de las últimas 3 h** (B-3): de `GET /api/historian/{tag}/{variable}` con `desde/hasta`; para bombas, presión
   de descarga y vibración; para filtros, presión diferencial: «sube / baja / estable de A a B». Es **contexto, no
   decisión**: si el historian no responde, el evento sale igual sin tendencia.

**Presupuesto:** una línea por dato; el `resumen` completo cabe en unos pocos cientos de caracteres, y las
muestras crudas del historian nunca se copian (solo primera, última y sentido).

## 4. Qué cambia cuando algo falla

| Falla | Qué entra en el contexto |
|---|---|
| El historian no responde | Se omite la tendencia; el evento y el parte salen igual |
| La foto no bajó | Entra la URL marcada «pendiente de descarga» |
| El servicio de embeddings no responde | `buscar_manuales` usa solo el recuperador léxico (medido: hit@5 0.859, igual que el híbrido) |
| El LLM no responde o excede su presupuesto | No entra nada del LLM; sigue el camino determinista |
| El rango histórico no está disponible | No se opina sobre el valor dictado (nunca se bloquea un parte) |

## Fuera de alcance de esta entrega

- No hay un "orquestador" que decida qué contexto pedir según la pregunta: el agente de esta entrega no responde
  preguntas libres; las tools están disponibles (LangChain y MCP) para quien las consuma.
- El recorte de contexto por relevancia (reranking) no se hizo: el léxico y el híbrido empatan en hit@5 con 71 preguntas.
