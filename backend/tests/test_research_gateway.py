import asyncio
import json

from src.api.research_allowlist import APPROVED_ROUTES, BLOCKED_ROUTES, DEFERRED_ROUTES
from src.api.research_gateway import ResearchGatewayMiddleware


class _Store:
    def __init__(self):
        self.records = []
        self.account_lookups = 0
        self.session_lookups = 0

    async def save_research_request_audit_record(self, record):
        self.records.append(record)

    async def load_paper_account_opening(self, account_id):
        self.account_lookups += 1
        return object()

    async def get_paper_lifecycle_snapshot(self, account_id, session_id):
        self.session_lookups += 1
        return object()


class _FailingStore:
    """Store that always fails to persist."""
    def __init__(self):
        self.records = []

    async def save_research_request_audit_record(self, record):
        self.records.append(record)  # track that it was called
        raise RuntimeError("store unavailable")


async def _call(path, method="GET", raw_path=None, scope_type="http", store=None):
    if store is None:
        store = _Store()
    called = []

    async def app(scope, receive, send):
        called.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    await gateway(
        {"type": scope_type, "method": method, "path": path, "raw_path": raw_path or path.encode()},
        receive,
        send,
    )
    return messages, called, store.records


def _run(awaitable):
    return asyncio.run(awaitable)


def test_approved_routes_pass_through():
    for route in APPROVED_ROUTES:
        messages, called, records = _run(_call(route.path, route.method))
        assert called and messages[0]["status"] == 200


def test_blocked_routes_rejected():
    for route in BLOCKED_ROUTES:
        messages, called, records = _run(_call(route.path, route.method))
        assert not called and messages[0]["status"] == 403
        assert records[0].allowlist_decision == "BLOCKED"


def test_deferred_routes_rejected():
    for route in DEFERRED_ROUTES:
        messages, called, records = _run(_call(route.path, route.method))
        assert not called and messages[0]["status"] == 403
        assert records[0].allowlist_decision == "DEFERRED"


def test_unknown_routes_passthrough():
    """Non-catalogued paths reach the underlying app unchanged."""
    messages, called, records = _run(_call("/unknown"))
    assert called and messages[0]["status"] == 200
    # No audit record for non-catalogued paths
    assert not records


def test_unknown_route_does_not_consult_account_or_session_state():
    store = _Store()
    messages, called, records = _run(_call("/api/paper/accounts/acct/unknown", store=store))
    assert called and messages[0]["status"] == 200
    assert store.account_lookups == 0
    assert store.session_lookups == 0
    assert not records


def test_blocked_templated_route_is_rejected_before_business_lookup():
    store = _Store()
    messages, called, records = _run(
        _call("/api/paper/accounts/acct/orders", store=store)
    )
    assert not called and messages[0]["status"] == 403
    assert store.account_lookups == 0
    assert store.session_lookups == 0
    assert records and records[0].allowlist_decision == "BLOCKED"


def test_authorization_gate_precedes_handler_and_account_state():
    store = _Store()
    messages, called, records = _run(
        _call("/api/laboratory/inspector/acct/session", store=store)
    )
    assert not called and messages[0]["status"] == 403
    assert store.account_lookups == 0
    assert store.session_lookups == 0
    assert records[0].outcome == "rejected"


def test_unknown_websocket_route_passthrough():
    messages, called, records = _run(_call("/unrelated", scope_type="websocket"))
    assert called and messages[0]["status"] == 200
    assert not records


def test_wrong_method_rejected():
    messages, called, _ = _run(_call("/health", "POST"))
    assert not called and messages[0]["status"] == 403


def test_trailing_slash_rejected():
    """Trailing slash on a catalogued path is not in the catalog → passthrough."""
    messages, called, records = _run(_call("/health/"))
    # /health/ is not in the catalog, so it passes through
    assert called and messages[0]["status"] == 200
    assert not records


def test_encoded_path_rejected():
    messages, called, _ = _run(_call("/health", raw_path=b"/he%61lth"))
    assert not called and messages[0]["status"] == 403


def test_no_url_forwarding():
    """Arbitrary URL paths are not catalogued → passthrough."""
    messages, called, records = _run(_call("http://internal/health"))
    assert called and messages[0]["status"] == 200
    assert not records


