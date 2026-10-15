"""End-to-end stock analysis through the production OpenRouter transport and WorkflowModelAdapter over mocked HTTP.

The research fixture is Microsoft's real SEC EDGAR evidence (10-K for the fiscal year ended 2026-06-30, recent 8-Ks,
the issuer earnings release and 23 CompanyFacts XBRL facts) captured from a live run; the portfolio is synthetic.
"""
import asyncio
import copy
import dataclasses
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import FakeDataProvider, OpenAIResponseTransport, WorkflowModelAdapter
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch
from tests.test_analysis import recommendation
from tests.test_openai_boundary import openrouter_message, openrouter_response, openrouter_transport
from tests.test_stock import stock_comparison_judgments

CIK = "CIK0000789019"
AS_OF = "2026-10-14"
TEN_K = "sec-10-k-0001193125-26-323660"
RELEASE = "issuer-8k-0001193125-26-323632"
SEQUENCE = ["review_portfolio", "get_sec_filings", "get_issuer_material", "calculate_company_cases", "calculate_comparison"]


def msft_research() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "microsoft_research.json").read_text(encoding="utf-8"))


def msft_request() -> dict:
    return {
        "question": "Should I keep my Microsoft position, or would a broad ETF or cash be better?",
        "portfolio": {
            "as_of": AS_OF, "reporting_currency": "USD",
            "accounts": [{"id": "taxable", "name": "Synthetic Taxable"}],
            "positions": [
                {"id": "msft", "account_id": "taxable", "kind": "stock", "ticker": "MSFT", "listing": "XNAS",
                 "company_id": CIK, "company_name": "Microsoft Corp", "shares": "20", "currency": "USD",
                 "mark": {"value": "500", "as_of": AS_OF, "source": "Broker display"}},
                {"id": "vti", "account_id": "taxable", "kind": "etf", "ticker": "VTI", "listing": "ARCX",
                 "currency": "USD", "shares": "0", "etf_role": "diversified",
                 "mark": {"value": "320", "as_of": AS_OF, "source": "Broker display"}},
                {"id": "cash", "account_id": "taxable", "kind": "cash", "cash": "3000", "currency": "USD"},
            ],
            "fx": [],
        },
        "stock": {"position_id": "msft"},
        "comparison": {
            "scope_position_ids": ["msft", "cash"],
            "alternatives": [
                {"id": "company", "kind": "stock", "position_id": "msft"},
                {"id": "fund", "kind": "etf", "position_id": "vti"},
                {"id": "cash", "kind": "cash", "position_id": "cash"},
                {"id": "keep", "kind": "no_action", "position_id": None}],
            "fund_facts": [{"position_id": "vti", "as_of": AS_OF, "source": "Synthetic sponsor facts",
                            "exposure": "Broad US equity", "annual_cost": "0.0003", "income_yield": "0.013"}],
            "effects": []},
    }


BASE = {"growth": "0.1", "margin": "0.36", "cash_conversion": "0.7", "payout": "0.25", "discount": "0.09", "exit": "25"}


def msft_judgments() -> dict:
    """What a capable model (DeepSeek V4.1 Flash) should supply: cited fact IDs and explained judgments only."""
    def case(name, growth, margin, conversion, multiple):
        return {"name": name, "growth": [growth] * 5, "margins": [margin] * 5, "cash_conversion": [conversion] * 5,
                "reinvestment": ["0"] * 5, "dilution": ["0"] * 5, "payout": [BASE["payout"]] * 5, "return_on_equity": None,
                "fx_multipliers": ["1"] * 5, "discount_rate": BASE["discount"], "exit_multiple": multiple,
                "exit_sensitivity": [str(int(multiple) - 5), multiple, str(int(multiple) + 5)],
                "assumptions": ["Cloud demand and capital intensity drive conditional free cash flow."],
                "uncertainty": ["Capital spending on data centres may not convert into proportional cash flow."]}
    return {"method": "fcf_exit", "revenue_fact_id": "revenue-fy-2026-06-30", "shares_fact_id": "shares-fy-2026-06-30",
            "metric_fact_id": None,
            "mid_cycle_context": "Current margins reflect a software and cloud mix rather than a cyclical peak.",
            "cases": [case("downside", "0.03", "0.3", "0.5", "18"),
                      case("base", BASE["growth"], BASE["margin"], BASE["cash_conversion"], BASE["exit"]),
                      case("upside", "0.15", "0.4", "0.85", "30")]}


