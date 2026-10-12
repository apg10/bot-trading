"""Pruebas aisladas de project_market_state sin engine, main.app ni transportes reales."""

from __future__ import annotations

import copy

import pytest

from test_analysis_market_input import _block_network  # noqa: F401

# ---------------------------------------------------------------------------
# Fixture de estado válido mínimo con extras permitidos.
# ---------------------------------------------------------------------------


def _market_state_fixture() -> dict:
    """Estado válido mínimo más extras arbitrarios."""
    base = {
        "connected": True,
        "history_loaded": True,
        "complete": True,
        "recovering": False,
        "stale": False,
        "pending_gaps": 0,
        # Extras que deben ignorarse:
        "_engine_id": "test-1",
        "last_update_ms": 1_700_000_000_000,
    }
    return base


# ---------------------------------------------------------------------------
# Control válido con extras y no mutación; comprobar exactamente los seis campos.
# ---------------------------------------------------------------------------


def test_project_market_state_valid_with_extras():
    state = _market_state_fixture()
    original_copy = copy.deepcopy(state)

    from src.ai.scenario_adapters import project_market_state

    result = project_market_state(state)

    # No mutó el dict original.
    assert state == original_copy

    # Comprobar exactamente los seis campos.
    assert result.connected is True
    assert result.history_loaded is True
    assert result.complete is True
    assert result.recovering is False
    assert result.stale is False
    assert result.pending_gaps == 0


# ---------------------------------------------------------------------------
# Comprobar que connected=False se conserva.
# ---------------------------------------------------------------------------


def test_project_market_state_connected_false_preserved():
    state = _market_state_fixture()
    state["connected"] = False

    from src.ai.scenario_adapters import project_market_state

    result = project_market_state(state)

    assert result.connected is False


# ---------------------------------------------------------------------------
# Negativos parametrizados aislados: complete ausente y pending_gaps=True.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "modify_key, modify_value",
    [
        ("complete", None),
        ("pending_gaps", True),
    ],
)
def test_project_market_state_negative_parametrized(modify_key, modify_value):
    state = _market_state_fixture()

    if modify_key == "complete":
        del state["complete"]
    else:
        state[modify_key] = modify_value

    from pydantic import ValidationError

    from src.ai.scenario_adapters import project_market_state

    with pytest.raises(ValidationError) as exc_info:
        project_market_state(state)

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == (modify_key,)


# ---------------------------------------------------------------------------
# Fixture PreparedMarketAnalysis simulada con 50 velas alineadas, closes 100.0…149.0.
# Se usan DTOs reales de api/analysis.py y api/analysis_market.py; no datos reales.
# ---------------------------------------------------------------------------


def _prepared_fixture() -> "PreparedMarketAnalysis":
    """Fixture simulada: 50 velas, closes 100.0…149.0, count=50, estado válido, as_of exacto."""
    from dataclasses import dataclass
    from typing import Literal

    # Importar DTOs reales (no se crean clases privadas).
    from src.api.analysis import CandleInput, AnalysisRequest
    from src.api.analysis_market import PreparedMarketAnalysis

    # Construir 50 velas con open_time=1800000000000+i*60000, i=0…49.
    candles_list: list[CandleInput] = []
    for i in range(50):
        candles_list.append(CandleInput(
            open_time=1800000000000 + i * 60_000,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.0 + i,
            volume=0.0,
        ))

    request = AnalysisRequest(symbol="BTC/USDT", timeframe="1m", candles_count=50, candles=candles_list)

    as_of_close_time_ms = 1800000000000 + 50 * 60_000 - 1

    return PreparedMarketAnalysis(
        request=request,
        data_source="market_engine",
        as_of_close_time_ms=as_of_close_time_ms,
        market_state={
            "connected": True,
            "history_loaded": True,
            "complete": True,
            "recovering": False,
            "stale": False,
            "pending_gaps": 0,
        },
    )


# ---------------------------------------------------------------------------
# Control: valor 149.0, dimensión/unidad/as_of exactos y entrada intacta.
# ---------------------------------------------------------------------------


def test_last_close_to_evidence_control():
    prepared = _prepared_fixture()
    original_copy = copy.deepcopy(prepared)

    from src.ai.scenario_adapters import last_close_to_evidence

    result = last_close_to_evidence(prepared, quote_unit="USDT")

    # Valor exacto del último close (149.0).
    assert result.value == 149.0
    # Dimensión fija.
    assert result.dimension == "price"
    # Unidad delegada.
    assert result.unit == "USDT"
    # observed_at_ms = as_of_close_time_ms del prepared.
    assert result.observed_at_ms == original_copy.as_of_close_time_ms

    # Entrada intacta (no mutada).
    assert prepared == original_copy


