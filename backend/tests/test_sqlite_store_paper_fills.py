"""Tests for PaperFill persistence in SQLiteStore (current schema v12).

Covers:
  - Round-trip Decimal fidelity via _paper_decimal validator.
  - Commission(amount="0") vs None distinction.
  - fill_id global uniqueness and idempotent no-op on duplicate.
  - Conflict detection on divergent content for same fill_id.
  - JSON-to-key identity validation on load (corruption / mismatch).
  - v5 → current schema migration: DDL creation, version bump, table presence.
  - Reopen without duplicate migration execution.
  - Migration rollback on injected DDL failure.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import (
    Commission,
    LotAllocation,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    StrategyRef,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperFillConflictError,
    PaperFillIntegrityError,
    FillApplyCommitError,
    SchemaVersionMismatchError,
    SQLiteStore,
    StoreClosedError,
    StoreCorruptionError,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V18_TABLES,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _run(coro: Any) -> Any:
    """Run a coroutine in the current event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_paper_fill(
    fill_id: str = "fill-1",
    order_id: str = "order-1",
    qty: Decimal | str = Decimal("1.5"),
    price: Decimal | str = Decimal("100.25"),
    timestamp_ms: int = 1_700_000_000_000,
    commission: Commission | None = None,
    lot_allocations: tuple[LotAllocation, ...] = (),
) -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        qty=qty,
        price=price,
        timestamp_ms=timestamp_ms,
        commission=commission,
        lot_allocations=lot_allocations,
    )


def _make_commission(amount: Decimal | str = Decimal("0.5"), asset: str = "USD") -> Commission:
    return Commission(amount=amount, asset=asset)


# ── 1. Round-trip Decimal fidelity ─────────────────────────────────────────────


def test_roundtrip_decimal_qty_price(tmp_path: Path) -> None:
    """Decimal inputs/outputs are preserved exactly through JSON round-trip."""
    db = tmp_path / "decimal.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    qty = Decimal("0.123456789012345678")
    price = Decimal("999.9999999999999999")
    fill = _make_paper_fill(qty=qty, price=price)
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == qty
    assert loaded.price == price
    assert isinstance(loaded.qty, Decimal)
    assert isinstance(loaded.price, Decimal)
    _run(store.close())


def test_roundtrip_decimal_string_input(tmp_path: Path) -> None:
    """String Decimal inputs are accepted and preserved exactly."""
    db = tmp_path / "decimal-str.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill = _make_paper_fill(qty="1.00", price="50.00")
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == Decimal("1.00")
    assert loaded.price == Decimal("50.00")
    _run(store.close())


def test_decimal_float_rejected(tmp_path: Path) -> None:
    """Float inputs are rejected by _paper_decimal validator."""
    with pytest.raises(ValidationError, match="amounts must be Decimal"):
        PaperFill(
            fill_id="fill-float",
            order_id="order-1",
            qty=1.5,  # type: ignore
            price=Decimal("100"),
            timestamp_ms=1_000,
            commission=None,
        )


def test_save_revalidates_unvalidated_fill_and_does_not_insert(tmp_path: Path) -> None:
    """A model_construct PaperFill with invalid quantity cannot enter storage."""
    db = tmp_path / "invalid-fill.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    invalid = PaperFill.model_construct(
        schema_version="paper_fill.v1",
        fill_id="fill-invalid-qty",
        order_id="order-unresolved",
        qty=Decimal("-1"),
        price=Decimal("1"),
        timestamp_ms=0,
        commission=None,
        lot_allocations=(),
    )
    with pytest.raises(ValidationError):
        _run(store.save_paper_fill(invalid))

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id = ?",
        ("fill-invalid-qty",),
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_save_revalidates_nested_fill_content_and_does_not_insert(
    tmp_path: Path,
) -> None:
    """Nested invalid LotAllocation content is revalidated before writing."""
    db = tmp_path / "invalid-nested-fill.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    invalid_allocation = LotAllocation.model_construct(
        position_id="position-1",
        account_id="account-1",
        symbol="BTC/USD",
        strategy_ref=StrategyRef(strategy_id="strategy-1", strategy_version="v1"),
        qty=Decimal("-0.25"),
    )
    invalid = PaperFill.model_construct(
        schema_version="paper_fill.v1",
        fill_id="fill-invalid-allocation",
        order_id="order-unresolved",
        qty=Decimal("1"),
        price=Decimal("1"),
        timestamp_ms=0,
        commission=None,
        lot_allocations=(invalid_allocation,),
    )
    with pytest.raises(ValidationError):
        _run(store.save_paper_fill(invalid))

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id = ?",
        ("fill-invalid-allocation",),
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


