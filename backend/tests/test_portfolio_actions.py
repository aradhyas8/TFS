"""Tests for portfolio proposal preparation, deterministic differences, warnings, and retrieval."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider
from analyst.schemas import (
    Account,
    BenchmarkSetting,
    FinancialEvidence,
    PortfolioSettings,
    Position,
    SavedPortfolio,
    Snapshot,
    UnresolvedHolding,
)



def sample_portfolio() -> SavedPortfolio:
    return SavedPortfolio(
        snapshot=Snapshot(
            as_of=date(2026, 10, 1),
            reporting_currency="CAD",
            accounts=[Account(id="tfsa", name="TFSA")],
            positions=[
                Position(
                    id="tfsa-xeqt-to",
                    account_id="tfsa",
                    kind="etf",
                    currency="CAD",
                    ticker="XEQT",
                    listing="XTSE",
                    shares=Decimal("100"),
                )
            ],
            fx=[],
        ),
        average_costs={"tfsa-xeqt-to": Decimal("30.00")},
        settings=PortfolioSettings(single_company_cap=Decimal("0.15")),
        benchmark=BenchmarkSetting(ticker="SPY", listing="XNYS", currency="USD", name="SPDR S&P 500"),
        unresolved=[],
    )


def client(tmp_path, store=None, reference=None):
    ps = store or PortfolioStore(tmp_path)
    return TestClient(
        create_app(
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(reference or FinancialEvidence()),
            portfolio_store=ps,
        )
    ), ps


def test_first_creation_import_prepares_proposal_without_committing(tmp_path):
    api, store = client(tmp_path)
    assert api.get("/api/portfolio").json() is None

    csv_data = "account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,100,30.00,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "prepared"
    proposal = data["proposal"]
    assert proposal["id"].startswith("prop-")
    assert proposal["base_revision"] is None
    assert proposal["origin"] == "import"
    assert proposal["status"] == "pending"
    assert proposal["before_artifact"] is None
    assert proposal["after_artifact"]["basis"] == "hypothetical"
    assert len(proposal["differences"]) > 0

    # Ensure committed portfolio is STILL None
    assert api.get("/api/portfolio").json() is None

    # Retrieve proposal detail
    detail_res = api.get(f"/api/portfolio/proposals/{proposal['id']}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == proposal["id"]
    assert detail["before_portfolio"] is None
    assert detail["after_portfolio"] is not None
    assert detail["after_portfolio"]["snapshot"]["reporting_currency"] == "CAD"
    assert detail["after_portfolio"]["average_costs"]["tfsa-xeqt-to"] == "30.00"


def test_stale_base_revision_returns_409(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,100,30.00,etf\n"

    # Database is empty, but client provides base_revision = 1
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": 1},
    )
    assert res.status_code == 409
    assert "stale" in res.json()["detail"].lower()


def test_missing_base_revision_field_returns_422(tmp_path):
    api, _ = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"

    # Missing base_revision key completely
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD"},
    )
    assert res.status_code == 422


def test_manual_edit_prepares_proposal_and_detects_noop(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    saved = store.save(initial)
    current = api.get("/api/portfolio").json()
    assert current["revision"] == 1

    # Exact same content -> unchanged no-op
    noop_res = api.put(
        "/api/portfolio",
        json={"base_revision": 1, "portfolio": saved.model_dump(mode="json")},
    )
    assert noop_res.status_code == 200
    assert noop_res.json() == {"status": "unchanged", "proposal": None}

    # No proposal created
    assert api.get("/api/portfolio/proposals").json() == []

    # Modify a position
    modified = saved.model_copy(deep=True)
    modified.snapshot.positions.append(
        Position(
            id="tfsa-shop",
            account_id="tfsa",
            kind="stock",
            currency="CAD",
            ticker="SHOP",
            listing="XTSE",
            shares=Decimal("10"),
        )
    )
    edit_res = api.put(
        "/api/portfolio",
        json={"base_revision": 1, "portfolio": modified.model_dump(mode="json")},
    )
    assert edit_res.status_code == 200
    edit_data = edit_res.json()
    assert edit_data["status"] == "prepared"
    prop = edit_data["proposal"]
    assert prop["base_revision"] == 1
    assert prop["origin"] == "manual"
    diff_kinds = [d["kind"] for d in prop["differences"]]
    assert "position_added" in diff_kinds

    # Committed revision is STILL 1 and unchanged
    check = api.get("/api/portfolio").json()
    assert check["revision"] == 1
    assert len(check["portfolio"]["snapshot"]["positions"]) == 1


def test_replacement_import_preserves_rules_and_benchmark(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)

    # Import replacement with only a cash position
    csv_data = "account,ticker,shares,type\nTFSA,CASH,500,cash\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-05", "reporting_currency": "CAD", "base_revision": 1},
    )
    assert res.status_code == 200
    prop = res.json()["proposal"]

    detail = api.get(f"/api/portfolio/proposals/{prop['id']}").json()
    after = detail["after_portfolio"]
    # New holdings replaced the old ones
    assert len(after["snapshot"]["positions"]) == 1
    assert after["snapshot"]["positions"][0]["kind"] == "cash"
    # Rules and benchmark were preserved from revision 1
    assert after["settings"]["single_company_cap"] == "0.15"
    assert after["benchmark"]["ticker"] == "SPY"

    # Differences show the removed XEQT and added cash
    diff_kinds = {d["kind"] for d in prop["differences"]}
    assert "position_removed" in diff_kinds
    assert "position_added" in diff_kinds


def test_identity_correction_prepares_proposal(tmp_path):
    api, store = client(tmp_path)
    unresolved_holding = UnresolvedHolding(
        account_id="tfsa",
        ticker="AAPL",
        shares=Decimal("10"),
        currency="USD",
    )
    initial = SavedPortfolio(
        snapshot=Snapshot(
            as_of=date(2026, 10, 1),
            reporting_currency="USD",
            accounts=[Account(id="tfsa", name="TFSA")],
            positions=[],
            fx=[],
        ),
        unresolved=[unresolved_holding],
    )
    store.save(initial)

    res = api.post(
        "/api/portfolio/identify",
        json={"base_revision": 1, "account_id": "tfsa", "ticker": "AAPL", "kind": "stock"},
    )
    assert res.status_code == 200
    prop = res.json()["proposal"]
    assert prop["origin"] == "identify"

    detail = api.get(f"/api/portfolio/proposals/{prop['id']}").json()
    after = detail["after_portfolio"]
    assert len(after["unresolved"]) == 1
    assert after["unresolved"][0]["kind"] == "stock"

    # Committed state unchanged
    committed = api.get("/api/portfolio").json()["portfolio"]
    assert committed["unresolved"][0]["kind"] is None


def test_guardrail_warnings_and_qualifications(tmp_path):
    api, store = client(tmp_path)
    # Portfolio with unresolved holdings and missing prices
    unres = UnresolvedHolding(
        account_id="tfsa",
        ticker="SHOP",
        shares=Decimal("10"),
    )
    port = SavedPortfolio(
        snapshot=Snapshot(
            as_of=date(2026, 10, 1),
            reporting_currency="CAD",
            accounts=[Account(id="tfsa", name="TFSA")],
            positions=[],
            fx=[],
        ),
        unresolved=[unres],
        settings=PortfolioSettings(single_company_cap=Decimal("0.10")),
    )
    res = api.put("/api/portfolio", json={"base_revision": None, "portfolio": port.model_dump(mode="json")})
    assert res.status_code == 200
    prop = res.json()["proposal"]
    assert len(prop["warnings"]) > 0
    assert any("unresolved holding" in w.lower() for w in prop["warnings"])


def test_proposal_retrieval_and_restart_restoration(tmp_path):
    api1, store1 = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res1 = api1.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop1_id = res1.json()["proposal"]["id"]

    proposals_list1 = api1.get("/api/portfolio/proposals").json()
    assert len(proposals_list1) == 1
    assert proposals_list1[0]["id"] == prop1_id

    # Restart app with fresh client pointing to same storage
    api2, store2 = client(tmp_path)
    proposals_list2 = api2.get("/api/portfolio/proposals").json()
    assert len(proposals_list2) == 1
    assert proposals_list2[0]["id"] == prop1_id

    detail2 = api2.get(f"/api/portfolio/proposals/{prop1_id}").json()
    assert detail2["id"] == prop1_id
    assert detail2["after_portfolio"]["snapshot"]["reporting_currency"] == "CAD"


def test_unknown_proposal_id_returns_404(tmp_path):
    api, _ = client(tmp_path)
    res = api.get("/api/portfolio/proposals/prop-nonexistent")
    assert res.status_code == 404
