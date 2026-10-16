"""Replays real model output saved from live OpenRouter stock-analysis runs (DeepSeek V4.1 Flash, Nemotron) through the
complete analysis pipeline over mocked HTTP. Each saved run once ended in HTTP 502 or an unusable answer; the
replay checks that valid tool arguments are calculated rather than rejected for their wording."""
import json
from pathlib import Path

import pytest

from tests.test_openai_boundary import openrouter_message, openrouter_response
from tests.test_stock_openrouter_e2e import AS_OF, SEQUENCE, Model, msft_request, run

RUNS = json.loads((Path(__file__).parent / "fixtures" / "saved_openrouter_runs.json").read_text(encoding="utf-8"))


def saved_model(run_name: str) -> Model:
    """Answers each forced tool with the saved run's own arguments; the final answer is the saved one when it was kept."""
    saved = RUNS[run_name]
    model = Model(cad_request())
    model.arguments["calculate_company_cases"] = json.dumps(saved["company_cases"][0])
    if saved["comparison"]:
        model.arguments["calculate_comparison"] = json.dumps(saved["comparison"][0])
    if saved["answers"]:
        answer = json.dumps(saved["answers"][0])
        model.script = {6: lambda: openrouter_response(openrouter_message(answer), model="deepseek/deepseek-v4.1-flash")}
    return model


def cad_request() -> dict:
    """The live runs' request: a USD holding reported in CAD with a supplied dated rate."""
    request = msft_request()
    request["portfolio"]["reporting_currency"] = "CAD"
    request["portfolio"]["fx"] = [{"from_currency": "USD", "to_currency": "CAD", "rate": "1.38", "as_of": AS_OF, "source": "Supplied FX"}]
    return request


def forced(model: Model) -> list:
    return [p["tool_choice"]["name"] if isinstance(p.get("tool_choice"), dict) else p.get("tool_choice") for p in model.sent]


@pytest.mark.parametrize("run_name", ["deepseek_run2", "deepseek_run3", "deepseek_run4", "nemotron_run1"])
def test_saved_run_completes_every_required_tool_and_returns_a_result(monkeypatch, run_name):
    model = saved_model(run_name)
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    result = response.json()
    # Every required tool ran once, in order, then the model answered.
    assert forced(model)[:5] == SEQUENCE
    assert result["stock"] is not None and result["comparison"] is not None
    assert result["recommendation"]["preferred_action"]


def test_saved_deepseek_case_wording_is_calculated_on_the_first_call(monkeypatch):
    """Run 4 described its exit multiple and reinvestment; the cases were rejected twice for wording and the run failed."""
    model = saved_model("deepseek_run4")
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert forced(model).count("calculate_company_cases") == 1
    assert all(case["terminal_price"] is not None for case in response.json()["stock"]["cases"])


def test_saved_nemotron_cases_without_fact_ids_explain_what_is_missing(monkeypatch):
    """Nemotron sent no usable fact IDs: the cases stay unknown and the answer says what is missing instead of failing."""
    response = run(monkeypatch, saved_model("nemotron_run1"))
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(case["terminal_price"] is None for case in result["stock"]["cases"])
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_answer_that_still_fails_after_correction_keeps_the_calculated_results(monkeypatch):
    """A final answer whose wording fails twice no longer discards the completed research, cases and comparison."""
    bad = json.dumps({**json.loads(json.dumps(RUNS["deepseek_run3"]["answers"][0])),
                      "reason": "There is a high probability that Microsoft recovers."})
    model = saved_model("deepseek_run3")
    model.script = {6: lambda: openrouter_response(openrouter_message(bad)), 7: lambda: openrouter_response(openrouter_message(bad))}
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["stock"]["cases"] and result["comparison"] is not None
    recommendation = result["recommendation"]
    assert recommendation["preferred_action"] == "wait_for_inputs"
    assert "probability" not in json.dumps(recommendation).lower()
    assert len(model.sent) == 7



def test_company_without_any_primary_research_explains_what_is_missing(monkeypatch):
    """No filings or issuer material: every tool still runs, and the answer names the missing evidence instead of HTTP 502."""
    research = {**json.loads((Path(__file__).parent / "fixtures" / "microsoft_research.json").read_text(encoding="utf-8")),
                "documents": [], "facts": []}
    model = saved_model("deepseek_run3")
    response = run(monkeypatch, model, research=research)
    assert response.status_code == 200, response.text
    assert forced(model)[:5] == SEQUENCE
    result = response.json()
    assert all(case["terminal_price"] is None for case in result["stock"]["cases"])
    recommendation = result["recommendation"]
    assert recommendation["preferred_action"] == "wait_for_inputs"
    assert "Company evidence" in recommendation["reason"]
