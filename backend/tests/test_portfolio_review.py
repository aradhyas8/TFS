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
    assert "Company cases could not be calculated" in result["reunderwriting"]["assessments"][0]["change_reason"]
    # The unresolved holding no longer forces the portfolio answer; the model's own synthesis stands.
    assert result["recommendation"]["preferred_action"] == "review_only"


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


def test_reunderwriting_normalizes_unknown_in_numeric_fields_and_preserves_strict_schemas():
    holding_judgment = assessment()
    for case in holding_judgment["judgments"]["cases"]:
        case["growth"] = ["unknown", "unknown", "unknown", "unknown", "unknown"]
        case["margins"] = ["unknown", "unknown", "unknown", "unknown", "unknown"]
        case["discount_rate"] = "unknown"
        case["exit_multiple"] = "n/a"
    response, _ = run_review(judgment=holding_judgment)
    assert response.status_code == 200, response.text
    result = response.json()
    assert "reunderwriting" in result
    stocks = result["reunderwriting"]["stocks"]
    assert len(stocks) == 1
    # Check that cases were calculated using normalized baseline numbers and uncertainty was recorded
    cases = stocks[0]["cases"]
    assert any("normalized" in u.lower() or "missing" in u.lower() for c in cases for u in c["judgment"]["uncertainty"])


