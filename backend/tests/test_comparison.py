import json

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ModelTurn, ScriptedModel, ToolCall
from tests.test_analysis import recommendation, snapshot


def comparison_request():
    portfolio = snapshot()
    portfolio["positions"][0].update(kind="etf", etf_role="diversified")
    return {
        "question": "Compare the broad fund, cash and retaining my holdings",
        "portfolio": portfolio,
        "comparison": {
            "scope_position_ids": ["p1", "c1"],
            "alternatives": [
                {"id": "fund", "kind": "etf", "position_id": "p1"},
                {"id": "cash", "kind": "cash", "position_id": "c1"},
                {"id": "keep", "kind": "no_action", "position_id": None},
            ],
            "fund_facts": [{"position_id": "p1", "as_of": "2026-09-30",
                "source": "User supplied sponsor facts", "source_url": None,
                "exposure": "Diversified global equity", "annual_cost": "0",
                "income_yield": "0.02"}],
            "effects": [],
        },
    }


def driver(position_id, *, cash=False, returns="0", reinvest=True):
    return {"position_id": position_id, "annual_returns": None if cash else [returns] * 5,
            "return_basis": "price_only", "cost_basis": "gross",
            "annual_rates": ["0.04", "0.03", "0.02", "0.01", "0"] if cash else None,
            "income_multipliers": None if cash else ["1"] * 5,
            "reinvest": reinvest, "fx_multipliers": ["1"] * 5}


def case_drivers(drivers, name):
    result = json.loads(json.dumps(drivers))
    for row in result:
        if row["annual_returns"] is not None:
            row["annual_returns"] = [{"downside": "-0.1", "base": "0", "upside": "0.1"}[name]] * 5
        if row["annual_rates"] is not None and name != "base":
            row["annual_rates"] = (["0"] if name == "downside" else ["0.05"]) * 5
    return result


def judgments():
    return {"alternatives": [{"alternative_id": key, "cases": [
        {"name": name, "drivers": case_drivers(drivers, name),
         "assumptions": [{"downside": "Weak equity exposure and lower reinvestment rates are conditional judgments.",
                          "base": "Steady equity exposure and moderating reinvestment rates are conditional judgments.",
                          "upside": "Stronger equity exposure and sustained reinvestment rates are conditional judgments."}[name]],
         "downside": "Equity losses and currency changes can impair capital.",
         "uncertainty": ["Future income and reinvestment rates may differ."]}
        for name in ["downside", "base", "upside"]]}
        for key, drivers in [("fund", [driver("p1")]), ("cash", [driver("c1", cash=True)]),
                             ("keep", [driver("p1"), driver("c1", cash=True)])]]}


def run_comparison(request=None, paths=None, *, skip=False):
    turns = [ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")])]
    if not skip:
        turns.append(ModelTurn(calls=[ToolCall("comparison", "calculate_comparison", json.dumps(paths or judgments()))]))
    turns.append(ModelTurn(answer=recommendation()))
    model = ScriptedModel(turns)
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json=request or comparison_request())
    return response, model


def test_selected_alternatives_receive_python_cases_on_same_dated_currency_basis():
    response, model = run_comparison()
    assert response.status_code == 200, response.text
    result = response.json()
    comparison = result["comparison"]
    assert comparison["as_of"] == "2026-09-30"
    assert comparison["reporting_currency"] == "CAD"
    assert comparison["starting_value"] == "1950"
    fund, cash, keep = comparison["alternatives"]
    assert [row["name"] for row in fund["cases"]] == ["downside", "base", "upside"]
    assert fund["cases"][0]["known_terminal_value"] == "1285.20897024"
    assert fund["cases"][2]["known_terminal_value"] == "3436.56628224"
    # 1950 grows by a reinvested 2% income yield for five years.
    assert fund["cases"][1]["known_terminal_value"] == "2152.95756624"
    # Falling rate path: 1950 * 1.04 * 1.03 * 1.02 * 1.01 * 1.
    assert cash["cases"][1]["known_terminal_value"] == "2151.922968"
    # No action retains 1300 CAD of USD fund and 650 CAD cash.
    assert keep["cases"][1]["known_terminal_value"] == "2152.61270016"
    assert keep["position_ids"] == ["p1", "c1"]
    assert fund["cases"][1]["terminal_value"] is None  # costs/taxes unknown
    assert "calculate_comparison" in str(model.requests[-1])
    assert result["recommendation"]["amount"] is None
    assert "probability" not in json.dumps(comparison)


def test_total_return_income_and_net_fund_cost_are_counted_once():
    request = comparison_request()
    request["comparison"]["fund_facts"][0].update(annual_cost="0.1", income_yield="0.2")
    request["comparison"]["effects"] = [{"alternative_id": "fund", "transaction_cost": "50",
        "terminal_tax": "100", "as_of": "2026-09-30", "source": "User supplied effects"}]
    paths = judgments()
    for case in paths["alternatives"][0]["cases"]:
        case["drivers"][0].update(annual_returns=["0.1"] * 5, return_basis="total_return",
                                  cost_basis="net_of_fund_cost", income_multipliers=None)
    response, _ = run_comparison(request, paths)
    assert response.status_code == 200, response.text
    case = response.json()["comparison"]["alternatives"][0]["cases"][1]
    # 1900 * 1.1^5 - 100; no extra income or expense deductions.
    assert case["terminal_value"] == "2959.969"