# ── 2. Commission(amount="0") vs None distinction ─────────────────────────────


def test_commission_zero_vs_none(tmp_path: Path) -> None:
    """Commission(amount='0') is distinct from None; both round-trip correctly."""
    db = tmp_path / "commission.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # With zero commission.
    fill_zero = _make_paper_fill(commission=Commission(amount="0", asset="USD"))
    _run(store.save_paper_fill(fill_zero))
    loaded_zero = _run(store.load_paper_fill("fill-1"))
    assert loaded_zero is not None
    assert loaded_zero.commission is not None
    assert loaded_zero.commission.amount == Decimal("0")

    # With None commission.
    fill_none = _make_paper_fill(fill_id="fill-2", commission=None)
    _run(store.save_paper_fill(fill_none))
    loaded_none = _run(store.load_paper_fill("fill-2"))
    assert loaded_none is not None
    assert loaded_none.commission is None

    # They are different in JSON.
    json_zero = fill_zero.model_dump_json()
    json_none = fill_none.model_dump_json()
    assert json_zero != json_none
    _run(store.close())


# ── 3. fill_id uniqueness and idempotent no-op ─────────────────────────────────


def test_fill_id_uniqueness(tmp_path: Path) -> None:
    """Different fill_ids are stored independently."""
    db = tmp_path / "unique.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    for i in range(5):
        fill = _make_paper_fill(fill_id=f"fill-{i}", qty=Decimal(str(i + 1)))
        _run(store.save_paper_fill(fill))

    for i in range(5):
        loaded = _run(store.load_paper_fill(f"fill-{i}"))
        assert loaded is not None
        assert loaded.qty == Decimal(str(i + 1))
    _run(store.close())


def test_idempotent_same_content_no_error(tmp_path: Path) -> None:
    """Identical fill_id+payload is an idempotent no-op — no error, no duplicate."""
    db = tmp_path / "idem.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill = _make_paper_fill(qty="2.0", price="50.0")
    _run(store.save_paper_fill(fill))
    # Exact retry after commit is idempotent and does not duplicate.
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == Decimal("2.0")

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
    assert count == 1
    conn.close()
    _run(store.close())


def test_conflict_different_content_same_fill_id(tmp_path: Path) -> None:
    """Divergent content for same fill_id raises PaperFillConflictError."""
    db = tmp_path / "conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill1 = _make_paper_fill(qty="2.0", price="50.0")
    _run(store.save_paper_fill(fill1))

    fill2 = _make_paper_fill(fill_id="fill-1", qty="3.0", price="50.0")
    with pytest.raises(PaperFillConflictError, match="different content"):
        _run(store.save_paper_fill(fill2))

    # Original is preserved.
    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == Decimal("2.0")
    _run(store.close())


def test_conflict_different_commission_same_fill_id(tmp_path: Path) -> None:
    """Different commission for same fill_id raises conflict."""
    db = tmp_path / "conflict-comm.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill1 = _make_paper_fill(commission=Commission(amount="0", asset="USD"))
    _run(store.save_paper_fill(fill1))

    fill2 = _make_paper_fill(commission=Commission(amount="1", asset="USD"))
    with pytest.raises(PaperFillConflictError, match="different content"):
        _run(store.save_paper_fill(fill2))
    _run(store.close())