def msft_answer() -> dict:
    answer = recommendation()
    answer.update(preferred_action="hold",
                  reason="Hold conditionally: the reported filings support durable cloud cash generation, while heavy capital spending keeps the cases wide.",
                  alternatives=[{"action": "reduce", "reason": "A conditional reduction could lower single-company exposure."},
                                {"action": "no_action", "reason": "Retaining the snapshot avoids an unconfirmed transaction."}],
                  evidence_ids=[TEN_K, RELEASE])
    return answer


def call(name, arguments="{}"):
    return openrouter_response([{"type": "function_call", "id": f"fc_{name}", "call_id": f"call_{name}", "name": name,
                                 "arguments": arguments, "status": "completed"}], model="deepseek/deepseek-v4.1-flash")


def incomplete():
    """What the live DeepSeek company-case turn returned: reasoning only, no tool call, no stated reason."""
    return httpx.Response(200, json={"id": "gen-x", "object": "response", "created_at": 1, "model": "deepseek/deepseek-v4.1-flash",
                                     "status": "incomplete", "incomplete_details": None, "error": None, "parallel_tool_calls": True,
                                     "output": [{"type": "reasoning", "id": "rs_1", "summary": [],
                                                 "content": [{"type": "reasoning_text", "text": "Thinking about margins..."}]}],
                                     "usage": {"input_tokens": 21509, "output_tokens": 14058, "total_tokens": 35567,
                                               "input_tokens_details": {"cached_tokens": 0},
                                               "output_tokens_details": {"reasoning_tokens": 14058}}})


class Model:
    """Obeys a named tool_choice with valid arguments and answers when the choice is left to it. `script` overrides
    the response for a given request number (1-based)."""

    def __init__(self, request: dict, script: dict | None = None):
        bound = AnalysisRequest.model_validate(request).model_dump(mode="json")
        self.arguments = {"calculate_company_cases": json.dumps(msft_judgments()),
                          "calculate_comparison": json.dumps(stock_comparison_judgments(bound))}
        self.script = script or {}
        self.sent: list[dict] = []

    def __call__(self, http_request):
        payload = json.loads(http_request.content)
        self.sent.append(payload)
        scripted = self.script.get(len(self.sent))
        if scripted is not None:
            return scripted() if callable(scripted) else scripted
        choice = payload.get("tool_choice")
        if isinstance(choice, dict):
            return call(choice["name"], self.arguments.get(choice["name"], "{}"))
        return openrouter_response(openrouter_message(json.dumps(msft_answer())), model="deepseek/deepseek-v4.1-flash")


def run(monkeypatch, model, *, research: dict | None = None, effort: str | None = "max", request: dict | None = None):
    transport, _ = openrouter_transport(monkeypatch, model, model="deepseek/deepseek-v4.1-flash")
    transport.settings = dataclasses.replace(transport.settings, reasoning_effort=effort)
    source = ReviewedResearchProvider({CIK: CompanyResearch.model_validate(research or msft_research())})
    app = create_app(model=WorkflowModelAdapter(transport), data=FakeDataProvider(), research=source)
    return TestClient(app).post("/api/analyze", json=request or msft_request())


def outputs(payload: dict) -> dict[str, dict]:
    """The tool outputs the model saw in one request, by tool name."""
    names = {item["call_id"]: item["name"] for item in payload["input"] if item.get("type") == "function_call"}
    return {names[item["call_id"]]: json.loads(item["output"]) for item in payload["input"] if item.get("type") == "function_call_output"}