def test_reunderwriting_maps_position_id_to_comparison_alternative_id():
    request = review_request()
    comparison = {
        "scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
        "alternatives": [
            {"id": "company-p1", "kind": "stock", "position_id": "p1"},
            {"id": "cash", "kind": "cash", "position_id": "c1"},
            {"id": "keep", "kind": "no_action", "position_id": None},
        ],
    }
    bound = {**request, "comparison": comparison}
    # Pass "p1" as alternative_id instead of "company-p1"
    paths = stock_comparison_judgments(bound)
    for alt in paths["alternatives"]:
        if alt["alternative_id"] == "company-p1":
            alt["alternative_id"] = "p1"
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(paths))]),
        ModelTurn(answer=review_answer()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None
    # Check that comparison_inputs were exposed in reunderwrite_holding output
    thesis_output = json.loads(model.requests[2][-1]["output"])
    assert "comparison_inputs" in thesis_output
    assert any(a["id"] == "company-p1" for a in thesis_output["comparison_inputs"]["alternatives"])


def test_reunderwriting_maps_company_id_and_kind_to_comparison_alternative_id():
    request = review_request()
    comparison = {
        "scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
        "alternatives": [
            {"id": "company-p1", "kind": "stock", "position_id": "p1"},
            {"id": "cash", "kind": "cash", "position_id": "c1"},
            {"id": "keep", "kind": "no_action", "position_id": None},
        ],
    }
    bound = {**request, "comparison": comparison}
    paths = stock_comparison_judgments(bound)
    for alt in paths["alternatives"]:
        if alt["alternative_id"] == "company-p1":
            # Test model sending company_id ("company-acme") and driver position_id "acme"
            alt["alternative_id"] = "company-acme"
            for c in alt["cases"]:
                for d in c["drivers"]:
                    d["position_id"] = "acme"
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(paths))]),
        ModelTurn(answer=review_answer()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None


def test_reunderwriting_reprompts_if_model_answers_before_calculate_comparison():
    request = review_request()
    comparison = {
        "scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
        "alternatives": [
            {"id": "company-p1", "kind": "stock", "position_id": "p1"},
            {"id": "cash", "kind": "cash", "position_id": "c1"},
            {"id": "keep", "kind": "no_action", "position_id": None},
        ],
    }
    bound = {**request, "comparison": comparison}
    paths = stock_comparison_judgments(bound)
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
        # Premature answer before calculate_comparison
        ModelTurn(answer=review_answer()),
        # Reprompted turn: now calls calculate_comparison
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(paths))]),
        ModelTurn(answer=review_answer()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None
    assert any("You MUST call calculate_comparison" in str(msg) for msg in model.requests[-2])


def test_reunderwriting_review_portfolio_is_idempotent():
    request = review_request()
    comparison = {
        "scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
        "alternatives": [
            {"id": "company-p1", "kind": "stock", "position_id": "p1"},
            {"id": "cash", "kind": "cash", "position_id": "c1"},
            {"id": "keep", "kind": "no_action", "position_id": None},
        ],
    }
    bound = {**request, "comparison": comparison}
    paths = stock_comparison_judgments(bound)
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
        # Model calls review_portfolio again
        ModelTurn(calls=[ToolCall("portfolio2", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(paths))]),
        ModelTurn(answer=review_answer()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None


def test_validate_review_baseline_distinguishes_qualifications_from_claims():
    from analyst.pipeline import InvalidReview, validate_review_baseline
    from analyst.schemas import AnalysisRequest

    req = AnalysisRequest.model_validate(review_request())
    # Qualifying statements must pass without raising InvalidReview
    validate_review_baseline("Rebalancing toward a target allocation requires a user-supplied baseline.", req)
    validate_review_baseline("Target-relative rebalancing requires a user-supplied baseline.", req)
    validate_review_baseline("Rebalancing to target weights is unsupplied and absent.", req)

    # Unqualified target claims without a baseline must fail closed
    with pytest.raises(InvalidReview, match="Target-relative rebalancing requires a user-supplied baseline"):
        validate_review_baseline("We should rebalance to target weights.", req)
    with pytest.raises(InvalidReview, match="Target-relative rebalancing requires a user-supplied baseline"):
        validate_review_baseline("Move portfolio to target mix.", req)


def test_reunderwriting_forced_tool_forces_reunderwrite_holding_after_premature_answer():
    """Regression: when the model answers before reunderwrite_holding, forced_tool ensures
    the next turn is forced to reunderwrite_holding, completing the workflow without loop exhaustion.
    Previously WF5 would exhaust the 12-turn budget; now forced_tool + increased budget (16) prevents that."""
    request = review_request()
    comparison = {
        "scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
        "alternatives": [
            {"id": "company-p1", "kind": "stock", "position_id": "p1"},
            {"id": "cash", "kind": "cash", "position_id": "c1"},
            {"id": "keep", "kind": "no_action", "position_id": None},
        ],
    }
    bound = {**request, "comparison": comparison}
    paths = stock_comparison_judgments(bound)
    # Simulate model answering prematurely before reunderwrite_holding.
    # With forced_tool this must still succeed within the turn budget.
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        # Premature answer before reunderwrite_holding
        ModelTurn(answer=review_answer()),
        # Forced reprompt → reunderwrite_holding (scripted model pops next turn regardless)
        ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(paths))]),
        ModelTurn(answer=review_answer()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None
    # Confirm the reprompt message told the model to call reunderwrite_holding with numeric field warning
    reprompt_messages = model.requests[2]  # Messages seen before the 3rd model call
    assert any(
        "reunderwrite_holding" in str(msg) and "numeric" in str(msg)
        for msg in reprompt_messages
    )


def bad_cash_conversion():
    # earnings_exit with an FCF-only driver: calculate_company_cases rejects it.
    bad = assessment()
    for case in bad["judgments"]["cases"]:
        case["cash_conversion"] = ["0.8"] * 5
    return bad


def run_review_turns(thesis_calls):
    request = review_request()
    comparison = {"scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
                  "alternatives": [{"id": "company-p1", "kind": "stock", "position_id": "p1"},
                                   {"id": "cash", "kind": "cash", "position_id": "c1"},
                                   {"id": "keep", "kind": "no_action", "position_id": None}]}
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        *[ModelTurn(calls=[ToolCall(f"thesis-{index}", "reunderwrite_holding", json.dumps(args))]) for index, args in enumerate(thesis_calls)],
        ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(stock_comparison_judgments({**request, "comparison": comparison})))]),
        ModelTurn(answer=review_answer())])
    return TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json={**request, "comparison": comparison})


