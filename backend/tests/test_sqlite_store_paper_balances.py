"""Tests for explicit, account-scoped PAPER balance openings (current schema v12)."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import AssetBalance, PaperAccountOpening, PaperFill
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperAccountOpeningConflictError,
    PaperAccountOpeningIntegrityError,
    SQLiteStore,
    _SCHEMA_V6_TABLES,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V16_TABLES,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _balance(asset: str, amount: Decimal | str) -> AssetBalance:
    value = Decimal(amount) if isinstance(amount, str) else amount
    return AssetBalance(asset=asset, total=value, available=value, reserved=Decimal("0"))


def _opening(
    account_id: str = "paper-account-1",
    balances: tuple[AssetBalance, ...] | None = None,
) -> PaperAccountOpening:
    entries = balances or (_balance("USD", Decimal("100.00")),)
    return PaperAccountOpening(account_id=account_id, balances=entries)


def _table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def test_open_explicit_account_balances_and_load_after_reopen(tmp_path: Path) -> None:
    db = tmp_path / "paper-open.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = _opening(
        balances=(
            _balance("USD", Decimal("100.00")),
            _balance("BTC", Decimal("0")),
        )
    )

    saved = _run(store.open_paper_account(opening))
    assert saved == opening
    _run(store.close())

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    loaded_opening = _run(reopened.load_paper_account_opening("paper-account-1"))
    loaded_balances = _run(reopened.load_paper_balance_view("paper-account-1"))
    assert loaded_opening == opening
    assert loaded_balances is not None
    assert [(item.asset, item.total) for item in loaded_balances.balances] == [
        ("BTC", Decimal("0")),
        ("USD", Decimal("100.00")),
    ]
    _run(reopened.close())


def test_same_opening_is_idempotent_and_does_not_reset_current_balances(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-opening-idempotent.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = _opening()
    _run(store.open_paper_account(opening))

    # Simulate a later accounting projection update; retries compare to the
    # immutable opening record, not this mutable current-balance row.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_account_balances SET balance_json = ? "
        "WHERE account_id = ? AND asset = ?",
        (
            _balance("USD", Decimal("75.00")).model_dump_json(),
            "paper-account-1",
            "USD",
        ),
    )
    conn.commit()
    conn.close()

    assert _run(store.open_paper_account(opening)) == opening
    current = _run(store.load_paper_balance_view("paper-account-1"))
    assert current is not None
    assert current.balances[0].total == Decimal("75.00")
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_openings WHERE account_id = ?",
        ("paper-account-1",),
    ).fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_different_opening_conflicts_without_reinitializing(tmp_path: Path) -> None:
    db = tmp_path / "paper-opening-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    original = _opening()
    _run(store.open_paper_account(original))

    changed = _opening(balances=(_balance("USD", Decimal("200.00")),))
    with pytest.raises(PaperAccountOpeningConflictError):
        _run(store.open_paper_account(changed))

    assert _run(store.load_paper_account_opening("paper-account-1")) == original
    current = _run(store.load_paper_balance_view("paper-account-1"))
    assert current is not None and current.balances[0].total == Decimal("100.00")
    _run(store.close())


def test_open_revalidates_untrusted_nested_balance_before_any_insert(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-opening-invalid.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    invalid_balance = AssetBalance.model_construct(
        asset="USD",
        total=Decimal("-1"),
        available=Decimal("-1"),
        reserved=Decimal("0"),
    )
    invalid_opening = PaperAccountOpening.model_construct(
        schema_version="paper_account_opening.v1",
        environment="paper",
        account_id="paper-invalid",
        balances=(invalid_balance,),
    )

    with pytest.raises(ValidationError):
        _run(store.open_paper_account(invalid_opening))

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_openings WHERE account_id = ?",
        ("paper-invalid",),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_balances WHERE account_id = ?",
        ("paper-invalid",),
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_load_opening_rejects_corrupt_json_and_key_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "paper-opening-integrity.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.open_paper_account(_opening()))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_account_openings SET opening_json = ? WHERE account_id = ?",
        ("not-json", "paper-account-1"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperAccountOpeningIntegrityError, match="invalid JSON"):
        _run(store.load_paper_account_opening("paper-account-1"))

    conn = sqlite3.connect(str(db))
    mismatched = _opening(account_id="different-account").model_dump_json()
    conn.execute(
        "UPDATE paper_account_openings SET opening_json = ? WHERE account_id = ?",
        (mismatched, "paper-account-1"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperAccountOpeningIntegrityError, match="indexed account_id"):
        _run(store.load_paper_account_opening("paper-account-1"))
    _run(store.close())


def test_load_balance_rejects_json_asset_key_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "paper-balance-integrity.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.open_paper_account(_opening()))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_account_balances SET balance_json = ? "
        "WHERE account_id = ? AND asset = ?",
        (_balance("EUR", Decimal("100.00")).model_dump_json(), "paper-account-1", "USD"),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperAccountOpeningIntegrityError, match="indexed asset"):
        _run(store.load_paper_balance_view("paper-account-1"))
    _run(store.close())


def test_concurrent_openings_across_store_instances_are_idempotent_or_conflict(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-opening-concurrent.db"
    store_a = SQLiteStore(db)
    store_b = SQLiteStore(db)
    _run(store_a.initialize())
    _run(store_b.initialize())
    same = _opening()

    async def _open_same():
        return await asyncio.gather(
            store_a.open_paper_account(same),
            store_b.open_paper_account(same),
            return_exceptions=True,
        )

    same_results = _run(_open_same())
    assert same_results == [same, same]

    different_a = _opening(
        account_id="paper-account-race",
        balances=(_balance("USD", Decimal("10")),),
    )
    different_b = _opening(
        account_id="paper-account-race",
        balances=(_balance("USD", Decimal("20")),),
    )

    async def _open_different():
        return await asyncio.gather(
            store_a.open_paper_account(different_a),
            store_b.open_paper_account(different_b),
            return_exceptions=True,
        )

    different_results = _run(_open_different())
    assert sum(isinstance(result, PaperAccountOpening) for result in different_results) == 1
    assert sum(
        isinstance(result, PaperAccountOpeningConflictError)
        for result in different_results
    ) == 1

    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_openings WHERE account_id = ?",
        ("paper-account-race",),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_balances WHERE account_id = ?",
        ("paper-account-race",),
    ).fetchone()[0] == 1
    conn.close()
    original = _run(store_a.load_paper_account_opening("paper-account-race"))
    assert original in (different_a, different_b)
    _run(store_a.close())
    _run(store_b.close())


def test_multi_asset_opening_rolls_back_all_rows_on_mid_write_failure(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-opening-rollback.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TRIGGER fail_usd_opening_balance
        BEFORE INSERT ON paper_account_balances
        WHEN NEW.asset = 'USD'
        BEGIN
            SELECT RAISE(ABORT, 'injected balance insert failure');
        END;
    """)
    conn.close()
    opening = _opening(
        balances=(
            _balance("USD", Decimal("10")),
            _balance("BTC", Decimal("1")),
        )
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected balance insert failure"):
        _run(store.open_paper_account(opening))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def _make_v6_database(db: Path) -> None:
    """Downgrade a fresh v12 database to the exact historical v6 table set."""
    store = SQLiteStore(db)
    _run(store.initialize())
    fill = PaperFill(
        fill_id="historical-fill",
        order_id="opaque-order",
        qty=Decimal("1.0"),
        price=Decimal("10.00"),
        timestamp_ms=1,
        commission=None,
    )
    _run(store.save_paper_fill(fill))
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE paper_fill_evaluations")
    conn.execute("DROP TABLE paper_reservations")
    conn.execute("DROP TABLE paper_order_acceptances")
    conn.execute("DROP TABLE paper_orders")
    conn.execute("DROP TABLE paper_account_balances")
    conn.execute("DROP TABLE paper_account_openings")
    conn.execute("DROP TABLE paper_fill_applications")
    conn.execute("DROP TABLE position_lots")
    conn.execute("DROP TABLE IF EXISTS paper_reservation_lot_allocations")
    conn.execute("DROP TABLE IF EXISTS paper_sell_accounting")
    conn.execute("DROP TABLE IF EXISTS paper_lifecycle_reconciliations")
    conn.execute("DROP TABLE IF EXISTS paper_lifecycle_states")
    conn.execute("DROP TABLE IF EXISTS paper_lifecycle_events")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    # The v6 fixture is carved out of a v16 database; ui_preferences is
    # pre-existing and must survive the failed migration rollback.
    assert _table_names(db) == _SCHEMA_V6_TABLES | {"ui_preferences"}
    conn.close()


def test_v6_to_v12_migration_preserves_paper_fill(tmp_path: Path) -> None:
    db = tmp_path / "v6-to-v12.db"
    _make_v6_database(db)

    store = SQLiteStore(db)
    _run(store.initialize())
    assert _table_names(db) == _SCHEMA_V16_TABLES
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 16
    conn.close()
    assert _run(store.load_paper_fill("historical-fill")) is not None
    _run(store.close())


def test_v6_to_v12_migration_rolls_back_all_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "v6-to-v12-rollback.db"
    _make_v6_database(db)
    original_ddl = sqlite_store_module._PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_ACCOUNT_BALANCE_DDL_STATEMENTS",
        (*original_ddl[:1], "THIS IS INVALID SQL"),
    )

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    assert _table_names(db) == _SCHEMA_V6_TABLES | {"ui_preferences"}
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    row = conn.execute(
        "SELECT record_json FROM paper_fills WHERE fill_id = ?",
        ("historical-fill",),
    ).fetchone()
    assert row is not None
    assert PaperFill.model_validate_json(row[0]).fill_id == "historical-fill"
    conn.close()
    _run(store.close())
