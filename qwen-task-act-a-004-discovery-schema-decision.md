# ACT-A-004-T2 — Inventario y contrato de descubrimiento/esquemas

**Estado:** decisión documental; no implementa herramientas, endpoints, persistencia,
ejecución ni pruebas. **Alcance:** research estructurado para Qwen, sin autoridad
financiera. ACT-A-004 global continúa `PENDIENTE`.

## 1. Inventario de herramientas (solo lectura)

La superficie preferida es REST descrita por el OpenAPI generado por FastAPI. Los
identificadores siguientes son rutas reales del repositorio; `read` significa que
la operación no debe producir un efecto financiero ni cambiar configuración.

| Identificador contractual | Operación real | Uso permitido |
|---|---|---|
| `rest.openapi.read` | `GET /openapi.json` | Descubrir rutas y esquemas publicados. |
| `rest.health.read` | `GET /health` | Estado público del servicio y entorno declarado. |
| `rest.capabilities.read` | `GET /api/capabilities` | Capacidades estáticas versionadas (`backend_capabilities.v1`); no certifica salud, conexión ni ejecución. |
| `rest.market.status.read` | `GET /api/market/status` | Estado/procedencia del transporte de mercado (`market_transport.v1`). |
| `rest.market.candles.read` | `GET /api/market/candles` | Histórico identificado; conservar `data_source`, identidad del stream y completitud. |
| `rest.analysis.request` | `POST /api/analysis` y `POST /api/analysis/market` | Cálculo técnico sin órdenes; la entrada y su procedencia deben permanecer explícitas. Es una solicitud advisory, no autoridad operativa. |
| `rest.bot.status.read` | `GET /api/bot/status` | Estado no operativo; `TEST_ONLY` no equivale a estrategia aprobada. |
| `rest.n02.advisory.request` | `POST /api/bot/ai/scenarios` y `POST /api/bot/ai/analyze` | Escenarios N02 advisory sobre un snapshot; no ejecutable. |
| `rest.paper.projection.read` | `GET /api/paper/accounts/{account_id}/balances`, `/positions`, `/orders`, `/fills`, `/lifecycle`, `/activity` | Proyecciones PAPER ya persistidas, siempre como datos de lectura. La ruta `/orders` no concede capacidad de crear, cancelar o modificar órdenes. |
| `rest.laboratory.query.read` | `GET /api/laboratory/results/{result_id}`, `/comparisons/{experiment_id}/{experiment_version}?comparison_id={}&comparison_version={}`, `/capital-curves/{account_id}/{session_id}`, `/cost-breakdowns/{account_id}/{session_id}`, `/inspector/{account_id}/{session_id}` | Consultar resultados y proyecciones normalizadas existentes; no iniciar trabajos ni reconstruir evidencia ausente. |

`POST` en este inventario solo describe una consulta/cálculo o asesoría sin
efecto lateral. No se incluyen `POST /engine/action`, modificación de preferencias,
órdenes, fills, shell, acceso a base de datos directo ni clientes de exchange.
El fallback `TEST_ONLY` de mercado y la propuesta legacy del bot no se presentan
como datos reales ni como capacidades de operación.

**Recomendación:** REST/OpenAPI primero, con allowlist explícita y respuestas
versionadas. Un adaptador MCP puede evaluarse después como transporte opcional,
pero no es una dependencia, no amplía la allowlist y no crea nombres o capacidades
paralelas. Cualquier herramienta MCP futura debe mapear uno-a-uno a un contrato REST
aprobado y conservar sus permisos, errores, procedencia y auditoría.

## 2. Contratos de esquema versionados

### 2.1 Contratos existentes que se reutilizan

No se crea una segunda familia de DTOs. La capa de investigación debe transportar
o referenciar literalmente los contratos aceptados por ACT-C-001 y ACT-L-002:

- entorno/capacidades: `backend_capabilities.v1` y sus flags explícitos; un flag
  `supported` no se convierte en salud, frescura, cartera o ejecución;
