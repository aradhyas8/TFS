import json
from decimal import Decimal

from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider
from analyst.portfolio import PortfolioStore
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import (
    NEW_CASH_DESTINATION,
    AnalysisRequest,
    BenchmarkSetting,
    FinancialEvidence,
    SavedPortfolio,
    Snapshot,
)
from tests.analysis_harness import analyze_with
from tests.test_allocation import (
    allocation_answer,
    allocation_evidence,
    allocation_request,
    comparison_judgments,
    run_allocation,
)

SIMPLE = "account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,120,31.50,etf\nTFSA,AAPL,10,,\nRRSP,VFV.TO,5,88,\n"


def client(tmp_path, reference=None):
    return TestClient(create_app(data=FakeDataProvider(), financial=FakeFinancialProvider(reference or FinancialEvidence()),
                                 portfolio_store=PortfolioStore(tmp_path)))


def imported(api, csv=SIMPLE, base_revision=..., **extra):
    cur = api.get("/api/portfolio").json()
    base = (cur["revision"] if cur else None) if base_revision is ... else base_revision
    return api.post(
        "/api/portfolio/import",
        json={"csv": csv, "as_of": "2026-10-01", "reporting_currency": "CAD", "base_revision": base, **extra},
    )


def imported_portfolio(api, csv=SIMPLE, base_revision=..., **extra) -> SavedPortfolio:
    res = imported(api, csv=csv, base_revision=base_revision, **extra)
    assert res.status_code == 200, res.text
    prop_id = res.json()["proposal"]["id"]
    detail = api.get(f"/api/portfolio/proposals/{prop_id}").json()
    return SavedPortfolio.model_validate(detail["after_portfolio"])


def test_simple_holdings_import_resolves_what_is_known_without_cash(tmp_path):
    saved = imported_portfolio(client(tmp_path)).model_dump(mode="json")
    snapshot = saved["snapshot"]
    assert snapshot["as_of"] == "2026-10-01"
    assert [a["name"] for a in snapshot["accounts"]] == ["TFSA", "RRSP"]
    # Suffix gives the listing, the type column gives the type; nothing else is filled in.
    assert [row["id"] for row in snapshot["positions"]] == ["tfsa-xeqt-to"]
    assert {"ticker": "XEQT", "listing": "XTSE", "currency": "CAD", "kind": "etf", "shares": "120"}.items() <= snapshot["positions"][0].items()
    assert snapshot["positions"][0]["mark"] is None
    # Average cost is optional and kept beside the snapshot, never as a price.
    assert saved["average_costs"] == {"tfsa-xeqt-to": "31.50"}
    assert saved["settings"] is None


def test_unknown_tickers_stay_unresolved_with_only_known_fields(tmp_path):
    saved = imported_portfolio(client(tmp_path)).model_dump(mode="json")
    unresolved = {row["ticker"]: row for row in saved["unresolved"]}
    # A bare ticker gets no assumed listing, type or currency.
    assert unresolved["AAPL"] == {"account_id": "tfsa", "ticker": "AAPL", "shares": "10", "average_cost": None,
                                       "currency": None, "listing": None, "kind": None, "candidates": []}
    # A suffix fixes listing and currency; only the type is still missing.
    assert (unresolved["VFV.TO"]["listing"], unresolved["VFV.TO"]["currency"], unresolved["VFV.TO"]["kind"]) == ("XTSE", "CAD", None)


