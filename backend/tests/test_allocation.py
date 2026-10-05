import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.financial_data import FakeFinancialProvider
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.schemas import FinancialEvidence
from tests.test_analysis import recommendation


def allocation_request():
    return {"question": "I have this portfolio and $6,000. What should I do?",
            "portfolio": {"as_of": "2026-09-30", "reporting_currency": "USD",
                          "accounts": [{"id": "broker", "name": "Brokerage"}],
                          "positions": [
                              {"id": "fund", "account_id": "broker", "kind": "etf", "currency": "USD",
                               "ticker": "BROAD", "listing": "XNYS", "shares": "80", "etf_role": "diversified"},
                              {"id": "cash", "account_id": "broker", "kind": "cash", "currency": "USD", "cash": "2000"}]},
            "settings": {"single_company_cap": "0.15", "active_budget": "0.3",
                         "indirect_cap_policy": "direct_only", "cash_is_deliberate_tilt": False},
            "new_cash": {"amount": "6000", "cash_position_id": "cash", "confirmed": True,
                         "risk_context": "Long horizon; equity losses are tolerable and no near-term withdrawal is planned."}}


def allocation_evidence():
    return FinancialEvidence.model_validate({
        "identities": {"fund": {"status": "verified", "ticker": "BROAD", "listing": "XNYS", "currency": "USD",
                               "kind": "etf", "source": "Exchange fixture", "source_url": "https://example.test/fund", "as_of": "2026-09-30",
                               "captured_at": "2026-09-30T20:00:00Z"}},
        "quotes": {"fund": {"value": "100", "ticker": "BROAD", "listing": "XNYS", "currency": "USD",
                            "as_of": "2026-09-30", "source": "Qualified fixture", "status": "delayed",
                            "captured_at": "2026-09-30T20:00:00Z",
                            "qualification": {"source": "Qualified fixture", "terms_url": "https://example.test/terms",
                                              "checked_on": "2026-09-30", "personal_use_permitted": True,
                                              "covered_listings": ["XNYS"]}}}})


def comparison_judgments(ids=("fund", "cash", "keep")):
    from tests.test_comparison import driver
    return {"alternatives": [{"alternative_id": key, "cases": [
        {"name": name, "drivers": [driver("__new_cash__" if key in {"cash", "keep"} else key, cash=key in {"cash", "keep"})],
         "assumptions": ["Paths are conditional judgments."], "downside": "Equity losses and falling rates can impair wealth.",
         "uncertainty": ["Future returns and rates remain uncertain."]}
        for name in ("downside", "base", "upside")]} for key in ids]}


def allocation_answer():
    answer = recommendation()
    answer.update(preferred_action="add", reason="A conditional fund addition offers diversified exposure while retaining a cash reserve.",
                  alternatives=[{"action": "no_action", "reason": "Retaining the contribution as cash preserves liquidity if the equity downside is uncomfortable."},
                                {"action": "clarify_inputs", "reason": "Defer a commitment while checking fund costs and the breadth of current opportunity coverage."}],
                  downside="Equity drawdowns can impair capital; cash faces falling reinvestment rates and purchasing-power erosion.",
                  assumptions=["Confirmed new cash is additional to the dated whole-portfolio snapshot; future paths remain analyst judgments."],
                  uncertainty=["Fund costs, personal tax effects and indirect company overlap remain unquantified. Screening coverage may be limited."],
                  what_could_change=["New issuer evidence, updated fund costs, revised rate paths or changed withdrawal needs could change the choice."], evidence_ids=[])
    return answer


def run_allocation(request=None, *, sizing=None, answer=None, financial=None):
    turns = [ModelTurn(calls=[ToolCall(key, name, json.dumps(args))]) for key, name, args in [
        ("review", "review_portfolio", {}), ("scan", "scan_opportunities", {}),
        ("comparison", "calculate_comparison", comparison_judgments()),
        ("sizing", "size_allocation", sizing or {"position_id": "fund", "min_weight": "0.55", "max_weight": "0.65",
                                                "reason": "Diversification and a retained reserve justify this exposure range."})]]
    turns.append(ModelTurn(answer=answer or allocation_answer()))
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(), financial=financial or FakeFinancialProvider(allocation_evidence()))).post(
        "/api/analyze", json=request or allocation_request())
    return response, model


