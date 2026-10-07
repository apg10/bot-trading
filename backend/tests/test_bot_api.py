"""Estados, propuestas TEST_ONLY y contrato N02; sin mercado ni red real."""

import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.bot as bot_api
from src.ai.ollama_client import OllamaClient
from src.ai.scenario_models import MAX_SNAPSHOT_AGE_MS, QWEN_MODEL
from src.bot.rules import RulesEngine, create_default_strategy


CLOSE_MS = 1_800_000_059_999  # cierre inclusivo Binance de una vela 1m
NOW_MS = CLOSE_MS + 1


@pytest.fixture(autouse=True)
def isolated_state_and_no_network(monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("Esta suite prohíbe HTTP, conexiones y DNS reales")

    async def forbid_async_http(*args, **kwargs):
        forbid_network()

    # Conservar AF_UNIX para los sockets locales usados por asyncio/AnyIO.
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            forbid_network()
        return connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            forbid_network()
        return connect_ex(sock, address)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_async_http)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(socket, "gethostbyname", forbid_network)
    monkeypatch.setattr(socket, "gethostbyname_ex", forbid_network)
    monkeypatch.setattr(socket, "gethostbyaddr", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(bot_api, "_bot_state", {
        "strategy": create_default_strategy(), "engine": None,
        "positions": [], "orders": [], "ollama_client": None,
    })


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(bot_api.router)
    with TestClient(app) as test_client:
        yield test_client


def snapshot_dict(source="TEST_ONLY"):
    return {
        "schema_version": "market_snapshot.v1",
        "snapshot_id": "bot-api-test",
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "environment": "paper",
        "data_source": source,
        "captured_at_ms": NOW_MS,
        "as_of_close_time_ms": CLOSE_MS,
        "valid_until_ms": CLOSE_MS + MAX_SNAPSHOT_AGE_MS,
        "market_state": {
            "connected": True, "history_loaded": True, "complete": True,
            "recovering": False, "stale": False, "pending_gaps": 0,
        },
        "evidence": {
            ref: {
                "value": value, "dimension": "price", "unit": "USDT",
                "observed_at_ms": CLOSE_MS,
            }
            for ref, value in (
                ("close", 65_100.0), ("ema", 65_000.0), ("resistance", 65_200.0),
            )
        },
        "missing_data": ["order_book"],
    }


def scenario_output(snapshot):
    def condition(operator, threshold):
        return {
            "applies_to": "future_closed_snapshot", "metric_ref": "close",
            "operator": operator, "threshold_ref": threshold,
        }

    def scenario(operator, inverse, threshold):
        return {
            "evidence": [
                {"evidence_id": ref, "observed_value": snapshot["evidence"][ref]["value"]}
                for ref in ("close", "ema", "resistance")
            ],
            "observations": [{"left_ref": "close", "operator": "gt", "right_ref": "ema"}],
            "condition_logic": "all",
            "conditions": [condition(operator, threshold)],
            "invalidation_logic": "any",
            "invalidations": [condition(inverse, threshold)],
        }

    return {
        "schema_version": "scenario_analysis.v1",
        **{key: snapshot[key] for key in (
            "snapshot_id", "symbol", "timeframe", "as_of_close_time_ms",
            "valid_until_ms", "data_source",
        )},
        "result_type": "scenario_advisory", "executable": False,
        "provenance_verified": False,
        "scenarios": {
            "continuation": scenario("gt", "lte", "resistance"),
            "failure": scenario("lt", "gte", "ema"),
            "sideways": {
                **scenario("gte", "lt", "ema"),
                "conditions": [condition("gte", "ema"), condition("lte", "resistance")],
                "invalidations": [condition("lt", "ema"), condition("gt", "resistance")],
            },
        },
        "missing_data": list(snapshot["missing_data"]),
    }


@contextmanager
def mocked_ollama(client, snapshot):
    calls, payloads = [], []

    def handle(request):
        assert request.url.host == "ollama.test"
        calls.append((request.method, request.url.path))
        if (request.method, request.url.path) == ("GET", "/api/tags"):
            return httpx.Response(200, json={"models": [{"name": QWEN_MODEL}]})
        assert (request.method, request.url.path) == ("POST", "/api/chat")
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": QWEN_MODEL, "created_at": "2027-01-15T08:01:00.000Z",
            "message": {"role": "assistant", "content": json.dumps(scenario_output(snapshot))},
            "done": True, "done_reason": "stop",
        })

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle), trust_env=False)
    ollama = OllamaClient(
        base_url="http://ollama.test:11434", http=http, clock_ms=lambda: NOW_MS,
    )
    bot_api._bot_state["ollama_client"] = ollama
    try:
        yield calls, payloads
    finally:
        # El cliente HTTP inyectado pertenece al test; cerrar ambos en el loop de la API.
        client.portal.call(ollama.close)
        client.portal.call(http.aclose)


@pytest.fixture
def unused_ollama(monkeypatch):
    analyze = AsyncMock(side_effect=AssertionError("Una entrada inválida no debe invocar Ollama"))
    constructor = Mock(side_effect=AssertionError("Una entrada inválida no debe crear Ollama"))
    monkeypatch.setattr(OllamaClient, "analyze_snapshot", analyze)
    monkeypatch.setattr(bot_api, "OllamaClient", constructor)
    return constructor, analyze


