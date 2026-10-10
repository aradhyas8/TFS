import httpx
import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider, PersonalFinancialProvider
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import FinancialEvidence
from tests.test_analysis import recommendation, snapshot


def test_broker_fallback_has_dates_age_and_blocks_confident_sizing():
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
        ModelTurn(answer=recommendation()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review dated broker marks", "portfolio": snapshot()}
    )
    assert response.status_code == 200, response.text
    review = response.json()["portfolio"]
    row = review["positions"][0]
    assert review["total_value"] == "3600"
    assert row["quote_used"]["status"] == "manual"
    assert row["quote_used"]["source"] == "Broker display"
    assert row["quote_age_days"] == 0
    assert row["quote_used"]["captured_at"] is None
    assert row["source_inputs_usable"] is False
    assert review["source_inputs_usable"] is False
    assert "dividends" in review["calculation_basis"]


def evidence_fixture():
    return {
        "identities": {
            "p1": {"status": "verified", "ticker": "ACME", "listing": "XNAS", "currency": "USD",
                   "kind": "stock", "company_id": "acme", "company_name": "Acme",
                   "source": "Exchange reference", "source_url": "https://example.test/listing/acme",
                   "as_of": "2026-09-30", "captured_at": "2026-09-30T20:00:00Z"},
        },
        "quotes": {
            "p1": {"value": "120", "as_of": "2026-09-30", "source": "Qualified fixture",
                   "captured_at": "2026-09-30T20:00:00Z", "ticker": "ACME", "listing": "XNAS",
                   "currency": "USD", "status": "delayed", "basis": "unadjusted",
                   "qualification": {"source": "Qualified fixture", "terms_url": "https://example.test/terms",
                                     "checked_on": "2026-09-29", "personal_use_permitted": True,
                                     "covered_listings": ["XNAS"]}},
        },
        "fx": [{"from_currency": "USD", "to_currency": "CAD", "rate": "1.4",
                "as_of": "2026-09-30", "source": "Dated FX fixture", "status": "indicative",
                "captured_at": "2026-09-30T21:00:00Z"}],
    }


def review_with_evidence(evidence, portfolio=None, financial=None):
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("identity", "resolve_identities", "{}")]),
        ModelTurn(calls=[ToolCall("quote", "get_quotes", "{}")]),
        ModelTurn(calls=[ToolCall("fx", "get_fx", "{}")]),
        ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
        ModelTurn(answer=recommendation()),
    ])
    provider = financial or FakeFinancialProvider(FinancialEvidence.model_validate(evidence))
    response = TestClient(create_app(model=model, data=FakeDataProvider(), financial=provider)).post(
        "/api/analyze", json={"question": "Refresh this portfolio", "portfolio": portfolio or snapshot()}
    )
    assert response.status_code == 200, response.text
    return response.json(), model


def test_financial_tools_refresh_the_same_review_and_exposure():
    result, model = review_with_evidence(evidence_fixture())
    review = result["portfolio"]
    row = review["positions"][0]
    assert row["identity_status"] == "verified"
    assert row["supplied"]["mark"]["value"] == "100"
    assert row["quote_used"]["value"] == "120"
    assert row["local_value"] == "1200"
    assert row["value"] == "1680"
    assert row["fx_used"]["rate"] == "1.4"
    assert review["total_value"] == "4030"
    assert review["direct_companies"][0]["value"] == "2680"
    assert review["direct_companies"][0]["weight"] == "0.66501241"
    assert row["quote_age_days"] == row["quote_age_at_capture_days"] == row["fx_age_days"] == 0
    assert result["recommendation"]["amount"] is None
    assert '"identities"' in model.requests[1][-1]["output"]
    assert '"quotes"' in model.requests[2][-1]["output"]
    assert '"fx"' in model.requests[3][-1]["output"]


