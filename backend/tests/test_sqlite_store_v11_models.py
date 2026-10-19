"""Tests for internal Pydantic models and schema v11 tables (Pair 1).

Covers:
- _PaperLotReservationAllocationV1 model validation & immutability
- _PaperSellAccountingRecordV1 model validation (proceeds_net = proceeds_gross - fee_amount) & immutability
- Schema v11 table existence post-migration
- v10→v11 migration with preservation/rollback
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import ROUND_DOWN, Decimal, getcontext, localcontext, setcontext
from pathlib import Path
from typing import Any

import pytest
from src.models.paper_contracts import (
    AssetBalance,
    PaperAccountOpening,
    PaperFill,
    LotAllocation,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    ReservationAmount,
    StrategyRef,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperOrderContextConflictError,
    PaperOrderAcceptanceConflictError,
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceScopeError,
    PaperOrderAcceptanceStateError,
    SQLiteStore,
    _SCHEMA_V10_TABLES,
    _SCHEMA_V11_TABLES,
    _PAPER_SELL_DDL_STATEMENTS,
)

# Import internal models
from src.persistence.sqlite_store import (
    _PaperLotReservationAllocationV1,
    _PaperSellAccountingRecordV1,
    _PaperSellAllocationSlotV1,
)

# Rebuild models to resolve forward references (PaperDecimal)
_PaperLotReservationAllocationV1.model_rebuild()
_PaperSellAccountingRecordV1.model_rebuild()
_PaperSellAllocationSlotV1.model_rebuild()


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Test fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database file."""
    db_path = tmp_path / "test_v11.db"
    db_path.touch()
    return db_path


def _lot_reservation(**overrides):
    values = {
        "schema_version": "paper_lot_reservation_allocation.v1",
        "reservation_id": "res-001",
        "owner_order_id": "sell-order-001",
        "position_id": "entry-order-001",
        "account_id": "acct-001",
        "paper_session_id": "session-001",
        "environment": "paper",
        "symbol": "BTC/USDT",
        "base_asset": "BTC",
        "strategy_ref": StrategyRef(
            strategy_id="strategy-001",
            strategy_version="v1",
            decision_id="decision-001",
        ),
        "initial_qty": Decimal("1.5"),
        "remaining_qty": Decimal("1.5"),
    }
    values.update(overrides)
    return _PaperLotReservationAllocationV1(**values)


def _sell_accounting(**overrides):
    strategy = StrategyRef(
        strategy_id="strategy-001",
        strategy_version="v1",
        decision_id="decision-001",
    )
    values = {
        "schema_version": "paper_sell_accounting.v1",
        "fill_id": "fill-sell-001",
        "order_id": "sell-order-001",
        "account_id": "acct-001",
        "paper_session_id": "session-001",
        "environment": "paper",
        "quote_asset": "USDT",
        "strategy_ref": strategy,
        "total_qty_filled": Decimal("1"),
        "proceeds_gross": Decimal("10"),
        "fee_asset": "USDT",
        "fee_amount": Decimal("1"),
        "proceeds_net": Decimal("9"),
        "total_cost_basis_removed": Decimal("8"),
        "pnl_gross": Decimal("2"),
        "pnl_net": Decimal("1"),
        "allocation_slots": (
            _PaperSellAllocationSlotV1(
                slot_version="sell_allocation_slot.v1",
                position_id="entry-buy-001",
                qty_sold=Decimal("1"),
                cost_basis_removed=Decimal("8"),
                proceeds_attributed=Decimal("10"),
            ),
        ),
    }
    values.update(overrides)
    return _PaperSellAccountingRecordV1(**values)


# ── Test _PaperLotReservationAllocationV1 model ───────────────────────────