def test_new_cash_completes_fresh_scan_comparison_and_checked_approximate_amount():
    response, model = run_allocation()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["total_value"] == "10000"
    assert result["allocation"]["scan"]["scanned_at"]
    assert result["allocation"]["researched"] == []
    assert result["comparison"]["starting_value"] == "6000"
    assert {row["selection"]["kind"] for row in result["comparison"]["alternatives"]} == {"etf", "cash", "no_action"}
    assert result["recommendation"]["amount"] == {"minimum": "800", "maximum": "2400", "currency": "USD", "position_id": "fund"}
    assert result["allocation"]["previews"][1]["post_total_value"] == "16000"
    assert result["allocation"]["previews"][1]["positions"][0]["weight"] == "0.65000000"
    assert result["portfolio"]["indirect_exposure"] == "unknown"
    assert "__new_cash__" in str(model.requests[-1])


@pytest.mark.parametrize("fault", ["unconfirmed", "amount", "account", "risk", "cap", "budget", "indirect", "cash_tilt", "identity", "price", "snapshot", "price_zero"])
def test_decision_critical_gaps_complete_with_conditional_direction(fault):
    request = allocation_request()
    evidence = allocation_evidence()
    if fault == "unconfirmed":
        request["new_cash"]["confirmed"] = False
    elif fault in {"amount", "risk", "account"}:
        request["new_cash"][{"amount": "amount", "risk": "risk_context", "account": "cash_position_id"}[fault]] = None
    elif fault in {"cap", "budget", "indirect", "cash_tilt"}:
        request["settings"][{"cap": "single_company_cap", "budget": "active_budget", "indirect": "indirect_cap_policy", "cash_tilt": "cash_is_deliberate_tilt"}[fault]] = "include_known_indirect" if fault == "indirect" else None
    elif fault == "identity":
        evidence.identities["fund"].status = "ambiguous"
    elif fault == "price":
        evidence.quotes["fund"].status = "stale"
    elif fault == "price_zero":
        evidence.quotes["fund"].value = Decimal(0)
    else:
        request["portfolio"]["positions"].append({"id": "missing", "account_id": "broker", "kind": "stock", "currency": "USD", "ticker": "MISSING", "listing": "XNYS", "company_id": "missing", "company_name": "Missing", "shares": "10"})
    response, _ = run_allocation(request, financial=FakeFinancialProvider(evidence))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["recommendation"]["amount"] is None
    assert result["allocation"]["missing_inputs"]


class FakeDiscovery:
    def __init__(self, count):
        self.calls = 0
        self.count = count

    async def scan(self, snapshot):
        from analyst.schemas import DiscoveryScan
        self.calls += 1
        return DiscoveryScan.model_validate({"scanned_at": datetime.now(UTC), "as_of": snapshot.as_of,
            "source": "Controlled dated opportunity screen", "candidates": [
                {"position": {"id": f"candidate-{i}", "account_id": "broker", "kind": "stock", "currency": "USD",
                              "ticker": f"ISSUER-{i}", "listing": "XNYS", "company_id": f"issuer-{i}",
                              "company_name": f"Issuer {i}", "shares": "0"},
                 "as_of": snapshot.as_of, "source": "Dated screen fixture", "signal": "Operating evidence may distinguish this company from diversified exposure."}
                for i in range(self.count)]})