def test_valid_company_cases_complete_with_correct_calculations_and_compact_context(monkeypatch):
    model = Model(msft_request())
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    result = response.json()
    # Backend-owned sequence: one request per mandatory step, then the final answer.
    assert [p.get("tool_choice", {}).get("name") if isinstance(p.get("tool_choice"), dict) else p.get("tool_choice") for p in model.sent] \
        == [*SEQUENCE, "auto"]
    # Bounded reasoning on tool turns; the configured effort is kept for the final analysis.
    assert [p.get("reasoning") for p in model.sent] == [{"effort": "medium"}] * 5 + [{"effort": "max"}]

    # Calculations are Python's: recompute the base case independently from the reported facts and the judgments.
    facts = {row["id"]: Decimal(row["value"]) for row in msft_research()["facts"]}
    revenue, shares = facts["revenue-fy-2026-06-30"], facts["shares-fy-2026-06-30"]
    terminal_revenue = revenue * (1 + Decimal(BASE["growth"])) ** 5
    fcf = terminal_revenue * Decimal(BASE["margin"]) * Decimal(BASE["cash_conversion"])
    base = next(row for row in result["stock"]["cases"] if row["name"] == "base")
    assert Decimal(base["starting_metric"]) == revenue and Decimal(base["starting_shares"]) == shares
    assert Decimal(base["terminal_price"]) == (fcf * Decimal(BASE["exit"]) / shares).quantize(Decimal("0.0001"))
    assert all(row["terminal_price"] is not None for row in result["stock"]["cases"])
    assert result["comparison"] is not None
    # Only reported facts: the research in the result is exactly the bound SEC/issuer evidence.
    assert result["stock"]["research"]["facts"] == CompanyResearch.model_validate(msft_research()).model_dump(mode="json")["facts"]
    assert result["recommendation"]["preferred_action"] == "hold"
    assert set(result["recommendation"]["evidence_ids"]) == {TEN_K, RELEASE}

    # Model context: each fact once, compact, with a validated case-input summary before company cases.
    seen = outputs(model.sent[3])
    facts_sent = [row for out in seen.values() for row in out.get("facts", [])]
    assert len(facts_sent) == len(msft_research()["facts"])
    assert all("sources" not in row for row in facts_sent)
    inputs = seen["get_issuer_material"]["case_inputs"]
    assert inputs["revenue_fact_ids"][0] == "revenue-fy-2026-06-30" and inputs["shares_fact_ids"][0] == "shares-fy-2026-06-30"
    assert "mid_cycle_context" in json.dumps(inputs["requirements"])
    # The model is not sent back its own judgments or the research it has already seen.
    final = outputs(model.sent[-1])
    assert "judgments" not in final["calculate_company_cases"] and "research" not in final["calculate_company_cases"]
    assert "judgment" not in json.dumps(final["calculate_comparison"]["alternatives"][0]["cases"][0])


def test_incomplete_reasoning_without_tool_call_recovers_once_with_lower_effort(monkeypatch):
    model = Model(msft_request(), script={4: incomplete})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert [p.get("tool_choice", {}).get("name") for p in model.sent[3:5]] == ["calculate_company_cases"] * 2
    assert [p.get("reasoning") for p in model.sent[3:5]] == [{"effort": "medium"}, {"effort": "low"}]


def test_repeated_incomplete_response_fails_with_an_accurate_error_and_no_third_attempt(monkeypatch):
    model = Model(msft_request(), script={4: incomplete, 5: incomplete})
    response = run(monkeypatch, model)
    assert response.status_code == 502
    assert "incomplete" in response.json()["detail"] and "calculate_company_cases" in response.json()["detail"]
    assert len(model.sent) == 5


def test_incomplete_response_is_not_repeated_when_no_reasoning_effort_can_be_lowered(monkeypatch):
    model = Model(msft_request(), script={4: incomplete})
    response = run(monkeypatch, model, effort=None)
    assert response.status_code == 502 and "incomplete" in response.json()["detail"]
    assert len(model.sent) == 4 and all("reasoning" not in p for p in model.sent)


def test_answer_before_a_required_tool_is_reprompted_with_the_tool_forced(monkeypatch):
    model = Model(msft_request(), script={4: lambda: openrouter_response(openrouter_message(json.dumps(msft_answer())))})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert [p["tool_choice"]["name"] for p in model.sent[3:5]] == ["calculate_company_cases"] * 2


@pytest.mark.parametrize("fault", ["missing", "conflicting"])
def test_missing_or_conflicting_revenue_leaves_cases_unknown_and_withholds_a_direction(monkeypatch, fault):
    research = msft_research()
    revenue = next(row for row in research["facts"] if row["id"] == "revenue-fy-2026-06-30")
    if fault == "missing":
        research["facts"].remove(revenue)
    else:
        research["facts"].append({**copy.deepcopy(revenue), "id": "revenue-fy-2026-06-30-restated", "value": "300000000000"})
    model = Model(msft_request())
    response = run(monkeypatch, model, research=research)
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(row["terminal_price"] is None for row in result["stock"]["cases"])
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    # The summary the model saw already said revenue was unusable, so nothing was presented as reported.
    inputs = outputs(model.sent[3])["get_issuer_material"]["case_inputs"]
    assert "revenue-fy-2026-06-30" not in inputs["revenue_fact_ids"]


def test_invalid_company_case_arguments_are_returned_once_then_fail(monkeypatch):
    bad = msft_judgments()
    bad["cases"][0]["margins"] = ["2"] * 5  # margins above 100% are rejected by the schema
    model = Model(msft_request())
    model.arguments["calculate_company_cases"] = json.dumps(bad)
    response = run(monkeypatch, model)
    assert response.status_code == 502
    company_turns = [p for p in model.sent if isinstance(p.get("tool_choice"), dict) and p["tool_choice"]["name"] == "calculate_company_cases"]
    assert len(company_turns) == 2
    assert "rejected" in outputs(company_turns[1])["calculate_company_cases"]["error"]