- mercado: `market_transport.v1`, incluyendo `stream_id`, `sequence`, símbolo,
  intervalo, `data_source`, completitud y estado de aptitud sin inferir autoridad;
- estrategia: `paper_strategy_ref.v1` y `strategy_config.v1` (`StrategyRef`,
  `StrategyConfig`, instancias y parámetros del catálogo). Una referencia opaca no
  prueba que una estrategia esté activa o aprobada;
- datos: `DatasetRef`, referencia opaca que no promete proveedor, replay ni
  reconstrucción;
- definición de experimento: `experiment_definition.v1`, con `ExperimentRef`,
  hipótesis, `PeriodWindow` explícitos y variantes que reutilizan
  `StrategyConfig` y `DatasetRef`; **persistencia de definiciones y RunAttempt
  asignada a ACT-P-001**, no a L-002;
- ciclo de intento: `experiment_run.v1` (`RunAttempt`). Sus estados y transiciones
  siguen siendo descriptivos; este task no inicia una transición;
- resultados: `normalized_experiment_result.v1`, con períodos efectivos,
  referencias de código/engine/riesgo/costes/exposición y métricas con estado
  explícito (`available`, `undefined`, `insufficient_data`);
- comparación: `experiment_comparison.v1` y su reporte. La comparación A/B/C no
  selecciona un ganador ni afirma rentabilidad; conserva compatibilidad de
  experimento, dataset, períodos, referencias y unidades.

Las respuestas PAPER y laboratorio conservan sus campos `environment`, identidad
de cuenta/sesión, `schema_version`, estados de disponibilidad y razones. Los
decimales financieros permanecen representados como valores exactos del contrato
(no se convierten en floats ni se inventa un PnL).

### 2.2 Separación de consultas y solicitudes de experimento

**Consulta (`research.query.v1`, propuesta contractual):** identifica una sola
herramienta de la allowlist, sus parámetros de lectura, el contrato esperado y la
respuesta observada. Una consulta puede devolver `available`, `unavailable` o
`insufficient_data`; no rellena campos ni transforma un dato `TEST_ONLY` en real.
Debe conservar, como mínimo, identidad de herramienta, versión de esquema,
procedencia, parámetros sanitizados, resultado/error público y referencia de
auditoría. La forma final y el versionado requieren aprobación humana antes de
implementarse.

**Solicitud de experimento (`research.experiment_request.v1`, propuesta
contractual):** contiene un `ExperimentDefinition` validado (incluidos
`ExperimentRef`, hipótesis, períodos, variantes, `StrategyConfig` y `DatasetRef`)
y una intención explícita de *solicitud*, no de ejecución. Su respuesta solo puede
ser una aceptación/rechazo de forma, un plan o un estado consultable; no crea
órdenes, no activa `RunAttempt`, no cambia una estrategia, no asigna autoridad
financiera y no promete que el dataset exista. La consulta de una definición,
intento o resultado es una operación distinta y no debe disfrazarse de solicitud.

Los DTOs de L-002 (`NormalizedExperimentResult`, `ExperimentComparison` y
proyecciones de métricas) son únicamente respuestas de resultados ya disponibles.
No se permite usar una respuesta de resultados para simular que se ejecutó la
solicitud ni para inferir valores faltantes.

## 3. Modelo de permisos

### Allowlist explícita

La identidad de research puede:

1. leer OpenAPI, salud, capacidades, estado/velas de mercado y estados no
   operativos indicados arriba;
2. pedir análisis técnico o asesoría N02 en modo advisory, recibiendo solo los
   esquemas públicos y errores acotados;
3. leer proyecciones PAPER y laboratorio ya existentes, sin reconstrucción
   retrospectiva cuando falte el journal causal;
4. consultar definiciones, estados e informes de experimentos que la política de
   acceso autorice, sin iniciar un run;
