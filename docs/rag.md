# RAG, tools y memoria

## Qué se consulta por RAG y qué por API o SQL

| Dato | Fuente | Por qué no por RAG |
|---|---|---|
| Límites de alarma de cada sensor, estado y roles de los equipos | `GET /api/planta` (tool `equipos`) | Son los valores que valen. Los manuales traen otros (Rev. C) o los de otros modelos |
| Historial de sensores | `GET /api/historian` (tool `historial_sensores`) | Series numéricas, se agregan con código |
| Órdenes anteriores | `GET /api/historial-ordenes` (tool `historial_ordenes`) | Filtro por equipo y fecha, orden cronológico |
| Stock, costo, lista de materiales | `materiales.yaml` (tool `inventario`) | Cantidades exactas: un fragmento parecido no sirve |
| Tareas estándar y no estándar, horas, repuestos | `materiales.yaml` (tool `catalogo_tareas`) | Catálogo cerrado; las reglas de aprobación se calculan en código |
| Procedimientos, causas probables, correspondencia de códigos de falla (MBP-4 §6), reglas de la operadora, contrato | **RAG** (tool `buscar_manuales`) | Texto libre; la conclusión necesita su cita |

Regla: **un número que decide algo (límite, stock, precio) nunca sale del RAG**. El RAG aporta el procedimiento y
la cita de la sección; cada conclusión lleva su cita (`TX350-D §3.2`, `MBP-4 §6`, `historial OT-25-0026`).

## Corpus y metadatos

`src/sistema/datos/documentos/` es una copia de `fragmentos.jsonl` e `indice.json` de la planta (semilla 2026; el
mismo contenido que sirve `/api/documentos`). Cada fragmento (`conocimiento/corpus.py`) tiene: equipo(s),
fabricante, documento, sección, cita, página, **vigente** y **aplica**.

Trampas del corpus y cómo se manejan:

- **Rev. C vencida** (`vigente: false`): no se devuelve salvo `incluir_vencidos=true`.
- **No aplica a PIA-3** (otros modelos de la familia, PIA-1/PIA-2, opcionales, instalación, preservación,
  precios del contrato anterior): se marca `aplica: false` y se degrada a la mitad del puntaje. No se excluye porque
  una pregunta puede pedirlo.
- **Códigos del fabricante** (`TX-T07`, `FC-T02`) -> F01…F10: la correspondencia está en `MBP-4 §6`, que se recupera
  como cualquier otra sección; el código se cita, no se traduce a mano.
- **Texto libre de campo** en las órdenes: el tokenizador normaliza tildes y recorta a 6 letras.

## Recuperación: léxico, denso e híbrido

- **Léxico:** BM25 sobre cuerpo + título (x3) con los filtros de vigencia y aplicabilidad.
- **Denso:** `intfloat/multilingual-e5-small` (ONNX, MIT, revisión `614241f6…`, sha256 verificado en el build), servicio
  `emb` con 0,5 CPU y 1 GB. Se usa como pide su model card: **`passage: ` al indexar y `query: ` al consultar**, mean
  pooling, vectores normalizados y coseno. Los 701 fragmentos son 710 pasajes (los largos se parten por párrafos a
  ~1.400 caracteres, con el título delante y la misma cita). **Los embeddings de los pasajes se calculan en el build**
  y el servicio los carga en la tabla `fragmento_vec` de pgvector al arrancar, con los metadatos de equipo, fabricante,
  documento, sección, cita, vigente y aplica. No se descarga nada en runtime.
- **Híbrido:** fusión RRF (k = 60) con el léxico al doble de peso que el denso; lo que no aplica se degrada a la mitad.
  Si `emb` no responde, la búsqueda sigue solo con el léxico.

Medición con las 71 preguntas oficiales (`evaluacion/retrieval.py`, resultado en `docs/resultados/retrieval.json`):

| Variante | MRR | hit@1 | hit@5 | recall@5 | recall@10 | distractor@5 | Rev. C antes que D | ms/pregunta |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BM25 oficial (línea base) | 0,573 | 0,352 | 0,845 | 0,664 | 0,772 | 0,099 | 39 | 0,4 |
| Léxico (propio) | 0,679 | 0,535 | 0,859 | 0,707 | 0,786 | 0,014 | 0 | 0,4 |
| Denso con prefijos | 0,660 | 0,549 | 0,789 | 0,631 | 0,739 | 0,028 | 0 | 33 |
| **Híbrido con prefijos** | **0,683** | 0,563 | 0,859 | 0,702 | 0,779 | 0,014 | 0 | 35 |
| Denso **sin** prefijos | 0,610 | 0,479 | 0,775 | 0,615 | 0,707 | 0,014 | 0 | 31 |
| Híbrido **sin** prefijos | 0,670 | 0,549 | 0,859 | 0,676 | 0,765 | 0,014 | 0 | 35 |

Lectura:

- Todas las variantes propias superan a la línea base oficial en MRR, hit@1 y recall@5, y ninguna deja pasar la
  Rev. C antes que la D (39 de 71 en la base). Esa mejora viene sobre todo de los filtros de vigencia.
- **El denso solo es peor que el léxico** en hit@5 y recall@5; el híbrido lo alcanza, no lo supera con holgura
  (MRR +0,004, hit@1 +0,03, recall@10 −0,007). Con 71 preguntas, diferencias de ±0,02 son ruido.
- **Los prefijos importan:** sin ellos el denso baja 0,05 de MRR y 0,10 de hit@1; en el híbrido el efecto es menor
  (−0,013 de MRR, −0,026 de recall@5).
- El peso 2:1 se eligió después de mirar 7 configuraciones sobre estas mismas preguntas: el número es algo optimista.
- Costo: ~35 ms por consulta (embedding de la pregunta + pgvector), contra 0,4 ms del léxico.

## Tools (Anexo 1.3)

`src/sistema/herramientas/`: seis tools de solo lectura, todas con entrada Pydantic `extra="forbid"` y `strict=True`
(un argumento de más o un tipo equivocado se rechaza, no se corrige), timeout por intento, reintentos solo ante
errores transitorios (red, 5xx, timeout) y un resultado uniforme `ResultadoTool` (`ok`, `datos` o `error`, `intentos`,
`ms`). Una tool nunca lanza hacia el llamador. Tests en `tests/unit/test_herramientas.py`.

Servidor MCP: `python -m sistema.herramientas.servidor_mcp` (stdio) expone las seis; `buscar_manuales` usa el híbrido si hay `EMB_URL`. Probado con el cliente MCP real,
en memoria y por stdio.

## Memoria

Verificada en V6 contra las tablas reales: ver [memoria.md](memoria.md). El diálogo de cada nota (mensaje de tablet,
nota, borrador e intento) vive en `dialogo` y `mensaje_nota` de Postgres y sobrevive a un reinicio, incluso a mitad de
una pregunta de equipo o de una corrección.

## Pendiente

- Context builder (diseño) para el análisis de la Entrega 2.
