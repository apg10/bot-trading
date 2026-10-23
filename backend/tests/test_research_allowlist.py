"""Contract tests for the documentary research allowlist."""

from src.api.research_allowlist import (
    ALLOWED,
    ALLOWLIST,
    APPROVED_ROUTES,
    BLOCKED,
    BLOCKED_ROUTES,
    DEFERRED,
    DEFERRED_ROUTES,
    DENIED,
    EXCLUDED_SCOPE,
    TEST_ONLY_LABEL,
    TEST_ONLY_SEPARATION_NOTE,
    check_allowlist,
)


def test_approved_routes_return_allowed():
    for route in APPROVED_ROUTES:
        assert check_allowlist(route.method, route.path) == ALLOWED


def test_blocked_routes_return_blocked():
    for route in BLOCKED_ROUTES:
        assert check_allowlist(route.method, route.path) == BLOCKED


def test_deferred_routes_return_deferred():
    for route in DEFERRED_ROUTES:
        assert check_allowlist(route.method, route.path) == DEFERRED


def test_unlisted_routes_return_denied():
    assert check_allowlist("GET", "/api/not-approved") == DENIED
    assert check_allowlist("POST", "/orders") == DENIED
    assert check_allowlist("GET", "/api/market/candles/extra") == DENIED


def test_method_matters():
    assert check_allowlist("POST", "/health") == DENIED
    assert check_allowlist("GET", "/api/analysis/market") == DENIED


def test_no_duplicate_entries():
    keys = [(route.method, route.path) for route in ALLOWLIST]
    assert len(keys) == len(set(keys))


def test_all_approved_routes_have_permission_category():
    assert all(route.permission_category for route in APPROVED_ROUTES)


def test_all_blocked_routes_have_permission_category():
    assert all(route.permission_category for route in BLOCKED_ROUTES)


def test_test_only_labeling_preserved():
    assert TEST_ONLY_LABEL == "TEST_ONLY"
    assert "TEST_ONLY" in TEST_ONLY_SEPARATION_NOTE
    assert "advisory" in TEST_ONLY_SEPARATION_NOTE
    assert "PAPER" in TEST_ONLY_SEPARATION_NOTE
    assert "LIVE" in TEST_ONLY_SEPARATION_NOTE


def test_exclusions_documented():
    assert {"order commands", "shell", "secrets", "strategy activation", "LIVE"} <= EXCLUDED_SCOPE


def test_approved_routes_match_expected_count():
    """Guards against silent route omissions: approved count must stay stable."""
    assert len(APPROVED_ROUTES) == 6


def test_blocked_routes_match_expected_count():
    assert len(BLOCKED_ROUTES) == 13


def test_deferred_routes_match_expected_count():
    assert len(DEFERRED_ROUTES) == 2


def test_catalog_contains_all_three_categories():
    """ALLOWLIST = APPROVED + BLOCKED + DEFERRED exactly."""
    expected = APPROVED_ROUTES + BLOCKED_ROUTES + DEFERRED_ROUTES
    assert ALLOWLIST == expected
    assert len(ALLOWLIST) == 6 + 13 + 2
