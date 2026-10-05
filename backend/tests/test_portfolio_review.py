import json

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from analyst.research import ReviewedResearchProvider
from analyst.schemas import CompanyResearch
from tests.test_analysis import recommendation, snapshot
from tests.test_stock import company_judgments, research_fixture, stock_comparison_judgments


def review_research_fixture():
    source = research_fixture()
    for doc in source["documents"]:
        doc["excerpt"] += " Current orders softened and management lowered its demand outlook, challenging the earlier resilience assumption."
    return source


def review_request():
    return {"question": "Review the whole portfolio after weaker demand and a falling price.",
            "portfolio": snapshot(), "portfolio_review": {"prior_theses": [
                {"company_id": "acme", "as_of": "2025-12-31", "thesis": "Demand should remain resilient."}]}}


def review_answer():
    return {**recommendation(), "preferred_action": "reduce",
            "reason": "Weaker current demand warrants a conditional reduction despite prior ownership.",
            "alternatives": [{"action": "no_action", "reason": "Retaining exposure is conditional on demand resilience improving; it retains the existing concentration risk."}],
            "downside": "Operating-demand weakness, valuation compression and concentrated exposure can impair capital.",
            "assumptions": ["Cases are conditional judgments using the dated whole portfolio and current primary excerpts."],
            "uncertainty": ["Uncovered listing outcomes, indirect overlap and future operating or valuation paths remain qualified."],
            "what_could_change": ["Changed demand evidence, revised margin paths or supplied personal context could change the view."],
            "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}


def assessment():
    return {"position_id": "p1", "judgments": company_judgments(), "assessment": {
        "position_id": "p1", "status": "changed", "action": "reduce",
        "current_thesis": "Weaker operating demand challenges the resilience thesis.",
        "change_reason": "Demand evidence challenges prior assumptions independently of the falling price.",
        "downside": "Demand weakness and valuation compression could impair capital.",
        "what_could_change": ["Improving demand and supported margins could restore the case."],
        "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}}


def run_review(request=None, judgment=None, answer=None, research=None):
    request = request or review_request()
    # Whole-portfolio scope, with a US company alternative and actual cash/no action.
    comparison = {"scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
                  "alternatives": [{"id": "company-p1", "kind": "stock", "position_id": "p1"},
                                   {"id": "cash", "kind": "cash", "position_id": "c1"},
                                   {"id": "keep", "kind": "no_action", "position_id": None}]}
    bound = {**request, "comparison": comparison}
    final = answer or review_answer()
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(judgment or assessment()))]),
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
        ModelTurn(answer=final)])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(research or review_research_fixture())}))).post("/api/analyze", json=request)
    return response, model


def test_whole_portfolio_review_reunderwrites_against_bound_evidence_without_targets():
    response, model = run_review()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["total_value"] == "3600"
    assert result["portfolio"]["cash_value"] == "1300"
    assert result["portfolio"]["baseline"] is None
    review = result["reunderwriting"]
    assert review["assessments"][0]["status"] == "changed"
    assert review["assessments"][0]["action"] == "reduce"
    assert review["stocks"][0]["cases"][1]["terminal_price"] == "100"
    assert result["comparison"]["starting_value"] == "3600"
    assert result["recommendation"]["preferred_action"] == "reduce"
    assert result["recommendation"]["amount"] is None
    assert "Demand should remain resilient" in str(model.requests[-1])
    assert "review-p1-filing" in str(model.requests[-1])
    assert review["qualifications"]


def test_supplied_baseline_and_reduction_preview_show_actual_post_weights():
    request = review_request()
    request["settings"] = {"single_company_cap": "0.5", "active_budget": "0.9",
                           "cash_is_deliberate_tilt": False, "indirect_cap_policy": "direct_only",
                           "baseline": {"stocks": "0.5", "cash": "0.5"}}
    request["proposed_changes"] = {"new_cash": [], "trades": [
        {"position_id": "p1", "shares_change": "-5", "cash_position_id": "c2"}]}
    # Same account/currency cash is necessary; use the brokerage US holding.
    request["portfolio"]["positions"][0]["account_id"] = "broker"
    response, _ = run_review(request)
    assert response.status_code == 200, response.text
    result = response.json()
    check = result["portfolio"]["guardrails"]["companies"][0]
    assert check["status"] == "breached"
    assert check["reduction_to_cash"] == "500"
    preview = result["proposals"][0]
    assert preview["status"] == "within_limits"
    assert preview["post_cash_value"] == "1950"
    assert preview["guardrails"]["companies"][0]["current_weight"] == "0.45833333"
    assert preview["guardrails"]["baseline_comparison"][0]["baseline_weight"] == "0.5"
    assert result["recommendation"]["preferred_action"] == "reduce"