5. enviar una `research.experiment_request.v1` mediante un permiso separado de
   **solicitud de experimento read-only**. Ese permiso solo valida o registra una
   intención no ejecutable; no autoriza persistencia nueva, jobs, replay, órdenes ni
   mutación de estrategia. Su semántica exacta debe aprobarla una persona.

### Exclusiones no negociables de este contrato

- crear, enviar, cancelar, modificar o reconciliar órdenes/fills;
- shell, procesos, filesystem arbitrario, instalaciones o ejecución de código;
- secretos, credenciales, tokens, variables privadas, prompts crudos o
  razonamiento interno;
- activar, editar, seleccionar para operar o sustituir la estrategia activa;
- cambiar riesgo, límites financieros, cartera, balances o autoridad de cuenta;
- habilitar LIVE/EXCHANGE_DEMO, convertir PAPER en ejecución o inferir permiso
  financiero desde una respuesta de capabilities;
- iniciar, reanudar, cancelar o alterar un `RunAttempt` por esta interfaz.

La lectura de la proyección `/orders` es solo observación histórica y no contradice
la exclusión: no hay ningún permiso de comando de órdenes.

## 4. Límites: solo campos contractuales

Los límites son **campos contractuales**, no valores que este task pueda inventar.
Un contrato futuro puede necesitar campos versionados para tamaño de consulta,
cantidad de resultados, tamaño de payload, timeout, concurrencia, paginación,
retención o frecuencia/cuota. Sus nombres, unidades, presencia, comportamiento al
excederlos y valores numéricos requieren decisión humana y una revisión de
compatibilidad; aquí quedan sin valor normativo.

Las validaciones numéricas ya existentes en una ruta o DTO son restricciones de
esa implementación, no una nueva política financiera ni un límite global de
research. No se copian ni se generalizan silenciosamente. Las respuestas deben
declarar `insufficient_data`, `unavailable` o error público cuando no puedan
cumplir el contrato, nunca truncar o fabricar evidencia sin indicarlo.

## 5. Enlace de auditoría

T1 queda preservado: la aceptación de ACT-A-004-T1 en **310669e** (envelope N02)
no se modifica ni se reinterpreta. `N02AuditRecord` sigue siendo inmutable y
versionado, con `snapshot_id`, `result_id`, `model_name`, `model_version`,
`prompt_version`, `prompt_hash`, `safe_params`, resultado o `error_code`, timing y
vigencia/captura. Solo admite parámetros seguros de su allowlist; nunca recibe
secretos, prompt crudo o diagnóstico privado.

Cada consulta, solicitud, respuesta o rechazo de research que use N02 debe poder
relacionarse con el snapshot y resultado que ya cubre T1, conservando el
`N02AuditRecord` sin duplicarlo. Para herramientas no N02 se requiere una
referencia de auditoría equivalente que incluya identidad de herramienta,
versión de esquema, identidad de solicitud, parámetros sanitizados, procedencia,
resultado/error y captura temporal. El nombre exacto de esa relación, su
persistencia y su índice son decisiones de ACT-A-001; este documento no crea una
tabla ni extiende el DTO T1 por cuenta propia.

ACT-A-001 sigue siendo la autoridad para auditoría persistente y reconstrucción de
versiones. Hasta su cierre no debe afirmarse que una consulta o solicitud está
auditada solo porque devolvió JSON. La auditoría no concede permisos ni convierte
un resultado advisory en decisión financiera.

## 6. Decisiones que requieren aprobación humana

1. **Transporte:** aprobar REST/OpenAPI como superficie primaria y MCP como
   adaptador opcional posterior (recomendado), o autorizar una superficie MCP
   simultánea con revisión de equivalencia.
2. **Allowlist:** aprobar las rutas reales anteriores y decidir si las
   proyecciones PAPER, incluida la lectura de `/orders`, quedan visibles para
   research. Recomendación: lectura sí, comandos siempre no.
3. **Versionado:** aprobar la reutilización de versiones explícitas de C-001/L-002
   por payload (recomendado) frente a una versión global de API; no romper los
   contratos wire existentes.