# ---------------------------------------------------------------------------
# Negativo aislado: misma fixture con candles vacías; comprobar ValueError.
# ---------------------------------------------------------------------------


def test_last_close_to_evidence_empty_candles():
    prepared = _prepared_fixture()
    # Vaciar las candles del request simulado.
    prepared.request.candles = []  # type: ignore[union-attr]

    from src.ai.scenario_adapters import last_close_to_evidence

    with pytest.raises(ValueError) as exc_info:
        last_close_to_evidence(prepared, quote_unit="USDT")

    assert str(exc_info.value) == "EMPTY_PREPARED_CANDLES"


# ---------------------------------------------------------------------------
# Helper de kwargs para build_market_scenario_snapshot.
# ---------------------------------------------------------------------------


def _snapshot_kwargs(prepared):
    """Kwargs válidos para build_market_scenario_snapshot desde la fixture."""
    return {
        "snapshot_id": "snap-test-001",
        "close_ref": "last_close",
        "captured_at_ms": prepared.as_of_close_time_ms + 1,
        "valid_until_ms": prepared.as_of_close_time_ms + 30_000,
        "missing_data": ["ohlc_extra"],
    }


# ---------------------------------------------------------------------------
# Control: captura=as_of+1 y vencimiento=as_of+30000 como datos de fixture;
# metadata/evidencia exactas, solo seis campos de estado y missing_data sin alias.
# ---------------------------------------------------------------------------


def test_build_market_scenario_snapshot_control():
    prepared = _prepared_fixture()

    from src.ai.scenario_adapters import build_market_scenario_snapshot

    kwargs = _snapshot_kwargs(prepared)
    result = build_market_scenario_snapshot(prepared, **kwargs)

    # Metadata exacta del prepared.
    assert result.schema_version == "market_snapshot.v1"
    assert result.snapshot_id == "snap-test-001"
    assert result.symbol == "BTC/USDT"
    assert result.timeframe == "1m"
    assert result.environment == "paper"
    assert result.data_source == "market_engine"
    assert result.captured_at_ms == prepared.as_of_close_time_ms + 1
    assert result.as_of_close_time_ms == prepared.as_of_close_time_ms
    assert result.valid_until_ms == prepared.as_of_close_time_ms + 30_000

    # Evidencia exacta.
    assert "last_close" in result.evidence
    ev = result.evidence["last_close"]
    assert ev.value == 149.0
    assert ev.dimension == "price"
    assert ev.unit == "USDT"
    assert ev.observed_at_ms == prepared.as_of_close_time_ms

    # Solo seis campos de estado (no hay alias).
    ms = result.market_state
    assert ms.connected is True
    assert ms.history_loaded is True
    assert ms.complete is True
    assert ms.recovering is False
    assert ms.stale is False
    assert ms.pending_gaps == 0

    # missing_data copiada sin alias.
    assert result.missing_data == ["ohlc_extra"]

    # Independencia: listas distintas, mutaciones aisladas.
    assert result.missing_data is not kwargs["missing_data"]
    kwargs["missing_data"].append("caller_change")
    assert result.missing_data == ["ohlc_extra"]
    result.missing_data.append("snapshot_change")
    assert kwargs["missing_data"] == ["ohlc_extra", "caller_change"]
    assert result.missing_data == ["ohlc_extra", "snapshot_change"]


# ---------------------------------------------------------------------------
# Negativos aislados: source/flag inválidos (parametrizados),
# vencimiento>as_of+90000 (ValidationError) y connected=False
# (ScenarioError.code=="market_incomplete").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("data_source", "unverified"),
        ("execution_available", True),
    ],
)
def test_build_market_scenario_snapshot_invalid_source_flag(field, invalid_value):
    prepared = _prepared_fixture()

    from src.ai.scenario_adapters import build_market_scenario_snapshot

    # Modificar el campo inválido en el prepared.
    if field == "data_source":
        object.__setattr__(prepared, "data_source", invalid_value)  # type: ignore[attr-defined]
    else:
        object.__setattr__(prepared, "execution_available", invalid_value)  # type: ignore[attr-defined]

    kwargs = _snapshot_kwargs(prepared)

    with pytest.raises(ValueError):
        build_market_scenario_snapshot(prepared, **kwargs)


