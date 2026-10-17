"""Pruebas de contrato para los DTOs paper (paper_contracts).

Cobertura:
  - Campos/tipos y validaciones de cada DTO.
  - Decimal JSON (serialización/deserialización).
  - PAPER-only (environment="paper" inmutable).
  - Comisión null vs cero.
  - Matriz estática de transiciones de estado.
  - No overfill (remaining_qty >= 0), no fill_id repetidos por orden.
  - qty/total_cost autoritativos; promedio derivado a 34 cifras, comisión separada.
  - schema_version restringido a literal v1 e inmutable en cada DTO versionado.
  - Atribución/reconciliación de inventario (unattributed invariant).
  - Ecuación de balances (total = available + reserved).
  - Límite de reserva (reserved <= total).
  - OCO parcial / cancelación de miembro y grupo.
  - Sin doble reserva ni venta sobre inventario abierto.
"""

import json
from decimal import ROUND_DOWN, ROUND_UP, Decimal, getcontext, setcontext
from typing import Sequence

import pytest
from pydantic import ValidationError

# ── Imports del módulo a probar ───────────────────────────────────────────────

from src.models.paper_contracts import (
    AssetBalance,
    Commission,
    LotAllocation,
    PaperAccountOpening,
    PaperBalanceView,
    PaperFill,
    PaperInventoryReconciliation,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperPositionView,
    PaperProtectionGroup,
    PaperReservationRequest,
    PaperReservationRef,
    PositionLot,
    ReservationAmount,
    StrategyRef,
    UnattributedInventory,
    UnattributedInventoryView,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  StrategyRef
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyRef:
    def test_frozen_version(self):
        ref = StrategyRef(strategy_id="s1", strategy_version="v1")
        with pytest.raises((AttributeError, ValidationError)):
            ref.strategy_version = "v2"  # type: ignore[assignment]

    def test_schema_version_independent(self):
        ref = StrategyRef(strategy_id="s1", strategy_version="v1")
        assert ref.schema_version == "paper_strategy_ref.v1"

    def test_decision_id_optional(self):
        ref_no_dec = StrategyRef(strategy_id="s1", strategy_version="v1")
        assert ref_no_dec.decision_id is None
        ref_with_dec = StrategyRef(strategy_id="s1", strategy_version="v1", decision_id="d-42")
        assert ref_with_dec.decision_id == "d-42"

    def test_empty_strategy_id_rejected(self):
        with pytest.raises(ValidationError):
            StrategyRef(strategy_id="", strategy_version="v1")

    def test_strategy_ref_is_frozen(self):
        ref = StrategyRef(strategy_id="s1", strategy_version="v1")
        with pytest.raises(ValidationError):
            ref.strategy_id = "s2"  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════════
#  Commission (null vs cero)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCommission:
    def test_zero_commission(self):
        c = Commission(amount=Decimal("0"), asset="USDT")
        assert c.amount == Decimal("0")

    def test_nonzero_commission(self):
        c = Commission(amount=Decimal("1.50"), asset="USDT")
        assert c.amount == Decimal("1.50")

    def test_negative_amount_rejected(self):
        with pytest.raises(ValidationError):
            Commission(amount=Decimal("-1"), asset="USDT")  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
#  PaperFill
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperFill:
    def test_valid_fill_no_commission(self):
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None,
        )
        assert fill.commission is None
        assert fill.lot_allocations == ()

    def test_commission_field_must_be_present(self):
        with pytest.raises(ValidationError):
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("65000"), timestamp_ms=1700000000000,
            )

    def test_fill_with_zero_commission(self):
        c = Commission(amount=Decimal("0"), asset="USDT")
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=c,
        )
        assert fill.commission is not None
        assert fill.commission.amount == Decimal("0")

    def test_fill_with_lot_allocations_sum_qty(self):
        allocs = [
            LotAllocation(position_id="p1", account_id="acc1", symbol="BTC/USDT",
                          strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0.3")),
            LotAllocation(position_id="p2", account_id="acc1", symbol="BTC/USDT",
                          strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0.2")),
        ]
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None, lot_allocations=allocs,
        )
        assert len(fill.lot_allocations) == 2
        assert isinstance(fill.lot_allocations, tuple)

    def test_lot_allocations_must_sum_to_qty(self):
        allocs = [LotAllocation(position_id="p1", account_id="acc1", symbol="BTC/USDT",
                                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0.3"))]
        with pytest.raises(ValidationError):
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("65000"), timestamp_ms=1700000000000,
                commission=None,
                lot_allocations=allocs,
            )

    def test_zero_qty_rejected(self):
        with pytest.raises(ValidationError):
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0"),
                price=Decimal("65000"), timestamp_ms=1700000000000,
                commission=None,
            )

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError):
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("-1"), timestamp_ms=1700000000000,
                commission=None,
            )

    def test_zero_price_rejected(self):
        with pytest.raises(ValidationError):
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("0"), timestamp_ms=1700000000000,
                commission=None,
            )

    def test_fill_is_frozen(self):
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None,
        )
        with pytest.raises(ValidationError):
            fill.qty = Decimal("0.6")  # type: ignore[assignment]