def test_user_answers_the_minimum_clarification_and_the_holding_joins_the_snapshot(tmp_path):
    api = client(tmp_path)
    init_port = imported_portfolio(api)
    api.app.state.portfolio_store.save(init_port)
    partial_res = api.post("/api/portfolio/identify", json={"base_revision": 1, "account_id": "tfsa", "ticker": "AAPL", "kind": "stock"})
    assert partial_res.status_code == 200
    partial_id = partial_res.json()["proposal"]["id"]
    partial = api.get(f"/api/portfolio/proposals/{partial_id}").json()["after_portfolio"]
    assert next(row for row in partial["unresolved"] if row["ticker"] == "AAPL")["kind"] == "stock"
    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(partial))
    done_res = api.post("/api/portfolio/identify", json={"base_revision": 2, "account_id": "tfsa", "ticker": "AAPL", "listing": "XNAS"})
    assert done_res.status_code == 200
    done_id = done_res.json()["proposal"]["id"]
    done = api.get(f"/api/portfolio/proposals/{done_id}").json()["after_portfolio"]
    aapl = next(row for row in done["snapshot"]["positions"] if row["id"] == "tfsa-aapl")
    assert (aapl["listing"], aapl["currency"], aapl["kind"]) == ("XNAS", "USD", "stock")
    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(done))
    done_res2 = api.post("/api/portfolio/identify", json={"base_revision": 3, "account_id": "rrsp", "ticker": "VFV.TO", "kind": "etf"})
    assert done_res2.status_code == 200
    done2 = api.get(f"/api/portfolio/proposals/{done_res2.json()['proposal']['id']}").json()["after_portfolio"]
    assert done2["unresolved"] == []
    assert done2["average_costs"] == {"tfsa-xeqt-to": "31.50", "rrsp-vfv-to": "88"}
    assert api.post("/api/portfolio/identify", json={"base_revision": 3, "account_id": "tfsa", "ticker": "AAPL", "listing": "XLON"}).status_code == 422


def test_import_uses_reference_identity_for_listing_kind_and_issuer(tmp_path):
    reference = FinancialEvidence.model_validate({"identities": {"any": {
        "status": "supplied", "ticker": "AAPL", "listing": "XNAS", "currency": "USD", "kind": "stock",
        "company_id": "apple", "company_name": "Apple Inc.", "source": "Reference"}}})
    saved = imported_portfolio(client(tmp_path, reference)).model_dump(mode="json")
    aapl = next(row for row in saved["snapshot"]["positions"] if row["id"] == "tfsa-aapl")
    assert (aapl["listing"], aapl["company_id"], aapl["company_name"]) == ("XNAS", "apple", "Apple Inc.")
    assert [row["ticker"] for row in saved["unresolved"]] == ["VFV.TO"]


def test_rules_persist_with_the_portfolio_and_survive_replacing_holdings(tmp_path):
    api = client(tmp_path)
    saved = imported_portfolio(api).model_dump(mode="json")
    rules = {"single_company_cap": "0.15", "active_budget": None, "baseline": {"stocks": "0.2", "diversified_etfs": None, "sector_theme_etfs": None, "cash": "0.05"},
             "indirect_cap_policy": "include_known_indirect", "cash_is_deliberate_tilt": None}
    prep_res = api.put("/api/portfolio", json={"base_revision": None, "portfolio": saved | {"settings": rules}})
    assert prep_res.status_code == 200
    after = api.get(f"/api/portfolio/proposals/{prep_res.json()['proposal']['id']}").json()["after_portfolio"]
    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(after))
    restored = client(tmp_path).get("/api/portfolio").json()
    assert restored["revision"] == 1
    assert restored["portfolio"]["settings"] == rules  # unknown stays unknown: no defaults filled in
    rep_res = imported(api, "account,ticker,shares\nMargin,CASH,500\n", as_of="2026-10-05")
    assert rep_res.status_code == 200
    rep_after = api.get(f"/api/portfolio/proposals/{rep_res.json()['proposal']['id']}").json()["after_portfolio"]
    assert rep_after["settings"] == rules


