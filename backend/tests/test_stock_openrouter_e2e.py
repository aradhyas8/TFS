"""End-to-end stock analysis through the production OpenRouter transport and WorkflowModelAdapter over mocked HTTP.

The research fixture is Microsoft's real SEC EDGAR evidence (10-K for the fiscal year ended 2026-06-30, recent 8-Ks,
the issuer earnings release and 23 CompanyFacts XBRL facts) captured from a live run; the portfolio is synthetic.
"""
import asyncio
import copy
import dataclasses
import json
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import (
    FakeDataProvider,
    OpenAIResponseTransport,
    WorkflowModelAdapter,
    latest_price_position,
)
from analyst.research import ReviewedResearchProvider
from analyst.schemas import (
    FX,
    AnalysisRequest,
    CompanyResearch,
    Identity,
    Quote,
    SourceQualification,
)
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
            return reads_valuation(scripted() if callable(scripted) else scripted, payload)
        choice = payload.get("tool_choice")
        if isinstance(choice, dict):
            return call(choice["name"], self.arguments.get(choice["name"], "{}"))
        return reads_valuation(openrouter_response(openrouter_message(json.dumps(msft_answer())), model="deepseek/deepseek-v4.1-flash"), payload)


def reads_valuation(response, payload):
    """An answer without price_position takes it from the valuation the tools returned, as a correct model does."""
    position = latest_price_position(payload.get("input", []))
    body = json.loads(response.content)
    for item in body.get("output", []):
        for part in item.get("content") or [] if item.get("type") == "message" else []:
            try:
                answer = json.loads(part.get("text", ""))
            except ValueError:
                continue
            if isinstance(answer, dict) and "preferred_action" in answer and "price_position" not in answer and position:
                part["text"] = json.dumps({**answer, "price_position": position})
    return httpx.Response(response.status_code, json=body)


def run(monkeypatch, model, *, research: dict | None = None, effort: str | None = "max", request: dict | None = None, financial=None):
    transport, _ = openrouter_transport(monkeypatch, model, model="deepseek/deepseek-v4.1-flash")
    transport.settings = dataclasses.replace(transport.settings, reasoning_effort=effort)
    source = ReviewedResearchProvider({CIK: CompanyResearch.model_validate(research or msft_research())})
    app = create_app(model=WorkflowModelAdapter(transport), data=FakeDataProvider(), research=source, financial=financial)
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
    (answer_with(reason="Hold: there is a 90% probability that revenue doubles within two years."), "unsupported quantitative claims"),
])
def test_corrected_answer_that_still_fails_keeps_the_results_and_withholds_the_direction(monkeypatch, second, reason):
    model = Model(msft_request(), script={6: answer_with(uncertainty=TRANSCRIPT_CLAIM), 7: second})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    result = response.json()
    # The research, cases and comparison are valid tool results and are returned; only the model's direction is withheld.
    assert all(case["terminal_price"] is not None for case in result["stock"]["cases"]) and result["comparison"] is not None
    recommendation = result["recommendation"]
    assert recommendation["preferred_action"] == "wait_for_inputs" and recommendation["evidence_ids"] == []
    assert reason.lower() in " ".join(recommendation["uncertainty"]).lower()
    assert len(model.sent) == 7


def test_withheld_answer_shown_to_the_user_never_quotes_model_text(monkeypatch):
    model = Model(msft_request(), script={6: answer_with(uncertainty=TRANSCRIPT_CLAIM), 7: answer_with(reason="There is a high probability that Microsoft recovers.")})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    shown = json.dumps(response.json()["recommendation"])
    assert "Unsupported quantitative claims or execution direction." in shown
    assert "probability" not in shown and "Rejected phrase" not in shown


def judgments_with(text: str) -> str:
    judgments = msft_judgments()
    for case in judgments["cases"]:
        case["assumptions"] = ["Payout reflects the observed mix of dividends and buybacks.", text]
    return json.dumps(judgments)


