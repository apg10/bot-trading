"""Cash-curve and execution-cost projections for PAPER data.

The functions in this module intentionally consume journal/read projections
only.  They do not mark fills applied, mutate balances, or introduce another
accounting ledger.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, Mapping, Sequence

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from src.models.paper_contracts import (
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    _exact_product,
    _exact_sum,
)
from src.models.paper_execution import PaperFillCandidateResult, PaperFillEvaluationRecord


MetricStatus = Literal["available", "insufficient_data"]
CostStatus = Literal["available", "insufficient_data"]


class CapitalCurvePoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp_ms: int = Field(..., ge=0)
    fill_id: str = Field(..., min_length=1)
    balance: Decimal
    peak_balance: Decimal
    drawdown: Decimal


class CapitalCurveReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["capital_curve_report.v1"] = Field(
        default="capital_curve_report.v1", frozen=True
    )
    account_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    quote_asset: str = Field(..., min_length=1)
    balance_kind: Literal["cash"] = Field(default="cash", frozen=True)
    status: MetricStatus
    initial_balance: Decimal
    points: tuple[CapitalCurvePoint, ...] = ()
    reason: str | None = None


class CostValue(BaseModel):
    """One known cost value.

    ``amount=0`` is a known zero.  Unknown commission is represented by a
    null ``CostValue`` on ``FillCostRow`` and is never converted to zero.
    ``value`` is accepted as an input alias for clients using metric naming.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    amount: Decimal | None = Field(validation_alias=AliasChoices("amount", "value"))
    status: CostStatus = "available"
    reason: str | None = None

    @property
    def value(self) -> Decimal | None:
        return self.amount

    @model_validator(mode="after")
    def _known_amount(self) -> "CostValue":
        if self.status == "available" and self.amount is None:
            raise ValueError("available cost requires an amount")
        if self.status == "insufficient_data" and self.amount is not None:
            raise ValueError("insufficient cost must not contain an amount")
        if self.status == "insufficient_data" and not self.reason:
            raise ValueError("insufficient cost requires a reason")
        return self


class FillCostRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fill_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    timestamp_ms: int = Field(..., ge=0)
    side: str = Field(..., min_length=1)
    qty: Decimal
    price: Decimal
    notional: Decimal
    commission: CostValue | None = None
    slippage: CostValue
    total_cost: CostValue | None = None
    cost_model: Literal["UNMODELLED", "MODELLED", "UNKNOWN"]


class CostBreakdownReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["cost_breakdown_report.v1"] = Field(
        default="cost_breakdown_report.v1", frozen=True
    )
    account_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
    quote_asset: str = Field(..., min_length=1)
    status: MetricStatus
    fills: tuple[FillCostRow, ...] = ()
    reason: str | None = None

    @property
    def rows(self) -> tuple[FillCostRow, ...]:
        return self.fills


class MetricsInconsistencyError(ValueError):
    """Persisted PAPER records disagree about accounting context."""


class MetricsInsufficientDataError(ValueError):
    """The requested projection cannot be derived from durable records."""


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fill(value: PaperFill | Mapping[str, Any]) -> PaperFill:
    if isinstance(value, PaperFill):
        return value
    # SQLite read projections intentionally carry journal metadata alongside
    # the immutable DTO (``kind``, account/session and application markers).
    # Validate only the DTO fields while retaining the raw mapping below for
    # the explicit ``applied`` marker.
    return PaperFill.model_validate(
        {name: value[name] for name in PaperFill.model_fields if name in value}
    )


def _order(value: PaperOrder | Mapping[str, Any]) -> PaperOrder:
    if isinstance(value, PaperOrder):
        return value
    return PaperOrder.model_validate(
        {name: value[name] for name in PaperOrder.model_fields if name in value}
    )


def _order_values(
    orders: Mapping[str, Mapping[str, Any] | PaperOrder]
    | Sequence[Mapping[str, Any] | PaperOrder],
) -> Sequence[Mapping[str, Any] | PaperOrder]:
    return tuple(orders.values()) if isinstance(orders, Mapping) else orders


def _evaluation_result(record: PaperFillEvaluationRecord) -> PaperFillCandidateResult:
    try:
        return PaperFillCandidateResult.model_validate_json(record.result_json)
    except Exception as exc:
        raise MetricsInconsistencyError(
            f"evaluation {record.evaluation_id} has invalid result data"
        ) from exc