def test_unsupported_provider_parameter_names_the_cause(monkeypatch):
    body = {"error": {"message": "No endpoints found that can handle the requested parameters. To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection",
                      "code": 404, "metadata": {"routing_funnel": [{"step": "Initial Endpoints", "endpoint_count": 1}],
                                                "failed_routing_step": "Filter by Parameters"}}}
    model = Model(msft_request(), script={1: httpx.Response(404, json=body)})
    response = run(monkeypatch, model)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "does not support" in detail and "try again later" not in detail
    assert len(model.sent) == 1


def test_analysis_stops_at_its_execution_deadline(monkeypatch):
    monkeypatch.setattr("analyst.api.ANALYZE_DEADLINE_SECONDS", 0.5)
    model = Model(msft_request())
    started: list[float] = []

    async def slow(request):
        started.append(time.monotonic())
        await asyncio.sleep(5)
        return model(request)

    response, elapsed = None, time.monotonic()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("analyst.providers.AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow))))
        transport = OpenAIResponseTransport(Settings("sk-or-secret", "deepseek/deepseek-v4.1-flash",
                                                     base_url="https://openrouter.ai/api/v1", provider="openrouter"))
        source = ReviewedResearchProvider({CIK: CompanyResearch.model_validate(msft_research())})
        app = create_app(model=WorkflowModelAdapter(transport), data=FakeDataProvider(), research=source)
        response = TestClient(app).post("/api/analyze", json=msft_request())
    elapsed = time.monotonic() - elapsed
    assert response.status_code == 504
    assert "deadline" in response.json()["detail"]
    assert elapsed < 3 and len(started) == 1  # the in-flight request was cancelled; nothing further was sent


def test_incomplete_response_for_a_non_budget_reason_is_not_retried(monkeypatch):
    def filtered():
        response = incomplete()
        body = json.loads(response.content)
        body["incomplete_details"] = {"reason": "content_filter"}
        return httpx.Response(200, json=body)

    model = Model(msft_request(), script={4: filtered})
    response = run(monkeypatch, model)
    assert response.status_code == 502 and "content_filter" in response.json()["detail"]
    assert len(model.sent) == 4


def answer_with(**changes):
    answer = msft_answer()
    answer.update(changes)
    return lambda: openrouter_response(openrouter_message(json.dumps(answer)), model="deepseek/deepseek-v4.1-flash")


TRANSCRIPT_CLAIM = ["The earnings-call transcript says management expects capital intensity to ease."]


def test_rejected_transcript_claim_is_corrected_once_without_repeating_research_or_calculations(monkeypatch):
    research_calls = []
    original = ReviewedResearchProvider.company

    async def counted(self, position, as_of):
        research_calls.append(position.id)
        return await original(self, position, as_of)

    monkeypatch.setattr(ReviewedResearchProvider, "company", counted)
    model = Model(msft_request(), script={6: answer_with(uncertainty=TRANSCRIPT_CLAIM)})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "hold"
    assert len(model.sent) == 7 and research_calls == ["msft"]
    correction = model.sent[6]
    # An answer-only turn: no tools to call, so research, cases and comparison cannot run again.
    assert "tools" not in correction and "tool_choice" not in correction
    assert "transcript Q&A cannot be claimed reviewed" in json.dumps(correction["input"][-1])
    assert [p["tool_choice"]["name"] for p in model.sent if isinstance(p.get("tool_choice"), dict)] == SEQUENCE


@pytest.mark.parametrize("second, reason", [
    (answer_with(uncertainty=TRANSCRIPT_CLAIM), "transcript Q&A cannot be claimed reviewed"),
    # A correction that brings in unsupported evidence or a numerical claim is not accepted either.
    (answer_with(evidence_ids=["sec-10-k-invented"]), "cannot cite unavailable primary evidence"),
    (answer_with(reason="Hold: there is a 90% probability that revenue doubles within two years."), "still failed validation"),
])
def test_corrected_answer_that_still_fails_returns_an_explicit_error(monkeypatch, second, reason):
    model = Model(msft_request(), script={6: answer_with(uncertainty=TRANSCRIPT_CLAIM), 7: second})
    response = run(monkeypatch, model)
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert "still failed validation" in detail and reason.lower() in detail.lower()
    assert len(model.sent) == 7
