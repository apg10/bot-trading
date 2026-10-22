# Trading bot — Inicio

> Dashboard de orientación. **Fuente de verdad:** el plan de acción en la raíz del repo.
> Revisión de esta vista: 2026-10-21. Comprueba el plan antes de actuar.

## Estado breve

- Último código publicado: `2cbd689` (`feat(paper): gate atomic acceptance with risk filters`), `master` sincronizada con `origin/master`.
- ACT-P-001, ACT-P-002 y ACT-P-003 globales: **PENDIENTES**. Sus slices aceptados no cierran los criterios globales restantes.
- ACT-P-004: **ACEPTADO** tras risk policy pura, filtros versionados, aceptación atómica/concurrente y regresión backend (`1,784 passed`, 0 fallos, 0 skips, 1 warning deprecado).
- ACT-P-005: semántica aprobada en §14.29. El evaluator puro reporta 79 passed pero sigue `REQUIERE_CAMBIOS`: capacity de otro símbolo y reservation sin cuenta pueden pasar. El rework vigente está anotado en §14.35 y [[Handoffs/ACT-P-005-fill-simulator-pure-review-5]].
- PAPER sigue en desarrollo, no es ciclo autónomo ni matching de exchange. LIVE y envío externo permanecen fuera de alcance.

## Navegación de la bóveda

- [[Seguimiento]] — resumen del estado y regla de selección del siguiente task.
- [[Mapa del proyecto]] — componentes y límites funcionales.
- [[Decisiones/Registro de decisiones]] — decisiones aceptadas, parciales y pendientes con su fuente.
- [[Handoffs/Registro de handoffs]] — recibos/evidencia y distinción entre handoff, prompt y archivo histórico.
- [[Decisiones/Plantilla de decisión]] — registrar una decisión sin inventar estado.
- [[Handoffs/Plantilla de handoff]] — formato para una entrega verificable.

## Fuentes canónicas

- [Plan de acción](../../trading-bot-action-plan.md) — única autoridad para estados, dependencias, aceptaciones y selección del siguiente microtask.
- [Plan técnico](../../trading-bot-development-plan.md) — requisitos y arquitectura objetivo.
- [Brief visual](../../trading-console-visual-brief.md) — requisitos de interfaz.
- [Inventario visual ACT-T-001](../../ACT-T-001-inventario-visual-mapa-vistas.md) — vistas, paneles y gaps observados.
- [Plantilla Qwen](../../qwen-task-template.txt) — formato de autorización/implementación.

Los archivos canónicos están fuera de esta bóveda dedicada. Los enlaces permiten abrirlos, pero los resúmenes de aquí no los sustituyen ni convierten su contenido en una copia sincronizada.

## Reglas

1. Los estados de Obsidian son una vista; no actualizan ni reemplazan el plan raíz.
2. Cada hecho importante debe indicar fuente, fecha y alcance; separar evidencia, objetivo y opinión.
3. No guardar secretos, credenciales, bases de datos, logs ni datos sensibles.
4. Cada implementación requiere autorización, allowlist y checks explícitos. No autoavanzar.
