# ACT-P-005-DECISION-MAP-001 — prepare the PAPER fill-policy decision contract

Status: READY for manual executor/Qwen review. This is a planning/analysis task with one Obsidian-note write; it is not an implementation authorization.

## Objective

Use the repository and Obsidian as shared context to produce one complete, recommendation-backed decision matrix for ACT-P-005: PAPER fill generation, costs, partials, gaps, and intrabar ambiguity. Identify only semantics that cannot be derived from the approved plan/current contracts. Do not invent financial values or approve a policy on the master's behalf.

## Preconditions and authority

- Workspace: `/home/adrian10/bot_trading`; branch `master`; expected HEAD `2cbd689117469d3fa7a1093c066b245336dc8653`.
- The worktree is intentionally dirty. Capture full status; preserve all existing changes. Only the decision draft below may change. Stop if HEAD differs or the draft SHA is not `b5c658bd825e9a408a97e78392f6d73504e591d9f63aa486794570070ecb9e8d`.
- Read the root `trading-bot-action-plan.md`: ACT-P-005 row, §10 decision table, relevant P-002/003/004 criteria, and §14.22–14.28.
- Read `backend/src/models/paper_contracts.py`, the PAPER fill application/accounting paths in `backend/src/persistence/sqlite_store.py`, and `handoff-act-p-002-sell.md`.
- Read Obsidian `Seguimiento.md`, `Decisiones/Registro de decisiones.md`, and `Decisiones/ACT-P-005-fill-policy-draft.md`.
- The root action plan remains the authority for task state. ACT-P-004 is accepted. ACT-P-002/003 are globally pending; assess dependencies by exact criterion, not status label alone.

## Verified boundary

`PaperFill` represents an already determined execution; the store journals and applies that fill but does not choose its execution price, timing, quantity, or fee. Accepted accounting distinguishes unknown commission (`None`) from known zero, and known fee accounting is limited to the approved quote-asset semantics. Results are not net of unrecorded spread/slippage. Do not reopen those accepted accounting rules without a reproducible defect.

## Questions to map together

For each item, state definition, units, scope, period/window, explicit input/provenance/version, deterministic outcome when unavailable, and the tests needed:

1. What makes a LIMIT/MARKET order eligible to fill; a price touch alone is not sufficient.
2. Which explicit price reference determines a simulated fill (bid/ask/mark/trade/OHLC), by side and order type; how spread is represented.
3. Gap, latency and slippage treatment, without proposing numeric rates.
4. Commission source/schedule, asset and unknown-vs-zero behavior for generated fills, consistent with accepted ledger semantics.
5. Partial-fill capacity, quantity and timing; no fabricated volume or implicit remainder behavior.
6. When OHLC permits both stop and target in one bar but not their order, whether the result is explicitly ambiguous or follows another documented conservative rule.

## Recommendation framework (not a decision)

- Use explicit, immutable, versioned input snapshots; keep execution policy separate from `StrategyConfig`.
- Missing price/fee/volume inputs must not be estimated or treated as zero. Surface a deterministic no-fill/incomplete/ambiguous result for the master to choose.
- Prefer marking an unresolved same-bar stop/target ordering as ambiguous rather than inventing an intrabar sequence.
- No percentages, fee schedules, spread values, slippage values or operational limits are requested to write an eventual validator; tests use fixtures only.

## Authorized write scope

Update only `docs/obsidian/Decisiones/ACT-P-005-fill-policy-draft.md` with verified facts, the decisions still open, and a recommendation for each. Do not edit source code, tests, the root plan, other Obsidian notes, or task prompts. If a new financial choice or missing dependency appears, report it in that decision table; do not resolve it silently.

## Deliverable and stop

- A single concise matrix grouping all unresolved decisions, with citations to code/plan/accepted slices and a recommended option for each.
- Explicitly state whether any ACT-P-002/003 criterion needed for this analysis is actually missing; do not infer completion of either global task.
- No tests are needed for this read-only planning pass. Do not start implementation, call external services, stage, commit, push, or advance to another task.
- Stop after updating the one allowlisted decision draft and report the file path for master's review.
