"""DTOs contractuales para órdenes paper, fills, posiciones y reservas Spot.

Estos DTOs son estrictamente aditivos: no reemplazan ni reexportan los modelos
actuales de ``bot.models`` ni ``models.state``.  Se importan desde
``src.models.paper_contracts`` y pueden leerse con lectura sola para mapear
compatibilidad con los enums existentes en ``src.models.state``.
"""

from __future__ import annotations

from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Subnormal,
    Underflow,
    localcontext,
)
from typing import Annotated, Literal, Optional, Sequence

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator
from src.models.state import OrderSide as PaperOrderSide
from src.models.state import OrderStatus as PaperOrderStatus
from src.models.state import OrderType as PaperOrderType


def _paper_decimal(value: object) -> Decimal:
    """Accept exact Decimal/string inputs only; never round-trip through binary float."""
    if isinstance(value, float) or isinstance(value, int) or isinstance(value, bool):
        raise ValueError("amounts must be Decimal or decimal strings, never float/int")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal string") from exc
    else:
        raise ValueError("amounts must be Decimal or decimal strings")
    if not result.is_finite():
        raise ValueError("amounts must be finite")
    return result


PaperDecimal = Annotated[Decimal, BeforeValidator(_paper_decimal)]


_POSITION_AVERAGE_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    Emin=MIN_EMIN,
    Emax=MAX_EMAX,
    clamp=0,
    traps=[Clamped, DivisionByZero, InvalidOperation, Overflow, Subnormal, Underflow],
)


def _average_projection(qty: Decimal, total_cost: Decimal) -> Decimal:
    """Derive the 34-significant-digit average without consulting global context.

    The projection uses ROUND_HALF_EVEN, 34 significant digits, Emin=-999999999999999999,
    Emax=999999999999999999 and clamp=0. Clamped, division-by-zero, invalid, overflow,
    underflow and subnormal signal a validation error; ordinary inexact/rounded division
    is the intended projection. The explicit context is copied per operation, never
    inherited from the process/thread Decimal context.
    """
    try:
        with localcontext(_POSITION_AVERAGE_CONTEXT):
            return total_cost / qty
    except DecimalException as exc:
        raise ValueError(
            "avg_entry_price cannot be projected within the explicit Decimal context"
        ) from exc


def _exact_sum(values: Sequence[Decimal]) -> Decimal:
    """Add finite Decimals exactly using enough local precision for alignment/carry."""
    if not values:
        return Decimal("0")
    min_exponent = min(value.as_tuple().exponent for value in values)
    max_adjusted = max(value.adjusted() if value else 0 for value in values)
    precision = max(1, max_adjusted - min_exponent + 1 + len(str(len(values))))
    context = Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        clamp=0,
        traps=[Clamped, DivisionByZero, Inexact, InvalidOperation, Overflow, Underflow],
    )
    try:
        with localcontext(context):
            result = Decimal("0")
            for value in values:
                result += value
            return result
    except DecimalException as exc:
        raise ValueError("exact Decimal sum exceeds supported exponent range") from exc


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite Decimals exactly with operand-derived local precision."""
    precision = max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))
    context = Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        clamp=0,
        traps=[Clamped, DivisionByZero, Inexact, InvalidOperation, Overflow, Underflow],
    )
    try:
        with localcontext(context):
            return left * right
    except DecimalException as exc:
        raise ValueError("exact Decimal product exceeds supported exponent range") from exc

# ── Reutilización de enums legacy (solo lectura) ────────────────────────────────
# Los tests pueden verificar compatibilidad con estos valores sin importar los
# modelos legacy directamente.

LEGACY_ORDER_SIDE_VALUES = tuple(value.value for value in PaperOrderSide)
LEGACY_ORDER_TYPE_VALUES = tuple(value.value for value in PaperOrderType)
LEGACY_ORDER_STATUS_VALUES = tuple(value.value for value in PaperOrderStatus)

# ── Matriz estática de transiciones PAPER local ────────────────────────────────
# Expuesta como constante del módulo para que los tests la verifiquen.

TRANSITION_MATRIX: dict[str, list[str]] = {
    "prepared": ["accepted", "rejected", "cancelled", "expired"],
    "accepted": ["partially_filled", "filled", "cancelled", "expired"],
    "partially_filled": [
        "partially_filled", "filled", "cancelled", "expired",
    ],
}

TERMINAL_STATES: set[str] = {"rejected", "filled", "cancelled", "expired"}


# ── StrategyRef ───────────────────────────────────────────────────────────────

class StrategyRef(BaseModel):
    """Identificador inmutable de una estrategia y su versión.

    ``strategy_id`` es estable; ``strategy_version`` no cambia tras la creación.
    ``decision_id`` refiere a un ``DecisionRecord`` (cadena arbitraria).
    La ``schema_version`` de este DTO es independiente de la versión de estrategia.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["paper_strategy_ref.v1"] = Field(
        default="paper_strategy_ref.v1", frozen=True
    )
    strategy_id: str = Field(..., min_length=1)
    strategy_version: str = Field(..., frozen=True)
    decision_id: Optional[str] = None


