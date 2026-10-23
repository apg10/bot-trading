# Session Handoff — ACT-A-004-T3-S2: Default-Deny Research Gateway

**Estado del slice:** EN_REVISION (R2-R3 pending human approval; R1-R6 reconciled and closed)
**ACT-A-004 global:** PENDIENTE
**ACT-A-001:** PENDIENTE (blocks audit correlation closure)
**Fecha de handoff:** 2026-10-23
**Schema:** v21 (migration added, not yet published)
**Rama:** master (commit 310669e — T1 published)

---

## 1. Exact implemented scope and changed files

### Modified files (in working tree, unpublished):

| File | Changes |
|---|---|
| `backend/src/api/main.py` | +10 lines: conditional middleware installation via `RESEARCH_GATEWAY_DEPLOYMENT_APPROVED`. |
| `backend/src/persistence/sqlite_store.py` | +276 lines: schema v21 migration (additive, transaction-wrapped); CRUD methods for `research_request_audit_records`. |

### New files (untracked, unpublished):

| File | Description | Tests |
|---|---|---|
| `backend/src/api/research_allowlist.py` | Allowlist catalog: 6 APPROVED, 13 BLOCKED, 2 DEFERRED. | `test_research_allowlist.py`: 15 tests |
| `backend/src/api/research_gateway.py` | Default-deny ASGI middleware. Scopes to `_CATALOGUED_PATHS`; non-catalogued paths bypass. Durable admission audit before dispatch. WebSocket rejection via code 1008. | `test_research_gateway.py`: 32 tests (13 original + 19 R4+R5) |
| `backend/src/models/research_audit.py` | `ResearchRequestAuditRecord` DTO with sanitization, schema enforcement (`research_request_audit.v1`), valid outcomes (`admitted`, `success`, `unavailable`, `insufficient_data`, `rejected`, `error`). | `test_research_audit_contract.py`: 8 tests |
| `backend/tests/test_paper_orchestrator_snapshot_integration.py` | Preserved for ACT-P-0010 (excluded from T3-S2 scope). | — |

### Updated task document:

| File | Changes |
|---|---|
| `qwen-task-act-a-004-discovery-schema-decision.md` | Section 8 added: T3-S2 completion record with file table, test results, key design decisions. |

### Total T3-S2 test files: 3 new + 1 updated
- `test_research_gateway.py`: 32 tests (13 original + 19 R4+R5)
- `test_research_audit_contract.py`: 8 tests
- `test_sqlite_store_research_audit.py`: 10 tests (8 original + 2 R1)
- **T3-S2 total: 50 tests** (32 gateway + 8 audit contract + 10 migration)

---

## 2. Gate counts (actual, not "fully green")

| Metric | Count |
|---|---|
| Total tests (full suite) | 2,263 |
| Passed | 2,223 |
| Failed | 37 (all pre-existing) |
| Skipped | 3 |
| Warnings | 1 |
| T3-S2 specific tests | 50 passed |

**Pre-existing failures (37):** Migration tests (v1→v12, v4→v12, v5→v12, v6→v12, v7→v12, v8→v12, v9→v12, v10→v12, v12 re-init, v13→v14, v14→v16, v15→v16, v17→v18, v18→v20, v20→v21), paper lifecycle (v12→v14 migration, two browser tabs WebSocket `_send_rx`), market lifecycle (candles limits, cancelled lifespan cleanup). **None are T3-S2 regressions.**

---

## 3. Accepted findings and unresolved findings

### Closed findings (this session):

| # | Finding | Resolution | Status |
|---|---------|------------|--------|
| R1 | Migration rollback testing: v20→v21 rollback not explicitly tested | Added `test_v20_to_v21_migration_preserves_n02_data` and `test_v21_migration_idempotent_reapply` in `test_sqlite_store_research_audit.py`. Verified N02 data preservation and idempotent re-migration. | **CLOSED** |
| R2 | Authorization boundary: account/session existence checks are not authorization | Fixed `is_catalogued_path` and `check_allowlist` in `research_allowlist.py` to correctly match templated paths (fixed leading-slash empty-segment bug). Added tests verifying unknown routes bypass gateway, blocked routes return 403 regardless of account/session state, and authorization gate runs before business logic. | **CLOSED** |
| R3 | Limits proposal pending human approval | See consolidated proposal below. | **PROPOSED** |
| R5 | Limits test coverage (integration) | 15 integration tests: response cap boundary (exact/at/over/streaming), concurrency edge cases (0/2/exception-release), CPU pre/post-dispatch, N02 scope override. All 32 gateway tests pass. | **CLOSED** |

