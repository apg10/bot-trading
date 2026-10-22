# ACT-P-005-FILL-SIMULATOR-PURE-REWORK-002 — residual corrections

Status: HANDOFF REQUIRES CHANGES. The reported 60-pass gate was reproduced, but ACT-P-005-FILL-SIMULATOR-PURE-001 is still not accepted. Do not start SQLite integration.

## Baseline

- Workspace `/home/adrian10/bot_trading`; branch `master`; HEAD and origin/master `2cbd689117469d3fa7a1093c066b245336dc8653`.
- Only authorized existing untracked paths:
  - `backend/src/models/paper_execution.py` — SHA-256 `b94c284c7747b366ec44b6fa196a6a1097b83cb43f7fd7d50655f35fcd671034`
  - `backend/tests/test_paper_execution.py` — SHA-256 `d377123963b63a091931ed9762ba5318cd7c3b771c21389978e834a35c1bef64`
- Re-capture HEAD/status/hashes before any edit; preserve every other dirty/untracked path. If hashes differ, STOP and re-review ownership. No stage/commit/push/reset/clean/stash.

## Gate evidence

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_paper_execution.py` → **60 passed**. The passing gate does not cover the following failures.

## Reproduced semantic failures

1. Reservation qty is still ignored when positive: order remaining 1, reservation `remaining_qty=0.25`, `capacity=1` returns `fill_candidate`.
2. LIMIT BUY notional is not checked against quote reserve: qty 1 × limit 40001, reserve amount 1 returns `fill_candidate`.
3. MARKET BUY ignores the order's `max_quote_amount`: max quote 1 with reserve 40001 returns `fill_candidate`.
4. Reservation `owner_order_id` is not matched to the candidate order; a different owner returns `fill_candidate`. The snapshot has no account id to match.
5. Market snapshot symbol is not compared with order symbol; a BTC/USDT order evaluated with ETH/USDT bid/ask returns `fill_candidate`.
6. MODELLED BUY LIMIT with limit 40001 and explicit modelled execution price 40002 returns `fill_candidate` at 40002.
7. STOP_LOSS can return `fill_candidate` without its trigger being reached. STOP_LOSS/TAKE_PROFIT are not currently guarded by trigger logic.
8. Protection collision checks symbol/account but not matching `protection_group_id` against the order/group; unrelated protection data can cause an ambiguous result.

## Required rework in the same two files

- Keep executable market capacity distinct from reservation qty/amount and give that capacity an explicit market-source reference. Candidate qty must be bounded by order remaining qty, actual executable capacity, reservation qty, and the appropriate BASE/QUOTE amount.
- Validate reservation owner order/group, account, symbol and asset; do not accept cross-order or cross-account reservations.
- For BUY LIMIT, require quote reserve to cover `fill_qty × fill_price`; for BUY MARKET also enforce `max_quote_amount`. Apply modelled price before these checks; it must be positive and respect LIMIT/max-quote constraints.
- Reject a market snapshot whose symbol differs from the order; retain a stable snapshot/source id/version/timestamp in the result.
- Restrict fill candidates to defined LIMIT/MARKET rules. STOP/TAKE must not fill absent an explicit reached-trigger condition. Ambiguity applies only to the matching protection group/order pair.
- Add tests for every reproduced case above, plus genuine missing bid/ask/reference and reservation/market identity mismatches. Preserve the exact Decimal-context edge test; no ambient rounding.

No scope expansion: still pure evaluator + tests only; no matching/OCO persistence, fills/SQLite, services, numeric fee/slippage defaults, or later phase. Re-run the same focal gate after corrections and stop for review. ACT-P-005 remains globally PENDING until this handoff is accepted.