def company_turns(model) -> list[dict]:
    return [p for p in model.sent if isinstance(p.get("tool_choice"), dict) and p["tool_choice"]["name"] == "calculate_company_cases"]


@pytest.mark.parametrize("text", [
    "Exit multiple is a normalized multiple on final-year free cash flow.",
    # Case explanations follow the Stock Analysis rules: adjustment verbs and reported figures describe the case.
    "Reduce the margin path if capital intensity stays high. Increase reinvestment while datacenter build-out continues.",
    "Exit the position in the downside case if cloud margins fall. Reported FY2026 free cash flow was about 30% of revenue.",
])
def test_case_explanation_wording_is_accepted_without_a_correction(monkeypatch, text):
    model = Model(msft_request())
    model.arguments["calculate_company_cases"] = judgments_with(text)
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert len(company_turns(model)) == 1
    assert [p.get("tool_choice", {}).get("name") if isinstance(p.get("tool_choice"), dict) else p.get("tool_choice") for p in model.sent] \
        == [*SEQUENCE, "auto"]


def rejected_call(arguments):
    response = call("calculate_company_cases", arguments)
    body = json.loads(response.content)
    body["output"][0].update(id="fc_rejected", call_id="call_rejected")
    return httpx.Response(200, json=body)


def test_rejected_trade_size_is_corrected_with_the_exact_phrase_and_reason(monkeypatch):
    model = Model(msft_request(), script={4: lambda: rejected_call(judgments_with("Sell half the position if margins fall."))})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    turns = company_turns(model)
    assert len(turns) == 2
    error = outputs(turns[1])["calculate_company_cases"]["error"]
    assert "Rejected phrase 'Sell half': reads as an unsupported number, probability, guarantee or trade size" in error
    assert response.json()["stock"]["cases"][0]["terminal_price"] is not None


class Market:
    """A dated market-data provider: verified identities, same-day indicative quotes and provider FX, all configurable."""

    def __init__(self, *, fx_date: str = "2026-10-13", fx_status: str = "indicative", unverified: frozenset = frozenset(),
                 no_quote: frozenset = frozenset()):
        self.fx_date, self.fx_status = date.fromisoformat(fx_date), fx_status
        self.unverified, self.no_quote = unverified, no_quote

    async def identity(self, position, as_of):
        return Identity(status="supplied" if position.id in self.unverified else "verified", ticker=position.ticker,
                        listing=position.listing, currency=position.currency, kind=position.kind, company_id=position.company_id,
                        company_name=position.company_name, source="Test registry", source_url="https://registry.example.com/listing",
                        as_of=as_of, captured_at=datetime(2026, 10, 14, 20, tzinfo=UTC))

    async def quote(self, position, as_of):
        if position.id in self.no_quote:
            return None
        terms = SourceQualification(source="Test feed", terms_url="https://feed.example.com/terms", checked_on=as_of,
                                    personal_use_permitted=True, covered_listings=[position.listing])
        return Quote(value=position.mark.value, as_of=as_of, source="Test feed", captured_at=datetime(2026, 10, 14, 20, tzinfo=UTC),
                     ticker=position.ticker, listing=position.listing, currency=position.currency, status="indicative", qualification=terms)

    async def fx(self, from_currency, to_currency, as_of):
        return FX(from_currency=from_currency, to_currency=to_currency, rate=Decimal("1.4"), as_of=self.fx_date,
                  source="Test central bank", captured_at=datetime(2026, 10, 14, 20, tzinfo=UTC), status=self.fx_status)

    async def sponsor_holdings(self, position, as_of):
        return None


def cad_request():
    request = msft_request()
    request["portfolio"]["reporting_currency"] = "CAD"  # the USD stock, ETF and cash all need dated FX
    return request


def values(result):
    # comparison_value: the known value before costs and taxes, which stay unknown (listed in `unmodeled`), never zero.
    return {alt["selection"]["id"]: [case["comparison_value"] for case in alt["cases"]] for alt in result["comparison"]["alternatives"]}