def test_no_prior_thesis_cannot_claim_changed_or_unchanged():
    request = review_request()
    request["portfolio_review"]["prior_theses"] = []
    response, _ = run_review(request)
    assert response.status_code == 502


def test_unknown_current_facts_do_not_support_direction():
    source = research_fixture()
    source["facts"][0]["value"] = None
    request = review_request()
    from analyst.schemas import AnalysisRequest
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("p", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("t", "reunderwrite_holding", json.dumps(assessment()))]),
        ModelTurn(calls=[ToolCall("c", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
        ModelTurn(answer={**recommendation(), "evidence_ids": []})])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(source)}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reunderwriting"]["assessments"][0]["status"] == "unknown"
    assert result["reunderwriting"]["assessments"][0]["action"] == "wait_for_inputs"
    assert result["reunderwriting"]["stocks"][0]["cases"][0]["terminal_price"] is None
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"


def sizing_review_request():
    request = review_request()
    portfolio = request["portfolio"]
    portfolio["positions"] = [portfolio["positions"][0], portfolio["positions"][3]]
    portfolio["positions"][0]["account_id"] = "broker"
    portfolio["reporting_currency"] = "USD"
    request["portfolio_review"]["risk_context"] = "Can tolerate equity losses; no near-term withdrawals."
    request["settings"] = {"single_company_cap": "0.6", "active_budget": "0.8", "indirect_cap_policy": "direct_only", "cash_is_deliberate_tilt": False}
    request["comparison"] = {"scope_position_ids": ["p1", "c2"], "alternatives": [
        {"id": "company-p1", "kind": "stock", "position_id": "p1"},
        {"id": "cash", "kind": "cash", "position_id": "c2"},
        {"id": "keep", "kind": "no_action", "position_id": None}],
        "effects": [{"alternative_id": "company-p1", "transaction_cost": "0", "terminal_tax": "0",
                     "as_of": "2026-09-30", "source": "Explicit fixture effects"}]}
    return request


def run_sized_review(request=None, sizing=None):
    from analyst.financial_data import FakeFinancialProvider
    from analyst.schemas import FinancialEvidence
    from tests.test_freshness import evidence_fixture
    request = request or sizing_review_request()
    final = {**review_answer(),
             "reason": "Weaker demand supports a conditional reduction after comparing company, cash and retained exposure.",
             "assumptions": ["Current primary evidence informs the judged exposure range.", "Supplied loss/withdrawal context supports considering an amount." if request["portfolio_review"].get("risk_context") else "Loss/withdrawal context is missing, so the amount remains undetermined."],
             "uncertainty": ["Operating paths and exit valuation remain conditional; unknown sizing inputs stay visible in the review."]}
    turns = [ModelTurn(calls=[ToolCall(name, name, json.dumps(args))]) for name, args in [
        ("review_portfolio", {}), ("reunderwrite_holding", assessment()),
        ("calculate_comparison", stock_comparison_judgments(request)),
        ("size_review", sizing or {"position_id": "p1", "cash_position_id": "c2", "min_weight": "0.4", "max_weight": "0.5",
                                   "reason": "Weaker demand justifies lower issuer exposure while retaining a reserve."})]]
    model = ScriptedModel([*turns, ModelTurn(answer=final)])
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        financial=FakeFinancialProvider(FinancialEvidence.model_validate(evidence_fixture())),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    return response, model


def test_supported_reduction_range_uses_refreshed_marks_and_checks_both_outcomes():
    response, _ = run_sized_review()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["total_value"] == "1700"
    assert result["recommendation"]["amount"] == {"minimum": "350", "maximum": "520", "currency": "USD", "position_id": "p1"}
    review = result["reunderwriting"]
    assert len(review["previews"]) == 2
    assert all(row["status"] == "within_limits" for row in review["previews"])
    assert review["missing_inputs"] == []