def test_concurrent_save_same_fill_id_is_idempotent_or_conflicts_without_overwrite(
    tmp_path: Path,
) -> None:
    """Two store instances serialize same-ID PaperFill writes transactionally."""
    db = tmp_path / "concurrent-same-fill.db"
    store_a = SQLiteStore(db)
    store_b = SQLiteStore(db)
    _run(store_a.initialize())
    _run(store_b.initialize())

    identical = _make_paper_fill(
        fill_id="fill-identical-race", qty=Decimal("1.00"), price=Decimal("2.00")
    )

    async def _save_identical_concurrently():
        return await asyncio.gather(
            store_a.save_paper_fill(identical),
            store_b.save_paper_fill(identical),
            return_exceptions=True,
        )

    identical_results = _run(_save_identical_concurrently())
    assert identical_results == [None, None]

    divergent_a = _make_paper_fill(
        fill_id="fill-conflicting-race", qty=Decimal("1.00"), price=Decimal("2.00")
    )
    divergent_b = _make_paper_fill(
        fill_id="fill-conflicting-race", qty=Decimal("2.00"), price=Decimal("2.00")
    )

    async def _save_divergent_concurrently():
        return await asyncio.gather(
            store_a.save_paper_fill(divergent_a),
            store_b.save_paper_fill(divergent_b),
            return_exceptions=True,
        )

    divergent_results = _run(_save_divergent_concurrently())
    assert sum(result is None for result in divergent_results) == 1
    assert sum(
        isinstance(result, PaperFillConflictError) for result in divergent_results
    ) == 1

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id = ?",
        ("fill-identical-race",),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id = ?",
        ("fill-conflicting-race",),
    ).fetchone()[0] == 1
    conn.close()

    winning_fill = _run(store_a.load_paper_fill("fill-conflicting-race"))
    assert winning_fill in (divergent_a, divergent_b)
    _run(store_a.close())
    _run(store_b.close())


# ── 4. JSON-to-key identity validation on load ─────────────────────────────────


def test_load_corrupt_json_rejected(tmp_path: Path) -> None:
    """load_paper_fill rejects corrupt JSON with PaperFillIntegrityError."""
    db = tmp_path / "corrupt.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO paper_fills (fill_id, record_json) VALUES ('fill-corrupt', 'NOT JSON')",
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperFillIntegrityError, match="corrupt JSON"):
        _run(store.load_paper_fill("fill-corrupt"))
    _run(store.close())