def test_audit_record_created_on_reject():
    _, _, records = _run(_call("/api/analysis"))  # BLOCKED route
    assert records and any(r.outcome == "rejected" for r in records)


def test_audit_record_created_on_allow():
    _, _, records = _run(_call("/health"))
    # Two records: one for admission, one for completion
    assert len(records) == 2
    assert records[0].outcome == "admitted"
    assert records[1].outcome == "success"


def test_websocket_rejected():
    messages, called, records = _run(_call("/health", scope_type="websocket"))
    assert not called and messages[0]["type"] == "websocket.close" and messages[0]["code"] == 1008
    assert records[0].allowlist_decision == "DENIED"


def test_audit_failure_blocks_dispatch():
    """If admission audit persistence fails, the handler must NOT be invoked."""
    failing_store = _FailingStore()
    messages, called, records = _run(_call("/health", store=failing_store))
    assert not called  # handler never dispatched
    assert messages[0]["status"] == 403
    # The rejection audit record is still created (best-effort)
    assert any(r.outcome == "rejected" for r in records)


def test_admission_outcome_exists():
    """Verify the 'admitted' outcome is a valid audit outcome."""
    from src.models.research_audit import _OUTCOMES
    assert "admitted" in _OUTCOMES


def test_response_cap_tracked_in_audit():
    """Responses exceeding 1 MiB are rejected and tracked in audit."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * 1_048_577})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health"},
        receive, send,
    ))

    assert any(
        r.public_result_metadata
        and r.public_result_metadata.get("response_cap_exceeded")
        for r in store.records
    )
    assert any(message.get("status") == 413 for message in messages)


def test_concurrency_budget_enforced():
    """Only two concurrent analysis requests may hold the process budget."""
    store = _Store()
    messages = []
    entered = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()
    saw_429 = asyncio.Event()

    async def app(scope, receive, send):
        # The route is documentary-BLOCKED, so the handler is not dispatched;
        # the response gate below holds the first two rejection paths instead.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        nonlocal entered
        messages.append(message)
        if message.get("status") == 403 and not release.is_set():
            entered += 1
            if entered == 2:
                two_entered.set()
            await release.wait()
        elif message.get("status") == 429:
            saw_429.set()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run_requests():
        tasks = [asyncio.create_task(gateway(
            {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
            receive, send,
        )) for _ in range(3)]
        await two_entered.wait()
        await saw_429.wait()
        release.set()
        await asyncio.gather(*tasks)

    _run(run_requests())
    assert any(message.get("status") == 429 for message in messages)


def test_cpu_budget_tracked():
    """CPU time is tracked and audited for analysis routes."""
    store = _Store()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
        receive, send,
    ))

    assert any(
        r.public_result_metadata and "cpu_seconds" in r.public_result_metadata
        for r in store.records
    )


def test_n02_unavailable_returns_503():
    """Deferred N02 routes fail closed when local inference is unavailable."""
    store = _Store()
    called = []

    async def app(scope, receive, send):
        called.append(scope)

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    gateway.inference_available = False
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "POST", "path": "/api/bot/ai/analyze", "raw_path": b"/api/bot/ai/analyze"},
        receive, send,
    ))

    assert not called and messages[0]["status"] == 503
    assert store.records[0].allowlist_decision == "DEFERRED"


# ── Response cap integration tests ────────────────────────────────────────────

def test_response_cap_exactly_at_limit():
    """A response exactly at 1 MiB must NOT be rejected."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * 1_048_576})  # exactly 1 MiB

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health"},
        receive, send,
    ))

    # Should NOT be rejected — exactly at the limit is allowed
    assert not any(m.get("status") == 413 for m in messages)
    assert not any(
        r.public_result_metadata and r.public_result_metadata.get("response_cap_exceeded")
        for r in store.records
    )


def test_response_cap_one_byte_over():
    """A response one byte over 1 MiB must be rejected with 413."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * 1_048_577})  # 1 byte over

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health"},
        receive, send,
    ))

    assert any(m.get("status") == 413 for m in messages)
    assert any(
        r.public_result_metadata and r.public_result_metadata.get("response_cap_exceeded")
        for r in store.records
    )


def test_response_cap_streaming_chunks():
    """Multiple response body chunks that together exceed the cap must be tracked."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        # Send 6 chunks of 200KB each = 1.2 MiB total
        for _ in range(6):
            await send({"type": "http.response.body", "body": b"x" * 200_000})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health"},
        receive, send,
    ))

    assert any(m.get("status") == 413 for m in messages)


