# ACT-P-005-FILL-SIMULATOR-PURE-REWORK-003 — residual corrections

Status: HANDOFF REQUIRES CHANGES. The 71-pass gate was reproduced, but the pure evaluator is not accepted and SQLite integration remains blocked.

## Baseline and scope

- Workspace `/home/adrian10/bot_trading`; branch `master`; HEAD/origin `2cbd689117469d3fa7a1093c066b245336dc8653`.
- Current two files:
  - `backend/src/models/paper_execution.py` — SHA-256 `4ff62b5e34aa1c562f73e3c0fd19b1a1cfb4d8111f05269fbc6b715547c192c5`
  - `backend/tests/test_paper_execution.py` — SHA-256 `4858b1020d327cb269eea1c6cd6ecbfd97742c51a45937336fca2813d7ebe5c5`
- Re-capture HEAD/status/hashes before editing. Preserve all other dirty/untracked files. Only these two paths are authorized to change; no stage/commit/push/reset/clean/stash.

## Gate evidence

The focal command from `backend/` was reproduced: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_paper_execution.py` → **71 passed**. Passing tests do not cover the reproduced unsafe paths below.

## Reproduced residual failures

- MODELLED BUY LIMIT: ask `40000`, limit `40002`, modelled execution price `40001`, quote reserve `40000` → `fill_candidate`; reserve is checked at ask, not the actual modelled fill price.
- MODELLED SELL LIMIT: bid `40000`, limit `39999`, modelled execution price `39998` → `fill_candidate` below its limit.
- A `PaperReservationSnapshot` with `owner_order_id=None` and `owner_group_id=None` is accepted as a fill reservation; the DTO has no `account_id`, so account mismatch cannot be checked. The code rejects a different non-null owner and rejects a market-symbol mismatch, but it does not require owner identity.
- Executable `capacity` is a required field on `PaperReservationSnapshot`, not tied to a market-capacity source/reference. It remains possible to supply reservation-shaped data as liquidity without a market capacity ref.
- A protection snapshot whose group is `pg-1` can still trigger ambiguity for an order with no protection group; the equality check only runs when both group ids are non-null. The protection snapshot has no bar/snapshot identity, so the evaluator cannot verify it is the same OHLC bar.

## Required rework in the same two files

1. Carry executable capacity as an explicit market/capacity input with its own source/ref; keep it separate from `PaperReservationSnapshot`. Continue bounding by order remainder, capacity, and reservation remaining quantity/amount.
2. Require reservation identity and account context. Match exactly one valid owner (order id or a supported matching protection group) to the candidate; missing owner or account mismatch fails closed. Validate BASE/QUOTE asset.
3. After selecting the actual fill price, recompute exact notional for every BUY order. Enforce quote reserve for LIMIT and MARKET plus `max_quote_amount` for MARKET. For MODELLED SELL LIMIT, enforce `fill_price >= limit_price`; for BUY LIMIT, `fill_price <= limit_price`. Apply reservation/max-quote bounds to the modelled price, not the raw quote.
4. A protection collision requires matching, non-empty protection group/order identity and a protection snapshot tied to the same market bar/snapshot. Missing or unrelated protection context must not mark an unrelated candidate ambiguous.
5. Add regression tests for all cases above. Keep the corrected symbolic-market check, Commission-in-QUOTE check, trigger guard, and exact Decimal helper. Explicitly test a missing owner, account mismatch, capacity without a market ref, modelled price vs. both BUY/SELL LIMITs and reservation, and missing/mismatched protection group/bar identity.

No scope expansion: pure evaluator and tests only; no matching/OCO persistence, fills/SQLite, services, fee/slippage defaults, or later phase. Run the same focal gate after the correction and stop for review. ACT-P-005 remains globally PENDING until this handoff is accepted.