def test_load_key_mismatch_rejected(tmp_path: Path) -> None:
    """load_paper_fill rejects fill JSON whose fill_id doesn't match stored column."""
    db = tmp_path / "keymismatch.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill = _make_paper_fill(fill_id="fill-mismatch")
    conn = sqlite3.connect(str(db))
    # Insert with mismatched fill_id in JSON vs column.
    mismatched = PaperFill(
        fill_id="fill-different",  # different from stored key
        order_id="order-1",
        qty="1.0",
        price="100.0",
        timestamp_ms=1_000,
        commission=None,
    ).model_dump_json()
    conn.execute(
        "INSERT INTO paper_fills (fill_id, record_json) VALUES ('fill-mismatch', ?)",
        (mismatched,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperFillIntegrityError, match="does not match indexed identity"):
        _run(store.load_paper_fill("fill-mismatch"))
    _run(store.close())


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    """load_paper_fill returns None when no fill exists for the fill_id."""
    db = tmp_path / "none.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    loaded = _run(store.load_paper_fill("nonexistent"))
    assert loaded is None
    _run(store.close())


# ── 5. v5 → current migration: DDL creation, version bump, table presence ─────


def test_v5_to_v12_migration_creates_current_tables(tmp_path: Path) -> None:
    """A v5 source migrates to current v12 with fill, balance, reserve, and fill application tables."""
    db = tmp_path / "v5-to-v12.db"

    # Build a v5 database directly.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE run_attempts (
            run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
            experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')),
            run_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id, experiment_version)
                REFERENCES experiment_definitions(experiment_id, experiment_version)
        );
        CREATE TABLE experiment_results (
            result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(run_id),
            result_json TEXT NOT NULL
        );
        CREATE TABLE variant_dispositions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            variant_id TEXT NOT NULL, record_json TEXT NOT NULL,
            PRIMARY KEY (experiment_id, experiment_version, variant_id)
        );
        CREATE TABLE result_exclusions (
            result_id TEXT PRIMARY KEY, record_json TEXT NOT NULL,
            FOREIGN KEY (result_id) REFERENCES experiment_results(result_id)
        );
        PRAGMA user_version = 5;
    """)
    conn.execute("INSERT INTO sessions (session_id) VALUES ('sess-v5')")
    conn.commit()
    conn.close()

    # Migrate to the current schema.
    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    # All historical tables and current account-opening tables are present.
    assert _SCHEMA_V18_TABLES <= tables
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()
    _run(upgraded.close())


def test_v5_to_v9_migration_preserves_all_v5_data(tmp_path: Path) -> None:
    """v5 → current migration preserves all existing v5 data."""
    db = tmp_path / "v5-data-preserve.db"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL, PRIMARY KEY (strategy_id, strategy_version));
        CREATE TABLE session_strategy_configs (session_id TEXT NOT NULL, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, PRIMARY KEY (session_id, strategy_id, strategy_version));
        CREATE TABLE session_dataset_refs (session_id TEXT NOT NULL, dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref));
        CREATE TABLE experiment_definitions (experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version));
        CREATE TABLE run_attempts (run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')), run_json TEXT NOT NULL);
        CREATE TABLE experiment_results (result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, result_json TEXT NOT NULL);
        CREATE TABLE variant_dispositions (experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version, variant_id));
        CREATE TABLE result_exclusions (result_id TEXT PRIMARY KEY, record_json TEXT NOT NULL);
        PRAGMA user_version = 5;
    """)
    conn.execute("INSERT INTO sessions VALUES ('sess-data')")
    conn.execute("INSERT INTO strategy_config_versions VALUES ('s1','v1','{}')")
    conn.execute("INSERT INTO experiment_definitions VALUES ('exp-1','v1','{}')")
    conn.execute("INSERT INTO run_attempts VALUES ('run-1','exp-1','v1','va','pending','{}')")
    conn.execute("INSERT INTO experiment_results VALUES ('res-1','run-1','{}')")
    conn.execute("INSERT INTO variant_dispositions VALUES ('exp-1','v1','va','{}')")
    conn.execute("INSERT INTO result_exclusions VALUES ('res-excl','{}')")
    conn.commit()
    conn.close()

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM strategy_config_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM variant_dispositions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM result_exclusions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


# ── 6. Reopen without duplicate migration execution ────────────────────────────


