"""Atomic SQLite store for session, StrategyConfig versions, DatasetRef references,
experiment definitions, run attempts, experiment results, variant dispositions,
result exclusions, paper fills, explicit PAPER account openings/balances, and
initial PAPER order-context records. It also persists durable fill application
marks and aggregated position lots by order_id (schema v10).

This module contains accepted backend persistence slices for ACT-P-001 and the
PaperFill journal slice of ACT-P-002, plus the account-opening slice in EN_REVISION.
Initial PaperOrder context registration/lookup and atomic order acceptance/reservation
are ACT-P-003 and coordinated ACT-P-002 slices in EN_REVISION.
It persists opaque session identity, StrategyConfig JSON (with versioned StrategyRef),
literal DatasetRef strings, immutable
ExperimentDefinition payloads, RunAttempt lifecycle records (pending / running /
failed / cancelled / completed), the experiment_results table (v4), disposition
tables variant_dispositions + result_exclusions (v5), paper fills (v6), explicit
account openings/current balances by asset (v7), and initial order-context lookup
(v8), and atomic BUY order acceptance/reservation (v9). It also persists durable
fill application marks (paper_fill_applications) and aggregated position lots
(position_lots) required by ACT-P-002 fill application (v10). It applies BUY and SELL
fill execution, admits BUY and SELL order cancellation (ACCEPTED / PARTIALLY_FILLED),
and implements SELL lot reservations with paper_reservation_lot_allocations and
paper_sell_accounting tables (v11). It does NOT implement OCO order branching, risk
management, matching, execution, backtesting or replay.

Schema versions:
    v0 — original session/config/dataset schema (no tables by default).
    v1 — same as v0 with explicit DDL.
    v2 — adds experiment_definitions table.
    v3 — adds run_attempts table.
    v4 — adds experiment_results table.
    v5 — adds variant_dispositions and result_exclusions tables.
    v6 — adds paper_fills table.
    v7 — adds explicit paper account openings and current balances by asset.
    v8 — adds initial PAPER order records for order_id-to-context lookup.
    v9 — adds stable order acceptance payloads and active account reservations.
    v10 — adds durable fill application marks (paper_fill_applications) and
        aggregated position lots by order_id (position_lots).
    v11 — adds paper_reservation_lot_allocations and paper_sell_accounting tables.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from decimal import (
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Underflow,
    localcontext,
)
from pathlib import Path
from typing import Any, Literal, Optional

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from src.models.dataset_ref import DatasetRef
from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentResult,
    ResultExclusionRecord,
    RunAttempt,
    TERMINAL_STATES,
    VALID_TRANSITIONS,
    validate_transition,
    VariantDiscardRecord,
)
from src.models.paper_contracts import (
    AssetBalance,
    Commission,
    PaperDecimal,
    PaperAccountOpening,
    PaperBalanceView,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    PaperReservationRequest,
    ReservationAmount,
    StrategyRef,
    _POSITION_AVERAGE_CONTEXT,
    _exact_product,
    _exact_sum,
)
from src.models.strategy_config import StrategyConfig

# ── AssetBalance JSON loader con precisión contractual ───────────────────────────

def _asset_balance_json_to_model(json_str: str) -> AssetBalance:
    """Parse JSON *and* validate under sufficient precision so the validator
    (total == available + reserved) sees exact operands even when the global
    decimal context is restricted (e.g. prec=6, ROUND_DOWN).

    Precision is derived contractually from the JSON payload itself: the
    maximum adjusted exponent minus the minimum exponent plus one, which is
    exactly the number of significant digits required to represent the
    largest operand without loss.  This avoids hard-coding a magic number.
    """
    raw = json.loads(json_str)
    total = Decimal(str(raw["total"]))
    avail = Decimal(str(raw["available"]))
    reserved = Decimal(str(raw["reserved"]))

    # Contractual bound: precision must cover all three fields exactly.
    min_exp = min(total.as_tuple().exponent, avail.as_tuple().exponent, reserved.as_tuple().exponent)
    max_adj = max(total.adjusted(), avail.adjusted(), reserved.adjusted())
    required_prec = max(1, max_adj - min_exp + 1)

    ctx = Context(prec=required_prec, rounding=ROUND_HALF_EVEN,
                  Emin=-999999999, Emax=999999999, clamp=0,
                  traps=[Clamped, DivisionByZero, Inexact,
                         InvalidOperation, Overflow, Underflow])
    with localcontext(ctx):
        return AssetBalance.model_validate_json(json_str)


def _asset_balance_construct(asset: str, total: Decimal, available: Decimal, reserved: Decimal) -> AssetBalance:
    """Construct an AssetBalance under sufficient precision so the validator
    (total == available + reserved) sees exact operands even when the global
    decimal context is restricted.

    Precision is derived contractually from the provided values.
    """
    vals = [total, available, reserved]
    min_exp = min(v.as_tuple().exponent for v in vals)
    max_adj = max(v.adjusted() for v in vals)
    required_prec = max(1, max_adj - min_exp + 1)

    ctx = Context(prec=required_prec, rounding=ROUND_HALF_EVEN,
                  Emin=-999999999, Emax=999999999, clamp=0,
                  traps=[Clamped, DivisionByZero, Inexact,
                         InvalidOperation, Overflow, Underflow])
    with localcontext(ctx):
        return AssetBalance(asset=asset, total=total, available=available, reserved=reserved)


# ── Schema version ────────────────────────────────────────────────────────────────

_SCHEMA_VERSION = 11
_DATASET_REF_ADAPTER = TypeAdapter(DatasetRef)
_PAPER_FILL_ADAPTER = TypeAdapter(PaperFill)
_PAPER_ACCOUNT_OPENING_ADAPTER = TypeAdapter(PaperAccountOpening)
_PAPER_ORDER_ADAPTER = TypeAdapter(PaperOrder)


# ── PositionLot (v10 schema model) ───────────────────────────────────────────

class PositionLot(BaseModel):
    """Aggregated position lot by order_id."""

    order_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    paper_session_id: str = Field(..., min_length=1)
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    total_qty: Decimal = Field(..., ge=Decimal("0"))
    total_cost: Decimal = Field(..., ge=Decimal("0"))


# ── Internal versioned Pydantic models (v11) ───────────────────────────────


class _PaperLotReservationAllocationV1(BaseModel):
    """Internal v1 model for paper_reservation_lot_allocations rows.

    schema_version is frozen to "paper_lot_reservation_allocation.v1".
    Validates that allocation context matches the active reservation and lot.
    Context validation (account/session/environment) happens in the store layer
    by cross-referencing reservation, order, and lot — not by deriving fields
    from position_id text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["paper_lot_reservation_allocation.v1"] = Field(
        default="paper_lot_reservation_allocation.v1", frozen=True
    )
    reservation_id: str = Field(..., min_length=1)
    owner_order_id: str = Field(..., min_length=1)
    position_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    paper_session_id: str = Field(..., min_length=1)
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    symbol: str = Field(..., min_length=3)
    base_asset: str = Field(..., min_length=1)
    strategy_ref: StrategyRef
    initial_qty: PaperDecimal = Field(..., gt=Decimal("0"))
    remaining_qty: PaperDecimal = Field(..., gt=Decimal("0"))

    @model_validator(mode="after")
    def validate_strategy_ref(self) -> "_PaperLotReservationAllocationV1":
        """Validate fields intrinsic to one active lot reservation."""
        if not self.strategy_ref.strategy_id or not self.strategy_ref.strategy_version:
            raise ValueError(
                "strategy_ref must have non-empty strategy_id and strategy_version"
            )
        parts = self.symbol.split("/")
        if len(parts) != 2 or parts[0] != self.base_asset:
            raise ValueError("base_asset must match the BASE component of symbol")
        if self.remaining_qty > self.initial_qty:
            raise ValueError("remaining_qty cannot exceed initial_qty")
        return self


class _PaperSellAllocationSlotV1(BaseModel):
    """One lot-allocation slot inside a PaperSellAccountingRecordV1.

    Represents a single position_lot consumed by a sell fill, with its own
    quantity sold, cost basis removed, and per-slot proceeds attribution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slot_version: Literal["sell_allocation_slot.v1"] = Field(
        default="sell_allocation_slot.v1", frozen=True
    )
    position_id: str = Field(..., min_length=1)
    qty_sold: PaperDecimal = Field(..., gt=Decimal("0"))
    cost_basis_removed: PaperDecimal = Field(..., ge=Decimal("0"))
    proceeds_attributed: PaperDecimal = Field(..., ge=Decimal("0"))


class _PaperSellAccountingRecordV1(BaseModel):
    """Internal v1 model for paper_sell_accounting rows.

    One row per fill_id containing a typed/versioned sequence of lot-allocation
    slots plus aggregate financials (gross proceeds, fee in quote asset, net
    proceeds, total cost basis removed, gross and net P&L).

    Invariants enforced at construction:
      - sum(slot.qty_sold) == total_qty_filled  (checked by caller/store)
      - sum(slot.cost_basis_removed) == total_cost_basis_removed
      - sum(slot.proceeds_attributed) == proceeds_gross
      - proceeds_net = proceeds_gross - fee_amount
      - pnl_gross = proceeds_gross - total_cost_basis_removed
      - pnl_net = proceeds_net - total_cost_basis_removed
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["paper_sell_accounting.v1"] = Field(
        default="paper_sell_accounting.v1", frozen=True
    )
    fill_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    paper_session_id: str = Field(..., min_length=1)
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    quote_asset: str = Field(..., min_length=1)
    strategy_ref: StrategyRef

    # Aggregate fields
    total_qty_filled: PaperDecimal = Field(..., gt=Decimal("0"))
    proceeds_gross: PaperDecimal = Field(..., gt=Decimal("0"))
    fee_asset: str = Field(..., min_length=1)
    fee_amount: PaperDecimal = Field(..., ge=Decimal("0"))
    proceeds_net: PaperDecimal = Field(..., ge=Decimal("0"))
    total_cost_basis_removed: PaperDecimal = Field(..., ge=Decimal("0"))
    # Realized P&L is signed; the finite Decimal contract applies without an
    # arbitrary lower bound.
    pnl_gross: PaperDecimal
    pnl_net: PaperDecimal

    # Typed immutable sequence of per-lot allocation slots (frozen via tuple)
    allocation_slots: tuple[_PaperSellAllocationSlotV1, ...] = Field(
        default_factory=tuple, frozen=True
    )

    @model_validator(mode="after")
    def validate_quote_context(self) -> "_PaperSellAccountingRecordV1":
        """The sell fee must be known in the symbol's QUOTE asset."""
        if self.fee_asset != self.quote_asset:
            raise ValueError("sell commission must use the quote asset")
        return self

    @model_validator(mode="after")
    def validate_proceeds_net(self) -> "_PaperSellAccountingRecordV1":
        """proceeds_net must equal proceeds_gross - fee_amount (explicit arithmetic)."""
        expected = _exact_sum(
            (self.proceeds_gross, self.fee_amount.copy_negate())
        )
        if self.proceeds_net != expected:
            raise ValueError(
                f"proceeds_net ({self.proceeds_net}) must equal "
                f"proceeds_gross ({self.proceeds_gross}) - fee_amount ({self.fee_amount})"
            )
        return self

    @model_validator(mode="after")
    def validate_pnl(self) -> "_PaperSellAccountingRecordV1":
        """Validate gross and net P&L using explicit arithmetic."""
        expected_gross = _exact_sum(
            (self.proceeds_gross, self.total_cost_basis_removed.copy_negate())
        )
        if self.pnl_gross != expected_gross:
            raise ValueError(
                f"pnl_gross ({self.pnl_gross}) must equal "
                f"proceeds_gross ({self.proceeds_gross}) - total_cost_basis_removed "
                f"({self.total_cost_basis_removed})"
            )
        expected_net = _exact_sum(
            (self.proceeds_net, self.total_cost_basis_removed.copy_negate())
        )
        if self.pnl_net != expected_net:
            raise ValueError(
                f"pnl_net ({self.pnl_net}) must equal "
                f"proceeds_net ({self.proceeds_net}) - total_cost_basis_removed "
                f"({self.total_cost_basis_removed})"
            )
        return self

    @model_validator(mode="after")
    def validate_allocation_slots_sum(self) -> "_PaperSellAccountingRecordV1":
        """Validate that allocation slots sum to aggregate quantities."""
        if not self.allocation_slots:
            raise ValueError("allocation_slots must contain at least one slot")
        position_ids = [slot.position_id for slot in self.allocation_slots]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("allocation_slots cannot repeat a position_id")

        total_qty = _exact_sum(tuple(slot.qty_sold for slot in self.allocation_slots))
        total_cost = _exact_sum(
            tuple(slot.cost_basis_removed for slot in self.allocation_slots)
        )
        total_proceeds = _exact_sum(
            tuple(slot.proceeds_attributed for slot in self.allocation_slots)
        )
        if total_qty != self.total_qty_filled:
            raise ValueError(
                f"sum(slot.qty_sold)={total_qty} must equal "
                f"total_qty_filled={self.total_qty_filled}"
            )
        if total_cost != self.total_cost_basis_removed:
            raise ValueError(
                f"sum(slot.cost_basis_removed)={total_cost} must equal "
                f"total_cost_basis_removed={self.total_cost_basis_removed}"
            )
        if total_proceeds != self.proceeds_gross:
            raise ValueError(
                f"sum(slot.proceeds_attributed)={total_proceeds} must equal "
                f"proceeds_gross={self.proceeds_gross}"
            )
        return self

    @model_validator(mode="after")
    def validate_fee_asset_matches_quote(self) -> "_PaperSellAccountingRecordV1":
        """fee_asset must equal quote_asset for sell accounting records."""
        if self.fee_asset != self.quote_asset:
            raise ValueError(
                f"fee_asset ({self.fee_asset}) must equal quote_asset ({self.quote_asset})"
            )
        return self

# ── Custom exceptions ─────────────────────────────────────────────────────────────


class StoreClosedError(RuntimeError):
    """Raised when an operation is attempted on a closed store."""


class ConfigConflictError(ValueError):
    """Same StrategyRef with different JSON — append-only conflict."""


class ExperimentDefinitionConflictError(ValueError):
    """Same ExperimentRef with different immutable definition JSON."""


class ExperimentDefinitionIntegrityError(ValueError):
    """Stored definition payload does not match its primary-key identity."""


class SchemaVersionMismatchError(RuntimeError):
    """Database user_version does not match the expected schema version."""


class RunAttemptConflictError(ValueError):
    """Same run_id with different content — no overwrite allowed."""


class RunAttemptStateError(ValueError):
    """Invalid state transition or terminal-state violation."""


class RunAttemptIntegrityError(ValueError):
    """Stored run JSON does not match its indexed identity or status."""


class ExperimentResultConflictError(ValueError):
    """Same result_id with different content — no overwrite allowed."""


class ExperimentResultStateError(ValueError):
    """Invalid state for experiment result operations."""


class VariantDiscardConflictError(ValueError):
    """Same variant disposition with different content — no overwrite allowed."""


class ResultExclusionConflictError(ValueError):
    """Same result exclusion with different content — no overwrite allowed."""


class PaperFillConflictError(ValueError):
    """Same fill_id with different content — no overwrite allowed."""


class PaperFillIntegrityError(ValueError):
    """Stored fill JSON does not match its indexed identity or payload."""


class PaperAccountOpeningConflictError(ValueError):
    """Account already has a different immutable opening snapshot."""


class PaperAccountOpeningIntegrityError(ValueError):
    """Stored opening or balance JSON does not match its indexed identity."""


class PaperOrderContextConflictError(ValueError):
    """Order identity or client idempotency key already has different content."""


class PaperOrderContextIntegrityError(ValueError):
    """Stored order JSON does not match its indexed identity/context."""


class PaperOrderContextStateError(ValueError):
    """Order registration is limited to its initial PREPARED state."""


class PaperOrderAcceptanceConflictError(ValueError):
    """Order acceptance identity already has a different stable payload."""


class PaperOrderAcceptanceFundsError(ValueError):
    """Account has no matching balance or insufficient available funds."""


class PaperOrderAcceptanceStateError(ValueError):
    """Order cannot be accepted in its current state or order type."""


class PaperOrderAcceptanceIntegrityError(ValueError):
    """Stored acceptance/reservation payload does not match its indexed identity."""


class PaperOrderAcceptanceScopeError(ValueError):
    """Order is outside the authorized BUY LIMIT/MARKET acceptance slice."""


class PaperOrderCancellationStateError(ValueError):
    """Order cannot be cancelled in its current state or is not a BUY order."""


class PaperOrderCancellationConflictError(ValueError):
    """Order cancellation identity already has a different stable payload."""


# ── SQL DDL (idempotent) ──────────────────────────────────────────────────────────

