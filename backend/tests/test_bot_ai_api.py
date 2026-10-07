"""C12 migrado a N02: escenarios tipados, restricciones Spot y cero autoridad.

Los cuerpos libres, el texto strategy_draft y los errores HTTP 200 pertenecían
al contrato anterior. Se conservan sus garantías con snapshots válidos, reloj
fijo y transporte simulado. Esta suite no inicia el lifespan de mercado.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.bot as bot_api
from src.ai.ollama_client import OllamaClient, OllamaMessage, OllamaResponse
from src.ai.scenario_models import (
    INVERSE, MAX_SNAPSHOT_AGE_MS, QWEN_MODEL, ScenarioAnalysisResult,
    ScenarioError, ScenarioOutput, ScenarioSnapshot,
)
from src.bot.models import Order, OrderType, Position, Side
from src.bot.rules import RulesEngine, create_default_strategy


CLOSE_MS = 1_800_000_059_999
NOW_MS = CLOSE_MS + 1
ENDPOINTS = ("analyze", "scenarios", "strategy")


def _snapshot_dict(source="TEST_ONLY", snapshot_id="c12-snapshot"):
    return {
        "schema_version": "market_snapshot.v1", "snapshot_id": snapshot_id,
        "symbol": "BTC/USDT", "timeframe": "1m", "environment": "paper",
        "data_source": source, "captured_at_ms": NOW_MS,
        "as_of_close_time_ms": CLOSE_MS,
        "valid_until_ms": CLOSE_MS + MAX_SNAPSHOT_AGE_MS,
        "market_state": {
            "connected": True, "history_loaded": True, "complete": True,
            "recovering": False, "stale": False, "pending_gaps": 0,
        },
        "evidence": {
            name: {"value": value, "dimension": "price", "unit": "USDT",
                   "observed_at_ms": CLOSE_MS}
            for name, value in (("close", 65_100.0), ("ema", 65_000.0),
                                ("resistance", 65_200.0))
        },
        "missing_data": ["order_book"],
    }


def _output_dict(context):
    def scenario(conditions):
        refs = {ref for metric, _, threshold in conditions for ref in (metric, threshold)}
        refs.update(("close", "ema"))
        return {
            "evidence": [{"evidence_id": ref, "observed_value": context.evidence[ref].value}
                         for ref in sorted(refs)],
            "observations": [{"left_ref": "close", "operator": "gt", "right_ref": "ema"}],
            "condition_logic": "all",
            "conditions": [{"applies_to": "future_closed_snapshot", "metric_ref": metric,
                            "operator": op, "threshold_ref": threshold}
                           for metric, op, threshold in conditions],
            "invalidation_logic": "any",
            "invalidations": [{"applies_to": "future_closed_snapshot", "metric_ref": metric,
                               "operator": INVERSE[op], "threshold_ref": threshold}
                              for metric, op, threshold in conditions],
        }

    return {
        "schema_version": "scenario_analysis.v1",
        **{name: getattr(context, name) for name in (
            "snapshot_id", "symbol", "timeframe", "as_of_close_time_ms",
            "valid_until_ms", "data_source",
        )},
        "result_type": "scenario_advisory", "executable": False,
        "provenance_verified": False,
        "scenarios": {
            "continuation": scenario([("close", "gt", "resistance")]),
            "failure": scenario([("close", "lt", "ema")]),
            "sideways": scenario([("close", "gte", "ema"), ("close", "lte", "resistance")]),
        },
        "missing_data": list(context.missing_data),
    }


def _runtime_response(context):
    return {
        "model": QWEN_MODEL, "created_at": "2026-10-04T12:00:00Z",
        "message": {"role": "assistant", "content": json.dumps(_output_dict(context))},
        "done": True, "done_reason": "stop",
        "total_duration": 2_000_000_000, "load_duration": 100_000_000,
        "prompt_eval_count": 100, "eval_count": 50, "eval_duration": 1_000_000_000,
    }


class _FakeOllama:
    """Doble del contrato de rutas; las pruebas de transporte usan el cliente real."""

    def __init__(self, error_code=None):
        self.calls = []
        self.error_code = error_code

    async def analyze_snapshot(self, context):
        assert isinstance(context, ScenarioSnapshot)
        self.calls.append(context.model_copy(deep=True))
        context.assert_current(NOW_MS)
        if self.error_code:
            raise ScenarioError(self.error_code)
        analysis = ScenarioOutput.model_validate(_output_dict(context))
        analysis.validate_for(context, NOW_MS)
        return ScenarioAnalysisResult(
            analysis=analysis, model_used=QWEN_MODEL, duration_ms=1,
            data_source="TEST_ONLY" if context.data_source == "TEST_ONLY" else "unverified",
            summaries={name: "Hipótesis condicional no ejecutable."
                       for name in ("continuation", "failure", "sideways")},
        )

    async def health_check(self):
        raise AssertionError("La ruta debe delegar al contrato analyze_snapshot")

    async def analyze_market(self, _):
        raise AssertionError("La ruta no debe usar la interfaz antigua")

    async def generate_strategy(self, _):
        raise AssertionError("La ruta no debe generar estrategias operativas")

    async def close(self):
        pass


class _FakeHTTP:
    """GET de disponibilidad y POST de inferencia observables, nunca de red."""

    def __init__(self):
        self.calls = []
        self.payloads = []
        self.tag_status = 200
        self.tags = [{"name": QWEN_MODEL}]
        self.tag_error = None
        self.chat_status = 200
        self.chat_error = None
        self.raw_reply = None

    def handle(self, request):
        self.calls.append((request.method, request.url.path))
        if (request.method, request.url.path) == ("GET", "/api/tags"):
            if self.tag_error:
                raise self.tag_error
            return httpx.Response(self.tag_status, json={"models": self.tags})
        assert (request.method, request.url.path) == ("POST", "/api/chat")
        payload = json.loads(request.content)
        self.payloads.append(payload)
        if self.chat_error:
            raise self.chat_error
        if self.raw_reply is not None:
            return httpx.Response(self.chat_status, content=self.raw_reply)
        context = ScenarioSnapshot.model_validate(json.loads(payload["messages"][1]["content"]))
        return httpx.Response(self.chat_status, json=_runtime_response(context))


@contextmanager
def _isolated_bot_state() -> Iterator[None]:
    original_state = bot_api._bot_state
    bot_api._bot_state = {
        "strategy": create_default_strategy(), "engine": None,
        "positions": [], "orders": [], "ollama_client": None,
    }
    try:
        yield
    finally:
        bot_api._bot_state = original_state


@pytest.fixture(autouse=True)
def isolated_state_and_no_network(monkeypatch):
    def guarded_http(service):
        assert service._http is not None, "C12 exige HTTP falso inyectado"
        return service._http

    async def forbid_http(*args, **kwargs):
        raise AssertionError("C12 prohíbe HTTP de red real")

    def forbid_network(*args, **kwargs):
        raise AssertionError("C12 prohíbe conexiones/DNS reales")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            forbid_network()
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            forbid_network()
        return original_connect_ex(sock, address)

    monkeypatch.setattr(OllamaClient, "_get_http", guarded_http)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_http)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(socket, "gethostbyname", forbid_network)
    monkeypatch.setattr(socket, "gethostbyname_ex", forbid_network)
    monkeypatch.setattr(socket, "gethostbyaddr", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    with _isolated_bot_state():
        yield


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(bot_api.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def scenario_service(client):
    fake = _FakeHTTP()
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handle), trust_env=False)
    service = OllamaClient(base_url="http://ollama.test:11434", http=http, clock_ms=lambda: NOW_MS)
    bot_api._bot_state["ollama_client"] = service
    try:
        yield service, fake
    finally:
        client.portal.call(service.close)
        client.portal.call(http.aclose)


def _post(client, endpoint, snapshot=None):
    return client.post(f"/api/bot/ai/{endpoint}", json={
        "snapshot": _snapshot_dict() if snapshot is None else snapshot,
    })


def _assert_success(response):
    assert response.status_code == 200, response.text
    result = ScenarioAnalysisResult.model_validate(response.json())
    assert result.available is True
    assert result.result_type == "scenario_advisory"
    assert result.executable is False
    assert result.execution_available is False
    assert result.analysis.executable is False
    assert result.analysis.provenance_verified is False
    assert set(result.summaries) == {"continuation", "failure", "sideways"}
    assert all(result.summaries.values())
    assert "BORRADOR" not in response.text
    assert "strategy_draft" not in response.text
    return result


def _assert_error(response, status, code):
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["available"] is False
    assert detail["executable"] is False
    assert detail["execution_available"] is False
    assert detail["requested_model"] == QWEN_MODEL
    assert isinstance(detail["duration_ms"], int)
    assert detail["duration_ms"] >= 0
    assert "analysis" not in detail
    return detail


@pytest.mark.parametrize("fail_inside", [False, True])
def test_isolated_state_restores_original(fail_inside):
    original_state = bot_api._bot_state
    original_strategy = original_state["strategy"]
    original_client = _FakeOllama()
    original_state["ollama_client"] = original_client
    before = deepcopy({k: v for k, v in original_state.items() if k != "ollama_client"})

    def exercise():
        with _isolated_bot_state():
            assert bot_api._bot_state is not original_state
            bot_api._bot_state["strategy"].rules[0].enabled = False
            bot_api._bot_state["positions"].append({"test_only": True})
            bot_api._bot_state["ollama_client"] = _FakeOllama()
            if fail_inside:
                raise RuntimeError("simulated test failure")

    if fail_inside:
        with pytest.raises(RuntimeError, match="simulated test failure"):
            exercise()
    else:
        exercise()
    assert bot_api._bot_state is original_state
    assert original_state["strategy"] is original_strategy
    assert original_state["ollama_client"] is original_client
    assert {k: v for k, v in original_state.items() if k != "ollama_client"} == before


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_ai_returns_structured_non_executable_scenarios(client, scenario_service, endpoint):
    result = _assert_success(_post(client, endpoint))
    _, fake = scenario_service
    assert fake.calls == [("GET", "/api/tags"), ("POST", "/api/chat")]
    context = ScenarioSnapshot.model_validate(_snapshot_dict())
    result.analysis.validate_for(context, NOW_MS)
    assert result.analysis.model_dump() == _output_dict(context)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_route_passes_complete_typed_snapshot(client, endpoint):
    fake = _FakeOllama()
    bot_api._bot_state["ollama_client"] = fake
    payload = _snapshot_dict()
    before = deepcopy(payload)
    _assert_success(_post(client, endpoint, payload))
    assert len(fake.calls) == 1
    assert fake.calls[0].model_dump() == before
    assert payload == before


@pytest.mark.parametrize("source,expected", [
    ("TEST_ONLY", "TEST_ONLY"), ("market_engine", "unverified"), ("unverified", "unverified"),
])
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_ai_preserves_declared_origin_without_certifying_it(
    client, scenario_service, source, expected, endpoint,
):
    result = _assert_success(_post(client, endpoint, _snapshot_dict(source)))
    assert result.data_source == expected
    assert result.analysis.data_source == source
    assert result.analysis.provenance_verified is False
    _, fake = scenario_service
    assert len(fake.payloads) == 1


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("payload", [
    {"symbol": "BTC/USDT", "timeframe": "15m", "market_data": {"data_source": "TEST_ONLY"}},
    {"snapshot": _snapshot_dict(), "market_data": {}},
])
def test_old_or_mixed_contract_is_rejected_before_client_creation(client, monkeypatch, endpoint, payload):
    constructor = Mock(side_effect=AssertionError("Una petición inválida no debe crear Ollama"))
    monkeypatch.setattr(bot_api, "OllamaClient", constructor)
    assert client.post(f"/api/bot/ai/{endpoint}", json=payload).status_code == 422
    constructor.assert_not_called()
    assert bot_api._bot_state["ollama_client"] is None


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("cause", ["unavailable", "model_missing", "transport_failure"])
def test_unavailable_model_prevents_inference(client, scenario_service, endpoint, cause):
    _, fake = scenario_service
    if cause == "unavailable":
        fake.tag_status = 503
    elif cause == "model_missing":
        fake.tags = [{"name": "llama3"}]
    else:
        fake.tag_error = httpx.ConnectError("private http://localhost:11434 abc123")
    _assert_error(_post(client, endpoint), 503,
                  "model_missing" if cause == "model_missing" else "unavailable")
    assert fake.calls == [("GET", "/api/tags")]
    assert fake.payloads == []


@pytest.mark.parametrize("cause", ["unavailable", "transport_failure"])
def test_health_check_fails_without_inference(client, scenario_service, cause):
    service, fake = scenario_service
    if cause == "unavailable":
        fake.tag_status = 503
    else:
        fake.tag_error = httpx.ConnectError("private availability failure")
    assert client.portal.call(service.health_check) is False
    assert fake.calls == [("GET", "/api/tags")]
    assert fake.payloads == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("cause,status,code", [
    ("failure", 503, "unavailable"), ("timeout", 504, "timeout"),
    ("invalid_json", 502, "invalid_json"),
])
def test_generation_errors_are_public_and_do_not_leak_private_data(
    client, scenario_service, endpoint, cause, status, code,
):
    _, fake = scenario_service
    private = "abc123 secret456 http://localhost:11434 private prompt"
    if cause == "failure":
        fake.chat_error = httpx.ConnectError(private)
    elif cause == "timeout":
        fake.chat_error = httpx.ReadTimeout(private)
    else:
        fake.raw_reply = private.encode()
    payload = _snapshot_dict(snapshot_id="private-snapshot-abc123")
    _assert_error(_post(client, endpoint, payload), status, code)
    response = _post(client, endpoint, payload)
    _assert_error(response, status, code)
    for secret in ("abc123", "secret456", "localhost:11434", "private prompt", payload["snapshot_id"]):
        assert secret not in response.text
    # Ni el fallo ni el timeout se cachean; ambos intentos llegan al transporte.
    assert fake.calls == [("GET", "/api/tags"), ("POST", "/api/chat")] * 2


def _assert_spot_messages(messages: list[OllamaMessage], context):
    assert [message.role for message in messages] == ["system", "user"]
    system = messages[0].content.lower()
    assert "binance spot" in system
    assert "nunca una estrategia" in system
    assert "no tienes autoridad para comprar, vender, fijar tamaños, stops, riesgo ni ejecución" in system
    assert "no short, margin, futuros, préstamos o apalancamiento" in system
    assert "no prosa" in system
    assert "instrucciones operativas" in system
    assert "futuro snapshot cerrado" in system
    assert "executable=false" in system
    # El usuario lleva solo datos tipados, no instrucciones de estrategia libre.
    assert json.loads(messages[1].content) == context.model_dump(mode="json")


@pytest.mark.parametrize("method", ["analyze_market", "generate_strategy"])
def test_legacy_named_wrappers_keep_spot_restrictions_and_typed_output(monkeypatch, method):
    async def exercise():
        context = ScenarioSnapshot.model_validate(_snapshot_dict())
        service = OllamaClient(clock_ms=lambda: NOW_MS)
        chat = AsyncMock(return_value=OllamaResponse.model_validate(_runtime_response(context)))
        monkeypatch.setattr(service, "chat", chat)
        try:
            argument = context if method == "analyze_market" else context.model_dump_json()
            result = await getattr(service, method)(argument)
            chat.assert_awaited_once()
            _assert_spot_messages(chat.await_args.args[0], context)
            assert chat.await_args.kwargs["snapshot"] == context
            schema = chat.await_args.kwargs["response_schema"]
            assert schema["properties"]["snapshot_id"]["const"] == context.snapshot_id
            assert result.result_type == "scenario_advisory"
            assert result.executable is False
            assert result.execution_available is False
            result.analysis.validate_for(context, NOW_MS)
            assert service._http is None
        finally:
            await service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("outcome,status,code", [
    ("available", 200, None), ("unavailable", 503, "unavailable"),
    ("failure", 502, "invalid_json"), ("timeout", 504, "timeout"),
])
@pytest.mark.parametrize("engine_initialized", [False, True])
def test_no_state_modification_from_ai_endpoints(
    client, monkeypatch, endpoint, outcome, status, code, engine_initialized,
):
    strategy = bot_api._bot_state["strategy"]
    strategy.max_position_size = 0.5
    strategy.stop_loss_pct = 0.03
    strategy.take_profit_pct = 0.06
    strategy.rules[0].weight = 0.4
    bot_api._bot_state["positions"] = [Position(
        id="test-position", symbol="BTC/USDT", side=Side.BUY,
        quantity=0.01, entry_price=60000.0, current_price=61000.0,
        timestamp_ms=1700000000000, stop_loss_price=58000.0,
    )]
    bot_api._bot_state["orders"] = [Order(
        id="test-order", symbol="BTC/USDT", side=Side.SELL,
        order_type=OrderType.LIMIT, quantity=0.005, price=65000.0,
        timestamp_ms=1700000000000,
    )]
    if engine_initialized:
        bot_api._bot_state["engine"] = RulesEngine(strategy)
    engine = bot_api._bot_state["engine"]
    active_rules = deepcopy(engine.active_rules) if engine else None
    before = deepcopy({k: v for k, v in bot_api._bot_state.items()
                       if k not in ("engine", "ollama_client")})
    evaluate = Mock(side_effect=AssertionError("IA no debe evaluar reglas"))
    construct_rules = Mock(side_effect=AssertionError("IA no debe inicializar reglas"))
    monkeypatch.setattr(RulesEngine, "evaluate", evaluate)
    monkeypatch.setattr(bot_api, "RulesEngine", construct_rules)
    fake = _FakeOllama(code)
    bot_api._bot_state["ollama_client"] = fake

    response = _post(client, endpoint)
    if outcome == "available":
        _assert_success(response)
    else:
        _assert_error(response, status, code)
    assert len(fake.calls) == 1  # No aprobar por un 422 antes de la lógica de IA.
    evaluate.assert_not_called()
    construct_rules.assert_not_called()
    assert bot_api._bot_state["strategy"] is strategy
    assert {k: v for k, v in bot_api._bot_state.items()
            if k not in ("engine", "ollama_client")} == before
    assert bot_api._bot_state["engine"] is engine
    assert bot_api._bot_state["ollama_client"] is fake
    if engine is not None:
        assert engine.strategy is strategy
        assert engine.active_rules == active_rules


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_ollama_lazy_initialization_is_only_change_and_reuses_client(client, monkeypatch, endpoint):
    fake = _FakeOllama()
    constructor = Mock(return_value=fake)
    monkeypatch.setattr(bot_api, "OllamaClient", constructor)
    before = deepcopy({k: v for k, v in bot_api._bot_state.items() if k != "ollama_client"})
    assert bot_api._bot_state["ollama_client"] is None
    for _ in range(2):
        _assert_success(_post(client, endpoint))
    constructor.assert_called_once_with(model=QWEN_MODEL)
    assert len(fake.calls) == 2
    assert bot_api._bot_state["ollama_client"] is fake
    assert {k: v for k, v in bot_api._bot_state.items() if k != "ollama_client"} == before


def test_strategy_alias_is_deprecated_in_openapi(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/bot/ai/strategy"]["post"]["deprecated"] is True