def test_reopen_v12_is_idempotent(tmp_path: Path) -> None:
    """Re-initializing a v10 database is idempotent; no error or schema change."""
    db = tmp_path / "reinit-v10.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    # Second initialize must succeed without error.
    _run(store1.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert _SCHEMA_V18_TABLES <= tables
    conn.close()
    _run(store1.close())


def test_reopen_after_close_recovers_paper_fills(tmp_path: Path) -> None:
    """After close/reopen, paper fills are recoverable."""
    db = tmp_path / "reopen-fills.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    fill = _make_paper_fill(qty="5.0", price="25.0")
    _run(store1.save_paper_fill(fill))
    _run(store1.close())

    # Reopen.
    store2 = SQLiteStore(db)
    _run(store2.initialize())

    loaded = _run(store2.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == Decimal("5.0")
    assert loaded.price == Decimal("25.0")
    _run(store2.close())


# ── 7. Migration rollback on injected DDL failure ──────────────────────────────


def test_v5_to_v6_migration_rolls_back_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DDL failure during v5 → v6 leaves schema/data/version intact."""
    db = tmp_path / "v5-to-v6-fail.db"

    # Build a v5 database.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL, PRIMARY KEY (strategy_id, strategy_version));
        CREATE TABLE session_strategy_configs (session_id TEXT NOT NULL, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, PRIMARY KEY (session_id, strategy_id, strategy_version));
        CREATE TABLE session_dataset_refs (session_id TEXT NOT NULL, dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref));
        CREATE TABLE experiment_definitions (experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version));
        CREATE TABLE run_attempts (run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')), run_json TEXT NOT NULL);
        CREATE TABLE experiment_results (result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, result_json TEXT NOT NULL);
        CREATE TABLE variant_dispositions (experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL, record_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version, variant_id));
        CREATE TABLE result_exclusions (result_id TEXT PRIMARY KEY, record_json TEXT NOT NULL);
        PRAGMA user_version = 5;
    """)
    conn.execute("INSERT INTO sessions (session_id) VALUES ('preserved-v5')")
    conn.commit()
    conn.close()

    original_v6_statements = sqlite_store_module._PAPER_FILL_DDL_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_FILL_DDL_STATEMENTS",
        (*original_v6_statements[:1], "THIS IS INVALID SQL"),
    )

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "paper_fills" not in tables
    # v5 data preserved.
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", ("preserved-v5",)).fetchone()[0] == 1
    conn.close()


# ── 8. Fresh database initializes at v9 ──────────────────────────────────────


def test_new_database_initializes_directly_at_v12(tmp_path: Path) -> None:
    """A fresh database initializes with current order-acceptance tables."""
    db = tmp_path / "fresh-v10.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert _SCHEMA_V18_TABLES <= tables
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()
    _run(store.close())


# ── 9. StoreClosedError on closed store ────────────────────────────────────────


def test_save_paper_fill_on_closed_store_raises(tmp_path: Path) -> None:
    """save_paper_fill raises StoreClosedError after close."""
    db = tmp_path / "closed.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.close())

    fill = _make_paper_fill()
    with pytest.raises(StoreClosedError, match="store is closed"):
        _run(store.save_paper_fill(fill))


def test_load_paper_fill_on_closed_store_raises(tmp_path: Path) -> None:
    """load_paper_fill raises StoreClosedError after close."""
    db = tmp_path / "closed-load.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.close())

    with pytest.raises(StoreClosedError, match="store is closed"):
        _run(store.load_paper_fill("fill-1"))


# ── 10. Unknown version rejected without schema change ─────────────────────────