class TestPaperLotReservationAllocationV1:
    """Tests for the internal v1 reservation allocation model."""

    def test_model_creation(self) -> None:
        """Test that a valid model can be created with accepted StrategyRef fields."""
        model = _lot_reservation()
        assert model.schema_version == "paper_lot_reservation_allocation.v1"
        assert model.reservation_id == "res-001"
        assert model.owner_order_id == "sell-order-001"
        assert model.position_id == "entry-order-001"
        assert model.account_id == "acct-001"
        assert model.paper_session_id == "session-001"
        assert model.environment == "paper"
        assert model.symbol == "BTC/USDT"
        assert model.base_asset == "BTC"
        assert model.strategy_ref.strategy_id == "strategy-001"
        assert model.strategy_ref.strategy_version == "v1"
        assert model.strategy_ref.decision_id == "decision-001"
        assert model.initial_qty == Decimal("1.5")
        assert model.remaining_qty == Decimal("1.5")

    def test_model_frozen(self) -> None:
        """Test that the model is immutable (frozen)."""
        model = _lot_reservation()
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            model.reservation_id = "res-002"

    def test_model_extra_forbidden(self) -> None:
        """Test that extra fields are forbidden."""
        with pytest.raises(ValueError):
            _lot_reservation(extra_field="should_fail")

    def test_model_validation_qty_gt_zero(self) -> None:
        """An active allocation must have positive remaining quantity."""
        with pytest.raises(ValueError):
            _lot_reservation(remaining_qty=Decimal("0"))

    def test_remaining_qty_cannot_exceed_initial_qty(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed initial_qty"):
            _lot_reservation(remaining_qty=Decimal("2"))

    def test_model_validation_strategy_ref_required(self) -> None:
        """Test that strategy_ref must have non-empty strategy_id and strategy_version."""
        with pytest.raises(ValueError, match="strategy_id"):
            _lot_reservation(
                strategy_ref=StrategyRef(
                    strategy_id="",
                    strategy_version="v1",
                    decision_id="decision-001",
                )
            )

    def test_model_no_fake_position_id_derivation(self) -> None:
        """Test that account_id is NOT derived from position_id text.

        Context validation happens in the store layer by cross-referencing
        reservation, order, and lot — not by deriving fields from position_id.
        """
        # This should succeed even if account_id doesn't "match" position_id format
        model = _lot_reservation(
            position_id="opaque-lot-id",
            account_id="unrelated-account-prefix",
        )
        # If we get here without ValueError, the fake derivation is removed
        assert model.account_id == "unrelated-account-prefix"


# ── Test _PaperSellAccountingRecordV1 model ───────────────────────────────

class TestPaperSellAccountingRecordV1:
    """Tests for the internal v1 sell accounting model with allocation slots."""

    def test_model_creation(self) -> None:
        """Test that a valid model can be created with allocation slots."""
        model = _PaperSellAccountingRecordV1(
            schema_version="paper_sell_accounting.v1",
            fill_id="fill-001",
            order_id="order-001",
            account_id="acct-001",
            paper_session_id="session-001",
            environment="paper",
            quote_asset="USDT",
            strategy_ref=StrategyRef(
                strategy_id="strategy-001",
                strategy_version="v1",
                decision_id="decision-001",
            ),
            total_qty_filled=Decimal("1.5"),
            proceeds_gross=Decimal("46500.00"),
            fee_asset="USDT",
            fee_amount=Decimal("150.00"),
            proceeds_net=Decimal("46350.00"),  # 46500 - 150 = 46350
            total_cost_basis_removed=Decimal("45000.00"),
            pnl_gross=Decimal("1500.00"),  # 46500 - 45000 = 1500
            pnl_net=Decimal("1350.00"),  # 46350 - 45000 = 1350
            allocation_slots=[
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-001",
                    qty_sold=Decimal("1.0"),
                    cost_basis_removed=Decimal("30000.00"),
                    proceeds_attributed=Decimal("31000.00"),
                ),
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-002",
                    qty_sold=Decimal("0.5"),
                    cost_basis_removed=Decimal("15000.00"),
                    proceeds_attributed=Decimal("15500.00"),
                ),
            ],
        )
        assert model.schema_version == "paper_sell_accounting.v1"
        assert model.fill_id == "fill-001"
        assert model.total_qty_filled == Decimal("1.5")
        assert len(model.allocation_slots) == 2
        # Verify slots sum to aggregates
        total_qty = sum(s.qty_sold for s in model.allocation_slots)
        assert total_qty == model.total_qty_filled
        total_cost = sum(s.cost_basis_removed for s in model.allocation_slots)
        assert total_cost == model.total_cost_basis_removed
        total_proceeds = sum(s.proceeds_attributed for s in model.allocation_slots)
        assert total_proceeds == model.proceeds_gross

    def test_sell_fee_asset_must_match_quote_asset(self) -> None:
        with pytest.raises(ValueError, match="sell commission must use the quote asset"):
            _sell_accounting(fee_asset="BTC")

    def test_sell_realized_pnl_can_be_negative(self) -> None:
        model = _sell_accounting(
            total_cost_basis_removed=Decimal("12"),
            pnl_gross=Decimal("-2"),
            pnl_net=Decimal("-3"),
            allocation_slots=(
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="entry-buy-001",
                    qty_sold=Decimal("1"),
                    cost_basis_removed=Decimal("12"),
                    proceeds_attributed=Decimal("10"),
                ),
            ),
        )
        assert model.pnl_gross == Decimal("-2")
        assert model.pnl_net == Decimal("-3")

    def test_model_frozen(self) -> None:
        """Test that the model is immutable (frozen)."""
        model = _PaperSellAccountingRecordV1(
            schema_version="paper_sell_accounting.v1",
            fill_id="fill-001",
            order_id="order-001",
            account_id="acct-001",
            paper_session_id="session-001",
            environment="paper",
            quote_asset="USDT",
            strategy_ref=StrategyRef(
                strategy_id="strategy-001",
                strategy_version="v1",
                decision_id="decision-001",
            ),
            total_qty_filled=Decimal("1.5"),
            proceeds_gross=Decimal("46500.00"),
            fee_asset="USDT",
            fee_amount=Decimal("150.00"),
            proceeds_net=Decimal("46350.00"),
            total_cost_basis_removed=Decimal("45000.00"),
            pnl_gross=Decimal("1500.00"),
            pnl_net=Decimal("1350.00"),
            allocation_slots=[
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-001",
                    qty_sold=Decimal("1.5"),
                    cost_basis_removed=Decimal("45000.00"),
                    proceeds_attributed=Decimal("46500.00"),
                ),
            ],
        )
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            model.fill_id = "fill-002"

    def test_model_extra_forbidden(self) -> None:
        """Test that extra fields are forbidden."""
        with pytest.raises(ValueError):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46350.00"),
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1500.00"),
                pnl_net=Decimal("1350.00"),
                allocation_slots=[],
                extra_field="should_fail",
            )

    def test_model_validation_proceeds_net(self) -> None:
        """Test that proceeds_net must equal proceeds_gross - fee_amount."""
        with pytest.raises(ValueError, match="proceeds_net"):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46300.00"),  # Wrong: should be 46350
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1500.00"),
                pnl_net=Decimal("1350.00"),
                allocation_slots=[
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-001",
                        qty_sold=Decimal("1.5"),
                        cost_basis_removed=Decimal("45000.00"),
                        proceeds_attributed=Decimal("46500.00"),
                    ),
                ],
            )

    def test_model_validation_pnl_gross(self) -> None:
        """Test that pnl_gross must equal proceeds_gross - total_cost_basis_removed."""
        with pytest.raises(ValueError, match="pnl_gross"):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46350.00"),
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1000.00"),  # Wrong: should be 1500
                pnl_net=Decimal("1350.00"),
                allocation_slots=[
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-001",
                        qty_sold=Decimal("1.5"),
                        cost_basis_removed=Decimal("45000.00"),
                        proceeds_attributed=Decimal("46500.00"),
                    ),
                ],
            )

    def test_model_validation_pnl_net(self) -> None:
        """Test that pnl_net must equal proceeds_net - total_cost_basis_removed."""
        with pytest.raises(ValueError, match="pnl_net"):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46350.00"),
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1500.00"),
                pnl_net=Decimal("1000.00"),  # Wrong: should be 1350
                allocation_slots=[
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-001",
                        qty_sold=Decimal("1.5"),
                        cost_basis_removed=Decimal("45000.00"),
                        proceeds_attributed=Decimal("46500.00"),
                    ),
                ],
            )

    def test_model_validation_slots_sum_to_aggregates(self) -> None:
        """Test that allocation slots sum to aggregate quantities."""
        with pytest.raises(ValueError, match="sum\\(slot.qty_sold\\)"):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),  # Slots sum to 2.0, not 1.5
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46350.00"),
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1500.00"),
                pnl_net=Decimal("1350.00"),
                allocation_slots=[
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-001",
                        qty_sold=Decimal("1.0"),
                        cost_basis_removed=Decimal("30000.00"),
                        proceeds_attributed=Decimal("31000.00"),
                    ),
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-002",
                        qty_sold=Decimal("1.0"),  # Total = 2.0, not 1.5
                        cost_basis_removed=Decimal("15000.00"),
                        proceeds_attributed=Decimal("15500.00"),
                    ),
                ],
            )

    def test_model_validation_at_least_one_slot(self) -> None:
        """Test that allocation_slots must contain at least one slot."""
        with pytest.raises(ValueError, match="allocation_slots"):
            _PaperSellAccountingRecordV1(
                schema_version="paper_sell_accounting.v1",
                fill_id="fill-001",
                order_id="order-001",
                account_id="acct-001",
                paper_session_id="session-001",
                environment="paper",
                quote_asset="USDT",
                strategy_ref=StrategyRef(
                    strategy_id="strategy-001",
                    strategy_version="v1",
                    decision_id="decision-001",
                ),
                total_qty_filled=Decimal("1.5"),
                proceeds_gross=Decimal("46500.00"),
                fee_asset="USDT",
                fee_amount=Decimal("150.00"),
                proceeds_net=Decimal("46350.00"),
                total_cost_basis_removed=Decimal("45000.00"),
                pnl_gross=Decimal("1500.00"),
                pnl_net=Decimal("1350.00"),
                allocation_slots=[],  # Empty slots should fail
            )

    def test_model_two_lots_per_fill(self) -> None:
        """Test a sell fill with two distinct position lots."""
        model = _PaperSellAccountingRecordV1(
            schema_version="paper_sell_accounting.v1",
            fill_id="fill-two-lots",
            order_id="order-002",
            account_id="acct-002",
            paper_session_id="session-002",
            environment="paper",
            quote_asset="USDT",
            strategy_ref=StrategyRef(
                strategy_id="strategy-002",
                strategy_version="v2",
                decision_id="decision-002",
            ),
            total_qty_filled=Decimal("2.0"),
            proceeds_gross=Decimal("100000.00"),
            fee_asset="USDT",
            fee_amount=Decimal("50.00"),
            proceeds_net=Decimal("99950.00"),
            total_cost_basis_removed=Decimal("80000.00"),
            pnl_gross=Decimal("20000.00"),  # 100000 - 80000
            pnl_net=Decimal("19950.00"),  # 99950 - 80000
            allocation_slots=[
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-A",
                    qty_sold=Decimal("1.2"),
                    cost_basis_removed=Decimal("48000.00"),
                    proceeds_attributed=Decimal("60000.00"),
                ),
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-B",
                    qty_sold=Decimal("0.8"),
                    cost_basis_removed=Decimal("32000.00"),
                    proceeds_attributed=Decimal("40000.00"),
                ),
            ],
        )
        # Verify invariants
        assert len(model.allocation_slots) == 2
        total_qty = sum(s.qty_sold for s in model.allocation_slots)
        assert total_qty == Decimal("2.0") == model.total_qty_filled
        total_cost = sum(s.cost_basis_removed for s in model.allocation_slots)
        assert total_cost == Decimal("80000.00") == model.total_cost_basis_removed
        total_proceeds = sum(s.proceeds_attributed for s in model.allocation_slots)
        assert total_proceeds == Decimal("100000.00") == model.proceeds_gross
        # Verify P&L
        assert model.pnl_gross == Decimal("20000.00")
        assert model.pnl_net == Decimal("19950.00")

    def test_model_explicit_decimal_arithmetic(self) -> None:
        """Large sell totals remain exact when the global Decimal context is tiny."""
        original = getcontext().copy()
        try:
            qty_1 = Decimal("123456789012345678901234567890.123")
            qty_2 = Decimal("0.000000000000000000000000000001")
            cost_1 = Decimal("987654321098765432109876543210.987")
            cost_2 = Decimal("0.000000000000000000000000000003")
            gross_1 = Decimal("1987654321098765432109876543210.987")
            gross_2 = Decimal("0.000000000000000000000000000004")
            fee = Decimal("123456789012345678901234567890.123")
            strategy = StrategyRef(
                strategy_id="strategy-003",
                strategy_version="v3",
                decision_id="decision-003",
            )
            with localcontext() as context:
                context.prec = 100
                total_qty = qty_1 + qty_2
                total_cost = cost_1 + cost_2
                gross = gross_1 + gross_2
                net = gross - fee
                pnl_gross = gross - total_cost
                pnl_net = net - total_cost

            context = getcontext()
            context.prec = 3
            context.rounding = ROUND_DOWN
            context.clear_flags()
            flags_before = context.flags.copy()

            model = _sell_accounting(
                fill_id="fill-precision",
                order_id="order-003",
                account_id="acct-003",
                paper_session_id="session-003",
                strategy_ref=strategy,
                total_qty_filled=total_qty,
                proceeds_gross=gross,
                fee_amount=fee,
                proceeds_net=net,
                total_cost_basis_removed=total_cost,
                pnl_gross=pnl_gross,
                pnl_net=pnl_net,
                allocation_slots=(
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-1",
                        qty_sold=qty_1,
                        cost_basis_removed=cost_1,
                        proceeds_attributed=gross_1,
                    ),
                    _PaperSellAllocationSlotV1(
                        slot_version="sell_allocation_slot.v1",
                        position_id="pos-lot-2",
                        qty_sold=qty_2,
                        cost_basis_removed=cost_2,
                        proceeds_attributed=gross_2,
                    ),
                ),
            )
            assert model.total_qty_filled == total_qty
            assert model.total_cost_basis_removed == total_cost
            assert model.proceeds_net == net
            assert model.pnl_gross == pnl_gross
            assert model.pnl_net == pnl_net
            assert context.prec == 3
            assert context.rounding == ROUND_DOWN
            assert context.flags == flags_before

        finally:
            setcontext(original)

    def test_model_allocation_slots_is_tuple_not_list(self) -> None:
        """Test that allocation_slots is a tuple (immutable), not a list."""
        model = _PaperSellAccountingRecordV1(
            schema_version="paper_sell_accounting.v1",
            fill_id="fill-001",
            order_id="order-001",
            account_id="acct-001",
            paper_session_id="session-001",
            environment="paper",
            quote_asset="USDT",
            strategy_ref=StrategyRef(
                strategy_id="strategy-001",
                strategy_version="v1",
                decision_id="decision-001",
            ),
            total_qty_filled=Decimal("1.5"),
            proceeds_gross=Decimal("46500.00"),
            fee_asset="USDT",
            fee_amount=Decimal("150.00"),
            proceeds_net=Decimal("46350.00"),
            total_cost_basis_removed=Decimal("45000.00"),
            pnl_gross=Decimal("1500.00"),
            pnl_net=Decimal("1350.00"),
            allocation_slots=(
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-001",
                    qty_sold=Decimal("1.5"),
                    cost_basis_removed=Decimal("45000.00"),
                    proceeds_attributed=Decimal("46500.00"),
                ),
            ),
        )
        assert isinstance(model.allocation_slots, tuple)
        # Verify immutability: the model is frozen so any reassignment raises ValidationError
        with pytest.raises(Exception):  # ValidationError (frozen_instance)
            model.fill_id = "fill-002"

    def test_model_allocation_slots_cannot_be_mutated_in_place(self) -> None:
        """Test that individual slots in the tuple cannot be mutated."""
        model = _PaperSellAccountingRecordV1(
            schema_version="paper_sell_accounting.v1",
            fill_id="fill-001",
            order_id="order-001",
            account_id="acct-001",
            paper_session_id="session-001",
            environment="paper",
            quote_asset="USDT",
            strategy_ref=StrategyRef(
                strategy_id="strategy-001",
                strategy_version="v1",
                decision_id="decision-001",
            ),
            total_qty_filled=Decimal("1.5"),
            proceeds_gross=Decimal("46500.00"),
            fee_asset="USDT",
            fee_amount=Decimal("150.00"),
            proceeds_net=Decimal("46350.00"),
            total_cost_basis_removed=Decimal("45000.00"),
            pnl_gross=Decimal("1500.00"),
            pnl_net=Decimal("1350.00"),
            allocation_slots=(
                _PaperSellAllocationSlotV1(
                    slot_version="sell_allocation_slot.v1",
                    position_id="pos-lot-001",
                    qty_sold=Decimal("1.5"),
                    cost_basis_removed=Decimal("45000.00"),
                    proceeds_attributed=Decimal("46500.00"),
                ),
            ),
        )
        # Tuple items are immutable by nature; verify the slot itself is frozen
        first_slot = model.allocation_slots[0]
        with pytest.raises(Exception):  # FrozenInstanceError or similar
            first_slot.qty_sold = Decimal("999.00")