_SCHEMA_V1_STATEMENTS = (
    """CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY NOT NULL
    )""",
    """CREATE TABLE strategy_config_versions (
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        config_json TEXT NOT NULL,
        PRIMARY KEY (strategy_id, strategy_version)
    )""",
    """CREATE TABLE session_strategy_configs (
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        strategy_id TEXT NOT NULL,
        strategy_version TEXT NOT NULL,
        PRIMARY KEY (session_id, strategy_id, strategy_version),
        FOREIGN KEY (strategy_id, strategy_version)
            REFERENCES strategy_config_versions(strategy_id, strategy_version)
    )""",
    """CREATE TABLE session_dataset_refs (
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        dataset_ref TEXT NOT NULL,
        PRIMARY KEY (session_id, dataset_ref)
    )""",
)

_SCHEMA_V2_STATEMENTS = (
    """CREATE TABLE experiment_definitions (
        experiment_id TEXT NOT NULL,
        experiment_version TEXT NOT NULL,
        definition_json TEXT NOT NULL,
        PRIMARY KEY (experiment_id, experiment_version)
    )""",
)

_SCHEMA_V3_STATEMENTS = (
    """CREATE TABLE run_attempts (
        run_id TEXT PRIMARY KEY,
        experiment_id TEXT NOT NULL,
        experiment_version TEXT NOT NULL,
        variant_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')),
        run_json TEXT NOT NULL,
        FOREIGN KEY (experiment_id, experiment_version)
            REFERENCES experiment_definitions(experiment_id, experiment_version)
    )""",
)

_SCHEMA_V4_STATEMENTS = (
    """CREATE TABLE experiment_results (
        result_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(run_id),
        result_json TEXT NOT NULL
    )""",
)

_SCHEMA_V5_STATEMENTS = (
    """CREATE TABLE variant_dispositions (
        experiment_id TEXT NOT NULL,
        experiment_version TEXT NOT NULL,
        variant_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        PRIMARY KEY (experiment_id, experiment_version, variant_id),
        FOREIGN KEY (experiment_id, experiment_version)
            REFERENCES experiment_definitions(experiment_id, experiment_version)
    )""",
    """CREATE TABLE result_exclusions (
        result_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL,
        FOREIGN KEY (result_id)
            REFERENCES experiment_results(result_id)
    )""",
)

_SCHEMA_V1_TABLES = {
    "sessions",
    "strategy_config_versions",
    "session_strategy_configs",
    "session_dataset_refs",
}
_SCHEMA_V2_TABLES = _SCHEMA_V1_TABLES | {"experiment_definitions"}
_SCHEMA_V3_TABLES = _SCHEMA_V2_TABLES | {"run_attempts"}
_SCHEMA_V4_TABLES = _SCHEMA_V3_TABLES | {"experiment_results"}
_SCHEMA_V5_TABLES = _SCHEMA_V4_TABLES | {"variant_dispositions", "result_exclusions"}
_SCHEMA_V6_TABLES = _SCHEMA_V5_TABLES | {"paper_fills"}
_SCHEMA_V7_TABLES = _SCHEMA_V6_TABLES | {
    "paper_account_openings",
    "paper_account_balances",
}
_SCHEMA_V8_TABLES = _SCHEMA_V7_TABLES | {"paper_orders"}
_SCHEMA_V9_TABLES = _SCHEMA_V8_TABLES | {
    "paper_order_acceptances",
    "paper_reservations",
}
_SCHEMA_V10_TABLES = _SCHEMA_V9_TABLES | {
    "paper_fill_applications",
    "position_lots",
}
_SCHEMA_V11_TABLES = _SCHEMA_V10_TABLES | {
    "paper_reservation_lot_allocations",
    "paper_sell_accounting",
}

_PAPER_FILL_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_fills (
        fill_id TEXT PRIMARY KEY,
        record_json TEXT NOT NULL
    )""",
)

_PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_account_openings (
        account_id TEXT PRIMARY KEY,
        opening_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS paper_account_balances (
        account_id TEXT NOT NULL,
        asset TEXT NOT NULL,
        balance_json TEXT NOT NULL,
        PRIMARY KEY (account_id, asset),
        FOREIGN KEY (account_id) REFERENCES paper_account_openings(account_id)
    )""",
)

_PAPER_ORDER_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_orders (
        order_id TEXT PRIMARY KEY,
        paper_account_id TEXT NOT NULL,
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        client_order_id TEXT NOT NULL,
        order_json TEXT NOT NULL,
        UNIQUE (paper_account_id, paper_session_id, environment, client_order_id)
    )""",
)

_PAPER_ORDER_RESERVATION_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_order_acceptances (
        order_id TEXT PRIMARY KEY REFERENCES paper_orders(order_id),
        reservation_id TEXT NOT NULL UNIQUE,
        paper_account_id TEXT NOT NULL,
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        client_order_id TEXT NOT NULL,
        initial_order_json TEXT NOT NULL,
        acceptance_payload_json TEXT NOT NULL,
        reservation_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS paper_reservations (
        reservation_id TEXT PRIMARY KEY REFERENCES paper_order_acceptances(reservation_id),
        owner_order_id TEXT NOT NULL UNIQUE REFERENCES paper_orders(order_id),
        account_id TEXT NOT NULL,
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        asset TEXT NOT NULL,
        amount_json TEXT NOT NULL,
        reservation_json TEXT NOT NULL
    )""",
)

_PAPER_FILL_APPLICATION_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_fill_applications (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
        account_id TEXT NOT NULL,
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        applied_at_ms INTEGER NOT NULL,
        application_json TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS position_lots (
        order_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        lot_json TEXT NOT NULL
    )""",
)

_PAPER_SELL_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS paper_reservation_lot_allocations (
        reservation_id TEXT NOT NULL
            REFERENCES paper_reservations(reservation_id) ON DELETE CASCADE,
        owner_order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
        position_id TEXT NOT NULL REFERENCES position_lots(order_id),
        account_id TEXT NOT NULL REFERENCES paper_account_openings(account_id),
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        symbol TEXT NOT NULL,
        base_asset TEXT NOT NULL,
        strategy_ref_json TEXT NOT NULL,
        initial_qty TEXT NOT NULL,
        remaining_qty TEXT NOT NULL,
        allocation_json TEXT NOT NULL,
        PRIMARY KEY (reservation_id, position_id)
    )""",
    """CREATE TABLE IF NOT EXISTS paper_sell_accounting (
        fill_id TEXT PRIMARY KEY REFERENCES paper_fill_applications(fill_id),
        order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
        account_id TEXT NOT NULL REFERENCES paper_account_openings(account_id),
        paper_session_id TEXT NOT NULL,
        environment TEXT NOT NULL CHECK (environment = 'paper'),
        quote_asset TEXT NOT NULL,
        strategy_ref_json TEXT NOT NULL,
        total_qty_filled TEXT NOT NULL,
        proceeds_gross TEXT NOT NULL,
        fee_asset TEXT NOT NULL,
        fee_amount TEXT NOT NULL,
        proceeds_net TEXT NOT NULL,
        total_cost_basis_removed TEXT NOT NULL,
        pnl_gross TEXT NOT NULL,
        pnl_net TEXT NOT NULL,
        allocation_slots_json TEXT NOT NULL,
        accounting_json TEXT NOT NULL,
        CHECK (fee_asset = quote_asset)
    )""",
)

# ── SQLiteStore ────────────────────────────────────────────────────────────────────