def test_unknown_version_rejected_no_schema_change(tmp_path: Path) -> None:
    """An unknown schema version is rejected without modifying the database."""
    db = tmp_path / "unknown-version.db"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 99;
    """)
    conn.close()

    store = SQLiteStore(db)
    with pytest.raises(SchemaVersionMismatchError):
        _run(store.initialize())

    # Verify no changes were made.
    conn = sqlite3.connect(str(db))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == {"sessions"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


# ── 11. LotAllocation round-trip ───────────────────────────────────────────────


def test_lot_allocations_roundtrip(tmp_path: Path) -> None:
    """LotAllocations are preserved exactly through save/load."""
    db = tmp_path / "lotalloc.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    strategy_ref = StrategyRef(strategy_id="strat-1", strategy_version="v1")
    lot_allocs = (
        LotAllocation(
            position_id="pos-1",
            account_id="acc-1",
            symbol="BTC/USD",
            strategy_ref=strategy_ref,
            qty=Decimal("0.5"),
        ),
        LotAllocation(
            position_id="pos-2",
            account_id="acc-1",
            symbol="BTC/USD",
            strategy_ref=strategy_ref,
            qty=Decimal("1.0"),
        ),
    )
    fill = _make_paper_fill(fill_id="fill-lot", qty=Decimal("1.5"), lot_allocations=lot_allocs)
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-lot"))
    assert loaded is not None
    assert len(loaded.lot_allocations) == 2
    assert loaded.lot_allocations[0].position_id == "pos-1"
    assert loaded.lot_allocations[0].qty == Decimal("0.5")
    assert loaded.lot_allocations[1].position_id == "pos-2"
    assert loaded.lot_allocations[1].qty == Decimal("1.0")
    _run(store.close())


# ── 12. Empty lot_allocations default ──────────────────────────────────────────


def test_empty_lot_allocations_default(tmp_path: Path) -> None:
    """Default empty tuple for lot_allocations is preserved."""
    db = tmp_path / "empty-lot.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill = _make_paper_fill(fill_id="fill-empty-lot")
    assert fill.lot_allocations == ()
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-empty-lot"))
    assert loaded is not None
    assert loaded.lot_allocations == ()
    _run(store.close())


# ── 13. SchemaVersionMismatchError on missing tables after v12 init ─────────────


def test_v12_schema_version_mismatch_on_missing_tables(tmp_path: Path) -> None:
    """If user_version is 12 but current tables are missing, mismatch error."""
    db = tmp_path / "v12-missing-table.db"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 12;
    """)
    conn.close()

    store = SQLiteStore(db)
    with pytest.raises(
        SchemaVersionMismatchError,
        match="schema version is 12 but required tables are missing",
    ):
        _run(store.initialize())


# ── 14. PaperFill type validation ──────────────────────────────────────────────


def test_save_paper_fill_rejects_non_paperfill(tmp_path: Path) -> None:
    """save_paper_fill rejects non-PaperFill instances."""
    db = tmp_path / "type-check.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    with pytest.raises(TypeError, match="fill must be a PaperFill instance"):
        _run(store.save_paper_fill("not-a-fill"))  # type: ignore


def test_load_paper_fill_rejects_empty_fill_id(tmp_path: Path) -> None:
    """load_paper_fill rejects empty or whitespace-only fill_id."""
    db = tmp_path / "empty-id.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    with pytest.raises(ValueError, match="fill_id must be a non-empty string"):
        _run(store.load_paper_fill(""))

    with pytest.raises(ValueError, match="fill_id must be a non-empty string"):
        _run(store.load_paper_fill("   "))
    _run(store.close())


# ── 15. Direct DB manipulation for concurrency conflict simulation ──────────────


def test_conflict_detected_via_direct_db_manipulation(tmp_path: Path) -> None:
    """Direct DB manipulation with different content triggers conflict on save."""
    db = tmp_path / "direct-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill1 = _make_paper_fill(qty="1.0", price="100.0")
    _run(store.save_paper_fill(fill1))

    # Directly insert a different payload with the same fill_id.
    conn = sqlite3.connect(str(db))
    fill2_json = _make_paper_fill(fill_id="fill-1", qty="99.0", price="1.0").model_dump_json()
    conn.execute(
        "UPDATE paper_fills SET record_json = ? WHERE fill_id = ?",
        (fill2_json, "fill-1"),
    )
    conn.commit()
    conn.close()

    # Attempt to save the original again — should conflict.
    with pytest.raises(PaperFillConflictError, match="different content"):
        _run(store.save_paper_fill(fill1))
    _run(store.close())


# ── 16. Large timestamp preservation ───────────────────────────────────────────


def test_large_timestamp_preserved(tmp_path: Path) -> None:
    """Large millisecond timestamps are preserved exactly."""
    db = tmp_path / "large-ts.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    ts = 9_999_999_999_999
    fill = _make_paper_fill(timestamp_ms=ts)
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.timestamp_ms == ts
    _run(store.close())


# ── 17. Zero timestamp allowed ─────────────────────────────────────────────────


def test_zero_timestamp_allowed(tmp_path: Path) -> None:
    """timestamp_ms=0 is valid (ge=0 constraint)."""
    db = tmp_path / "zero-ts.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    fill = _make_paper_fill(timestamp_ms=0)
    _run(store.save_paper_fill(fill))

    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.timestamp_ms == 0
    _run(store.close())