@pytest.mark.parametrize("fault", ["risk", "cap", "budget", "cash", "cost", "tax", "nonzero_cost", "cap_breach", "oversell"])
def test_unsupported_sizing_remains_conditional_without_amount(fault):
    request = sizing_review_request()
    sizing = {"position_id": "p1", "cash_position_id": "c2", "min_weight": "0.4", "max_weight": "0.5", "reason": "Lower issuer exposure reflects weakening demand."}
    if fault == "risk":
        request["portfolio_review"]["risk_context"] = None
    elif fault in {"cap", "budget"}:
        del request["settings"]["single_company_cap" if fault == "cap" else "active_budget"]
    elif fault == "cash":
        request["portfolio"]["positions"][1]["account_id"] = "tfsa"
    elif fault in {"cost", "tax", "nonzero_cost"}:
        request["comparison"]["effects"][0]["transaction_cost" if fault != "tax" else "terminal_tax"] = "10" if fault == "nonzero_cost" else None
    elif fault == "cap_breach":
        request["settings"]["single_company_cap"] = "0.3"
    else:
        # Another account holds the same issuer, so the chosen account cannot fund
        # removal of the entire aggregate issuer exposure.
        request["portfolio"]["positions"].append({**request["portfolio"]["positions"][0], "id": "other", "account_id": "tfsa", "shares": "100"})
        request["comparison"]["scope_position_ids"].append("other")
    response, _ = run_sized_review(request, sizing)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["amount"] is None
    assert result["reunderwriting"]["amount"] is None
    assert result["reunderwriting"]["missing_inputs"]


@pytest.mark.parametrize("status,action", [("unchanged", "hold"), ("changed", "exit"), ("unknown", "reduce")])
def test_current_evidence_drives_actions_independently_of_price_movement(status, action):
    request = review_request()
    request["portfolio"]["positions"][0]["mark"]["value"] = "50"
    chosen = assessment()
    chosen["assessment"].update(status=status, action=action)
    if status == "unchanged":
        chosen["assessment"].update(current_thesis="Current demand evidence still supports the operating case.", change_reason="The price decline alone does not invalidate the demand thesis.")
    if status == "unknown":
        request["portfolio_review"]["prior_theses"] = []
    final = {**recommendation(), "preferred_action": action,
             "reason": "Current operating evidence drives this conditional direction, independently of price movement.",
             "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}
    source = review_research_fixture()
    if status == "unchanged":
        for doc in source["documents"]:
            doc["excerpt"] = "Current orders and margins remain resilient; revenue and diluted shares are reported in the statements."
    response, _ = run_review(request, chosen, final, source)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["positions"][0]["value"] == "650"
    assert result["reunderwriting"]["assessments"][0]["status"] == status
    assert result["recommendation"]["preferred_action"] == action
    assert result["recommendation"]["amount"] is None
    assert result["reunderwriting"]["stocks"][0]["research"]["documents"][0]["excerpt"] == source["documents"][0]["excerpt"]


@pytest.mark.parametrize("fault", ["invented_evidence", "amount", "trade", "target", "omitted_evidence", "unsupported_add"])
def test_review_answer_cannot_invent_targets_trades_amounts_or_evidence(fault):
    final = {**recommendation(), "preferred_action": "reduce", "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}
    if fault == "invented_evidence":
        final["evidence_ids"].append("invented")
    elif fault == "amount":
        final["amount"] = {"minimum": "100", "maximum": "200", "currency": "USD", "position_id": "p1"}
    elif fault == "trade":
        final["reason"] = "We sold the holding and placed an order."
    elif fault == "target":
        final["reason"] = "Rebalance toward the target mix."
    elif fault == "omitted_evidence":
        final["evidence_ids"] = []
    else:
        final["alternatives"] = [{"action": "add", "reason": "The falling price justifies averaging down."}]
    response, _ = run_review(answer=final)
    assert response.status_code == 502, response.text


def test_current_cap_breach_does_not_allow_a_holding_level_hold_exception():
    request = review_request()
    request["settings"] = {"single_company_cap": "0.5", "active_budget": "0.9", "indirect_cap_policy": "direct_only", "cash_is_deliberate_tilt": False}
    judgment = assessment()
    judgment["assessment"]["action"] = "hold"
    response, _ = run_review(request, judgment)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reunderwriting"]["assessments"][0]["action"] == "reduce"
    assert "prior ownership is not an exception" in result["reunderwriting"]["assessments"][0]["change_reason"]
    assert result["portfolio"]["guardrails"]["companies"][0]["reduction_to_cash"] == "500"



def test_two_distinct_companies_get_independent_thesis_cases_and_namespaced_evidence():
    from analyst.schemas import AnalysisRequest
    request = review_request()
    request["portfolio"]["positions"][1].update(listing="XNYS", currency="USD", ticker="OTHER", company_id="other", company_name="Other company")
    request["portfolio_review"]["prior_theses"].append({"company_id": "other", "as_of": "2025-12-31", "thesis": "Steady demand."})
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    other = review_research_fixture()
    other["company_id"] = "other"
    for doc in other["documents"]:
        doc["company_id"] = "other"
    second = assessment()
    second["position_id"] = second["assessment"]["position_id"] = "p2"
    second["assessment"]["evidence_ids"] = ["review-p2-filing", "review-p2-issuer"]
    turns = [ModelTurn(calls=[ToolCall(name, tool, json.dumps(args))]) for name, tool, args in [
        ("p", "review_portfolio", {}), ("first", "reunderwrite_holding", assessment()),
        ("second", "reunderwrite_holding", second), ("c", "calculate_comparison", stock_comparison_judgments(bound))]]
    turns.append(ModelTurn(answer={**recommendation(), "preferred_action": "reduce", "evidence_ids": ["review-p1-filing", "review-p1-issuer", "review-p2-filing", "review-p2-issuer"]}))
    response = TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture()), "other": CompanyResearch.model_validate(other)}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["reunderwriting"]["assessments"]) == 2
    assert len(result["reunderwriting"]["stocks"]) == 2
    assert result["comparison"]["starting_value"] == "3900"
    keep = result["comparison"]["alternatives"][-1]
    assert keep["cases"][1]["known_terminal_value"] == "3384.615312"