def test_bot_status_does_not_claim_execution_or_a_queried_portfolio(client):
    data = client.get("/api/bot/status").json()
    assert data["is_running"] is False
    assert data["implementation_status"] == "not_implemented"
    assert data["execution_available"] is False
    assert data["portfolio_available"] is False
    assert data["positions_count"] is None
    assert data["orders_count"] is None
    assert data["strategy_status"] == "TEST_ONLY"
    assert bot_api._bot_state["engine"] is None


def test_existing_rule_evaluator_does_not_make_bot_running(client):
    bot_api._bot_state["engine"] = RulesEngine(bot_api._bot_state["strategy"])
    assert client.get("/api/bot/status").json()["is_running"] is False


def test_placeholder_lists_are_not_reported_as_account_counts(client):
    bot_api._bot_state["positions"] = ["not an exchange position"]
    bot_api._bot_state["orders"] = ["not an exchange order"]
    data = client.get("/api/bot/status").json()
    assert data["positions_count"] is None
    assert data["orders_count"] is None


def test_status_does_not_wait_for_or_probe_ollama(client):
    class UnavailableProbe:
        async def health_check(self):
            raise AssertionError("Status must not call Ollama")

    bot_api._bot_state["ollama_client"] = UnavailableProbe()
    response = client.get("/api/bot/status")
    assert response.status_code == 200
    assert response.json()["ollama_available"] is None


@pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT"])
def test_fixed_rule_analysis_is_explicitly_test_only_and_never_executes(client, symbol):
    response = client.post("/api/bot/analyze", json={"symbol": symbol})
    assert response.status_code == 200
    data = response.json()
    assert data["data_source"] == "TEST_ONLY"
    assert data["result_type"] == "proposal"
    assert data["executable"] is False
    assert data["execution_available"] is False
    assert data["analysis"]["data_source"] == "TEST_ONLY"
    assert data["signal"]["data_source"] == "TEST_ONLY"
    assert data["signal"]["result_type"] == "proposal"
    assert data["signal"]["executable"] is False
    assert data["signal"]["symbol"] == symbol
    assert bot_api._bot_state["positions"] == []
    assert bot_api._bot_state["orders"] == []
    assert client.get("/api/bot/status").json()["is_running"] is False


@pytest.mark.parametrize("source, expected", [
    ("TEST_ONLY", "TEST_ONLY"),
    ("market_engine", "unverified"),
    ("unverified", "unverified"),
])
def test_ai_response_is_advisory_and_preserves_test_origin(client, source, expected):
    snapshot = snapshot_dict(source)
    strategy_before = bot_api._bot_state["strategy"].model_dump()
    with mocked_ollama(client, snapshot) as (calls, payloads):
        response = client.post("/api/bot/ai/analyze", json={"snapshot": snapshot})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["available"] is True
        assert data["data_source"] == expected
        assert data["result_type"] == "scenario_advisory"
        assert data["executable"] is False
        assert data["execution_available"] is False
        assert data["requested_model"] == data["model_used"] == QWEN_MODEL
        assert data["analysis"] == scenario_output(snapshot)
        assert data["analysis"]["data_source"] == source
        assert data["analysis"]["provenance_verified"] is False
        assert calls == [("GET", "/api/tags"), ("POST", "/api/chat")]
        assert len(payloads) == 1
        assert payloads[0]["model"] == QWEN_MODEL
        assert payloads[0]["stream"] is False
        assert payloads[0]["think"] is False
        assert payloads[0]["format"]["properties"]["data_source"]["const"] == source
        assert json.loads(payloads[0]["messages"][-1]["content"]) == snapshot
    assert bot_api._bot_state["strategy"].model_dump() == strategy_before
    assert bot_api._bot_state["engine"] is None
    assert bot_api._bot_state["positions"] == []
    assert bot_api._bot_state["orders"] == []


@pytest.mark.parametrize("source", ["synthetic_test", None], ids=["synthetic_test", "missing_origin"])
def test_invalid_snapshot_origin_is_rejected_before_ollama(client, unused_ollama, source):
    snapshot = snapshot_dict()
    if source is None:
        del snapshot["data_source"]
    else:
        snapshot["data_source"] = source
    response = client.post("/api/bot/ai/analyze", json={"snapshot": snapshot})
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "snapshot", "data_source"]
               for error in response.json()["detail"])
    constructor, analyze = unused_ollama
    constructor.assert_not_called()
    analyze.assert_not_called()
    assert bot_api._bot_state["ollama_client"] is None


@pytest.mark.parametrize("source", ["TEST_ONLY", "synthetic_test", "market_engine", None])
def test_legacy_market_data_format_is_rejected_before_ollama(client, unused_ollama, source):
    market_data = {} if source is None else {"data_source": source}
    response = client.post("/api/bot/ai/analyze", json={
        "symbol": "BTC/USDT", "market_data": market_data,
    })
    assert response.status_code == 422
    assert any(error["loc"] == ["body", "snapshot"] and error["type"] == "missing"
               for error in response.json()["detail"])
    constructor, analyze = unused_ollama
    constructor.assert_not_called()
    analyze.assert_not_called()
    assert bot_api._bot_state["ollama_client"] is None