def test_portfolio_persists_and_replaces(tmp_path):
    api = client(tmp_path)
    assert api.get("/api/portfolio").json() is None
    res = imported(api)
    assert res.status_code == 200
    assert api.get("/api/portfolio").json() is None
    after = api.get(f"/api/portfolio/proposals/{res.json()['proposal']['id']}").json()["after_portfolio"]
    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(after))
    restarted = client(tmp_path)  # a new app process reads the same file
    assert len(restarted.get("/api/portfolio").json()["portfolio"]["unresolved"]) == 2
    res2 = imported(restarted, "account,ticker,shares,type\nCash account,CASH,500,\nMargin,MSFT,2,stock\n", as_of="2026-10-05")
    assert res2.status_code == 200
    after2 = restarted.get(f"/api/portfolio/proposals/{res2.json()['proposal']['id']}").json()["after_portfolio"]
    assert after2["snapshot"]["as_of"] == "2026-10-05"
    assert {row["id"] for row in after2["snapshot"]["positions"]} == {"cash-account-cash-cad"}
    assert [row["ticker"] for row in after2["unresolved"]] == ["MSFT"]
    assert after2["average_costs"] == {}
    restarted.app.state.portfolio_store.save(SavedPortfolio.model_validate(after2))
    saved = restarted.get("/api/portfolio").json()["portfolio"]
    edited = saved | {"snapshot": saved["snapshot"] | {"positions": saved["snapshot"]["positions"] + [
        {"id": "margin-xeqt", "account_id": "margin", "kind": "etf", "currency": "CAD", "ticker": "XEQT", "listing": "XTSE", "shares": "3", "etf_role": "diversified"}]}}
    edit_res = restarted.put("/api/portfolio", json={"base_revision": 2, "portfolio": edited})
    assert edit_res.status_code == 200
    after3 = restarted.get(f"/api/portfolio/proposals/{edit_res.json()['proposal']['id']}").json()["after_portfolio"]
    restarted.app.state.portfolio_store.save(SavedPortfolio.model_validate(after3))
    assert next(row for row in client(tmp_path).get("/api/portfolio").json()["portfolio"]["snapshot"]["positions"] if row["id"] == "margin-xeqt")["etf_role"] == "diversified"


def test_rich_template_csv_still_imports(tmp_path):
    rich = ("row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source\n"
            "etf,fund,broker,Brokerage,BROAD,XNYS,,,80,,USD,100,2026-09-30,Broker,,,,\n")
    saved = imported_portfolio(client(tmp_path), rich).model_dump(mode="json")
    assert saved["snapshot"]["positions"][0]["id"] == "fund" and saved["snapshot"]["positions"][0]["mark"]["value"] == "100"


def test_bad_simple_csv_is_rejected_with_a_clear_reason(tmp_path):
    api = client(tmp_path)
    assert "columns" in imported(api, "ticker,qty\nAAPL,1\n").json()["detail"]
    assert "appears twice" in imported(api, "account,ticker,shares\nTFSA,AAPL,1\nTFSA,aapl,2\n").json()["detail"]
    assert "Line 2: shares" in imported(api, "account,ticker,shares\nTFSA,AAPL,many\n").json()["detail"]
    assert api.get("/api/portfolio").json() is None



def no_cash_request():
    request = allocation_request()
    request["portfolio"]["positions"] = [row for row in request["portfolio"]["positions"] if row["kind"] != "cash"]
    request["new_cash"] = {"amount": "6000", "account_id": "broker", "currency": "USD", "confirmed": True,
                           "risk_context": request["new_cash"]["risk_context"]}
    return request


def test_new_cash_destination_binds_a_temporary_zero_cash_row():
    request = AnalysisRequest.model_validate(no_cash_request())
    row = next(row for row in request.portfolio.positions if row.id == NEW_CASH_DESTINATION)
    assert (row.account_id, row.currency, row.cash) == ("broker", "USD", Decimal(0))
    assert request.new_cash and request.new_cash.cash_position_id == NEW_CASH_DESTINATION
    bad = no_cash_request()
    bad["new_cash"]["account_id"] = "elsewhere"
    try:
        AnalysisRequest.model_validate(bad)
    except ValueError as error:
        assert "destination account" in str(error)
    else:
        raise AssertionError("unknown destination accepted")


def test_new_cash_completes_against_a_portfolio_with_no_cash(tmp_path):
    # Without the old US$2,000 balance the fund is 8,000 of 14,000 after the contribution.
    sizing = {"position_id": "fund", "min_weight": "0.6", "max_weight": "0.7", "reason": "Diversification and a retained reserve."}
    response, _ = run_allocation(no_cash_request(), sizing=sizing, financial=FakeFinancialProvider(allocation_evidence()))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["allocation"]["amount"] == {"minimum": "400", "maximum": "1800", "currency": "USD", "position_id": "fund"}
    assert result["allocation"]["context"]["cash_position_id"] == NEW_CASH_DESTINATION