def test_etf_cash_and_keep_alternatives_are_valued_with_the_latest_published_provider_fx(monkeypatch):
    # Provider FX is published for the previous business day; it values the holdings, so it must value the cases too.
    response = run(monkeypatch, Model(cad_request()), request=cad_request(), financial=Market())
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(value is not None for row in values(result).values() for value in row), values(result)
    starting = Decimal(result["comparison"]["starting_value"])
    assert starting == (20 * Decimal(500) + 3000) * Decimal("1.4")
    # Cash: the shared starting capital compounds at the judged annual rates and converts back at the dated rate.
    cash = next(alt for alt in result["comparison"]["alternatives"] if alt["selection"]["id"] == "cash")
    growth = Decimal(1)
    for rate in ("0.04", "0.03", "0.02", "0.01", "0"):
        growth *= 1 + Decimal(rate)
    assert abs(Decimal(cash["cases"][1]["comparison_value"]) - starting * growth) <= Decimal("0.01")
    # No action keeps the actual scope: the company case for the shares and the cash path for the cash.
    keep = next(alt for alt in result["comparison"]["alternatives"] if alt["selection"]["id"] == "keep")
    assert {row["position_id"]: row["known_terminal_value"] is not None for row in keep["cases"][1]["components"]} == {"msft": True, "cash": True}
    assert result["recommendation"]["preferred_action"] == "hold"


@pytest.mark.parametrize("market", [
    Market(fx_status="stale"),
    Market(fx_date="2026-10-01"),                        # older than the recent window
    Market(fx_date="2026-10-15"),                        # dated after the snapshot
    Market(fx_date="2026-10-13", fx_status="manual"),    # a manual rate must be dated on the snapshot
], ids=["stale", "too_old", "future", "manual_previous_day"])
def test_unusable_fx_leaves_every_converted_alternative_unknown(monkeypatch, market):
    response = run(monkeypatch, Model(cad_request()), request=cad_request(), financial=market)
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(value is None for row in values(result).values() for value in row)
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_missing_stock_quote_is_never_filled_in(monkeypatch):
    # No provider quote and no user-entered mark: nothing may stand in for the price.
    request = cad_request()
    del request["portfolio"]["positions"][0]["mark"]
    response = run(monkeypatch, Model(request), request=request, financial=Market(no_quote=frozenset({"msft"})))
    assert response.status_code == 200, response.text
    result = response.json()
    assert all(case["terminal_price"] is None for case in result["stock"]["cases"])
    assert values(result)["company"] == [None] * 3 and result["recommendation"]["preferred_action"] == "wait_for_inputs"


def test_unverified_fund_identity_is_valued_provisionally_and_qualified(monkeypatch):
    response = run(monkeypatch, Model(cad_request()), request=cad_request(), financial=Market(unverified=frozenset({"vti"})))
    assert response.status_code == 200, response.text
    result = response.json()
    fund = next(alt for alt in result["comparison"]["alternatives"] if alt["selection"]["id"] == "fund")
    assert all(case["comparison_value"] is not None for case in fund["cases"])
    vti = next(row for row in result["portfolio"]["positions"] if row["supplied"]["id"] == "vti")
    assert vti["source_inputs_usable"] is False and "Identity is not verified by a primary source; valuation is provisional." in vti["issues"]


def test_reason_cut_off_at_the_length_limit_is_corrected_not_shown(monkeypatch):
    cut = ("Hold conditionally while the filings support durable cloud cash generation " * 20)[:1000]
    assert len(cut) == 1000 and not cut.endswith(".")
    model = Model(msft_request(), script={6: answer_with(reason=cut)})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["reason"] == msft_answer()["reason"]
    assert len(model.sent) == 7 and "cut off mid-sentence" in json.dumps(model.sent[6]["input"][-1])


def test_complete_text_at_the_length_limit_is_accepted(monkeypatch):
    complete = ("Hold conditionally while the filings support durable cloud cash generation " * 20)[:999] + "."
    model = Model(msft_request(), script={6: answer_with(reason=complete)})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["reason"] == complete.strip() and len(model.sent) == 6


