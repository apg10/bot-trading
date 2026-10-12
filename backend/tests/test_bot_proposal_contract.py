# ── test_bot_proposal_contract.py ────────────────────────────────────────────
# Prueba aislada del contrato de propuesta TEST_ONLY para POST /api/bot/analyze.
# No lee ni modifica tests N02; no llama rutas IA; no importa fixtures N02.

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

# Montar la app de FastAPI sin importar módulos N02.
_backend_root = Path(__file__).resolve().parents[2]
if str(_backend_root) not in sys.path:
    sys.path.insert(0, str(_backend_root))

from src.bot.models import (  # noqa: E402
    BotProposalSignal,
    BotProposalAnalysis,
    BotProposalResponse,
    TradingSignal,
    AnalysisResult,
    SignalType,
    SignalStrength,
)


# ── Fixture autouse de aislamiento de bot state ───────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_bot_state():
    """Aísla/restaura el estado global del bot por test: snapshot seguro, estrategia determinista, restauración en finally."""
    from src.api.bot import _bot_state  # noqa: E402
    from src.bot.models import TradingStrategy  # noqa: E402

    # Snapshot profundo del estado previo
    snapshot = {k: v for k, v in _bot_state.items()}

    # Estrategia determinista sin importar src.bot.rules
    _deterministic_strategy = TradingStrategy(
        name="test_isolation",
        rules=[],
        description="",
        max_position_size=0.01,
        stop_loss_pct=0.001,
        take_profit_pct=0.001,
    )

    # Establecer estado determinista antes de cada prueba
    _bot_state["strategy"] = _deterministic_strategy
    _bot_state["engine"] = None
    _bot_state["orders"] = []
    _bot_state["positions"] = []
    _bot_state["ollama_client"] = None

    yield

    # Restaurar el snapshot en finally para garantizar limpieza
    _bot_state.clear()
    _bot_state.update(snapshot)


# ── Helpers de montaje aislado ───────────────────────────────────────────────

def _make_analyze_app():
    """Monta SOLO /api/bot/analyze en una app nueva; sin router completo ni IA."""
    from src.api.bot import analyze_with_rules  # noqa: E402
    app = FastAPI()
    app.add_api_route("/api/bot/analyze", analyze_with_rules, methods=["POST"])
    return TestClient(app)


# ── Fixture de app aislada ───────────────────────────────────────────────────

@pytest.fixture()
def client():
    """App FastAPI con SOLO /api/bot/analyze; sin Ollama ni IA."""
    return _make_analyze_app()


# ── Tests del contrato ───────────────────────────────────────────────────────

TOP_KEYS = frozenset((
    "data_source", "result_type", "executable", "execution_available",
    "warning", "signal", "strategy", "analysis",
))

SIGNAL_KEYS = frozenset((
    "timestamp_ms", "symbol", "signal_type", "strength", "confidence",
    "entry_price", "stop_loss_price", "take_profit_price", "reason",
    "rules_triggered", "data_source", "result_type", "executable",
))

STRATEGY_KEYS = frozenset((
    "description", "max_position_size", "name", "rules",
    "stop_loss_pct", "take_profit_pct",
))

WARNING_TEXT = "Indicadores fijos de desarrollo; reglas del método pendientes. No operativo."


def test_analyze_returns_200_and_exact_top_keys(client):
    """POST /api/bot/analyze → 200 con conjuntos exactos de claves."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == TOP_KEYS


def test_analyze_literal_values_top_level(client):
    """Valores Literal en el nivel superior del response."""
    resp = client.post("/api/bot/analyze", json={"symbol": "ETH/USDT"})
    data = resp.json()
    assert data["data_source"] == "TEST_ONLY"
    assert data["result_type"] == "proposal"
    assert data["executable"] is False
    assert data["execution_available"] is False
    assert isinstance(data["warning"], str) and len(data["warning"]) > 0


def test_signal_exact_keys_and_flags(client):
    """signal: BotProposalSignal — conjuntos exactos + hereda flags."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    sig = resp.json()["signal"]
    assert set(sig.keys()) == SIGNAL_KEYS
    assert sig["data_source"] == "TEST_ONLY"
    assert sig["result_type"] == "proposal"
    assert sig["executable"] is False
    assert sig["symbol"] == "BTC/USDT"


