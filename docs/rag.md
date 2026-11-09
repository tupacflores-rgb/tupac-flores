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

## Recuperador actual (léxico)

BM25 sobre cuerpo + título (el título pesa x3), con los filtros de arriba. Medición con la suite oficial de 71
preguntas (`python evaluacion/retrieval.py --docs "Context/Clon del REPO"`; resultado en
`docs/resultados/retrieval.json`):

| Métrica | BM25 oficial | Recuperador propio |
|---|---:|---:|
| MRR | 0,573 | **0,679** |
| hit@1 | 0,352 | **0,535** |
| hit@5 | 0,845 | **0,859** |
| recall@5 | 0,664 | **0,707** |
| distractor@5 (revisión vieja antes que la relevante) | 0,099 | **0,014** |
| Rev. C antes que Rev. D (top-5, de 71 preguntas) | 39 | **0** |

La mejora más grande es de vigencia (la Rev. C ya no se cuela); la mejora de recall es modesta. Lo que falta para
un salto de recall es el componente denso (ver más abajo).

## Tools (Anexo 1.3)

`src/sistema/herramientas/`: seis tools de solo lectura, todas con entrada Pydantic `extra="forbid"` y `strict=True`
(un argumento de más o un tipo equivocado se rechaza, no se corrige), timeout por intento, reintentos solo ante
errores transitorios (red, 5xx, timeout) y un resultado uniforme `ResultadoTool` (`ok`, `datos` o `error`, `intentos`,
`ms`). Una tool nunca lanza hacia el llamador. Tests en `tests/unit/test_herramientas.py`.

Servidor MCP: `python -m sistema.herramientas.servidor_mcp` (stdio) expone las seis. Probado con el cliente MCP real,
en memoria y por stdio.

## Memoria

Verificada en V6 contra las tablas reales: ver [memoria.md](memoria.md). El diálogo de cada nota (mensaje de tablet,
nota, borrador e intento) vive en `dialogo` y `mensaje_nota` de Postgres y sobrevive a un reinicio, incluso a mitad de
una pregunta de equipo o de una corrección.

## Pendiente

- Índice denso en pgvector (`multilingual-e5-small`, `passage: `/`query: `, coseno, ablación sin prefijos) y
  recuperador híbrido. Necesita un modelo en la imagen y una cuota de CPU propia.
- Context builder (diseño) para el análisis de la Entrega 2.