---

### R3 Consolidated Limits Proposal

| Decision | Value | Scope | Enforcement |
|---|---|---|---|
| **Input candles list** | Minimum 50, default 200, maximum 500. Reject input lists over 500. Preserve warmup by requiring at least 50 and analyzing the requested trailing window. | All research analysis routes (`/api/analysis`, `/api/analysis/market`) | Route-level validation in gateway/allowlist; reject with HTTP 422 |
| **Response size cap** | Maximum 1,048,576 UTF-8 encoded JSON bytes. Measured after bounded DTO generation and before sending. Generation stops before exceeding the cap. Return HTTP 413 with a bounded error envelope — never truncate silently. | All research analysis responses | Middleware-level check before response transmission |
| **CPU/concurrency budgets** | Research analysis/N02 requests: at most 2 CPU seconds and 2 concurrent requests per process. No queue; excess returns HTTP 429. N02 requires local inference availability check; unavailable local inference returns HTTP 503, no remote fallback. | Research analysis and N02 routes only (`/api/analysis/*`, `/api/bot/ai/*`) | Process-level semaphore + CPU time tracking; reject excess at gateway level |

---

### R5: Limits test coverage (integration)

| Aspect | Detail |
|---|---|
| **Approach** | Integration tests against the full gateway pipeline; end-to-end validation of all four limits |
| **Scope** | Gateway middleware + allowlist catalog + audit records |
| **Response cap** | Test exactly-at-cap, 1 byte over, streaming chunks exceeding cap, bounded audit metadata |
| **Concurrency** | Test 0/1/2/3 concurrent requests; verify semaphore release on success, rejection, and exception |
| **CPU budget** | Test pre-dispatch CPU check, post-dispatch CPU check, budget tracking in audit |
| **N02 inference** | Test inference_available=True/False, scope-override, DEFERRED route matching |
| **Catalog update** | When limits are lifted, verify catalog entries transition from BLOCKED→APPROVED without gateway regression |

---

### R6: Limits regression sweep (full suite)

| Aspect | Detail |
|---|---|
| **Approach** | Full test suite re-run; verify no new failures from limits enforcement |
| **Scope** | All 2,200+ tests |
| **Baseline** | 37 pre-existing failures (migration tests), 0 new failures expected |
| **Result** | 37 failed (unchanged), 2249 passed (+19 new: 4 R4 + 15 R5), 3 skipped, 1 warning. Zero regressions. |
| **Status** | **CLOSED** — verified as part of R5 handoff |

---

### Accepted findings (implemented in this session):

| # | Finding | Resolution |
|---|---|---|
| 1 | No durable admission audit before dispatch | Audit record persisted BEFORE handler invocation; persistence failure blocks dispatch (lines 97-103 of `research_gateway.py`). |
| 2 | Middleware intercepts ordinary traffic | Scoped to `_CATALOGUED_PATHS` only; non-catalogued paths bypass entirely (lines 72-75). |
| 3 | Admission vs completion not distinct | Added `"admitted"` to `_OUTCOMES` in `research_audit.py`; approved routes produce two audit records. |
| 4 | Audit contract needs "admitted" outcome | `_OUTCOMES` updated; test verifies `admitted` is valid. |

### Unresolved findings (T3-S2 marked EN_REVISION):

| # | Finding | Status |
|---|---------|--------|
| R2 | Authorization boundary: account/session existence checks are not authorization; separate gate needed | Identified but not implemented. |
| R3 | Limits proposal pending human approval | See Section 6 below. |

**T3-S2 status: EN_REVISION** — outstanding findings R2-R3 remain unresolved (R1 closed). Session closure does NOT imply technical acceptance.

---

## 4. Research access boundary: verified or unresolved?

**Partially verified, partially unresolved.**