def _indexed_context(
    fills: Sequence[Mapping[str, Any] | PaperFill],
    orders: Mapping[str, Mapping[str, Any] | PaperOrder]
    | Sequence[Mapping[str, Any] | PaperOrder],
    account_id: str,
    session_id: str,
) -> list[tuple[PaperFill, PaperOrder, bool]]:
    entries: list[tuple[PaperFill, PaperOrder, bool]] = []
    for raw in fills:
        fill = _fill(raw)
        raw_map = raw if isinstance(raw, Mapping) else None
        order_raw = (
            orders.get(fill.order_id)
            if isinstance(orders, Mapping)
            else next(
                (candidate for candidate in _order_values(orders)
                 if _order(candidate).order_id == fill.order_id),
                None,
            )
        )
        if order_raw is None:
            raise MetricsInconsistencyError(f"fill {fill.fill_id} references a missing order")
        order = _order(order_raw)
        if order.paper_account_id != account_id or order.paper_session_id != session_id:
            continue
        if order.order_id != fill.order_id or order.environment != "paper":
            raise MetricsInconsistencyError(f"fill {fill.fill_id} has inconsistent order context")
        # Typed PaperFill inputs represent the caller's already-applied set;
        # SQLite read projections carry the explicit application marker.
        applied = True if raw_map is None else bool(raw_map.get("applied", False))
        entries.append((fill, order, applied))
    entries.sort(key=lambda item: (item[0].timestamp_ms, item[0].fill_id))
    return entries


def _quote_context(
    entries: Sequence[tuple[PaperFill, PaperOrder, bool]],
    quote_asset: str,
    account_id: str,
    orders: Mapping[str, Mapping[str, Any] | PaperOrder]
    | Sequence[Mapping[str, Any] | PaperOrder]
    | None = None,
) -> None:
    quotes = {
        order.symbol.split("/", 1)[1]
        for _, order, _ in entries
        if "/" in order.symbol and order.symbol.split("/", 1)[1]
    }
    if orders is not None:
        for raw_order in _order_values(orders):
            order = _order(raw_order)
            if order.paper_account_id == account_id:
                if "/" not in order.symbol:
                    raise MetricsInconsistencyError("order symbol has no quote asset")
                quotes.add(order.symbol.split("/", 1)[1])
    if any("/" not in order.symbol for _, order, _ in entries):
        raise MetricsInconsistencyError("order symbol has no quote asset")
    if len(quotes) > 1 or (quotes and quote_asset not in quotes):
        raise MetricsInconsistencyError("mixed or mismatched quote assets")


def build_capital_curve(
    account_id: str,
    session_id: str,
    quote_asset: str,
    initial_balance: Decimal,
    fills: Sequence[Mapping[str, Any] | PaperFill],
    orders: Mapping[str, Mapping[str, Any] | PaperOrder]
    | Sequence[Mapping[str, Any] | PaperOrder],
) -> CapitalCurveReport:
    """Derive cash after each already-applied fill from opening cash."""
    entries = _indexed_context(fills, orders, account_id, session_id)
    _quote_context(entries, quote_asset, account_id, orders)
    applied = [entry for entry in entries if entry[2]]
    if not applied:
        return CapitalCurveReport(
            account_id=account_id,
            session_id=session_id,
            quote_asset=quote_asset,
            status="insufficient_data",
            initial_balance=initial_balance,
            reason="no applied fills are available",
        )

    balance = initial_balance
    peak = initial_balance
    points: list[CapitalCurvePoint] = []
    for fill, order, _ in applied:
        if fill.commission is None:
            raise MetricsInsufficientDataError(
                f"fill {fill.fill_id} has unknown commission for cash derivation"
            )
        if fill.commission.asset != quote_asset:
            raise MetricsInconsistencyError(
                f"fill {fill.fill_id} commission is not denominated in {quote_asset}"
            )
        notional = _exact_product(fill.qty, fill.price)
        commission = fill.commission.amount
        change = _exact_sum((notional, commission))
        if order.side == PaperOrderSide.BUY:
            balance = _exact_sum((balance, change.copy_negate()))
        elif order.side == PaperOrderSide.SELL:
            balance = _exact_sum((balance, _exact_sum((notional, commission.copy_negate()))))
        else:  # pragma: no cover - PaperOrder currently has only BUY/SELL
            raise MetricsInconsistencyError(f"fill {fill.fill_id} has unsupported side")
        if balance > peak:
            peak = balance
        points.append(
            CapitalCurvePoint(
                timestamp_ms=fill.timestamp_ms,
                fill_id=fill.fill_id,
                balance=balance,
                peak_balance=peak,
                drawdown=_exact_sum((peak, balance.copy_negate())),
            )
        )
    return CapitalCurveReport(
        account_id=account_id,
        session_id=session_id,
        quote_asset=quote_asset,
        status="available",
        initial_balance=initial_balance,
        points=tuple(points),
    )