def test_gross_fund_expense_applies_after_income_reinvestment():
    request = comparison_request()
    request["comparison"]["fund_facts"][0].update(annual_cost="0.1", income_yield="0.1")
    response, _ = run_comparison(request)
    assert response.status_code == 200, response.text
    # 1950 * (1.1 * .9)^5, before unknown transaction/tax effects.
    assert response.json()["comparison"]["alternatives"][0]["cases"][1]["known_terminal_value"] == "1854.430597305"


def test_only_selected_short_bill_alternative_is_calculated_without_forcing_a_fund():
    request = comparison_request()
    request["comparison"]["alternatives"] = [request["comparison"]["alternatives"][1]]
    request["comparison"]["alternatives"][0]["kind"] = "short_bill"
    paths = {"alternatives": [judgments()["alternatives"][1]]}
    for case in paths["alternatives"][0]["cases"]:
        case["drivers"][0]["reinvest"] = False
    response, _ = run_comparison(request, paths)
    assert response.status_code == 200, response.text
    comparison = response.json()["comparison"]
    assert len(comparison["alternatives"]) == 1
    assert comparison["alternatives"][0]["selection"]["kind"] == "short_bill"
    assert comparison["alternatives"][0]["cases"][1]["known_terminal_value"] == "2145"


@pytest.mark.parametrize("fault", ["missing_scope", "duplicate_scope", "wrong_kind", "unselected_effect", "duplicate_alternative"])
def test_invalid_comparison_selection_is_rejected_before_reasoning(fault):
    request = comparison_request()
    comparison = request["comparison"]
    if fault == "missing_scope":
        comparison["scope_position_ids"] = ["invented"]
    elif fault == "duplicate_scope":
        comparison["scope_position_ids"].append("p1")
    elif fault == "wrong_kind":
        comparison["alternatives"][0]["position_id"] = "p2"
    elif fault == "unselected_effect":
        comparison["effects"] = [{"alternative_id": "unselected", "as_of": "2026-09-30", "source": "User"}]
    else:
        comparison["alternatives"][1]["id"] = "fund"
    response, model = run_comparison(request)
    assert response.status_code == 422
    assert not model.requests


@pytest.mark.parametrize("reinvest,expected", [(True, "3925.618125"), (False, "3461.25")])
def test_unreinvested_income_converts_at_each_year_fx_and_remains_idle_cash(reinvest, expected):
    paths = judgments()
    for case in paths["alternatives"][0]["cases"]:
        case["drivers"][0].update(reinvest=reinvest, fx_multipliers=["1", "1", "1", "1", "1.25"])
    request = comparison_request()
    request["comparison"]["fund_facts"][0]["income_yield"] = "0.1"
    response, _ = run_comparison(request, paths)
    assert response.status_code == 200, response.text
    case = response.json()["comparison"]["alternatives"][0]["cases"][1]
    assert case["known_terminal_value"] == expected


@pytest.mark.parametrize("missing", ["income", "cost", "old_facts", "old_fx", "retained_stock"])
def test_missing_facts_and_stock_no_action_are_explicitly_unquantified(missing):
    request = comparison_request()
    if missing == "income":
        request["comparison"]["fund_facts"][0]["income_yield"] = None
    elif missing == "cost":
        request["comparison"]["fund_facts"][0]["annual_cost"] = None
    elif missing == "old_facts":
        request["comparison"]["fund_facts"][0]["as_of"] = "2026-09-29"
    elif missing == "old_fx":
        request["portfolio"]["fx"][0]["as_of"] = "2026-09-29"
    else:
        request["comparison"]["scope_position_ids"].append("p2")
    paths = judgments()
    if missing == "retained_stock":
        for case in paths["alternatives"][2]["cases"]:
            case["drivers"].append(driver("p2"))
    response, _ = run_comparison(request, paths)
    assert response.status_code == 200, response.text
    comparison = response.json()["comparison"]
    assert comparison["alternatives"][0]["cases"][0]["terminal_value"] is None
    if missing == "old_fx":
        assert comparison["starting_value"] is None
        assert all(row["cases"][0]["known_terminal_value"] is None for row in comparison["alternatives"])
    if missing == "retained_stock":
        assert comparison["alternatives"][2]["cases"][0]["known_terminal_value"] is None
        assert "Retained stock outcomes are unknown" in str(comparison)
    assert "unknown" in str(comparison)


@pytest.mark.parametrize("fault", ["omit_tool", "extra_alternative", "duplicate_case", "missing_component",
    "income_twice", "same_currency_fx", "probability", "hurdle", "invent_facts", "nan", "short_path"])
def test_invalid_judgments_cannot_complete_a_comparison(fault):
    paths = judgments()
    case = paths["alternatives"][0]["cases"][0]
    if fault == "extra_alternative":
        paths["alternatives"][0]["alternative_id"] = "unselected"
    elif fault == "duplicate_case":
        case["name"] = "base"
    elif fault == "missing_component":
        paths["alternatives"][2]["cases"][0]["drivers"].pop()
    elif fault == "income_twice":
        case["drivers"][0]["return_basis"] = "total_return"
    elif fault == "same_currency_fx":
        paths["alternatives"][1]["cases"][0]["drivers"][0]["fx_multipliers"][0] = "1.1"
    elif fault == "probability":
        case["assumptions"] = ["The base case has a probability of fifty percent."]
    elif fault == "hurdle":
        case["assumptions"] = ["This must earn 20% annually."]
    elif fault == "invent_facts":
        paths["fund_facts"] = []
    elif fault == "nan":
        case["drivers"][0]["annual_returns"][0] = "NaN"
    elif fault == "short_path":
        case["drivers"][0]["annual_returns"].pop()
    response, _ = run_comparison(paths=paths, skip=fault == "omit_tool")
    assert response.status_code == 502, response.text
    assert "comparison" not in response.json()