@pytest.mark.parametrize("fault", ["ambiguous", "conflicting", "currency", "stale", "old", "future", "adjusted", "dividend_adjusted", "missing", "fx_stale", "fx_old"])
def test_unusable_sources_leave_values_unknown_and_amount_unset(fault):
    evidence = evidence_fixture()
    portfolio = snapshot()
    if fault in {"ambiguous", "conflicting"}:
        evidence["identities"]["p1"]["status"] = fault
    elif fault == "currency":
        evidence["quotes"]["p1"]["currency"] = "CAD"
    elif fault == "stale":
        evidence["quotes"]["p1"]["status"] = "stale"
    elif fault in {"old", "future"}:
        evidence["quotes"]["p1"]["as_of"] = "2026-09-22" if fault == "old" else "2026-10-01"
        if fault == "future":  # a price after the analysis date is dropped; with no on-or-before price the value is unknown
            portfolio["positions"][0]["mark"] = None
    elif fault in {"adjusted", "dividend_adjusted"}:
        evidence["quotes"]["p1"]["basis"] = "split_adjusted" if fault == "adjusted" else "total_return_adjusted"
    elif fault == "missing":
        evidence["quotes"]["p1"] = None
        portfolio["positions"][0]["mark"] = None
    elif fault == "fx_stale":
        evidence["fx"][0]["status"] = "stale"
    else:
        evidence["fx"][0]["as_of"] = "2026-09-22"
    result, _ = review_with_evidence(evidence, portfolio)
    review = result["portfolio"]
    assert review["positions"][0]["value"] is None
    assert review["total_value"] is None
    assert review["positions"][0]["weight"] is None
    assert review["source_inputs_usable"] is False
    assert review["qualifications"]
    assert result["recommendation"]["amount"] is None


@pytest.mark.parametrize("source", ["quote", "fx"])
def test_recent_provider_values_value_the_portfolio_with_their_own_date_but_never_size(source):
    evidence = evidence_fixture()
    if source == "quote":
        evidence["quotes"]["p1"]["as_of"] = "2026-09-29"
    else:
        evidence["fx"][0]["as_of"] = "2026-09-29"
    result, _ = review_with_evidence(evidence)
    row = result["portfolio"]["positions"][0]
    assert row["value"] == "1680"
    assert row["quote_age_days" if source == "quote" else "fx_age_days"] == 1
    assert row["source_inputs_usable"] is False


def test_manual_marks_from_an_earlier_day_stay_unknown():
    portfolio = snapshot()
    portfolio["positions"][0]["mark"]["as_of"] = "2026-09-29"
    result, _ = review_with_evidence({}, portfolio)
    assert result["portfolio"]["positions"][0]["value"] is None


@pytest.mark.parametrize("status", ["delayed", "cached", "stale", "manual"])
def test_price_status_is_preserved_without_becoming_live(status):
    evidence = evidence_fixture()
    evidence["quotes"]["p1"]["status"] = status
    result, _ = review_with_evidence(evidence)
    row = result["portfolio"]["positions"][0]
    assert row["quote_used"]["status"] == status
    if status in {"cached", "stale", "manual"}:
        assert row["source_inputs_usable"] is False
    assert any(status in issue for issue in row["issues"])


@pytest.mark.parametrize("fault", ["terms", "coverage", "conflicting_identity"])
def test_unqualified_prices_fall_back_and_identity_conflicts_stay_unresolved(fault):
    evidence = evidence_fixture()
    if fault == "terms":
        evidence["quotes"]["p1"]["qualification"]["personal_use_permitted"] = False
    elif fault == "coverage":
        evidence["quotes"]["p1"]["qualification"]["covered_listings"] = ["XTSE"]
    else:
        evidence["identities"]["p1"]["company_id"] = "other-company"
    result, _ = review_with_evidence(evidence)
    row = result["portfolio"]["positions"][0]
    if fault == "conflicting_identity":
        assert row["identity_status"] == "unresolved"
        assert row["identity"]["status"] == "conflicting"
        assert row["value"] is None
    else:
        assert row["quote_used"]["status"] == "manual"
        assert row["quote_used"]["value"] == "100"
        assert row["value"] == "1400"
        assert row["source_inputs_usable"] is False


