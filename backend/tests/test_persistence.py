import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import pytest

from analyst.persistence import Database, SchemaMigrationError
from analyst.schemas import (
    Account,
    ArtifactReference,
    Position,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
)


def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=datetime(2025, 1, 1).date(),
            reporting_currency="CAD",
            accounts=[Account(id="acc-1", name="Tax-Free")],
            positions=[
                Position(
                    id="pos-1",
                    account_id="acc-1",
                    kind="stock",
                    currency="CAD",
                    ticker="RY",
                    listing="XTSE",
                    shares=Decimal("50"),
                )
            ],
            fx=[],
        ),
        average_costs={"pos-1": Decimal("100.50")},
        unresolved=[
            UnresolvedHolding(
                account_id="acc-1",
                ticker="SHOP.TO",
                shares=Decimal("10"),
                average_cost=Decimal("95.50"),
                currency="CAD",
            )
        ],
    )



def test_database_initialization_and_reopen(tmp_path: Path):
    db_path = tmp_path / "test.db"
    db = Database(db_path)
    db.initialize()

    # Reopening is idempotent
    db2 = Database(db_path)
    db2.initialize()

    assert db2.get_schema_version() == 1


def test_database_path_injection_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PORTFOLIO_DIR", str(tmp_path))
    db = Database()
    assert db.path == tmp_path / "portfolio.db"


def test_immutable_artifact_storage_and_projections(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.initialize()

    portfolio = sample_portfolio()
    ref = ArtifactReference(
        id="art-101",
        kind="portfolio_snapshot",
        label="Test Snapshot",
        created_at=datetime.now(UTC),
        revision=1,
        basis="committed",
    )

    db.store_artifact(ref, portfolio)

    # Storing again with same ID must fail or be idempotent if identical
    with pytest.raises(sqlite3.IntegrityError):
        db.store_artifact(ref, portfolio)

    # Retrieval as human projection (default) preserves average costs
    retrieved_human = db.get_artifact("art-101", projection="human")
    assert retrieved_human is not None
    assert retrieved_human["average_costs"] == {"pos-1": "100.50"}
    assert retrieved_human["unresolved"][0]["average_cost"] == "95.50"

    # Retrieval as model projection strips average costs from both positions and unresolved holdings
    retrieved_model = db.get_artifact("art-101", projection="model")
    assert retrieved_model is not None
    assert retrieved_model["average_costs"] == {}
    assert retrieved_model["unresolved"][0]["average_cost"] is None

    # Reference retrieval
    stored_ref = db.get_artifact_reference("art-101")
    assert stored_ref is not None
    assert stored_ref.id == "art-101"
    assert stored_ref.label == "Test Snapshot"
    assert stored_ref.basis == "committed"