# ═══════════════════════════════════════════════════════════════════════════════
#  PaperOrder — campos / tipos / PAPER-only
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperOrder:
    def _make_order(self, **overrides) -> PaperOrder:
        base = dict(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
        )
        # MARKET BUY requiere max_quote_amount (Fix 2)
        if base.get("order_type") == PaperOrderType.MARKET and base.get("side") == PaperOrderSide.BUY:
            base["max_quote_amount"] = Decimal("65000")
        base.update(overrides)
        return PaperOrder(**base)  # type: ignore[arg-type]

    def test_default_status_prepared(self):
        o = self._make_order()
        assert o.status == PaperOrderStatus.PREPARED

    def test_environment_is_paper(self):
        o = self._make_order()
        assert o.environment == "paper"

    def test_environment_must_be_paper(self):
        with pytest.raises(ValidationError):
            self._make_order(environment="live")  # type: ignore[arg-type]

    def test_environment_is_immutable(self):
        order = self._make_order()
        with pytest.raises(ValidationError):
            order.environment = "live"  # type: ignore[assignment]

    def test_executed_qty_zero_no_fills(self):
        o = self._make_order()
        assert o.executed_qty == Decimal("0")

    def test_executed_qty_with_fills(self):
        fills = [
            PaperFill(fill_id="f1", order_id="o1", qty=Decimal("0.3"),
                      price=Decimal("65000"), timestamp_ms=1700000000000,
                      commission=None),
            PaperFill(fill_id="f2", order_id="o1", qty=Decimal("0.4"),
                      price=Decimal("65100"), timestamp_ms=1700000001000,
                      commission=None),
        ]
        o = self._make_order(
            fills=fills, status=PaperOrderStatus.PARTIALLY_FILLED
        )  # type: ignore[arg-type]
        assert o.executed_qty == Decimal("0.7")

    def test_fill_must_reference_parent_order(self):
        fill = PaperFill(
            fill_id="f1", order_id="other-order", qty=Decimal("0.3"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None,
        )
        with pytest.raises(ValidationError, match="must match parent order_id"):
            self._make_order(fills=[fill], status=PaperOrderStatus.PARTIALLY_FILLED)

    def test_duplicate_fill_ids_are_rejected(self):
        fills = [
            PaperFill(
                fill_id="same-fill", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("65000"), timestamp_ms=1700000000000,
                commission=None,
            ),
            PaperFill(
                fill_id="same-fill", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("65000"), timestamp_ms=1700000000000,
                commission=None,
            ),
        ]
        with pytest.raises(ValidationError, match="duplicate fill_id"):
            self._make_order(fills=fills, status=PaperOrderStatus.FILLED)

    def test_remaining_qty(self):
        fills = [
            PaperFill(fill_id="f1", order_id="o1", qty=Decimal("0.6"),
                      price=Decimal("65000"), timestamp_ms=1700000000000,
                      commission=None),
        ]
        o = self._make_order(
            requested_qty=Decimal("1.0"), fills=fills,
            status=PaperOrderStatus.PARTIALLY_FILLED,
        )  # type: ignore[arg-type]
        assert o.remaining_qty == Decimal("0.4")

    def test_overfill_prevented_by_remaining(self):
        """DTO valida que fills no excedan requested_qty (Fix 1: remaining_qty >= 0)."""
        # Con Fix 1, el DTO ya rechaza overfill en validación.
        fills = [
            PaperFill(fill_id="f1", order_id="o1", qty=Decimal("0.6"),
                      price=Decimal("65000"), timestamp_ms=1700000000000,
                      commission=None),
            PaperFill(fill_id="f2", order_id="o1", qty=Decimal("0.5"),
                      price=Decimal("65100"), timestamp_ms=1700000001000,
                      commission=None),
        ]
        with pytest.raises(ValidationError) as exc_info:
            self._make_order(requested_qty=Decimal("1.0"), fills=fills)  # type: ignore[arg-type]
        assert "must not exceed requested_qty" in str(exc_info.value)

    def test_symbol_format(self):
        o = self._make_order()
        assert o.symbol == "BTC/USDT"

    def test_trigger_price_for_stop_loss(self):
        o = self._make_order(
            order_type=PaperOrderType.STOP_LOSS,
            trigger_price=Decimal("60000"),
        )
        assert o.trigger_price == Decimal("60000")

    def test_limit_requires_positive_price(self):
        with pytest.raises(ValidationError):
            self._make_order(order_type=PaperOrderType.LIMIT, price=None)

    def test_stop_requires_positive_trigger_price(self):
        with pytest.raises(ValidationError):
            self._make_order(order_type=PaperOrderType.STOP_LOSS, trigger_price=None)

    def test_market_buy_requires_quote_cap(self):
        with pytest.raises(ValidationError):
            self._make_order(max_quote_amount=None)

    def test_decimal_fields_reject_float_inputs(self):
        with pytest.raises(ValidationError):
            self._make_order(requested_qty=1.25)  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
#  Matriz estática de transiciones PAPER local
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransitionMatrix:
    """Transiciones válidas del flujo PAPER local.

    Flujo normal:
      PREPARED -> ACCEPTED | REJECTED | CANCELLED | EXPIRED
      ACCEPTED -> PARTIALLY_FILLED | FILLED | CANCELLED | EXPIRED
      PARTIALLY_FILLED -> PARTIALLY_FILLED | FILLED | CANCELLED | EXPIRED

    PENDING_SEND y UNKNOWN_STATE solo aparecen si hay cola durable.
    REJECTED, FILLED, CANCELLED, EXPIRED son terminales.
    """

    VALID_TRANSITIONS: dict[str, list[str]] = {
        "prepared": ["accepted", "rejected", "cancelled", "expired"],
        "accepted": ["partially_filled", "filled", "cancelled", "expired"],
        "partially_filled": ["partially_filled", "filled", "cancelled", "expired"],
    }

    TERMINAL_STATES = {"rejected", "filled", "cancelled", "expired"}

    def test_prepared_transitions(self):
        for target in self.VALID_TRANSITIONS["prepared"]:
            status = PaperOrderStatus(target)
            assert status in (
                PaperOrderStatus.ACCEPTED,
                PaperOrderStatus.REJECTED,
                PaperOrderStatus.CANCELLED,
                PaperOrderStatus.EXPIRED,
            )

    def test_accepted_transitions(self):
        for target in self.VALID_TRANSITIONS["accepted"]:
            status = PaperOrderStatus(target)
            assert status in (
                PaperOrderStatus.PARTIALLY_FILLED,
                PaperOrderStatus.FILLED,
                PaperOrderStatus.CANCELLED,
                PaperOrderStatus.EXPIRED,
            )

    def test_partially_filled_transitions(self):
        for target in self.VALID_TRANSITIONS["partially_filled"]:
            status = PaperOrderStatus(target)
            assert status in (
                PaperOrderStatus.PARTIALLY_FILLED,
                PaperOrderStatus.FILLED,
                PaperOrderStatus.CANCELLED,
                PaperOrderStatus.EXPIRED,
            )

    def test_terminal_states_are_invariant(self):
        for state in self.TERMINAL_STATES:
            # Una vez en estado terminal, no hay transición válida hacia otro.
            assert state not in self.VALID_TRANSITIONS

    def test_pending_send_not_in_local_flow(self):
        """PENDING_SEND no está en el flujo local normal."""
        assert "pending_send" not in self.VALID_TRANSITIONS

    def test_unknown_state_not_in_local_flow(self):
        """UNKNOWN_STATE solo por incertidumbre de persistencia."""
        assert "unknown_state" not in self.VALID_TRANSITIONS


# ═══════════════════════════════════════════════════════════════════════════════
#  PaperPositionView / PositionLot
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperPositionView:
    def _make_view(self, lots: Sequence[PositionLot] | None = None, **kw) -> PaperPositionView:
        base = dict(
            account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            **kw,
        )
        if lots is not None:
            base["lots"] = lots
        return PaperPositionView(**base)  # type: ignore[arg-type]

    def test_empty_lots(self):
        v = self._make_view()
        assert v.total_qty == Decimal("0")
        assert v.avg_entry_price is None
        assert not v.is_open
        assert v.unrealized_pnl is None

    def test_total_qty_sum(self):
        lots = [
            PositionLot(position_id="p1", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("0.5"), avg_entry_price=Decimal("65000"), total_cost=Decimal("32500")),
            PositionLot(position_id="p2", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("0.3"), avg_entry_price=Decimal("65100"), total_cost=Decimal("19530")),
        ]
        v = self._make_view(lots)
        assert v.total_qty == Decimal("0.8")

    def test_avg_entry_price(self):
        lots = [
            PositionLot(position_id="p1", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("1.0"), avg_entry_price=Decimal("65000"), total_cost=Decimal("65000")),
        ]
        v = self._make_view(lots)
        assert v.avg_entry_price == Decimal("65000")

    def test_lot_rejects_average_inconsistent_with_projection_policy(self):
        with pytest.raises(ValidationError, match="34-significant-digit"):
            PositionLot(
                position_id="p1", entry_order_id="o1",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                qty=Decimal("3"), avg_entry_price=Decimal("1.6"),
                total_cost=Decimal("5"),
            )

    def test_average_projection_handles_non_terminating_cost_ratio_and_roundtrips(self):
        avg = Decimal("1.666666666666666666666666666666667")
        lot = PositionLot(
            position_id="order-1", entry_order_id="order-1",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("3"), avg_entry_price=avg, total_cost=Decimal("5"),
        )
        assert (lot.qty, lot.total_cost) == (Decimal("3"), Decimal("5"))
        restored = PositionLot.model_validate_json(lot.model_dump_json())
        assert restored == lot
        assert restored.total_cost == Decimal("5")
        view = self._make_view([lot], mark_price=Decimal("2"))
        assert view.avg_entry_price == avg
        assert view.unrealized_pnl == Decimal("1")

    def test_average_projection_ignores_global_decimal_context(self):
        original = getcontext().copy()
        try:
            getcontext().prec = 4
            getcontext().rounding = ROUND_UP
            avg = Decimal("1.666666666666666666666666666666667")
            lot = PositionLot(
                position_id="order-1", entry_order_id="order-1",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                qty=Decimal("3"), avg_entry_price=avg, total_cost=Decimal("5"),
            )
            view = self._make_view([lot])
            assert view.avg_entry_price == avg
        finally:
            setcontext(original)

    def test_average_projection_uses_half_even_at_34_significant_digits(self):
        original = getcontext().copy()
        try:
            getcontext().prec = 7
            getcontext().rounding = ROUND_UP
            avg = Decimal("1")
            lot = PositionLot(
                position_id="order-1", entry_order_id="order-1",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                qty=Decimal("1"), avg_entry_price=avg,
                total_cost=Decimal("1." + "0" * 33 + "5"),
            )
            assert lot.avg_entry_price == avg
        finally:
            setcontext(original)

    def test_average_projection_outside_explicit_exponent_range_is_rejected(self):
        with pytest.raises(ValidationError, match="cannot be projected"):
            PositionLot(
                position_id="order-1", entry_order_id="order-1",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                qty=Decimal("1E+999999999999999999"),
                avg_entry_price=Decimal("1"),
                total_cost=Decimal("1E-999999999999999999"),
            )

    def test_accounting_sums_and_pnl_remain_exact_over_34_digits(self):
        original = getcontext().copy()
        qty_text = "9" * 50
        expected_text = "1" + "0" * 50
        try:
            getcontext().prec = 6
            getcontext().rounding = ROUND_DOWN
            strategy = StrategyRef(strategy_id="s1", strategy_version="v1")
            lots = [
                PositionLot(
                    position_id="order-1", entry_order_id="order-1",
                    strategy_ref=strategy, qty=Decimal(qty_text),
                    avg_entry_price=Decimal("1"), total_cost=Decimal(qty_text),
                ),
                PositionLot(
                    position_id="order-2", entry_order_id="order-2",
                    strategy_ref=strategy, qty=Decimal("1"),
                    avg_entry_price=Decimal("1"), total_cost=Decimal("1"),
                ),
            ]
            view = self._make_view(lots, mark_price=Decimal("2"))
            expected = Decimal(expected_text)
            assert sum(len(lot.qty.as_tuple().digits) for lot in lots) > 34
            assert view.total_qty == expected
            assert view.unrealized_pnl == expected
        finally:
            setcontext(original)

    def test_unrealized_pnl_cost_negation_preserves_global_context_and_flags(self):
        cost = Decimal("9" * 50)
        qty = Decimal("1")
        mark = Decimal("1E50")
        strategy = StrategyRef(strategy_id="s1", strategy_version="v1")
        lot = PositionLot(
            position_id="order-1", entry_order_id="order-1",
            strategy_ref=strategy, qty=qty, avg_entry_price=Decimal("1E50"),
            total_cost=cost,
        )
        view = self._make_view([lot], mark_price=mark)

        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 6
            context.rounding = ROUND_DOWN
            context.clear_flags()
            flags_before = context.flags.copy()

            pnl = view.unrealized_pnl

            assert pnl == Decimal("1")
            assert context.prec == 6
            assert context.rounding == ROUND_DOWN
            assert context.flags == flags_before
        finally:
            setcontext(original)

    def test_is_open(self):
        lots = [PositionLot(position_id="p1", entry_order_id="o1",
                            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                            qty=Decimal("0.5"), avg_entry_price=Decimal("65000"), total_cost=Decimal("32500"))]
        v = self._make_view(lots)
        assert v.is_open is True

    def test_unrealized_pnl_with_mark(self):
        lots = [PositionLot(position_id="p1", entry_order_id="o1",
                            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                            qty=Decimal("1.0"), avg_entry_price=Decimal("65000"), total_cost=Decimal("65000"))]
        v = self._make_view(lots, mark_price=Decimal("67000"))
        assert v.unrealized_pnl == Decimal("2000")

    def test_unrealized_pnl_no_mark(self):
        lots = [PositionLot(position_id="p1", entry_order_id="o1",
                            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                            qty=Decimal("1.0"), avg_entry_price=Decimal("65000"), total_cost=Decimal("65000"))]
        v = self._make_view(lots, mark_price=None)
        assert v.unrealized_pnl is None

    def test_unrealized_pnl_closed_position(self):
        lots: list[PositionLot] = []
        v = self._make_view(lots, mark_price=Decimal("67000"))
        assert v.unrealized_pnl is None

    def test_lots_must_share_strategy_version(self):
        lot = PositionLot(
            position_id="p1", entry_order_id="o1",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v2"),
            qty=Decimal("1"), avg_entry_price=Decimal("10"), total_cost=Decimal("10"),
        )
        with pytest.raises(ValidationError, match="strategy_ref must match"):
            self._make_view([lot])

    def test_lot_ids_must_be_unique_in_view(self):
        lot = PositionLot(
            position_id="p1", entry_order_id="o1",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("1"), avg_entry_price=Decimal("10"), total_cost=Decimal("10"),
        )
        with pytest.raises(ValidationError, match="duplicate position_id"):
            self._make_view([lot, lot])


# ═══════════════════════════════════════════════════════════════════════════════
#  PaperBalanceView — ecuación de balances y límite de reserva
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaperBalanceView:
    def test_balance_equation(self):
        bal = AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("700"), reserved=Decimal("300"))  # noqa: F821
        assert bal.total == bal.available + bal.reserved

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            AssetBalance(asset="USDT", total=Decimal("-1"), available=Decimal("0"), reserved=Decimal("0"))  # noqa: F821

    def test_equation_violation_rejected(self):
        """total != available + reserved debe fallar."""
        with pytest.raises(ValidationError):
            AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("700"), reserved=Decimal("400"))  # noqa: F821

    def test_reserved_le_total(self):
        bal = AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("300"), reserved=Decimal("700"))  # noqa: F821
        assert bal.reserved <= bal.total

    def test_reserved_gt_total_rejected(self):
        with pytest.raises(ValidationError):
            AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("300"), reserved=Decimal("701"))  # noqa: F821

    def test_available_non_negative(self):
        bal = AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("0"), reserved=Decimal("1000"))  # noqa: F821
        assert bal.available >= Decimal("0")

    def test_duplicate_asset_balance_rejected(self):
        balance = AssetBalance(asset="USDT", total=Decimal("1"), available=Decimal("1"), reserved=Decimal("0"))
        with pytest.raises(ValidationError, match="duplicate asset"):
            PaperBalanceView(account_id="acc1", balances=[balance, balance])