@pytest.mark.parametrize("fault", ["capture_before_quote", "identity_name", "future_capture", "spoofed_fx"])
def test_contradictory_capture_and_identity_cannot_establish_usable_inputs(fault):
    evidence = evidence_fixture()
    portfolio = snapshot()
    if fault == "capture_before_quote":
        evidence["quotes"]["p1"]["captured_at"] = "2026-09-29T20:00:00Z"
    elif fault == "future_capture":
        evidence["quotes"]["p1"]["captured_at"] = "2099-09-30T20:00:00Z"
    elif fault == "identity_name":
        evidence["identities"]["p1"]["company_name"] = "Contradictory Company"
    else:
        evidence["fx"] = []
        portfolio["fx"][0]["status"] = "indicative"
        portfolio["fx"][0]["captured_at"] = "2026-09-30T21:00:00Z"
    result, _ = review_with_evidence(evidence, portfolio)
    row = result["portfolio"]["positions"][0]
    assert row["source_inputs_usable"] is False
    if fault == "identity_name":
        assert row["value"] is None
    if fault == "spoofed_fx":
        assert row["fx_used"]["status"] == "manual"


@pytest.mark.parametrize("inverse", [False, True])
def test_valet_dated_cad_conversion_through_application_with_fake_http(inverse):
    requests = []
    def fake_valet(request):
        requests.append(request)
        assert request.url.host == "www.bankofcanada.ca"
        assert request.url.path.endswith("/observations/FXUSDCAD/json")
        assert (request.url.params["start_date"], request.url.params["end_date"]) == ("2026-09-23", "2026-09-30")
        return httpx.Response(200, json={"observations": [{"d": "2026-09-29", "FXUSDCAD": {"v": "1.1"}}, {"d": "2026-09-30", "FXUSDCAD": {"v": "1.25"}}]})
    portfolio = snapshot()
    portfolio["fx"] = []
    if inverse:
        portfolio["reporting_currency"] = "USD"
    provider = PersonalFinancialProvider(valet=True, transport=httpx.MockTransport(fake_valet))
    result, _ = review_with_evidence({}, portfolio, provider)
    assert len(requests) == 1
    review = result["portfolio"]
    assert review["total_value"] == ("2820" if inverse else "3525")
    row = review["positions"][1 if inverse else 0]
    assert row["fx_used"]["rate"] == ("0.8000000000" if inverse else "1.25")
    assert row["fx_used"]["status"] == "indicative"
    assert "Bank of Canada Valet FXUSDCAD" in row["fx_used"]["source"]
    assert row["fx_used"]["captured_at"]


def test_valet_before_publication_uses_the_latest_earlier_day_with_its_date():
    def fake_valet(request):
        return httpx.Response(200, json={"observations": [{"d": "2026-09-29", "FXUSDCAD": {"v": "1.25"}}]})
    portfolio = snapshot()
    portfolio["fx"] = []
    provider = PersonalFinancialProvider(valet=True, transport=httpx.MockTransport(fake_valet))
    result, _ = review_with_evidence({}, portfolio, provider)
    row = result["portfolio"]["positions"][0]
    assert row["fx_used"]["as_of"] == "2026-09-29"
    assert row["fx_age_days"] == 1
    assert row["value"] == "1250"