def test_strategy_exact_keys(client):
    """strategy: TradingStrategy — conjunto exacto de claves."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    strat = resp.json()["strategy"]
    assert set(strat.keys()) == STRATEGY_KEYS


def test_warning_wire_exact(client):
    """warning: texto wire exacto del endpoint actual."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    data = resp.json()
    assert data["warning"] == WARNING_TEXT


def test_analysis_exact_keys_and_data_source(client):
    """analysis: BotProposalAnalysis — conjuntos exactos + data_source='TEST_ONLY'."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    ana = resp.json()["analysis"]
    assert set(ana.keys()) == frozenset((
        "timeframe", "keltner_ema", "macd_line", "macd_signal", "macd_histogram",
        "pivot_highs", "pivot_lows", "consolidation_zones", "data_source",
        "keltner_upper", "keltner_lower", "symbol",
    ))
    assert ana["data_source"] == "TEST_ONLY"
    assert ana["symbol"] == "BTC/USDT"


def test_bot_state_isolated_per_test(client):
    """El estado del bot se restablece por test; no persiste entre requests."""
    resp = client.post("/api/bot/analyze", json={"symbol": "BTC/USDT"})
    assert resp.status_code == 200
    from src.api.bot import _bot_state  # noqa: E402
    assert _bot_state["positions"] == []
    assert _bot_state["orders"] == []


def test_bot_proposal_signal_inherits_trading_signal():
    """BotProposalSignal es subtipo de TradingSignal y fija flags."""
    s = BotProposalSignal(
        timestamp_ms=1, symbol="X", signal_type=SignalType.HOLD,
        strength=SignalStrength.MODERATE, confidence=0.5, reason="test",
    )
    assert isinstance(s, TradingSignal)
    assert s.data_source == "TEST_ONLY"
    assert s.result_type == "proposal"
    assert s.executable is False


def test_bot_proposal_analysis_inherits_analysis_result():
    """BotProposalAnalysis es subtipo de AnalysisResult y fija data_source."""
    a = BotProposalAnalysis(symbol="X", timeframe="15m")
    assert isinstance(a, AnalysisResult)
    assert a.data_source == "TEST_ONLY"


def test_bot_proposal_response_rejects_invalid_literal():
    """BotProposalResponse rechaza valores no permitidos en campos Literal."""
    with pytest.raises(ValidationError):
        BotProposalResponse(
            data_source="LIVE",  # type: ignore
            result_type="proposal",
            executable=False,
            execution_available=False,
            warning="w",
            signal=BotProposalSignal(
                timestamp_ms=1, symbol="X", signal_type=SignalType.HOLD,
                strength=SignalStrength.MODERATE, confidence=0.5, reason="t",
            ),
            strategy={"name": "mock", "rules": [], "description": "", "max_position_size": 0, "stop_loss_pct": 0, "take_profit_pct": 0},
            analysis=BotProposalAnalysis(symbol="X", timeframe="15m"),
        )


def test_bot_proposal_response_accepts_valid_literal():
    """BotProposalResponse acepta valores permitidos en campos Literal."""
    resp = BotProposalResponse(
        data_source="TEST_ONLY",
        result_type="proposal",
        executable=False,
        execution_available=False,
        warning="ok",
        signal=BotProposalSignal(
            timestamp_ms=1, symbol="X", signal_type=SignalType.HOLD,
            strength=SignalStrength.MODERATE, confidence=0.5, reason="t",
        ),
        strategy={"name": "mock", "rules": [], "description": "", "max_position_size": 0.01, "stop_loss_pct": 0.001, "take_profit_pct": 0.001},
        analysis=BotProposalAnalysis(symbol="X", timeframe="15m"),
    )
    assert resp.data_source == "TEST_ONLY"
    assert resp.result_type == "proposal"
    assert resp.executable is False
    assert resp.execution_available is False