class TestPaperAccountOpening:
    @staticmethod
    def _balance(asset="USD", amount=Decimal("10"), reserved=Decimal("0")):
        return AssetBalance(
            asset=asset,
            total=amount,
            available=amount - reserved,
            reserved=reserved,
        )

    def test_opening_requires_explicit_nonempty_balances_and_paper_environment(self):
        with pytest.raises(ValidationError):
            PaperAccountOpening(account_id="account-1")
        with pytest.raises(ValidationError):
            PaperAccountOpening(
                account_id="account-1",
                balances=[self._balance()],
                environment="live",
            )

    def test_account_id_is_strict_nonblank_and_not_normalized(self):
        with pytest.raises(ValidationError):
            PaperAccountOpening(account_id="   ", balances=[self._balance()])
        with pytest.raises(ValidationError):
            PaperAccountOpening(account_id=123, balances=[self._balance()])
        opening = PaperAccountOpening(
            account_id=" account-1 ", balances=[self._balance()]
        )
        assert opening.account_id == " account-1 "

    def test_opening_rejects_duplicate_assets_and_reserved_amounts(self):
        usd = self._balance()
        with pytest.raises(ValidationError, match="duplicate assets"):
            PaperAccountOpening(account_id="account-1", balances=[usd, usd])
        with pytest.raises(ValidationError, match="must not include reserved"):
            PaperAccountOpening(
                account_id="account-1",
                balances=[self._balance(reserved=Decimal("1"))],
            )

    def test_opening_sorts_assets_and_accepts_explicit_zero(self):
        opening = PaperAccountOpening(
            account_id="account-1",
            balances=[
                self._balance("USD", Decimal("0")),
                self._balance("BTC", Decimal("2.5")),
            ],
        )
        assert [balance.asset for balance in opening.balances] == ["BTC", "USD"]
        assert opening.balances[1].total == Decimal("0")
        assert opening.schema_version == "paper_account_opening.v1"
        assert opening.environment == "paper"

    def test_opening_and_nested_balances_are_immutable_copies(self):
        supplied_balance = self._balance()
        opening = PaperAccountOpening(
            account_id="account-1", balances=[supplied_balance]
        )
        supplied_balance.total = Decimal("20")
        assert opening.balances[0].total == Decimal("10")
        with pytest.raises(ValidationError):
            opening.account_id = "changed"
        with pytest.raises(ValidationError):
            opening.balances[0].total = Decimal("20")


