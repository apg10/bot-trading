# ACT-P-005-FILL-SIMULATOR-PURE-REWORK-001 — required corrections

Status: HANDOFF REQUIRES CHANGES. The reported 47-pass gate is reproducible, but the pure slice is not accepted; do not start integration.

## Baseline and scope

- Workspace: `/home/adrian10/bot_trading`; branch `master`; expected HEAD and `origin/master`: `2cbd689117469d3fa7a1093c066b245336dc8653`.
- Preserve all dirty/untracked worktree state. Only these two existing untracked files are authorized to change:
  - `backend/src/models/paper_execution.py` — current SHA-256 `e697ce73d34b30eb2198dc07a70206b379b1a6e2dfcf6c630b0ebb2daa1b60dd`
  - `backend/tests/test_paper_execution.py` — current SHA-256 `46a6c69961fcd98c6c2c74978b218309676b60f8c37c19c29ce0428e35eb09b6`
- If either hash differs at execution start, STOP and re-capture/review ownership. No reset/clean/stash, staging, commit, push, or new path.
- Follow the approved contract in plan §14.29 and the task `ACT-P-005-fill-simulator-pure-task.md`. Do not start SQLite/API integration.

## Findings that block acceptance

1. `PaperMarketExecutionSnapshot` lacks source id/version, snapshot id, and capture timestamp; the result's `market_reference` is only the symbol. Return exact version/provenance references.
2. There is no market `executable_capacity_qty`. `_compute_capacity` treats `reservation.remaining_qty` as execution liquidity, so a full reservation alone creates a full fill. Keep reservation capacity and executable market capacity separate; missing/zero executable capacity must produce no fill.
3. Reservation ownership/assets are not validated. Require the active reservation to match `order_id` and account; BUY must use the symbol QUOTE asset and cover exact quote notional (including MARKET `max_quote_amount`); SELL must use BASE and cover the candidate quantity. Mismatches fail closed.
4. `commission.asset` is never checked against the symbol QUOTE asset. Add a wrong-asset rejection test; known zero remains valid only when explicitly supplied.
5. `MODELLED` currently executes `pass`: it accepts without a `PaperModelledExecution`, ignores `modelled_execution_price`/`latency_ms`, then fills at bid/ask. Either consume and validate the explicit modelled inputs and use the supplied execution price, or fail closed; never label an unmodelled quote fill as MODELLED.
6. STOP_LOSS/TAKE_PROFIT currently pass the LIMIT checks and can return a bid/ask fill without a trigger crossing. Do not emit a fill candidate for unsupported trigger execution. For collision detection, require the explicit protective pair/group to match; unrelated orders must not become ambiguous.
7. `_compute_capacity` multiplies `executable_qty * market.ask` under the ambient Decimal context. Reproducer: qty `1.23`, ask `123.45`, quote reserve `151.5`; with global `prec=3, ROUND_DOWN` it returns `fill_candidate`, though exact notional is `151.8435`. Use exact/local-context arithmetic and add this boundary regression.
8. The test named `test_no_fill_without_market_bid_ask` supplies valid bid/ask and expects a fill. Add real missing/incoherent snapshot and missing quote cases that fail closed with stable results; do not rely only on Pydantic construction errors.

## Required focused coverage before handoff

- Exact market capacity distinct from reservation; no capacity cannot become full fill.
- BUY quote budget/reservation, SELL BASE reservation, owner/account/symbol/asset mismatches.
- Fee QUOTE validation and fee quantity match.
- Missing source/version/time and missing bid/ask result codes.
- MODELLED mode requires and uses the explicit modelled price/latency; otherwise no fill.
- LIMIT/MARKET remain the only fill-candidate order types unless trigger rules are separately defined; matched protection-group collision returns ambiguous/no fill.
- Decimal-context edge above plus global-context invariance.
- Inputs remain immutable; stable codes/references.

Run only the existing focused gate from `backend/` after edits:
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_paper_execution.py`

Report the full changed-file/status set and gate. Keep ACT-P-005 globally PENDING. Stop after this pure-slice correction; no Qwen invocation from this planner, no code commit/push, and no integration until this handoff is reviewed and accepted.
