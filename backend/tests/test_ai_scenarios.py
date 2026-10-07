"""Contrato N02: escenarios tipados, HTTP falso y ninguna autoridad operativa.

Se ejecuta con pytest normal: cada prueba async usa asyncio.run, sin lifespan,
src.api.main, Ollama real, Binance ni dependencia de pytest-asyncio.

Migración N02 deliberada: los tests antiguos test_bot_ai_api/test_bot_api que
envían market_data libre o esperan analysis textual/strategy_draft ya no definen
este contrato. No se editan aquí: están fuera de los cuatro archivos autorizados.
Estos tests cubren sus garantías de no ejecución con snapshots tipados y además
comprueban que la entrada antigua se rechaza antes de crear un cliente.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import math
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

import src.api.bot as bot_api
from src.ai.ollama_client import OllamaClient, OllamaMessage
from src.ai.scenario_models import (
    ERROR_STATUS,
    INVERSE,
    MAX_SNAPSHOT_AGE_MS,
    QWEN_MODEL,
    ScenarioAnalysisRequest,
    ScenarioAnalysisResult,
    ScenarioError,
    ScenarioOutput,
    ScenarioSnapshot,
)


def test_missing_optional_httpx_fails_closed_without_network(client_factory, monkeypatch):
    import src.ai.ollama_client as module

    def missing():
        raise ScenarioError("unavailable")

    async def exercise():
        monkeypatch.setattr(module, "_httpx_module", missing)
        async with client_factory() as (client, fake, _):
            assert await client.health_check() is False
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "unavailable")
            assert fake.calls == []

    asyncio.run(exercise())


CLOSE_MS = 1_800_000_059_999  # cierre inclusivo Binance 1m
AI_PATHS = (
    "/api/bot/ai/analyze",
    "/api/bot/ai/scenarios",
    "/api/bot/ai/strategy",
)


def snapshot_dict(snapshot_id="snapshot-test", *, close_ms=CLOSE_MS):
    def evidence(value, dimension="price", unit="USDT", observed=None):
        return {
            "value": value, "dimension": dimension, "unit": unit,
            "observed_at_ms": close_ms if observed is None else observed,
        }

    return {
        "schema_version": "market_snapshot.v1",
        "snapshot_id": snapshot_id,
        "symbol": "BTC/USDT",
        "timeframe": "1m",
        "environment": "paper",
        "data_source": "TEST_ONLY",
        "captured_at_ms": close_ms + 1,
        "as_of_close_time_ms": close_ms,
        "valid_until_ms": close_ms + MAX_SNAPSHOT_AGE_MS,
        "market_state": {
            "connected": True, "history_loaded": True, "complete": True,
            "recovering": False, "stale": False, "pending_gaps": 0,
        },
        "evidence": {
            "close": evidence(65_100.0),
            "ema": evidence(65_000.0),
            "resistance": evidence(65_200.0),
            "hist": evidence(184.0, "oscillator"),
            "hist_previous": evidence(203.0, "oscillator", observed=close_ms - 60_000),
            "volume": evidence(10.0, "volume", "BTC"),
        },
        "missing_data": ["order_book"],
    }


def snapshot(snapshot_id="snapshot-test", **kwargs):
    return ScenarioSnapshot.model_validate(snapshot_dict(snapshot_id, **kwargs))


def condition(metric, operator, threshold):
    return {
        "applies_to": "future_closed_snapshot", "metric_ref": metric,
        "operator": operator, "threshold_ref": threshold,
    }


def complement(conditions):
    return [dict(item, operator=INVERSE[item["operator"]]) for item in conditions]


def output_dict(context=None):
    context = context or snapshot()

    def scenario(conditions, observations):
        refs = {item["metric_ref"] for item in conditions}
        refs.update(item["threshold_ref"] for item in conditions)
        refs.update(item["left_ref"] for item in observations)
        refs.update(item["right_ref"] for item in observations)
        return {
            "evidence": [
                {"evidence_id": ref, "observed_value": context.evidence[ref].value}
                for ref in sorted(refs)
            ],
            "observations": observations,
            "condition_logic": "all", "conditions": conditions,
            "invalidation_logic": "any", "invalidations": complement(conditions),
        }

    return {
        "schema_version": "scenario_analysis.v1",
        **{key: getattr(context, key) for key in (
            "snapshot_id", "symbol", "timeframe", "as_of_close_time_ms",
            "valid_until_ms", "data_source",
        )},
        "result_type": "scenario_advisory", "executable": False, "provenance_verified": False,
        "scenarios": {
            "continuation": scenario(
                [condition("close", "gt", "resistance")],
                [{"left_ref": "close", "operator": "gt", "right_ref": "ema"}],
            ),
            "failure": scenario(
                [condition("hist", "lt", "hist_previous")],
                [{"left_ref": "hist", "operator": "lt", "right_ref": "hist_previous"}],
            ),
            "sideways": scenario(
                [condition("close", "gte", "ema"), condition("close", "lte", "resistance")],
                [],
            ),
        },
        "missing_data": list(context.missing_data),
    }


def runtime_response(context=None, *, content=None):
    return {
        "model": QWEN_MODEL, "created_at": "2026-10-04T12:00:00.000Z",
        "message": {
            "role": "assistant",
            "content": json.dumps(output_dict(context)) if content is None else content,
        },
        "done": True, "done_reason": "stop",
        "total_duration": 2_000_000_000, "load_duration": 100_000_000,
        "prompt_eval_count": 100, "eval_count": 50, "eval_duration": 1_000_000_000,
    }


@dataclass
class Clock:
    now: int = CLOSE_MS + 1

    def __call__(self):
        return self.now


class FakeOllamaHTTP:
    """Un servidor determinista dentro de MockTransport, con POST bloqueable."""

    def __init__(self, context=None):
        self.reply = runtime_response(context)
        self.tags = [{"name": QWEN_MODEL}]
        self.calls = []
        self.payloads = []
        self.tag_status = 200
        self.chat_status = 200
        self.tag_error = None
        self.chat_error = None
        self.raw_reply = None
        self.before_post = None
        self.delay_s = 0.0
        self.block = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.active = 0
        self.max_active = 0

    @property
    def posts(self):
        return self.calls.count(("POST", "/api/chat"))

    @property
    def gets(self):
        return self.calls.count(("GET", "/api/tags"))

    async def handle(self, request):
        self.calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/tags":
            if self.tag_error:
                raise self.tag_error
            return httpx.Response(self.tag_status, json={"models": self.tags})
        assert (request.method, request.url.path) == ("POST", "/api/chat")
        self.payloads.append(json.loads(request.content))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            if self.before_post:
                self.before_post()
            if self.block:
                await self.release.wait()
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            if self.chat_error:
                raise self.chat_error
            if self.raw_reply is not None:
                return httpx.Response(self.chat_status, content=self.raw_reply)
            return httpx.Response(self.chat_status, json=self.reply)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1


async def eventually(predicate: Callable[[], bool], timeout=1.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.002)


async def no_new_tasks(baseline):
    await eventually(lambda: not {
        task for task in asyncio.all_tasks()
        if task not in baseline and not task.done()
    })


def assert_error(error, code):
    assert isinstance(error, ScenarioError)
    assert error.code == code
    assert error.status_code == ERROR_STATUS[code]
    assert isinstance(error.duration_ms, int) and error.duration_ms >= 0


@pytest.fixture(autouse=True)
def isolated_state_and_fake_http_only(monkeypatch):
    """Falla cerrado incluso si una ruta crea un cliente inesperado."""
    allowed = {}

    def guarded_get_http(client):
        assert id(client) in allowed, "N02 prohíbe un OllamaClient sin HTTP falso inyectado"
        return allowed[id(client)]

    async def forbid_network(*args, **kwargs):
        raise AssertionError("N02 prohíbe HTTP de red real")

    def forbid_socket_network(*args, **kwargs):
        raise AssertionError("N02 prohíbe conexiones/DNS reales, incluido cualquier SDK")

    monkeypatch.setattr(OllamaClient, "_get_http", guarded_get_http)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_socket_network)
    monkeypatch.setattr(socket, "create_connection", forbid_socket_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_socket_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_socket_network)
    monkeypatch.setattr(bot_api, "_bot_state", {
        "strategy": deepcopy(bot_api._bot_state["strategy"]),
        "engine": None, "positions": [{"id": "position-test", "quantity": 0.01}],
        "orders": [{"id": "order-test", "price": 65_000.0}],
        "ollama_client": None,
    })
    return allowed


@pytest.fixture
def client_factory(isolated_state_and_fake_http_only):
    @asynccontextmanager
    async def make(fake=None, clock=None, *, timeout_s=110.0):
        fake = fake or FakeOllamaHTTP()
        clock = clock or Clock()
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
            client = OllamaClient(
                base_url="http://ollama.test:11434", model=QWEN_MODEL,
                http=http, clock_ms=clock, timeout_s=timeout_s,
            )
            isolated_state_and_fake_http_only[id(client)] = http
            try:
                yield client, fake, clock
            finally:
                await client.close()
                isolated_state_and_fake_http_only.pop(id(client), None)

    return make


@pytest.fixture
def isolated_app():
    app = FastAPI()
    app.include_router(bot_api.router)
    return app


def test_client_defaults_and_exact_model():
    signature = inspect.signature(OllamaClient)
    assert signature.parameters["base_url"].default == "http://localhost:11434"
    assert signature.parameters["model"].default == QWEN_MODEL
    assert signature.parameters["timeout_s"].default == 110.0
    for name in ("http", "clock_ms", "timeout_s"):
        assert signature.parameters[name].kind == inspect.Parameter.KEYWORD_ONLY
    assert OllamaClient().model == QWEN_MODEL


@pytest.mark.parametrize("model", ["llama3", "qwen3.6:35b", QWEN_MODEL + ":latest", "", None])
def test_other_model_is_rejected_at_construction(model):
    with pytest.raises(ValueError):
        OllamaClient(model=model)


def test_snapshot_exact_ttl_and_fingerprint():
    data = snapshot_dict()
    context = ScenarioSnapshot.model_validate(data)
    context.assert_current(context.captured_at_ms)
    context.assert_current(context.valid_until_ms)
    assert context.valid_until_ms - context.as_of_close_time_ms == 90_000
    reordered = dict(reversed(list(data.items())))
    reordered["evidence"] = dict(reversed(list(data["evidence"].items())))
    assert context.fingerprint() == ScenarioSnapshot.model_validate(reordered).fingerprint()
    data["evidence"]["close"]["value"] += 1.0
    assert context.fingerprint() != ScenarioSnapshot.model_validate(data).fingerprint()


@pytest.mark.parametrize("now", [CLOSE_MS, CLOSE_MS + 90_001])
def test_snapshot_future_or_expired_clock(now):
    with pytest.raises(ScenarioError) as caught:
        snapshot().assert_current(now)
    assert_error(caught.value, "snapshot_expired")


@pytest.mark.parametrize("field,value", [
    ("connected", False), ("history_loaded", False), ("complete", False),
    ("recovering", True), ("stale", True), ("pending_gaps", 1),
])
def test_incomplete_market_is_not_analyzable(field, value):
    data = snapshot_dict()
    data["market_state"][field] = value
    context = ScenarioSnapshot.model_validate(data)
    with pytest.raises(ScenarioError) as caught:
        context.assert_current(CLOSE_MS + 1)
    assert_error(caught.value, "market_incomplete")


SNAPSHOT_BAD_FIELDS = [
    (("as_of_close_time_ms",), CLOSE_MS + 1),
    (("valid_until_ms",), CLOSE_MS + 90_001),
    (("captured_at_ms",), CLOSE_MS - 1),
    (("captured_at_ms",), CLOSE_MS + 90_001),
    (("timeframe",), "15m"), (("environment",), "live"),
    (("symbol",), "btcusdt"), (("data_source",), "Binance"),
    (("captured_at_ms",), str(CLOSE_MS + 1)),
    (("market_state", "connected"), "true"),
    (("market_state", "pending_gaps"), -1),
    (("market_state", "extra"), True),
    (("evidence", "close", "observed_at_ms"), CLOSE_MS + 60_000),
    (("evidence", "close", "observed_at_ms"), CLOSE_MS - 1),
    (("evidence", "close", "unit"), "BTC"),
    (("evidence", "hist", "unit"), "BTC"),
    (("evidence", "volume", "unit"), "USDT"),
    (("evidence", "close", "value"), float("nan")),
    (("evidence", "close", "value"), float("inf")),
    (("evidence", "hist", "value"), -float("inf")),
    (("evidence", "close", "value"), "65100"),
    (("evidence", "close", "value"), True),
    (("evidence", "close", "value"), 0.0),
    (("evidence", "volume", "value"), -1.0),
    (("evidence", "close", "forming"), True),
    (("evidence",), {}), (("evidence", "invented-ref"), {}),
    (("missing_data",), ["order_book", "order_book"]),
    (("extra",), "free context"),
]


def set_path(data, path, value):
    target = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


@pytest.mark.parametrize("path,value", SNAPSHOT_BAD_FIELDS)
def test_snapshot_strict_validation(path, value):
    data = snapshot_dict()
    set_path(data, path, value)
    with pytest.raises(ValidationError):
        ScenarioSnapshot.model_validate(data)


def test_request_requires_only_typed_snapshot():
    assert ScenarioAnalysisRequest.model_validate({"snapshot": snapshot_dict()}).snapshot == snapshot()
    for data in (
        snapshot_dict(), {"market_data": snapshot_dict()},
        {"snapshot": snapshot_dict(), "market_data": {}},
        {"snapshot": snapshot_dict(), "symbol": "BTC/USDT"},
    ):
        with pytest.raises(ValidationError):
            ScenarioAnalysisRequest.model_validate(data)


def test_three_valid_scenarios_and_exact_inverse_contract():
    context = snapshot()
    result = ScenarioOutput.model_validate(output_dict(context))
    result.validate_for(context, context.valid_until_ms)
    assert set(result.scenarios.model_dump()) == {"continuation", "failure", "sideways"}
    for scenario in result.scenarios.model_dump().values():
        assert scenario["condition_logic"] == "all"
        assert scenario["invalidation_logic"] == "any"
        assert scenario["invalidations"] == complement(scenario["conditions"])


OUTPUT_BAD_FIELDS = [
    (("scenarios", "failure", "evidence", 0, "observed_value"), 999.0, "invalid_evidence"),
    (("scenarios", "failure", "evidence", 0, "evidence_id"), "invented", "invalid_evidence"),
    (("scenarios", "failure", "observations", 0, "operator"), "gt", "false_observation"),
    (("scenarios", "continuation", "observations", 0, "operator"), "lt", "false_observation"),
    (("scenarios", "continuation", "conditions", 0, "threshold_ref"), "invented", "invalid_evidence"),
    (("scenarios", "sideways", "invalidations", 0, "operator"), "gte", "invalid_invalidation"),
    (("scenarios", "sideways", "invalidations"), [condition("close", "lt", "ema")], "invalid_invalidation"),
    (("missing_data",), [], "invalid_evidence"),
    (("missing_data",), ["order_book", "invented"], "invalid_evidence"),
    (("missing_data",), ["order_book", "order_book"], "invalid_evidence"),
    (("snapshot_id",), "another-snapshot", "snapshot_mismatch"),
    (("symbol",), "ETH/USDT", "snapshot_mismatch"),
    (("as_of_close_time_ms",), CLOSE_MS - 60_000, "snapshot_mismatch"),
    (("valid_until_ms",), CLOSE_MS + 80_000, "snapshot_mismatch"),
    (("data_source",), "unverified", "snapshot_mismatch"),
]


@pytest.mark.parametrize("path,value,code", OUTPUT_BAD_FIELDS)
def test_output_semantic_validation(path, value, code):
    data = output_dict()
    set_path(data, path, value)
    output = ScenarioOutput.model_validate(data)
    with pytest.raises(ScenarioError) as caught:
        output.validate_for(snapshot(), CLOSE_MS + 1)
    assert_error(caught.value, code)


CITATION_CASES = [
    ("missing_citation", "invalid_evidence"),
    ("duplicate_citation", "invalid_evidence"),
    ("mixed_dimensions", "invalid_evidence"),
    ("duplicate_condition", "invalid_invalidation"),
    ("duplicate_invalidation", "invalid_invalidation"),
    ("wrong_inverse_ref", "invalid_invalidation"),
    ("impossible_range", "invalid_condition"),
    ("impossible_equality", "invalid_condition"),
    ("excluded_equality", "invalid_condition"),
]


def invalid_citation_output(case):
    data = output_dict()
    item = data["scenarios"]["sideways"]
    if case == "missing_citation":
        item["evidence"] = item["evidence"][1:]
    elif case == "duplicate_citation":
        item["evidence"].append(deepcopy(item["evidence"][0]))
    elif case == "mixed_dimensions":
        item["evidence"].append({"evidence_id": "volume", "observed_value": 10.0})
        item["conditions"][0]["threshold_ref"] = "volume"
    elif case == "duplicate_condition":
        item["conditions"].append(deepcopy(item["conditions"][0]))
    elif case == "duplicate_invalidation":
        item["invalidations"].append(deepcopy(item["invalidations"][0]))
    elif case == "wrong_inverse_ref":
        item["invalidations"][0]["metric_ref"] = "ema"
    else:
        operators = {
            "impossible_range": [("gt", "resistance"), ("lt", "ema")],
            "impossible_equality": [("eq", "ema"), ("eq", "resistance")],
            "excluded_equality": [("eq", "ema"), ("ne", "ema")],
        }[case]
        item["conditions"] = [condition("close", op, ref) for op, ref in operators]
        item["invalidations"] = complement(item["conditions"])
    return data


@pytest.mark.parametrize("case,code", CITATION_CASES)
def test_citations_complements_and_feasibility(case, code):
    output = ScenarioOutput.model_validate(invalid_citation_output(case))
    with pytest.raises(ScenarioError) as caught:
        output.validate_for(snapshot(), CLOSE_MS + 1)
    assert_error(caught.value, code)


@pytest.mark.parametrize("case,code", CITATION_CASES)
def test_client_checks_citations_complements_and_impossible_conditions(client_factory, case, code):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.reply["message"]["content"] = json.dumps(invalid_citation_output(case))
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, code)
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("operator,inverse", list(INVERSE.items()))
def test_all_six_comparison_inverses_are_required(operator, inverse):
    data = output_dict()
    item = data["scenarios"]["sideways"]
    item["conditions"] = [condition("close", operator, "ema")]
    item["invalidations"] = [condition("close", inverse, "ema")]
    ScenarioOutput.model_validate(data).validate_for(snapshot(), CLOSE_MS + 1)
    item["invalidations"][0]["operator"] = operator
    with pytest.raises(ScenarioError) as caught:
        ScenarioOutput.model_validate(data).validate_for(snapshot(), CLOSE_MS + 1)
    assert_error(caught.value, "invalid_invalidation")


STRUCTURE_BAD_FIELDS = [
    (("scenarios", "continuation", "condition_logic"), "any"),
    (("scenarios", "continuation", "invalidation_logic"), "all"),
    (("scenarios", "continuation", "conditions", 0, "applies_to"), "current_snapshot"),
    (("scenarios", "continuation", "conditions", 0, "threshold"), 70_000.0),
    (("timeframe",), "15m"), (("executable",), True),
    (("executable",), 0), (("result_type",), "strategy_draft"),
    (("scenarios", "continuation", "evidence", 0, "observed_value"), "65100"),
    (("scenarios", "continuation", "evidence", 0, "observed_value"), float("nan")),
]
STRUCTURE_BAD_FIELDS += [
    (path + (field,), "modelo no autorizado")
    for path in ((), ("scenarios", "continuation"))
    for field in ("summary", "interpretation", "size", "risk", "order", "strategy")
]


@pytest.mark.parametrize("path,value", STRUCTURE_BAD_FIELDS)
def test_no_free_prose_operational_fields_or_loose_structure(path, value):
    data = output_dict()
    set_path(data, path, value)
    with pytest.raises(ValidationError):
        ScenarioOutput.model_validate(data)


@pytest.mark.parametrize("state_field,value,code", [
    (None, CLOSE_MS, "snapshot_expired"),
    (None, CLOSE_MS + 90_001, "snapshot_expired"),
    ("complete", False, "market_incomplete"),
    ("stale", True, "market_incomplete"),
    ("recovering", True, "market_incomplete"),
    ("pending_gaps", 1, "market_incomplete"),
])
def test_client_rejects_unusable_snapshot_before_http(client_factory, state_field, value, code):
    async def exercise():
        data = snapshot_dict()
        clock = Clock()
        if state_field is None:
            clock.now = value
        else:
            data["market_state"][state_field] = value
        async with client_factory(clock=clock) as (client, fake, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(ScenarioSnapshot.model_validate(data))
            assert_error(caught.value, code)
            assert fake.calls == []

    asyncio.run(exercise())


def schema_resolve(schema, node):
    while "$ref" in node:
        target = schema
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


def test_success_is_pure_validated_advisory_with_server_summaries(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        async with client_factory() as (client, fake, clock):
            result = await client.analyze_snapshot(snapshot())
            assert isinstance(result, ScenarioAnalysisResult)
            assert result.analysis.model_dump(mode="json") == output_dict()
            result.analysis.validate_for(snapshot(), clock())
            assert result.available is True
            assert result.result_type == "scenario_advisory"
            assert result.executable is False and result.execution_available is False
            assert result.requested_model == result.model_used == QWEN_MODEL
            assert result.data_source == "TEST_ONLY"
            assert result.duration_ms >= 0
            assert result.total_duration_ms == 2000.0
            assert result.load_duration_ms == 100.0
            assert result.prompt_tokens == 100 and result.generated_tokens == 50
            assert result.generation_tokens_per_second == 50.0
            assert result.cache_hit is False and result.deduplicated is False
            assert set(result.summaries) == {"continuation", "failure", "sideways"}
            assert all(isinstance(text, str) and text.strip() for text in result.summaries.values())
            assert "summaries" not in json.loads(fake.reply["message"]["content"])
            assert fake.calls == [("GET", "/api/tags"), ("POST", "/api/chat")]
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_payload_exact_model_options_schema_constants_and_refs(client_factory):
    async def exercise():
        context = snapshot()
        async with client_factory() as (client, fake, _):
            await client.analyze_snapshot(context)
            payload = fake.payloads[0]
            assert payload["model"] == QWEN_MODEL
            assert payload["stream"] is False and payload["think"] is False
            assert payload["options"]["num_ctx"] == 8192
            assert payload["options"]["num_predict"] == 2048
            assert payload["options"]["temperature"] == 0.2
            schema = payload["format"]
            assert isinstance(schema, dict) and schema["type"] == "object"
            properties = schema["properties"]
            for name in (
                "snapshot_id", "symbol", "timeframe", "as_of_close_time_ms",
                "valid_until_ms", "data_source",
            ):
                assert properties[name]["const"] == getattr(context, name)
            scenarios = schema_resolve(schema, properties["scenarios"])
            for name in ("continuation", "failure", "sideways"):
                item = schema_resolve(schema, scenarios["properties"][name])
                assert item["additionalProperties"] is False
                for collection, fields in (
                    ("evidence", ("evidence_id",)),
                    ("observations", ("left_ref", "right_ref")),
                    ("conditions", ("metric_ref", "threshold_ref")),
                    ("invalidations", ("metric_ref", "threshold_ref")),
                ):
                    entry = schema_resolve(schema, item["properties"][collection]["items"])
                    for field in fields:
                        ref_schema = schema_resolve(schema, entry["properties"][field])
                        assert set(ref_schema["enum"]) == set(context.evidence)
            prompt = "\n".join(message["content"] for message in payload["messages"])
            assert context.snapshot_id in prompt and "184" in prompt and "203" in prompt

    asyncio.run(exercise())


@pytest.mark.parametrize("path,value,code", OUTPUT_BAD_FIELDS)
def test_client_rejects_semantically_false_model_output(client_factory, path, value, code):
    async def exercise():
        fake = FakeOllamaHTTP()
        data = output_dict()
        set_path(data, path, value)
        fake.reply["message"]["content"] = json.dumps(data)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, code)
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("path,value", STRUCTURE_BAD_FIELDS)
def test_client_rejects_untyped_model_output(client_factory, path, value):
    async def exercise():
        fake = FakeOllamaHTTP()
        data = output_dict()
        set_path(data, path, value)
        fake.reply["message"]["content"] = json.dumps(data)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            expected = "invalid_json" if isinstance(value, float) and not math.isfinite(value) else "invalid_structure"
            assert_error(caught.value, expected)

    asyncio.run(exercise())


@pytest.mark.parametrize("content", [
    "not JSON", "```json\n{}\n```", '{"snapshot_id":"first","snapshot_id":"second"}',
    '{"scenarios":{"x":1,"x":2}}', '{"value":NaN}', '{"value":Infinity}',
    '{"value":-Infinity}', '{"snapshot_id":',
])
def test_invalid_json_duplicate_keys_nonfinite_and_truncation(client_factory, content):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.reply["message"]["content"] = content
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_json")
            assert fake.posts == 1  # sin fallback/reintento silencioso

    asyncio.run(exercise())


RUNTIME_BAD_FIELDS = [
    (("model",), "llama3", "model_mismatch"),
    (("model",), QWEN_MODEL + ":latest", "model_mismatch"),
    (("done",), False, "incomplete_generation"),
    (("done_reason",), "length", "incomplete_generation"),
    (("message", "role"), "user", "invalid_structure"),
    (("message", "thinking"), "razonamiento privado", "unexpected_reasoning"),
    (("message", "content"), "<think>razonamiento</think>{}", "unexpected_reasoning"),
]


@pytest.mark.parametrize("path,value,code", RUNTIME_BAD_FIELDS)
def test_runtime_envelope_exact_model_completion_role_and_no_thinking(client_factory, path, value, code):
    async def exercise():
        fake = FakeOllamaHTTP()
        set_path(fake.reply, path, value)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, code)
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("missing", ["model", "created_at", "message", "done", "done_reason"])
def test_runtime_required_fields(client_factory, missing):
    async def exercise():
        fake = FakeOllamaHTTP()
        del fake.reply[missing]
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_structure")

    asyncio.run(exercise())


@pytest.mark.parametrize("case", ["outer_duplicate", "nested_duplicate", "nonfinite", "overflow"])
def test_duplicate_keys_and_nonfinite_inside_otherwise_valid_json(client_factory, case):
    async def exercise():
        fake = FakeOllamaHTTP()
        if case == "outer_duplicate":
            body = json.dumps(fake.reply)
            fake.raw_reply = (body[:-1] + ',"model":' + json.dumps(QWEN_MODEL) + '}').encode()
        else:
            body = fake.reply["message"]["content"]
            if case == "nested_duplicate":
                body = body.replace('"observed_value": 65100.0', '"observed_value": 65100.0,"observed_value": 65100.0', 1)
            else:
                replacement = "NaN" if case == "nonfinite" else "1e309"
                body = body.replace('"observed_value": 65100.0', '"observed_value": ' + replacement, 1)
            assert body != fake.reply["message"]["content"]
            fake.reply["message"]["content"] = body
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_json")
            assert fake.posts == 1

    asyncio.run(exercise())


def test_direct_chat_returns_required_runtime_metrics_and_exact_model(client_factory):
    async def exercise():
        async with client_factory() as (client, fake, _):
            response = await client.chat([OllamaMessage(role="user", content="test transport")])
            assert response.model == QWEN_MODEL
            assert response.created_at
            assert response.message.role == "assistant"
            assert response.done is True and response.done_reason == "stop"
            for name in ("total_duration", "load_duration", "prompt_eval_count", "eval_count", "eval_duration"):
                assert getattr(response, name) == fake.reply[name]
            assert fake.calls == [("GET", "/api/tags"), ("POST", "/api/chat")]
            assert fake.payloads[0]["model"] == QWEN_MODEL
            assert fake.payloads[0]["think"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("path,value,code", RUNTIME_BAD_FIELDS)
def test_direct_chat_centralizes_runtime_validation(client_factory, path, value, code):
    async def exercise():
        fake = FakeOllamaHTTP()
        set_path(fake.reply, path, value)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.chat([OllamaMessage(role="user", content="test transport")])
            assert_error(caught.value, code)
            assert fake.posts == 1

    asyncio.run(exercise())


def test_direct_chat_missing_exact_model_never_posts(client_factory):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.tags = [{"name": "llama3"}]
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.chat([OllamaMessage(role="user", content="test transport")])
            assert_error(caught.value, "model_missing")
            assert fake.posts == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("tags", [
    [], [{"name": "llama3"}], [{"name": QWEN_MODEL + ":latest"}],
    [{"name": "qwen3.6:35b-a3b-q4_K_M"}], [{"model": QWEN_MODEL}],
])
def test_model_absent_means_no_post_and_health_false(client_factory, tags):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.tags = tags
        async with client_factory(fake) as (client, _, _):
            assert await client.health_check() is False
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "model_missing")
            assert fake.posts == 0

    asyncio.run(exercise())


def test_health_requires_exact_name_not_first_tag(client_factory):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.tags = [{"name": "llama3"}, {"name": QWEN_MODEL}]
        async with client_factory(fake) as (client, _, _):
            assert await client.health_check() is True
            assert fake.posts == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("stage,kind,code", [
    ("tags", "status", "unavailable"), ("chat", "status", "unavailable"),
    ("tags", "connection", "unavailable"), ("chat", "connection", "unavailable"),
    ("tags", "timeout", "timeout"), ("chat", "timeout", "timeout"),
    ("chat", "invalid_json", "invalid_json"),
])
def test_transport_errors_are_public_codes_without_fallback(client_factory, stage, kind, code):
    async def exercise():
        fake = FakeOllamaHTTP()
        prefix = "tag" if stage == "tags" else "chat"
        if kind == "status":
            setattr(fake, prefix + "_status", 503)
        elif kind in ("connection", "timeout"):
            error_type = httpx.ConnectError if kind == "connection" else httpx.ReadTimeout
            setattr(fake, prefix + "_error", error_type("secret-private-transport"))
        else:
            fake.raw_reply = b'{"model":'
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, code)
            assert "secret-private-transport" not in str(caught.value)
            assert fake.posts == (0 if stage == "tags" else 1)

    asyncio.run(exercise())


def test_real_deadline_timeout_without_clock_jump(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake, timeout_s=0.02) as (client, _, clock):
            initial_clock = clock()
            with pytest.raises(ScenarioError) as caught:
                await asyncio.wait_for(client.analyze_snapshot(snapshot()), 0.5)
            assert_error(caught.value, "timeout")
            assert clock() == initial_clock
            await eventually(lambda: fake.active == 0)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


@pytest.mark.parametrize("wrapper", ["analyze_market", "generate_strategy"])
def test_legacy_wrappers_use_typed_context(client_factory, wrapper):
    async def exercise():
        async with client_factory() as (client, fake, _):
            argument = snapshot_dict()
            if wrapper == "generate_strategy":
                argument = json.dumps(argument)
            result = await getattr(client, wrapper)(argument)
            assert isinstance(result, ScenarioAnalysisResult)
            assert result.analysis.model_dump(mode="json") == output_dict()
            assert result.result_type == "scenario_advisory"
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("wrapper,argument", [
    ("analyze_market", {"price": 65_100.0}),
    ("analyze_market", {"snapshot": snapshot_dict()}),
    ("generate_strategy", "Comprar BTC; riesgo 2%"),
    ("generate_strategy", '{"market_data":{"price":65100}}'),
    ("generate_strategy", '{"snapshot_id":"a","snapshot_id":"b"}'),
])
def test_legacy_wrappers_reject_freestyle_before_http(client_factory, wrapper, argument):
    async def exercise():
        async with client_factory() as (client, fake, _):
            with pytest.raises(ScenarioError) as caught:
                await getattr(client, wrapper)(argument)
            assert_error(caught.value, "invalid_snapshot")
            assert fake.calls == []

    asyncio.run(exercise())


def test_five_callers_deduplicate_and_live_success_cache(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            tasks = [asyncio.create_task(client.analyze_snapshot(snapshot())) for _ in range(5)]
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                await asyncio.sleep(0.02)
                assert fake.posts == fake.gets == 1
                fake.release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
                assert sum(result.deduplicated for result in results) == 4
                assert all(result.analysis == results[0].analysis for result in results)
                cached = await client.analyze_snapshot(snapshot())
                assert cached.cache_hit is True
                assert cached.analysis == results[0].analysis
                assert fake.posts == fake.gets == 1
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


@pytest.mark.parametrize("in_flight", [False, True])
def test_same_live_id_with_different_fingerprint_is_conflict(client_factory, in_flight):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.block = in_flight
        altered = snapshot_dict()
        altered["evidence"]["close"]["value"] += 1.0
        async with client_factory(fake) as (client, _, _):
            first = asyncio.create_task(client.analyze_snapshot(snapshot()))
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                if not in_flight:
                    await first
                with pytest.raises(ScenarioError) as caught:
                    await client.analyze_snapshot(ScenarioSnapshot.model_validate(altered))
                assert_error(caught.value, "snapshot_id_conflict")
                assert fake.posts == fake.gets == 1
            finally:
                fake.release.set()
                await asyncio.gather(first, return_exceptions=True)

    asyncio.run(exercise())


def test_cache_checks_expiry_including_exact_boundary(client_factory):
    async def exercise():
        context = snapshot()
        async with client_factory() as (client, fake, clock):
            await client.analyze_snapshot(context)
            clock.now = context.valid_until_ms
            assert (await client.analyze_snapshot(context)).cache_hit is True
            clock.now += 1
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(context)
            assert_error(caught.value, "snapshot_expired")
            assert fake.posts == fake.gets == 1
            replacement = snapshot(close_ms=CLOSE_MS + 120_000)
            clock.now = replacement.captured_at_ms
            fake.reply = runtime_response(replacement)
            assert (await client.analyze_snapshot(replacement)).cache_hit is False
            assert fake.posts == fake.gets == 2

    asyncio.run(exercise())


def test_expiry_rechecked_after_blocked_post(client_factory):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, clock):
            task = asyncio.create_task(client.analyze_snapshot(snapshot()))
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                clock.now = snapshot().valid_until_ms + 1
                fake.release.set()
                with pytest.raises(ScenarioError) as caught:
                    await asyncio.wait_for(task, 1)
                assert_error(caught.value, "snapshot_expired")
                assert fake.posts == 1
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_failed_results_are_not_cached(client_factory):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.reply["message"]["content"] = "invalid"
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_json")
            fake.reply = runtime_response()
            assert (await client.analyze_snapshot(snapshot())).cache_hit is False
            assert fake.posts == fake.gets == 2

    asyncio.run(exercise())


def test_cache_is_bounded_to_64_live_results(client_factory):
    async def exercise():
        async with client_factory() as (client, fake, _):
            for index in range(65):
                context = snapshot(f"cache-{index}")
                fake.reply = runtime_response(context)
                await client.analyze_snapshot(context)
            assert fake.posts == 65
            # No impone FIFO/LRU: cualquier política bounded debe haber expulsado
            # al menos un resultado entre los 65 snapshots todavía vigentes.
            cache_hits = 0
            for index in range(65):
                context = snapshot(f"cache-{index}")
                fake.reply = runtime_response(context)
                cache_hits += (await client.analyze_snapshot(context)).cache_hit
            assert cache_hits <= 64
            assert fake.posts >= 66

    asyncio.run(exercise())


@pytest.mark.parametrize("second_call", ["analyze_snapshot", "chat"])
def test_two_instances_share_global_gate_including_direct_chat(client_factory, second_call):
    async def exercise():
        baseline = asyncio.all_tasks()
        first_http = FakeOllamaHTTP()
        first_http.block = True
        second_context = snapshot("snapshot-second")
        second_http = FakeOllamaHTTP(second_context)
        async with client_factory(first_http) as (first, _, _):
            async with client_factory(second_http) as (second, _, _):
                one = asyncio.create_task(first.analyze_snapshot(snapshot()))
                two = None
                try:
                    await asyncio.wait_for(first_http.entered.wait(), 1)
                    if second_call == "chat":
                        call = second.chat([OllamaMessage(role="user", content="test typed transport")])
                    else:
                        call = second.analyze_snapshot(second_context)
                    two = asyncio.create_task(call)
                    await asyncio.sleep(0.03)
                    assert second_http.posts == 0
                    assert not two.done()
                    first_http.release.set()
                    await asyncio.wait_for(asyncio.gather(one, two), 1)
                    assert first_http.max_active == second_http.max_active == 1
                    assert second_http.calls == [("GET", "/api/tags"), ("POST", "/api/chat")]
                finally:
                    first_http.release.set()
                    for task in (one, two):
                        if task is not None:
                            task.cancel()
                    await asyncio.gather(*(task for task in (one, two) if task), return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_process_gate_across_threads_and_event_loops(client_factory):
    lock = threading.Lock()
    first_entered = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    counts = {"active": 0, "max_active": 0, "posts": 0}

    async def worker(index):
        context = snapshot(f"thread-{index}")
        fake = FakeOllamaHTTP(context)

        async def handle(request):
            if request.method == "GET":
                return httpx.Response(200, json={"models": [{"name": QWEN_MODEL}]})
            with lock:
                counts["active"] += 1
                counts["posts"] += 1
                counts["max_active"] = max(counts["max_active"], counts["active"])
            try:
                if index == 0:
                    first_entered.set()
                    await eventually(release.is_set, timeout=2)
                await asyncio.sleep(0.02)
                return httpx.Response(200, json=runtime_response(context))
            finally:
                with lock:
                    counts["active"] -= 1

        fake.handle = handle
        async with client_factory(fake) as (client, _, _):
            if index == 1:
                second_started.set()
            return await client.analyze_snapshot(context)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: asyncio.run(worker(0)))
        second = None
        try:
            if not first_entered.wait(1):
                first.result(timeout=1)  # propagar el fallo real del worker
                pytest.fail("El primer POST simulado no empezó")
            second = pool.submit(lambda: asyncio.run(worker(1)))
            assert second_started.wait(1)
            # Un bloqueo threading.Lock.acquire directo bloquearía ese event loop;
            # el test async anterior verifica que también progresa el loop.
            time.sleep(0.03)
            with lock:
                assert counts["posts"] == 1 and counts["max_active"] == 1
            release.set()
            assert first.result(timeout=2).available is True
            assert second.result(timeout=2).available is True
            assert counts == {"active": 0, "max_active": 1, "posts": 2}
        finally:
            release.set()


@pytest.mark.parametrize("reason", ["timeout", "snapshot_expired"])
def test_waiting_global_gate_counts_toward_deadline_and_ttl(client_factory, reason):
    async def exercise():
        first_http = FakeOllamaHTTP()
        first_http.block = True
        second_http = FakeOllamaHTTP(snapshot("queued"))
        async with client_factory(first_http) as (first, _, _):
            timeout = 0.02 if reason == "timeout" else 110.0
            async with client_factory(second_http, timeout_s=timeout) as (second, _, clock):
                one = asyncio.create_task(first.analyze_snapshot(snapshot()))
                two = None
                try:
                    await asyncio.wait_for(first_http.entered.wait(), 1)
                    two = asyncio.create_task(second.analyze_snapshot(snapshot("queued")))
                    await asyncio.sleep(0.01)
                    if reason == "snapshot_expired":
                        clock.now = snapshot().valid_until_ms + 1
                        first_http.release.set()
                    with pytest.raises(ScenarioError) as caught:
                        await asyncio.wait_for(two, 0.5)
                    assert_error(caught.value, reason)
                    assert second_http.posts == 0
                finally:
                    first_http.release.set()
                    await asyncio.gather(*(task for task in (one, two) if task), return_exceptions=True)

    asyncio.run(exercise())


def test_at_most_64_pending_jobs_admission_busy_and_cleanup(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP(snapshot("job-0"))
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            tasks = [asyncio.create_task(client.analyze_snapshot(snapshot("job-0")))]
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                tasks.extend(
                    asyncio.create_task(client.analyze_snapshot(snapshot(f"job-{index}")))
                    for index in range(1, 64)
                )
                await asyncio.sleep(0.05)
                assert not any(task.done() for task in tasks)
                shared = asyncio.create_task(client.analyze_snapshot(snapshot("job-0")))
                tasks.append(shared)
                await asyncio.sleep(0.01)
                assert not shared.done()  # dedup no consume otro slot
                with pytest.raises(ScenarioError) as caught:
                    await asyncio.wait_for(client.analyze_snapshot(snapshot("job-64")), 0.5)
                assert_error(caught.value, "busy")
                assert fake.posts == 1
            finally:
                await client.close()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_cancel_one_waiter_preserves_remaining_shared_caller(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            first = asyncio.create_task(client.analyze_snapshot(snapshot()))
            second = None
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                second = asyncio.create_task(client.analyze_snapshot(snapshot()))
                await asyncio.sleep(0.02)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert fake.active == 1 and not fake.cancelled.is_set()
                fake.release.set()
                result = await asyncio.wait_for(second, 1)
                assert result.available is True and result.deduplicated is True
                assert fake.posts == fake.gets == 1
            finally:
                for task in (first, second):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_cancel_only_waiter_does_not_leave_background_job(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            caller = asyncio.create_task(client.analyze_snapshot(snapshot()))
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
                await asyncio.wait_for(fake.cancelled.wait(), 1)
                assert fake.active == 0
                await no_new_tasks(baseline)
                fake.block = False
                result = await client.analyze_snapshot(snapshot())
                assert result.cache_hit is False and fake.posts == 2
            finally:
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_close_cancels_active_and_queued_jobs_and_rejects_reuse(client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            first = asyncio.create_task(client.analyze_snapshot(snapshot()))
            second = None
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                second = asyncio.create_task(client.analyze_snapshot(snapshot("close-queued")))
                await asyncio.sleep(0.02)
                await asyncio.wait_for(client.close(), 1)
                outcomes = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
                assert all(isinstance(item, (asyncio.CancelledError, ScenarioError)) for item in outcomes)
                for item in outcomes:
                    if isinstance(item, ScenarioError):
                        assert_error(item, "client_closed")
                assert fake.active == 0 and fake.cancelled.is_set()
                assert fake.posts == 1
                with pytest.raises(ScenarioError) as caught:
                    await client.analyze_snapshot(snapshot())
                assert_error(caught.value, "client_closed")
                await no_new_tasks(baseline)
            finally:
                for task in (first, second):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(*(task for task in (first, second) if task), return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("source,expected", [
    ("TEST_ONLY", "TEST_ONLY"), ("market_engine", "unverified"), ("unverified", "unverified"),
])
def test_client_does_not_certify_client_submitted_market_data(client_factory, source, expected):
    async def exercise():
        data = snapshot_dict()
        data["data_source"] = source
        context = ScenarioSnapshot.model_validate(data)
        async with client_factory(FakeOllamaHTTP(context)) as (client, _, _):
            result = await client.analyze_snapshot(context)
            assert result.data_source == expected
            assert result.analysis.data_source == source

    asyncio.run(exercise())


class FakeScenarioClient:
    """Doble de rutas; nunca transforma un snapshot en texto o una estrategia."""

    model = QWEN_MODEL

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def analyze_snapshot(self, context):
        assert isinstance(context, ScenarioSnapshot)
        self.calls.append(context)
        if self.error:
            raise self.error
        return ScenarioAnalysisResult(
            analysis=ScenarioOutput.model_validate(output_dict(context)),
            model_used=QWEN_MODEL, duration_ms=7,
            data_source="TEST_ONLY" if context.data_source == "TEST_ONLY" else "unverified",
            summaries={name: f"Resumen del servidor: {name}" for name in (
                "continuation", "failure", "sideways",
            )},
        )

    async def health_check(self):
        return True

    async def analyze_market(self, *args):
        raise AssertionError("La ruta debe usar analyze_snapshot")

    async def generate_strategy(self, *args):
        raise AssertionError("La ruta deprecated no debe crear un draft operativo")


@pytest.mark.parametrize("path", AI_PATHS)
@pytest.mark.parametrize("engine_initialized", [False, True])
@pytest.mark.parametrize("code", [None, "model_missing", "false_observation"])
def test_api_only_lazy_client_changes_state(
    isolated_app, monkeypatch, path, engine_initialized, code,
):
    async def exercise():
        state = bot_api._bot_state
        strategy = state["strategy"]
        strategy.max_position_size = 0.5
        strategy.stop_loss_pct = 0.03
        strategy.take_profit_pct = 0.06
        strategy.rules[0].weight = 0.4
        engine = object() if engine_initialized else None
        state["engine"] = engine
        before = deepcopy({key: value for key, value in state.items() if key not in ("engine", "ollama_client")})
        identities = {key: value for key, value in state.items() if key != "ollama_client"}
        fake = FakeScenarioClient(ScenarioError(code, duration_ms=9) if code else None)
        factory = Mock(return_value=fake)
        forbidden_rules = Mock(side_effect=AssertionError("IA no debe construir/evaluar RulesEngine"))
        monkeypatch.setattr(bot_api.RulesEngine, "evaluate", forbidden_rules)
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        monkeypatch.setattr(bot_api, "RulesEngine", forbidden_rules)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
            response = await http.post(path, json={"snapshot": snapshot_dict()})
        assert response.status_code == (ERROR_STATUS[code] if code else 200)
        factory.assert_called_once_with(model=QWEN_MODEL)
        forbidden_rules.assert_not_called()
        assert state["ollama_client"] is fake
        assert fake.calls == [snapshot()]
        assert state.keys() == set(identities) | {"ollama_client"}
        assert all(state[key] is value for key, value in identities.items())
        assert {key: value for key, value in state.items() if key not in ("engine", "ollama_client")} == before
        if code:
            assert response.json()["detail"]["code"] == code
        else:
            body = response.json()
            assert body["analysis"] == output_dict()
            assert body["result_type"] == "scenario_advisory" and body["available"] is True
            assert body["executable"] is False and body["execution_available"] is False
            assert body["requested_model"] == body["model_used"] == QWEN_MODEL
            assert body["summaries"] == {name: f"Resumen del servidor: {name}" for name in (
                "continuation", "failure", "sideways",
            )}
            assert "BORRADOR" not in json.dumps(body) and "strategy_draft" not in json.dumps(body)

    asyncio.run(exercise())


@pytest.mark.parametrize("path", AI_PATHS)
@pytest.mark.parametrize("code,status", list(ERROR_STATUS.items()))
def test_api_structured_http_exception_for_every_public_error(isolated_app, path, code, status):
    async def exercise():
        effective = QWEN_MODEL if code != "model_missing" else None
        fake = FakeScenarioClient(ScenarioError(code, duration_ms=23, effective_model=effective))
        bot_api._bot_state["ollama_client"] = fake
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
            response = await http.post(path, json={"snapshot": snapshot_dict()})
        assert response.status_code == status
        assert response.json() == {"detail": {
            "code": code, "available": False,
            "requested_model": QWEN_MODEL, "effective_model": effective,
            "duration_ms": 23, "executable": False, "execution_available": False,
        }}
        assert fake.calls == [snapshot()]

    asyncio.run(exercise())


@pytest.mark.parametrize("path", AI_PATHS)
@pytest.mark.parametrize("body", [
    {"symbol": "BTC/USDT", "market_data": {"price": 65_100}},
    snapshot_dict(), {"snapshot": {"price": 65_100}},
    {"snapshot": snapshot_dict(), "market_data": {}},
    {"snapshot": snapshot_dict(), "strategy": "buy"},
])
def test_api_rejects_old_or_extra_request_without_creating_client(isolated_app, monkeypatch, path, body):
    async def exercise():
        factory = Mock(side_effect=AssertionError("No crear cliente para request inválida"))
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
            response = await http.post(path, json=body)
        assert response.status_code == 422
        factory.assert_not_called()
        assert bot_api._bot_state["ollama_client"] is None

    asyncio.run(exercise())


def test_strategy_alias_is_documented_as_deprecated(isolated_app):
    schema = isolated_app.openapi()
    assert schema["paths"]["/api/bot/ai/strategy"]["post"]["deprecated"] is True


def test_bot_status_remains_nonblocking_while_model_post_waits(isolated_app, client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = FakeOllamaHTTP()
        fake.block = True
        async with client_factory(fake) as (client, _, _):
            bot_api._bot_state["ollama_client"] = client
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
                analysis = asyncio.create_task(http.post(AI_PATHS[0], json={"snapshot": snapshot_dict()}))
                try:
                    await asyncio.wait_for(fake.entered.wait(), 1)
                    status = await asyncio.wait_for(http.get("/api/bot/status"), 0.2)
                    assert status.status_code == 200
                    assert status.json()["execution_available"] is False
                    assert status.json()["is_running"] is False
                    assert not analysis.done() and fake.active == 1
                    fake.release.set()
                    response = await asyncio.wait_for(analysis, 1)
                    assert response.status_code == 200
                    assert response.json()["executable"] is False
                finally:
                    analysis.cancel()
                    await asyncio.gather(analysis, return_exceptions=True)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


# Regresiones de auditoría: ownership, retiring jobs, límites de recepción y N01.


class SuspendedCancellationHTTP(FakeOllamaHTTP):
    """El transporte necesita un await para terminar de limpiar su cancelación."""

    def __init__(self):
        super().__init__()
        self.block = True
        self.cleanup_entered = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_finished = asyncio.Event()
        self.cancellation_count = 0

    async def handle(self, request):
        try:
            return await super().handle(request)
        except asyncio.CancelledError:
            self.cancellation_count += 1
            self.cleanup_entered.set()
            try:
                await self.cleanup_release.wait()
            except asyncio.CancelledError:
                self.cancellation_count += 1
                raise
            else:
                self.cleanup_finished.set()
            raise


class TrackedOwnedHTTP(httpx.AsyncClient):
    """HTTP falso cuyo aclose y su orden de limpieza son observables."""

    def __init__(self, fake):
        super().__init__(transport=httpx.MockTransport(fake.handle))
        self.fake = fake
        self.close_count = 0
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.suspend_close = False
        self.cleanup_finished_when_closed = []

    async def aclose(self):
        self.close_count += 1
        cleanup = getattr(self.fake, "cleanup_finished", None)
        self.cleanup_finished_when_closed.append(cleanup is None or cleanup.is_set())
        self.close_entered.set()
        if self.suspend_close:
            await self.close_release.wait()
        await super().aclose()
        self.close_finished.set()


@pytest.fixture
def owned_client_factory(isolated_state_and_fake_http_only):
    @asynccontextmanager
    async def make(fake=None):
        fake = fake or FakeOllamaHTTP()
        http = TrackedOwnedHTTP(fake)
        client = OllamaClient(http=http, model=QWEN_MODEL, clock_ms=Clock())
        # Ownership se activa expresamente sin crear ningún transporte real.
        client._owns_http = True
        isolated_state_and_fake_http_only[id(client)] = http
        try:
            yield client, fake, http
        finally:
            release = getattr(fake, "cleanup_release", None)
            if release is not None:
                release.set()
            fake.release.set()
            http.close_release.set()
            try:
                await asyncio.wait_for(client.close(), 1)
            finally:
                if not http.is_closed:
                    await http.aclose()
                isolated_state_and_fake_http_only.pop(id(client), None)

    return make


async def finish_test_callers(*tasks):
    """No redeliver cancel sobre un transporte que ya está limpiándose."""
    tasks = [task for task in tasks if task is not None]
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)


def test_close_waits_for_retiring_last_waiter_transport_cleanup(owned_client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        async with owned_client_factory(SuspendedCancellationHTTP()) as (client, fake, http):
            caller = asyncio.create_task(client.analyze_snapshot(snapshot()))
            closing = None
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                caller.cancel()
                await asyncio.wait_for(fake.cleanup_entered.wait(), 1)
                closing = asyncio.create_task(client.close())
                await asyncio.sleep(0.02)
                assert not closing.done(), "close perdió el job retirado durante cancelación"
                assert not caller.done(), "El último waiter debe esperar a limpiar su job"
                assert http.close_count == 0 and not http.is_closed
                assert fake.cancellation_count == 1
                fake.cleanup_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(caller, 1)
                await asyncio.wait_for(closing, 1)
                assert fake.cleanup_finished.is_set()
                assert fake.cancellation_count == 1
                assert http.cleanup_finished_when_closed == [True]
                assert http.close_count == 1 and http.is_closed
                await client.close()
                assert http.close_count == 1
                await no_new_tasks(baseline)
            finally:
                fake.cleanup_release.set()
                await finish_test_callers(caller, closing)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


@pytest.mark.parametrize("stage", ["transport_cleanup", "owned_http_close"])
def test_cancelled_close_continues_owned_cleanup_and_second_close_is_idempotent(owned_client_factory, stage):
    async def exercise():
        baseline = asyncio.all_tasks()
        fake = SuspendedCancellationHTTP() if stage == "transport_cleanup" else FakeOllamaHTTP()
        async with owned_client_factory(fake) as (client, _, http):
            caller = first_close = second_close = None
            try:
                if stage == "transport_cleanup":
                    caller = asyncio.create_task(client.analyze_snapshot(snapshot()))
                    await asyncio.wait_for(fake.entered.wait(), 1)
                else:
                    http.suspend_close = True
                    assert await client.health_check() is True
                first_close = asyncio.create_task(client.close())
                entered = fake.cleanup_entered if stage == "transport_cleanup" else http.close_entered
                await asyncio.wait_for(entered.wait(), 1)
                first_close.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first_close
                assert not http.close_finished.is_set()
                second_close = asyncio.create_task(client.close())
                await asyncio.sleep(0.02)
                assert not second_close.done(), "La segunda close debe seguir esperando la misma limpieza"
                if stage == "transport_cleanup":
                    assert fake.cancellation_count == 1 and http.close_count == 0
                    fake.cleanup_release.set()
                else:
                    assert http.close_count == 1
                    http.close_release.set()
                await asyncio.wait_for(second_close, 1)
                if caller is not None:
                    outcome = (await asyncio.gather(caller, return_exceptions=True))[0]
                    assert isinstance(outcome, (asyncio.CancelledError, ScenarioError))
                    assert fake.cleanup_finished.is_set() and fake.cancellation_count == 1
                assert http.close_count == 1 and http.is_closed
                assert http.close_finished.is_set() and http.cleanup_finished_when_closed == [True]
                await client.close()
                assert http.close_count == 1
                await no_new_tasks(baseline)
            finally:
                if isinstance(fake, SuspendedCancellationHTTP):
                    fake.cleanup_release.set()
                http.close_release.set()
                await finish_test_callers(caller, first_close, second_close)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_close_cancels_direct_chat_and_queued_chat_without_late_success(owned_client_factory):
    async def exercise():
        baseline = asyncio.all_tasks()
        async with owned_client_factory(SuspendedCancellationHTTP()) as (client, fake, http):
            messages = [OllamaMessage(role="user", content="transport-only regression")]
            active = asyncio.create_task(client.chat(messages))
            queued = closing = None
            try:
                await asyncio.wait_for(fake.entered.wait(), 1)
                queued = asyncio.create_task(client.chat(messages))
                await asyncio.sleep(0.02)
                closing = asyncio.create_task(client.close())
                await asyncio.wait_for(fake.cleanup_entered.wait(), 1)
                await asyncio.sleep(0.02)
                assert not closing.done() and http.close_count == 0
                assert fake.posts == 1 and fake.cancellation_count == 1
                with pytest.raises(ScenarioError) as caught:
                    await client.chat(messages)
                assert_error(caught.value, "client_closed")
                fake.cleanup_release.set()
                await asyncio.wait_for(closing, 1)
                # Un servidor tardío no puede convertir un chat cerrado en éxito.
                fake.release.set()
                outcomes = await asyncio.wait_for(asyncio.gather(active, queued, return_exceptions=True), 1)
                for outcome in outcomes:
                    assert isinstance(outcome, (asyncio.CancelledError, ScenarioError))
                    if isinstance(outcome, ScenarioError):
                        assert_error(outcome, "client_closed")
                assert fake.cancellation_count == 1 and fake.cleanup_finished.is_set()
                assert fake.posts == 1 and http.cleanup_finished_when_closed == [True]
                await no_new_tasks(baseline)
            finally:
                fake.cleanup_release.set()
                await finish_test_callers(active, queued, closing)
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def scenario_log_fields(caplog):
    records = [record for record in caplog.records
               if record.name == "src.ai.ollama_client" and record.getMessage().startswith("qwen_scenarios ")]
    assert len(records) == 1
    fields = dict(token.split("=", 1) for token in records[0].getMessage().split()[1:])
    assert fields["snapshot"] == snapshot().snapshot_id
    assert fields["requested_model"] == QWEN_MODEL
    assert int(fields["duration_ms"]) >= 0
    return fields


@pytest.mark.parametrize("outcome", ["timeout", "model_missing"])
def test_logging_failure_outcome_duration_and_unknown_actual_model(client_factory, caplog, outcome):
    caplog.set_level("INFO", logger="src.ai.ollama_client")

    async def exercise():
        fake = FakeOllamaHTTP()
        fake.block = outcome == "timeout"
        if outcome == "model_missing":
            fake.tags = []
        async with client_factory(fake, timeout_s=0.02) as (client, _, clock):
            initial_clock = clock()
            with pytest.raises(ScenarioError) as caught:
                await asyncio.wait_for(client.analyze_snapshot(snapshot()), 1)
            assert_error(caught.value, outcome)
            assert caught.value.effective_model is None
            assert clock() == initial_clock
            fields = scenario_log_fields(caplog)
            assert fields["outcome"] == outcome and fields["model"] == "None"
            assert fields["tokens"] == "None"
            if outcome == "timeout":
                assert caught.value.duration_ms >= 10 and int(fields["duration_ms"]) >= 10
            else:
                assert fake.posts == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("reported_model", [QWEN_MODEL, "llama3"])
@pytest.mark.parametrize("field,value", [("created_at", None), ("done", "true"), ("eval_count", 2**63)])
def test_malformed_envelope_preserves_known_actual_model_in_error_and_log(
    client_factory, caplog, reported_model, field, value,
):
    caplog.set_level("INFO", logger="src.ai.ollama_client")

    async def exercise():
        fake = FakeOllamaHTTP()
        fake.reply["model"] = reported_model
        fake.reply[field] = value
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_structure")
            assert caught.value.effective_model == reported_model
            fields = scenario_log_fields(caplog)
            assert fields["model"] == reported_model and fields["outcome"] == "invalid_structure"
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("location", ["envelope", "content"])
@pytest.mark.parametrize("depth", [65, 16_000])
def test_json_depth_limit_catches_recursion_before_parser(client_factory, location, depth):
    async def exercise():
        nested = b"[" * depth + b"0" + b"]" * depth
        assert len(nested) <= 32_001
        if depth == 16_000:
            assert len(nested) == 32_001
        fake = FakeOllamaHTTP()
        if location == "envelope":
            fake.raw_reply = nested
        else:
            fake.reply["message"]["content"] = nested.decode()
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_json")
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("field", ["total_duration", "load_duration", "prompt_eval_count", "eval_count", "eval_duration"])
@pytest.mark.parametrize("value", [2**63, -(2**63) - 1, 10**63])
def test_runtime_metrics_outside_i64_never_raise_raw_overflow(client_factory, field, value):
    async def exercise():
        fake = FakeOllamaHTTP()
        fake.reply[field] = value
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_structure")
            assert caught.value.effective_model == QWEN_MODEL
            assert fake.posts == 1

    asyncio.run(exercise())


@pytest.mark.parametrize("location", ["envelope", "content", "legacy_snapshot"])
def test_extremely_large_integer_has_public_error_not_overflow(client_factory, location):
    async def exercise():
        fake = FakeOllamaHTTP()
        huge_integer = "9" * 5000
        if location == "envelope":
            body = json.dumps(fake.reply).replace('"total_duration": 2000000000', '"total_duration": ' + huge_integer)
            fake.raw_reply = body.encode()
        elif location == "content":
            body = fake.reply["message"]["content"]
            fake.reply["message"]["content"] = body.replace('"observed_value": 65100.0', '"observed_value": ' + huge_integer, 1)
        else:
            body = json.dumps(snapshot_dict()).replace('"value": 65100.0', '"value": ' + huge_integer, 1)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                if location == "legacy_snapshot":
                    await client.generate_strategy(body)
                else:
                    await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_snapshot" if location == "legacy_snapshot" else "invalid_json")
            assert fake.posts == (0 if location == "legacy_snapshot" else 1)

    asyncio.run(exercise())


class CountedByteStream(httpx.AsyncByteStream):
    """Chunks reales de HTTPX: el cliente no recibe el body ya bufeado."""

    def __init__(self, body, chunk_size):
        self.body = body
        self.chunk_size = chunk_size
        self.yielded_bytes = 0
        self.yielded_chunks = 0
        self.close_count = 0

    async def __aiter__(self):
        for offset in range(0, len(self.body), self.chunk_size):
            chunk = self.body[offset:offset + self.chunk_size]
            self.yielded_bytes += len(chunk)
            self.yielded_chunks += 1
            await asyncio.sleep(0)
            yield chunk

    async def aclose(self):
        self.close_count += 1


@pytest.mark.parametrize("method,path", [("GET", "/api/tags"), ("POST", "/api/chat")])
@pytest.mark.parametrize("size", [65_535, 65_536, 98_304, 262_144])
@pytest.mark.parametrize("chunk_size", [997, 4096])
def test_http_stream_cap_stops_reading_and_closes_response(client_factory, method, path, size, chunk_size):
    async def exercise():
        fake = FakeOllamaHTTP()
        body = json.dumps({"models": fake.tags} if method == "GET" else fake.reply).encode()
        stream = CountedByteStream(body + b" " * (size - len(body)), chunk_size)
        original_handler = fake.handle

        async def streaming_handler(request):
            if (request.method, request.url.path) != (method, path):
                return await original_handler(request)
            assert request.headers["accept-encoding"] == "identity"
            fake.calls.append((request.method, request.url.path))
            if method == "POST":
                fake.payloads.append(json.loads(request.content))
            return httpx.Response(200, stream=stream)

        fake.handle = streaming_handler
        async with client_factory(fake) as (client, _, _):
            if size <= 65_536:
                result = await client.analyze_snapshot(snapshot())
                assert result.analysis.model_dump(mode="json") == output_dict()
                assert stream.yielded_bytes == size
                assert fake.gets == fake.posts == 1
            else:
                with pytest.raises(ScenarioError) as caught:
                    await client.analyze_snapshot(snapshot())
                assert_error(caught.value, "invalid_structure")
                assert stream.yielded_bytes < size, "Se bufeó todo el stream antes de comprobar el límite"
                assert 65_536 < stream.yielded_bytes <= 65_536 + chunk_size
                assert fake.posts == (0 if method == "GET" else 1)
            assert stream.close_count == 1
        assert stream.close_count == 1

    asyncio.run(exercise())


def public_api_error(code):
    return {"detail": {
        "code": code, "available": False, "requested_model": QWEN_MODEL,
        "effective_model": None, "duration_ms": 0,
        "executable": False, "execution_available": False,
    }}


@pytest.mark.parametrize("path", AI_PATHS)
@pytest.mark.parametrize("size", [32_769, 131_072])
def test_api_stream_body_limit_before_factory(isolated_app, monkeypatch, path, size):
    async def exercise():
        factory = Mock(side_effect=AssertionError("No crear IA antes de validar límite del body"))
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        prefix = json.dumps({"snapshot": snapshot_dict()}).encode()
        body = prefix + b" " * (size - len(prefix))
        yielded_bytes = 0

        async def chunks():
            nonlocal yielded_bytes
            for offset in range(0, len(body), 1024):
                chunk = body[offset:offset + 1024]
                yielded_bytes += len(chunk)
                yield chunk

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
            response = await http.post(path, content=chunks(), headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert response.json() == public_api_error("request_too_large")
        factory.assert_not_called()
        assert bot_api._bot_state["ollama_client"] is None
        assert 32_768 < yielded_bytes <= 32_768 + 1024
        if size > 32_768 + 1024:
            assert yielded_bytes < size

    asyncio.run(exercise())


@pytest.mark.parametrize("path", AI_PATHS)
@pytest.mark.parametrize("case", ["depth", "nan", "infinity", "duplicate_root", "duplicate_nested"])
def test_illegal_api_json_body_is_invalid_snapshot_before_factory(isolated_app, monkeypatch, path, case):
    async def exercise():
        body = json.dumps({"snapshot": snapshot_dict()})
        if case == "depth":
            body = "[" * 16_000 + "0" + "]" * 16_000
        elif case in ("nan", "infinity"):
            body = body.replace('"value": 65100.0', '"value": ' + ("NaN" if case == "nan" else "Infinity"), 1)
        elif case == "duplicate_root":
            body = body[:-1] + ',"snapshot":' + json.dumps(snapshot_dict()) + "}"
        else:
            body = body.replace('"value": 65100.0', '"value": 65100.0,"value": 65100.0', 1)
        assert len(body.encode()) <= 32_768
        factory = Mock(side_effect=AssertionError("No crear IA para JSON inválido"))
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
            response = await http.post(path, content=body, headers={"Content-Type": "application/json"})
        assert response.status_code == 422
        assert response.json() == public_api_error("invalid_snapshot")
        factory.assert_not_called()
        assert bot_api._bot_state["ollama_client"] is None

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [0, 1, True, "false", None])
def test_provenance_verified_requires_actual_false_boolean(value):
    data = output_dict()
    data["provenance_verified"] = value
    with pytest.raises(ValidationError):
        ScenarioOutput.model_validate(data)


def test_provenance_verified_is_mandatory_in_model_output():
    data = output_dict()
    del data["provenance_verified"]
    with pytest.raises(ValidationError):
        ScenarioOutput.model_validate(data)


@pytest.mark.parametrize("value", [0, True, "false", None])
def test_client_rejects_nonboolean_or_verified_provenance(client_factory, value):
    async def exercise():
        fake = FakeOllamaHTTP()
        data = output_dict()
        data["provenance_verified"] = value
        fake.reply["message"]["content"] = json.dumps(data)
        async with client_factory(fake) as (client, _, _):
            with pytest.raises(ScenarioError) as caught:
                await client.analyze_snapshot(snapshot())
            assert_error(caught.value, "invalid_structure")

    asyncio.run(exercise())


@pytest.mark.parametrize("path", AI_PATHS)
def test_declared_market_engine_source_remains_unverified_in_api(isolated_app, client_factory, path):
    async def exercise():
        data = snapshot_dict()
        data["data_source"] = "market_engine"
        context = ScenarioSnapshot.model_validate(data)
        async with client_factory(FakeOllamaHTTP(context)) as (client, _, _):
            bot_api._bot_state["ollama_client"] = client
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
                response = await http.post(path, json={"snapshot": data})
            assert response.status_code == 200
            result = response.json()
            assert result["data_source"] == "unverified"
            assert result["analysis"]["data_source"] == "market_engine"
            assert result["analysis"]["provenance_verified"] is False
            assert result["executable"] is False and result["execution_available"] is False

    asyncio.run(exercise())


@pytest.mark.parametrize("summaries", [
    {}, {"continuation": "c", "failure": "f"},
    {"continuation": "c", "failure": "f", "sideways": "s", "other": "o"},
    {"continuation": "c", "failure": "f", "invented": "s"},
    {"continuation": "", "failure": "f", "sideways": "s"},
    {"continuation": "c" * 257, "failure": "f", "sideways": "s"},
])
def test_result_summaries_require_exact_three_named_nonempty_keys(summaries):
    with pytest.raises(ValidationError):
        ScenarioAnalysisResult(
            analysis=ScenarioOutput.model_validate(output_dict()), model_used=QWEN_MODEL,
            duration_ms=0, data_source="TEST_ONLY", summaries=summaries,
        )


@pytest.mark.parametrize("registered_context", [False, True])
def test_bot_lifespan_waits_closes_registered_client_once_and_clears_state(
    isolated_app, monkeypatch, registered_context,
):
    async def exercise():
        baseline = asyncio.all_tasks()

        class LifespanClient(FakeScenarioClient):
            def __init__(self):
                super().__init__()
                self.close_count = 0
                self.close_entered = asyncio.Event()
                self.close_release = asyncio.Event()

            async def close(self):
                self.close_count += 1
                self.close_entered.set()
                await self.close_release.wait()

        fake = LifespanClient()
        factory = Mock(side_effect=AssertionError("Status no debe inicializar HTTP/IA"))
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        context = isolated_app.router.lifespan_context if registered_context else bot_api._bot_lifespan
        leave = asyncio.Event()
        entered = asyncio.Event()

        async def lifecycle():
            async with context(isolated_app):
                entered.set()
                await leave.wait()

        task = asyncio.create_task(lifecycle())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as http:
                response = await http.get("/api/bot/status")
            assert response.status_code == 200 and response.json()["execution_available"] is False
            factory.assert_not_called()
            assert bot_api._bot_state["ollama_client"] is None
            bot_api._bot_state["ollama_client"] = fake
            leave.set()
            await asyncio.wait_for(fake.close_entered.wait(), 1)
            assert not task.done() and bot_api._bot_state["ollama_client"] is fake
            fake.close_release.set()
            await asyncio.wait_for(task, 1)
            assert fake.close_count == 1
            assert bot_api._bot_state["ollama_client"] is None
            # Otra salida sin cliente registrado no repite su cierre.
            async with context(isolated_app):
                pass
            assert fake.close_count == 1
            await no_new_tasks(baseline)
        finally:
            leave.set()
            fake.close_release.set()
            await finish_test_callers(task)

    asyncio.run(exercise())


def test_real_market_engine_receives_forming_and_closed_bars_while_qwen_post_waits(
    client_factory, monkeypatch,
):
    # Importar el motor real solamente; nunca src.api.main ni lifespan de mercado.
    import src.market.engine as engine_module

    async def exercise():
        baseline = asyncio.all_tasks()
        clock = Clock()
        rest_calls = []
        rest_instances = []
        ws_instances = []

        def candle(open_time, *, closed, close=65_100.0, volume=10.0):
            return {
                "symbol": "BTC/USDT", "open_time": open_time,
                "close_time": open_time + 59_999, "open": 65_000.0,
                "high": 65_300.0, "low": 64_900.0, "close": close,
                "volume": volume, "is_closed": closed,
            }

        class FakeBinanceHTTP:
            def __init__(self, config):
                self.config = config
                self.close_count = 0
                rest_instances.append(self)

            async def get_klines(self, symbol, interval="1m", limit=500, start_ts=None, end_ts=None):
                assert symbol == "BTC/USDT" and interval == "1m"
                assert limit == 500 and start_ts is None and end_ts == CLOSE_MS
                rest_calls.append((symbol, interval, limit, start_ts, end_ts))
                last_open = CLOSE_MS - 59_999
                return [candle(last_open - (499 - index) * 60_000, closed=True) for index in range(500)]

            async def close(self):
                self.close_count += 1

        class FakeBinanceWS:
            def __init__(self, config):
                self.config = config
                self.handlers = {}
                self.started = asyncio.Event()
                self.finished = asyncio.Event()
                self.stop_count = 0
                ws_instances.append(self)

            def on_message(self, callback):
                self.handlers["message"] = callback

            def on_connect(self, callback):
                self.handlers["connect"] = callback

            def on_disconnect(self, callback):
                self.handlers["disconnect"] = callback

            def on_error(self, callback):
                self.handlers["error"] = callback

            async def start(self, symbol):
                assert symbol == "BTC/USDT"
                self.started.set()
                await self.finished.wait()

            def connect(self):
                self.handlers["connect"]()

            def message(self, payload):
                self.handlers["message"](payload)

            async def stop(self):
                self.stop_count += 1
                self.finished.set()

        monkeypatch.setattr(engine_module, "BinanceHTTPClient", FakeBinanceHTTP)
        monkeypatch.setattr(engine_module, "BinanceWSClient", FakeBinanceWS)
        engine = engine_module.MarketEngine(engine_module.BinanceConfig(kline_interval="1m"), clock_ms=clock)
        updates, closes, states = [], [], []

        async def on_update(payload):
            updates.append(payload)

        async def on_close(payload):
            closes.append(payload)

        async def on_state(payload):
            states.append(payload)

        engine.on_bar_updated(on_update)
        engine.on_bar_closed(on_close)
        engine.on_state_change(on_state)
        start_task = asyncio.create_task(engine.start("BTC/USDT"))
        inference = None
        fake = FakeOllamaHTTP()
        fake.block = True
        try:
            await asyncio.wait_for(engine.started.wait(), 1)
            assert len(ws_instances) == 1
            ws = ws_instances[0]
            await asyncio.wait_for(ws.started.wait(), 1)
            ws.connect()
            await eventually(lambda: engine.snapshot()["entries_allowed"])
            boot = engine.snapshot()
            assert boot["history_loaded"] and boot["complete"] and not boot["recovering"]
            assert boot["closed_candles_count"] == 500 and boot["pending_gaps"] == 0
            assert boot["last_closed_close_time"] == CLOSE_MS and not boot["stale"]
            assert len(rest_calls) == 1
            data = snapshot_dict()
            data["data_source"] = "market_engine"
            context = ScenarioSnapshot.model_validate(data)
            fake.reply = runtime_response(context)
            async with client_factory(fake, clock) as (client, _, _):
                inference = asyncio.create_task(client.analyze_snapshot(context))
                try:
                    await asyncio.wait_for(fake.entered.wait(), 1)
                    next_open = CLOSE_MS + 1
                    forming = candle(next_open, closed=False, close=65_150.0, volume=12.0)
                    ws.message(forming)
                    await eventually(lambda: bool(updates))
                    assert updates[-1]["open_time"] == next_open and updates[-1]["is_closed"] is False
                    assert engine.snapshot()["last_closed_close_time"] == CLOSE_MS
                    assert fake.active == 1 and not inference.done()
                    clock.now += 60_000
                    closed = candle(next_open, closed=True, close=65_200.0, volume=20.0)
                    ws.message(closed)
                    await eventually(lambda: bool(closes) and states[-1]["last_closed_close_time"] == CLOSE_MS + 60_000)
                    assert closes[-1]["close_time"] == CLOSE_MS + 60_000 and closes[-1]["is_closed"] is True
                    ready = engine.snapshot()
                    assert ready["last_closed_close_time"] == CLOSE_MS + 60_000
                    assert engine.closed_candles[-1]["close"] == 65_200.0
                    assert ready["entries_allowed"] and ready["history_loaded"] and ready["complete"]
                    assert not ready["recovering"] and not ready["stale"] and ready["pending_gaps"] == 0
                    assert ready["execution_available"] is False
                    assert len(rest_calls) == 1 and fake.posts == fake.gets == 1
                    assert fake.active == 1 and not inference.done()
                    assert clock() - context.as_of_close_time_ms == 60_001 < 90_000
                    fake.release.set()
                    result = await asyncio.wait_for(inference, 1)
                    assert result.analysis.model_dump(mode="json") == output_dict(context)
                    result.analysis.validate_for(context, clock())
                    assert result.data_source == "unverified" and result.analysis.provenance_verified is False
                    assert result.executable is False and result.execution_available is False
                finally:
                    fake.release.set()
                    await finish_test_callers(inference)
        finally:
            fake.release.set()
            await asyncio.wait_for(engine.stop(), 1)
            await asyncio.wait_for(start_task, 1)
        assert rest_instances[0].close_count == ws_instances[0].stop_count == 1
        assert engine.snapshot()["connected"] is False and engine.snapshot()["entries_allowed"] is False
        await no_new_tasks(baseline)

    asyncio.run(exercise())


def test_status_with_registered_client_never_starts_http_and_lifespan_closes_it(
    isolated_app, owned_client_factory, monkeypatch,
):
    async def exercise():
        baseline = asyncio.all_tasks()
        factory = Mock(side_effect=AssertionError("Status no debe crear otro cliente IA"))
        monkeypatch.setattr(bot_api, "OllamaClient", factory)
        async with owned_client_factory() as (client, fake, http):
            bot_api._bot_state["ollama_client"] = client
            async with isolated_app.router.lifespan_context(isolated_app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url="http://app.test") as app_http:
                    for _ in range(2):
                        response = await asyncio.wait_for(app_http.get("/api/bot/status"), 0.2)
                        assert response.status_code == 200
                        assert response.json()["execution_available"] is False
                assert fake.calls == [], "Status no debe consultar tags ni ejecutar inferencia"
                assert http.close_count == 0 and bot_api._bot_state["ollama_client"] is client
                factory.assert_not_called()
            assert bot_api._bot_state["ollama_client"] is None
            assert fake.calls == [] and http.is_closed and http.close_count == 1
            await client.close()
            assert http.close_count == 1
        await no_new_tasks(baseline)

    asyncio.run(exercise())
