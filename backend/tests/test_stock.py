import copy
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from tests.test_analysis import recommendation, snapshot


def stock_request():
    request = {"question": "Should I hold Acme in my portfolio?", "portfolio": snapshot(),
               "stock": {"position_id": "p1"}}
    return request


def test_named_stock_requires_primary_research_and_company_cases_before_answering():
    model = ScriptedModel([
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(answer=recommendation()),
    ])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json=stock_request())
    assert response.status_code == 502, response.text
    assert "stock" not in response.json()


def research_fixture():
    documents = [{"id": key, "authority": authority, "company_id": "acme",
                  "url": url, "published_on": "2026-02-01", "as_of": "2025-12-31",
                  "title": title, "excerpt": "Revenue and diluted shares are reported in the consolidated statements.",
                  "available": True, "qa_available": False}
                 for key, authority, url, title in [
                     ("filing", "sec", "https://www.sec.gov/Archives/edgar/data/123/456/acme.htm", "Acme annual filing"),
                     ("issuer", "issuer", "https://investors.example.com/annual", "Acme annual release")]]
    facts = [{"id": key, "metric": key, "value": value, "unit": unit, "currency": currency,
              "period_start": "2025-01-01", "period_end": "2025-12-31",
              "definition": definition, "document_ids": ["filing", "issuer"],
              "filing_checked": True, "notes_checked": True, "custom_tags_checked": True,
              "segments_checked": True}
             for key, value, unit, currency, definition in [
                 ("revenue", "1000", "currency", "USD", "Consolidated annual revenue in USD, without scaling"),
                 ("shares", "10", "shares", None, "Diluted weighted average shares, without scaling")]]
    return {"company_id": "acme", "sector": "industrial", "cyclical": True,
            "documents": documents, "facts": facts, "issues": []}


def company_judgments():
    return {"method": "earnings_exit", "revenue_fact_id": "revenue", "shares_fact_id": "shares",
            "metric_fact_id": None, "mid_cycle_context": "Margins represent a conditional mid-cycle level rather than a peak.",
            "cases": [{"name": name, "growth": ["0"] * 5, "margins": [margin] * 5,
                       "cash_conversion": ["1"] * 5, "reinvestment": ["0"] * 5,
                       "dilution": ["0"] * 5, "payout": ["0"] * 5, "return_on_equity": None,
                       "fx_multipliers": ["1"] * 5, "discount_rate": "0.1",
                       "exit_multiple": "10", "exit_sensitivity": ["8", "10", "12"],
                       "assumptions": ["Stable revenue and conditional margins drive the exit value."],
                       "uncertainty": ["Operating demand and exit valuation may differ."]}
                      for name, margin in [("downside", "0.05"), ("base", "0.1"), ("upside", "0.15")]]}


def stock_answer():
    answer = recommendation()
    answer.update(preferred_action="hold", reason="Hold conditionally while the company cases are assessed against cash and existing issuer concentration.",
                  alternatives=[{"action": "reduce", "reason": "A conditional reduction could lower company exposure."},
                                {"action": "no_action", "reason": "Retaining the snapshot avoids an unconfirmed transaction."}],
                  evidence_ids=["filing", "issuer"])
    return answer


def run_stock(*, research=None, paths=None, request=None, answer=None, skip=None):
    from analyst.research import ReviewedResearchProvider
    from analyst.stock import CompanyResearch
    source = ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research or research_fixture())})
    turns = [ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")])]
    for key, name, arguments in [("sec", "get_sec_filings", {}), ("issuer", "get_issuer_material", {}),
                                  ("company", "calculate_company_cases", paths or company_judgments())]:
        if skip != name:
            turns.append(ModelTurn(calls=[ToolCall(key, name, json.dumps(arguments))]))
    from analyst.schemas import AnalysisRequest
    bound = AnalysisRequest.model_validate(request or stock_request()).model_dump(mode="json")
    turns.append(ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(stock_comparison_judgments(bound)))]))
    turns.append(ModelTurn(answer=answer or stock_answer()))
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post(
        "/api/analyze", json=request or stock_request())
    return response, model