# ── Test schema v11 tables ────────────────────────────────────────────────

class TestSchemaV11Tables:
    """Tests for schema v11 table existence and structure."""

    def test_schema_v11_tables_set(self) -> None:
        """Test that _SCHEMA_V11_TABLES contains the expected tables."""
        assert "paper_reservation_lot_allocations" in _SCHEMA_V11_TABLES
        assert "paper_sell_accounting" in _SCHEMA_V11_TABLES

    def test_schema_v11_tables_superset_of_v10(self) -> None:
        """Test that v11 tables are a superset of v10 tables."""
        assert _SCHEMA_V10_TABLES <= _SCHEMA_V11_TABLES

    def test_ddl_statements_count(self) -> None:
        """Test that _PAPER_SELL_DDL_STATEMENTS contains 2 statements."""
        assert len(_PAPER_SELL_DDL_STATEMENTS) == 2

    def test_ddl_statements_table_names(self) -> None:
        """Test that DDL statements create the expected tables."""
        table_names = set()
        for ddl in _PAPER_SELL_DDL_STATEMENTS:
            # Extract table name from CREATE TABLE statement
            if "paper_reservation_lot_allocations" in ddl:
                table_names.add("paper_reservation_lot_allocations")
            elif "paper_sell_accounting" in ddl:
                table_names.add("paper_sell_accounting")
        assert table_names == {"paper_reservation_lot_allocations", "paper_sell_accounting"}