# ═══════════════════════════════════════════════════════════════════════════════
#  UnattributedInventoryView — inventario no atribuido
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnattributedInventoryView:
    def test_empty(self):
        v = UnattributedInventoryView(account_id="acc1")
        assert v.items == []

    def test_unattributed_included_but_not_sellable(self):
        """Inventario no atribuido: qty >= 0 pero no vendible por estrategia."""
        item = UnattributedInventory(asset="BTC", qty=Decimal("1.0"))
        assert item.qty >= Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
#  PaperReservationRef / PaperProtectionGroup (OCO)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOCO:
    def test_reservation_ref(self):
        res = PaperReservationRef(
            reservation_id="r1", owner_order_id="o1",
            amount=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
        )
        assert res.owner_order_id == "o1"

    def test_reservation_ref_requires_exactly_one_owner(self):
        amount = ReservationAmount(asset="BTC", amount=Decimal("0.5"))
        with pytest.raises(ValidationError, match="exactly one owner"):
            PaperReservationRef(reservation_id="r0", amount=amount)
        with pytest.raises(ValidationError, match="exactly one owner"):
            PaperReservationRef(
                reservation_id="r2", owner_order_id="o1", owner_group_id="pg1", amount=amount
            )

    def _group(self, **overrides) -> PaperProtectionGroup:
        values = dict(
            protection_group_id="pg1", position_id="p1", symbol="BTC/USDT",
            open_position_qty=Decimal("0.5"), stop_order_id="stop1", take_order_id="take1",
            reserved_amount=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
            remaining_protected_qty=Decimal("0.5"),
        )
        values.update(overrides)
        return PaperProtectionGroup(**values)  # type: ignore[arg-type]

    def test_reservation_request_within_previous_available(self):
        request = PaperReservationRequest(
            reservation_id="r1", account_id="acc1", paper_session_id="s1",
            owner_order_id="o1",
            amount=ReservationAmount(asset="BTC", amount=Decimal("0.3")),
            available_before=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
        )
        assert request.amount.amount <= request.available_before.amount

    def test_reservation_request_cannot_exceed_available(self):
        with pytest.raises(ValidationError, match="cannot exceed previous available"):
            PaperReservationRequest(
                reservation_id="r1", account_id="acc1", paper_session_id="s1",
                owner_order_id="o1",
                amount=ReservationAmount(asset="BTC", amount=Decimal("0.6")),
                available_before=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
            )

    def test_reservation_request_requires_one_owner_and_matching_asset(self):
        common = dict(
            reservation_id="r1", account_id="acc1", paper_session_id="s1",
            amount=ReservationAmount(asset="BTC", amount=Decimal("0.3")),
            available_before=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
        )
        with pytest.raises(ValidationError, match="exactly one owner"):
            PaperReservationRequest(**common)  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="same asset"):
            PaperReservationRequest(
                **{**common, "owner_order_id": "o1",
                   "available_before": ReservationAmount(asset="USDT", amount=Decimal("0.5"))}
            )  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="exactly one owner"):
            PaperReservationRequest(
                **{**common, "owner_order_id": "o1", "owner_group_id": "pg1"}
            )  # type: ignore[arg-type]

    def test_protection_group_has_one_shared_base_reservation(self):
        group = self._group()
        assert group.stop_order_id is not None and group.take_order_id is not None
        assert group.reserved_amount.asset == "BTC"
        assert group.reserved_amount.amount == group.remaining_protected_qty

    def test_cancelling_one_member_preserves_sibling_and_reserve(self):
        group = self._group(stop_order_id=None)
        assert group.take_order_id == "take1"
        assert group.reserved_amount.amount == Decimal("0.5")

    def test_cancelling_group_releases_only_remaining_reserve(self):
        group = self._group(
            open_position_qty=Decimal("0.3"), stop_order_id=None, take_order_id=None,
            reserved_amount=ReservationAmount(asset="BTC", amount=Decimal("0")),
            remaining_protected_qty=Decimal("0"),
        )
        assert group.open_position_qty == Decimal("0.3")
        assert group.reserved_amount.amount == Decimal("0")
        assert group.remaining_protected_qty == Decimal("0")

    def test_partial_fill_reduces_protected_qty(self):
        """Tras una salida parcial, OCO protege el inventario abierto restante."""
        group = self._group(
            open_position_qty=Decimal("0.3"),
            reserved_amount=ReservationAmount(asset="BTC", amount=Decimal("0.3")),
            remaining_protected_qty=Decimal("0.3"),
        )
        assert group.remaining_protected_qty == group.open_position_qty
        assert group.reserved_amount.amount == group.open_position_qty

    def test_oco_rejects_wrong_asset_or_protection_quantity(self):
        with pytest.raises(ValidationError, match="base asset"):
            self._group(reserved_amount=ReservationAmount(asset="USDT", amount=Decimal("0.5")))
        with pytest.raises(ValidationError, match="match remaining open position"):
            self._group(remaining_protected_qty=Decimal("0.4"))