def test_a_rejected_holding_judgment_is_returned_once_to_correct():
    response = run_review_turns([bad_cash_conversion(), assessment()])
    assert response.status_code == 200, response.text
    assert [row["position_id"] for row in response.json()["reunderwriting"]["assessments"]] == ["p1"]


def test_a_holding_judgment_rejected_twice_still_fails_closed():
    assert run_review_turns([bad_cash_conversion(), bad_cash_conversion()]).status_code == 502


def test_a_rejected_review_answer_is_returned_once_to_correct():
    request = review_request()
    comparison = {"scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
                  "alternatives": [{"id": "company-p1", "kind": "stock", "position_id": "p1"},
                                   {"id": "cash", "kind": "cash", "position_id": "c1"},
                                   {"id": "keep", "kind": "no_action", "position_id": None}]}
    invented = {**review_answer(), "evidence_ids": ["review-p1-filing", "quote-p1"]}
    def run(*answers):
        model = ScriptedModel([
            ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
            ModelTurn(calls=[ToolCall("thesis", "reunderwrite_holding", json.dumps(assessment()))]),
            ModelTurn(calls=[ToolCall("compare", "calculate_comparison", json.dumps(stock_comparison_judgments({**request, "comparison": comparison})))]),
            *[ModelTurn(answer=answer) for answer in answers]])
        return TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
            {"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json={**request, "comparison": comparison})
    corrected = run(invented, review_answer())
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["recommendation"]["evidence_ids"] == ["review-p1-filing", "review-p1-issuer"]
    assert run(invented, invented).status_code == 502


def test_an_unresolved_holding_does_not_block_a_decision_on_the_others():
    request = review_request()
    request["portfolio"]["positions"].append({"id": "p9", "account_id": "tfsa", "kind": "stock", "ticker": "BANK", "listing": "XNYS",
        "company_id": "bank", "company_name": "Bank", "shares": "1", "currency": "USD", "mark": {"value": "40", "as_of": "2026-09-30", "source": "Broker display"}})
    bank = json.loads(json.dumps(review_research_fixture()).replace('"acme"', '"bank"'))
    bank["facts"][0]["value"] = None  # its cases can't be calculated
    comparison = {"scope_position_ids": [row["id"] for row in request["portfolio"]["positions"]],
                  "alternatives": [{"id": "company-p1", "kind": "stock", "position_id": "p1"},
                                   {"id": "cash", "kind": "cash", "position_id": "c1"},
                                   {"id": "keep", "kind": "no_action", "position_id": None}]}
    bound = {**request, "comparison": comparison}
    unresolved = assessment()
    unresolved["position_id"] = unresolved["assessment"]["position_id"] = "p9"
    unresolved["assessment"].update(action="hold", status="unknown", evidence_ids=["review-p9-filing"])
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("p", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("t1", "reunderwrite_holding", json.dumps(assessment()))]),
        ModelTurn(calls=[ToolCall("t9", "reunderwrite_holding", json.dumps(unresolved))]),
        ModelTurn(calls=[ToolCall("c", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
        ModelTurn(answer={**review_answer(), "evidence_ids": ["review-p1-filing"]})])  # one supporting document is enough
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture()), "bank": CompanyResearch.model_validate(bank)}))).post("/api/analyze", json=bound)
    assert response.status_code == 200, response.text
    result = response.json()
    assert {row["position_id"]: row["action"] for row in result["reunderwriting"]["assessments"]} == {"p1": "reduce", "p9": "wait_for_inputs"}
    assert result["recommendation"]["preferred_action"] == "reduce"
    # The final synthesis was told which holdings are resolved, which aren't, their weights and why.
    holdings = json.loads(next(item["output"] for item in model.requests[-1] if item.get("type") == "function_call_output" and item["call_id"] == "c"))["holdings"]
    assert [row["position_id"] for row in holdings["resolved"]] == ["p1"]
    assert holdings["unresolved"][0]["position_id"] == "p9"
    assert holdings["unresolved"][0]["company_weight"] is not None
    assert "left unchanged pending evidence" in holdings["unresolved"][0]["why"]