# ── 18. v4 → current migration path ───────────────────────────────────────────


def test_v4_to_v12_migration_creates_current_tables(tmp_path: Path) -> None:
    """v4 → current migration creates fills, balances, orders, reservations, and fill application tables."""
    db = tmp_path / "v4-to-v12.db"

    # Build a v4 database (with experiment_results).
    store_v4 = SQLiteStore(db)
    _run(store_v4.initialize())
    _run(store_v4.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE variant_dispositions")
    conn.execute("DROP TABLE result_exclusions")
    conn.execute("DROP TABLE paper_fills")
    conn.execute("DROP TABLE paper_account_balances")
    conn.execute("DROP TABLE paper_account_openings")
    conn.execute("DROP TABLE paper_reservations")
    conn.execute("DROP TABLE paper_order_acceptances")
    conn.execute("DROP TABLE paper_orders")
    conn.execute("DROP TABLE paper_fill_applications")
    conn.execute("DROP TABLE position_lots")
    conn.execute("DROP TABLE audit_decisions")
    tables_before = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "paper_fills" not in tables_before
    assert "paper_account_openings" not in tables_before
    assert "paper_account_balances" not in tables_before
    assert "paper_orders" not in tables_before
    assert "paper_order_acceptances" not in tables_before
    assert "paper_reservations" not in tables_before
    conn.execute("INSERT INTO sessions (session_id) VALUES ('preserved-v4')")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert _SCHEMA_V18_TABLES <= tables
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", ("preserved-v4",)).fetchone()[0] == 1
    conn.close()
    _run(upgraded.close())


# ── 19. v3 → current migration path ───────────────────────────────────────────


def test_v3_to_v12_migration_preserves_all_data(tmp_path: Path) -> None:
    """v3 → current migration preserves data and creates current tables."""
    db = tmp_path / "v3-to-v12.db"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL, PRIMARY KEY (strategy_id, strategy_version));
        CREATE TABLE session_strategy_configs (session_id TEXT NOT NULL, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, PRIMARY KEY (session_id, strategy_id, strategy_version));
        CREATE TABLE session_dataset_refs (session_id TEXT NOT NULL, dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref));
        CREATE TABLE experiment_definitions (experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version));
        CREATE TABLE run_attempts (run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled')), run_json TEXT NOT NULL);
        PRAGMA user_version = 3;
    """)
    conn.execute("INSERT INTO sessions (session_id) VALUES ('sess-v3')")
    conn.execute("INSERT INTO experiment_definitions VALUES ('exp-1','v1','{}')")
    conn.execute("INSERT INTO run_attempts VALUES ('run-1','exp-1','v1','va','pending','{}')")
    conn.commit()
    conn.close()

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "paper_fills" in tables
    assert _SCHEMA_V18_TABLES <= tables
    # v3 data preserved.
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 1
    # New tables empty.
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


def _seed_fill_recovery_case(db: Path, *, with_application: bool) -> tuple[SQLiteStore, PaperFill]:
    store = SQLiteStore(db)
    _run(store.initialize())
    order = PaperOrder(
        order_id="recovery-order",
        client_order_id="recovery-client",
        paper_account_id="recovery-account",
        paper_session_id="recovery-session",
        environment="paper",
        symbol="BASE/QUOTE",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.MARKET,
        requested_qty=Decimal("2"),
        fills=(),
        lot_allocations=(),
        price=None,
        trigger_price=None,
        max_quote_amount=Decimal("300"),
        strategy_ref=StrategyRef(strategy_id="recovery-strategy", strategy_version="1"),
        decision_id=None,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_000_000,
        position_id=None,
        protection_group_id=None,
    )
    _run(store.register_paper_order(order))
    fill = _make_paper_fill(
        fill_id="recovery-fill", order_id=order.order_id, qty="1", price="100"
    )
    _run(store.save_paper_fill(fill))
    conn = sqlite3.connect(str(db))
    if with_application:
        applied_order = order.model_copy(
            update={"status": PaperOrderStatus.PARTIALLY_FILLED, "fills": (fill,)}
        )
        conn.execute(
            "UPDATE paper_orders SET order_json=? WHERE order_id=?",
            (applied_order.model_dump_json(), order.order_id),
        )
        conn.execute(
            "INSERT INTO paper_fill_applications "
            "(fill_id, order_id, account_id, paper_session_id, environment, "
            "applied_at_ms, application_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fill.fill_id,
                order.order_id,
                order.paper_account_id,
                order.paper_session_id,
                order.environment,
                fill.timestamp_ms,
                fill.model_dump_json(),
            ),
        )
    conn.commit()
    conn.close()
    return store, fill


@pytest.mark.parametrize(
    "inconsistency",
    ["journal_fill_id", "order_id", "account_id", "paper_session_id", "environment", "application_json"],
)
def test_fill_application_recovery_rejects_inconsistent_durable_identity(
    tmp_path: Path, inconsistency: str
) -> None:
    db = tmp_path / f"application-{inconsistency}.db"
    store, fill = _seed_fill_recovery_case(db, with_application=True)
    conn = sqlite3.connect(str(db))
    if inconsistency == "journal_fill_id":
        corrupt_fill = fill.model_copy(update={"fill_id": "other-fill"})
        conn.execute(
            "UPDATE paper_fills SET record_json=? WHERE fill_id=?",
            (corrupt_fill.model_dump_json(), fill.fill_id),
        )
    elif inconsistency == "application_json":
        corrupt_fill = fill.model_copy(update={"qty": Decimal("1.5")})
        conn.execute(
            "UPDATE paper_fill_applications SET application_json=? WHERE fill_id=?",
            (corrupt_fill.model_dump_json(), fill.fill_id),
        )
    else:
        if inconsistency == "environment":
            conn.execute("PRAGMA ignore_check_constraints=ON")
        conn.execute(
            f"UPDATE paper_fill_applications SET {inconsistency}=? WHERE fill_id=?",
            ("mismatched-value", fill.fill_id),
        )
    conn.commit()
    conn.close()

    with pytest.raises(StoreCorruptionError):
        _run(
            store._recover_fill_commit(
                fill.fill_id, FillApplyCommitError("injected commit failure")
            )
        )
    _run(store.close())


def test_corrupt_order_json_during_fill_recovery_is_store_corruption(tmp_path: Path) -> None:
    db = tmp_path / "corrupt-order-recovery.db"
    store, fill = _seed_fill_recovery_case(db, with_application=False)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_orders SET order_json='not-json' WHERE order_id='recovery-order'"
    )
    conn.commit()
    conn.close()

    with pytest.raises(StoreCorruptionError, match="corrupt order JSON"):
        _run(
            store._recover_fill_commit(
                fill.fill_id, FillApplyCommitError("injected commit failure")
            )
        )
    _run(store.close())


def test_inconsistent_application_rolls_back_idempotent_fill_attempt(tmp_path: Path) -> None:
    db = tmp_path / "inconsistent-application-rollback.db"
    store, fill = _seed_fill_recovery_case(db, with_application=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_fill_applications SET application_json='{}' WHERE fill_id=?",
        (fill.fill_id,),
    )
    conn.commit()
    before = conn.execute(
        "SELECT * FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()
    order_before = conn.execute(
        "SELECT order_json FROM paper_orders WHERE order_id='recovery-order'"
    ).fetchone()[0]
    conn.close()

    with pytest.raises(StoreCorruptionError, match="corrupt application JSON"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT * FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone() == before
    assert conn.execute(
        "SELECT order_json FROM paper_orders WHERE order_id='recovery-order'"
    ).fetchone()[0] == order_before
    conn.close()
    _run(store.close())