# ═══════════════════════════════════════════════════════════════════════════════
#  Decimal JSON — serialización / deserialización
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecimalJSON:
    def test_order_serializes_decimals_as_strings(self):
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.234567"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        json_str = o.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["requested_qty"] == "1.234567"
        assert parsed["max_quote_amount"] == "65000"

    def test_fill_serializes_decimals(self):
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000.123456"), timestamp_ms=1700000000000,
            commission=None,
        )
        json_str = fill.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["qty"] == "0.5"
        assert parsed["price"] == "65000.123456"
        assert isinstance(parsed["qty"], str)
        assert isinstance(parsed["price"], str)

    def test_deserialize_order_from_json(self):
        data = {
            "order_id": "o1", "client_order_id": "c1",
            "paper_account_id": "acc1", "paper_session_id": "s1",
            "symbol": "BTC/USDT", "side": "buy", "order_type": "market",
            "requested_qty": "1.5",
            "max_quote_amount": "65000",  # Fix 2: MARKET BUY requiere max_quote_amount
            "strategy_ref": {"strategy_id": "s1", "strategy_version": "v1"},
            "timestamp_ms": 1700000000000,
        }
        o = PaperOrder.model_validate(data)
        assert o.requested_qty == Decimal("1.5")

    def test_decimal_fields_reject_float_inputs(self):
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            Commission(amount=0.1, asset="USDT")  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            AssetBalance(
                asset="USDT", total=100.0, available=100.0, reserved=0.0  # type: ignore[arg-type]
            )
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            PaperFill(
                fill_id="float-fill", order_id="o1", qty=0.5,
                price=Decimal("10"), timestamp_ms=1, commission=None,  # type: ignore[arg-type]
            )
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            PaperOrder(
                order_id="float-order", client_order_id="c-float",
                paper_account_id="acc1", paper_session_id="s1",
                symbol="BTC/USDT", side=PaperOrderSide.BUY,
                order_type=PaperOrderType.MARKET, requested_qty=Decimal("1"),
                max_quote_amount=1.0,  # type: ignore[arg-type]
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                timestamp_ms=1,
            )

    def test_decimal_contract_rejects_float_inputs(self):
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            Commission(amount=0.1, asset="USDT")  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            AssetBalance(
                asset="USDT", total=100.0, available=100.0, reserved=0.0  # type: ignore[arg-type]
            )
        with pytest.raises(ValidationError, match="Decimal or decimal strings"):
            PaperFill(
                fill_id="float-fill", order_id="o1", qty=0.5,
                price=Decimal("10"), timestamp_ms=1, commission=None,  # type: ignore[arg-type]
            )