def candidate_journey(count, *, research_count=None, size=False, settings=None, answer=None, citations=None):
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    from tests.test_stock import company_judgments, research_fixture
    request = allocation_request()
    if settings:
        request["settings"].update(settings)
    discovery = FakeDiscovery(count)
    records = {}
    evidence = allocation_evidence()
    for i in range(count):
        source = research_fixture()
        source["company_id"] = f"issuer-{i}"
        for doc in source["documents"]:
            doc["company_id"] = source["company_id"]
        records[source["company_id"]] = CompanyResearch.model_validate(source)
        evidence.identities[f"candidate-{i}"] = evidence.identities["fund"].model_copy(update={
            "ticker": f"ISSUER-{i}", "kind": "stock", "company_id": source["company_id"], "company_name": f"Issuer {i}"})
        evidence.quotes[f"candidate-{i}"] = evidence.quotes["fund"].model_copy(update={"ticker": f"ISSUER-{i}"})
    researched = count if research_count is None else research_count
    turns = [ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]), ModelTurn(calls=[ToolCall("scan", "scan_opportunities", "{}")])]
    for i in range(researched):
        turns.extend([ModelTurn(calls=[ToolCall(f"research-{i}", "research_candidate", json.dumps({"position_id": f"candidate-{i}", "reason": "Primary evidence could change the choice versus diversified exposure."}))]),
                      ModelTurn(calls=[ToolCall(f"cases-{i}", "calculate_company_cases", json.dumps({"position_id": f"candidate-{i}", "judgments": company_judgments()}))])])
    paths = comparison_judgments(tuple([f"candidate-{i}" for i in range(researched)] + ["fund", "cash", "keep"]))
    for alternative in paths["alternatives"]:
        if alternative["alternative_id"].startswith("candidate-"):
            for case in alternative["cases"]:
                case["drivers"][0].update(annual_returns=None, income_multipliers=None, reinvest=False)
    turns.append(ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(paths))]))
    final = answer or allocation_answer()
    if size:
        turns.append(ModelTurn(calls=[ToolCall("size", "size_allocation", json.dumps({"position_id": "candidate-0", "min_weight": "0.05", "max_weight": "0.1", "reason": "The conditional cases support a modest exposure while preserving diversification."}))]))
        final.update(reason="A modest conditional company addition balances the operating-case upside against company downside and existing diversified exposure.",
                     alternatives=[{"action": "add", "reason": "A diversified fund offers broader exposure if company-specific downside is uncomfortable."},
                                   {"action": "no_action", "reason": "Retain the new cash if the operating-case judgments do not justify commitment."}],
                     evidence_ids=["candidate-1-document-1", "candidate-1-document-2"] if citations is None else citations)
    else:
        final.update(preferred_action="no_action", reason="The screened companies do not clearly improve the tradeoff against the diversified fund and retaining cash.",
                     alternatives=[{"action": "add", "reason": "A conditional diversified-fund addition could reduce reliance on individual company outcomes."},
                                   {"action": "clarify_inputs", "reason": "Defer the choice while testing pivotal operating assumptions and screening coverage."}])
    turns.append(ModelTurn(answer=final))
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(evidence),
                                    research=ReviewedResearchProvider(records), discovery=discovery)).post("/api/analyze", json=request)
    return response, discovery, model


@pytest.mark.parametrize("count", [0, 1, 2])
def test_fresh_bounded_scan_and_research_stop_without_forcing_a_buy(count):
    response, provider, _ = candidate_journey(count)
    assert response.status_code == 200, response.text
    result = response.json()
    assert provider.calls == 1
    assert len(result["allocation"]["researched"]) == count
    assert len(result["allocation"]["stocks"]) == count
    assert len(result["comparison"]["alternatives"]) == count + 3
    assert all(row["as_of"] == result["portfolio"]["as_of"] for row in result["allocation"]["stocks"])
    assert result["recommendation"]["preferred_action"] == "no_action"
    assert result["recommendation"]["amount"] is None


def test_discovery_rejects_third_deep_research_candidate():
    response, _, _ = candidate_journey(3)
    assert response.status_code == 502, response.text
    assert "recommendation" not in response.json()


def test_stock_amount_and_post_contribution_limits_are_authoritative():
    response, _, _ = candidate_journey(1, size=True)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["amount"] == {"minimum": "800", "maximum": "1600", "currency": "USD", "position_id": "candidate-0"}
    post = result["allocation"]["previews"][1]
    assert post["post_total_value"] == "16000"
    assert post["guardrails"]["companies"][0]["current_weight"] == "0.10000000"
    assert post["guardrails"]["active"]["weight"] == "0.10000000"
    assert post["status"] == "within_limits"