def test_a_single_sec_filing_supports_a_rebalance_holding():
    one_source = assessment()
    one_source["assessment"]["evidence_ids"] = ["review-p1-filing"]
    response, _ = run_review(judgment=one_source, answer={**review_answer(), "evidence_ids": ["review-p1-filing"]})
    assert response.status_code == 200, response.text
    assert response.json()["reunderwriting"]["assessments"][0]["action"] == "reduce"
    assert response.json()["recommendation"]["preferred_action"] == "reduce"


def test_an_unsized_add_stays_directional_without_cash():
    added = assessment()
    added["assessment"].update(action="add", current_thesis="Demand evidence supports a larger position.")
    response, _ = run_review(judgment=added, answer={**review_answer(), "preferred_action": "add", "reason": "Current demand evidence supports adding, funded by trimming elsewhere."})
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "add"
    assert response.json()["recommendation"]["amount"] is None


def two_company_review(thesis_calls):
    """Acme (p1) and Other (p2), each with distinctive research, re-underwritten through the given scripted calls."""
    from analyst.schemas import AnalysisRequest
    request = review_request()
    request["portfolio"]["positions"][1].update(listing="XNYS", currency="USD", ticker="OTHER", company_id="other", company_name="Other company")
    request["portfolio_review"]["prior_theses"].append({"company_id": "other", "as_of": "2025-12-31", "thesis": "Steady demand."})
    bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
    other = review_research_fixture()
    other["company_id"] = "other"
    for doc in other["documents"]:
        doc["company_id"] = "other"
        doc["excerpt"] = "Other company excerpt marker."
    model = ScriptedModel([ModelTurn(calls=[ToolCall("p", "review_portfolio", "{}")]),
                           *[ModelTurn(calls=[ToolCall(f"t{index}", "reunderwrite_holding", json.dumps(args))]) for index, args in enumerate(thesis_calls)],
                           ModelTurn(calls=[ToolCall("c", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]),
                           ModelTurn(answer={**recommendation(), "preferred_action": "reduce", "evidence_ids": ["review-p1-filing", "review-p2-filing"]})])
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=ReviewedResearchProvider(
        {"acme": CompanyResearch.model_validate(review_research_fixture()), "other": CompanyResearch.model_validate(other)}))).post("/api/analyze", json=request)
    return response, model


def second_assessment():
    second = assessment()
    second["position_id"] = second["assessment"]["position_id"] = "p2"
    second["assessment"]["evidence_ids"] = ["review-p2-filing", "review-p2-issuer"]
    return second


def test_python_forces_each_required_holding_once_in_queue_order():
    response, model = two_company_review([assessment(), second_assessment()])
    assert response.status_code == 200, response.text
    assert [row["position_id"] for row in response.json()["reunderwriting"]["assessments"]] == ["p1", "p2"]
    # review_portfolio, then one forced reunderwrite_holding per queued holding, then the forced comparison.
    assert model.forced_tools[1:4] == ["reunderwrite_holding", "reunderwrite_holding", "calculate_comparison"]


def test_a_holding_out_of_queue_order_is_returned_once_and_counts_as_its_correction():
    response, _ = two_company_review([second_assessment(), assessment(), second_assessment()])
    assert response.status_code == 200, response.text
    assert [row["position_id"] for row in response.json()["reunderwriting"]["assessments"]] == ["p1", "p2"]
    # The required holding's single correction is spent: a second out-of-order call fails closed.
    assert two_company_review([second_assessment(), second_assessment()])[0].status_code == 502