# ── Test v10→v11 migration ────────────────────────────────────────────────

class TestV10ToV11Migration:
    """Tests for v10→v11 migration with preservation/rollback."""

    def test_migration_creates_v11_tables(self, temp_db: Path) -> None:
        """Test that migration from v10 to v11 creates the new tables."""
        # Create a v10 database
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Execute v10 DDL statements (from _SCHEMA_V10_TABLES)
        for table_name in _SCHEMA_V10_TABLES:
            if table_name == "paper_fill_applications":
                cursor.execute("""CREATE TABLE paper_fill_applications (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                    account_id TEXT NOT NULL,
                    paper_session_id TEXT NOT NULL,
                    environment TEXT NOT NULL CHECK (environment = 'paper'),
                    applied_at_ms INTEGER NOT NULL,
                    application_json TEXT NOT NULL
                )""")
            elif table_name == "position_lots":
                cursor.execute("""CREATE TABLE position_lots (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    paper_session_id TEXT NOT NULL,
                    environment TEXT NOT NULL CHECK (environment = 'paper'),
                    lot_json TEXT NOT NULL
                )""")
            elif table_name == "paper_orders":
                cursor.execute("""CREATE TABLE paper_orders (
                    order_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACCEPTED', 'FILLED', 'CANCELLED', 'REJECTED')),
                    type TEXT NOT NULL CHECK(type IN ('LIMIT', 'MARKET')),
                    price TEXT NOT NULL,
                    qty TEXT NOT NULL,
                    environment TEXT NOT NULL CHECK (environment = 'paper'),
                    order_json TEXT NOT NULL
                )""")
            elif table_name == "paper_order_acceptances":
                cursor.execute("""CREATE TABLE paper_order_acceptances (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    type TEXT NOT NULL CHECK(type IN ('LIMIT', 'MARKET')),
                    price TEXT NOT NULL,
                    qty TEXT NOT NULL,
                    environment TEXT NOT NULL CHECK (environment = 'paper'),
                    acceptance_json TEXT NOT NULL
                )""")
            elif table_name == "paper_reservations":
                cursor.execute("""CREATE TABLE paper_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    amount TEXT NOT NULL,
                    environment TEXT NOT NULL CHECK (environment = 'paper'),
                    reservation_json TEXT NOT NULL
                )""")

        # Set schema version to 10
        cursor.execute("PRAGMA user_version = 10")
        conn.commit()

        # Now migrate to v11
        for ddl in _PAPER_SELL_DDL_STATEMENTS:
            cursor.execute(ddl)

        # Update schema version to 11
        cursor.execute("PRAGMA user_version = 11")
        conn.commit()
        conn.close()

        # Verify v11 tables exist
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "paper_reservation_lot_allocations" in table_names
        assert "paper_sell_accounting" in table_names

    def test_migration_preserves_v10_data(self, temp_db: Path) -> None:
        """Test that migration preserves existing v10 data."""
        # Create a v10 database with some data
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Create v10 tables and insert test data
        cursor.execute("""CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY,
            client_order_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACCEPTED', 'FILLED', 'CANCELLED', 'REJECTED')),
            type TEXT NOT NULL CHECK(type IN ('LIMIT', 'MARKET')),
            price TEXT NOT NULL,
            qty TEXT NOT NULL,
            environment TEXT NOT NULL CHECK (environment = 'paper'),
            order_json TEXT NOT NULL
        )""")

        cursor.execute("""INSERT INTO paper_orders (order_id, client_order_id, account_id, symbol, side, status, type, price, qty, environment, order_json)
            VALUES ('order-001', 'client-001', 'acct-001', 'BTC-USDT', 'BUY', 'ACCEPTED', 'LIMIT', '50000.00', '1.0', 'paper', '{}')""")

        cursor.execute("PRAGMA user_version = 10")
        conn.commit()

        # Migrate to v11
        for ddl in _PAPER_SELL_DDL_STATEMENTS:
            cursor.execute(ddl)

        cursor.execute("PRAGMA user_version = 11")
        conn.commit()
        conn.close()

        # Verify data is preserved
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT order_id FROM paper_orders WHERE order_id = 'order-001'")
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == "order-001"

    def test_migration_rejects_missing_v10_tables(self, temp_db: Path) -> None:
        """Test that migration rejects databases missing v10 tables.

        Verifies that when a database has user_version=10 but lacks the required
        v10 tables (paper_order_acceptances, paper_reservations), the store's
        initialize() method raises SchemaVersionMismatchError and leaves the
        database version intact.
        """
        # Create a database with user_version=10 but without v10 tables
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()

        # Create only paper_orders table (v8) - missing v10 tables
        cursor.execute("""CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY,
            client_order_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACCEPTED', 'FILLED', 'CANCELLED', 'REJECTED')),
            type TEXT NOT NULL CHECK(type IN ('LIMIT', 'MARKET')),
            price TEXT NOT NULL,
            qty TEXT NOT NULL,
            environment TEXT NOT NULL CHECK (environment = 'paper'),
            order_json TEXT NOT NULL
        )""")

        cursor.execute("PRAGMA user_version = 10")
        conn.commit()

        # Verify the version is set to 10
        cursor.execute("PRAGMA user_version")
        assert cursor.fetchone()[0] == 10

        conn.close()

        # Now try to initialize with SQLiteStore - should fail because v10 tables are missing
        store = SQLiteStore(str(temp_db))

        with pytest.raises(Exception):  # SchemaVersionMismatchError or similar
            _run(store.initialize())

        # Verify that user_version remains intact (rollback occurred)
        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("PRAGMA user_version")
        version_after = cursor.fetchone()[0]
        conn.close()

        # Version should still be 10 (or whatever it was before, as rollback should preserve it)
        assert version_after == 10


# ── Run tests ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