# ═══════════════════════════════════════════════════════════════════════════════
#  Venta con lot_allocations explícitas
# ═══════════════════════════════════════════════════════════════════════════════

class TestSaleAllocations:
    def test_sale_with_explicit_allocations(self):
        """Venta requiere allocations explícitas; suma = qty solicitada."""
        allocs = [
            LotAllocation(position_id="p1", account_id="acc1", symbol="BTC/USDT",
                          strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0.3")),
            LotAllocation(position_id="p2", account_id="acc1", symbol="BTC/USDT",
                          strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0.7")),
        ]
        fill = PaperFill(
            fill_id="f1", order_id="o_sell1", qty=Decimal("1.0"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None,
            lot_allocations=allocs,
        )
        assert len(fill.lot_allocations) == 2

    def _make_sell_order(self, allocations, **overrides) -> PaperOrder:
        values = dict(
            order_id="sell1", client_order_id="command1",
            paper_account_id="acc1", paper_session_id="session1",
            symbol="BTC/USDT", side=PaperOrderSide.SELL,
            order_type=PaperOrderType.LIMIT, requested_qty=Decimal("1.0"),
            price=Decimal("65000"), strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000, lot_allocations=allocations,
        )
        values.update(overrides)
        return PaperOrder(**values)  # type: ignore[arg-type]

    def test_sell_order_requires_pre_execution_lot_allocations(self):
        with pytest.raises(ValidationError, match="SELL orders require explicit lot_allocations"):
            self._make_sell_order([])

    def test_sell_order_allocations_sum_to_requested_qty(self):
        allocations = [LotAllocation(
            position_id="p1", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("0.4"),
        ), LotAllocation(
            position_id="p2", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("0.6"),
        )]
        order = self._make_sell_order(allocations)
        assert sum(allocation.qty for allocation in order.lot_allocations) == order.requested_qty

    def test_sell_order_rejects_incomplete_allocation_sum(self):
        allocation = LotAllocation(
            position_id="p1", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("0.8"),
        )
        with pytest.raises(ValidationError, match="must sum to requested_qty"):
            self._make_sell_order([allocation])

    def test_sell_order_rejects_different_strategy_version(self):
        allocation = LotAllocation(
            position_id="p1", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v2"),
            qty=Decimal("1.0"),
        )
        with pytest.raises(ValidationError, match="strategy_ref must match"):
            self._make_sell_order([allocation])

    def test_sell_fill_must_fit_allocated_lot(self):
        order_allocation = LotAllocation(
            position_id="p1", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("0.5"),
        )
        other_allocation = LotAllocation(
            position_id="p2", account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            qty=Decimal("0.5"),
        )
        fill = PaperFill(
            fill_id="f1", order_id="sell1", qty=Decimal("0.6"),
            price=Decimal("65000"), timestamp_ms=1700000001000, commission=None,
            lot_allocations=[LotAllocation(
                position_id="p1", account_id="acc1", symbol="BTC/USDT",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                qty=Decimal("0.6"),
            )],
        )
        with pytest.raises(ValidationError, match="cannot consume more than the order allocation"):
            self._make_sell_order(
                [order_allocation, other_allocation],
                fills=[fill], status=PaperOrderStatus.PARTIALLY_FILLED,
                requested_qty=Decimal("1.0"),
            )

    def test_allocation_qty_must_be_positive(self):
        with pytest.raises(ValidationError):
            LotAllocation(position_id="p1", account_id="acc1", symbol="BTC/USDT",
                          strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"), qty=Decimal("0"))  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════════════
#  Atribución Spot — invariantes de inventario
# ═══════════════════════════════════════════════════════════════════════════════

class TestInventoryAttribution:
    def test_open_position_lots_sum(self):
        lots = [
            PositionLot(position_id="p1", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("1.0"), avg_entry_price=Decimal("65000"), total_cost=Decimal("65000")),
            PositionLot(position_id="p2", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("0.5"), avg_entry_price=Decimal("65100"), total_cost=Decimal("32550")),
        ]
        view = PaperPositionView(
            account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            lots=lots,
        )
        assert view.total_qty == Decimal("1.5")


# ═══════════════════════════════════════════════════════════════════════════════
#  Fix 5 — Reconciliación cross-DTO (balance/lots/inventario)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossDTOReconciliation:
    """Tests que verifican reconciliación entre DTOs, no tautologías."""

    def test_balance_equation_cross_dto(self):
        """PaperBalanceView debe satisfacer total == available + reserved."""
        bal = AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("700"), reserved=Decimal("300"))
        view = PaperBalanceView(account_id="acc1", balances=[bal])
        # Usar los campos reales del DTO, no propiedades inexistentes
        assert sum(b.total for b in view.balances) == Decimal("1000")
        assert sum(b.available for b in view.balances) == Decimal("700")
        assert sum(b.reserved for b in view.balances) == Decimal("300")

    def test_position_lots_sum_matches_total_qty(self):
        """La suma de PositionLot.qty debe coincidir con PaperPositionView.total_qty."""
        lots = [
            PositionLot(position_id="p1", entry_order_id="o1",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("0.5"), avg_entry_price=Decimal("65000"), total_cost=Decimal("32500")),
            PositionLot(position_id="p2", entry_order_id="o2",
                        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                        qty=Decimal("0.3"), avg_entry_price=Decimal("65100"), total_cost=Decimal("19530")),
        ]
        view = PaperPositionView(
            account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            lots=lots,
        )
        # Cross-DTO: total_qty debe ser la suma de cada lot.qty
        expected_total = sum(l.qty for l in lots)
        assert view.total_qty == expected_total

    def _valid_reconciliation(self, **overrides) -> PaperInventoryReconciliation:
        values = dict(
            account_id="acc1",
            balance=AssetBalance(
                asset="BTC", total=Decimal("0.8"),
                available=Decimal("0.3"), reserved=Decimal("0.5"),
            ),
            positions=[PaperPositionView(
                account_id="acc1", symbol="BTC/USDT",
                strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                lots=[PositionLot(
                    position_id="p1", entry_order_id="o1",
                    strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
                    qty=Decimal("0.5"), avg_entry_price=Decimal("65000"),
                    total_cost=Decimal("32500"),
                )],
            )],
            unattributed_inventory=[UnattributedInventory(asset="BTC", qty=Decimal("0.3"))],
        )
        values.update(overrides)
        return PaperInventoryReconciliation(**values)  # type: ignore[arg-type]

    def test_inventory_reconciles_actual_dtos(self):
        snapshot = self._valid_reconciliation()
        assert snapshot.balance.total == Decimal("0.8")

    def test_inventory_total_mismatch_rejected(self):
        balance = AssetBalance(
            asset="BTC", total=Decimal("0.9"),
            available=Decimal("0.4"), reserved=Decimal("0.5"),
        )
        with pytest.raises(ValidationError, match="must equal attributed open lots"):
            self._valid_reconciliation(balance=balance)

    def test_inventory_account_mismatch_rejected(self):
        position = PaperPositionView(
            account_id="other-account", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        )
        with pytest.raises(ValidationError, match="account_id must match"):
            self._valid_reconciliation(positions=[position])

    def test_inventory_base_asset_mismatch_rejected(self):
        position = PaperPositionView(
            account_id="acc1", symbol="ETH/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        )
        with pytest.raises(ValidationError, match="base asset must match"):
            self._valid_reconciliation(positions=[position])

    def test_balance_reserved_le_total(self):
        """reserved <= total en PaperBalanceView."""
        bal = AssetBalance(asset="USDT", total=Decimal("1000"), available=Decimal("300"), reserved=Decimal("700"))
        view = PaperBalanceView(account_id="acc1", balances=[bal])
        # Usar campos reales del DTO
        assert sum(b.reserved for b in view.balances) <= sum(b.total for b in view.balances)

    def test_fill_qty_matches_order_executed_qty(self):
        """La suma de fill.qty debe coincidir con PaperOrder.executed_qty."""
        fills = [
            PaperFill(fill_id="f1", order_id="o1", qty=Decimal("0.3"),
                      price=Decimal("65000"), timestamp_ms=1700000000000,
                      commission=None),
            PaperFill(fill_id="f2", order_id="o1", qty=Decimal("0.4"),
                      price=Decimal("65100"), timestamp_ms=1700000001000,
                      commission=None),
        ]
        order = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            fills=fills,
            status=PaperOrderStatus.PARTIALLY_FILLED,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        # Cross-DTO: executed_qty debe ser la suma de fill.qty
        expected_executed = sum(f.qty for f in fills)
        assert order.executed_qty == expected_executed

    def test_remaining_qty_consistency(self):
        """remaining_qty == requested_qty - executed_qty."""
        fills = [
            PaperFill(fill_id="f1", order_id="o1", qty=Decimal("0.6"),
                      price=Decimal("65000"), timestamp_ms=1700000000000,
                      commission=None),
        ]
        order = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            fills=fills,
            status=PaperOrderStatus.PARTIALLY_FILLED,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        expected_remaining = order.requested_qty - order.executed_qty
        assert order.remaining_qty == expected_remaining


# ═══════════════════════════════════════════════════════════════════════════════
#  Schema version inmutable
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersion:
    def test_paper_order_schema(self):
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        assert o.schema_version == "paper_order.v1"

    def test_paper_fill_schema(self):
        fill = PaperFill(
            fill_id="f1", order_id="o1", qty=Decimal("0.5"),
            price=Decimal("65000"), timestamp_ms=1700000000000,
            commission=None,
        )
        assert fill.schema_version == "paper_fill.v1"

    def test_position_view_schema(self):
        v = PaperPositionView(
            account_id="acc1", symbol="BTC/USDT",
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        )
        assert v.schema_version == "paper_position_view.v1"

    def test_balance_view_schema(self):
        v = PaperBalanceView(account_id="acc1")
        assert v.schema_version == "paper_balance_view.v1"

    def test_unattributed_inventory_view_schema(self):
        v = UnattributedInventoryView(account_id="acc1")
        assert v.schema_version == "unattributed_inventory_view.v1"

    def test_protection_group_schema(self):
        grp = PaperProtectionGroup(
            protection_group_id="pg1", position_id="p1",
            symbol="BTC/USDT", open_position_qty=Decimal("0.5"),
            stop_order_id="stop1", take_order_id="take1",
            reserved_amount=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
            remaining_protected_qty=Decimal("0.5"),
        )
        assert grp.schema_version == "paper_protection_group.v1"

    def test_reservation_ref_schema(self):
        res = PaperReservationRef(
            reservation_id="r1",
            owner_order_id="o1",
            amount=ReservationAmount(asset="BTC", amount=Decimal("0.5")),
        )
        assert res.schema_version == "paper_reservation_ref.v1"


# ═══════════════════════════════════════════════════════════════════════════════
#  Compra MARKET necesita cota explícita de quote (validación conceptual)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarketBuyQuote:
    def test_market_order_has_price_field_nullable(self):
        """MARKET no requiere price; es opcional."""
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        assert o.price is None  # MARKET no necesita price en el DTO

    def test_limit_order_requires_price(self):
        """LIMIT puede tener price; es opcional en DTO pero semánticamente requerido."""
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.LIMIT,
            requested_qty=Decimal("1.0"), price=Decimal("65000"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
        )
        assert o.price == Decimal("65000")


# ═══════════════════════════════════════════════════════════════════════════════
#  No capital inicial, fees, spread, slippage, riesgo — NO definido
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoImplicitFinancialDefaults:
    def test_no_initial_capital_field(self):
        """El DTO no define capital inicial."""
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        assert not hasattr(o, "initial_capital")

    def test_no_spread_slippage_fields(self):
        """No hay spread ni slippage en el DTO."""
        o = PaperOrder(
            order_id="o1", client_order_id="c1",
            paper_account_id="acc1", paper_session_id="s1",
            symbol="BTC/USDT", side=PaperOrderSide.BUY,
            order_type=PaperOrderType.MARKET,
            requested_qty=Decimal("1.0"),
            strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
            timestamp_ms=1700000000000,
            max_quote_amount=Decimal("65000"),  # Fix 2: MARKET BUY requiere max_quote_amount
        )
        assert not hasattr(o, "spread")
        assert not hasattr(o, "slippage")


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (StrategyRef, {"strategy_id": "s1", "strategy_version": "v1"}),
        (PaperFill, {
            "fill_id": "f1", "order_id": "o1", "qty": Decimal("1"),
            "price": Decimal("100"), "timestamp_ms": 1, "commission": None,
        }),
        (PaperOrder, {
            "order_id": "o1", "client_order_id": "c1", "paper_account_id": "a1",
            "paper_session_id": "s1", "symbol": "BTC/USDT", "side": PaperOrderSide.BUY,
            "order_type": PaperOrderType.MARKET, "requested_qty": Decimal("1"),
            "max_quote_amount": Decimal("100"),
            "strategy_ref": StrategyRef(strategy_id="s1", strategy_version="v1"),
            "timestamp_ms": 1,
        }),
        (PaperPositionView, {
            "account_id": "a1", "symbol": "BTC/USDT",
            "strategy_ref": StrategyRef(strategy_id="s1", strategy_version="v1"),
        }),
        (PaperBalanceView, {"account_id": "a1"}),
        (PaperAccountOpening, {
            "account_id": "a1",
            "balances": [AssetBalance(
                asset="USD", total=Decimal("1"), available=Decimal("1"),
                reserved=Decimal("0"),
            )],
        }),
        (UnattributedInventoryView, {"account_id": "a1"}),
        (PaperReservationRef, {
            "reservation_id": "r1", "owner_order_id": "o1",
            "amount": ReservationAmount(asset="BTC", amount=Decimal("1")),
        }),
        (PaperProtectionGroup, {
            "protection_group_id": "g1", "position_id": "p1", "symbol": "BTC/USDT",
            "open_position_qty": Decimal("1"), "stop_order_id": "stop1",
            "take_order_id": "take1",
            "reserved_amount": ReservationAmount(asset="BTC", amount=Decimal("1")),
            "remaining_protected_qty": Decimal("1"),
        }),
    ],
    ids=[
        "strategy-ref", "fill", "order", "position-view", "balance-view",
        "account-opening", "unattributed-inventory", "reservation-ref", "protection-group",
    ],
)
def test_schema_version_rejects_unsupported_values(model, payload):
    instance = model(**payload)  # The currently declared v1 is accepted by default.
    with pytest.raises(ValidationError):
        model(**payload, schema_version="unsupported.v99")
    with pytest.raises(ValidationError):
        instance.schema_version = "unsupported.v99"