@pytest.mark.parametrize("limit", ["single_company_cap", "active_budget"])
def test_stock_sizing_cannot_waive_configured_limits(limit):
    response, _, _ = candidate_journey(1, size=True, settings={limit: "0.08"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["amount"] is None
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["allocation"]["previews"][1]["status"] == "blocked"


@pytest.mark.parametrize("fault", ["amount", "probability", "order", "trade_completed", "invented_evidence"])
def test_model_cannot_supply_amounts_probabilities_orders_or_invented_sources(fault):
    answer = allocation_answer()
    if fault == "amount":
        answer["amount"] = {"minimum": "800", "maximum": "2400", "currency": "USD", "position_id": "fund"}
    elif fault == "probability":
        answer["reason"] = "The upside probability is high."
    elif fault == "order":
        answer["reason"] = "We placed an order and bought the fund."
    elif fault == "trade_completed":
        answer["reason"] = "The trade completed and the order was filled."
    else:
        answer["evidence_ids"] = ["invented"]
    response, _ = run_allocation(answer=answer)
    assert response.status_code == 502, response.text


def test_question_alone_requests_confirmation_in_the_same_completed_answer():
    request = allocation_request()
    del request["new_cash"]
    response, _ = run_allocation(request)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"] is None
    assert response.json()["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_fresh_scan_repeats_for_identical_new_cash_questions():
    from analyst.allocation import ReviewedDiscoveryProvider
    class CountingDiscovery(ReviewedDiscoveryProvider):
        def __init__(self):
            self.calls = 0
        async def scan(self, snapshot):
            self.calls += 1
            return await super().scan(snapshot)
    provider = CountingDiscovery()
    responses = []
    for _ in range(2):
        # Capture only the external scripted turns from the ordinary journey.
        model = ScriptedModel([ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
                               ModelTurn(calls=[ToolCall("scan", "scan_opportunities", "{}")]),
                               ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(comparison_judgments()))]),
                               ModelTurn(answer={**allocation_answer(), "preferred_action": "no_action"})])
        response = TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(allocation_evidence()), discovery=provider)).post("/api/analyze", json=allocation_request())
        assert response.status_code == 200, response.text
        responses.append(response.json())
    assert provider.calls == 2
    assert responses[0]["allocation"]["scan"]["scanned_at"] != responses[1]["allocation"]["scan"]["scanned_at"]


def test_reporting_currency_and_account_cash_are_added_once_to_the_denominator():
    request = allocation_request()
    request["portfolio"]["reporting_currency"] = "CAD"
    evidence = allocation_evidence()
    from analyst.schemas import FX
    evidence.fx = [FX(from_currency="USD", to_currency="CAD", rate="1.5", as_of="2026-09-30", source="FX fixture",
                      captured_at="2026-09-30T20:00:00Z", status="indicative")]
    response, _ = run_allocation(request, financial=FakeFinancialProvider(evidence))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["total_value"] == "15000"
    assert result["comparison"]["starting_value"] == "9000"
    assert result["allocation"]["previews"][1]["post_total_value"] == "24000"
    assert result["recommendation"]["amount"]["maximum"] == "2400"
    assert result["recommendation"]["amount"]["currency"] == "USD"


def test_missing_fx_completes_unknown_cases_without_a_numeric_allocation():
    request = allocation_request()
    request["portfolio"]["reporting_currency"] = "CAD"
    response, _ = run_allocation(request)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"] is None
    assert response.json()["comparison"]["starting_value"] is None


def test_unused_unpriced_scan_candidate_does_not_prohibit_fund_sizing():
    discovery = FakeDiscovery(1)
    model = ScriptedModel([ModelTurn(calls=[ToolCall("review", "review_portfolio", "{}")]),
                          ModelTurn(calls=[ToolCall("scan", "scan_opportunities", "{}")]),
                          ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(comparison_judgments()))]),
                          ModelTurn(calls=[ToolCall("size", "size_allocation", json.dumps({"position_id": "fund", "min_weight": "0.55", "max_weight": "0.65", "reason": "Diversified exposure offers a stronger tradeoff than the unresolved company."}))]),
                          ModelTurn(answer=allocation_answer())])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), financial=FakeFinancialProvider(allocation_evidence()), discovery=discovery)).post("/api/analyze", json=allocation_request())
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"]["maximum"] == "2400"


def test_missing_stock_citations_suppress_every_rendered_allocation_amount():
    response, _, _ = candidate_journey(1, size=True, citations=[])
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"] is None
    assert response.json()["allocation"]["amount"] is None


def test_allocation_can_cite_both_serious_companies_and_size_one():
    response, _, _ = candidate_journey(2, size=True, citations=[
        "candidate-1-document-1", "candidate-1-document-2",
        "candidate-2-document-1", "candidate-2-document-2"])
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"]["maximum"] == "1600"


def test_screen_preserves_source_capture_separately_from_request_time(monkeypatch, tmp_path):
    captured = "2026-09-30T20:00:00Z"
    source = tmp_path / "discovery.json"
    source.write_text(json.dumps({"scanned_at": captured, "as_of": "2026-09-30", "source": "Reviewed fixture", "candidates": []}))
    monkeypatch.setenv("DISCOVERY_REFERENCE_FILE", str(source))
    response, _ = run_allocation()
    assert response.status_code == 200, response.text
    scan = response.json()["allocation"]["scan"]
    assert scan["source_captured_at"] == captured
    assert datetime.fromisoformat(scan["scanned_at"]) > datetime.fromisoformat(captured)
    assert any("source capture" in issue for issue in scan["issues"])
