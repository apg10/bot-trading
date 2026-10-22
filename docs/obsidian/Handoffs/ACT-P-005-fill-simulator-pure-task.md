# ACT-P-005-FILL-SIMULATOR-PURE-001 — deterministic fill candidate evaluator

Status: PREPARED for manual Qwen execution. Implement only this pure slice; stop at handoff. This is not authorization to integrate persistence or start a full PAPER cycle.

## Authority and baseline

- Workspace: `/home/adrian10/bot_trading`; branch `master`; HEAD when prepared: `2cbd689117469d3fa7a1093c066b245336dc8653`.
- The worktree is intentionally dirty. Capture status/HEAD before editing and preserve all existing changes. If HEAD differs or either allowed new path already exists/changed, STOP/BLOCKED.
- Read plan §§7, 10, 14.22–14.29; Obsidian `Seguimiento.md`, `Decisiones/ACT-P-005-fill-policy-draft.md`; `PaperOrder`, `PaperFill`, `PaperReservationRef`, risk/filter DTOs, and the accepted fill application/accounting code.
- P-002/P-003 global PENDING do not block criteria whose exact slices are accepted; do not infer their global completion. P-004 is ACCEPTED.

## Approved semantic contract

1. The evaluator is pure, deterministic, Decimal-exact, immutable, versioned and separate from `StrategyConfig`, risk policy and SQLite. It returns a candidate/result; it does not persist or mutate `PaperOrder`, reservations or balances.
2. Consume explicit `PaperOrder`, active reservation snapshot, execution-policy snapshot, market-execution snapshot and fee snapshot. Every identity/version/time needed to trace inputs is explicit; no silently substituted marks, OHLC close, fee, latency, capacity or cost values.
3. Order eligibility requires status `ACCEPTED` or `PARTIALLY_FILLED`, a matching active reservation, the side/order-type price condition, and explicit executable capacity. A price touch is not sufficient to fill.
4. BUY uses explicit `ask`; SELL uses explicit `bid`. LIMIT BUY is price-eligible only when `ask <= limit_price`; LIMIT SELL only when `bid >= limit_price`. The eligible fill price is that side quote, subject to explicit capacity/reserve. No fallback to `mark`, `last`, OHLC, or an unapproved source.
5. Spread is already reflected in bid/ask execution price; `ask - bid` may be reported separately but must not be charged again to P&L.
6. Gap detection uses the snapshot's explicit interval and bar times. If `bar_open_time_ms > prior_bar_open_time_ms + interval_ms`, return `FILL_GAP_UNCERTAIN` and no fill. Missing required continuity timestamps fail closed.
7. Fill quantity is bounded by `min(order_remaining_qty, executable_capacity_qty, reservation_capacity)`. Missing/zero capacity means no fill; never manufacture a full fill. BUY quote reserve and MARKET `max_quote_amount` must cover the resulting notional; SELL BASE reserve must cover resulting quantity.
8. A fee snapshot supplies an explicit, versioned `Commission` for the exact resulting candidate fill quantity. Commission must be known and in symbol QUOTE; zero is allowed only when explicitly supplied as a known zero. Missing/unknown or quantity-mismatched fee snapshot => no fill. Do not calculate rates or derive a fee from `PaperRiskPolicy`.
9. `latency_slippage_mode` is explicit, never defaulted. `UNMODELLED` uses the side quote, returns a gross/not-net status and does not assign zero latency/slippage. `MODELLED` is accepted only with explicit versioned `modelled_execution_price` and `latency_ms`; consume those inputs without inventing a formula. Missing required modelled inputs => no fill.
10. If a supplied competing STOP_LOSS and TAKE_PROFIT for the same symbol/account/protection group are both reachable in one OHLC bar and the snapshot gives no event order, return `FILL_AMBIGUOUS_INTRABAR` with no fill. No OCO persistence, cancellation, or order mutation in this slice.

## Result contract

Return a frozen result containing stable status/code, exact policy/market/fee/reservation references, cost-model status, and optional candidate `fill_qty`, `fill_price`, and known `Commission`. Use deterministic outcomes including `fill_candidate`, `no_fill_insufficient_data`, `fill_gap_uncertain`, `fill_ambiguous_intrabar`, `fill_limit_not_crossed`, `fill_capacity_missing`, `fill_fee_unknown`, `fill_fee_snapshot_mismatch`, and `fill_reservation_invalid`. Do not construct/persist a `PaperFill` ID in this slice; that is a later integration concern.

## Exhaustive allowlist — create only these two new files

- `backend/src/models/paper_execution.py` — policy/snapshot/result DTOs and pure fill-candidate evaluator.
- `backend/tests/test_paper_execution.py` — synthetic fixture tests only.

Do not edit `sqlite_store.py`, existing DTOs, risk/filter modules, accepted tests, APIs, `StrategyConfig`, docs, task prompts, dependencies, or other paths. If another file is required, STOP and explain why.

## Required test coverage and gate

- BUY/SELL MARKET bid/ask; BUY/SELL LIMIT crossing and non-crossing; no OHLC-only/mark fallback.
- Order status/reservation mismatch, missing/mismatched/incoherent source references, missing bid/ask, missing continuity times and gap result. Validate timestamp ordering/identity; do not invent a maximum-age/freshness cutoff.
- Capacity-bounded partial and full candidates; remaining quantity and quote/BASE reservation limits; no capacity never becomes full fill.
- Exact fee snapshot quantity/QUOTE asset; unknown fee rejected; explicit zero accepted; no rate/default derivation.
- Explicit `UNMODELLED` versus `MODELLED` inputs; missing mode or modelled price/latency rejected; result not labelled net when unmodelled.
- Same-bar stop/target collision is ambiguous and returns no candidate fill; no mutation of input DTOs; stable codes/refs and Decimal-context invariance.
- Numeric test values are fixtures only, not operational recommendations; no network/Binance connection.
- Gate from `backend/`:
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_paper_execution.py`

## Handoff

Capture status/diff; prove only the two new files changed and all pre-existing changes remain intact. Run `git diff --check`; report exact command/exit/counts, limitations and any unresolved technical dependency. No staging, commit, push, SQLite integration, API/UI, matching, external execution or next task. Stop after this pure slice; ACT-P-005 stays globally PENDING until later authorized slices and regression.
