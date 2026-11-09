# Matriz de cumplimiento · Entrega 1

Requisitos del **Enunciado (Opción A)** y del **Anexo de la Entrega 1** contra el estado real del repositorio, con la
evidencia de cada uno. Revisión hecha leyendo el código, los tests y las corridas; no se cambió código para escribirla.

Leyenda: ✅ cumple y tiene evidencia · 🟡 cumple en parte (se dice qué falta) · ❌ no cumple · ❔ no se puede verificar desde acá.

## Repo y ejecución

| Requisito | Estado | Evidencia |
|---|---|---|
| Repo público en GitHub `nombre-apellido` | 🟡 | Nombre decidido (D11): **`tupac-flores`**, ya creado como `tupacflores-rgb/tupac-flores` (dato del usuario; no lo verifiqué desde acá). La rama `entrega-1` **todavía no se subió** (sin push, a pedido) |
| En la raíz: `README.md`, `docker-compose.yml`, `tests/` | ✅ | Raíz del repo (`ls`) |
| `docker compose up` levanta el sistema al lado de la planta | ✅ | `README.md` (cómo levantarlo); verificado desde un clon limpio (ver «Verificación con clon limpio» en `docs/resultados/linea_base.md`) |
| Servicios solo en `planta-lan` (external), label `academia.planta: pc` | ✅ | `tests/infra/test_compose.py` (`test_red_planta_lan_externa`, `test_servicio_solo_en_planta_lan`, `test_servicio_con_label_de_planta`) |
| Suman ≤ 4 CPU y 8 GB | ✅ | 4,0 CPU y 7,5 GB: `test_la_suma_exacta_de_la_planta_con_llm_es_4_cpu_y_7_5_gb` |
| `HTTP(S)_PROXY=http://enlace:3128`, `NO_PROXY` con `broker,sim,localhost` | ✅ | `docker-compose.yml` (`x-proxy`): `NO_PROXY` es un superconjunto (`broker,sim,db,asr,emb,llm,localhost,127.0.0.1`); `test_proxy_de_salida_por_el_enlace` |
| Sin política `restart` (el simulador apaga y enciende los contenedores) | ✅ | `test_servicio_sin_politica_de_reinicio` |
| `planta_contrato` por pip, sin copia de los modelos | ✅🟡 | `pyproject.toml` (commit fijo de `NovitSoftware/academia2026-tp`); `tests/test_contrato_importa.py`. **Provisional**: la URL de `docs/contrato.md` devuelve 404 y falta confirmación de Novit |
| Latido `sistema/estado` cada 30 s con `sistema`, `enlace_ok`, `pendientes_sincronizar` | ✅ | `src/sistema/latido.py`; `tests/unit/test_supervisor_latido.py`; corrector F2 = 100 % |
| Primer latido antes de 180 s desde el arranque en frío | ✅ | Medido en la verificación con clon limpio (tiempo en `docs/resultados/linea_base.md`) |
| PostgreSQL 16 con pgvector dentro del compose | ✅ | `docker/db/Dockerfile` (`pgvector/pgvector:pg16`); `tests/integracion/test_base_real.py`; tabla `fragmento_vec` (`emb/servidor.py`) |

## A · Parte por voz