class SQLiteStore:
    """Thin async wrapper around a single SQLite file with atomic writes.

    Writes are serialized via an instance-level asyncio.Lock so that
    concurrent ``save_session`` calls on the *same* store never interleave.
    Different stores (different DB paths) have independent locks.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._lock = asyncio.Lock()
        self._closed = False
        self._initialized = False

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Open (or reopen) the database, run migration, return silently."""
        if self._closed:
            raise StoreClosedError("store is closed")
        await self._write(self._initialize_inner)
        self._initialized = True

    async def close(self) -> None:
        """Signal closure; rejects further writes."""
        self._closed = True

    # ── public API ─────────────────────────────────────────────────────────

    async def save_session(
        self,
        session_id: str,
        strategy_configs: Sequence[StrategyConfig],
        dataset_refs: Sequence[DatasetRef],
    ) -> None:
        """Atomically persist *session_id* with its configs and refs.

        Parameters
        ----------
        session_id :
            Opaque session identifier.
        strategy_configs :
            Accepted, validated ``StrategyConfig`` instances.
        dataset_refs :
            Opaque references validated using the accepted ``DatasetRef`` type.

        Raises
        ------
        ConfigConflictError
            If any strategy_config entry has the same ``(strategy_id,
            strategy_version)`` but a *different* ``config_json`` than what is
            already persisted.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if any(not isinstance(config, StrategyConfig) for config in strategy_configs):
            raise TypeError("strategy_configs must contain StrategyConfig instances")
        validated_refs = tuple(
            _DATASET_REF_ADAPTER.validate_python(ref) for ref in dataset_refs
        )
        await self._write(
            lambda conn: self._save_session_inner(
                conn, session_id, tuple(strategy_configs), validated_refs
            )
        )

    async def load_session(self, session_id: str) -> dict[str, Any] | None:
        """Return the persisted snapshot for *session_id*, or ``None``."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        return await self._read(lambda conn: self._load_session_inner(conn, session_id))

    async def register_experiment(
        self, definition: ExperimentDefinition
    ) -> ExperimentDefinition:
        """Persist one immutable experiment definition, idempotently by its ref."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(definition, ExperimentDefinition):
            raise TypeError("definition must be an ExperimentDefinition instance")
        validated = ExperimentDefinition.model_validate(definition.model_dump(mode="python"))
        return await self._write(
            lambda conn: self._register_experiment_inner(conn, validated)
        )

    async def load_experiment(
        self, experiment_ref: ExperimentRef
    ) -> ExperimentDefinition | None:
        """Load and validate one immutable experiment definition by reference."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(experiment_ref, ExperimentRef):
            raise TypeError("experiment_ref must be an ExperimentRef instance")
        ref = ExperimentRef.model_validate(experiment_ref.model_dump(mode="python"))
        return await self._read(
            lambda conn: self._load_experiment_inner(conn, ref)
        )

    async def create_run_attempt(self, run: RunAttempt) -> RunAttempt:
        """Persist a new run attempt or return the existing one if idempotent.

        Parameters
        ----------
        run :
            A validated ``RunAttempt`` whose status MUST be ``pending``.

        Raises
        ------
        ValueError
            If *run* is not in ``pending`` state, or if the experiment/variant
            does not exist, or if a different payload already occupies *run_id*.
        RunAttemptStateError
            If the referenced variant has a persisted pre-run discard decision.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(run, RunAttempt):
            raise TypeError("run must be a RunAttempt instance")

        # Enforce pending-only at the API level.
        if run.status != "pending":
            raise ValueError("create_run_attempt only accepts pending runs")

        validated = RunAttempt.model_validate(run.model_dump(mode="python"))
        return await self._write(
            lambda conn: self._create_run_attempt_inner(conn, validated)
        )

    async def transition_run_attempt(
        self, run_id: str, target: RunAttempt
    ) -> RunAttempt:
        """Transition a persisted run to a new state.

        Parameters
        ----------
        run_id :
            The primary-key of the row to transition.
        target :
            A validated ``RunAttempt`` whose ``run_id`` and ``status`` define
            the destination identity and state.

        Raises
        ------
        RunAttemptStateError
            On invalid transitions, stale state, identity mismatch, or attempt
            to reach ``completed`` (which requires publishing an
            ``ExperimentResult`` outside this slice).
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(target, RunAttempt):
            raise TypeError("target must be a RunAttempt instance")

        validated = RunAttempt.model_validate(target.model_dump(mode="python"))
        return await self._write(
            lambda conn: self._transition_run_attempt_inner(conn, run_id, validated)
        )

    async def load_run_attempt(self, run_id: str) -> RunAttempt | None:
        """Load and validate one run attempt by its opaque run_id."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        return await self._read(lambda conn: self._load_run_attempt_inner(conn, run_id))

    async def publish_experiment_result(
        self, completed_run: RunAttempt, result: ExperimentResult
    ) -> None:
        """Atomically persist an experiment result and mark the run as completed.

        Parameters
        ----------
        completed_run :
            A validated ``RunAttempt`` whose status MUST be ``completed``.
        result :
            A validated ``ExperimentResult`` with matching ``run_id`` / ``result_id``.

        Raises
        ------
        ExperimentResultStateError
            If the run is not in ``completed`` state, or if the stored run
            identity/context has changed since *completed_run* was produced.
        ExperimentResultConflictError
            If a different result already occupies *result_id*, or if the
            same *(run_id, result_id)* pair exists with different content
            (idempotency check).
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(completed_run, RunAttempt):
            raise TypeError("completed_run must be a RunAttempt instance")
        if not isinstance(result, ExperimentResult):
            raise TypeError("result must be an ExperimentResult instance")

        # Re-validate both DTOs.
        validated_run = RunAttempt.model_validate(
            completed_run.model_dump(mode="python")
        )
        validated_result = ExperimentResult.model_validate(
            result.model_dump(mode="python")
        )

        # Verify run is completed.
        if validated_run.status != "completed":
            raise ExperimentResultStateError(
                f"run {validated_run.run_id} status is '{validated_run.status}'; "
                "must be 'completed' to publish a result"
            )

        # Verify run_id/result_id match.
        if validated_run.run_id != validated_result.run_id:
            raise ExperimentResultStateError(
                f"run_id mismatch: run={validated_run.run_id}, result.run_id={validated_result.run_id}"
            )
        if validated_run.result_id != validated_result.result_id:
            raise ExperimentResultStateError(
                f"result_id mismatch: run.result_id={validated_run.result_id}, "
                f"result.result_id={validated_result.result_id}"
            )

        # Verify artifact_refs is not empty.
        if not validated_result.artifact_refs:
            raise ExperimentResultStateError(
                "artifact_refs must contain at least one entry"
            )

        await self._write(
            lambda conn: self._publish_experiment_result_inner(
                conn, validated_run, validated_result
            )
        )

    async def load_experiment_result(self, run_id: str) -> ExperimentResult | None:
        """Load and validate an experiment result linked to *run_id*, or ``None``."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_experiment_result_inner(conn, run_id)
        )

    async def record_variant_discard(self, record: VariantDiscardRecord) -> None:
        """Persist a variant discard decision atomically.

        Parameters
        ----------
        record :
            A validated ``VariantDiscardRecord`` whose identity is
            ``(experiment_id, experiment_version, variant_id)``.

        Raises
        ------
        ExperimentDefinitionConflictError
            If the referenced experiment definition does not exist.
        VariantDiscardConflictError
            If a different disposition already occupies this identity, or if
            any ``RunAttempt`` exists for *variant_id* (post-run guard).
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(record, VariantDiscardRecord):
            raise TypeError("record must be a VariantDiscardRecord instance")

        validated = VariantDiscardRecord.model_validate(
            record.model_dump(mode="python")
        )
        await self._write(
            lambda conn: self._record_variant_discard_inner(conn, validated)
        )

    async def load_variant_discard(
        self, experiment_ref: ExperimentRef, variant_id: str
    ) -> VariantDiscardRecord | None:
        """Load a variant discard record by its identity, or ``None``."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(experiment_ref, ExperimentRef):
            raise TypeError("experiment_ref must be an ExperimentRef instance")
        ref = ExperimentRef.model_validate(
            experiment_ref.model_dump(mode="python")
        )
        return await self._read(
            lambda conn: self._load_variant_discard_inner(conn, ref, variant_id)
        )

    async def record_result_exclusion(self, record: ResultExclusionRecord) -> None:
        """Persist a result exclusion decision atomically.

        Parameters
        ----------
        record :
            A validated ``ResultExclusionRecord`` whose identity is *result_id*.

        Raises
        ------
        ExperimentResultStateError
            If the referenced ``ExperimentResult`` does not exist.
        ResultExclusionConflictError
            If a different exclusion already occupies this *result_id*.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(record, ResultExclusionRecord):
            raise TypeError("record must be a ResultExclusionRecord instance")

        validated = ResultExclusionRecord.model_validate(
            record.model_dump(mode="python")
        )
        await self._write(
            lambda conn: self._record_result_exclusion_inner(conn, validated)
        )

    async def load_result_exclusion(
        self, result_id: str
    ) -> ResultExclusionRecord | None:
        """Load a result exclusion record by *result_id*, or ``None``."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(result_id, str) or not result_id.strip():
            raise ValueError("result_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_result_exclusion_inner(conn, result_id)
        )

    async def save_paper_fill(self, fill: PaperFill) -> None:
        """Persist one immutable paper fill idempotently by its *fill_id*.

        Parameters
        ----------
        fill :
            A validated ``PaperFill`` whose identity is *fill_id*.

        Raises
        ------
        PaperFillConflictError
            If a different payload already occupies *fill_id* (conflict).
        PaperFillIntegrityError
            If the stored JSON does not match its primary-key identity.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(fill, PaperFill):
            raise TypeError("fill must be a PaperFill instance")

        # Revalidate the full payload rather than trusting an existing model
        # instance: ``validate_python(instance)`` can skip validation when a
        # Pydantic model was constructed with ``model_construct``. Dumping in
        # Python mode keeps Decimal values intact and recursively turns nested
        # models into data that the adapter validates again.
        validated = _PAPER_FILL_ADAPTER.validate_python(
            fill.model_dump(mode="python")
        )
        await self._write(
            lambda conn: self._save_paper_fill_inner(conn, validated)
        )

    async def load_paper_fill(self, fill_id: str) -> PaperFill | None:
        """Load and validate one paper fill by its *fill_id*, or ``None``."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(fill_id, str) or not fill_id.strip():
            raise ValueError("fill_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_paper_fill_inner(conn, fill_id)
        )

    async def open_paper_account(
        self, opening: PaperAccountOpening
    ) -> PaperAccountOpening:
        """Create one explicit, immutable account opening and its initial balances.

        The opening record is kept separately from the current per-asset
        balances. Repeating the original payload returns that record without
        resetting balances that may have changed in a later ledger task.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(opening, PaperAccountOpening):
            raise TypeError("opening must be a PaperAccountOpening instance")

        validated = _PAPER_ACCOUNT_OPENING_ADAPTER.validate_python(
            opening.model_dump(mode="python")
        )
        return await self._write(
            lambda conn: self._open_paper_account_inner(conn, validated)
        )

    async def load_paper_account_opening(
        self, account_id: str
    ) -> PaperAccountOpening | None:
        """Load the immutable opening snapshot for *account_id*, if present."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be a non-empty, non-blank string")
        return await self._read(
            lambda conn: self._load_paper_account_opening_inner(conn, account_id)
        )

    async def load_paper_balance_view(
        self, account_id: str
    ) -> PaperBalanceView | None:
        """Load the current per-asset balances for *account_id*, if opened."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("account_id must be a non-empty, non-blank string")
        return await self._read(
            lambda conn: self._load_paper_balance_view_inner(conn, account_id)
        )

    async def register_paper_order(self, order: PaperOrder) -> PaperOrder:
        """Persist one initial PAPER order context; no lifecycle transitions."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(order, PaperOrder):
            raise TypeError("order must be a PaperOrder instance")

        validated = _PAPER_ORDER_ADAPTER.validate_python(
            order.model_dump(mode="python")
        )
        if validated.status != PaperOrderStatus.PREPARED or validated.fills:
            raise PaperOrderContextStateError(
                "initial order-context registration only accepts PREPARED orders without fills"
            )
        return await self._write(
            lambda conn: self._register_paper_order_inner(conn, validated)
        )

    async def load_paper_order(self, order_id: str) -> PaperOrder | None:
        """Load an initial order record by its store-unique *order_id*."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_paper_order_inner(conn, order_id)
        )

    async def accept_paper_order(self, order_id: str) -> PaperOrder:
        """Atomically reserve BUY quote or SELL base and accept one order.

        The service derives the reservation from the persisted order and current
        account balance; callers cannot supply a reserve amount or stale balance.
        Retries use the immutable acceptance record and never recompute a reserve.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id must be a non-empty string")
        return await self._write(
            lambda conn: self._accept_paper_order_inner(conn, order_id)
        )

    async def load_paper_reservation(
        self, reservation_id: str
    ) -> PaperReservationRef | None:
        """Load the current active reservation, if it has not been consumed/released."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_paper_reservation_inner(conn, reservation_id)
        )

    async def apply_paper_fill(self, fill_id: str) -> PaperFill:
        """Apply one already-journalized paper fill atomically.

        Updates in a single transaction:
          - durable application mark (paper_fill_applications)
          - QUOTE balance (principal + commission debit, reserve release)
          - BASE balance (qty credit)
          - position_lots aggregation
          - order status to FILLED / PARTIALLY_FILLED

        Idempotent: repeated calls with the same fill_id are a no-op after first apply.
        Rejects: fill not found, already applied, order not ACCEPTED, insufficient funds/fee.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(fill_id, str) or not fill_id.strip():
            raise ValueError("fill_id must be a non-empty string")
        return await self._write(
            lambda conn: self._apply_paper_fill_inner(conn, fill_id)
        )

    async def cancel_paper_order(self, order_id: str) -> PaperOrder:
        """Cancel the remaining quantity of one accepted BUY or SELL order.

        Admits only orders in ACCEPTED or PARTIALLY_FILLED state.  Changes
        status to CANCELLED, preserves fills / executed_qty / lot / cost /
        inventory / commissions already accounted for, and releases the active
        reservation remainder (total unchanged, reserved decreases by the
        remainder, available increases by the same amount).

        Idempotent: repeating cancellation of an order already CANCELLED with
        its reservation already released returns the stored state without side
        effects.  FILLED / REJECTED / EXPIRED are rejected without changes.
        """
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id must be a non-empty string")
        return await self._write(
            lambda conn: self._cancel_paper_order_inner(conn, order_id)
        )

    @staticmethod
    async def _cancel_paper_order_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> PaperOrder:
        """Dispatch cancellation to BUY or SELL handler after loading the order."""
        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders WHERE order_id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PaperOrderAcceptanceStateError(
                f"paper order {order_id} not found"
            )
        order = SQLiteStore._validate_paper_order_row(row)
        if order.side == PaperOrderSide.SELL:
            return await SQLiteStore._cancel_paper_sell_order_inner(conn, order_id)
        return await SQLiteStore._cancel_paper_order_buy_inner(conn, order_id)

    async def load_paper_reservation(
        self, reservation_id: str
    ) -> PaperReservationRef | None:
        """Load the current active reservation, if it has not been consumed/released."""
        if self._closed:
            raise StoreClosedError("store is closed")
        if not self._initialized:
            raise RuntimeError("store must be initialized before use")
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        return await self._read(
            lambda conn: self._load_paper_reservation_inner(conn, reservation_id)
        )

    async def _write(self, fn, *args: Any) -> Any:
        """Run *fn(conn, *args)* inside the lock + BEGIN IMMEDIATE."""
        async with self._lock:
            conn = await aiosqlite.connect(self._db_path)
            try:
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA busy_timeout = 5000")
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    result = await fn(conn, *args)
                    await conn.commit()
                    return result
                except BaseException:
                    await conn.rollback()
                    raise
            finally:
                await conn.close()

    # ── variant disposition helpers (v5) ───────────────────────────────────

    @staticmethod
    async def _record_variant_discard_inner(
        conn: aiosqlite.Connection,
        record: VariantDiscardRecord,
    ) -> None:
        """Atomically persist a VariantDiscardRecord with preconditions."""
        exp_id = record.experiment_ref.experiment_id
        exp_ver = record.experiment_ref.experiment_version
        variant_id = record.variant_id

        # Verify the referenced experiment definition exists.
        cursor = await conn.execute(
            "SELECT definition_json FROM experiment_definitions "
            "WHERE experiment_id = ? AND experiment_version = ?",
            (exp_id, exp_ver),
        )
        def_row = await cursor.fetchone()
        if def_row is None:
            raise ExperimentDefinitionConflictError(
                f"experiment {exp_id} version {exp_ver} not found"
            )

        # Validate variant_id against the stored definition.
        definition = ExperimentDefinition.model_validate_json(def_row[0])
        variant = next(
            (v for v in definition.variants if v.variant_id == variant_id), None
        )
        if variant is None:
            raise VariantDiscardConflictError(
                f"variant {variant_id} not found in experiment "
                f"{exp_id} version {exp_ver}"
            )

        # Pre-run guard: no RunAttempt may exist for this variant.
        cursor = await conn.execute(
            "SELECT run_id FROM run_attempts "
            "WHERE experiment_id = ? AND experiment_version = ? AND variant_id = ?",
            (exp_id, exp_ver, variant_id),
        )
        if await cursor.fetchone() is not None:
            raise VariantDiscardConflictError(
                f"variant {variant_id} already has a run attempt; "
                "discard must be pre-run"
            )

        # Idempotency / conflict by identity + JSON.
        record_json = record.model_dump_json()
        cursor = await conn.execute(
            "SELECT record_json FROM variant_dispositions "
            "WHERE experiment_id = ? AND experiment_version = ? AND variant_id = ?",
            (exp_id, exp_ver, variant_id),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            if existing_row[0] == record_json:
                return  # idempotent, nothing to do
            raise VariantDiscardConflictError(
                f"variant disposition for {variant_id} already exists with different content; "
                "no overwrite allowed"
            )

        await conn.execute(
            "INSERT INTO variant_dispositions "
            "(experiment_id, experiment_version, variant_id, record_json) "
            "VALUES (?, ?, ?, ?)",
            (exp_id, exp_ver, variant_id, record_json),
        )

    @staticmethod
    async def _load_variant_discard_inner(
        conn: aiosqlite.Connection,
        experiment_ref: ExperimentRef,
        variant_id: str,
    ) -> VariantDiscardRecord | None:
        """Load a variant discard record by its identity."""
        cursor = await conn.execute(
            "SELECT record_json FROM variant_dispositions "
            "WHERE experiment_id = ? AND experiment_version = ? AND variant_id = ?",
            (experiment_ref.experiment_id, experiment_ref.experiment_version, variant_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        record = VariantDiscardRecord.model_validate_json(row[0])
        stored_ref = record.experiment_ref
        if (
            stored_ref.experiment_id != experiment_ref.experiment_id
            or stored_ref.experiment_version != experiment_ref.experiment_version
            or record.variant_id != variant_id
        ):
            raise VariantDiscardConflictError(
                "stored disposition payload does not match indexed identity"
            )
        return record

    # ── result exclusion helpers (v5) ──────────────────────────────────────

    @staticmethod
    async def _record_result_exclusion_inner(
        conn: aiosqlite.Connection,
        record: ResultExclusionRecord,
    ) -> None:
        """Atomically persist a ResultExclusionRecord with preconditions."""
        result_id = record.result_id

        # Verify the referenced ExperimentResult exists.
        cursor = await conn.execute(
            "SELECT result_id FROM experiment_results WHERE result_id = ?",
            (result_id,),
        )
        if await cursor.fetchone() is None:
            raise ExperimentResultStateError(
                f"experiment result {result_id} not found"
            )

        # Idempotency / conflict by identity + JSON.
        record_json = record.model_dump_json()
        cursor = await conn.execute(
            "SELECT record_json FROM result_exclusions WHERE result_id = ?",
            (result_id,),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            if existing_row[0] == record_json:
                return  # idempotent, nothing to do
            raise ResultExclusionConflictError(
                f"result exclusion for {result_id} already exists with different content; "
                "no overwrite allowed"
            )

        await conn.execute(
            "INSERT INTO result_exclusions (result_id, record_json) VALUES (?, ?)",
            (result_id, record_json),
        )

    @staticmethod
    async def _load_result_exclusion_inner(
        conn: aiosqlite.Connection,
        result_id: str,
    ) -> ResultExclusionRecord | None:
        """Load a result exclusion record by result_id."""
        cursor = await conn.execute(
            "SELECT record_json FROM result_exclusions WHERE result_id = ?",
            (result_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        record = ResultExclusionRecord.model_validate_json(row[0])
        if record.result_id != result_id:
            raise ResultExclusionConflictError(
                "stored exclusion payload does not match indexed identity"
            )
        return record

    # ── paper fill helpers (v6) ────────────────────────────────────────────

    @staticmethod
    async def _save_paper_fill_inner(
        conn: aiosqlite.Connection,
        fill: PaperFill,
    ) -> None:
        """Atomically persist a PaperFill with idempotency and conflict detection."""
        fill_id = fill.fill_id
        record_json = fill.model_dump_json()

        # Idempotency / conflict by identity + JSON.
        cursor = await conn.execute(
            "SELECT record_json FROM paper_fills WHERE fill_id = ?",
            (fill_id,),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            if existing_row[0] == record_json:
                return  # idempotent, nothing to do
            raise PaperFillConflictError(
                f"paper fill {fill_id} already exists with different content; "
                "no overwrite allowed"
            )

        await conn.execute(
            "INSERT INTO paper_fills (fill_id, record_json) VALUES (?, ?)",
            (fill_id, record_json),
        )

    @staticmethod
    async def _load_paper_fill_inner(
        conn: aiosqlite.Connection,
        fill_id: str,
    ) -> PaperFill | None:
        """Load and validate one paper fill by its fill_id."""
        cursor = await conn.execute(
            "SELECT fill_id, record_json FROM paper_fills WHERE fill_id = ?",
            (fill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        stored_fill_id, record_json = row

        # Validate the JSON can be parsed.
        try:
            fill = PaperFill.model_validate_json(record_json)
        except Exception as exc:
            raise PaperFillIntegrityError(
                f"paper fill for {fill_id} has corrupt JSON"
            ) from exc

        # Validate keys match stored columns (JSON-to-key identity).
        if fill.fill_id != stored_fill_id:
            raise PaperFillIntegrityError(
                "stored paper fill payload does not match indexed identity"
            )

        return fill

    @staticmethod
    async def _register_paper_order_inner(
        conn: aiosqlite.Connection,
        order: PaperOrder,
    ) -> PaperOrder:
        """Persist one immutable paper order by its order_id."""
        order_id = order.order_id
        record_json = order.model_dump_json()

        cursor = await conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id = ?",
            (order_id,),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            if existing_row[0] == record_json:
                return order  # idempotent, nothing to do
            raise PaperOrderContextConflictError(
                f"paper order {order_id} already exists with different content; "
                "no overwrite allowed"
            )

        await conn.execute(
            "INSERT INTO paper_orders (order_id, paper_account_id, paper_session_id, environment, client_order_id, order_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                order.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                order.client_order_id,
                record_json,
            ),
        )
        return order

    # ── paper account opening / balance helpers (v7) ────────────────────────

    @staticmethod
    async def _open_paper_account_inner(
        conn: aiosqlite.Connection,
        opening: PaperAccountOpening,
    ) -> PaperAccountOpening:
        """Persist an immutable opening and initial balances atomically."""
        account_id = opening.account_id
        opening_json = opening.model_dump_json()
        cursor = await conn.execute(
            "SELECT opening_json FROM paper_account_openings WHERE account_id = ?",
            (account_id,),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            existing = SQLiteStore._validate_opening_json(
                existing_row[0], account_id
            )
            if existing_row[0] == opening_json:
                return existing
            raise PaperAccountOpeningConflictError(
                f"paper account {account_id} already has a different opening; "
                "no reinitialization allowed"
            )

        await conn.execute(
            "INSERT INTO paper_account_openings (account_id, opening_json) "
            "VALUES (?, ?)",
            (account_id, opening_json),
        )
        for balance in opening.balances:
            await conn.execute(
                "INSERT INTO paper_account_balances "
                "(account_id, asset, balance_json) VALUES (?, ?, ?)",
                (account_id, balance.asset, balance.model_dump_json()),
            )
        return opening

    @staticmethod
    def _validate_opening_json(
        opening_json: str, account_id: str
    ) -> PaperAccountOpening:
        try:
            opening = PaperAccountOpening.model_validate_json(opening_json)
        except Exception as exc:
            raise PaperAccountOpeningIntegrityError(
                f"paper account opening for {account_id} has invalid JSON"
            ) from exc
        if opening.account_id != account_id:
            raise PaperAccountOpeningIntegrityError(
                "paper account opening JSON does not match indexed account_id"
            )
        return opening

    @staticmethod
    async def _load_paper_account_opening_inner(
        conn: aiosqlite.Connection,
        account_id: str,
    ) -> PaperAccountOpening | None:
        cursor = await conn.execute(
            "SELECT account_id, opening_json FROM paper_account_openings "
            "WHERE account_id = ?",
            (account_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SQLiteStore._validate_opening_json(row[1], row[0])

    @staticmethod
    async def _load_paper_balance_view_inner(
        conn: aiosqlite.Connection,
        account_id: str,
    ) -> PaperBalanceView | None:
        opening = await SQLiteStore._load_paper_account_opening_inner(
            conn, account_id
        )
        if opening is None:
            return None

        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? ORDER BY asset",
            (account_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            raise PaperAccountOpeningIntegrityError(
                f"paper account {account_id} has an opening but no current balances"
            )

        balances: list[AssetBalance] = []
        for indexed_asset, balance_json in rows:
            try:
                balance = AssetBalance.model_validate_json(balance_json)
            except Exception as exc:
                raise PaperAccountOpeningIntegrityError(
                    f"paper account {account_id} has invalid balance JSON"
                ) from exc
            if balance.asset != indexed_asset:
                raise PaperAccountOpeningIntegrityError(
                    "paper balance JSON does not match indexed asset"
                )
            balances.append(balance)

        return PaperBalanceView(account_id=account_id, balances=tuple(balances))

    # ── initial PAPER order context helpers (v8; no lifecycle) ──────────────

    @staticmethod
    async def _register_paper_order_inner(
        conn: aiosqlite.Connection,
        order: PaperOrder,
    ) -> PaperOrder:
        order_json = order.model_dump_json()
        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders WHERE order_id = ?",
            (order.order_id,),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            existing = SQLiteStore._validate_paper_order_row(existing_row)
            if existing_row[5] == order_json:
                return existing
            acceptance = await SQLiteStore._load_paper_order_acceptance_inner(
                conn, order.order_id
            )
            if acceptance is not None and acceptance["initial_order_json"] == order_json:
                return existing
            raise PaperOrderContextConflictError(
                f"order_id {order.order_id} already has a different initial payload"
            )

        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders "
            "WHERE paper_account_id = ? AND paper_session_id = ? "
            "AND environment = ? AND client_order_id = ?",
            (
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                order.client_order_id,
            ),
        )
        existing_row = await cursor.fetchone()
        if existing_row is not None:
            existing = SQLiteStore._validate_paper_order_row(existing_row)
            if existing_row[5] == order_json:
                return existing
            acceptance = await SQLiteStore._load_paper_order_acceptance_inner(
                conn, existing.order_id
            )
            if acceptance is not None and acceptance["initial_order_json"] == order_json:
                return existing
            raise PaperOrderContextConflictError(
                "client order idempotency key already has a different payload"
            )

        await conn.execute(
            "INSERT INTO paper_orders "
            "(order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                order.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                order.client_order_id,
                order_json,
            ),
        )
        return order

    @staticmethod
    def _validate_paper_order_row(row) -> PaperOrder:
        (
            indexed_order_id,
            indexed_account_id,
            indexed_session_id,
            indexed_environment,
            indexed_client_order_id,
            order_json,
        ) = row
        try:
            order = PaperOrder.model_validate_json(order_json)
        except Exception as exc:
            raise PaperOrderContextIntegrityError(
                f"paper order {indexed_order_id} has invalid JSON"
            ) from exc
        indexed = (
            indexed_order_id,
            indexed_account_id,
            indexed_session_id,
            indexed_environment,
            indexed_client_order_id,
        )
        payload = (
            order.order_id,
            order.paper_account_id,
            order.paper_session_id,
            order.environment,
            order.client_order_id,
        )
        if payload != indexed:
            raise PaperOrderContextIntegrityError(
                "paper order JSON does not match indexed identity/context"
            )
        return order

    @staticmethod
    async def _load_paper_order_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> PaperOrder | None:
        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders WHERE order_id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        return None if row is None else SQLiteStore._validate_paper_order_row(row)

    @staticmethod
    def _build_acceptance_payload_json(
        initial_order_json: str,
        reservation: PaperReservationRef,
    ) -> str:
        """Canonical immutable acceptance payload; deliberately excludes live balance."""
        return json.dumps(
            {
                "schema_version": "paper_order_acceptance.v1",
                "initial_order": json.loads(initial_order_json),
                "reservation": json.loads(reservation.model_dump_json()),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _validate_paper_order_acceptance_row(row) -> dict[str, Any]:
        (
            indexed_order_id,
            indexed_reservation_id,
            indexed_account_id,
            indexed_session_id,
            indexed_environment,
            indexed_client_order_id,
            initial_order_json,
            acceptance_payload_json,
            reservation_json,
        ) = row
        try:
            initial_order = PaperOrder.model_validate_json(initial_order_json)
            reservation = PaperReservationRef.model_validate_json(reservation_json)
            acceptance_payload = json.loads(acceptance_payload_json)
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"acceptance record for order {indexed_order_id} is invalid"
            ) from exc

        if initial_order.model_dump_json() != initial_order_json:
            raise PaperOrderAcceptanceIntegrityError(
                "initial order snapshot is not canonical"
            )
        if initial_order.status != PaperOrderStatus.PREPARED or initial_order.fills:
            raise PaperOrderAcceptanceIntegrityError(
                "acceptance snapshot must retain the initial PREPARED order without fills"
            )
        if (
            initial_order.order_id != indexed_order_id
            or reservation.reservation_id != indexed_reservation_id
            or reservation.owner_order_id != indexed_order_id
            or initial_order.paper_account_id != indexed_account_id
            or initial_order.paper_session_id != indexed_session_id
            or initial_order.environment != indexed_environment
            or initial_order.client_order_id != indexed_client_order_id
        ):
            raise PaperOrderAcceptanceIntegrityError(
                "acceptance JSON does not match indexed order/reservation identity"
            )

        expected_payload = SQLiteStore._build_acceptance_payload_json(
            initial_order_json, reservation
        )
        if acceptance_payload_json != expected_payload or acceptance_payload.get(
            "schema_version"
        ) != "paper_order_acceptance.v1":
            raise PaperOrderAcceptanceIntegrityError(
                "acceptance payload is not canonical or differs from its immutable snapshot"
            )
        return {
            "initial_order_json": initial_order_json,
            "initial_order": initial_order,
            "reservation": reservation,
            "acceptance_payload_json": acceptance_payload_json,
        }

    @staticmethod
    async def _load_paper_order_acceptance_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            "SELECT order_id, reservation_id, paper_account_id, paper_session_id, "
            "environment, client_order_id, initial_order_json, "
            "acceptance_payload_json, reservation_json "
            "FROM paper_order_acceptances WHERE order_id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        return (
            None
            if row is None
            else SQLiteStore._validate_paper_order_acceptance_row(row)
        )

    @staticmethod
    def _compute_order_reservation(order: PaperOrder) -> ReservationAmount:
        """Compute BUY quote or SELL base reserve without estimated fees."""
        if order.protection_group_id is not None:
            raise PaperOrderAcceptanceScopeError(
                "OCO/protection-group orders are outside atomic acceptance"
            )
        symbol_parts = order.symbol.split("/")
        if len(symbol_parts) != 2 or any(
            not part or part.strip() != part or any(char.isspace() for char in part)
            for part in symbol_parts
        ):
            raise PaperOrderAcceptanceStateError(
                "BUY order symbol must use canonical BASE/QUOTE form"
            )
        base_asset, quote_asset = symbol_parts
        if order.order_type not in (PaperOrderType.LIMIT, PaperOrderType.MARKET):
            raise PaperOrderAcceptanceScopeError(
                "atomic acceptance supports LIMIT and MARKET orders only"
            )
        if order.order_type == PaperOrderType.LIMIT and order.price is None:
            raise PaperOrderAcceptanceStateError(
                f"LIMIT {order.side} has no explicit price"
            )

        if order.side == PaperOrderSide.BUY:
            if order.order_type == PaperOrderType.LIMIT:
                amount = _exact_product(order.requested_qty, order.price)
            else:
                if order.max_quote_amount is None:
                    raise PaperOrderAcceptanceStateError(
                        "MARKET BUY requires an explicit max_quote_amount"
                    )
                amount = order.max_quote_amount
            return ReservationAmount(asset=quote_asset, amount=amount)

        if order.side == PaperOrderSide.SELL:
            if not order.lot_allocations:
                raise PaperOrderAcceptanceStateError(
                    "SELL order requires explicit lot_allocations"
                )
            allocated_qty = _exact_sum(
                tuple(allocation.qty for allocation in order.lot_allocations)
            )
            if allocated_qty != order.requested_qty:
                raise PaperOrderAcceptanceStateError(
                    "SELL lot_allocations must sum to requested_qty"
                )
            return ReservationAmount(asset=base_asset, amount=allocated_qty)

        raise PaperOrderAcceptanceScopeError(
            f"unsupported order side for atomic acceptance: {order.side}"
        )

    @staticmethod
    async def _load_paper_reservation_inner(
        conn: aiosqlite.Connection,
        reservation_id: str,
    ) -> PaperReservationRef | None:
        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json "
            "FROM paper_reservations WHERE reservation_id = ?",
            (reservation_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        (
            indexed_reservation_id,
            indexed_order_id,
            indexed_account_id,
            indexed_session_id,
            indexed_environment,
            indexed_asset,
            amount_json,
            reservation_json,
        ) = row
        try:
            amount = ReservationAmount.model_validate_json(amount_json)
            reservation = PaperReservationRef.model_validate_json(reservation_json)
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"reservation {indexed_reservation_id} has invalid stored JSON"
            ) from exc

        acceptance = await SQLiteStore._load_paper_order_acceptance_inner(
            conn, indexed_order_id
        )
        if (
            acceptance is None
            or indexed_reservation_id != indexed_order_id
            or acceptance["reservation"].reservation_id != indexed_reservation_id
            or acceptance["initial_order"].paper_account_id != indexed_account_id
            or acceptance["initial_order"].paper_session_id != indexed_session_id
            or acceptance["initial_order"].environment != indexed_environment
            or reservation.reservation_id != indexed_reservation_id
            or reservation.owner_order_id != indexed_order_id
            or reservation.amount.asset != indexed_asset
            or reservation.amount != amount
        ):
            raise PaperOrderAcceptanceIntegrityError(
                "reservation payload does not match indexed acceptance identity"
            )
        return reservation

    @staticmethod
    async def _validate_sell_allocations_for_acceptance_inner(
        conn: aiosqlite.Connection,
        order: PaperOrder,
    ) -> tuple[_PaperLotReservationAllocationV1, ...]:
        """Validate that an accepted SELL reserves only owned, unreserved lots."""
        if order.side != PaperOrderSide.SELL:
            return ()
        if not order.lot_allocations:
            raise PaperOrderAcceptanceStateError(
                "SELL order requires explicit lot allocations"
            )

        symbol_parts = order.symbol.split("/")
        if len(symbol_parts) != 2:
            raise PaperOrderAcceptanceStateError(
                "SELL symbol must use canonical BASE/QUOTE form"
            )
        base_asset, _quote_asset = symbol_parts
        seen_positions: set[str] = set()
        reservations: list[_PaperLotReservationAllocationV1] = []

        for allocation in order.lot_allocations:
            position_id = allocation.position_id
            if position_id in seen_positions:
                raise PaperOrderAcceptanceStateError(
                    f"SELL order repeats lot allocation {position_id}"
                )
            seen_positions.add(position_id)
            if (
                allocation.account_id != order.paper_account_id
                or allocation.symbol != order.symbol
                or allocation.strategy_ref != order.strategy_ref
            ):
                raise PaperOrderAcceptanceStateError(
                    f"SELL allocation {position_id} has a different account, symbol, "
                    "or strategy context"
                )

            cursor = await conn.execute(
                "SELECT account_id, paper_session_id, environment, lot_json "
                "FROM position_lots WHERE order_id = ?",
                (position_id,),
            )
            lot_row = await cursor.fetchone()
            if lot_row is None:
                raise PaperOrderAcceptanceFundsError(
                    f"SELL allocation references unavailable lot {position_id}"
                )
            try:
                lot = PositionLot.model_validate_json(lot_row[3])
            except Exception as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"position lot {position_id} has invalid JSON"
                ) from exc
            if (
                lot.order_id != position_id
                or lot.account_id != lot_row[0]
                or lot.paper_session_id != lot_row[1]
                or lot.environment != lot_row[2]
                or lot.account_id != order.paper_account_id
                or lot.paper_session_id != order.paper_session_id
                or lot.environment != order.environment
                or lot.total_qty <= Decimal("0")
                or lot.total_cost <= Decimal("0")
            ):
                raise PaperOrderAcceptanceIntegrityError(
                    f"position lot {position_id} has inconsistent owner or inventory"
                )

            cursor = await conn.execute(
                "SELECT order_json FROM paper_orders WHERE order_id = ?",
                (position_id,),
            )
            entry_order_row = await cursor.fetchone()
            if entry_order_row is None:
                raise PaperOrderAcceptanceIntegrityError(
                    f"position lot {position_id} has no entry order"
                )
            try:
                entry_order = PaperOrder.model_validate_json(entry_order_row[0])
            except Exception as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"entry order for position lot {position_id} has invalid JSON"
                ) from exc
            if (
                entry_order.side != PaperOrderSide.BUY
                or entry_order.paper_account_id != order.paper_account_id
                or entry_order.paper_session_id != order.paper_session_id
                or entry_order.environment != order.environment
                or entry_order.symbol != order.symbol
                or entry_order.strategy_ref != allocation.strategy_ref
                or entry_order.strategy_ref != order.strategy_ref
            ):
                raise PaperOrderAcceptanceStateError(
                    f"SELL allocation {position_id} is outside the order's account, "
                    "symbol, or strategy context"
                )

            cursor = await conn.execute(
                "SELECT reservation_id, owner_order_id, position_id, account_id, "
                "paper_session_id, environment, symbol, base_asset, strategy_ref_json, "
                "initial_qty, remaining_qty, allocation_json "
                "FROM paper_reservation_lot_allocations WHERE position_id = ?",
                (position_id,),
            )
            existing_rows = await cursor.fetchall()
            already_reserved: list[Decimal] = []
            for row in existing_rows:
                try:
                    existing = _PaperLotReservationAllocationV1.model_validate_json(
                        row[11]
                    )
                except Exception as exc:
                    raise PaperOrderAcceptanceIntegrityError(
                        f"existing lot reservation for {position_id} has invalid JSON"
                    ) from exc
                indexed = (
                    existing.reservation_id,
                    existing.owner_order_id,
                    existing.position_id,
                    existing.account_id,
                    existing.paper_session_id,
                    existing.environment,
                    existing.symbol,
                    existing.base_asset,
                    existing.strategy_ref.model_dump_json(),
                    str(existing.initial_qty),
                    str(existing.remaining_qty),
                )
                if indexed != tuple(str(value) for value in row[:11]):
                    raise PaperOrderAcceptanceIntegrityError(
                        f"lot reservation for {position_id} disagrees with indexed columns"
                    )
                if (
                    existing.account_id != order.paper_account_id
                    or existing.symbol != order.symbol
                    or existing.base_asset != base_asset
                    or existing.strategy_ref != allocation.strategy_ref
                ):
                    raise PaperOrderAcceptanceIntegrityError(
                        f"lot reservation for {position_id} has conflicting context"
                    )
                already_reserved.append(existing.remaining_qty)

            total_reserved = _exact_sum(tuple(already_reserved))
            unreserved_qty = _exact_sum((lot.total_qty, total_reserved.copy_negate()))
            if allocation.qty > unreserved_qty:
                raise PaperOrderAcceptanceFundsError(
                    f"SELL allocation {allocation.qty} exceeds unreserved lot quantity "
                    f"{unreserved_qty} for {position_id}"
                )

            reservations.append(
                _PaperLotReservationAllocationV1(
                    reservation_id=order.order_id,
                    owner_order_id=order.order_id,
                    position_id=position_id,
                    account_id=order.paper_account_id,
                    paper_session_id=order.paper_session_id,
                    environment=order.environment,
                    symbol=order.symbol,
                    base_asset=base_asset,
                    strategy_ref=allocation.strategy_ref,
                    initial_qty=allocation.qty,
                    remaining_qty=allocation.qty,
                )
            )

        allocated_qty = _exact_sum(
            tuple(allocation.qty for allocation in order.lot_allocations)
        )
        if allocated_qty != order.requested_qty:
            raise PaperOrderAcceptanceStateError(
                "SELL lot allocation quantity must equal requested_qty"
            )
        return tuple(reservations)

    @staticmethod
    async def _accept_paper_order_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> PaperOrder:
        """Reserve the order's asset and persist ACCEPTED state atomically."""
        existing_acceptance = await SQLiteStore._load_paper_order_acceptance_inner(
            conn, order_id
        )
        if existing_acceptance is not None:
            # Recognition uses only the immutable acceptance record. In
            # particular, do not re-read available funds or recompute a reserve.
            current_order = await SQLiteStore._load_paper_order_inner(conn, order_id)
            if current_order is None:
                raise PaperOrderAcceptanceIntegrityError(
                    f"accepted order {order_id} has no current order record"
                )
            return current_order

        order = await SQLiteStore._load_paper_order_inner(conn, order_id)
        if order is None:
            raise PaperOrderAcceptanceStateError(
                f"paper order {order_id} is not registered"
            )
        if order.status != PaperOrderStatus.PREPARED or order.fills:
            raise PaperOrderAcceptanceStateError(
                "only a PREPARED order without fills can be accepted"
            )

        reserve_amount = SQLiteStore._compute_order_reservation(order)
        sell_lot_reservations: tuple[_PaperLotReservationAllocationV1, ...] = ()
        if order.side == PaperOrderSide.SELL:
            sell_lot_reservations = (
                await SQLiteStore._validate_sell_allocations_for_acceptance_inner(
                    conn, order
                )
            )
        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? AND asset = ?",
            (order.paper_account_id, reserve_amount.asset),
        )
        balance_row = await cursor.fetchone()
        if balance_row is None:
            raise PaperOrderAcceptanceFundsError(
                f"account {order.paper_account_id} has no opened balance for "
                f"{reserve_amount.asset}"
            )
        try:
            balance = AssetBalance.model_validate_json(balance_row[1])
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"account {order.paper_account_id} has invalid balance JSON"
            ) from exc
        if balance.asset != balance_row[0] or balance.asset != reserve_amount.asset:
            raise PaperOrderAcceptanceIntegrityError(
                "account balance JSON does not match indexed asset"
            )
        if reserve_amount.amount > balance.available:
            raise PaperOrderAcceptanceFundsError(
                f"insufficient available {balance.asset}: "
                f"need {reserve_amount.amount}, have {balance.available}"
            )

        # Build the pure contract from the balance read inside this transaction;
        # never accept a caller-provided available_before value.
        request = PaperReservationRequest(
            reservation_id=order.order_id,
            account_id=order.paper_account_id,
            paper_session_id=order.paper_session_id,
            environment=order.environment,
            owner_order_id=order.order_id,
            amount=reserve_amount,
            available_before=ReservationAmount(
                asset=balance.asset, amount=balance.available
            ),
        )
        reservation = PaperReservationRef(
            reservation_id=request.reservation_id,
            owner_order_id=request.owner_order_id,
            amount=request.amount,
        )
        initial_order_json = order.model_dump_json()
        reservation_json = reservation.model_dump_json()
        acceptance_payload_json = SQLiteStore._build_acceptance_payload_json(
            initial_order_json, reservation
        )

        await conn.execute(
            "INSERT INTO paper_order_acceptances "
            "(order_id, reservation_id, paper_account_id, paper_session_id, "
            "environment, client_order_id, initial_order_json, "
            "acceptance_payload_json, reservation_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                order.order_id,
                reservation.reservation_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                order.client_order_id,
                initial_order_json,
                acceptance_payload_json,
                reservation_json,
            ),
        )
        await conn.execute(
            "INSERT INTO paper_reservations "
            "(reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                reservation.reservation_id,
                order.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                reserve_amount.asset,
                reserve_amount.model_dump_json(),
                reservation_json,
            ),
        )
        for lot_reservation in sell_lot_reservations:
            await conn.execute(
                "INSERT INTO paper_reservation_lot_allocations "
                "(reservation_id, owner_order_id, position_id, account_id, "
                "paper_session_id, environment, symbol, base_asset, strategy_ref_json, "
                "initial_qty, remaining_qty, allocation_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    lot_reservation.reservation_id,
                    lot_reservation.owner_order_id,
                    lot_reservation.position_id,
                    lot_reservation.account_id,
                    lot_reservation.paper_session_id,
                    lot_reservation.environment,
                    lot_reservation.symbol,
                    lot_reservation.base_asset,
                    lot_reservation.strategy_ref.model_dump_json(),
                    str(lot_reservation.initial_qty),
                    str(lot_reservation.remaining_qty),
                    lot_reservation.model_dump_json(),
                ),
            )

        updated_balance = AssetBalance(
            asset=balance.asset,
            total=balance.total,
            available=balance.available - reserve_amount.amount,
            reserved=balance.reserved + reserve_amount.amount,
        )
        await conn.execute(
            "UPDATE paper_account_balances SET balance_json = ? "
            "WHERE account_id = ? AND asset = ?",
            (
                updated_balance.model_dump_json(),
                order.paper_account_id,
                reserve_amount.asset,
            ),
        )

        updated_values = order.model_dump(mode="python")
        updated_values["status"] = PaperOrderStatus.ACCEPTED
        accepted_order = _PAPER_ORDER_ADAPTER.validate_python(updated_values)
        cursor = await conn.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ? AND order_json = ?",
            (accepted_order.model_dump_json(), order.order_id, initial_order_json),
        )
        if cursor.rowcount != 1:
            raise PaperOrderAcceptanceConflictError(
                f"paper order {order.order_id} changed during acceptance"
            )
        return accepted_order

    # ── fill application (v10) ─────────────────────────────────────────────

    @staticmethod
    async def _apply_paper_fill_inner(
        conn: aiosqlite.Connection,
        fill_id: str,
    ) -> PaperFill:
        """Apply one already-journalized paper fill atomically.

        Updates in a single transaction:
          - durable application mark (paper_fill_applications)
          - QUOTE balance (principal + commission debit, reserve release)
          - BASE balance (qty credit)
          - position_lots aggregation
          - order status to FILLED / PARTIALLY_FILLED

        Idempotent: repeated calls with the same fill_id are a no-op after first apply.
        Rejects: fill not found, already applied, order not ACCEPTED, insufficient funds/fee.
        """
        # 1. Load fill and validate it exists.
        cursor = await conn.execute(
            "SELECT fill_id, record_json FROM paper_fills WHERE fill_id = ?",
            (fill_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PaperFillIntegrityError(f"paper fill {fill_id} not found")

        stored_fill_id, record_json = row
        try:
            fill = PaperFill.model_validate_json(record_json)
        except Exception as exc:
            raise PaperFillIntegrityError(
                f"paper fill for {fill_id} has corrupt JSON"
            ) from exc
        if fill.fill_id != stored_fill_id:
            raise PaperFillIntegrityError(
                "stored paper fill payload does not match indexed identity"
            )

        # 2. Check idempotency: already applied?
        cursor = await conn.execute(
            "SELECT fill_id FROM paper_fill_applications WHERE fill_id = ?",
            (fill_id,),
        )
        if await cursor.fetchone() is not None:
            return fill  # idempotent no-op

        # 3. Load order and validate ACCEPTED state.
        cursor = await conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id = ?",
            (fill.order_id,),
        )
        order_row = await cursor.fetchone()
        if order_row is None:
            raise PaperOrderAcceptanceStateError(
                f"paper order {fill.order_id} not found"
            )
        try:
            order = PaperOrder.model_validate_json(order_row[0])
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"order {fill.order_id} has corrupt JSON"
            ) from exc

        if order.status not in (
            PaperOrderStatus.ACCEPTED,
            PaperOrderStatus.PARTIALLY_FILLED,
        ):
            raise PaperOrderAcceptanceStateError(
                f"order {fill.order_id} is not open for fills (status={order.status})"
            )
        if order.side == PaperOrderSide.SELL:
            return await SQLiteStore._apply_paper_sell_fill_inner(conn, fill, order)
        if order.position_id is not None and order.position_id != order.order_id:
            raise PaperOrderAcceptanceStateError(
                "an order with a different position_id is not supported in this slice"
            )
        if (
            order.order_type == PaperOrderType.LIMIT
            and (order.price is None or fill.price > order.price)
        ):
            raise PaperOrderAcceptanceStateError(
                "LIMIT BUY fill price must not exceed the order limit"
            )

        # 4. Load immutable original order/reservation terms from acceptance.
        cursor = await conn.execute(
            "SELECT initial_order_json, reservation_json "
            "FROM paper_order_acceptances WHERE order_id = ?",
            (fill.order_id,),
        )
        acc_row = await cursor.fetchone()
        if acc_row is None:
            raise PaperOrderAcceptanceStateError(
                f"order {fill.order_id} has no acceptance record"
            )
        initial_order = PaperOrder.model_validate_json(acc_row[0])
        initial_reservation = PaperReservationRef.model_validate_json(acc_row[1])

        immutable_order_fields = (
            "order_id", "client_order_id", "paper_account_id", "paper_session_id",
            "environment", "symbol", "side", "order_type", "requested_qty", "price",
            "trigger_price", "max_quote_amount", "strategy_ref", "decision_id",
            "timestamp_ms", "position_id", "protection_group_id", "lot_allocations",
        )
        if any(
            getattr(order, field) != getattr(initial_order, field)
            for field in immutable_order_fields
        ):
            raise PaperOrderAcceptanceIntegrityError(
                f"order {fill.order_id} immutable terms differ from acceptance snapshot"
            )

        if initial_reservation.owner_order_id != order.order_id:
            raise PaperOrderAcceptanceIntegrityError(
                "acceptance reservation snapshot does not match its order"
            )

        # The mutable reservation row is the authority for the current remainder.
        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json "
            "FROM paper_reservations WHERE owner_order_id = ?",
            (order.order_id,),
        )
        active_reservation_row = await cursor.fetchone()
        if active_reservation_row is None:
            raise PaperOrderAcceptanceStateError(
                f"order {order.order_id} has no active reservation"
            )
        active_reservation = PaperReservationRef.model_validate_json(
            active_reservation_row[7]
        )
        if (
            active_reservation.reservation_id != active_reservation_row[0]
            or active_reservation.owner_order_id != active_reservation_row[1]
            or active_reservation_row[2] != order.paper_account_id
            or active_reservation_row[3] != order.paper_session_id
            or active_reservation_row[4] != order.environment
            or active_reservation.amount.asset != active_reservation_row[5]
            or active_reservation.amount.asset != initial_reservation.amount.asset
            or active_reservation.amount.model_dump_json() != active_reservation_row[6]
        ):
            raise PaperOrderAcceptanceIntegrityError(
                f"active reservation for order {order.order_id} disagrees with indexed columns"
            )

        # 5. Load QUOTE balance (reserve asset).
        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? AND asset = ?",
            (order.paper_account_id, active_reservation.amount.asset),
        )
        quote_row = await cursor.fetchone()
        if quote_row is None:
            raise PaperOrderAcceptanceFundsError(
                f"no balance for {active_reservation.amount.asset} on account {order.paper_account_id}"
            )
        try:
            quote_balance = _asset_balance_json_to_model(quote_row[1])
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"account {order.paper_account_id} has invalid QUOTE balance JSON"
            ) from exc

        # 6. Compute principal and commission.
        principal = _exact_product(fill.qty, fill.price)
        if fill.commission is None:
            raise PaperOrderAcceptanceStateError(
                "a known QUOTE commission is required to apply a fill"
            )
        if fill.commission.asset != initial_reservation.amount.asset:
            raise PaperOrderAcceptanceStateError(
                f"fill commission asset {fill.commission.asset} must match "
                f"reserved QUOTE asset {initial_reservation.amount.asset}"
            )
        commission_amount = fill.commission.amount

        applied_fills = (*order.fills, fill)
        total_filled_qty = _exact_sum(tuple(applied.qty for applied in applied_fills))
        if total_filled_qty > initial_order.requested_qty:
            raise PaperOrderAcceptanceStateError(
                f"applied fills exceed requested quantity for order {order.order_id}"
            )
        remaining_qty = _exact_sum((initial_order.requested_qty, total_filled_qty.copy_negate()))
        total_filled_notional = _exact_sum(
            tuple(_exact_product(applied.qty, applied.price) for applied in applied_fills)
        )
        if initial_order.order_type == PaperOrderType.MARKET:
            if initial_order.max_quote_amount is None:
                raise PaperOrderAcceptanceIntegrityError(
                    f"MARKET BUY order {order.order_id} has no original quote cap"
                )
            if total_filled_notional > initial_order.max_quote_amount:
                raise PaperOrderAcceptanceFundsError(
                    f"cumulative MARKET notional {total_filled_notional} exceeds "
                    f"max_quote_amount {initial_order.max_quote_amount}"
                )

        if remaining_qty == Decimal("0"):
            new_status = PaperOrderStatus.FILLED
            target_reservation_amount = Decimal("0")
        else:
            new_status = PaperOrderStatus.PARTIALLY_FILLED
            if initial_order.order_type == PaperOrderType.LIMIT:
                if initial_order.price is None:
                    raise PaperOrderAcceptanceIntegrityError(
                        f"LIMIT BUY order {order.order_id} has no original limit price"
                    )
                target_reservation_amount = _exact_product(remaining_qty, initial_order.price)
            else:
                assert initial_order.max_quote_amount is not None
                target_reservation_amount = _exact_sum(
                    (initial_order.max_quote_amount, total_filled_notional.copy_negate())
                )
                if target_reservation_amount < Decimal("0"):
                    raise PaperOrderAcceptanceFundsError(
                        f"cumulative MARKET notional {total_filled_notional} exceeds "
                        f"max_quote_amount {initial_order.max_quote_amount}"
                    )

        if active_reservation.amount.amount < _exact_sum((principal, target_reservation_amount)):
            raise PaperOrderAcceptanceFundsError(
                "active reservation cannot cover the fill and required remaining coverage"
            )

        # Principal is consumed from the reservation; commission is debited only
        # from unreserved available funds.
        if commission_amount > quote_balance.available:
            raise PaperOrderAcceptanceFundsError(
                f"insufficient available {quote_balance.asset}: "
                f"commission needs {commission_amount}, have {quote_balance.available}"
            )
        needed = _exact_sum((principal, commission_amount))

        # 7. Load BASE balance (fill asset) from original accepted symbol terms.
        # Symbol is "BASE/QUOTE"; base asset is the first part.
        base_asset = initial_order.symbol.split("/")[0]

        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? AND asset = ?",
            (order.paper_account_id, base_asset),
        )
        base_row = await cursor.fetchone()
        if base_row is None:
            # Create BASE balance from zero.
            base_balance = _asset_balance_construct(
                asset=base_asset,
                total=_exact_product(fill.qty, Decimal("1")),
                available=_exact_product(fill.qty, Decimal("1")),
                reserved=Decimal("0"),
            )
        else:
            try:
                base_balance = _asset_balance_json_to_model(base_row[1])
            except Exception:
                raise PaperOrderAcceptanceIntegrityError(
                    f"account {order.paper_account_id} has invalid BASE balance JSON"
                )
            base_balance = _asset_balance_construct(
                asset=base_asset,
                total=_exact_sum((base_balance.total, fill.qty)),
                available=_exact_sum((base_balance.available, fill.qty)),
                reserved=base_balance.reserved,
            )

        # 8. Consume this fill's principal and retain only the required remainder.
        # Derive precision from all operands to avoid truncation under restricted global context.
        _bal_vals = [quote_balance.total, quote_balance.available, quote_balance.reserved, needed, active_reservation.amount.amount, target_reservation_amount]
        _min_exp = min(v.as_tuple().exponent for v in _bal_vals)
        _max_adj = max(v.adjusted() for v in _bal_vals)
        _fill_prec = max(1, _max_adj - _min_exp + 1)

        with localcontext(Context(prec=_fill_prec, rounding=ROUND_HALF_EVEN,
                                  Emin=-999999999, Emax=999999999, clamp=0,
                                  traps=[Clamped, DivisionByZero, Inexact,
                                         InvalidOperation, Overflow, Underflow])):
            new_total = _exact_sum((quote_balance.total, -needed))
            other_reserved = _exact_sum((quote_balance.reserved, -active_reservation.amount.amount))
            new_reserved = _exact_sum((other_reserved, target_reservation_amount))
            updated_quote = _asset_balance_construct(
                asset=quote_balance.asset,
                total=new_total,
                available=_exact_sum((new_total, -new_reserved)),
                reserved=new_reserved,
            )

        # 9. Update BASE balance.
        updated_base = _asset_balance_construct(
            asset=base_balance.asset,
            total=base_balance.total,
            available=base_balance.available,
            reserved=base_balance.reserved,
        )

        # 10. Upsert paper_fill_applications (durable mark).
        await conn.execute(
            "INSERT INTO paper_fill_applications "
            "(fill_id, order_id, account_id, paper_session_id, environment, applied_at_ms, application_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fill.fill_id,
                fill.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                fill.timestamp_ms,
                fill.model_dump_json(),
            ),
        )

        # 11. Update QUOTE balance row.
        await conn.execute(
            "UPDATE paper_account_balances SET balance_json = ? "
            "WHERE account_id = ? AND asset = ?",
            (updated_quote.model_dump_json(), order.paper_account_id, quote_balance.asset),
        )
        if remaining_qty == Decimal("0"):
            await conn.execute(
                "DELETE FROM paper_reservations WHERE owner_order_id = ?",
                (fill.order_id,),
            )
        else:
            updated_reservation = PaperReservationRef(
                reservation_id=active_reservation.reservation_id,
                owner_order_id=active_reservation.owner_order_id,
                owner_group_id=active_reservation.owner_group_id,
                amount=ReservationAmount(
                    asset=active_reservation.amount.asset,
                    amount=target_reservation_amount,
                ),
            )
            await conn.execute(
                "UPDATE paper_reservations SET amount_json = ?, reservation_json = ? "
                "WHERE owner_order_id = ?",
                (
                    updated_reservation.amount.model_dump_json(),
                    updated_reservation.model_dump_json(),
                    fill.order_id,
                ),
            )

        # 12. Upsert BASE balance row.
        if base_row is None:
            await conn.execute(
                "INSERT INTO paper_account_balances (account_id, asset, balance_json) VALUES (?, ?, ?)",
                (order.paper_account_id, base_balance.asset, updated_base.model_dump_json()),
            )
        else:
            await conn.execute(
                "UPDATE paper_account_balances SET balance_json = ? "
                "WHERE account_id = ? AND asset = ?",
                (updated_base.model_dump_json(), order.paper_account_id, base_balance.asset),
            )

        # 13. Update position_lots aggregation.
        cursor = await conn.execute(
            "SELECT lot_json FROM position_lots WHERE order_id = ?",
            (fill.order_id,),
        )
        lot_row = await cursor.fetchone()
        if lot_row is None:
            new_lot_qty = fill.qty
            new_lot_cost = principal
        else:
            existing_lot = PositionLot.model_validate_json(lot_row[0])
            new_lot_qty = _exact_sum((existing_lot.total_qty, fill.qty))
            new_lot_cost = _exact_sum((existing_lot.total_cost, principal))

        new_lot = PositionLot(
            order_id=fill.order_id,
            account_id=order.paper_account_id,
            paper_session_id=order.paper_session_id,
            environment=order.environment,
            total_qty=new_lot_qty,
            total_cost=new_lot_cost,
        )
        await conn.execute(
            "INSERT OR REPLACE INTO position_lots (order_id, account_id, paper_session_id, environment, lot_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                fill.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                new_lot.model_dump_json(),
            ),
        )

        # 14. Persist the full applied fill history and the resulting open/closed state.
        updated_order_values = order.model_dump(mode="python")
        updated_order_values["status"] = new_status
        updated_order_values["fills"] = [*order.fills, fill]
        validated_order = _PAPER_ORDER_ADAPTER.validate_python(updated_order_values)

        await conn.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
            (validated_order.model_dump_json(), fill.order_id),
        )

        return fill

    @staticmethod
    async def _apply_paper_sell_fill_inner(
        conn: aiosqlite.Connection,
        fill: PaperFill,
        order: PaperOrder,
    ) -> PaperFill:
        """Apply a SELL fill against its accepted lot reservations atomically."""
        if not fill.lot_allocations:
            raise PaperOrderAcceptanceStateError(
                "SELL fill requires explicit lot allocations"
            )
        if (
            fill.commission is None
            or fill.commission.asset != order.symbol.split("/")[-1]
        ):
            raise PaperOrderAcceptanceStateError(
                "a known QUOTE commission is required to apply a SELL fill"
            )
        if (
            order.order_type == PaperOrderType.LIMIT
            and (order.price is None or fill.price < order.price)
        ):
            raise PaperOrderAcceptanceStateError(
                "LIMIT SELL fill price must not be below the order limit"
            )

        cursor = await conn.execute(
            "SELECT initial_order_json FROM paper_order_acceptances WHERE order_id=?",
            (order.order_id,),
        )
        accepted_row = await cursor.fetchone()
        if accepted_row is None:
            raise PaperOrderAcceptanceStateError(
                f"order {order.order_id} has no acceptance record"
            )
        initial_order = PaperOrder.model_validate_json(accepted_row[0])
        immutable_fields = (
            "order_id", "client_order_id", "paper_account_id", "paper_session_id",
            "environment", "symbol", "side", "order_type", "requested_qty", "price",
            "trigger_price", "strategy_ref", "decision_id", "timestamp_ms",
            "position_id", "protection_group_id", "lot_allocations",
        )
        if any(getattr(order, name) != getattr(initial_order, name) for name in immutable_fields):
            raise PaperOrderAcceptanceIntegrityError(
                f"order {order.order_id} immutable terms differ from acceptance snapshot"
            )

        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json "
            "FROM paper_reservations WHERE owner_order_id=?",
            (order.order_id,),
        )
        reservation_row = await cursor.fetchone()
        if reservation_row is None:
            raise PaperOrderAcceptanceStateError(
                f"SELL order {order.order_id} has no active reservation"
            )
        reservation = PaperReservationRef.model_validate_json(reservation_row[7])
        if (
            reservation.reservation_id != reservation_row[0]
            or reservation.owner_order_id != reservation_row[1]
            or reservation_row[2] != order.paper_account_id
            or reservation_row[3] != order.paper_session_id
            or reservation_row[4] != order.environment
            or reservation.amount.asset != reservation_row[5]
            or reservation.amount.asset != order.symbol.split("/")[0]
            or reservation.amount.model_dump_json() != reservation_row[6]
        ):
            raise PaperOrderAcceptanceIntegrityError(
                f"active SELL reservation for {order.order_id} has inconsistent identity"
            )

        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, position_id, account_id, "
            "paper_session_id, environment, symbol, base_asset, strategy_ref_json, "
            "initial_qty, remaining_qty, allocation_json "
            "FROM paper_reservation_lot_allocations WHERE owner_order_id=? ORDER BY position_id",
            (order.order_id,),
        )
        lot_rows = await cursor.fetchall()
        active_lots: dict[str, _PaperLotReservationAllocationV1] = {}
        for lot_row in lot_rows:
            lot_reservation = _PaperLotReservationAllocationV1.model_validate_json(lot_row[11])

            # Normalize TEXT columns to Decimal before comparing with payload.
            try:
                idx_initial_qty = Decimal(lot_row[9])
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[0]} has invalid initial_qty indexed column: {lot_row[9]!r}"
                ) from exc
            try:
                idx_remaining_qty = Decimal(lot_row[10])
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[0]} has invalid remaining_qty indexed column: {lot_row[10]!r}"
                ) from exc

            if (
                lot_reservation.reservation_id != lot_row[0]
                or lot_reservation.owner_order_id != lot_row[1]
                or lot_row[2] != lot_reservation.position_id
                or lot_reservation.account_id != lot_row[3]
                or lot_reservation.paper_session_id != lot_row[4]
                or lot_reservation.environment != lot_row[5]
                or lot_reservation.symbol != lot_row[6]
                or lot_reservation.base_asset != lot_row[7]
                or lot_reservation.strategy_ref.model_dump_json() != lot_row[8]
                or idx_initial_qty != lot_reservation.initial_qty
                or idx_remaining_qty != lot_reservation.remaining_qty
            ):
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[2]} has inconsistent SELL context"
                )
            active_lots[lot_row[2]] = lot_reservation

        fill_allocations: dict[str, Decimal] = {}
        for allocation in fill.lot_allocations:
            if allocation.position_id in fill_allocations:
                raise PaperOrderAcceptanceStateError(
                    f"SELL fill repeats lot allocation {allocation.position_id}"
                )
            lot_reservation = active_lots.get(allocation.position_id)
            if lot_reservation is None:
                raise PaperOrderAcceptanceStateError(
                    f"SELL fill allocation {allocation.position_id} is not reserved by this order"
                )
            if (
                allocation.account_id != order.paper_account_id
                or allocation.symbol != order.symbol
                or allocation.strategy_ref != order.strategy_ref
                or allocation.qty > lot_reservation.remaining_qty
            ):
                raise PaperOrderAcceptanceFundsError(
                    f"SELL fill allocation exceeds or conflicts with reservation for "
                    f"{allocation.position_id}"
                )
            fill_allocations[allocation.position_id] = allocation.qty
        if _exact_sum(tuple(fill_allocations.values())) != fill.qty:
            raise PaperOrderAcceptanceStateError(
                "SELL fill allocations must sum exactly to fill qty"
            )

        applied_fills = (*order.fills, fill)
        total_filled = _exact_sum(tuple(item.qty for item in applied_fills))
        if total_filled > initial_order.requested_qty:
            raise PaperOrderAcceptanceStateError(
                f"applied fills exceed requested quantity for order {order.order_id}"
            )
        remaining_qty = _exact_sum(
            (initial_order.requested_qty, total_filled.copy_negate())
        )
        base_asset, quote_asset = order.symbol.split("/")
        proceeds_gross = _exact_product(fill.qty, fill.price)
        fee_amount = fill.commission.amount
        proceeds_net = _exact_sum((proceeds_gross, fee_amount.copy_negate()))
        if proceeds_net < Decimal("0"):
            raise PaperOrderAcceptanceFundsError(
                "SELL commission exceeds gross fill proceeds"
            )

        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id=? AND asset IN (?,?)",
            (order.paper_account_id, base_asset, quote_asset),
        )
        balances = {row[0]: AssetBalance.model_validate_json(row[1]) for row in await cursor.fetchall()}
        if base_asset not in balances or quote_asset not in balances:
            raise PaperOrderAcceptanceFundsError(
                "SELL fill requires existing BASE and QUOTE account balances"
            )
        base_balance = balances[base_asset]
        quote_balance = balances[quote_asset]
        if base_balance.reserved < fill.qty or reservation.amount.amount < fill.qty:
            raise PaperOrderAcceptanceFundsError(
                "SELL fill exceeds active BASE reservation"
            )

        slots: list[_PaperSellAllocationSlotV1] = []
        next_lot_reservations = dict(active_lots)
        fully_closed_positions: set[str] = set()
        for position_id, qty_sold in fill_allocations.items():
            cursor = await conn.execute(
                "SELECT account_id, paper_session_id, environment, lot_json "
                "FROM position_lots WHERE order_id=?",
                (position_id,),
            )
            lot_row = await cursor.fetchone()
            if lot_row is None:
                raise PaperOrderAcceptanceIntegrityError(
                    f"reserved SELL position lot {position_id} is missing"
                )
            lot = PositionLot.model_validate_json(lot_row[3])
            lot_reservation = active_lots[position_id]
            if (
                lot.order_id != position_id
                or lot.account_id != order.paper_account_id
                or lot.paper_session_id != order.paper_session_id
                or lot.environment != order.environment
                or lot_row[0] != lot.account_id
                or lot_row[1] != lot.paper_session_id
                or lot_row[2] != lot.environment
                or qty_sold > lot.total_qty
            ):
                raise PaperOrderAcceptanceIntegrityError(
                    f"reserved SELL position lot {position_id} has inconsistent inventory"
                )
            if qty_sold == lot.total_qty:
                cost_removed = lot.total_cost
                new_lot_qty = Decimal("0")
                new_lot_cost = Decimal("0")
            else:
                try:
                    with localcontext(_POSITION_AVERAGE_CONTEXT):
                        numerator = _exact_product(lot.total_cost, qty_sold)
                        cost_removed = numerator / lot.total_qty
                except DecimalException as exc:
                    raise ValueError(
                        "partial SELL cost basis cannot be calculated within the "
                        "approved Decimal context"
                    ) from exc
                if cost_removed > lot.total_cost:
                    cost_removed = lot.total_cost
                new_lot_qty = _exact_sum((lot.total_qty, qty_sold.copy_negate()))
                new_lot_cost = _exact_sum((lot.total_cost, cost_removed.copy_negate()))
            proceeds = _exact_product(qty_sold, fill.price)
            slots.append(
                _PaperSellAllocationSlotV1(
                    position_id=position_id,
                    qty_sold=qty_sold,
                    cost_basis_removed=cost_removed,
                    proceeds_attributed=proceeds,
                )
            )
            # Defer lot deletion until after reservation rows are cleaned up
            # to avoid FK constraint violations (position_id references
            # position_lots(order_id) without ON DELETE CASCADE).
            if new_lot_qty == Decimal("0"):
                fully_closed_positions.add(position_id)
            else:
                await conn.execute(
                    "UPDATE position_lots SET lot_json=? WHERE order_id=?",
                    (
                        PositionLot(
                            order_id=position_id,
                            account_id=lot.account_id,
                            paper_session_id=lot.paper_session_id,
                            environment=lot.environment,
                            total_qty=new_lot_qty,
                            total_cost=new_lot_cost,
                        ).model_dump_json(),
                        position_id,
                    ),
                )

            reservation_remaining = _exact_sum(
                (lot_reservation.remaining_qty, qty_sold.copy_negate())
            )
            if reservation_remaining > Decimal("0"):
                next_lot_reservations[position_id] = lot_reservation.model_copy(
                    update={"remaining_qty": reservation_remaining}
                )
            else:
                next_lot_reservations.pop(position_id)

        total_cost_removed = _exact_sum(
            tuple(slot.cost_basis_removed for slot in slots)
        )
        accounting = _PaperSellAccountingRecordV1(
            fill_id=fill.fill_id,
            order_id=order.order_id,
            account_id=order.paper_account_id,
            paper_session_id=order.paper_session_id,
            environment=order.environment,
            quote_asset=quote_asset,
            strategy_ref=order.strategy_ref,
            total_qty_filled=fill.qty,
            proceeds_gross=proceeds_gross,
            fee_asset=fill.commission.asset,
            fee_amount=fee_amount,
            proceeds_net=proceeds_net,
            total_cost_basis_removed=total_cost_removed,
            pnl_gross=_exact_sum((proceeds_gross, total_cost_removed.copy_negate())),
            pnl_net=_exact_sum((proceeds_net, total_cost_removed.copy_negate())),
            allocation_slots=tuple(slots),
        )
        new_reserved = _exact_sum(
            tuple(item.remaining_qty for item in next_lot_reservations.values())
        )
        if new_reserved != _exact_sum(
            (reservation.amount.amount, fill.qty.copy_negate())
        ):
            raise PaperOrderAcceptanceIntegrityError(
                "lot reservation remainder does not match active BASE reservation"
            )
        updated_base = AssetBalance(
            asset=base_asset,
            total=_exact_sum((base_balance.total, fill.qty.copy_negate())),
            available=base_balance.available,
            reserved=_exact_sum((base_balance.reserved, fill.qty.copy_negate())),
        )
        updated_quote = AssetBalance(
            asset=quote_asset,
            total=_exact_sum((quote_balance.total, proceeds_net)),
            available=_exact_sum((quote_balance.available, proceeds_net)),
            reserved=quote_balance.reserved,
        )
        new_status = (
            PaperOrderStatus.FILLED
            if remaining_qty == Decimal("0")
            else PaperOrderStatus.PARTIALLY_FILLED
        )
        updated_order_values = order.model_dump(mode="python")
        updated_order_values["status"] = new_status
        updated_order_values["fills"] = [*order.fills, fill]
        updated_order = _PAPER_ORDER_ADAPTER.validate_python(updated_order_values)

        await conn.execute(
            "INSERT INTO paper_fill_applications "
            "(fill_id, order_id, account_id, paper_session_id, environment, applied_at_ms, application_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fill.fill_id, order.order_id, order.paper_account_id, order.paper_session_id,
             order.environment, fill.timestamp_ms, fill.model_dump_json()),
        )
        await conn.execute(
            "INSERT INTO paper_sell_accounting "
            "(fill_id, order_id, account_id, paper_session_id, environment, quote_asset, "
            "strategy_ref_json, total_qty_filled, proceeds_gross, fee_asset, fee_amount, "
            "proceeds_net, total_cost_basis_removed, pnl_gross, pnl_net, "
            "allocation_slots_json, accounting_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fill.fill_id, order.order_id, order.paper_account_id, order.paper_session_id,
             order.environment, quote_asset, order.strategy_ref.model_dump_json(),
             str(fill.qty), str(proceeds_gross), fill.commission.asset, str(fee_amount),
             str(proceeds_net), str(total_cost_removed), str(accounting.pnl_gross),
             str(accounting.pnl_net),
             json.dumps([slot.model_dump(mode="json") for slot in slots]),
             accounting.model_dump_json()),
        )

        await conn.execute(
            "UPDATE paper_account_balances SET balance_json=? WHERE account_id=? AND asset=?",
            (updated_base.model_dump_json(), order.paper_account_id, base_asset),
        )
        await conn.execute(
            "UPDATE paper_account_balances SET balance_json=? WHERE account_id=? AND asset=?",
            (updated_quote.model_dump_json(), order.paper_account_id, quote_asset),
        )
        await conn.execute(
            "DELETE FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
            (order.order_id,),
        )
        if new_status == PaperOrderStatus.FILLED:
            await conn.execute(
                "DELETE FROM paper_reservations WHERE owner_order_id=?", (order.order_id,)
            )
        else:
            updated_reservation = PaperReservationRef(
                reservation_id=reservation.reservation_id,
                owner_order_id=reservation.owner_order_id,
                owner_group_id=reservation.owner_group_id,
                amount=ReservationAmount(asset=base_asset, amount=new_reserved),
            )
            await conn.execute(
                "UPDATE paper_reservations SET amount_json=?, reservation_json=? "
                "WHERE owner_order_id=?",
                (updated_reservation.amount.model_dump_json(),
                 updated_reservation.model_dump_json(), order.order_id),
            )
            for lot_reservation in next_lot_reservations.values():
                await conn.execute(
                    "INSERT INTO paper_reservation_lot_allocations "
                    "(reservation_id, owner_order_id, position_id, account_id, paper_session_id, "
                    "environment, symbol, base_asset, strategy_ref_json, initial_qty, "
                    "remaining_qty, allocation_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (lot_reservation.reservation_id, lot_reservation.owner_order_id,
                     lot_reservation.position_id, lot_reservation.account_id,
                     lot_reservation.paper_session_id, lot_reservation.environment,
                     lot_reservation.symbol, lot_reservation.base_asset,
                     lot_reservation.strategy_ref.model_dump_json(),
                     str(lot_reservation.initial_qty), str(lot_reservation.remaining_qty),
                      lot_reservation.model_dump_json()),
                 )

        # Defer deletion of fully-closed lots until after reservation rows
        # are cleaned up to avoid FK constraint violations.
        if fully_closed_positions:
            placeholders = ",".join("?" for _ in fully_closed_positions)
            await conn.execute(
                f"DELETE FROM position_lots WHERE order_id IN ({placeholders})",
                tuple(fully_closed_positions),
            )

        await conn.execute(
            "UPDATE paper_orders SET order_json=? WHERE order_id=?",
            (updated_order.model_dump_json(), order.order_id),
        )
        return fill

    # ── paper order cancellation (ACT-P-003) ─────────────────────────────


    @staticmethod
    async def _cancel_paper_order_buy_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> PaperOrder:
        """Cancel the remaining quantity of one accepted BUY order.

        Admits only ACCEPTED / PARTIALLY_FILLED BUY orders.  Idempotent for
        CANCELLED orders whose reservation has already been released.
        FILLED / REJECTED / EXPIRED are rejected without changes.
        """
        # 1. Load current order.
        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders WHERE order_id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PaperOrderAcceptanceStateError(
                f"paper order {order_id} not found"
            )
        current_order = SQLiteStore._validate_paper_order_row(row)

        # 2. Idempotency: already CANCELLED with reservation released → no-op.
        if current_order.status == PaperOrderStatus.CANCELLED:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id = ?",
                (order_id,),
            )
            if (await cursor.fetchone())[0] == 0:
                return current_order  # idempotent no-op

        # 3. Reject terminal states without changes.
        if current_order.status in (
            PaperOrderStatus.FILLED,
            PaperOrderStatus.REJECTED,
            PaperOrderStatus.EXPIRED,
        ):
            raise PaperOrderCancellationStateError(
                f"order {order_id} is in terminal state '{current_order.status.value}'; "
                "cannot cancel"
            )

        # 4. Only ACCEPTED / PARTIALLY_FILLED BUY orders are admitted.
        if current_order.status not in (
            PaperOrderStatus.ACCEPTED,
            PaperOrderStatus.PARTIALLY_FILLED,
        ):
            raise PaperOrderCancellationStateError(
                f"order {order_id} status '{current_order.status.value}' is outside "
                "the cancellation scope (ACCEPTED / PARTIALLY_FILLED only)"
            )

        if current_order.side != PaperOrderSide.BUY:
            raise PaperOrderCancellationStateError(
                f"order {order_id} side '{current_order.side.value}' is not BUY; "
                "cancellation scope limited to BUY orders"
            )

        # 5. Load active reservation — the authority for the current remainder.
        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json "
            "FROM paper_reservations WHERE owner_order_id = ?",
            (order_id,),
        )
        active_reservation_row = await cursor.fetchone()
        if active_reservation_row is None:
            raise PaperOrderAcceptanceStateError(
                f"order {order_id} has no active reservation to release"
            )

        active_reservation = PaperReservationRef.model_validate_json(
            active_reservation_row[7]
        )
        remainder_amount = active_reservation.amount.amount

        # Validate indexed columns match the loaded reservation.
        if (
            active_reservation.reservation_id != active_reservation_row[0]
            or active_reservation.owner_order_id != active_reservation_row[1]
            or active_reservation_row[2] != current_order.paper_account_id
            or active_reservation_row[3] != current_order.paper_session_id
            or active_reservation_row[4] != current_order.environment
            or active_reservation.amount.asset != active_reservation_row[5]
            or active_reservation.amount.model_dump_json() != active_reservation_row[6]
        ):
            raise PaperOrderAcceptanceIntegrityError(
                f"active reservation for order {order_id} disagrees with indexed columns"
            )

        # 6. Load QUOTE balance (reserve asset).
        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? AND asset = ?",
            (current_order.paper_account_id, active_reservation.amount.asset),
        )
        quote_row = await cursor.fetchone()
        if quote_row is None:
            raise PaperOrderAcceptanceFundsError(
                f"no balance for {active_reservation.amount.asset} on account "
                f"{current_order.paper_account_id}"
            )
        try:
            quote_balance = AssetBalance.model_validate_json(quote_row[1])
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"account {current_order.paper_account_id} has invalid QUOTE balance JSON"
            ) from exc

        # 7. Release the remainder: reserved decreases, available increases by remainder_amount.
        new_reserved = quote_balance.reserved - remainder_amount
        new_available = quote_balance.available + remainder_amount
        updated_quote = AssetBalance(
            asset=quote_balance.asset,
            total=quote_balance.total,  # total unchanged
            available=new_available,
            reserved=new_reserved,
        )

        # 8. Delete the active reservation row (total of paper_reservations does not change).
        await conn.execute(
            "DELETE FROM paper_reservations WHERE owner_order_id = ?",
            (order_id,),
        )

        # 9. Update QUOTE balance row.
        await conn.execute(
            "UPDATE paper_account_balances SET balance_json = ? "
            "WHERE account_id = ? AND asset = ?",
            (updated_quote.model_dump_json(), current_order.paper_account_id, quote_balance.asset),
        )

        # 10. Persist CANCELLED order — fills / executed_qty / lot / cost / inventory /
        #     commissions already accounted for are preserved exactly.
        updated_values = current_order.model_dump(mode="python")
        updated_values["status"] = PaperOrderStatus.CANCELLED
        cancelled_order = _PAPER_ORDER_ADAPTER.validate_python(updated_values)

        await conn.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
            (cancelled_order.model_dump_json(), order_id),
        )

        return cancelled_order

    async def _cancel_paper_sell_order_inner(
        conn: aiosqlite.Connection,
        order_id: str,
    ) -> PaperOrder:
        """Cancel the remaining quantity of one accepted SELL order.

        Admits only ACCEPTED / PARTIALLY_FILLED SELL orders.  Idempotent for
        CANCELLED orders whose BASE and per-lot reservations have already been
        released.  FILLED / REJECTED / EXPIRED are rejected without changes.
        """
        # 1. Load current order.
        cursor = await conn.execute(
            "SELECT order_id, paper_account_id, paper_session_id, environment, "
            "client_order_id, order_json FROM paper_orders WHERE order_id = ?",
            (order_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise PaperOrderAcceptanceStateError(
                f"paper order {order_id} not found"
            )
        current_order = SQLiteStore._validate_paper_order_row(row)

        # 2. Idempotency: already CANCELLED with all reservations released → no-op.
        if current_order.status == PaperOrderStatus.CANCELLED:
            # Check that no active reservations remain (BASE + per-lot).
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id = ?",
                (order_id,),
            )
            if (await cursor.fetchone())[0] == 0:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM paper_reservation_lot_allocations "
                    "WHERE owner_order_id = ?",
                    (order_id,),
                )
                if (await cursor.fetchone())[0] == 0:
                    return current_order  # idempotent no-op

        # 3. Reject terminal states without changes.
        if current_order.status in (
            PaperOrderStatus.FILLED,
            PaperOrderStatus.REJECTED,
            PaperOrderStatus.EXPIRED,
        ):
            raise PaperOrderCancellationStateError(
                f"order {order_id} is in terminal state '{current_order.status.value}'; "
                "cannot cancel"
            )

        # 4. Only ACCEPTED / PARTIALLY_FILLED SELL orders are admitted.
        if current_order.status not in (
            PaperOrderStatus.ACCEPTED,
            PaperOrderStatus.PARTIALLY_FILLED,
        ):
            raise PaperOrderCancellationStateError(
                f"order {order_id} status '{current_order.status.value}' is outside "
                "the cancellation scope (ACCEPTED / PARTIALLY_FILLED only)"
            )

        if current_order.side != PaperOrderSide.SELL:
            raise PaperOrderCancellationStateError(
                f"order {order_id} side '{current_order.side.value}' is not SELL; "
                "cancellation scope limited to SELL orders"
            )

        # 5. Load active BASE reservation — the authority for the current remainder.
        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
            "environment, asset, amount_json, reservation_json "
            "FROM paper_reservations WHERE owner_order_id = ?",
            (order_id,),
        )
        active_reservation_row = await cursor.fetchone()
        if active_reservation_row is None:
            raise PaperOrderAcceptanceStateError(
                f"order {order_id} has no active BASE reservation to release"
            )

        active_reservation = PaperReservationRef.model_validate_json(
            active_reservation_row[7]
        )
        remainder_amount = active_reservation.amount.amount

        # Verify the reservation asset matches the BASE asset from the order symbol.
        expected_base_asset = current_order.symbol.split("/")[0]
        if active_reservation.amount.asset != expected_base_asset:
            raise PaperOrderAcceptanceIntegrityError(
                f"BASE reservation asset '{active_reservation.amount.asset}' "
                f"does not match expected BASE asset '{expected_base_asset}' "
                f"from symbol '{current_order.symbol}'"
            )

        # Validate indexed columns match the loaded reservation.
        if (
            active_reservation.reservation_id != active_reservation_row[0]
            or active_reservation.owner_order_id != active_reservation_row[1]
            or active_reservation_row[2] != current_order.paper_account_id
            or active_reservation_row[3] != current_order.paper_session_id
            or active_reservation_row[4] != current_order.environment
            or active_reservation.amount.asset != active_reservation_row[5]
            or active_reservation.amount.model_dump_json() != active_reservation_row[6]
        ):
            raise PaperOrderAcceptanceIntegrityError(
                f"active BASE reservation for order {order_id} disagrees with indexed columns"
            )

        # 6. Load BASE balance (reserve asset for SELL).
        cursor = await conn.execute(
            "SELECT asset, balance_json FROM paper_account_balances "
            "WHERE account_id = ? AND asset = ?",
            (current_order.paper_account_id, active_reservation.amount.asset),
        )
        base_row = await cursor.fetchone()
        if base_row is None:
            raise PaperOrderAcceptanceFundsError(
                f"no balance for {active_reservation.amount.asset} on account "
                f"{current_order.paper_account_id}"
            )
        try:
            base_balance = AssetBalance.model_validate_json(base_row[1])
        except Exception as exc:
            raise PaperOrderAcceptanceIntegrityError(
                f"account {current_order.paper_account_id} has invalid BASE balance JSON"
            ) from exc

        # 7. Load per-lot reservations to verify consistency before releasing.
        cursor = await conn.execute(
            "SELECT reservation_id, owner_order_id, position_id, account_id, "
            "paper_session_id, environment, symbol, base_asset, strategy_ref_json, "
            "initial_qty, remaining_qty, allocation_json "
            "FROM paper_reservation_lot_allocations WHERE owner_order_id = ?",
            (order_id,),
        )
        lot_rows = await cursor.fetchall()

        # Validate each lot reservation's indexed columns against its payload and sum remainders.
        sum_remainder: Decimal = Decimal("0")
        for lot_row in lot_rows:
            lot_reservation = _PaperLotReservationAllocationV1.model_validate_json(lot_row[11])

            # Normalize TEXT columns to Decimal before comparing with payload.
            try:
                idx_initial_qty = Decimal(lot_row[9])
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[0]} has invalid initial_qty indexed column: {lot_row[9]!r}"
                ) from exc
            try:
                idx_remaining_qty = Decimal(lot_row[10])
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[0]} has invalid remaining_qty indexed column: {lot_row[10]!r}"
                ) from exc

            if (
                lot_reservation.reservation_id != lot_row[0]
                or lot_reservation.owner_order_id != lot_row[1]
                or lot_row[2] != lot_reservation.position_id
                or lot_reservation.account_id != lot_row[3]
                or lot_reservation.paper_session_id != lot_row[4]
                or lot_reservation.environment != lot_row[5]
                or lot_reservation.symbol != lot_row[6]
                or lot_reservation.base_asset != lot_row[7]
                or lot_reservation.strategy_ref.model_dump_json() != lot_row[8]
                or idx_initial_qty != lot_reservation.initial_qty
                or idx_remaining_qty != lot_reservation.remaining_qty
            ):
                raise PaperOrderAcceptanceIntegrityError(
                    f"lot reservation {lot_row[2]} has inconsistent indexed columns"
                )
            sum_remainder = _exact_sum((sum_remainder, lot_reservation.remaining_qty))

        if sum_remainder != remainder_amount:
            raise PaperOrderAcceptanceIntegrityError(
                f"sum of lot remainders ({sum_remainder}) does not match "
                f"BASE reservation amount ({remainder_amount})"
            )

        # 8. Release BASE balance: reserved decreases, available increases by remainder.
        new_reserved = base_balance.reserved - remainder_amount
        new_available = base_balance.available + remainder_amount
        updated_base = AssetBalance(
            asset=base_balance.asset,
            total=base_balance.total,  # total unchanged
            available=new_available,
            reserved=new_reserved,
        )

        # 9. Delete per-lot reservation rows first (FK: position_id → position_lots).
        if lot_rows:
            for lot_row in lot_rows:
                await conn.execute(
                    "DELETE FROM paper_reservation_lot_allocations "
                    "WHERE reservation_id = ? AND position_id = ?",
                    (lot_row[0], lot_row[2]),
                )

        # 10. Delete the active BASE reservation row.
        await conn.execute(
            "DELETE FROM paper_reservations WHERE owner_order_id = ?",
            (order_id,),
        )

        # 11. Update BASE balance row.
        await conn.execute(
            "UPDATE paper_account_balances SET balance_json = ? "
            "WHERE account_id = ? AND asset = ?",
            (updated_base.model_dump_json(), current_order.paper_account_id, base_balance.asset),
        )

        # 12. Persist CANCELLED order — fills / executed_qty / cost basis removed /
        #     proceeds / accounting already applied are preserved exactly.
        updated_values = current_order.model_dump(mode="python")
        updated_values["status"] = PaperOrderStatus.CANCELLED
        cancelled_order = _PAPER_ORDER_ADAPTER.validate_python(updated_values)

        await conn.execute(
            "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
            (cancelled_order.model_dump_json(), order_id),
        )

        return cancelled_order

    async def _read(self, fn, *args: Any) -> Any:
        """Run all reads in one snapshot transaction."""
        conn = await aiosqlite.connect(self._db_path)
        try:
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("BEGIN")
            try:
                result = await fn(conn, *args)
                await conn.commit()
                return result
            except BaseException:
                await conn.rollback()
                raise
        finally:
            await conn.close()

    # ── migration / initialization ─────────────────────────────────────────

    @staticmethod
    async def _initialize_inner(conn: aiosqlite.Connection) -> None:
        """Create v9 directly or atomically migrate the accepted schema path."""
        cursor = await conn.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        version = row[0]

        # Reject unknown versions before touching anything.
        if version not in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, _SCHEMA_VERSION):
            raise SchemaVersionMismatchError(
                f"expected schema version 0, 1, 2, 3, 4, 5, 6, 7, 8, 10 or {_SCHEMA_VERSION}, got {version}"
            )

        cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {row[0] for row in await cursor.fetchall()}

        # Already at v11 — just verify completeness.
        if version == _SCHEMA_VERSION:
            if not _SCHEMA_V11_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 11 but required tables are missing"
                )
            return

        # v8 → v11: add order reservations, fill application, and SELL tables (for direct v8→v11 migration).
        if version == 8:
            if not _SCHEMA_V8_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 8 but required v8 tables are missing"
                )
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v9 → v10: add durable fill application marks, position lots, and SELL tables.
        if version == 9:
            if not _SCHEMA_V9_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 9 but required v9 tables are missing"
                )
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v10 → v11: add paper_reservation_lot_allocations and paper_sell_accounting tables.
        if version == 10:
            if not _SCHEMA_V10_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 10 but required v10 tables are missing"
                )
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v7 → v11: add order context, reservations, fill application, and SELL tables.
        if version == 7:
            if not _SCHEMA_V7_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 7 but required v7 tables are missing"
                )
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v6 → v11: add account openings/balances, order context/reservations, fill application, and SELL tables.
        if version == 6:
            if not _SCHEMA_V6_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 6 but required v6 tables are missing"
                )
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v5 → v11: add paper_fills, account openings/balances, order context/reservations, fill application, and SELL tables.
        if version == 5:
            if not _SCHEMA_V5_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 5 but required v5 tables are missing"
                )
            for statement in _PAPER_FILL_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v4 → v11: add dispositions, paper_fills, account openings/balances, order context/reservations, fill application, and SELL tables.
        if version == 4:
            if not _SCHEMA_V4_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 4 but required v4 tables are missing"
                )
            for statement in _SCHEMA_V5_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v3 → v11: rebuild run_attempts and add all later schema tables.
        if version == 3:
            if not _SCHEMA_V3_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 3 but required v3 tables are missing"
                )
            # Rebuild run_attempts with 'completed' in the CHECK constraint, preserving data.
            await conn.execute("DROP TABLE IF EXISTS run_attempts_new")
            await conn.execute(
                """CREATE TABLE run_attempts_new (
                    run_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    experiment_version TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')),
                    run_json TEXT NOT NULL,
                    FOREIGN KEY (experiment_id, experiment_version)
                        REFERENCES experiment_definitions(experiment_id, experiment_version)
                )"""
            )
            await conn.execute(
                "INSERT INTO run_attempts_new SELECT * FROM run_attempts"
            )
            await conn.execute("DROP TABLE run_attempts")
            await conn.execute("ALTER TABLE run_attempts_new RENAME TO run_attempts")
            for statement in _SCHEMA_V4_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V5_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v2 → v11: apply all later schema versions atomically.
        if version == 2:
            if not _SCHEMA_V2_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 2 but required v2 tables are missing"
                )
            for statement in _SCHEMA_V3_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V4_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V5_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v1 → v11: apply all later schema versions atomically.
        if version == 1:
            if not _SCHEMA_V1_TABLES <= table_names:
                raise SchemaVersionMismatchError(
                    "schema version is 1 but required v1 tables are missing"
                )
            for statement in _SCHEMA_V2_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V3_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V4_STATEMENTS:
                await conn.execute(statement)
            for statement in _SCHEMA_V5_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
                await conn.execute(statement)
            for statement in _PAPER_SELL_DDL_STATEMENTS:
                await conn.execute(statement)
            await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            return

        # v0 (new database): initialise directly at v11.
        for statement in _SCHEMA_V1_STATEMENTS:
            await conn.execute(statement)
        for statement in _SCHEMA_V2_STATEMENTS:
            await conn.execute(statement)
        for statement in _SCHEMA_V3_STATEMENTS:
            await conn.execute(statement)
        for statement in _SCHEMA_V4_STATEMENTS:
            await conn.execute(statement)
        for statement in _SCHEMA_V5_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_FILL_DDL_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_ORDER_DDL_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_ORDER_RESERVATION_DDL_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_FILL_APPLICATION_DDL_STATEMENTS:
            await conn.execute(statement)
        for statement in _PAPER_SELL_DDL_STATEMENTS:
            await conn.execute(statement)
        await conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    # ── session helpers (unchanged from v2) ────────────────────────────────

    @staticmethod
    async def _save_session_inner(
        conn: aiosqlite.Connection,
        session_id: str,
        strategy_configs: Sequence[StrategyConfig],
        dataset_refs: Sequence[DatasetRef],
    ) -> None:
        # Session and associations are append-only; a repeated identity is harmless.
        await conn.execute(
            "INSERT OR IGNORE INTO sessions (session_id) VALUES (?)",
            (session_id,),
        )

        # Append-only config versions
        for config in strategy_configs:
            sid = config.strategy_ref.strategy_id
            sv = config.strategy_ref.strategy_version
            config_json = config.model_dump_json()
            cursor = await conn.execute(
                "SELECT config_json FROM strategy_config_versions "
                "WHERE strategy_id = ? AND strategy_version = ?",
                (sid, sv),
            )
            existing = await cursor.fetchone()
            if existing is None:
                await conn.execute(
                    "INSERT INTO strategy_config_versions (strategy_id, strategy_version, config_json) "
                    "VALUES (?, ?, ?)",
                    (sid, sv, config_json),
                )
            elif existing[0] != config_json:
                raise ConfigConflictError(
                    f"conflict for strategy {sid} version {sv}: JSON differs"
                )

        # Upsert session-strategy associations
        for config in strategy_configs:
            await conn.execute(
                "INSERT OR IGNORE INTO session_strategy_configs "
                "(session_id, strategy_id, strategy_version) VALUES (?, ?, ?)",
                (
                    session_id,
                    config.strategy_ref.strategy_id,
                    config.strategy_ref.strategy_version,
                ),
            )

        # Upsert dataset refs
        for ref in dataset_refs:
            await conn.execute(
                "INSERT OR IGNORE INTO session_dataset_refs (session_id, dataset_ref) VALUES (?, ?)",
                (session_id, ref),
            )

    @staticmethod
    async def _load_session_inner(
        conn: aiosqlite.Connection, session_id: str
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        configs: list[StrategyConfig] = []
        cursor = await conn.execute(
            "SELECT strategy_id, strategy_version, config_json "
            "FROM session_strategy_configs JOIN strategy_config_versions "
            "USING (strategy_id, strategy_version) "
            "WHERE session_strategy_configs.session_id = ? "
            "ORDER BY strategy_id, strategy_version",
            (session_id,),
        )
        for sid, version, config_json in await cursor.fetchall():
            config = StrategyConfig.model_validate_json(config_json)
            if (
                config.strategy_ref.strategy_id != sid
                or config.strategy_ref.strategy_version != version
            ):
                raise ValueError("stored config JSON does not match its StrategyRef key")
            configs.append(config)

        cursor = await conn.execute(
            "SELECT dataset_ref FROM session_dataset_refs WHERE session_id = ? "
            "ORDER BY dataset_ref",
            (session_id,),
        )
        refs = [
            _DATASET_REF_ADAPTER.validate_python(row[0])
            for row in await cursor.fetchall()
        ]
        return {"session_id": session_id, "strategy_configs": configs, "dataset_refs": refs}

    # ── experiment helpers (unchanged from v2) ─────────────────────────────

    @staticmethod
    async def _register_experiment_inner(
        conn: aiosqlite.Connection, definition: ExperimentDefinition
    ) -> ExperimentDefinition:
        experiment_id = definition.experiment_ref.experiment_id
        experiment_version = definition.experiment_ref.experiment_version
        definition_json = definition.model_dump_json()
        cursor = await conn.execute(
            "SELECT definition_json FROM experiment_definitions "
            "WHERE experiment_id = ? AND experiment_version = ?",
            (experiment_id, experiment_version),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            if existing[0] != definition_json:
                raise ExperimentDefinitionConflictError(
                    f"conflict for experiment {experiment_id} version {experiment_version}: definition differs"
                )
            return ExperimentDefinition.model_validate_json(existing[0])

        await conn.execute(
            "INSERT INTO experiment_definitions "
            "(experiment_id, experiment_version, definition_json) VALUES (?, ?, ?)",
            (experiment_id, experiment_version, definition_json),
        )
        return definition

    @staticmethod
    async def _load_experiment_inner(
        conn: aiosqlite.Connection, experiment_ref: ExperimentRef
    ) -> ExperimentDefinition | None:
        cursor = await conn.execute(
            "SELECT definition_json FROM experiment_definitions "
            "WHERE experiment_id = ? AND experiment_version = ?",
            (experiment_ref.experiment_id, experiment_ref.experiment_version),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        definition = ExperimentDefinition.model_validate_json(row[0])
        stored_ref = definition.experiment_ref
        if (
            stored_ref.experiment_id != experiment_ref.experiment_id
            or stored_ref.experiment_version != experiment_ref.experiment_version
        ):
            raise ExperimentDefinitionIntegrityError(
                "stored definition payload does not match its ExperimentRef key"
            )
        return definition

    # ── run attempt helpers (v3) ───────────────────────────────────────────

    @staticmethod
    async def _create_run_attempt_inner(
        conn: aiosqlite.Connection, run: RunAttempt
    ) -> RunAttempt:
        """Persist a pending run attempt with full validation."""
        run_id = run.run_id
        experiment_id = run.experiment_ref.experiment_id
        experiment_version = run.experiment_ref.experiment_version
        variant_id = run.variant_id
        run_json = run.model_dump_json()

        # Check that the experiment definition exists.
        cursor = await conn.execute(
            "SELECT definition_json FROM experiment_definitions "
            "WHERE experiment_id = ? AND experiment_version = ?",
            (experiment_id, experiment_version),
        )
        def_row = await cursor.fetchone()
        if def_row is None:
            raise ValueError(
                f"experiment {experiment_id} version {experiment_version} not found"
            )

        # Validate variant_id and dataset_ref against the stored definition.
        definition = ExperimentDefinition.model_validate_json(def_row[0])
        variant = next(
            (v for v in definition.variants if v.variant_id == variant_id), None
        )
        if variant is None:
            raise ValueError(
                f"variant {variant_id} not found in experiment "
                f"{experiment_id} version {experiment_version}"
            )

        # A persisted discard is a terminal decision for this variant. Check it
        # inside the same BEGIN IMMEDIATE transaction as the run insert so a
        # concurrent discard/run race can commit at most one outcome.
        cursor = await conn.execute(
            "SELECT 1 FROM variant_dispositions "
            "WHERE experiment_id = ? AND experiment_version = ? AND variant_id = ?",
            (experiment_id, experiment_version, variant_id),
        )
        if await cursor.fetchone() is not None:
            raise RunAttemptStateError(
                f"variant {variant_id} was discarded before run; "
                "cannot create a RunAttempt"
            )

        # The run's dataset_ref_used must match the variant's dataset_ref
        # when explicitly provided (None is allowed for pending runs that
        # haven't selected a dataset yet).
        if run.dataset_ref_used is not None and run.dataset_ref_used != variant.dataset_ref:
            raise ValueError(
                f"run dataset_ref_used does not match variant dataset_ref for "
                f"variant {variant_id}"
            )

        # Idempotency also validates indexed identity against the stored payload.
        existing = await SQLiteStore._load_run_attempt_inner(conn, run_id)
        if existing is not None:
            if existing.model_dump_json() == run_json:
                return existing
            # Same run_id, different content — conflict, no overwrite.
            else:
                raise RunAttemptConflictError(
                    f"run {run_id} already exists with different content; "
                    "no overwrite allowed"
                )

        # Insert the new row.
        await conn.execute(
            "INSERT INTO run_attempts "
            "(run_id, experiment_id, experiment_version, variant_id, status, run_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, experiment_id, experiment_version, variant_id, run.status, run_json),
        )
        return run

    @staticmethod
    async def _transition_run_attempt_inner(
        conn: aiosqlite.Connection, run_id: str, target: RunAttempt
    ) -> RunAttempt:
        """Transition a persisted run to a new state with CAS and validation."""
        # Reject completed explicitly.
        if target.status == "completed":
            raise RunAttemptStateError(
                "transitioning to 'completed' is not allowed in this slice; "
                "publish ExperimentResult atomically instead"
            )

        # Read and validate the current row (CAS base).
        existing = await SQLiteStore._load_run_attempt_inner(conn, run_id)
        if existing is None:
            raise RunAttemptStateError(f"run {run_id} not found")

        # A run's immutable identity cannot change across state transitions.
        if (
            existing.run_id != target.run_id
            or existing.experiment_ref != target.experiment_ref
            or existing.variant_id != target.variant_id
        ):
            raise RunAttemptStateError(
                "run_id, experiment_ref and variant_id are immutable across transitions"
            )

        stored_status = existing.status

        # Terminal-state guard.
        if stored_status in TERMINAL_STATES:
            raise RunAttemptStateError(
                f"run {run_id} is terminal ({stored_status}); no transition allowed"
            )

        # Transition validity check.
        if not validate_transition(stored_status, target.status):
            raise RunAttemptStateError(
                f"invalid transition: {stored_status} → {target.status}"
            )

        if existing.started_at_ms is not None:
            immutable_context = (
                "started_at_ms",
                "dataset_ref_used",
                "code_ref",
                "engine_ref",
                "effective_periods",
                "seed",
            )
            if any(getattr(existing, name) != getattr(target, name) for name in immutable_context):
                raise RunAttemptStateError(
                    "effective run context is immutable after execution starts"
                )

        # Once a run has started, its effective data must match the immutable variant.
        if target.started_at_ms is not None:
            definition = await SQLiteStore._load_experiment_inner(
                conn, target.experiment_ref
            )
            if definition is None:
                raise RunAttemptStateError("experiment definition no longer exists")
            variant = next(
                (item for item in definition.variants if item.variant_id == target.variant_id),
                None,
            )
            if variant is None or target.dataset_ref_used != variant.dataset_ref:
                raise RunAttemptStateError(
                    "effective DatasetRef does not match the immutable experiment variant"
                )

        # Compare-and-swap: ensure nobody changed the row between read and write.
        cursor = await conn.execute(
            "UPDATE run_attempts SET status = ?, run_json = ? "
            "WHERE run_id = ? AND status = ? "
            "RETURNING run_id",
            (target.status, target.model_dump_json(), run_id, stored_status),
        )
        updated = await cursor.fetchone()
        if updated is None:
            raise RunAttemptStateError(
                f"stale state for {run_id}: concurrent modification detected"
            )

        # Post-transition validation for non-pending states.
        if target.status in ("running", "failed", "cancelled"):
            _validate_transitioned_run(target)

        return target

    @staticmethod
    async def _load_run_attempt_inner(
        conn: aiosqlite.Connection, run_id: str
    ) -> RunAttempt | None:
        cursor = await conn.execute(
            "SELECT run_id, experiment_id, experiment_version, variant_id, status, run_json "
            "FROM run_attempts WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        stored_run_id, experiment_id, experiment_version, variant_id, status, run_json = row
        run = RunAttempt.model_validate_json(run_json)
        if (
            run.run_id != stored_run_id
            or run.experiment_ref.experiment_id != experiment_id
            or run.experiment_ref.experiment_version != experiment_version
            or run.variant_id != variant_id
            or run.status != status
        ):
            raise RunAttemptIntegrityError(
                "stored run payload does not match indexed run identity/state"
            )
        return run

    # ── experiment result helpers (v4) ─────────────────────────────────────

    @staticmethod
    async def _publish_experiment_result_inner(
        conn: aiosqlite.Connection,
        completed_run: RunAttempt,
        result: ExperimentResult,
    ) -> None:
        """Atomically save ExperimentResult and update run to completed."""
        run_id = completed_run.run_id
        result_id = result.result_id

        # Re-read the stored run to validate identity/context hasn't changed.
        existing_run = await SQLiteStore._load_run_attempt_inner(conn, run_id)
        if existing_run is None:
            raise ExperimentResultStateError(f"run {run_id} not found")
        if existing_run.status == "completed":
            existing_result = await SQLiteStore._load_experiment_result_inner(conn, run_id)
            if existing_result is None:
                raise ExperimentResultStateError(
                    f"completed run {run_id} has no persisted ExperimentResult"
                )
            if (
                existing_run.model_dump_json() == completed_run.model_dump_json()
                and existing_result.model_dump_json() == result.model_dump_json()
            ):
                return  # exact retry: same completed run and result, no duplicate
            raise ExperimentResultConflictError(
                f"run {run_id} is already completed with different result content"
            )
        if existing_run.status != "running":
            raise ExperimentResultStateError(
                f"run {run_id} status is '{existing_run.status}'; "
                "must be 'running' to publish a result"
            )

        # Validate immutable identity and context haven't changed.
        if (
            existing_run.run_id != completed_run.run_id
            or existing_run.experiment_ref != completed_run.experiment_ref
            or existing_run.variant_id != completed_run.variant_id
            or existing_run.started_at_ms != completed_run.started_at_ms
            or existing_run.seed != completed_run.seed
            or existing_run.dataset_ref_used != completed_run.dataset_ref_used
            or existing_run.code_ref != completed_run.code_ref
            or existing_run.engine_ref != completed_run.engine_ref
            or existing_run.effective_periods != completed_run.effective_periods
        ):
            raise ExperimentResultStateError(
                "run identity or effective context has changed since completion"
            )

        # Check for existing result with same result_id.
        cursor = await conn.execute(
            "SELECT result_json FROM experiment_results WHERE result_id = ?",
            (result_id,),
        )
        existing_result_row = await cursor.fetchone()
        if existing_result_row is not None:
            existing_result = ExperimentResult.model_validate_json(existing_result_row[0])
            # Idempotency: same run_id/result_id and identical content → no-op.
            if (
                existing_result.run_id == result.run_id
                and existing_result.result_id == result.result_id
                and existing_result.artifact_refs == result.artifact_refs
            ):
                return  # idempotent, nothing to do
            raise ExperimentResultConflictError(
                f"result {result_id} already exists with different content; "
                "no overwrite allowed"
            )

        # Check for existing result with same run_id (shouldn't happen due to FK uniqueness).
        cursor = await conn.execute(
            "SELECT result_id FROM experiment_results WHERE run_id = ?",
            (run_id,),
        )
        existing_by_run = await cursor.fetchone()
        if existing_by_run is not None:
            raise ExperimentResultConflictError(
                f"run {run_id} already has a result ({existing_by_run[0]}); "
                "at most one result per run"
            )

        # Insert the new experiment result.
        await conn.execute(
            "INSERT INTO experiment_results "
            "(result_id, run_id, result_json) VALUES (?, ?, ?)",
            (result_id, run_id, result.model_dump_json()),
        )

        # Update the run to completed with its result_id.
        await conn.execute(
            "UPDATE run_attempts SET status = ?, run_json = ? "
            "WHERE run_id = ? AND status = ?",
            (
                "completed",
                completed_run.model_dump_json(),
                run_id,
                "running",
            ),
        )

    @staticmethod
    async def _load_experiment_result_inner(
        conn: aiosqlite.Connection, run_id: str
    ) -> ExperimentResult | None:
        """Load and validate an experiment result linked to *run_id*."""
        cursor = await conn.execute(
            "SELECT result_id, run_id, result_json "
            "FROM experiment_results WHERE run_id = ?",
            (run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        stored_result_id, stored_run_id, result_json = row

        # Validate the result JSON can be parsed.
        try:
            result = ExperimentResult.model_validate_json(result_json)
        except Exception as exc:
            raise ExperimentResultConflictError(
                f"experiment result for run {run_id} has corrupt JSON"
            ) from exc

        # Validate keys match stored columns.
        if (
            result.result_id != stored_result_id
            or result.run_id != stored_run_id
        ):
            raise ExperimentResultConflictError(
                "stored experiment result payload does not match indexed identity"
            )

        # Validate non-empty artifact_refs.
        if not result.artifact_refs:
            raise ExperimentResultConflictError(
                f"experiment result {result.result_id} has empty artifact_refs"
            )

        return result


def _validate_transitioned_run(run: RunAttempt) -> None:
    """Validate a run that has transitioned to running/failed/cancelled.

    Checks the resolved StrategyConfig, effective DatasetRef, and contract fields.
    """
    # The contract-level model_validator already enforces state invariants;
    # we just verify the RunAttempt instance is valid at this point.
    try:
        RunAttempt.model_validate(run.model_dump(mode="python"))
    except Exception as exc:
        raise ValueError(f"post-transition validation failed: {exc}") from exc
