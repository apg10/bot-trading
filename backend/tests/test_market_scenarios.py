"""Tests para analyze_market_scenarios: forwarding exacto, propagación MarketAnalysisInputError y ScenarioError."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Importa la guarda original del helper.
from test_analysis_market_input import _block_network  # noqa: F401
from src.ai.scenario_models import ScenarioAnalysisResult, ScenarioError
from src.api.analysis_market import MarketAnalysisInputError


# ---------------------------------------------------------------------------
# Fake cliente N02: acepta snapshot y retorna un ScenarioAnalysisResult real.
# ---------------------------------------------------------------------------


def _make_result():
    """ScenarioAnalysisResult real de REF-CONTEXTO con campos mínimos válidos."""
    return ScenarioAnalysisResult(
        analysis={
            "schema_version": "scenario_analysis.v1",
            "snapshot_id": "snap-test-001",
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "as_of_close_time_ms": 1700000039999,
            "valid_until_ms": 1700000129999,
            "data_source": "market_engine",
            "provenance_verified": False,
            "result_type": "scenario_advisory",
            "executable": False,
            "scenarios": {
                "continuation": {
                    "evidence": [{"evidence_id": "last_close", "observed_value": 149.0}],
                    "observations": [],
                    "condition_logic": "all",
                    "conditions": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "gt", "threshold_ref": "last_close"},
                    ],
                    "invalidation_logic": "any",
                    "invalidations": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "lte", "threshold_ref": "last_close"},
                    ],
                },
                "failure": {
                    "evidence": [{"evidence_id": "last_close", "observed_value": 149.0}],
                    "observations": [],
                    "condition_logic": "all",
                    "conditions": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "lt", "threshold_ref": "last_close"},
                    ],
                    "invalidation_logic": "any",
                    "invalidations": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "gte", "threshold_ref": "last_close"},
                    ],
                },
                "sideways": {
                    "evidence": [{"evidence_id": "last_close", "observed_value": 149.0}],
                    "observations": [],
                    "condition_logic": "all",
                    "conditions": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "eq", "threshold_ref": "last_close"},
                    ],
                    "invalidation_logic": "any",
                    "invalidations": [
                        {"applies_to": "future_closed_snapshot", "metric_ref": "last_close", "operator": "ne", "threshold_ref": "last_close"},
                    ],
                },
            },
            "missing_data": [],
        },
        available=True,
        result_type="scenario_advisory",
        requested_model="qwen3.6:35b-a3b-q4_K_M-128k",
        model_used="qwen3.6:35b-a3b-q4_K_M-128k",
        duration_ms=10,
        total_duration_ms=None,
        load_duration_ms=None,
        prompt_tokens=None,
        generated_tokens=None,
        generation_tokens_per_second=None,
        data_source="unverified",
        cache_hit=False,
        deduplicated=False,
        executable=False,
        execution_available=False,
        summaries={
            "continuation": "Cont.",
            "failure": "Fall.",
            "sideways": "Side.",
        },
    )


# ---------------------------------------------------------------------------
# Test 1: Forwarding exacto, await una vez, mismo snapshot al fake cliente y
# retorno por identidad del ScenarioAnalysisResult real de REF-CONTEXTO.
# ---------------------------------------------------------------------------


def test_forwarding_exact():
    """Forwarding exacto: un await, snapshot idéntico, resultado por identidad."""
    from src.ai.market_scenarios import analyze_market_scenarios

    fake_result = _make_result()
    captured_snapshot = None

    async def fake_analyze_snapshot(snapshot):
        nonlocal captured_snapshot
        captured_snapshot = snapshot
        return fake_result

    fake_client = MagicMock()
    fake_client.analyze_snapshot = AsyncMock(side_effect=fake_analyze_snapshot)

    # Mock del helper importado en el módulo nuevo: controla esta frontera.
    fake_snapshot = _make_fake_snapshot()

    with patch(
        "src.ai.market_scenarios.prepare_market_scenario_snapshot",
        return_value=fake_snapshot,
    ) as mock_prepare:
        result = asyncio.run(
            analyze_market_scenarios(
                fake_client,
                engine=None,  # no se usa porque prepare está mocked.
                symbol="BTC/USDT",
                candles_count=200,
                snapshot_id="snap-test-001",
                close_ref="last_close",
                captured_at_ms=1700000040000,
                valid_until_ms=1700000129999,
                missing_data=[],
            )
        )

    # prepare fue llamado exactamente una vez con los argumentos exactos.
    mock_prepare.assert_called_once_with(
        None,
        symbol="BTC/USDT",
        candles_count=200,
        snapshot_id="snap-test-001",
        close_ref="last_close",
        captured_at_ms=1700000040000,
        valid_until_ms=1700000129999,
        missing_data=[],
    )

    # await una vez: fake_client.analyze_snapshot fue llamado una sola vez.
    assert fake_client.analyze_snapshot.call_count == 1

    # snapshot idéntico al retornado por prepare (por identidad).
    assert captured_snapshot is fake_snapshot

    # retorno por identidad del ScenarioAnalysisResult real de REF-CONTEXTO.
    assert result is fake_result


# ---------------------------------------------------------------------------
# Test 2: Helper lanza MarketAnalysisInputError("MARKET_NOT_READY","fixture"):
# misma excepción propagada y cero llamadas al cliente.
# ---------------------------------------------------------------------------


def test_helper_raises_market_not_ready():
    """MarketAnalysisInputError propagada intacta; cero llamadas al cliente."""
    from src.ai.market_scenarios import analyze_market_scenarios

    fake_client = MagicMock()
    fake_client.analyze_snapshot = AsyncMock()

    error_msg = "fixture"
    helper_error = MarketAnalysisInputError("MARKET_NOT_READY", error_msg)

    with patch(
        "src.ai.market_scenarios.prepare_market_scenario_snapshot",
        side_effect=helper_error,
    ):
        with pytest.raises(MarketAnalysisInputError) as exc_info:
            asyncio.run(
                analyze_market_scenarios(
                    fake_client,
                    engine=None,
                    symbol="BTC/USDT",
                    candles_count=200,
                    snapshot_id="snap-test-002",
                    close_ref="last_close",
                    captured_at_ms=1700000040000,
                    valid_until_ms=1700000129999,
                    missing_data=[],
                )
            )

    assert exc_info.value.code == "MARKET_NOT_READY"
    assert str(exc_info.value) == error_msg
    assert fake_client.analyze_snapshot.call_count == 0
    assert exc_info.value is helper_error


# ---------------------------------------------------------------------------
# Test 3: Cliente lanza ScenarioError("unavailable"):
# misma excepción propagada, sin respuesta de éxito ni sustitución.
# ---------------------------------------------------------------------------


def test_client_raises_scenario_error():
    """ScenarioError del cliente propagada intacta; sin respuesta de éxito."""
    from src.ai.market_scenarios import analyze_market_scenarios

    fake_result = _make_result()
    fake_client = MagicMock()
    client_error = ScenarioError("unavailable")
    fake_client.analyze_snapshot = AsyncMock(side_effect=client_error)

    fake_snapshot = _make_fake_snapshot()

    with patch(
        "src.ai.market_scenarios.prepare_market_scenario_snapshot",
        return_value=fake_snapshot,
    ):
        with pytest.raises(ScenarioError) as exc_info:
            asyncio.run(
                analyze_market_scenarios(
                    fake_client,
                    engine=None,
                    symbol="BTC/USDT",
                    candles_count=200,
                    snapshot_id="snap-test-003",
                    close_ref="last_close",
                    captured_at_ms=1700000040000,
                    valid_until_ms=1700000129999,
                    missing_data=[],
                )
            )

    assert exc_info.value.code == "unavailable"
    # No se retorna fake_result (sin sustitución).
    assert exc_info.value is client_error


# ---------------------------------------------------------------------------
# Helper: crea un ScenarioSnapshot mínimo válido para pruebas.
# ---------------------------------------------------------------------------


def _make_fake_snapshot():
    """ScenarioSnapshot mínimo válido para pruebas."""
    from src.ai.scenario_models import SnapshotMarketState, SnapshotEvidence

    ms = SnapshotMarketState(
        connected=True,
        history_loaded=True,
        complete=True,
        recovering=False,
        stale=False,
        pending_gaps=0,
    )
    ev = SnapshotEvidence(
        value=149.0,
        dimension="price",
        unit="USDT",
        observed_at_ms=1700000039999,
    )
    from src.ai.scenario_models import ScenarioSnapshot

    return ScenarioSnapshot(
        schema_version="market_snapshot.v1",
        snapshot_id="snap-test-001",
        symbol="BTC/USDT",
        timeframe="1m",
        environment="paper",
        data_source="market_engine",
        captured_at_ms=1700000040000,
        as_of_close_time_ms=1700000039999,
        valid_until_ms=1700000129999,
        market_state=ms,
        evidence={"last_close": ev},
        missing_data=[],
    )