| Requisito | Estado | Evidencia |
|---|---|---|
| Escucha `planta/recorridas` y baja el **audio** | ✅ | `src/sistema/integraciones/mqtt.py`, `pipeline.py`; corrector A1 = 100 % |
| …y baja las **fotos** | ✅ | `src/sistema/fotos.py`: se bajan a un volumen (`agente-fotos`) con el sha256 como nombre, asociadas a su fila y a la nota (tabla `foto`); timeout y reintentos; si falla se guarda la URL y queda `pendiente` hasta que baje. `tests/unit/test_fotos.py` (14). El análisis de imágenes es de la Entrega 2 |
| Transcribe con un modelo local (lista cerrada) | ✅ | Local: sí (`asr/`, faster-whisper `small` int8; funciona sin enlace, corrector E1 = 100 %). Lista cerrada: Modelos locales de libre elección según aclaración de Novit, confirmada por el usuario el 09/11/2026. |
| Extrae con structured output validado a `FilaParte` | ✅🟡 | `Fila.a_contrato()` valida contra `FilaParte`; el respaldo con LLM usa `with_structured_output` hacia `FilaParte` (`src/sistema/respaldo.py`, `tests/unit/test_respaldo_llm.py`). El camino principal es un **parser determinista**, no structured output |
| Una fila por equipo y variable, aunque la nota los mezcle | ✅ | `src/sistema/voz/parser.py`; `tests/unit/test_parser_respuestas.py` |
| Hora = `ts` de la nota, no el reloj de la máquina | ✅ | `tests/unit/test_seguridad.py::test_la_alerta_usa_ids_deterministas_y_el_ts_de_la_nota`; los eventos usan `ts_de(est)` |
| Fotos asociadas a la fila que corresponde | ✅ | `tests/unit/test_fotos_contexto.py` (cada «le saqué foto» se lleva la siguiente foto) |
| Nombres informales contra `/api/planta`; ambiguo («la bomba») → pregunta | ✅ | `tests/unit/test_dialogo.py` (pregunta con opciones), `test_normalizacion_difusa.py`; corrector A4a = 100 % |
| «kilos» = kg/cm²; manómetros kg/cm² (B-101), psi (B-102), bar (B-103) | ✅ | `Catalogo.unidad_manometro`; `tests/unit/test_dialogo.py` (1640 psi en B-102) |
| Diferencial «dieciocho» → se pregunta | ✅ | `tests/unit/test_dialogo.py::test_valor_ambiguo_borrador_con_candidato_provisional_y_dos_opciones` |
| Rango histórico (`/api/historian`, 45 días); fuera de rango pide confirmación | ✅ | `src/sistema/dominio/rango.py`; `tests/unit/test_rango.py` (el borrador avisa; con el «mil» perdido lo muestra como provisional y sin opciones) |
| Tablet: borrador en `pantalla.filas`, con opciones cuando hay que elegir | ✅ | `tests/unit/test_dialogo.py`; corrector A2/A3 |
| Entiende respuestas «como se habla en campo» y reenvía el borrador corregido | ✅ | `src/sistema/voz/respuestas.py`; `tests/unit/test_parser_respuestas.py` |
| El parte pasa de Borrador a Confirmado | ✅ | `EstadoParte`; `test_confirmacion_manda_aviso_guardado…` |
| Notas `VIVO-…`: sin parte esperado, pueden no traer nada | ✅ | `voz/dialogo.py::es_nota_vivo`: una nota `VIVO-` no tiene parte esperado; si no saca filas, aviso con `espera_respuesta=false` (no pide repetir, no gasta el LLM) y queda `sin_filas`; con contenido se trata como una nota normal. `tests/unit/test_vivo.py`. No se probó con un audio `VIVO-` real (el simulador no los emite) |

## B · Detección en planta (en código, sin LLM)

| Requisito | Estado | Evidencia |
|---|---|---|
| Cada parte confirmado y cada desvío de `planta/sensores/#` genera un `EventoSistema` (id idempotente, origen `parte`/`sensor`, `nota_ids`) | ✅ | `src/sistema/deteccion/eventos.py`, `sensores.py`; `tests/unit/test_eventos_sync.py`, `test_sensores.py` |
| Límites de `/api/planta`, no del manual Rev. C; decisión determinista | ✅ | `Catalogo.limites`; `eventos.es_anomalia`; sin LLM en el camino |
| Sin novedad o Escalado, con contexto (parte, fotos, tendencia de las últimas horas) | ✅ | `tests/unit/test_fotos_contexto.py::test_el_escalado_lleva_parte_fotos_y_tendencia_y_el_sin_novedad_no`. El contrato no tiene campos para eso: va en `resumen` |
| Se escala una anomalía sostenida, no un pico aislado | ✅ | `tests/unit/test_sensores.py::test_un_pico_aislado_no_escala` |
| «Parada, en reserva» no es anomalía | ✅ | `OBSERVACIONES_NORMALES`; `test_una_bomba_parada_en_reserva_se_ignora` |
| Alerta S1–S4: detector local, sin nube | ✅ | `src/sistema/deteccion/seguridad.py`; `asr/` local |
| Aviso en la tablet con `espera_respuesta: false` | ✅ | `alertas.construir`; `tests/unit/test_salida.py` |
| `AlertaSeguridad` en < 30 s reales | ✅ | Corrector: 4,0–5,0 s. 50 disparos con carga al tope: 50/50, p99 19,3 s (`docs/resultados/latencia_alerta_final_llm.json`). La puerta **interna** de 15 s no se cumple (ver INFORME) |
| Sin falsas alarmas | ✅ | 0 en 102 voces de referencia y en los 50 disparos; corpus negativo en `tests/unit/test_seguridad.py` |

## Tools y MCP, control y evaluación

| Requisito | Estado | Evidencia |
|---|---|---|
| Siete tools de solo lectura con Pydantic estricto (`extra="forbid"`) | ✅ | `src/sistema/herramientas/catalogo.py`; `tests/unit/test_herramientas.py` |
| Al menos una expuesta por MCP | ✅ | `servidor_mcp.py`; cliente MCP real en `test_herramientas.py` y por stdio con `langchain-mcp-adapters` en `test_langchain_tools.py` |
| Errores, timeouts y reintentos en cada tool | ✅ | `herramientas/base.py`; `test_reintenta_lo_transitorio_y_termina`, `test_timeout_corta_sin_esperar_al_hilo`, `test_error_de_datos_no_se_reintenta` |
| Tests automatizados de cada tool en `tests/` | ✅ | `tests/unit/test_herramientas.py` (una por tool) |
| Condiciones de corte, presupuesto de pasos y tokens, timeout global por nota | ✅ | `src/sistema/control/presupuesto.py`; `tests/unit/test_presupuesto.py`, `test_flujo_lcel.py` |
| Mini spec del flujo del parte (SDD) | ✅ | `docs/spec-parte.md` |
| Primera evaluación contra `/api/verdad`: aciertos por campo y si preguntó solo cuando había ambigüedad | ✅ | `evaluacion/verdad.py` lee `/api/verdad` y `/api/registro` (o una corrida del corrector) y reporta aciertos por campo, preguntas en notas ambiguas vs claras y falsos positivos/negativos. Sobre la corrida de control (13 notas, commit 55a6fe4): equipo, variable, valor y unidad 25/25; 3 notas ambiguas con 1 pregunta cada una, 10 claras con 0; 1/1 alerta de seguridad, 0 falsos. Muestra chica. `docs/resultados/verdad.md`; `tests/unit/test_verdad.py` |