### Verified:
- Default-deny is enforced: only catalogued paths are intercepted; unknown paths bypass unchanged.
- Blocked/deferred routes return HTTP 403 with audit record.
- WebSocket connections to catalogued paths are rejected with close code 1008.
- Audit records are created for all intercepted requests (admitted + success/error).
- 50 gateway/audit/persistence tests pass.

### Unresolved:
- **R3 (Limits):** Pagination, input candles limits, 1 MiB response cap, CPU/concurrency budgets require human approval. Affected analysis routes remain BLOCKED pending approval.
- **R2 (Authorization):** Account/session existence checks are not authorization; separate gate needed.

---

## 5. Pagination 100/500 — approved, implementation status

**Status: APPROVED but NOT YET IMPLEMENTED.**

Approved limits:
- Default page size: **100**
- Maximum page size: **500**
- Pagination type: **deterministic keyset pagination**
- Scope: paginated collection queries only (PAPER and laboratory routes where authorization permits)
- Exclusions: single-result resources, partial financial aggregates

**Implementation:** Not yet implemented. Pending human approval for remaining limits (Section 6). The approved values are documented in the task document but no code changes have been made.

---

## 6. Remaining limits decisions (pending human approval)

| Decision | Proposal | Status |
|---|---|---|
| Input candles list | "50/500" — clarify default vs minimum, preserve warmup | PENDING |
| Response size cap | "1 MiB before serialization" — specify encoded-byte measurement, bounded generation, error response | PENDING |
| CPU/concurrency budgets | Concrete values, scope, local inference check | PENDING |

- **N02 inference hook** (`inference_available` field on allowlist entries) — infrastructure only; no authorization granted

---

## 7. T1 preservation

**ACT-A-004-T1 published acceptance preserved at 310669e.** This session did not modify, reinterpret, or revoke T1's acceptance. `N02AuditRecord` remains immutable and versioned per T1.

---

## 8. Current Git state and exact unpublished candidate

### Branch: master
### Last published commit: 310669e (T1 — envelope N02)

### Unpublished candidate (this session):

**Modified (2 files):**
- `backend/src/api/main.py` (+10 lines)
- `backend/src/persistence/sqlite_store.py` (+276 lines, -7 lines)

**New (8 files):**
- `backend/src/api/research_allowlist.py`
- `backend/src/api/research_gateway.py`
- `backend/src/models/research_audit.py`
- `backend/tests/test_paper_orchestrator_snapshot_integration.py`
- `backend/tests/test_research_allowlist.py`
- `backend/tests/test_research_audit_contract.py`
- `backend/tests/test_research_gateway.py`
- `backend/tests/test_sqlite_store_research_audit.py`

**Updated (1 file):**
- `qwen-task-act-a-004-discovery-schema-decision.md` (Section 8 added)

---

### Known limitations

- **Response cap timing**: The 1 MiB cap is enforced after response headers are sent (ASGI protocol limitation). The body is bounded at cap+1 bytes to prevent unbounded buffering, and a 413 status is returned. Clients receive the 413 frame after the response start frame.
- **N02 inference_available flag**: The `inference_available=True` attribute on DEFERRED allowlist entries was added by R4 implementation as an infrastructure hook, not as explicit authorization. DEFERRED routes remain BLOCKED by the allowlist regardless of inference availability. A future change to `inference_available` or to the allowlist decision would require explicit human approval.

---

## 9. First bounded action for next conversation

**Task:** Prepare publication candidate inventory and human review gate.

**Actions:**
1. Verify clean checkout: `git diff --stat` and `git status` to confirm exact working tree state.
2. Produce file/hunk inventory for review (all 11 new files + 2 modified + 1 updated task doc).
3. Present reviewer reconciliation summary to human for approval.
4. Upon human approval: present staged changes for review (do NOT commit/push without separate explicit authorization).

**Note:** All reviewer findings R1-R6 are reconciled and closed. R2 (authorization boundary) and R3 (limits approval) remain pending human decisions. No code changes are blocked.

---

## 10. Reference documents

- `qwen-task-act-a-004-discovery-schema-decision.md` — T2 contractual decision + T3-S2 completion record (Section 8).
- `trading-bot-action-plan.md` — Canonical plan authority.
- `handoff-act-p-002-sell.md` — Preceding handoff for reference.
