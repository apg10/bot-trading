# ACT-P-005-FILL-SIMULATOR-PURE-REWORK-005 — residual snapshot binding

Status: TECHNICALLY_ACCEPTED (all review-5 fixes). 92-pass gate confirmed. ACT-P-005 globally remains PENDING for later integration.

## Baseline and scope

- Workspace `/home/adrian10/bot_trading`; branch `master`; HEAD/origin `2cbd689117469d3fa7a1093c066b245336dc8653`.
- Current files:
  - `backend/src/models/paper_execution.py` — SHA-256 `c3daf6ab4d4af6627d20f4558c70d37203065d22d4e79eefbcb25323ef174a0d`
  - `backend/tests/test_paper_execution.py` — SHA-256 `f9416c16ec5b6503ac29d5d9a7df3b51316075f50dfcc92c8b5349af5dfa5295`
- Re-capture status/HEAD/hashes before edits; preserve all other local changes. Only these two paths may change; no stage/commit/push/reset/clean/stash.

## Gate

From `backend/`, the focal gate returns **92 passed** (85 original + 7 new). TECHNICALLY_ACCEPTED by cloud-agent.

## Corrections Completed

### Correction 1: Bind capacity to exact market
- Added `capture_bar_open_time_ms` and `capture_snapshot_timestamp_ms` to `PaperMarketCapacitySnapshot`.
- Added Guard 2bbb checking capture fields match between capacity and market snapshots.
- Added `capacity_capture_bar_open_time_ms` and `capacity_capture_snapshot_timestamp_ms` to `PaperFillCandidateResult`.
- Added `_cap_kwargs()` helper and applied to all 16 capacity-bearing `_build_result` calls.
- Capacity symbol binding (Guard 2bc) placed before price evaluation.
- Tests: `test_capacity_capture_bar_mismatch_rejects_fill`, `test_capacity_capture_timestamp_mismatch_rejects_fill`, `test_capacity_matching_bar_and_timestamp_allows_fill`.

### Correction 2: Require reservation account identity
- `PaperReservationSnapshot.account_id` already requires `min_length=1` (non-blank).
- Evaluator checks account_id match to `order.paper_account_id`.
- Tests: `test_reservation_missing_account_id_rejected`, `test_reservation_account_id_empty_rejected`, `test_reservation_account_id_none_rejected`.

### Correction 3: Bind protection OHLC to exact bar
- Protection snapshot includes bar identity fields (source, timestamp, open time, interval).
- Evaluator checks bar identity against `PaperMarketExecutionSnapshot`.

### Correction 4: Replace misleading missing-quote test
- Renamed `test_no_fill_without_market_bid_ask` → `test_no_fill_when_continuity_genuinely_missing`.
- Added `test_bid_gt_ask_incomplete_quote_rejects_fill` for genuine malformed quote.

## Files Modified
- `/home/adrian10/bot_trading/backend/src/models/paper_execution.py`
- `/home/adrian10/bot_trading/backend/tests/test_paper_execution.py`

## Stop

All review-5 corrections completed and TECHNICALLY_ACCEPTED. ACT-P-005 globally remains PENDING until integration and human acceptance.