## RAG, contexto y memoria

| Requisito | Estado | Evidencia |
|---|---|---|
| Indexa en pgvector con equipo, fabricante, documento, sección, cita, vigente | ✅ | `emb/servidor.py` (tabla `fragmento_vec`); `docs/rag.md` |
| Trampa: Rev. C vencida con otros límites | ✅ | 0 de 71 preguntas con la Rev. C antes que la D (base: 39). `docs/resultados/retrieval.json` |
| Trampa: secciones de otros modelos/plantas que no aplican | ✅ | Marca `aplica`; `tests/unit/test_recuperador.py` |
| Trampa: códigos del fabricante → F01–F10 con MBP-4 §6 | ✅ | Tabla F01–F10 extraída de `MBP-4 §6.1` al cargar el corpus (`conocimiento/fallas.py`); tool `traducir_codigo` con citas, también por MCP; `tests/unit/test_fallas.py` |
| recall@k con `retrieval.json` (71 preguntas) contra BM25 (hit@5 0,85, recall@5 0,66) y Rev. C antes que D | ✅ | `evaluacion/retrieval.py`: léxico, denso, híbrido y sin prefijos (`docs/rag.md`). El híbrido iguala al léxico filtrado; no lo supera con holgura |
| Cada conclusión cita su sección | ✅🟡 | Cada resultado de `buscar_manuales` lleva su cita (`TX350-D §3.2`); en la Entrega 1 no se generan «conclusiones» de análisis |
| Justifica qué va por RAG y qué por API o SQL | ✅ | `docs/rag.md` (tabla) |
| Context builder **diseñado** | ✅ | `docs/context-builder.md`: qué entra, de dónde (API/SQL vs RAG), orden y presupuesto, para las tools, el respaldo LLM y el Escalado. Es diseño; no hay un orquestador que arme contexto para preguntas libres (declarado) |
| Memoria: estado de la conversación de cada nota en Postgres + política escrita | ✅ | `docs/memoria.md`; `tests/integracion/test_base_real.py`; reinicio a mitad de diálogo en `tests/unit/test_dialogo.py` |

## Restricciones del Enunciado y entregables

| Requisito | Estado | Evidencia |
|---|---|---|
| Python ≥ 3.11 con LangChain para el núcleo de agentes | ✅ | `pyproject.toml` (`requires-python`); tools como `StructuredTool`, cadenas LCEL y callback de consumo (`src/sistema/flujo.py`, `herramientas/langchain.py`) |
| Solo modelos de la lista cerrada | ✅ | Modelos locales de libre elección según aclaración de Novit, confirmada por el usuario el 09/11/2026. |
| PostgreSQL 16 + pgvector; manuales con metadatos | ✅ | Ver RAG |
| Todo se levanta con `docker compose up` | ✅ | Clon limpio |
| README: cómo levantar y probar; qué corre en planta y qué en la nube | ✅ | `README.md` (sección «Qué corre dónde»: todo corre en la PC de planta, nada en la nube) |
| Suite de evaluación que reporta aciertos, costo y latencia | ✅ | `evaluacion/` (corrector, latencia de alerta, ASR, RAG, LLM); costo de nube = 0 |
| `INFORME.md`: modelos por tarea, proveedor, versión, parámetros, hardware de prueba, tokens por tarea, costo, conclusión | ✅ | `INFORME.md` (completado en esta revisión) |

## Verificación del repositorio (solo lectura)

- **Historial** (`git log --all`): 56 commits al momento de la revisión; ninguna ruta de `Context/`, ningún `.env` (solo `.env.example`, con valores de
  desarrollo), ningún peso de modelo (`.gguf`, `.safetensors`, `.onnx`, `.bin`…), ninguna clave privada, ningún blob de más de
  2 MB y ningún patrón de credencial en los diffs. `.gitignore` excluye `/Context/`, `.env*`, `*.pem`, `*.key`, modelos y
  corridas. El hook de pre-commit (`scripts/bloquear_privados.py`, `detect-secrets`) lo vuelve a comprobar en cada commit.
- **Pendientes que dependen de otra persona:** confirmar con Novit el origen del contrato y la decisión D16. La libertad
  de elección de modelos locales (BL-5) quedó confirmada por el usuario el 09/11/2026.
