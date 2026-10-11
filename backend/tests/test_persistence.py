import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from analyst.persistence import Database
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


def test_typed_artifact_storage_and_generic_retrieval(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.initialize()

    # 1. Store analysis_result artifact
    analysis_ref = ArtifactReference(
        id="art-analysis-1",
        kind="analysis_result",
        label="Analysis Outcome",
        created_at=datetime.now(UTC),
        revision=1,
        basis="committed",
        source_artifact_id="art-101",
        originating_run_id="run-101",
    )
    analysis_payload = {
        "status": "completed",
        "question": "Review risk",
        "notes": ["well diversified", "low debt"],
    }
    db.store_artifact(analysis_ref, analysis_payload)

    # 2. Store company_research artifact
    research_ref = ArtifactReference(
        id="art-research-1",
        kind="company_research",
        label="SEC/SEDAR Company Research",
        created_at=datetime.now(UTC),
        basis="none",
        originating_run_id="run-101",
    )
    research_payload = {
        "company_id": "SHOP",
        "sector": "technology",
        "cyclical": False,
        "facts": [{"metric": "revenue", "value": "5000000000"}],
    }
    db.store_artifact(research_ref, research_payload)

    # 3. Store calculation_result artifact
    calc_ref = ArtifactReference(
        id="art-calc-1",
        kind="calculation_result",
        label="Exposure Calculation",
        created_at=datetime.now(UTC),
        basis="committed",
        revision=1,
        originating_run_id="run-101",
    )
    calc_payload = {
        "capability": "portfolio_exposure",
        "result": {"us_equity_pct": "0.60", "cash_pct": "0.10"},
    }
    db.store_artifact(calc_ref, calc_payload)

    # 4. Store web_research artifact
    web_ref = ArtifactReference(
        id="art-web-1",
        kind="web_research",
        label="Web Search Research",
        created_at=datetime.now(UTC),
        basis="none",
        originating_run_id="run-101",
    )
    web_payload = {
        "query": "Shopify latest earnings",
        "citations": [{"url": "https://example.com", "title": "Earnings Q3"}],
        "sources": ["https://example.com"],
    }
    db.store_artifact(web_ref, web_payload)

    # Verify retrieval and non-portfolio artifacts don't get portfolio serialization
    retrieved_analysis = db.get_artifact("art-analysis-1")
    assert retrieved_analysis == analysis_payload

    # Envelope retrieval
    env_research = db.get_artifact_envelope("art-research-1")
    assert env_research is not None
    assert env_research.reference.id == "art-research-1"
    assert env_research.reference.kind == "company_research"
    assert env_research.payload == research_payload

    env_calc = db.get_artifact_envelope("art-calc-1")
    assert env_calc is not None
    assert env_calc.reference.kind == "calculation_result"
    assert env_calc.payload == calc_payload

    env_web = db.get_artifact_envelope("art-web-1")
    assert env_web is not None
    assert env_web.reference.kind == "web_research"
    assert env_web.payload == web_payload

    # Immutability: duplicate ID fails
    with pytest.raises(sqlite3.IntegrityError):
        db.store_artifact(analysis_ref, analysis_payload)


def test_restart_preserves_revisions_pending_proposals_and_receipts(tmp_path: Path):
    db_path = tmp_path / "portfolio.db"
    db = Database(db_path)
    db.initialize()

    port1 = sample_portfolio()
    prop1 = db.store_proposal(
        base_revision=None,
        origin="import",
        before_portfolio=None,
        after_portfolio=port1,
        differences=[],
        warnings=[],
    )
    rev1, receipt1 = db.confirm_proposal(prop1.id)
    assert rev1.revision == 1
    assert receipt1.proposal_id == prop1.id
    assert receipt1.new_revision == 1

    port2 = port1.model_copy(deep=True)
    port2.snapshot.positions[0].shares = Decimal("75")
    prop2 = db.store_proposal(
        base_revision=1,
        origin="manual",
        before_portfolio=port1,
        after_portfolio=port2,
        differences=[],
        warnings=[],
    )
    rev2, receipt2 = db.confirm_proposal(prop2.id)
    assert rev2.revision == 2
    assert receipt2.new_revision == 2

    # Store a pending proposal based on revision 2
    port3 = port2.model_copy(deep=True)
    port3.snapshot.positions[0].shares = Decimal("100")
    prop3 = db.store_proposal(
        base_revision=2,
        origin="manual",
        before_portfolio=port2,
        after_portfolio=port3,
        differences=[],
        warnings=[],
    )
    assert prop3.status == "pending"

    # Simulate app restart by dropping the db instance and instantiating a new one
    del db
    db_restarted = Database(db_path)
    db_restarted.initialize()

    # 1. Current portfolio survives with revision 2
    current = db_restarted.get_current_portfolio()
    assert current is not None
    assert current.revision == 2
    assert current.portfolio.snapshot.positions[0].shares == Decimal("75")

    # 2. Pending proposal survives restart
    pending = db_restarted.list_proposals(status="pending")
    assert len(pending) == 1
    assert pending[0].id == prop3.id
    assert pending[0].status == "pending"

    # 3. Applied receipts survive restart and can be retrieved through proposal detail
    detail1 = db_restarted.get_proposal_detail(prop1.id)
    assert detail1 is not None
    assert detail1.receipt is not None
    assert detail1.receipt.new_revision == 1
    assert detail1.receipt.proposal_id == prop1.id

    detail2 = db_restarted.get_proposal_detail(prop2.id)
    assert detail2 is not None
    assert detail2.receipt is not None
    assert detail2.receipt.new_revision == 2
    assert detail2.receipt.previous_revision == 1


def test_backup_and_recovery(tmp_path: Path):
    import shutil

    db_path = tmp_path / "portfolio.db"
    backup_path = tmp_path / "portfolio_backup.db"

    db = Database(db_path)
    db.initialize()

    port = sample_portfolio()
    prop = db.store_proposal(
        base_revision=None,
        origin="import",
        before_portfolio=None,
        after_portfolio=port,
        differences=[],
        warnings=[],
    )
    rev1, receipt = db.confirm_proposal(prop.id)
    assert rev1.revision == 1

    # Take backup
    shutil.copy2(db_path, backup_path)

    # Mutate primary database to revision 2
    port2 = port.model_copy(deep=True)
    port2.snapshot.positions[0].shares = Decimal("500")
    prop2 = db.store_proposal(
        base_revision=1,
        origin="manual",
        before_portfolio=port,
        after_portfolio=port2,
        differences=[],
        warnings=[],
    )
    db.confirm_proposal(prop2.id)
    assert db.get_current_portfolio().revision == 2

    # Simulate disaster recovery: restore from backup
    shutil.copy2(backup_path, db_path)

    # Reopen recovered database
    recovered_db = Database(db_path)
    recovered_db.initialize()
    current_recovered = recovered_db.get_current_portfolio()
    assert current_recovered is not None
    assert current_recovered.revision == 1
    assert current_recovered.portfolio.snapshot.positions[0].shares == Decimal("50")