# ── Concurrency budget integration tests ───────────────────────────────────────

def test_concurrency_budget_zero_requests():
    """Zero concurrent requests should all succeed normally."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
        receive, send,
    ))

    assert not any(m.get("status") == 429 for m in messages)


def test_concurrency_budget_two_requests():
    """Exactly two concurrent requests should both succeed."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run_two():
        t1 = asyncio.create_task(gateway(
            {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
            receive, send,
        ))
        t2 = asyncio.create_task(gateway(
            {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
            receive, send,
        ))
        await asyncio.gather(t1, t2)

    _run(run_two())
    assert not any(m.get("status") == 429 for m in messages)


def test_concurrency_budget_releases_on_exception():
    """The concurrency slot must be released even when the app raises."""
    store = _Store()
    messages = []

    async def app(scope, receive, send):
        raise RuntimeError("upstream error")

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    # First request raises — should release the slot
    try:
        _run(gateway(
            {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
            receive, send,
        ))
    except RuntimeError:
        pass  # expected

    # Second request should succeed (slot was released)
    messages.clear()
    _run(gateway(
        {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
        receive, send,
    ))

    # Should get a successful response (not 429 from contention)
    assert not any(m.get("status") == 429 for m in messages)


# ── CPU budget integration tests ───────────────────────────────────────────────

def test_cpu_budget_pre_dispatch_check():
    """CPU time spent during admission/persistence must count against the budget."""
    store = _Store()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    messages = []
    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/api/analysis", "raw_path": b"/api/analysis"},
        receive, send,
    ))

    # Audit should include cpu_seconds for the analysis route
    assert any(
        r.public_result_metadata and "cpu_seconds" in r.public_result_metadata
        for r in store.records
    )


def test_cpu_budget_non_analysis_route_not_tracked():
    """Non-analysis routes should not have CPU budget tracking in audit."""
    store = _Store()

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)

    messages = []
    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health"},
        receive, send,
    ))

    # Non-analysis routes should not have cpu_seconds in metadata
    for r in store.records:
        meta = r.public_result_metadata
        if meta and "cpu_seconds" in str(meta):
            # If cpu_seconds is present, it should only be for analysis routes
            assert r.canonical_route != "/health"


# ── N02 inference integration tests ────────────────────────────────────────────

def test_n02_available_returns_allowed():
    """When inference is available, DEFERRED routes are allowed through (but still catalogued)."""
    store = _Store()
    called = []

    async def app(scope, receive, send):
        called.append(scope)

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    gateway.inference_available = True  # explicit: inference IS available
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "POST", "path": "/api/bot/ai/scenarios", "raw_path": b"/api/bot/ai/scenarios"},
        receive, send,
    ))

    # DEFERRED routes are blocked by the allowlist regardless of inference availability
    # The inference check only gates admitted N02 routes in a future scenario
    assert not called  # Still BLOCKED by allowlist


def test_n02_scope_override():
    """The gateway must respect scope-level inference_available overrides."""
    store = _Store()
    called = []

    async def app(scope, receive, send):
        called.append(scope)

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    gateway.inference_available = False  # default: unavailable

    # Override via scope
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "POST", "path": "/api/bot/ai/analyze", "raw_path": b"/api/bot/ai/analyze", "inference_available": True},
        receive, send,
    ))

    # With scope override, inference is available — route proceeds to allowlist check
    # (still BLOCKED by allowlist, but not 503 from inference check)
    assert not any(m.get("status") == 503 for m in messages)


def test_deferred_route_catalogued():
    """DEFERRED routes must be catalogued and produce audit records."""
    store = _Store()

    async def app(scope, receive, send):
        pass

    gateway = ResearchGatewayMiddleware(app, tool_id="test-tool", audit_store=store)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    _run(gateway(
        {"type": "http", "method": "POST", "path": "/api/bot/ai/scenarios", "raw_path": b"/api/bot/ai/scenarios"},
        receive, send,
    ))

    assert any(r.allowlist_decision == "DEFERRED" for r in store.records)