def test_stock_research_calculations_and_conditional_action_complete_in_same_request():
    response, model = run_stock()
    assert response.status_code == 200, response.text
    result = response.json()
    stock = result["stock"]
    assert stock["research"]["facts"][0]["period_end"] == "2025-12-31"
    assert stock["research"]["facts"][0]["definition"].startswith("Consolidated")
    assert stock["cases"][0]["terminal_price"] == "50"
    assert stock["cases"][1]["terminal_price"] == "100"
    assert stock["cases"][2]["terminal_price"] == "150"
    assert stock["cases"][1]["known_terminal_value"] == "1300"
    assert stock["cases"][1]["sensitivity_prices"] == ["80", "100", "120"]
    assert stock["cases"][1]["required_exit_multiple"] == "16.11"  # price 100 x 1.1^5 / year-5 EPS 10
    assert result["recommendation"]["preferred_action"] == "hold"
    assert result["recommendation"]["amount"] is None
    assert "filing" in str(model.requests[-1])
    assert stock["as_of"] == "2026-09-30" and stock["reporting_currency"] == "CAD"


@pytest.mark.parametrize("fault", ["missing", "conflicting", "unit", "currency", "period", "notes", "custom_tags", "segments", "future", "unavailable", "mid_cycle"])
def test_missing_or_contradictory_company_facts_remain_unknown(fault):
    source = research_fixture()
    paths = company_judgments()
    fact = source["facts"][0]
    if fault == "missing":
        fact["value"] = None
    elif fault == "conflicting":
        source["facts"].append({**fact, "id": "conflict", "value": "2000"})
    elif fault == "unit":
        fact["unit"] = "shares"
    elif fault == "currency":
        fact["currency"] = "CAD"
    elif fault == "period":
        fact["period_start"] = "2025-10-01"
    elif fault in {"notes", "custom_tags", "segments"}:
        fact[f"{fault}_checked"] = False
    elif fault == "future":
        source["documents"][0]["published_on"] = "2026-10-01"
    elif fault == "unavailable":
        source["documents"][0]["available"] = False
    else:
        paths["mid_cycle_context"] = None
    answer = stock_answer()
    if fault in {"future", "unavailable"}:
        answer["evidence_ids"] = ["issuer"]
    response, _ = run_stock(research=source, paths=paths, answer=answer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(case["terminal_price"] is None for case in result["stock"]["cases"])
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["recommendation"]["amount"] is None


@pytest.mark.parametrize("fault", ["amount", "probability", "invented_evidence", "unavailable_qa", "executed", "omitted_issuer", "omitted_cases", "extra_fact", "duplicate_case", "wrong_sector"])
def test_stock_answer_cannot_bypass_evidence_or_output_contract(fault):
    answer = stock_answer()
    source = research_fixture()
    paths = company_judgments()
    skip = None
    if fault == "amount":
        answer["amount"] = "1000"
    elif fault == "probability":
        answer["reason"] = "The upside case has a probability of fifty percent."
    elif fault == "invented_evidence":
        answer["evidence_ids"] = ["imaginary"]
    elif fault == "unavailable_qa":
        answer["reason"] = "Transcript Q&A was reviewed and confirms the thesis."
    elif fault == "executed":
        answer["reason"] = "We bought the company for your account."
    elif fault == "omitted_issuer":
        skip = "get_issuer_material"
    elif fault == "omitted_cases":
        skip = "calculate_company_cases"
    elif fault == "extra_fact":
        paths["revenue"] = "1000"
    elif fault == "duplicate_case":
        paths["cases"][0]["name"] = "base"
    else:
        source["sector"] = "financial"
    response, _ = run_stock(answer=answer, research=source, paths=paths, skip=skip)
    assert response.status_code == 502, response.text
    assert "recommendation" not in response.json()


def test_stock_direction_cannot_waive_existing_company_cap():
    request = stock_request()
    request["settings"] = {"single_company_cap": "0.5", "active_budget": "0.9",
                           "cash_is_deliberate_tilt": False, "indirect_cap_policy": "direct_only"}
    answer = stock_answer()
    answer.update(preferred_action="add", reason="Strong conviction permits an exception to configured limits.")
    response, _ = run_stock(request=request, answer=answer)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["portfolio"]["guardrails"]["companies"][0]["status"] == "breached"
    assert result["recommendation"]["preferred_action"] == "review_only"
    assert "Strong conviction" not in json.dumps(result["recommendation"])


def stock_comparison_request():
    request = stock_request()
    request["portfolio"]["positions"].append({
        "id": "fund", "account_id": "broker", "kind": "etf", "ticker": "BROAD", "listing": "XNYS",
        "currency": "USD", "shares": "0", "etf_role": "diversified",
        "mark": {"value": "100", "as_of": "2026-09-30", "source": "Broker display"}})
    request["comparison"] = {"scope_position_ids": ["p1", "c1"], "alternatives": [
        {"id": "company", "kind": "stock", "position_id": "p1"},
        {"id": "fund", "kind": "etf", "position_id": "fund"},
        {"id": "cash", "kind": "cash", "position_id": "c1"},
        {"id": "keep", "kind": "no_action", "position_id": None}],
        "fund_facts": [{"position_id": "fund", "as_of": "2026-09-30", "source": "Sponsor fixture",
                        "exposure": "Broad equity", "annual_cost": "0", "income_yield": "0"}], "effects": []}
    return request


def stock_comparison_judgments(request):
    from tests.test_comparison import driver
    rows = {row["id"]: row for row in request["portfolio"]["positions"]}
    alternatives = []
    for alt in request["comparison"]["alternatives"]:
        ids = request["comparison"]["scope_position_ids"] if alt["kind"] == "no_action" else [alt["position_id"]]
        drivers = [driver(key, cash=rows[key]["kind"] == "cash") for key in ids]
        for row in drivers:
            if rows[row["position_id"]]["kind"] == "stock":
                row.update(annual_returns=None, income_multipliers=None, reinvest=False)
        alternatives.append({"alternative_id": alt["id"], "cases": [
            {"name": name, "drivers": copy.deepcopy(drivers), "assumptions": ["Paths are conditional judgments."],
             "downside": "Operating losses or falling rates may impair capital.",
             "uncertainty": ["Future income and valuation may differ."]} for name in ["downside", "base", "upside"]]})
    return {"alternatives": alternatives}


def test_stock_etf_cash_and_actual_no_action_share_common_capital_cases():
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    turns = [ModelTurn(calls=[ToolCall(key, name, json.dumps(args))]) for key, name, args in [
        ("portfolio", "review_portfolio", {}), ("sec", "get_sec_filings", {}),
        ("issuer", "get_issuer_material", {}), ("company", "calculate_company_cases", company_judgments()),
        ("comparison", "calculate_comparison", stock_comparison_judgments(request))]]
    turns.append(ModelTurn(answer=stock_answer()))
    response = TestClient(create_app(model=ScriptedModel(turns), data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()["comparison"]
    assert result["starting_value"] == "1950"
    company, fund, cash, keep = result["alternatives"]
    assert company["cases"][0]["known_terminal_value"] == "975"
    assert company["cases"][1]["known_terminal_value"] == "1950"
    assert fund["cases"][1]["known_terminal_value"] == "1950"
    assert cash["cases"][1]["known_terminal_value"] == "2151.922968"
    assert keep["cases"][1]["known_terminal_value"] == "2017.307656"
    assert keep["position_ids"] == ["p1", "c1"]
    assert all(alt["cases"][1]["terminal_value"] is None for alt in result["alternatives"])


@pytest.mark.parametrize("sector,method,metric,value,expected", [
    ("industrial", "fcf_exit", "revenue", "1000", "60"),
    ("financial", "book_exit", "book_value", "200", "200"),
    ("reit", "ffo_exit", "ffo", "50", "50"),
])
def test_sector_appropriate_company_methods_use_reported_metric(sector, method, metric, value, expected):
    source = research_fixture()
    source["sector"] = sector
    source["facts"][0].update(metric=metric, value=value)
    paths = company_judgments()
    paths["method"] = method
    if method != "fcf_exit":
        paths.update(revenue_fact_id=None, metric_fact_id="revenue")
    for case in paths["cases"]:
        case.update(margins=["0.1" if method == "fcf_exit" else "1"] * 5,
                    cash_conversion=["0.8" if method == "fcf_exit" else "1"] * 5,
                    reinvestment=["0.25" if method == "fcf_exit" else "0"] * 5,
                    return_on_equity=["0"] * 5 if method == "book_exit" else None)  # zero ROE: book value stays flat
    response, _ = run_stock(research=source, paths=paths)
    assert response.status_code == 200, response.text
    assert response.json()["stock"]["cases"][1]["terminal_price"] == expected


def test_company_cash_conversion_reinvestment_and_idle_distributions_convert_once():
    paths = company_judgments()
    paths["method"] = "fcf_exit"
    for case in paths["cases"]:
        case.update(margins=["0.1"] * 5, cash_conversion=["0.8"] * 5,
                    reinvestment=["0.25"] * 5, payout=["0.5"] * 5,
                    fx_multipliers=["1", "1", "1", "1", "1.2"])
    # Comparison drivers must share the company FX path, so exercise the same API
    # with an explicitly selected cash alternative; company values remain in result.
    request = stock_request()
    request["comparison"] = {"scope_position_ids": ["p1"],
                             "alternatives": [{"id": "cash", "kind": "cash", "position_id": "c1"}]}
    response, _ = run_stock(paths=paths, request=request)
    assert response.status_code == 200, response.text
    case = response.json()["stock"]["cases"][1]
    assert case["terminal_metric"] == "60"
    assert case["terminal_price"] == "60"
    # FCF/share = 6; distribution = 3/year. FX = 1.3; last-year factor = 1.2.
    # Terminal price 60 * 1.3 * 1.2 plus idle distributions 3 * 1.3 * 5.2.
    assert case["known_terminal_value"] == "1138.8"


def test_dilution_changes_terminal_shares_and_exit_sensitivity():
    paths = company_judgments()
    for case in paths["cases"]:
        case["dilution"] = ["1", "0", "0", "0", "0"]
    response, _ = run_stock(paths=paths)
    assert response.status_code == 200, response.text
    case = response.json()["stock"]["cases"][1]
    assert case["terminal_shares"] == "20"
    assert case["terminal_price"] == "50"
    assert case["required_exit_multiple"] == "32.21"  # price 100 x 1.1^5 / year-5 EPS 5
    assert case["sensitivity_prices"] == ["40", "50", "60"]


def test_loss_making_downside_has_zero_equity_exit_and_no_negative_dividend():
    paths = company_judgments()
    paths["cases"][0].update(margins=["-0.1"] * 5, payout=["0.5"] * 5)
    response, _ = run_stock(paths=paths)
    assert response.status_code == 200, response.text
    case = response.json()["stock"]["cases"][0]
    assert case["terminal_metric"] == "-100"
    assert case["terminal_price"] == "0"
    assert case["known_terminal_value"] == "0"
    assert case["required_exit_multiple"] is None


def test_financial_company_payout_uses_roe_earnings_instead_of_book_capital():
    source = research_fixture()
    source["sector"] = "financial"
    source["facts"][0].update(metric="book_value", value="200")
    paths = company_judgments()
    paths.update(method="book_exit", revenue_fact_id=None, metric_fact_id="revenue")
    for case in paths["cases"]:
        case.update(margins=["1"] * 5, payout=["0.5"] * 5, exit_multiple="1",
                    return_on_equity=["0.1"] * 5)
    response, _ = run_stock(research=source, paths=paths)
    assert response.status_code == 200, response.text
    case = response.json()["stock"]["cases"][1]
    # Book/share = 20; ROE 10% with half paid out retains 5%, so book/share compounds to 20 x 1.05^5 = 25.5256.
    # Dividends are half of each year's earnings on opening book: 1 + 1.05 + ... + 1.05^4 = 5.52563.
    # Exit at 1x book = 25.5256; (25.52563 + 5.52563) x ten shares x 1.3 CAD/USD = 403.67.
    assert case["terminal_price"] == "25.5256"
    assert case["known_terminal_value"] == "403.67"
    assert [year["book_growth"] for year in case["path"]] == ["0.05"] * 5 and case["path"][0]["retention"] == "0.5"


def test_missing_roe_does_not_turn_book_capital_into_dividends():
    source = research_fixture()
    source["sector"] = "financial"
    source["facts"][0].update(metric="book_value", value="200")
    paths = company_judgments()
    paths.update(method="book_exit", revenue_fact_id=None, metric_fact_id="revenue")
    for case in paths["cases"]:
        case.update(margins=["1"] * 5, payout=["0.5"] * 5)
    response, _ = run_stock(research=source, paths=paths)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stock"]["cases"][1]["known_terminal_value"] is None
    assert "Return on equity is unknown" in str(result["stock"]["cases"][1])
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_stock_analysis_turns_expose_comparison_inputs_and_align_drivers():
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    # Misalign the driver position_id to test orchestration normalization
    judgments = stock_comparison_judgments(request)
    for alt in judgments["alternatives"]:
        if alt["alternative_id"] == "company":
            for case in alt["cases"]:
                for driver in case["drivers"]:
                    driver["position_id"] = "guessed_company_id"
        elif alt["alternative_id"] == "cash":
            for case in alt["cases"]:
                for driver in case["drivers"]:
                    driver["position_id"] = "guessed_cash_id"

    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
        ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(company_judgments()))]),
        ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(judgments))]),
        ModelTurn(answer=stock_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert "comparison" in result
    assert result["comparison"]["starting_value"] == "1950"
    # Verify comparison_inputs were exposed to model before calculate_comparison turn
    company_turn_context = str(model.requests[-2])
    assert "comparison_inputs" in company_turn_context
    assert "p1" in company_turn_context
    assert "c1" in company_turn_context


def test_retained_stock_fx_is_synchronized_with_company_cases():
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    judgments = stock_comparison_judgments(request)
    # Intentionally vary the fx_multipliers in comparison judgments from the company cases
    for alt in judgments["alternatives"]:
        if alt["alternative_id"] == "keep":
            for case in alt["cases"]:
                for driver in case["drivers"]:
                    if driver["position_id"] == "p1":
                        driver["fx_multipliers"] = ["1.05", "1.10", "1.15", "1.20", "1.25"]

    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
        ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(company_judgments()))]),
        ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(judgments))]),
        ModelTurn(answer=stock_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert "comparison" in result
    keep_alt = next(a for a in result["comparison"]["alternatives"] if a["selection"]["kind"] == "no_action")
    p1_driver = next(d for d in keep_alt["cases"][1]["judgment"]["drivers"] if d["position_id"] == "p1")
    # FX multipliers were synchronized to match the company case (["1"] * 5)
    assert p1_driver["fx_multipliers"] == ["1", "1", "1", "1", "1"]


def test_stock_analysis_reprompts_if_model_answers_before_calculate_comparison():
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    judgments = stock_comparison_judgments(request)
    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer", "get_issuer_material", "{}")]),
        ModelTurn(calls=[ToolCall("company", "calculate_company_cases", json.dumps(company_judgments()))]),
        # Model prematurely answers without calling calculate_comparison
        ModelTurn(answer=stock_answer(), continuation=[{"role": "assistant", "content": "Premature answer"}]),
        # Model is reprompted by the pipeline and now calls calculate_comparison
        ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(judgments))]),
        ModelTurn(answer=stock_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None
    # Check that reprompt message was sent to the model
    reprompt_request = model.requests[5]
    assert any("Selected alternatives require calculated conditional cases before answering" in item.get("content", "") for item in reprompt_request)


def test_stock_analysis_idempotent_tool_calls_and_research_reprompt():
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    judgments = stock_comparison_judgments(request)
    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec1", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer1", "get_issuer_material", "{}")]),
        # Premature answer after primary research completed
        ModelTurn(answer=stock_answer(), continuation=[{"role": "assistant", "content": "I reviewed the filings."}]),
        # Reprompt should say primary research is already complete and instruct calculate_company_cases
        # Model repeats sec filing call and then proceeds with calculate_company_cases
        ModelTurn(calls=[ToolCall("sec2", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("company1", "calculate_company_cases", json.dumps(company_judgments()))]),
        # Duplicate company cases call is idempotent
        ModelTurn(calls=[ToolCall("company2", "calculate_company_cases", json.dumps(company_judgments()))]),
        # Comparison call
        ModelTurn(calls=[ToolCall("comparison1", "calculate_comparison", json.dumps(judgments))]),
        # Duplicate comparison call is idempotent
        ModelTurn(calls=[ToolCall("comparison2", "calculate_comparison", json.dumps(judgments))]),
        ModelTurn(answer=stock_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None
    # Check that reprompt message told the model that primary research is already complete
    reprompt_request = model.requests[4]
    assert any("Primary research is already complete. You MUST now call calculate_company_cases" in item.get("content", "") for item in reprompt_request)


def test_stock_forced_tool_forces_calculate_company_cases_after_multiple_premature_answers():
    """Regression: model giving multiple premature answers during stock analysis (WF2) must not
    exhaust the loop. forced_tool='calculate_company_cases' ensures the model is directed to the
    right tool on each reprompt turn. Previously this failed with 'Model exceeded the bounded
    portfolio review loop' after 12 turns. Now succeeds with increased budget (16) + forced_tool."""
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    request = stock_comparison_request()
    judgments = stock_comparison_judgments(request)
    # Simulate model answering prematurely 3 times before calling calculate_company_cases.
    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("sec1", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("issuer1", "get_issuer_material", "{}")]),
        # 3 consecutive premature answers — previously would exhaust 12-turn budget
        ModelTurn(answer=stock_answer()),
        ModelTurn(answer=stock_answer()),
        ModelTurn(answer=stock_answer()),
        # Finally calls the required tool on forced reprompt
        ModelTurn(calls=[ToolCall("company1", "calculate_company_cases", json.dumps(company_judgments()))]),
        ModelTurn(calls=[ToolCall("comparison1", "calculate_comparison", json.dumps(judgments))]),
        ModelTurn(answer=stock_answer()),
    ]
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}))).post(
        "/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["comparison"] is not None


def test_explanatory_stock_prose_may_quote_figures_and_periods():
    from analyst.pipeline import InvalidReview, validate_prose
    # Ordinary explanation of reported figures and periods is allowed in Stock Analysis prose.
    validate_prose("ROE was 14.3% for fiscal 2025 and 15.2% annualized in the latest quarter; book value per share is 65.14.", explanatory=True)
    validate_prose("A brief history of one quarter and one fiscal year cannot settle where the credit cycle stands.", stock=True, explanatory=True)
    validate_prose("Credit losses could reduce earnings by 10% in a downturn.", stock=True, explanatory=True)
    # Probabilities, guarantees, all-your-cash and numeric trade sizing still fail.
    for prose in ("The base case has a probability of fifty percent.", "Returns are guaranteed.", "Move all your cash into the bank.",
                  "Add 30% to the position.", "Sell half of the holding.", "Buy 100 shares."):
        with pytest.raises(InvalidReview):
            validate_prose(prose, stock=True, explanatory=True)
    # Outside Stock Analysis the strict rule is unchanged.
    with pytest.raises(InvalidReview):
        validate_prose("ROE was 14.3% in the latest quarter.")


@pytest.mark.parametrize("reason,ok", [
    ("No earnings-call transcript was available, so management tone is unknown.", True),
    ("A transcript could change the view on credit quality.", True),
    ("The transcript says management expects lower credit losses.", False),
    ("Transcript Q&A was reviewed and confirms the thesis.", False),
])
def test_transcript_mentions_versus_claims(reason, ok):
    answer = stock_answer()
    answer["reason"] = reason
    response, _ = run_stock(answer=answer)
    assert (response.status_code == 200) is ok, response.text


def test_stray_citation_is_removed_but_fabricated_evidence_fails():
    answer = stock_answer()
    answer["evidence_ids"] = [*answer["evidence_ids"], "quote-p1"]
    response, _ = run_stock(answer=answer)
    assert response.status_code == 200, response.text
    recommendation = response.json()["recommendation"]
    assert "quote-p1" not in recommendation["evidence_ids"] and recommendation["preferred_action"] == "hold"
    assert "A citation to a document not available to this analysis was removed." in recommendation["uncertainty"]


def test_stock_analysis_zero_share_candidate_with_no_cash():
    req = stock_request()
    # Filter out cash rows
    req["portfolio"]["positions"] = [p for p in req["portfolio"]["positions"] if p["kind"] != "cash"]
    # Change p1 to a zero-share candidate row
    candidate = {
        "id": "candidate-acme-xnas",
        "account_id": "tfsa",
        "kind": "stock",
        "ticker": "ACME",
        "listing": "XNAS",
        "company_id": "acme",
        "company_name": "Acme",
        "shares": "0",
        "currency": "USD",
        "mark": {"value": "100", "as_of": "2026-09-30", "source": "Broker display"},
    }
    req["portfolio"]["positions"] = [candidate, *[p for p in req["portfolio"]["positions"] if p["id"] != "p1"]]
    req["stock"] = {"position_id": "candidate-acme-xnas"}

    response, model = run_stock(request=req)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stock"] is not None
    assert result["comparison"] is not None
    assert Decimal(result["comparison"]["starting_value"]) == Decimal(0)
