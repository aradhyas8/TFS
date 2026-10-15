from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from analyst.persistence import Database, LegacyMigrationError
from analyst.schemas import (
    Account,
    BenchmarkSetting,
    PortfolioSettings,
    Position,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
)


def sample_portfolio_json() -> str:
    portfolio = SavedPortfolio(
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
        settings=PortfolioSettings(single_company_cap=Decimal("0.15")),
        benchmark=BenchmarkSetting(ticker="XEQT", listing="XTSE", currency="CAD", name="iShares Core Equity ETF"),
        unresolved=[
            UnresolvedHolding(
                account_id="acc-1",
                ticker="SHOP.TO",
                shares=Decimal("10"),
                average_cost=Decimal("95.50"),
                currency="CAD",
            )
        ],
        saved_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
    )
    return portfolio.model_dump_json(indent=2)


def test_valid_legacy_migration_preserves_all_fields_and_dates(tmp_path: Path):
    db_path = tmp_path / "portfolio.db"
    json_path = tmp_path / "current.json"
    json_path.write_text(sample_portfolio_json(), encoding="utf-8")

    db = Database(db_path)
    db.initialize()
    db.migrate_legacy_portfolio(json_path)

    current = db.get_current_portfolio()
    assert current is not None
    assert current.revision == 1
    assert current.portfolio.snapshot.as_of == datetime(2025, 1, 1).date()
    assert current.portfolio.saved_at == datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert current.portfolio.average_costs == {"pos-1": Decimal("100.50")}
    assert current.portfolio.settings is not None
    assert current.portfolio.settings.single_company_cap == Decimal("0.15")
    assert current.portfolio.benchmark is not None
    assert current.portfolio.benchmark.ticker == "XEQT"
    assert current.portfolio.unresolved[0].average_cost == Decimal("95.50")

    # Original JSON remains completely intact
    assert json_path.exists()
    assert json_path.read_text(encoding="utf-8") == sample_portfolio_json()

    # Re-running migration does not reimport or increment revision
    db.migrate_legacy_portfolio(json_path)
    current_recheck = db.get_current_portfolio()
    assert current_recheck is not None
    assert current_recheck.revision == 1


def test_empty_migration_when_no_legacy_json(tmp_path: Path):
    db_path = tmp_path / "portfolio.db"
    json_path = tmp_path / "current.json"
    assert not json_path.exists()

    db = Database(db_path)
    db.initialize()
    db.migrate_legacy_portfolio(json_path)

    current = db.get_current_portfolio()
    assert current is None

    # Migration marker is recorded even without legacy file
    assert db.is_legacy_migration_complete()


def test_invalid_legacy_json_stops_initialization_with_error(tmp_path: Path):
    db_path = tmp_path / "portfolio.db"
    json_path = tmp_path / "current.json"
    json_path.write_text('{"bad": "data"}', encoding="utf-8")

    db = Database(db_path)
    db.initialize()

    with pytest.raises(LegacyMigrationError):
        db.migrate_legacy_portfolio(json_path)

    # Database does not silently start empty and migration is not marked complete
    assert not db.is_legacy_migration_complete()
    assert db.get_current_portfolio() is None


def test_api_get_returns_committed_revision_without_enrichment_or_writes(tmp_path: Path):
    from fastapi.testclient import TestClient

    from analyst.api import create_app
    from analyst.portfolio import PortfolioStore

    json_path = tmp_path / "current.json"
    json_path.write_text(sample_portfolio_json(), encoding="utf-8")

    store = PortfolioStore(tmp_path)
    client = TestClient(create_app(portfolio_store=store))

    response = client.get("/api/portfolio")
    assert response.status_code == 200
    data = response.json()
    assert data is not None
    assert data["revision"] == 1
    assert data["portfolio"]["snapshot"]["reporting_currency"] == "CAD"
    assert data["portfolio"]["settings"]["single_company_cap"] == "0.15"
    assert data["portfolio"]["benchmark"]["ticker"] == "XEQT"

    # Reopening with new store/client reads same committed revision
    store2 = PortfolioStore(tmp_path)
    client2 = TestClient(create_app(portfolio_store=store2))
    response2 = client2.get("/api/portfolio")
    assert response2.status_code == 200
    assert response2.json()["revision"] == 1

    # Stored artifact art-rev-1 is created by legacy migration
    art_response = client2.get("/api/artifacts/art-rev-1")
    assert art_response.status_code == 200
    assert art_response.json()["reference"]["revision"] == 1
    assert art_response.json()["reference"]["basis"] == "committed"


def test_concurrent_initialization_does_not_create_duplicate_initial_revisions(tmp_path: Path):
    import concurrent.futures

    db_path = tmp_path / "portfolio.db"
    json_path = tmp_path / "current.json"
    json_path.write_text(sample_portfolio_json(), encoding="utf-8")

    def init_and_migrate():
        db = Database(db_path)
        db.initialize()
        db.migrate_legacy_portfolio(json_path)
        return db.get_current_portfolio()

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(init_and_migrate) for _ in range(5)]
        results = [f.result() for f in futures]

    revisions = [r.revision for r in results if r is not None]
    assert len(revisions) == 5
    assert all(rev == 1 for rev in revisions)