def test_new_cash_analysis_does_not_alter_the_saved_portfolio(tmp_path):
    store = PortfolioStore(tmp_path)
    request = no_cash_request()
    saved = store.save(SavedPortfolio(snapshot=Snapshot.model_validate(request["portfolio"])))
    before = store.get_revisioned()
    model = ScriptedModel([ModelTurn(calls=[ToolCall(k, n, json.dumps(a))]) for k, n, a in [
        ("review", "review_portfolio", {}), ("scan", "scan_opportunities", {}),
        ("comparison", "calculate_comparison", comparison_judgments()),
        ("sizing", "size_allocation", {"position_id": "fund", "min_weight": "0.6", "max_weight": "0.7", "reason": "Diversification."})]]
        + [ModelTurn(answer=allocation_answer())])
    api = TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(allocation_evidence()), portfolio_store=store))
    request["portfolio"] = api.get("/api/portfolio").json()["portfolio"]["snapshot"]
    assert analyze_with(api, request).status_code == 200
    assert store.get_revisioned() == before
    assert all(row["id"] != NEW_CASH_DESTINATION for row in api.get("/api/portfolio").json()["portfolio"]["snapshot"]["positions"])
    assert saved.saved_at


def test_benchmark_field_persists_in_saved_portfolio_outside_settings(tmp_path):
    api = client(tmp_path)
    saved = imported_portfolio(api).model_dump(mode="json")
    benchmark = {
        "ticker": "SPY",
        "listing": "XNYS",
        "currency": "USD",
        "name": "SPDR S&P 500 ETF Trust",
    }
    # Update portfolio with benchmark
    res = api.put("/api/portfolio", json={"base_revision": None, "portfolio": saved | {"benchmark": benchmark}})
    assert res.status_code == 200, res.text
    prop_id = res.json()["proposal"]["id"]
    after = api.get(f"/api/portfolio/proposals/{prop_id}").json()["after_portfolio"]
    assert after["benchmark"] == benchmark
    # Stays outside settings
    assert after["settings"] is None

    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(after))

    # Persists across process restart (save/load)
    restarted = client(tmp_path)
    loaded = restarted.get("/api/portfolio").json()["portfolio"]
    assert loaded["benchmark"] == benchmark
    assert loaded["settings"] is None

    # Re-importing CSV preserves benchmark
    rep_res = imported(api, "account,ticker,shares\nMargin,CASH,500\n", as_of="2026-10-05")
    assert rep_res.status_code == 200
    rep_after = api.get(f"/api/portfolio/proposals/{rep_res.json()['proposal']['id']}").json()["after_portfolio"]
    assert rep_after["benchmark"] == benchmark

    # Clearing benchmark persists
    cleared = api.put("/api/portfolio", json={"base_revision": 1, "portfolio": rep_after | {"benchmark": None}})
    assert cleared.status_code == 200
    cleared_after = api.get(f"/api/portfolio/proposals/{cleared.json()['proposal']['id']}").json()["after_portfolio"]
    assert cleared_after["benchmark"] is None
    api.app.state.portfolio_store.save(SavedPortfolio.model_validate(cleared_after))
    assert client(tmp_path).get("/api/portfolio").json()["portfolio"]["benchmark"] is None


def test_market_refresh_includes_saved_benchmark(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from analyst.financial_data import QuoteCache
    from analyst.schemas import BENCHMARK_FUND, Quote

    cache = QuoteCache(tmp_path / "market")
    provider = FakeFinancialProvider(cache=cache)
    now = datetime.now(ZoneInfo("America/New_York"))
    cache.put({
        "SPY.US": Quote(
            value=Decimal("500"),
            as_of=now.date(),
            source="Test provider",
            basis="unadjusted",
            ticker="SPY",
            listing="XNYS",
            currency="USD",
            status="delayed",
            captured_at=now,
        )
    })

    store = PortfolioStore(tmp_path / "portfolio")
    api = TestClient(create_app(financial=provider, portfolio_store=store))
    init_port = imported_portfolio(api)
    store.save(
        init_port.model_copy(
            update={
                "benchmark": BenchmarkSetting(
                    ticker="SPY",
                    listing="XNYS",
                    currency="USD",
                    name="SPDR S&P 500 ETF Trust",
                )
            }
        )
    )

    refreshed = api.post("/api/market/refresh", json={}).json()
    assert BENCHMARK_FUND in refreshed["quotes"]
    assert refreshed["quotes"][BENCHMARK_FUND]["value"] == "500"
    assert refreshed["quotes"][BENCHMARK_FUND]["currency"] == "USD"