def build_cost_breakdown(
    account_id: str,
    session_id: str,
    quote_asset: str,
    fills: Sequence[Mapping[str, Any] | PaperFill],
    orders: Mapping[str, Mapping[str, Any] | PaperOrder]
    | Sequence[Mapping[str, Any] | PaperOrder],
    evaluations: Sequence[PaperFillEvaluationRecord],
) -> CostBreakdownReport:
    """Derive per-fill commission/slippage without inventing missing costs."""
    entries = _indexed_context(fills, orders, account_id, session_id)
    _quote_context(entries, quote_asset, account_id, orders)
    applied = [entry for entry in entries if entry[2]]
    if not applied:
        return CostBreakdownReport(
            account_id=account_id,
            session_id=session_id,
            quote_asset=quote_asset,
            status="insufficient_data",
            reason="no applied fills are available",
        )

    by_fill: dict[str, tuple[PaperFillEvaluationRecord, PaperFillCandidateResult]] = {}
    for evaluation in evaluations:
        if evaluation.account_id != account_id or evaluation.paper_session_id != session_id:
            continue
        if evaluation.fill_candidate_fill_id:
            by_fill[evaluation.fill_candidate_fill_id] = (evaluation, _evaluation_result(evaluation))

    rows: list[FillCostRow] = []
    for fill, order, _ in applied:
        notional = _exact_product(fill.qty, fill.price)
        commission = (
            None
            if fill.commission is None
            else CostValue(amount=fill.commission.amount, status="available")
        )
        if fill.commission is not None and fill.commission.asset != quote_asset:
            raise MetricsInconsistencyError(
                f"fill {fill.fill_id} commission is not denominated in {quote_asset}"
            )
        evaluation_pair = by_fill.get(fill.fill_id)
        result = evaluation_pair[1] if evaluation_pair else None
        model = result.cost_model if result is not None else "UNKNOWN"
        slippage: CostValue
        if model == "UNMODELLED":
            slippage = CostValue(amount=Decimal("0"), status="available")
        elif model == "MODELLED" and result is not None and result.reference_price is not None:
            reference_delta = _exact_sum((fill.price, result.reference_price.copy_negate()))
            slippage = CostValue(
                amount=_exact_product(fill.qty, reference_delta), status="available"
            )
        else:
            slippage = CostValue(
                amount=None,
                status="insufficient_data",
                reason="reference price is unavailable",
            )
        total = None
        if commission is not None and slippage.status == "available":
            total = CostValue(
                amount=_exact_sum((commission.amount, slippage.amount)), status="available"
            )
        rows.append(
            FillCostRow(
                fill_id=fill.fill_id,
                order_id=fill.order_id,
                timestamp_ms=fill.timestamp_ms,
                side=order.side.value,
                qty=fill.qty,
                price=fill.price,
                notional=notional,
                commission=commission,
                slippage=slippage,
                total_cost=total,
                cost_model=model,
            )
        )
    return CostBreakdownReport(
        account_id=account_id,
        session_id=session_id,
        quote_asset=quote_asset,
        status="available",
        fills=tuple(rows),
    )


# Readable aliases for callers that use "derive" terminology.
derive_capital_curve = build_capital_curve
derive_cost_breakdown = build_cost_breakdown


__all__ = [
    "CapitalCurvePoint", "CapitalCurveReport", "CostValue", "FillCostRow",
    "CostBreakdownReport", "MetricsInconsistencyError", "MetricsInsufficientDataError",
    "build_capital_curve", "build_cost_breakdown", "derive_capital_curve",
    "derive_cost_breakdown",
]