def test_comparison_reuses_the_calculated_company_cases_instead_of_restating_them(monkeypatch):
    """The slowest model turn restated the company cases Python had already calculated; it now marks them for reuse,
    and every comparison value is unchanged."""
    restated = run(monkeypatch, Model(msft_request()))
    model = Model(msft_request())
    full = json.loads(model.arguments["calculate_comparison"])["alternatives"]
    compact = {row["alternative_id"]: {"cases": row["cases"]} for row in full if row["alternative_id"] in {"fund", "cash"}}
    model.arguments["calculate_comparison"] = json.dumps({"alternatives": {**compact, "company": True, "keep": True}})
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    # The schema sent to the model offers reuse for the company and the retained portfolio.
    schema = next(tool["parameters"] for tool in model.sent[4]["tools"] if tool["name"] == "calculate_comparison")
    assert schema["properties"]["alternatives"]["properties"]["company"] == {"type": "boolean", "enum": [True]}
    assert schema["properties"]["alternatives"]["properties"]["keep"] == {"type": "boolean", "enum": [True]}
    assert values(response.json()) == values(restated.json())
    assert all(value is not None for value in values(response.json())["company"])


def test_answer_contradicting_the_calculated_price_position_is_corrected_with_pythons_values(monkeypatch):
    """The live MSFT answer said the price fell short of the upside case while Python placed it above: it compared
    today's price with the five-year exit price. A stated position that differs from Python's gets one correction."""
    model = Model(msft_request())
    seen = run(monkeypatch, Model(msft_request())).json()["stock"]["valuation"]
    wrong = next(row for row in ["below_downside", "base_to_upside", "above_upside"] if row != seen["position"])
    model.script = {6: answer_with(price_position=wrong)}
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    correction = json.dumps(model.sent[6]["input"][-1])
    assert f"price_position must be {seen['position']}" in correction and seen["base"] in correction
    assert "Exit prices are five-year future prices" in correction
    result = response.json()
    assert result["recommendation"]["price_position"] == result["stock"]["valuation"]["position"] == seen["position"]


def test_answer_that_keeps_contradicting_the_price_position_is_withheld(monkeypatch):
    model = Model(msft_request())
    seen = run(monkeypatch, Model(msft_request())).json()["stock"]["valuation"]["position"]
    wrong = "below_downside" if seen != "below_downside" else "above_upside"
    model.script = {6: answer_with(price_position=wrong), 7: answer_with(price_position=wrong)}
    response = run(monkeypatch, model)
    assert response.status_code == 200, response.text
    recommendation = response.json()["recommendation"]
    assert recommendation["preferred_action"] == "wait_for_inputs"
    assert f"price_position must be {seen}" in " ".join(recommendation["uncertainty"])


def test_completed_analysis_is_saved_as_a_conversation_that_survives_a_restart(monkeypatch):
    response = run(monkeypatch, Model(msft_request()))
    assert response.status_code == 200, response.text
    # A new app on the same SQLite database stands in for a restart; nothing comes from the earlier process.
    restarted = TestClient(create_app(model=WorkflowModelAdapter(openrouter_transport(monkeypatch, Model(msft_request()))[0])))
    [summary] = restarted.get("/api/threads").json()
    assert summary["title"] == msft_request()["question"]
    thread = restarted.get(f"/api/threads/{summary['id']}").json()
    user, assistant = thread["messages"]
    assert (user["role"], assistant["role"]) == ("user", "assistant")
    envelope = restarted.get(f"/api/artifacts/{assistant['artifact_ids'][0]}").json()
    assert envelope["reference"]["kind"] == "analysis_result" and envelope["reference"]["label"] == "Stock analysis"
    saved = envelope["payload"]
    assert saved["recommendation"] == response.json()["recommendation"]
    assert saved["stock"]["cases"] == response.json()["stock"]["cases"]
