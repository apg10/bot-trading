"""HTTP contract tests for the read-only laboratory API."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.laboratory import router
from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentVariant,
    PeriodWindow,
    RunAttempt,
)
from src.models.audit_contract import CandleSnapshot, CausalDecision
from src.models.normalized_experiment import NormalizedExperimentResult, NormalizedMetric
from src.models.paper_contracts import (
    AssetBalance,
    Commission,
    PaperAccountOpening,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderType,
    StrategyRef,
)
from src.models.paper_execution import PaperFillCandidateResult, PaperFillEvaluationRecord
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig
from src.persistence.sqlite_store import SQLiteStore


def _run(coro):
    return asyncio.run(coro)


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_ref=StrategyRef(strategy_id="strategy", strategy_version="v1", decision_id=None),
        indicator_instances=(
            IndicatorInstance(
                instance_id="ema",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="ema_period", value=20),
                    ParameterEntry(name="atr_period", value=14),
                    ParameterEntry(name="multiplier", value=2.0),
                ),
            ),
        ),
    )


def _experiment(experiment_id: str, variants: tuple[str, ...]) -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        hypothesis=f"hypothesis-{experiment_id}",
        planned_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        variants=tuple(
            ExperimentVariant(
                variant_id=variant,
                strategy_config=_strategy(),
                dataset_ref="dataset/laboratory",
            )
            for variant in variants
        ),
    )


def _completed_run(experiment_id: str, variant: str) -> RunAttempt:
    return RunAttempt(
        run_id=f"run-{experiment_id}-{variant}",
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        variant_id=variant,
        status="completed",
        started_at_ms=1,
        finished_at_ms=100,
        seed=7,
        dataset_ref_used="dataset/laboratory",
        code_ref="code/laboratory",
        engine_ref="engine/laboratory",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        result_id=f"result-{experiment_id}-{variant}",
    )


def _result(experiment_id: str, variant: str, value: str) -> NormalizedExperimentResult:
    return NormalizedExperimentResult(
        result_id=f"result-{experiment_id}-{variant}",
        run_id=f"run-{experiment_id}-{variant}",
        origin_id=f"origin-{experiment_id}-{variant}",
        result_version="v1",
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        variant_id=variant,
        dataset_ref="dataset/laboratory",
        strategy_config=_strategy(),
        code_ref="code/laboratory",
        engine_ref="engine/laboratory",
        risk_ref="risk/opaque",
        costs_ref="costs/opaque",
        exposure_ref="exposure/opaque",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=7,
        metrics=(
            NormalizedMetric(
                metric_id="return",
                unit="percent",
                status="available",
                value=Decimal(value),
            ),
            NormalizedMetric(
                metric_id="drawdown",
                unit="USD",
                status="undefined",
                reason="not provided",
            ),
        ),
    )


def _store_with_results(
    tmp_path: Path,
    experiment_id: str = "exp-lab",
    variants: tuple[str, ...] = ("A", "B", "C"),
) -> SQLiteStore:
    store = SQLiteStore(tmp_path / f"{experiment_id}.db")
    _run(store.initialize())
    _run(store.register_experiment(_experiment(experiment_id, variants)))
    for variant in variants:
        pending = RunAttempt(
            run_id=f"run-{experiment_id}-{variant}",
            experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
            variant_id=variant,
            status="pending",
            seed=None,
        )
        _run(store.create_run_attempt(pending))
        completed = _completed_run(experiment_id, variant)
        conn = sqlite3.connect(str(tmp_path / f"{experiment_id}.db"))
        conn.execute(
            "UPDATE run_attempts SET status=?, run_json=? WHERE run_id=?",
            (completed.status, json.dumps(completed.model_dump(mode="python")), completed.run_id),
        )
        conn.commit()
        conn.close()
        value = str(len(variants)) + ".12345678901234567890"
        _run(store.register_normalized_experiment_result(_result(experiment_id, variant, value)))
    return store


def _client(store: SQLiteStore | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    if store is not None:
        app.state._sqlite_store = store
    return TestClient(app)


def _metrics_store(
    monkeypatch,
    tmp_path: Path,
    *,
    account_id: str = "acct",
    session_id: str = "session",
    orders: tuple[PaperOrder, ...] = (),
    fills: tuple[PaperFill, ...] = (),
    evaluations: tuple[PaperFillEvaluationRecord, ...] = (),
) -> SQLiteStore:
    """Use a real store identity while isolating the endpoint from SQL setup."""
    store = SQLiteStore(tmp_path / "metrics.db")
    opening = PaperAccountOpening(
        account_id=account_id,
        balances=(AssetBalance(asset="USDT", total=Decimal("100"), available=Decimal("100"), reserved=Decimal("0")),),
    )
    order_rows = []
    for order in orders:
        row = order.model_dump(mode="python")
        row.update(kind="order", account_id=account_id, session_id=session_id)
        order_rows.append(row)
    fill_rows = []
    for fill in fills:
        row = fill.model_dump(mode="python")
        row.update(kind="fill", account_id=account_id, session_id=session_id, applied=True, applied_at_ms="1")
        fill_rows.append(row)

    async def load_opening(requested_account: str):
        return opening if requested_account == account_id else None

    async def get_orders(_account: str, _session: str):
        return order_rows

    async def get_fills(_account: str, _session: str):
        return fill_rows

    async def get_evaluations(_account: str, _session: str):
        return list(evaluations)

    monkeypatch.setattr(store, "load_paper_account_opening", load_opening)
    monkeypatch.setattr(store, "get_paper_orders", get_orders)
    monkeypatch.setattr(store, "get_paper_fills", get_fills)
    monkeypatch.setattr(store, "get_paper_fill_evaluations", get_evaluations)
    return store


def _metrics_order(order_id: str, *, symbol: str = "BTC/USDT") -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct",
        paper_session_id="session",
        symbol=symbol,
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1"),
        price=Decimal("10"),
        strategy_ref=StrategyRef(strategy_id="strategy", strategy_version="v1", decision_id=None),
        timestamp_ms=1,
    )


def _metrics_fill(fill_id: str, order_id: str) -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        qty=Decimal("1"),
        price=Decimal("10"),
        timestamp_ms=1,
        commission=Commission(amount=Decimal("0.10"), asset="USDT"),
    )


def _metrics_evaluation(fill_id: str, order_id: str) -> PaperFillEvaluationRecord:
    result = PaperFillCandidateResult(status_code="fill_candidate", cost_model="UNMODELLED")
    return PaperFillEvaluationRecord(
        evaluation_id=f"evaluation-{fill_id}",
        order_id=order_id,
        account_id="acct",
        paper_session_id="session",
        environment="paper",
        status_code="fill_candidate",
        result_json=result.model_dump_json(),
        evaluated_at_ms=1,
        fill_candidate_fill_id=fill_id,
    )


def _audit_pair(account_id: str = "acct", session_id: str = "session"):
    decision = CausalDecision(
        account_id=account_id, paper_session_id=session_id, decision_id="decision-1",
        signal_id="signal-1", segment_id="segment-1", symbol="BTC/USDT",
        strategy="TEST_ONLY", timestamp=1, candle_index=0, side="BUY", reason="test",
    )
    candle = CandleSnapshot(
        account_id=account_id, paper_session_id=session_id, decision_id="decision-1",
        symbol="BTC/USDT", timestamp=1, candle_index=0, open=Decimal("9"),
        high=Decimal("11"), low=Decimal("8"), close=Decimal("10"), volume=Decimal("2"),
    )
    return decision, candle


def test_inspector_success(monkeypatch, tmp_path: Path) -> None:
    order = _metrics_order("order-1").model_copy(update={"decision_id": "decision-1"})
    fill = _metrics_fill("fill-1", "order-1")
    store = _metrics_store(monkeypatch, tmp_path, orders=(order,), fills=(fill,))
    decision, candle = _audit_pair()
    async def audits(_account: str, _session: str):
        return [(decision, candle)]
    async def lifecycle(_account: str, _session: str):
        return {"state": "running"}
    monkeypatch.setattr(store, "load_audit_decisions", audits)
    monkeypatch.setattr(store, "get_paper_lifecycle_snapshot", lifecycle)
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/inspector/acct/session")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "available"
        assert data["decisions"][0]["candle"]["decision_id"] == "decision-1"
        assert data["decisions"][0]["orders"][0]["order_id"] == "order-1"
        assert data["decisions"][0]["fills"][0]["fill_id"] == "fill-1"
    finally:
        _run(store.close())


def test_inspector_404_account_not_found(monkeypatch, tmp_path: Path) -> None:
    store = _metrics_store(monkeypatch, tmp_path)
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/inspector/missing/session")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "LABORATORY_ACCOUNT_NOT_FOUND"
    finally:
        _run(store.close())


def test_inspector_404_session_not_found(monkeypatch, tmp_path: Path) -> None:
    store = _metrics_store(monkeypatch, tmp_path)
    async def no_lifecycle(_account: str, _session: str):
        return None
    async def no_audits(_account: str, _session: str):
        return []
    monkeypatch.setattr(store, "get_paper_lifecycle_snapshot", no_lifecycle)
    monkeypatch.setattr(store, "load_audit_decisions", no_audits)
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/inspector/acct/missing")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "LABORATORY_SESSION_NOT_FOUND"
    finally:
        _run(store.close())


def test_inspector_insufficient_data(monkeypatch, tmp_path: Path) -> None:
    store = _metrics_store(monkeypatch, tmp_path)
    async def no_audits(_account: str, _session: str):
        return []
    async def lifecycle(_account: str, _session: str):
        return {"state": "running"}
    monkeypatch.setattr(store, "load_audit_decisions", no_audits)
    monkeypatch.setattr(store, "get_paper_lifecycle_snapshot", lifecycle)
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/inspector/acct/session")
        assert response.status_code == 200
        assert response.json()["status"] == "insufficient_data"
        assert response.json()["reason"]
    finally:
        _run(store.close())


def test_inspector_session_isolation(monkeypatch, tmp_path: Path) -> None:
    store = _metrics_store(monkeypatch, tmp_path)
    decision_a, candle_a = _audit_pair(session_id="session-a")
    decision_b, candle_b = _audit_pair(session_id="session-b")
    async def audits(_account: str, session: str):
        return [(decision_a, candle_a)] if session == "session-a" else [(decision_b, candle_b)]
    async def lifecycle(_account: str, _session: str):
        return {"state": "running"}
    monkeypatch.setattr(store, "load_audit_decisions", audits)
    monkeypatch.setattr(store, "get_paper_lifecycle_snapshot", lifecycle)
    try:
        with _client(store) as client:
            a = client.get("/api/laboratory/inspector/acct/session-a").json()
            b = client.get("/api/laboratory/inspector/acct/session-b").json()
        assert [row["decision_id"] for row in a["decisions"]] == ["decision-1"]
        assert [row["paper_session_id"] for row in a["decisions"]] == ["session-a"]
        assert [row["paper_session_id"] for row in b["decisions"]] == ["session-b"]
    finally:
        _run(store.close())


def test_single_result_preserves_decimal_unit_and_opaque_fields(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, variants=("A",))
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/results/result-exp-lab-A")
        assert response.status_code == 200
        data = response.json()
        metric = next(item for item in data["metrics"] if item["metric_id"] == "return")
        assert metric["value"] == "1.12345678901234567890"
        assert metric["unit"] == "percent"
        assert data["costs_ref"] == "costs/opaque"
        assert data["exposure_ref"] == "exposure/opaque"
        assert data["effective_periods"][0]["purpose"] == "design"
    finally:
        _run(store.close())


def test_comparison_success_preserves_aggregates_and_unit(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path)
    try:
        with _client(store) as client:
            response = client.get(
                "/api/laboratory/comparisons/exp-lab/v1",
                params={"comparison_id": "cmp-1", "comparison_version": "v1"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data["comparison"]["normalized_results"]) == 3
        aggregate = next(item for item in data["aggregates"] if item["metric_id"] == "return")
        assert aggregate["unit"] == "percent"
        assert aggregate["mean"] == "3.12345678901234567890"
    finally:
        _run(store.close())


def test_missing_and_insufficient_comparisons_have_explicit_statuses(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, variants=("A",))
    try:
        with _client(store) as client:
            assert client.get("/api/laboratory/results/missing").status_code == 404
            assert client.get(
                "/api/laboratory/comparisons/no-results/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            ).status_code == 404
            assert client.get(
                "/api/laboratory/comparisons/exp-lab/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            ).status_code == 409
    finally:
        _run(store.close())


def test_store_unavailable_is_503() -> None:
    with _client(None) as client:
        response = client.get("/api/laboratory/results/anything")
    assert response.status_code == 503


def test_comparison_isolated_by_experiment_reference(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, experiment_id="exp-a")
    _run(store.register_experiment(_experiment("exp-b", ("A", "B", "C"))))
    try:
        with _client(store) as client:
            response = client.get(
                "/api/laboratory/comparisons/exp-b/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            )
        assert response.status_code == 404
    finally:
        _run(store.close())


def test_capital_curve_and_cost_breakdown_success_paths(tmp_path: Path, monkeypatch) -> None:
    order = _metrics_order("order-metrics")
    fill = _metrics_fill("fill-metrics", order.order_id)
    store = _metrics_store(
        monkeypatch,
        tmp_path,
        orders=(order,),
        fills=(fill,),
        evaluations=(_metrics_evaluation(fill.fill_id, order.order_id),),
    )
    with _client(store) as client:
        curve = client.get("/api/laboratory/capital-curves/acct/session")
        costs = client.get("/api/laboratory/cost-breakdowns/acct/session")
    assert curve.status_code == 200
    assert curve.json()["points"][0]["balance"] == "89.90"
    assert curve.json()["points"][0]["drawdown"] == "10.10"
    assert costs.status_code == 200
    row = costs.json()["fills"][0]
    assert row["commission"]["amount"] == "0.10"
    assert row["slippage"]["amount"] == "0"
    assert row["total_cost"]["amount"] == "0.10"


def test_metrics_api_returns_404_for_missing_account_or_session(tmp_path: Path, monkeypatch) -> None:
    store = _metrics_store(monkeypatch, tmp_path)
    with _client(store) as client:
        assert client.get("/api/laboratory/capital-curves/missing/session").status_code == 404
        assert client.get("/api/laboratory/cost-breakdowns/acct/missing").status_code == 404


def test_metrics_api_returns_409_for_mixed_quote_context(tmp_path: Path, monkeypatch) -> None:
    first = _metrics_order("order-usdt", symbol="BTC/USDT")
    second = _metrics_order("order-usd", symbol="ETH/USD")
    store = _metrics_store(
        monkeypatch,
        tmp_path,
        orders=(first, second),
        fills=(_metrics_fill("fill-usdt", first.order_id),),
    )
    with _client(store) as client:
        response = client.get("/api/laboratory/capital-curves/acct/session")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "LABORATORY_DATA_INCONSISTENCY"


def test_metrics_api_returns_503_when_store_is_unavailable() -> None:
    with _client(None) as client:
        assert client.get("/api/laboratory/capital-curves/acct/session").status_code == 503
        assert client.get("/api/laboratory/cost-breakdowns/acct/session").status_code == 503