def test_each_turn_carries_only_the_next_holdings_research_and_normalized_results():
    response, model = two_company_review([assessment(), second_assessment()])
    assert response.status_code == 200, response.text
    first, second, comparison, final = (json.dumps(model.requests[index]) for index in (1, 2, 3, 4))
    assert "orders softened" in first and "Other company excerpt marker" not in first
    # p1's raw research is not replayed once its normalized result replaces it in the call history.
    assert "Other company excerpt marker" in second and "orders softened" not in second
    for later in (comparison, final):
        assert "orders softened" not in later and "Other company excerpt marker" not in later
    # Every completed step stays visible as a tool call with its normalized output, so none is redone.
    calls = [item["name"] for item in model.requests[4] if item.get("type") == "function_call"]
    assert calls == ["review_portfolio", "reunderwrite_holding", "reunderwrite_holding", "calculate_comparison"]
    outputs = [json.loads(item["output"]) for item in model.requests[4] if item.get("type") == "function_call_output"]
    assert "next_holding" not in outputs[0] and outputs[1]["position_id"] == "p1" and outputs[1]["cases"]
    assert outputs[2]["available_evidence_ids"] == ["review-p2-filing", "review-p2-issuer"]


@pytest.mark.parametrize("prose", [
    "The tool therefore leaves all holdings unchanged pending evidence.",
    "All holdings stay unchanged pending evidence.",
    "Keeping all holdings while evidence is gathered retains the current concentration.",
    "Everything else in the portfolio remains unchanged.",
])
def test_retaining_all_holdings_is_valid_prose(prose):
    from analyst.pipeline import validate_prose
    validate_prose(prose)
    validate_prose(prose, stock=True, explanatory=True)


@pytest.mark.parametrize("prose", [
    "Invest all your cash in Broadcom.",
    "Put everything into Shopify.",
    "Sell all your holdings and wait.",
    "Move all of the portfolio into cash.",
    "All your savings should go into one bank.",
    "Buy 10 shares of Ford.",
    "Add 30% to Broadcom.",
    "Add thirty percent to Broadcom.",
    # Known gap closed: a trade size reached through its object, which no workflow produced or validated.
    "Reduce Broadcom by 25%.",
    "Trim Shopify to 5% of the portfolio.",
    "Sell a quarter of your Broadcom shares.",
    "Sell half of your holdings.",
    "Sell 25% in FY2025.",
    "Returns are guaranteed.",
    "There is a high probability of gains.",
])
def test_blanket_sized_and_guaranteed_language_is_still_rejected(prose):
    from analyst.pipeline import InvalidReview, validate_prose
    for mode in ({}, {"stock": True}, {"stock": True, "explanatory": True}):
        with pytest.raises(InvalidReview):
            validate_prose(prose, **mode)


@pytest.mark.parametrize("prose", [
    "Revenue grew in the latest quarter.",
    "The half-year filing reports lower free cash flow.",
    "Ford reported a net loss in FY2025.",
    "Margins improved in Q3 2026 versus the prior quarter.",
    "The 10-K for fiscal 2025 and the 6-K dated 2026-07-31 report earnings.",
    "Quarterly results were mixed and the first half showed weaker demand.",
])
def test_descriptive_fiscal_periods_do_not_consume_a_correction(prose):
    from analyst.pipeline import validate_prose
    for mode in ({}, {"stock": True}, {"stock": True, "explanatory": True}):
        validate_prose(prose, **mode)


@pytest.mark.parametrize("prose", ["Tariffs could reduce margins by two percent.", "Buybacks could reduce the share count by three percent.",
                                   "Management may reduce operating expenses by five percent."])
def test_company_operating_changes_are_not_position_sizes(prose):
    from analyst.pipeline import validate_prose
    validate_prose(prose, stock=True, explanatory=True)


def test_a_model_withheld_direction_with_valid_citations_is_not_reported_as_uncited():
    waiting = assessment()
    waiting["assessment"].update(action="wait_for_inputs", status="unknown")
    request = review_request()
    request["portfolio_review"]["prior_theses"] = []
    response, _ = run_review(request, waiting, {**review_answer(), "preferred_action": "review_only", "evidence_ids": []})
    assert response.status_code == 200, response.text
    reason = response.json()["reunderwriting"]["assessments"][0]["change_reason"]
    assert "did not cite" not in reason and "cites available evidence" in reason