def test_build_market_scenario_snapshot_valid_until_too_far():
    prepared = _prepared_fixture()

    from pydantic import ValidationError

    from src.ai.scenario_adapters import build_market_scenario_snapshot

    kwargs = _snapshot_kwargs(prepared)
    # Vencimiento > as_of + 90000 → ValidationError por el validador de ScenarioSnapshot.
    kwargs["valid_until_ms"] = prepared.as_of_close_time_ms + 90_001

    with pytest.raises(ValidationError):
        build_market_scenario_snapshot(prepared, **kwargs)


def test_build_market_scenario_snapshot_connected_false():
    prepared = _prepared_fixture()

    from src.ai.scenario_models import ScenarioError

    from src.ai.scenario_adapters import build_market_scenario_snapshot

    # connected=False → market_incomplete.
    prepared.market_state["connected"] = False  # type: ignore[index]

    kwargs = _snapshot_kwargs(prepared)

    with pytest.raises(ScenarioError) as exc_info:
        build_market_scenario_snapshot(prepared, **kwargs)

    assert exc_info.value.code == "market_incomplete"


# ---------------------------------------------------------------------------
# Fixture _aligned_engine_fixture: reutiliza _FakeEngine con candles de _prepared_fixture.
# ---------------------------------------------------------------------------


def _aligned_engine_fixture():
    """_FakeEngine con 50 cerradas alineadas a _prepared_fixture."""
    from test_analysis_market_input import _FakeEngine

    # Obtener las velas de la fixture preparada para extraer sus valores.
    prepared = _prepared_fixture()
    candles = prepared.request.candles  # type: ignore[attr-defined]

    # Sustituir _closed por un dict keyed open_time con las mismas velas.
    last_as_of = prepared.as_of_close_time_ms  # type: ignore[attr-defined]
    engine = _FakeEngine(closed_count=0, last_closed_close_time=last_as_of)
    engine._closed = {}  # type: ignore[attr-defined]
    for c in candles:
        ot = c.open_time
        engine._closed[ot] = {
            "open_time": ot,
            "close_time": ot + 59_999,
            "is_closed": True,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
    # Actualizar _last_closed_close_time al as_of de la fixture.
    engine._last_closed_close_time = last_as_of  # type: ignore[attr-defined]

    return engine


# ---------------------------------------------------------------------------
# Control: default200 con50 cerradas, valor149.0/as_of/metadata exactos y
# _snapshot_call_count==2; usar metadata explícita válida de _snapshot_kwargs.
# ---------------------------------------------------------------------------


def test_prepare_market_scenario_snapshot_valid():
    engine = _aligned_engine_fixture()

    from src.ai.scenario_adapters import prepare_market_scenario_snapshot

    # Metadata explícita válida alineada al as_of del engine.
    as_of = engine._last_closed_close_time  # type: ignore[attr-defined]
    result = prepare_market_scenario_snapshot(
        engine,
        symbol="BTC/USDT",
        candles_count=200,
        snapshot_id="snap-test-001",
        close_ref="last_close",
        captured_at_ms=as_of + 1,
        valid_until_ms=as_of + 30_000,
        missing_data=["ohlc_extra"],
    )

    # Valor exacto del último close (149.0).
    ev = result.evidence["last_close"]
    assert ev.value == 149.0
    assert ev.dimension == "price"
    assert ev.unit == "USDT"
    assert ev.observed_at_ms == result.as_of_close_time_ms

    # Metadata exacta del prepared/snapshot.
    assert result.schema_version == "market_snapshot.v1"
    assert result.snapshot_id == "snap-test-001"
    assert result.symbol == "BTC/USDT"
    assert result.timeframe == "1m"
    assert result.environment == "paper"
    assert result.data_source == "market_engine"
    assert result.captured_at_ms == as_of + 1
    assert result.as_of_close_time_ms == engine._last_closed_close_time  # type: ignore[attr-defined]
    assert result.valid_until_ms == as_of + 30_000

    # _snapshot_call_count==2 (prepare llama snapshot dos veces).
    assert engine._snapshot_call_count == 2


# ---------------------------------------------------------------------------
# Negativo: engine=None → MarketAnalysisInputError.code=="MARKET_NOT_READY".
# ---------------------------------------------------------------------------


def test_prepare_market_scenario_snapshot_no_engine():
    from src.api.analysis_market import MarketAnalysisInputError

    from src.ai.scenario_adapters import prepare_market_scenario_snapshot

    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_scenario_snapshot(
            None,
            symbol="BTC/USDT",
            candles_count=200,
            snapshot_id="snap-test-002",
            close_ref="last_close",
            captured_at_ms=1800000000000 + 50 * 60_000,
            valid_until_ms=1800000000000 + 50 * 60_000 + 30_000,
            missing_data=["ohlc_extra"],
        )

    assert exc_info.value.code == "MARKET_NOT_READY"