def test_cash_only_review_completes_without_company_tools_or_prerequisites():
    from analyst.schemas import AnalysisRequest
    request = {"question": "Review current cash exposure.", "portfolio": snapshot(), "portfolio_review": {}}
    request["portfolio"]["positions"] = [request["portfolio"]["positions"][2]]
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("p", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("c", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
        ModelTurn(answer={**recommendation(), "preferred_action": "no_action", "evidence_ids": []})])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reunderwriting"]["stocks"] == []
    assert result["comparison"]["starting_value"] == "650"
    assert result["recommendation"]["preferred_action"] == "no_action"



def test_review_loads_configured_backend_primary_research(monkeypatch, tmp_path):
    from analyst.schemas import AnalysisRequest
    request = review_request()
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    reference = tmp_path / "primary.json"
    reference.write_text(json.dumps({"acme": review_research_fixture()}), encoding="utf-8")
    monkeypatch.setenv("RESEARCH_REFERENCE_FILE", str(reference))
    turns = [ModelTurn(calls=[ToolCall(name, name, json.dumps(args))]) for name, args in [
        ("review_portfolio", {}), ("reunderwrite_holding", assessment()),
        ("calculate_comparison", stock_comparison_judgments(bound))]]
    turns.append(ModelTurn(answer={**recommendation(), "preferred_action": "reduce", "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}))
    response = TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider())).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["reunderwriting"]["stocks"][0]["research"]["documents"][0]["id"] == "review-p1-filing"



def test_same_listing_held_across_accounts_reuses_cases_for_each_retained_position():
    request = review_request()
    request["portfolio"]["positions"][1].update(listing="XNAS", currency="USD", mark={"value": "100", "as_of": "2026-09-30", "source": "Broker display"})
    response, _ = run_review(request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result["reunderwriting"]["research"]) == 1
    assert len(result["reunderwriting"]["assessments"]) == 1
    assert {row["position_id"] for row in result["reunderwriting"]["stocks"]} == {"p1", "p2"}
    keep = result["comparison"]["alternatives"][-1]
    assert keep["cases"][1]["known_terminal_value"] == "3384.615312"



def test_sizing_rationale_cannot_invent_a_baseline_target():
    sizing = {"position_id": "p1", "cash_position_id": "c2", "min_weight": "0.4", "max_weight": "0.5", "reason": "Restore the target mix."}
    response, _ = run_sized_review(sizing=sizing)
    assert response.status_code == 502


@pytest.mark.parametrize("baseline", [{}, {"cash": None}, {"cash": "0.4"}])
def test_empty_or_partial_baseline_does_not_authorize_an_invented_complete_mix(baseline):
    request = review_request()
    request["settings"] = {"baseline": baseline}
    final = {**recommendation(), "preferred_action": "reduce", "reason": "Rebalance toward the target mix.", "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}
    response, _ = run_review(request, answer=final)
    assert response.status_code == 502