@pytest.mark.parametrize("fault", ["missing", "future", "failure", "negative"])
def test_valet_missing_or_unusable_observation_stays_unknown(fault):
    def fake_valet(request):
        if fault == "failure":
            return httpx.Response(503)
        observations = [] if fault == "missing" else [{"d": "2026-10-01" if fault == "future" else "2026-09-30", "FXUSDCAD": {"v": "-1" if fault == "negative" else "1.25"}}]
        return httpx.Response(200, json={"observations": observations})
    portfolio = snapshot()
    portfolio["fx"] = []
    provider = PersonalFinancialProvider(valet=True, transport=httpx.MockTransport(fake_valet))
    result, _ = review_with_evidence({}, portfolio, provider)
    assert result["portfolio"]["positions"][0]["value"] is None
    assert result["portfolio"]["total_value"] is None
    assert result["recommendation"]["amount"] is None
    assert result["portfolio"]["sizing_eligible"] is False


def test_verified_source_resolves_omitted_listing_and_company_metadata():
    portfolio = snapshot()
    for field in ["ticker", "listing", "company_id", "company_name"]:
        portfolio["positions"][0][field] = None
    result, _ = review_with_evidence(evidence_fixture(), portfolio)
    row = result["portfolio"]["positions"][0]
    assert row["identity_status"] == "verified"
    assert row["supplied"]["company_id"] is None
    assert row["value"] == "1680"
    assert result["portfolio"]["direct_companies"][0]["value"] == "2680"
    assert result["portfolio"]["direct_companies"][0]["company_id"] == "acme"


def test_source_outage_preserves_verified_identity_and_explicit_broker_fallback():
    class UnavailableQuotes(FakeFinancialProvider):
        async def quote(self, position, as_of):
            raise httpx.ReadTimeout("Source unavailable")
    result, _ = review_with_evidence({}, financial=UnavailableQuotes(FinancialEvidence.model_validate(evidence_fixture())))
    row = result["portfolio"]["positions"][0]
    assert row["identity_status"] == "verified"
    assert row["quote_used"]["status"] == "manual"
    assert row["value"] == "1400"
    assert row["source_inputs_usable"] is False


def test_fx_outage_uses_only_explicit_manual_rate():
    class UnavailableFX(FakeFinancialProvider):
        async def fx(self, from_currency, to_currency, as_of):
            raise httpx.ReadTimeout("Source unavailable")
    result, _ = review_with_evidence({}, financial=UnavailableFX())
    assert result["portfolio"]["total_value"] == "3600"
    assert result["portfolio"]["positions"][0]["fx_used"]["status"] == "manual"
    assert result["portfolio"]["sizing_eligible"] is False


def test_post_split_snapshot_shares_and_unadjusted_price_are_counted_once():
    evidence = evidence_fixture()
    portfolio = snapshot()
    portfolio["positions"][0]["shares"] = "20"
    evidence["quotes"]["p1"]["value"] = "60"
    result, _ = review_with_evidence(evidence, portfolio)
    assert result["portfolio"]["positions"][0]["value"] == "1680"
    assert result["portfolio"]["total_value"] == "4030"
    assert result["portfolio"]["cash_value"] == "1350"
    assert "no split factor is applied again" in result["portfolio"]["calculation_basis"]
    assert "No dividends are added" in result["portfolio"]["calculation_basis"]


def dated_evidence(quote_as_of: str, analysis_day: str):
    """Fixture evidence with p1's cached quote on quote_as_of; identity and FX are re-checked on the analysis day."""
    evidence = evidence_fixture()
    evidence["quotes"]["p1"].update(as_of=quote_as_of, captured_at=f"{quote_as_of}T20:00:00Z")
    for row in [*evidence["identities"].values(), *evidence["fx"]]:
        row.update(as_of=analysis_day, captured_at=f"{analysis_day}T20:00:00Z")
    return evidence


