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
    Identity,
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


def test_confirmation_commits_stored_payload_and_creates_receipt(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,100,30.00,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    assert res.status_code == 200
    prop_id = res.json()["proposal"]["id"]

    # Confirm the proposal
    confirm_res = api.post(f"/api/portfolio/proposals/{prop_id}/confirm")
    assert confirm_res.status_code == 200, confirm_res.text
    data = confirm_res.json()
    assert "portfolio" in data
    assert "receipt" in data

    receipt = data["receipt"]
    assert receipt["proposal_id"] == prop_id
    assert receipt["previous_revision"] is None
    assert receipt["new_revision"] == 1
    assert "committed_at" in receipt

    portfolio_data = data["portfolio"]
    assert portfolio_data["revision"] == 1
    assert portfolio_data["portfolio"]["snapshot"]["reporting_currency"] == "CAD"
    assert portfolio_data["portfolio"]["average_costs"]["tfsa-xeqt-to"] == "30.00"

    # GET /api/portfolio returns revision 1
    current = api.get("/api/portfolio").json()
    assert current is not None
    assert current["revision"] == 1
    assert current["portfolio"]["snapshot"]["positions"][0]["ticker"] == "XEQT"

    # Proposal detail has status applied and receipt attached
    prop_detail = api.get(f"/api/portfolio/proposals/{prop_id}").json()
    assert prop_detail["status"] == "applied"
    assert prop_detail["receipt"] is not None
    assert prop_detail["receipt"]["new_revision"] == 1


def test_duplicate_confirmation_returns_same_receipt(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    first_confirm = api.post(f"/api/portfolio/proposals/{prop_id}/confirm").json()
    second_confirm = api.post(f"/api/portfolio/proposals/{prop_id}/confirm").json()

    assert first_confirm["receipt"] == second_confirm["receipt"]
    assert first_confirm["portfolio"]["revision"] == second_confirm["portfolio"]["revision"]


def test_applied_confirmation_retries_after_later_revisions_exist(tmp_path):
    api, store = client(tmp_path)
    # Prepare and confirm revision 1
    res1 = api.post(
        "/api/portfolio/import",
        json={"csv": "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n", "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop1_id = res1.json()["proposal"]["id"]
    confirm1 = api.post(f"/api/portfolio/proposals/{prop1_id}/confirm").json()
    receipt1 = confirm1["receipt"]

    # Prepare and confirm revision 2
    res2 = api.post(
        "/api/portfolio/import",
        json={"csv": "account,ticker,shares,type\nTFSA,CASH,500,cash\n", "as_of": "2026-10-05", "reporting_currency": "CAD", "base_revision": 1},
    )
    prop2_id = res2.json()["proposal"]["id"]
    confirm2 = api.post(f"/api/portfolio/proposals/{prop2_id}/confirm").json()
    assert confirm2["portfolio"]["revision"] == 2

    # Current revision is now 2
    assert api.get("/api/portfolio").json()["revision"] == 2

    # Retry confirmation of proposal 1: returns original receipt 1 and revision 1 portfolio
    retry1 = api.post(f"/api/portfolio/proposals/{prop1_id}/confirm")
    assert retry1.status_code == 200
    retry1_data = retry1.json()
    assert retry1_data["receipt"] == receipt1
    assert retry1_data["portfolio"]["revision"] == 1
    # Current portfolio in db remains revision 2
    assert api.get("/api/portfolio").json()["revision"] == 2


def test_competing_confirmations_cannot_overwrite_newer_state(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # Revision 1

    # Prepare proposal A against base 1
    mod_a = initial.model_copy(deep=True)
    mod_a.snapshot.positions.append(
        Position(id="tfsa-aapl", account_id="tfsa", kind="stock", currency="USD", ticker="AAPL", listing="XNYS", shares=Decimal("10"))
    )
    res_a = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod_a.model_dump(mode="json")})
    prop_a_id = res_a.json()["proposal"]["id"]

    # Prepare proposal B against base 1
    mod_b = initial.model_copy(deep=True)
    mod_b.snapshot.positions.append(
        Position(id="tfsa-msft", account_id="tfsa", kind="stock", currency="USD", ticker="MSFT", listing="XNYS", shares=Decimal("20"))
    )
    res_b = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod_b.model_dump(mode="json")})
    prop_b_id = res_b.json()["proposal"]["id"]

    # Confirm proposal A -> advances to revision 2
    conf_a = api.post(f"/api/portfolio/proposals/{prop_a_id}/confirm")
    assert conf_a.status_code == 200
    assert conf_a.json()["portfolio"]["revision"] == 2

    # Confirm proposal B -> must fail with 409 because base 1 is now stale
    conf_b = api.post(f"/api/portfolio/proposals/{prop_b_id}/confirm")
    assert conf_b.status_code == 409
    assert "stale" in conf_b.json()["detail"].lower()

    # Proposal B is now marked stale in the database
    prop_b_detail = api.get(f"/api/portfolio/proposals/{prop_b_id}").json()
    assert prop_b_detail["status"] == "stale"


def test_cancellation_semantics_and_duplicate(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    # Cancel the proposal
    cancel_res = api.post(f"/api/portfolio/proposals/{prop_id}/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "cancelled"

    # Committed state unchanged
    assert api.get("/api/portfolio").json() is None

    # Repeated cancellation is harmless
    cancel_res2 = api.post(f"/api/portfolio/proposals/{prop_id}/cancel")
    assert cancel_res2.status_code == 200
    assert cancel_res2.json()["status"] == "cancelled"

    # Cancelled proposal cannot be confirmed -> 409
    confirm_res = api.post(f"/api/portfolio/proposals/{prop_id}/confirm")
    assert confirm_res.status_code == 409
    assert "cancelled" in confirm_res.json()["detail"].lower()


def test_cannot_cancel_applied_proposal(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]
    api.post(f"/api/portfolio/proposals/{prop_id}/confirm")

    # Trying to cancel an applied proposal -> 409
    cancel_res = api.post(f"/api/portfolio/proposals/{prop_id}/cancel")
    assert cancel_res.status_code == 409


def test_rejection_of_replacement_payloads(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    # Confirm with non-empty JSON body -> 422
    confirm_bad = api.post(f"/api/portfolio/proposals/{prop_id}/confirm", json={"portfolio": {}})
    assert confirm_bad.status_code == 422

    # Confirm with raw string body -> 422
    confirm_raw = api.post(f"/api/portfolio/proposals/{prop_id}/confirm", content="unexpected text")
    assert confirm_raw.status_code == 422

    # Cancel with non-empty payload -> 422
    cancel_bad = api.post(f"/api/portfolio/proposals/{prop_id}/cancel", json={"force": True})
    assert cancel_bad.status_code == 422


def test_confirmation_independent_of_financial_provider_availability(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    # Now create an app with NO financial provider configured
    app_no_provider = create_app(
        data=FakeDataProvider(),
        financial=None,
        portfolio_store=store,
    )
    api_no_provider = TestClient(app_no_provider)

    # Confirmation succeeds completely without financial provider
    confirm_res = api_no_provider.post(f"/api/portfolio/proposals/{prop_id}/confirm")
    assert confirm_res.status_code == 200
    assert confirm_res.json()["portfolio"]["revision"] == 1


def test_confirmation_fault_injection_rolls_back_everything(tmp_path):
    from contextlib import contextmanager
    from unittest.mock import patch

    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    orig_connect = store.db.connect

    class ConnectionProxy:
        def __init__(self, target):
            self._target = target

        def execute(self, sql, *args, **kwargs):
            if "proposal_receipts" in sql:
                raise RuntimeError("Simulated crash midway through transaction")
            return self._target.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._target, name)

    @contextmanager
    def faulty_connect():
        with orig_connect() as conn:
            yield ConnectionProxy(conn)

    with patch.object(store.db, "connect", faulty_connect):
        with pytest.raises(RuntimeError, match="Simulated crash"):
            store.db.confirm_proposal(prop_id)

    # Verify rollback:
    # 1. No portfolio revision exists
    assert store.db.get_current_portfolio() is None
    # 2. Proposal is STILL pending
    prop = store.db.get_proposal(prop_id)
    assert prop is not None
    assert prop.status == "pending"
    assert prop.receipt is None

    # Now confirm without fault succeeds
    rev, receipt = store.db.confirm_proposal(prop_id)
    assert rev.revision == 1
    assert receipt.new_revision == 1



def test_confirmation_lifecycle_persists_across_restart(tmp_path):
    api1, store1 = client(tmp_path)
    csv_data = "account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,100,30.00,etf\n"
    res1 = api1.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res1.json()["proposal"]["id"]

    # Confirm in session 1
    conf_res1 = api1.post(f"/api/portfolio/proposals/{prop_id}/confirm")
    assert conf_res1.status_code == 200
    receipt1 = conf_res1.json()["receipt"]

    # Verify committed state in session 1
    curr1 = api1.get("/api/portfolio").json()
    assert curr1["revision"] == 1

    # Restart app with fresh client and fresh PortfolioStore pointing to same path
    api2, store2 = client(tmp_path)
    curr2 = api2.get("/api/portfolio").json()
    assert curr2 is not None
    assert curr2["revision"] == 1
    assert curr2["portfolio"]["snapshot"]["reporting_currency"] == "CAD"
    assert curr2["portfolio"]["average_costs"]["tfsa-xeqt-to"] == "30.00"

    # Detail persists status applied and receipt
    prop2_detail = api2.get(f"/api/portfolio/proposals/{prop_id}").json()
    assert prop2_detail["status"] == "applied"
    assert prop2_detail["receipt"]["proposal_id"] == prop_id
    assert prop2_detail["receipt"]["new_revision"] == 1

    # Retry confirm on restarted app returns original receipt
    retry_conf = api2.post(f"/api/portfolio/proposals/{prop_id}/confirm")
    assert retry_conf.status_code == 200
    assert retry_conf.json()["receipt"] == receipt1
    assert retry_conf.json()["portfolio"]["revision"] == 1


def test_database_locking_failure_returns_503(tmp_path):
    api, store = client(tmp_path)
    csv_data = "account,ticker,shares,type\nTFSA,XEQT.TO,100,etf\n"
    res = api.post(
        "/api/portfolio/import",
        json={"csv": csv_data, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": None},
    )
    prop_id = res.json()["proposal"]["id"]

    import sqlite3
    from unittest.mock import patch

    with patch("sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
        confirm_res = api.post(f"/api/portfolio/proposals/{prop_id}/confirm")
        assert confirm_res.status_code == 503
        assert "locked" in confirm_res.json()["detail"].lower() or "busy" in confirm_res.json()["detail"].lower()




def test_concurrent_confirmations_separate_connections(tmp_path):
    import concurrent.futures
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1

    # Prepare proposal 1
    mod1 = initial.model_copy(deep=True)
    mod1.snapshot.positions.append(Position(id="p1", account_id="tfsa", kind="stock", currency="CAD", ticker="SHOP", listing="XTSE", shares=Decimal("1")))
    res1 = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod1.model_dump(mode="json")})
    prop1_id = res1.json()["proposal"]["id"]

    # Prepare proposal 2
    mod2 = initial.model_copy(deep=True)
    mod2.snapshot.positions.append(Position(id="p2", account_id="tfsa", kind="stock", currency="CAD", ticker="BMO", listing="XTSE", shares=Decimal("1")))
    res2 = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod2.model_dump(mode="json")})
    prop2_id = res2.json()["proposal"]["id"]

    # Two separate API clients pointing to the same database
    c1, _ = client(tmp_path, store=store)
    c2, _ = client(tmp_path, store=store)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(lambda: c1.post(f"/api/portfolio/proposals/{prop1_id}/confirm").status_code)
        f2 = executor.submit(lambda: c2.post(f"/api/portfolio/proposals/{prop2_id}/confirm").status_code)
        results = [f1.result(), f2.result()]

    # Exactly one must be 200, and the other 409
    assert sorted(results) == [200, 409]
    # Current revision must be 2
    assert api.get("/api/portfolio").json()["revision"] == 2


def test_complete_identity_resolution_and_confirmation(tmp_path):
    now = datetime.now(UTC)
    evidence = FinancialEvidence()
    evidence.identities["bmo_ca"] = Identity(
        status="verified",
        ticker="BMO",
        listing="XTSE",
        currency="CAD",
        kind="stock",
        company_id="bmo",
        company_name="Bank of Montreal",
        source="Test reference",
        as_of=date(2026, 10, 1),
        captured_at=now,
    )
    api, store = client(tmp_path, reference=evidence)
    unresolved_holding = UnresolvedHolding(
        account_id="tfsa",
        ticker="BMO",
        shares=Decimal("50"),
        currency="CAD",
        average_cost=Decimal("125.50"),
    )
    initial = SavedPortfolio(
        snapshot=Snapshot(
            as_of=date(2026, 10, 1),
            reporting_currency="CAD",
            accounts=[Account(id="tfsa", name="TFSA")],
            positions=[],
            fx=[],
        ),
        unresolved=[unresolved_holding],
        average_costs={},
    )
    store.save(initial)  # revision 1
    assert api.get("/api/portfolio").json()["revision"] == 1

    # Prepare identity correction with complete exchange & kind
    res = api.post(
        "/api/portfolio/identify",
        json={"base_revision": 1, "account_id": "tfsa", "ticker": "BMO", "listing": "XTSE", "kind": "stock"},
    )
    assert res.status_code == 200, res.text
    prop = res.json()["proposal"]
    assert prop["origin"] == "identify"
    assert prop["status"] == "pending"

    # Verify detail has enriched position and empty unresolved
    detail = api.get(f"/api/portfolio/proposals/{prop['id']}").json()
    after = detail["after_portfolio"]
    assert len(after["unresolved"]) == 0
    assert len(after["snapshot"]["positions"]) == 1
    pos = after["snapshot"]["positions"][0]
    assert pos["ticker"] == "BMO"
    assert pos["listing"] == "XTSE"
    assert pos["company_name"] == "Bank of Montreal"
    assert pos["company_id"] == "bmo"
    assert pos["kind"] == "stock"
    # Average cost is preserved in human records
    assert after["average_costs"][pos["id"]] == "125.50"

    # Confirm proposal
    confirm_res = api.post(f"/api/portfolio/proposals/{prop['id']}/confirm")
    assert confirm_res.status_code == 200, confirm_res.text
    receipt = confirm_res.json()["receipt"]
    assert receipt["previous_revision"] == 1
    assert receipt["new_revision"] == 2

    # Verify committed portfolio
    committed_data = api.get("/api/portfolio").json()
    assert committed_data["revision"] == 2
    committed = committed_data["portfolio"]
    assert len(committed["unresolved"]) == 0
    assert len(committed["snapshot"]["positions"]) == 1
    committed_pos = committed["snapshot"]["positions"][0]
    assert committed_pos["ticker"] == "BMO"
    assert committed["average_costs"][committed_pos["id"]] == "125.50"


def test_decision_journal_confirmation_leaves_portfolio_revision_unchanged(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1
    assert api.get("/api/portfolio").json()["revision"] == 1

    # Save a decision in the decision journal
    decision_payload = {
        "id": "dec-01",
        "saved_at": "2026-10-01T12:00:00Z",
        "as_of": "2026-10-01",
        "question": "What is my exposure?",
        "evidence_references": [],
        "reasoning": {
            "reason": "Test reasoning",
            "assumptions": [],
            "uncertainty": [],
            "what_could_change": [],
            "downside": "Limited downside",
            "alternatives": [],
        },
        "conclusion": {"preferred_action": "no_action", "amount": None},
        "confirmed_action": None,
    }
    save_res = api.post("/api/decisions", json={"decision": decision_payload})
    assert save_res.status_code == 200

    # Confirm action on the decision
    confirm_decision_res = api.post(
        "/api/decisions/dec-01/confirm",
        json={"action": "no_action", "notes": "Confirmed by user"},
    )
    assert confirm_decision_res.status_code == 200
    assert confirm_decision_res.json()["confirmed_action"]["action"] == "no_action"

    # Crucial check: portfolio revision must REMAIN 1 and unchanged!
    portfolio_res = api.get("/api/portfolio").json()
    assert portfolio_res["revision"] == 1
    assert portfolio_res["portfolio"]["snapshot"]["positions"][0]["ticker"] == "XEQT"


def test_pending_proposals_listing_and_stale_recovery(tmp_path):
    api, store = client(tmp_path)
    initial = sample_portfolio()
    store.save(initial)  # revision 1

    # Prepare proposal 1 against revision 1
    mod1 = initial.model_copy(deep=True)
    mod1.snapshot.positions.append(Position(id="p1", account_id="tfsa", kind="stock", currency="CAD", ticker="SHOP", listing="XTSE", shares=Decimal("10")))
    res1 = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod1.model_dump(mode="json")})
    prop1_id = res1.json()["proposal"]["id"]

    # List pending proposals
    pending = api.get("/api/portfolio/proposals?status=pending").json()
    assert any(p["id"] == prop1_id for p in pending)

    # Now confirm an independent change, advancing current revision to 2
    mod2 = initial.model_copy(deep=True)
    mod2.snapshot.positions.append(Position(id="p2", account_id="tfsa", kind="stock", currency="CAD", ticker="RY", listing="XTSE", shares=Decimal("5")))
    res2 = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": mod2.model_dump(mode="json")})
    prop2_id = res2.json()["proposal"]["id"]
    conf2 = api.post(f"/api/portfolio/proposals/{prop2_id}/confirm")
    assert conf2.status_code == 200
    assert api.get("/api/portfolio").json()["revision"] == 2

    # Proposal 1 was on base_revision 1, so it should now be stale
    prop1_detail = api.get(f"/api/portfolio/proposals/{prop1_id}").json()
    assert prop1_detail["status"] == "stale"

    # Confirming a stale proposal returns 409
    conf1 = api.post(f"/api/portfolio/proposals/{prop1_id}/confirm")
    assert conf1.status_code == 409
    assert "stale" in conf1.json()["detail"].lower()


