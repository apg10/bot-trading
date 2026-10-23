"""Documentary allowlist for the approved research/read API surface.

This module is deliberately static.  It does not import FastAPI, register
routes, persist audit records, define DTOs, or authorize a request by itself.
``TEST_ONLY`` remains a provenance label rather than a permission.  Advisory,
PAPER, and LIVE remain separate concerns: this catalog grants no execution
authority, and LIVE is not an approved environment.

Orders (mutation commands), shell access, secrets, strategy activation, and LIVE routes are
explicitly outside this catalog.  N02 routes are listed only as DEFERRED
because their T1 work is the existing boundary; they are not converted into
an audit record by this module.

Routes requiring unresolved permissions or limits are listed under BLOCKED_ROUTES
and must not be treated as approved until a human gate approves the associated
limits and authorization contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


ALLOWED: Final[str] = "ALLOWED"
DENIED: Final[str] = "DENIED"
DEFERRED: Final[str] = "DEFERRED"
BLOCKED: Final[str] = "BLOCKED"

TEST_ONLY_LABEL: Final[str] = "TEST_ONLY"
TEST_ONLY_SEPARATION_NOTE: Final[str] = (
    "TEST_ONLY is a provenance label only; advisory, PAPER, and LIVE remain "
    "separate, and this catalog does not grant LIVE or execution authority."
)
EXCLUDED_SCOPE: Final[frozenset[str]] = frozenset(
    {"order commands", "shell", "secrets", "strategy activation", "LIVE"}
)


@dataclass(frozen=True, slots=True)
class AllowlistRoute:
    """One documentary route entry in the research catalog."""

    path: str
    method: str
    permission_category: str
    version_ref: str
    existing_limits: str | None
    inference_available: bool | None = None
    decision: str = ALLOWED


# ---------------------------------------------------------------------------
# APPROVED: routes with no unresolved permission or resource-limit blocker
# ---------------------------------------------------------------------------

APPROVED_ROUTES: Final[tuple[AllowlistRoute, ...]] = (
    AllowlistRoute(
        path="/openapi.json",
        method="GET",
        permission_category="discovery",
        version_ref="OpenAPI generated",
        existing_limits=None,
    ),
    AllowlistRoute(
        path="/health",
        method="GET",
        permission_category="status",
        version_ref="Current payload",
        existing_limits=None,
    ),
    AllowlistRoute(
        path="/api/capabilities",
        method="GET",
        permission_category="capabilities",
        version_ref="backend_capabilities.v1",
        existing_limits=None,
    ),
    AllowlistRoute(
        path="/api/market/status",
        method="GET",
        permission_category="market status",
        version_ref="market_transport.v1",
        existing_limits="Symbol validated by existing route",
    ),
    AllowlistRoute(
        path="/api/market/candles",
        method="GET",
        permission_category="market data",
        version_ref="market_transport.v1",
        existing_limits="page_size 1–500, default 100; opaque keyset cursor",
    ),
    AllowlistRoute(
        path="/api/bot/status",
        method="GET",
        permission_category="bot status",
        version_ref="Current payload",
        existing_limits=None,
    ),
)


# ---------------------------------------------------------------------------
# BLOCKED: routes pending unresolved limits or authorization
# ---------------------------------------------------------------------------

BLOCKED_ROUTES: Final[tuple[AllowlistRoute, ...]] = (
    # --- Analysis (CPU/memory processing; input-list and concurrency limits unresolved) ---
    AllowlistRoute(
        path="/api/analysis",
        method="POST",
        permission_category="analysis",
        version_ref="Current payload",
        existing_limits="candles_count 50–500; input candles list NOT capped (proposal needed)",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/analysis/market",
        method="POST",
        permission_category="analysis",
        version_ref="Current payload",
        existing_limits="candles_count 50–500; no rate/concurrency budget",
        decision=BLOCKED,
    ),
    # --- Paper projections (multiple SQLite reads; potentially unbounded results; no auth) ---
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/lifecycle",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; no pagination; unbounded result",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/balances",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; no pagination; unbounded result",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/positions",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; no pagination; unbounded result",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/orders",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; potentially unbounded; no pagination",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/fills",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; potentially unbounded; no pagination",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/paper/accounts/{account_id}/activity",
        method="GET",
        permission_category="paper projection",
        version_ref="paper_* DTOs",
        existing_limits="Multiple store reads; potentially unbounded; no pagination",
        decision=BLOCKED,
    ),
    # --- Laboratory queries (multiple SQLite reads; CPU report calculation; no auth) ---
    AllowlistRoute(
        path="/api/laboratory/results/{result_id}",
        method="GET",
        permission_category="lab query",
        version_ref="normalized_experiment_result.v1",
        existing_limits="SQLite read; untyped dict response; no pagination",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/laboratory/comparisons/{experiment_id}/{experiment_version}",
        method="GET",
        permission_category="lab query",
        version_ref="experiment_comparison.v1",
        existing_limits="SQLite read plus comparison materialization; no pagination",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/laboratory/capital-curves/{account_id}/{session_id}",
        method="GET",
        permission_category="lab query",
        version_ref="capital_curve_report.v1",
        existing_limits="Several SQLite reads plus CPU report calculation",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/laboratory/cost-breakdowns/{account_id}/{session_id}",
        method="GET",
        permission_category="lab query",
        version_ref="cost_breakdown_report.v1",
        existing_limits="Several SQLite reads plus CPU report calculation",
        decision=BLOCKED,
    ),
    AllowlistRoute(
        path="/api/laboratory/inspector/{account_id}/{session_id}",
        method="GET",
        permission_category="lab query",
        version_ref="operation_inspector.v1",
        existing_limits="Several SQLite reads; joins/materializes audit data",
        decision=BLOCKED,
    ),
)


# ---------------------------------------------------------------------------
# DEFERRED: N02 routes (T1 boundary; not converted to audit by this module)
# ---------------------------------------------------------------------------

DEFERRED_ROUTES: Final[tuple[AllowlistRoute, ...]] = (
    AllowlistRoute(
        path="/api/bot/ai/scenarios",
        method="POST",
        permission_category="N02",
        version_ref="T1",
        existing_limits=None,
        inference_available=True,
        decision=DEFERRED,
    ),
    AllowlistRoute(
        path="/api/bot/ai/analyze",
        method="POST",
        permission_category="N02",
        version_ref="T1",
        existing_limits=None,
        inference_available=True,
        decision=DEFERRED,
    ),
)


# The complete documentary catalog: APPROVED + BLOCKED + DEFERRED.
# No entry here registers an endpoint or grants access.
ALLOWLIST: Final[tuple[AllowlistRoute, ...]] = (
    APPROVED_ROUTES + BLOCKED_ROUTES + DEFERRED_ROUTES
)

_DECISIONS: dict[tuple[str, str], str] = {
    (entry.method, entry.path): entry.decision for entry in ALLOWLIST
}


def check_allowlist(method: str, path: str) -> str:
    """Return the static decision for an HTTP method/path pair.

    Literal paths are checked first, then catalog route templates are matched
    one segment at a time.  This is a catalog check, not a router or a
    business-state lookup.  HTTP method casing is normalized while the path
    itself is not decoded or normalized.

    Returns ALLOWED, BLOCKED, DEFERRED, or DENIED.
    """

    if not isinstance(method, str) or not isinstance(path, str):
        return DENIED
    normalized_method = method.upper()
    exact = _DECISIONS.get((normalized_method, path))
    if exact is not None:
        return exact

    # The catalog contains the route templates used by FastAPI.  Match one
    # path segment for each ``{parameter}`` rather than making account/session
    # existence a hidden authorization decision in the downstream handler.
    # This is deliberately a structural match only; the decision still comes
    # exclusively from the static catalog entry.
    # Strip only the leading slash so the first empty segment does not
    # interfere with segment comparison.  Trailing slashes are preserved.
    p_segments = path.lstrip("/").split("/")
    for (catalog_method, catalog_path), decision in _DECISIONS.items():
        if catalog_method != normalized_method:
            continue
        c_segments = catalog_path.lstrip("/").split("/")
        if len(c_segments) != len(p_segments):
            continue
        if all(
            actual
            and (expected == actual or (expected.startswith("{") and expected.endswith("}")))
            for expected, actual in zip(c_segments, p_segments)
        ):
            return decision
    return DENIED


def is_catalogued_path(path: str) -> bool:
    """Return whether *path* is an exact or templated catalog path.

    This predicate intentionally ignores the HTTP method.  Method
    authorization remains the responsibility of :func:`check_allowlist`, so
    a catalogued path with an unlisted method is rejected rather than passed
    to the application.
    """
    if not isinstance(path, str):
        return False
    # Strip only the leading slash so the first empty segment does not
    # interfere with segment comparison.  Trailing slashes are preserved
    # because ``/health/`` must NOT match ``/health``.
    p_segments = path.lstrip("/").split("/")
    return any(
        len(p_segments) == len(pattern.lstrip("/").split("/"))
        and all(
            actual
            and (expected == actual or (expected.startswith("{") and expected.endswith("}")))
            for expected, actual in zip(pattern.lstrip("/").split("/"), p_segments)
        )
        for pattern in (entry.path for entry in ALLOWLIST)
    )


__all__ = [
    "ALLOWED",
    "ALLOWLIST",
    "APPROVED_ROUTES",
    "BLOCKED",
    "BLOCKED_ROUTES",
    "DEFERRED",
    "DEFERRED_ROUTES",
    "DENIED",
    "EXCLUDED_SCOPE",
    "AllowlistRoute",
    "TEST_ONLY_LABEL",
    "TEST_ONLY_SEPARATION_NOTE",
    "check_allowlist",
    "is_catalogued_path",
]