def analyze_on(monkeypatch, today: str, evidence, *, analysis_date=None, mark=True, store=None):
    from datetime import date
    monkeypatch.setattr("analyst.pipeline.market_today", lambda: date.fromisoformat(today))
    portfolio = snapshot()  # holdings last confirmed 2026-09-30
    if not mark:
        portfolio["positions"][0]["mark"] = None
    model = ScriptedModel([ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]), ModelTurn(answer=recommendation())])
    body = {"question": "Review my portfolio", "portfolio": portfolio, **({"analysis_date": analysis_date} if analysis_date else {})}
    response = TestClient(create_app(model=model, data=FakeDataProvider(), portfolio_store=store,
                                     financial=FakeFinancialProvider(FinancialEvidence.model_validate(evidence)))).post("/api/analyze", json=body)
    assert response.status_code == 200, response.text
    return response.json()["portfolio"], portfolio


def test_current_review_values_older_holdings_with_a_newer_quote(monkeypatch):
    review, _ = analyze_on(monkeypatch, "2026-10-03", dated_evidence("2026-10-03", "2026-10-03"), mark=False)
    assert review["positions"][0]["value"] is not None and review["positions"][0]["quote_used"]["as_of"] == "2026-10-03"
    assert (review["as_of"], review["holdings_as_of"]) == ("2026-10-03", "2026-09-30")


def test_current_review_accepts_an_intermediate_quote_within_the_freshness_window(monkeypatch):
    review, _ = analyze_on(monkeypatch, "2026-10-03", dated_evidence("2026-10-01", "2026-10-03"), mark=False)
    assert review["positions"][0]["value"] is not None


def test_freshness_is_measured_from_the_analysis_date_not_the_holdings_date(monkeypatch):
    review, _ = analyze_on(monkeypatch, "2026-10-12", dated_evidence("2026-10-01", "2026-10-12"), mark=False)
    assert review["positions"][0]["value"] is None  # eleven days old on the analysis date


def test_historical_review_never_uses_a_later_quote(monkeypatch):
    review, _ = analyze_on(monkeypatch, "2026-10-03", dated_evidence("2026-10-03", "2026-09-30"), analysis_date="2026-09-30", mark=False)
    assert review["as_of"] == "2026-09-30"
    assert review["positions"][0]["value"] is None and review["positions"][0]["quote_used"] is None
    assert any("dated after the analysis date" in line for line in review["qualifications"])


@pytest.mark.parametrize("source", ["cached_quote", "broker_mark"])
def test_historical_review_uses_a_price_on_or_before_its_date(monkeypatch, source):
    quote_day = "2026-09-30" if source == "cached_quote" else "2026-10-03"  # a later cached quote falls back to the dated mark
    review, _ = analyze_on(monkeypatch, "2026-10-03", dated_evidence(quote_day, "2026-09-30"), analysis_date="2026-09-30", mark=source == "broker_mark")
    assert review["positions"][0]["value"] is not None
    assert review["positions"][0]["quote_used"]["as_of"] == "2026-09-30"


def test_an_analysis_cannot_predate_the_holdings(monkeypatch):
    from datetime import date
    monkeypatch.setattr("analyst.pipeline.market_today", lambda: date(2026, 10, 3))
    response = TestClient(create_app(model=ScriptedModel([]), data=FakeDataProvider())).post(
        "/api/analyze", json={"question": "Review", "portfolio": snapshot(), "analysis_date": "2026-09-29"})
    assert response.status_code == 422


def test_a_current_review_leaves_the_saved_portfolio_shares_and_date_unchanged(monkeypatch, tmp_path):
    from analyst.portfolio import PortfolioStore
    from analyst.schemas import SavedPortfolio, Snapshot
    store = PortfolioStore(tmp_path)
    store.save(SavedPortfolio(snapshot=Snapshot.model_validate(snapshot())))
    before = store.path.read_bytes()
    review, sent = analyze_on(monkeypatch, "2026-10-03", dated_evidence("2026-10-03", "2026-10-03"), store=store)
    assert store.path.read_bytes() == before
    assert [row["supplied"]["shares"] for row in review["positions"]] == [row.get("shares") for row in sent["positions"]]
    assert review["holdings_as_of"] == sent["as_of"] == "2026-09-30"
