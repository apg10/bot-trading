# ACT-P-005-FILL-SIMULATOR-PURE-REWORK-004 — remaining execution-contract gaps

Status: HANDOFF REQUIRES CHANGES. The 71-pass gate was reproduced, but the pure slice is still not accepted. No SQLite integration.

## Baseline and scope

- Workspace `/home/adrian10/bot_trading`; branch `master`; HEAD/origin `2cbd689117469d3fa7a1093c066b245336dc8653`.
- Current two files:
  - `backend/src/models/paper_execution.py` — SHA-256 `4ff62b5e34aa1c562f73e3c0fd19b1a1cfb4d8111f05269fbc6b715547c192c5`
  - `backend/tests/test_paper_execution.py` — SHA-256 `4858b1020d327cb269eea1c6cd6ecbfd97742c51a45937336fca2813d7ebe5c5`
- Re-capture hashes/status before editing. Preserve all other local worktree changes. Only these paths may change; no staging/commit/push/reset/clean/stash.

## Gate

Focused gate is **71 passed**, but direct review/reproduction still finds a fail-open reservation association:

- A BUY candidate with `reservation.owner_order_id=None`, `owner_group_id="foreign-group"`, and `account_id="other-account"` returns `fill_candidate`. The evaluator only checks that some owner is present; it never matches a group owner to the order's group, and never checks reservation account (the account field is optional).

## Remaining corrections

1. **Reservation identity:** require account id and exactly one owner. Match owner_order_id to candidate order, or owner_group_id to the same non-empty order protection group; reject absent/foreign owner, account mismatch, both owner fields set, or neither set.
2. **Capacity provenance:** `PaperMarketCapacitySnapshot` has source/version/capacity but no symbol or bar/snapshot identity, and `PaperFillCandidateResult` does not return the capacity reference. Bind capacity to the exact symbol/execution snapshot/time and preserve that ref in every relevant result.
3. **MODELLED budget consistency:** `_compute_capacity` checks quote reserve using `market.ask` before the modelled price is selected. A valid modelled BUY execution at a better price can be rejected because the pre-check uses the higher ask. Select the actual price first, then use that same price for capacity, quote reserve, LIMIT, and max_quote checks; preserve fail-closed over-budget behavior.
4. **Protection bar identity:** `PaperProtectionSnapshot` contains OHLC values but no source/snapshot/bar timestamp. It cannot prove that the OHLC collision is from the same bar as the market execution snapshot. Add and compare exact source/snapshot/bar refs.
5. **Collision precedence:** protection ambiguity is checked after the single STOP/TAKE trigger check. If one stop/target pair was both touched within the bar but the current quote is between them, the evaluator can return `fill_protection_trigger_missing` before detecting the approved `FILL_AMBIGUOUS_INTRABAR`. Detect a linked same-bar collision before single-trigger outcomes; return ambiguity with no fill.
6. **Snapshot coherence:** validate quote ordering (`bid <= ask`) and timestamp/bar ordering. Keep no maximum-age cutoff unless separately approved. Return stable fail-closed results for incoherent snapshots.
7. **Genuine missing quote coverage:** `test_no_fill_without_market_bid_ask` still constructs valid bid/ask values and expects a fill. Replace/add a real missing or invalid quote case that proves no fill without a reference.

Keep corrected behavior already present: exact Decimal helper, explicit MODELLED price/latency, fee in QUOTE, symbol match, trigger crossing, and LIMIT/max_quote price checks. Add regression tests for each remaining issue without weakening existing cases. No numeric defaults or new financial policy.

Run the same focused gate from `backend/`, report exact status/changed paths and test count, then stop. ACT-P-005 remains globally PENDING until the pure slice is accepted.