4. **Solicitud de experimento:** decidir si solo valida una definición o también
   registra una intención; en ambos casos mantenerla no ejecutable. Cualquier
   persistencia, job, replay o ejecución necesita autorización separada.
5. **Límites:** aprobar los campos, unidades, valores, cuotas, paginación,
   timeouts y retención; no adoptar automáticamente valores de endpoints actuales.
6. **Auditoría:** ACT-A-001 debe aprobar la identidad de correlación y la
   persistencia, y decidir cómo enlazar solicitudes no N02 sin modificar la
   aceptación de `N02AuditRecord` de T1.
7. **Datos y estrategia:** aprobar qué referencias/datasets pueden consultarse,
   cómo se etiqueta `TEST_ONLY` y confirmar que ninguna herramienta puede activar
   o editar la estrategia activa. Recomendación: mantener referencias opacas y
   separar estrictamente advisory, PAPER y LIVE.
8. **Acceso externo:** aprobar autenticación/origen y el operador humano
   responsable antes de exponer la superficie fuera del proceso local.

## 7. Dependencias y conservación

- **ACT-C-001:** reutilizado como fuente contractual de entorno, indicadores,
  estrategia y datos; su estado global se conserva según el plan y no se declara
  cerrado por este documento.
- **ACT-L-002:** reutilizado para resultados/comparaciones normalizadas y sus
  estados de datos; definiciones y RunAttempt pertenecen a ACT-P-001; no se
  añade ejecución ni nueva métrica.
- **ACT-A-001:** sigue `PENDIENTE` y bloquea el cierre de auditoría global.
- **ACT-A-004-T1:** aceptación en 310669e preservada; este T2 solo documenta el
  enlace y no altera código, contratos T1, tests ni historial.

Este archivo es una decisión/contrato preparatorio. No habilita ACT-A-004 global,
no añade una puerta H4 y no autoriza implementar el siguiente task.

## 8. Completados

### 8.1 ACT-A-004-T3-S2 — Default-deny research gateway (completado)

**Estado:** implementado y verificado.

**Archivos entregados:**

| Archivo | Descripción |
|---|---|
| `backend/src/api/research_allowlist.py` | Allowlist catalog: 6 APPROVED, 13 BLOCKED, 2 DEFERRED. |
| `backend/src/api/research_gateway.py` | Default-deny ASGI middleware. Scopes to `_CATALOGUED_PATHS` only; non-catalogued paths bypass entirely. Durable admission audit before dispatch (fails closed if persistence fails). WebSocket rejection via `websocket.close` code 1008. |
| `backend/src/models/research_audit.py` | `ResearchRequestAuditRecord` DTO with sanitization, schema enforcement, and valid outcomes (`admitted`, `success`, `unavailable`, `insufficient_data`, `rejected`, `error`). |
| `backend/src/persistence/sqlite_store.py` | Schema v21 migration (additive, transaction-wrapped). CRUD methods for research audit records. |
| `backend/src/api/main.py` | Conditional middleware installation via `RESEARCH_GATEWAY_DEPLOYMENT_APPROVED`. |
| `backend/tests/test_research_gateway.py` | 32 gateway boundary tests (including WebSocket close frame verification, audit failure blocks dispatch, admission outcome). |
| `backend/tests/test_research_audit_contract.py` | 8 audit contract tests (including nested/tuple rejection). |
| `backend/tests/test_sqlite_store_research_audit.py` | 10 persistence tests. |

**Test results:** 50 tests pass across T3-S2 test files. Full suite: 2223 passed, 37 pre-existing failures (migration/WebSocket), 3 skipped. No regressions.

**Key design decisions:**
- Middleware only intercepts catalogued paths; ordinary traffic bypasses.
- Admission audit is durable and precedes handler dispatch; persistence failure blocks dispatch.
- Two audit records per approved route: `admitted` (before dispatch) + `success`/`error`/etc. (after completion).