# ── Commission ────────────────────────────────────────────────────────────────

class Commission(BaseModel):
    """Comisión presente aunque nullable.

    - ``None``  -> coste desconocido (no afirmar PnL neto).
    - ``Commission(amount="0", asset=...)`` -> fee cero conocido.
    El campo ``commission`` DEBE estar presente en el DTO; no se permite omitirlo.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: PaperDecimal = Field(..., ge=Decimal("0"))
    asset: str = Field(..., min_length=1)


# ── PaperFill ─────────────────────────────────────────────────────────────────

class LotAllocation(BaseModel):
    """Asignación de un fill a un lote específico (venta).

    En ventas, cada allocation debe referir la misma cuenta, símbolo y estrategia.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    position_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=3)
    strategy_ref: StrategyRef
    qty: PaperDecimal = Field(..., gt=Decimal("0"))


class PaperFill(BaseModel):
    """Evento inmutable de ejecución.

    ``commission`` es obligatorio pero nullable:
      - ``None``  -> coste desconocido.
      - ``Commission(...)`` -> fee conocido (puede ser amount=0).
    En ventas, ``lot_allocations`` debe estar presente y sumar exactamente qty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["paper_fill.v1"] = Field(
        default="paper_fill.v1", frozen=True
    )
    fill_id: str = Field(..., min_length=1)
    order_id: str = Field(..., min_length=1)
    qty: PaperDecimal = Field(..., gt=Decimal("0"))
    price: PaperDecimal = Field(..., gt=Decimal("0"))
    timestamp_ms: int = Field(..., ge=0)
    commission: Optional[Commission]
    lot_allocations: tuple[LotAllocation, ...] = ()

    @field_validator("lot_allocations")
    @classmethod
    def allocations_sum_qty(cls, v: Sequence[LotAllocation], info) -> Sequence[LotAllocation]:
        if v:
            total = sum(a.qty for a in v)
            qty = info.data.get("qty", Decimal("0"))
            if total != qty:
                raise ValueError(
                    f"lot_allocations must sum to fill qty ({total} != {qty})"
                )
        return v


# ── PaperOrder ────────────────────────────────────────────────────────────────

class PaperOrder(BaseModel):
    """DTO de orden paper Spot.

    Restricciones del contrato:
      - ``environment`` siempre "paper", inmutable.
      - ``LIMIT`` requiere ``price`` positivo.
      - ``STOP_LOSS`` / ``TAKE_PROFIT`` requieren ``trigger_price`` positivo.
      - ``MARKET`` + ``BUY`` requiere ``max_quote_amount`` positivo.
      - La suma de fills NO puede exceder ``requested_qty`` (remaining_qty >= 0).
      - Todos los fills deben referir al mismo ``order_id``.
    """

    schema_version: Literal["paper_order.v1"] = Field(
        default="paper_order.v1", frozen=True
    )
    order_id: str = Field(..., min_length=1)
    client_order_id: str = Field(..., min_length=1)
    paper_account_id: str = Field(..., min_length=1)
    paper_session_id: str = Field(..., min_length=1)
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    symbol: str = Field(..., min_length=3)
    side: PaperOrderSide
    order_type: PaperOrderType
    requested_qty: PaperDecimal = Field(..., gt=Decimal("0"))
    fills: Sequence[PaperFill] = Field(default_factory=list)
    lot_allocations: Sequence[LotAllocation] = Field(default_factory=list)
    price: Optional[PaperDecimal] = None
    trigger_price: Optional[PaperDecimal] = None
    max_quote_amount: Optional[PaperDecimal] = None
    strategy_ref: StrategyRef
    decision_id: Optional[str] = None
    status: PaperOrderStatus = PaperOrderStatus.PREPARED
    timestamp_ms: int = Field(..., ge=0)
    position_id: Optional[str] = None
    protection_group_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_field_combos(self) -> PaperOrder:
        """Validar combinaciones de campos según order_type."""
        if self.order_type == PaperOrderType.LIMIT:
            if self.price is None or self.price <= Decimal("0"):
                raise ValueError("LIMIT requires positive price")
        if self.order_type in (PaperOrderType.STOP_LOSS, PaperOrderType.TAKE_PROFIT):
            if self.trigger_price is None or self.trigger_price <= Decimal("0"):
                raise ValueError(f"{self.order_type} requires positive trigger_price")
        if self.order_type == PaperOrderType.MARKET and self.side == PaperOrderSide.BUY:
            if self.max_quote_amount is None or self.max_quote_amount <= Decimal("0"):
                raise ValueError("MARKET BUY requires positive max_quote_amount")
        return self

    @model_validator(mode="after")
    def validate_fills(self) -> PaperOrder:
        """Validar fills: mismo order_id y no exceder requested_qty."""
        fill_ids: set[str] = set()
        for f in self.fills:
            if f.fill_id in fill_ids:
                raise ValueError(f"duplicate fill_id in order: {f.fill_id}")
            fill_ids.add(f.fill_id)
            if f.order_id != self.order_id:
                raise ValueError(
                    f"fill order_id ({f.order_id}) must match parent order_id ({self.order_id})"
                )
        total_filled = sum(f.qty for f in self.fills)
        if total_filled > self.requested_qty:
            raise ValueError(
                f"sum of fills ({total_filled}) must not exceed requested_qty ({self.requested_qty})"
            )
        if self.status == PaperOrderStatus.FILLED and total_filled != self.requested_qty:
            raise ValueError("FILLED order requires executed quantity equal to requested_qty")
        if self.status == PaperOrderStatus.PARTIALLY_FILLED and not (
            Decimal("0") < total_filled < self.requested_qty
        ):
            raise ValueError("PARTIALLY_FILLED order requires a positive non-final executed quantity")
        if self.status in (
            PaperOrderStatus.PREPARED,
            PaperOrderStatus.PENDING_SEND,
            PaperOrderStatus.ACCEPTED,
            PaperOrderStatus.REJECTED,
        ) and total_filled != Decimal("0"):
            raise ValueError(f"{self.status} order cannot contain fills")
        if self.status in (PaperOrderStatus.CANCELLED, PaperOrderStatus.EXPIRED) and total_filled >= self.requested_qty:
            raise ValueError(f"{self.status} order must have an unfilled remainder")
        return self

    @model_validator(mode="after")
    def validate_sale_allocations(self) -> PaperOrder:
        """Las ventas asignan la solicitud y cada fill a lotes explícitos."""
        if self.side == PaperOrderSide.BUY:
            if self.lot_allocations or any(fill.lot_allocations for fill in self.fills):
                raise ValueError("BUY orders/fills cannot consume existing position lots")
            return self

        if not self.lot_allocations:
            raise ValueError("SELL orders require explicit lot_allocations before acceptance")

        requested_by_lot: dict[str, Decimal] = {}
        for allocation in self.lot_allocations:
            if allocation.position_id in requested_by_lot:
                raise ValueError("SELL order lot_allocations must not duplicate position_id")
            self._validate_allocation_owner(allocation)
            requested_by_lot[allocation.position_id] = allocation.qty
        if sum(requested_by_lot.values()) != self.requested_qty:
            raise ValueError("SELL order lot_allocations must sum to requested_qty")

        filled_by_lot: dict[str, Decimal] = {}
        for fill in self.fills:
            if not fill.lot_allocations:
                raise ValueError(f"SELL fill {fill.fill_id} requires explicit lot_allocations")
            for allocation in fill.lot_allocations:
                self._validate_allocation_owner(allocation)
                if allocation.position_id not in requested_by_lot:
                    raise ValueError("SELL fill allocation must be included in order lot_allocations")
                filled_by_lot[allocation.position_id] = (
                    filled_by_lot.get(allocation.position_id, Decimal("0")) + allocation.qty
                )
                if filled_by_lot[allocation.position_id] > requested_by_lot[allocation.position_id]:
                    raise ValueError("SELL fills cannot consume more than the order allocation per lot")
        return self

    def _validate_allocation_owner(self, allocation: LotAllocation) -> None:
        if allocation.account_id != self.paper_account_id:
            raise ValueError("lot allocation account_id must match the order account")
        if allocation.symbol != self.symbol:
            raise ValueError("lot allocation symbol must match the order symbol")
        if (
            allocation.strategy_ref.strategy_id != self.strategy_ref.strategy_id
            or allocation.strategy_ref.strategy_version != self.strategy_ref.strategy_version
        ):
            raise ValueError("lot allocation strategy_ref must match the order strategy_ref")

    @property
    def executed_qty(self) -> Decimal:
        return sum(f.qty for f in self.fills) if self.fills else Decimal("0")

    @property
    def remaining_qty(self) -> Decimal:
        return self.requested_qty - self.executed_qty


# ── PaperPositionView / PositionLot ───────────────────────────────────────────

class PositionLot(BaseModel):
    """Lot with authoritative gross quantity/cost and a derived average projection."""

    position_id: str = Field(..., min_length=1)
    entry_order_id: str = Field(..., min_length=1)
    strategy_ref: StrategyRef
    qty: PaperDecimal = Field(..., gt=Decimal("0"))
    avg_entry_price: PaperDecimal = Field(..., gt=Decimal("0"))
    total_cost: PaperDecimal = Field(..., ge=Decimal("0"))

    @model_validator(mode="after")
    def average_matches_authoritative_cost(self) -> "PositionLot":
        expected_average = _average_projection(self.qty, self.total_cost)
        if self.avg_entry_price != expected_average:
            raise ValueError(
                "avg_entry_price must equal the 34-significant-digit "
                "ROUND_HALF_EVEN projection of total_cost / qty; "
                "qty and total_cost are authoritative and commissions are separate"
            )
        return self


class PaperPositionView(BaseModel):
    """Proyección no autoritativa de lotes agregados por account+symbol+strategy_ref."""

    schema_version: Literal["paper_position_view.v1"] = Field(
        default="paper_position_view.v1", frozen=True
    )
    account_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=3)
    strategy_ref: StrategyRef
    lots: Sequence[PositionLot] = Field(default_factory=list)
    mark_price: Optional[PaperDecimal] = None

    @model_validator(mode="after")
    def lots_match_view(self) -> "PaperPositionView":
        position_ids = [lot.position_id for lot in self.lots]
        if len(position_ids) != len(set(position_ids)):
            raise ValueError("position view cannot contain duplicate position_id values")
        for lot in self.lots:
            if (
                lot.strategy_ref.strategy_id != self.strategy_ref.strategy_id
                or lot.strategy_ref.strategy_version != self.strategy_ref.strategy_version
            ):
                raise ValueError("position lot strategy_ref must match its aggregate view")
        return self

    @property
    def total_qty(self) -> Decimal:
        return _exact_sum(tuple(l.qty for l in self.lots))

    @property
    def avg_entry_price(self) -> Optional[Decimal]:
        """34-significant-digit projection; summed quantity/cost remain authoritative."""
        if not self.lots or self.total_qty == Decimal("0"):
            return None
        total_cost = _exact_sum(tuple(l.total_cost for l in self.lots))
        return _average_projection(self.total_qty, total_cost)

    @property
    def is_open(self) -> bool:
        return self.total_qty > Decimal("0")

    @property
    def unrealized_pnl(self) -> Optional[Decimal]:
        """Gross unrealized P&L from exact mark value less gross cost; fees excluded."""
        if self.mark_price is None or not self.is_open:
            return None
        market_value = _exact_product(self.mark_price, self.total_qty)
        total_cost = _exact_sum(tuple(l.total_cost for l in self.lots))
        return _exact_sum((market_value, total_cost.copy_negate()))


# ── PaperBalanceView / AssetBalance ───────────────────────────────────────────

class AssetBalance(BaseModel):
    """Saldo de un asset individual."""

    asset: str = Field(..., min_length=1)
    total: PaperDecimal = Field(..., ge=Decimal("0"))
    available: PaperDecimal = Field(..., ge=Decimal("0"))
    reserved: PaperDecimal = Field(..., ge=Decimal("0"))

    @model_validator(mode="after")
    def total_eq_available_reserved(self) -> "AssetBalance":
        if self.total != self.available + self.reserved:
            raise ValueError(
                f"total ({self.total}) must equal available + reserved ({self.available} + {self.reserved})"
            )
        return self


class _PaperAccountOpeningAssetBalance(AssetBalance):
    """Frozen nested copy; AssetBalance's shared mutability stays unchanged."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class PaperAccountOpening(BaseModel):
    """Explicit, immutable opening snapshot for one PAPER account.

    ``account_id`` is account-scoped, not session-scoped. Every asset amount is
    supplied explicitly; opening balances cannot include reservations.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["paper_account_opening.v1"] = Field(
        default="paper_account_opening.v1", frozen=True
    )
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    account_id: str = Field(..., strict=True, min_length=1)
    balances: tuple[_PaperAccountOpeningAssetBalance, ...] = Field(
        ..., min_length=1
    )

    @field_validator("account_id")
    @classmethod
    def account_id_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("account_id must not be blank")
        return value

    @field_validator("balances", mode="before")
    @classmethod
    def copy_asset_balances(cls, values):
        if isinstance(values, (tuple, list)):
            return tuple(
                balance.model_dump(mode="python")
                if isinstance(balance, AssetBalance)
                else balance
                for balance in values
            )
        return values

    @field_validator("balances")
    @classmethod
    def opening_balances_are_explicit_and_canonical(
        cls, values: tuple[_PaperAccountOpeningAssetBalance, ...]
    ) -> tuple[_PaperAccountOpeningAssetBalance, ...]:
        assets = [balance.asset for balance in values]
        if len(assets) != len(set(assets)):
            raise ValueError("PaperAccountOpening cannot contain duplicate assets")
        if any(balance.reserved != Decimal("0") for balance in values):
            raise ValueError("PaperAccountOpening must not include reserved balances")
        return tuple(sorted(values, key=lambda balance: balance.asset))


class PaperBalanceView(BaseModel):
    """Vista de balances paper. Ledger ACT-P-002 es única autoridad."""

    schema_version: Literal["paper_balance_view.v1"] = Field(
        default="paper_balance_view.v1", frozen=True
    )
    account_id: str = Field(..., min_length=1)
    balances: Sequence[AssetBalance] = Field(default_factory=list)

    @model_validator(mode="after")
    def assets_unique(self) -> "PaperBalanceView":
        assets = [balance.asset for balance in self.balances]
        if len(assets) != len(set(assets)):
            raise ValueError("PaperBalanceView cannot contain duplicate asset balances")
        return self


# ── UnattributedInventoryView ─────────────────────────────────────────────────

class UnattributedInventory(BaseModel):
    """Inventario base sin ``strategy_ref``."""

    asset: str = Field(..., min_length=1)
    qty: PaperDecimal = Field(..., ge=Decimal("0"))


class UnattributedInventoryView(BaseModel):
    schema_version: Literal["unattributed_inventory_view.v1"] = Field(
        default="unattributed_inventory_view.v1", frozen=True
    )
    account_id: str = Field(..., min_length=1)
    items: Sequence[UnattributedInventory] = Field(default_factory=list)


class PaperInventoryReconciliation(BaseModel):
    """Snapshot contract checking attributed/unattributed base inventory locally.

    This DTO compares supplied views; it is not a ledger or an accounting engine.
    """

    account_id: str = Field(..., min_length=1)
    balance: AssetBalance
    positions: Sequence[PaperPositionView] = Field(default_factory=list)
    unattributed_inventory: Sequence[UnattributedInventory] = Field(default_factory=list)

    @model_validator(mode="after")
    def inventory_matches_balance(self) -> "PaperInventoryReconciliation":
        asset = self.balance.asset
        attributed_qty = Decimal("0")
        for position in self.positions:
            if position.account_id != self.account_id:
                raise ValueError("position account_id must match reconciliation account_id")
            parts = position.symbol.split("/")
            if len(parts) != 2 or not all(parts):
                raise ValueError("position symbol must use BASE/QUOTE form")
            if parts[0] != asset:
                raise ValueError("position base asset must match reconciled balance asset")
            attributed_qty += position.total_qty

        unattributed_qty = Decimal("0")
        for item in self.unattributed_inventory:
            if item.asset != asset:
                raise ValueError("unattributed inventory asset must match balance asset")
            unattributed_qty += item.qty

        if attributed_qty + unattributed_qty != self.balance.total:
            raise ValueError(
                "ledger base balance must equal attributed open lots plus unattributed inventory"
            )
        return self


# ── PaperReservationRef / PaperProtectionGroup (OCO) ─────────────────────────

class ReservationAmount(BaseModel):
    """Cantidad reservada de un asset."""

    asset: str = Field(..., min_length=1)
    amount: PaperDecimal = Field(..., ge=Decimal("0"))


class PaperReservationRef(BaseModel):
    """Reserva que identifica asset/amount y el orden/grupo dueño.

    Nunca excede ``available`` del balance correspondiente.
    La reserva OCO es por lote y compartida (no se suma por orden).
    """

    schema_version: Literal["paper_reservation_ref.v1"] = Field(
        default="paper_reservation_ref.v1", frozen=True
    )
    reservation_id: str = Field(..., min_length=1)
    owner_order_id: Optional[str] = None
    owner_group_id: Optional[str] = None
    amount: ReservationAmount = Field(...)

    @model_validator(mode="after")
    def has_single_owner(self) -> "PaperReservationRef":
        if bool(self.owner_order_id) == bool(self.owner_group_id):
            raise ValueError("reservation must have exactly one owner: order or OCO group")
        if self.amount.amount <= Decimal("0"):
            raise ValueError("committed reservation amount must be positive")
        return self


class PaperReservationRequest(BaseModel):
    """Pure reservation precondition; persistence must recheck atomically."""

    reservation_id: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    paper_session_id: str = Field(..., min_length=1)
    environment: Literal["paper"] = Field(default="paper", frozen=True)
    owner_order_id: Optional[str] = None
    owner_group_id: Optional[str] = None
    amount: ReservationAmount
    available_before: ReservationAmount

    @model_validator(mode="after")
    def validate_precondition(self) -> "PaperReservationRequest":
        if bool(self.owner_order_id) == bool(self.owner_group_id):
            raise ValueError("reservation request must have exactly one owner")
        if self.amount.asset != self.available_before.asset:
            raise ValueError("reservation and available_before must use the same asset")
        if self.amount.amount <= Decimal("0"):
            raise ValueError("new reservation amount must be positive")
        if self.amount.amount > self.available_before.amount:
            raise ValueError("new reservation cannot exceed previous available balance")
        return self


class PaperProtectionGroup(BaseModel):
    """Grupo OCO que enlaza stop/take a un ``position_id``.

    - Reserva compartida una sola vez, nunca suma duplicada.
    - Fill parcial reduce remaining_protected_qty del grupo y de ambos miembros.
    - Fill total (remaining=0) cancela el sibling.
    - Cancelar un miembro no cancela el otro ni libera la reserva que ese miembro aún necesita.
    - Cancelar el grupo cancela remanentes de ambos y libera solo reserva no consumida.
    """

    schema_version: Literal["paper_protection_group.v1"] = Field(
        default="paper_protection_group.v1", frozen=True
    )
    protection_group_id: str = Field(..., min_length=1)
    position_id: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=3)
    open_position_qty: PaperDecimal = Field(..., ge=Decimal("0"))
    stop_order_id: Optional[str] = None
    take_order_id: Optional[str] = None
    reserved_amount: ReservationAmount = Field(...)
    remaining_protected_qty: PaperDecimal = Field(..., ge=Decimal("0"))

    @model_validator(mode="after")
    def validate_shared_protection(self) -> "PaperProtectionGroup":
        """OCO owns one base-asset reservation; cancellation/partials are snapshots."""
        symbol_parts = self.symbol.split("/")
        if len(symbol_parts) != 2 or not all(symbol_parts):
            raise ValueError("protection symbol must use BASE/QUOTE form")
        if self.reserved_amount.asset != symbol_parts[0]:
            raise ValueError("OCO reservation asset must be the symbol base asset")

        has_active_member = bool(self.stop_order_id or self.take_order_id)
        if has_active_member:
            if self.open_position_qty <= Decimal("0"):
                raise ValueError("active protection requires an open position")
            if self.remaining_protected_qty != self.open_position_qty:
                raise ValueError("active OCO protection must match remaining open position qty")
            if self.reserved_amount.amount != self.remaining_protected_qty:
                raise ValueError("OCO shared reservation must equal remaining protected qty")
        elif (
            self.remaining_protected_qty != Decimal("0")
            or self.reserved_amount.amount != Decimal("0")
        ):
            raise ValueError("cancelled/completed OCO group must release its unconsumed reserve")

        if self.stop_order_id and self.stop_order_id == self.take_order_id:
            raise ValueError("OCO stop and take members must be distinct orders")
        return self
