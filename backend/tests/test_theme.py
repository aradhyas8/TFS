import json

import pytest
from fastapi.testclient import TestClient

from analyst.api import create_app
from analyst.providers import FakeDataProvider, ScriptedModel
from tests.test_stock import stock_comparison_request


def theme_request(confirmed=False):
    request = stock_comparison_request()
    request.pop("stock")
    request["theme"] = {
        "name": "Industrial automation",
        "mechanism": "Automation demand expands issuer revenue while competition may compress margins.",
        "shortlist": ["p1"],
        "max_candidates": 1,
        "max_tool_calls": 10,
        "confirmed": confirmed,
    }
    return request


def test_unagreed_theme_completes_clarification_without_model_or_candidate_research():
    model = ScriptedModel([])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json=theme_request()
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["theme"]["status"] == "awaiting_agreement"
    assert result["theme"]["stocks"] == []
    assert model.requests == []
    assert "shortlist" in json.dumps(result["recommendation"])


def theme_turns(request=None, answer=None):
    from analyst.providers import ModelTurn, ToolCall
    from tests.test_analysis import recommendation
    from tests.test_stock import company_judgments, stock_comparison_judgments

    request = request or theme_request(True)
    answer = answer or {
        **recommendation(),
        "preferred_action": "no_action",
        "evidence_ids": ["theme-p1-filing", "theme-p1-issuer"],
        "reason": "The automation mechanism is plausible, but reported revenue does not establish durable margin gains. Diversified fund exposure and retaining cash remain serious alternatives to company concentration.",
        "alternatives": [
            {
                "action": "clarify_inputs",
                "reason": "Compare diversified fund exposure if its sponsor holdings and costs support a broader way to participate.",
            },
            {
                "action": "clarify_inputs",
                "reason": "Retaining cash while testing issuer demand and competitive pressure avoids an unsupported company commitment.",
            },
        ],
        "downside": "Automation demand can disappoint while competition compresses margins; exit valuation and concentrated issuer exposure can compound losses.",
        "assumptions": [
            "Revenue growth, sustainable margins and reinvestment needs determine whether the named mechanism benefits shareholders."
        ],
        "uncertainty": [
            "Reported totals do not isolate automation demand; future margins, fund alignment, indirect overlap and personal tax effects remain uncertain."
        ],
        "what_could_change": [
            "Primary segment evidence linking automation demand to durable cash generation, usable sponsor exposure and costs, or changed portfolio limits could distinguish the alternatives."
        ],
    }
    calls = [
        ("portfolio", "review_portfolio", {}),
        (
            "research",
            "research_candidate",
            {
                "position_id": "p1",
                "reason": "Test whether automation demand improves issuer economics.",
            },
        ),
        (
            "mechanism",
            "test_theme_mechanism",
            {
                "position_id": "p1",
                "conclusion": "challenges",
                "explanation": "Reported revenue does not establish that automation demand improves margins.",
                "evidence_ids": ["theme-p1-filing", "theme-p1-issuer"],
            },
        ),
        (
            "cases",
            "calculate_company_cases",
            {"position_id": "p1", "judgments": company_judgments()},
        ),
        ("comparison", "calculate_comparison", stock_comparison_judgments(request)),
    ]
    return [
        ModelTurn(calls=[ToolCall(key, name, json.dumps(args))]) for key, name, args in calls
    ] + [ModelTurn(answer=answer)]


def run_theme(request=None, turns=None, records=None):
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    from tests.test_stock import research_fixture

    source = ReviewedResearchProvider(
        records
        if records is not None
        else {"acme": CompanyResearch.model_validate(research_fixture())}
    )
    model = ScriptedModel(turns or theme_turns(request))
    response = TestClient(create_app(model=model, data=FakeDataProvider(), research=source)).post(
        "/api/analyze", json=request or theme_request(True)
    )
    return response, model


def test_agreed_theme_tests_mechanism_compares_dated_cases_and_can_stop_with_no_action():
    response, model = run_theme()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "no_action"
    assert result["recommendation"]["amount"] is None
    assert result["theme"]["researched"] == ["p1"]
    assert result["theme"]["tests"][0]["conclusion"] == "challenges"
    assert result["theme"]["tool_calls_used"] == 5
    assert result["theme"]["stocks"][0]["cases"][1]["terminal_price"] == "100"
    assert {row["selection"]["kind"] for row in result["comparison"]["alternatives"]} == {
        "stock",
        "etf",
        "cash",
        "no_action",
    }
    assert result["comparison"]["reporting_currency"] == "CAD"
    assert len(model.requests) == 6


@pytest.mark.parametrize(
    "fault",
    [
        "outside",
        "repeat",
        "effort",
        "no_test",
        "no_cases",
        "no_comparison",
        "invented_evidence",
        "probability",
        "amount",
        "execution",
        "scan",
        "late_research",
    ],
)
def test_theme_cannot_escape_agreement_evidence_or_response_contract(fault):
    from analyst.providers import ModelTurn, ToolCall

    request = theme_request(True)
    turns = theme_turns(request)
    if fault == "outside":
        turns[1] = ModelTurn(
            calls=[
                ToolCall(
                    "research",
                    "research_candidate",
                    json.dumps({"position_id": "p2", "reason": "Test demand."}),
                )
            ]
        )
    elif fault == "repeat":
        turns.insert(
            2,
            ModelTurn(calls=[ToolCall("again", "research_candidate", turns[1].calls[0].arguments)]),
        )
    elif fault == "effort":
        request["theme"]["max_tool_calls"] = 2
    elif fault == "no_test":
        turns.pop(2)
    elif fault == "no_cases":
        turns.pop(3)
    elif fault == "no_comparison":
        turns.pop(4)
    elif fault == "scan":
        turns[1] = ModelTurn(calls=[ToolCall("scan", "scan_opportunities", "{}")])
    elif fault == "late_research":
        turns.insert(
            -1,
            ModelTurn(calls=[ToolCall("late", "research_candidate", turns[1].calls[0].arguments)]),
        )
    else:
        answer = dict(turns[-1].answer)
        if fault == "invented_evidence":
            answer["evidence_ids"] = ["invented"]
        elif fault == "probability":
            answer["reason"] = "There is a probability of fifty percent."
        elif fault == "amount":
            answer["amount"] = "100"
        else:
            answer["reason"] = "We bought the company for your account."
        turns[-1] = ModelTurn(answer=answer)
    response, model = run_theme(request, turns)
    assert response.status_code == 502, response.text
    assert "recommendation" not in response.json()
    if fault == "effort":
        assert len(model.requests) == 3


def test_unknown_primary_evidence_preserves_unknown_cases_and_conditional_direction():
    from analyst.providers import ModelTurn, ToolCall

    turns = theme_turns()
    turns[2] = ModelTurn(
        calls=[
            ToolCall(
                "mechanism",
                "test_theme_mechanism",
                json.dumps(
                    {
                        "position_id": "p1",
                        "conclusion": "unknown",
                        "explanation": "Primary evidence is unavailable to test the mechanism.",
                        "evidence_ids": [],
                    }
                ),
            )
        ]
    )
    turns[-1].answer["evidence_ids"] = []
    response, _ = run_theme(turns=turns, records={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["theme"]["stocks"][0]["cases"][0]["terminal_price"] is None


def test_theme_cannot_force_a_purchase_or_waive_existing_caps():
    from analyst.providers import ModelTurn

    request = theme_request(True)
    turns = theme_turns(request)
    turns[-1] = ModelTurn(answer={**turns[-1].answer, "preferred_action": "add"})
    response, _ = run_theme(request, turns)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert response.json()["recommendation"]["amount"] is None
    request["settings"] = {
        "single_company_cap": "0.1",
        "active_budget": "0.9",
        "indirect_cap_policy": "direct_only",
    }
    response, _ = run_theme(request)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "review_only"


@pytest.mark.parametrize(
    "fault",
    ["mixed", "duplicate", "too_many", "canadian", "unlisted_alternative", "unconfirmed_fields"],
)
def test_invalid_theme_agreement_is_rejected_before_model(fault):
    request = theme_request(True)
    if fault == "mixed":
        request["stock"] = {"position_id": "p1"}
    elif fault == "duplicate":
        request["theme"]["shortlist"] = ["p1", "p1"]
    elif fault == "too_many":
        request["theme"]["shortlist"] = ["p1", "p2"]
    elif fault == "canadian":
        request["theme"]["shortlist"] = ["p2"]
    elif fault == "unlisted_alternative":
        request["comparison"]["alternatives"][0]["position_id"] = "p2"
    else:
        request["theme"]["mechanism"] = None
    model = ScriptedModel([])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json=request
    )
    assert response.status_code == 422, response.text
    assert model.requests == []


def test_shortlisted_etf_uses_sponsor_exposure_cases_and_no_company_research():
    from analyst.providers import ModelTurn, ToolCall
    from tests.test_stock import stock_comparison_judgments

    request = theme_request(True)
    request["theme"]["shortlist"] = ["fund"]
    request["comparison"]["alternatives"] = [
        row for row in request["comparison"]["alternatives"] if row["kind"] != "stock"
    ]
    answer = theme_turns()[-1].answer
    answer["evidence_ids"] = ["fund-fund"]
    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(
            calls=[
                ToolCall(
                    "mechanism",
                    "test_theme_mechanism",
                    json.dumps(
                        {
                            "position_id": "fund",
                            "conclusion": "challenges",
                            "explanation": "Broad equity exposure dilutes the named automation mechanism.",
                            "evidence_ids": ["fund-fund"],
                        }
                    ),
                )
            ]
        ),
        ModelTurn(
            calls=[
                ToolCall(
                    "comparison",
                    "calculate_comparison",
                    json.dumps(stock_comparison_judgments(request)),
                )
            ]
        ),
        ModelTurn(answer=answer),
    ]
    response, model = run_theme(request, turns, records={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["theme"]["stocks"] == []
    assert result["theme"]["researched"] == []
    assert result["recommendation"]["preferred_action"] == "no_action"
    assert result["comparison"]["alternatives"][0]["cases"][1]["known_terminal_value"] == "1950"
    assert len(model.requests) == 4


def test_multiple_shortlisted_companies_remain_distinct_and_bounded():
    from analyst.providers import ModelTurn, ToolCall
    from analyst.schemas import CompanyResearch
    from tests.test_stock import research_fixture, stock_comparison_judgments

    request = theme_request(True)
    request["theme"].update(shortlist=["p1", "other"], max_candidates=2)
    other = {
        **request["portfolio"]["positions"][0],
        "id": "other",
        "ticker": "OTHER",
        "company_id": "other",
        "shares": "0",
    }
    request["portfolio"]["positions"].append(other)
    request["comparison"]["alternatives"].insert(
        1, {"id": "other", "kind": "stock", "position_id": "other"}
    )
    turns = theme_turns(request)
    extra = []
    for index, turn in enumerate(turns[1:4]):
        args = json.loads(turn.calls[0].arguments)
        args["position_id"] = "other"
        if "evidence_ids" in args:
            args["evidence_ids"] = [
                key.replace("theme-p1-", "theme-other-") for key in args["evidence_ids"]
            ]
        extra.append(
            ModelTurn(calls=[ToolCall(f"other-{index}", turn.calls[0].name, json.dumps(args))])
        )
    turns[4:4] = extra
    turns[-2] = ModelTurn(
        calls=[
            ToolCall(
                "comparison",
                "calculate_comparison",
                json.dumps(stock_comparison_judgments(request)),
            )
        ]
    )
    turns[-1].answer["evidence_ids"] += ["theme-other-filing", "theme-other-issuer"]
    other_record = research_fixture()
    other_record["company_id"] = "other"
    for doc in other_record["documents"]:
        doc["company_id"] = "other"
    response, _ = run_theme(
        request,
        turns,
        records={
            "acme": CompanyResearch.model_validate(research_fixture()),
            "other": CompanyResearch.model_validate(other_record),
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["theme"]["researched"] == ["p1", "other"]
    assert result["theme"]["tool_calls_used"] == 8
    assert result["recommendation"]["preferred_action"] == "no_action"


def test_theme_production_sdk_uses_bounded_tools_and_shared_structured_response(monkeypatch):
    import httpx
    from openai import AsyncOpenAI

    from analyst.config import Settings
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch
    from tests.test_stock import research_fixture

    scripted = theme_turns()
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        names = {tool["name"] for tool in payload["tools"]}
        assert {
            "research_candidate",
            "test_theme_mechanism",
            "calculate_company_cases",
            "calculate_comparison",
        }.issubset(names)
        assert not names.intersection({"scan_opportunities", "size_allocation", "get_sec_filings"})
        assert payload["text"]["format"]["schema"]["properties"]["amount"] == {"type": "null"}
        assert payload["parallel_tool_calls"] is False
        assert payload["store"] is False
        turn = scripted[len(sent) - 1]
        if turn.calls:
            call = turn.calls[0]
            output = [
                {
                    "type": "function_call",
                    "id": f"fc_{len(sent)}",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg_theme",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": json.dumps(turn.answer), "annotations": []}
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(sent)}",
                "object": "response",
                "created_at": 1,
                "model": "test-model",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    monkeypatch.setattr(
        "analyst.providers.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai))
        ),
    )
    response = TestClient(
        create_app(
            settings=Settings("sk-test-backend-only-never-browser", "test-model"),
            data=FakeDataProvider(),
            research=ReviewedResearchProvider(
                {"acme": CompanyResearch.model_validate(research_fixture())}
            ),
        )
    ).post("/api/analyze", json=theme_request(True))
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "no_action"
    assert len(sent) == 6
    assert "sk-test-backend-only-never-browser" not in response.text


def test_theme_model_cannot_invent_trade_previews():
    from analyst.providers import ModelTurn, ToolCall

    turns = theme_turns()
    turns.insert(
        1,
        ModelTurn(
            calls=[
                ToolCall(
                    "invented",
                    "check_proposed_changes",
                    json.dumps(
                        {
                            "new_cash": [],
                            "trades": [
                                {
                                    "position_id": "p1",
                                    "shares_change": "1",
                                    "cash_position_id": "c1",
                                }
                            ],
                        }
                    ),
                )
            ]
        ),
    )
    response, _ = run_theme(turns=turns)
    assert response.status_code == 502


def test_unconfirmed_theme_does_not_need_openai_configuration_or_candidate_evidence(monkeypatch):
    from analyst.config import Settings

    monkeypatch.setenv("RESEARCH_REFERENCE_FILE", "missing-candidate-file.json")
    response = TestClient(create_app(settings=Settings("", ""), data=FakeDataProvider())).post(
        "/api/analyze", json=theme_request()
    )
    assert response.status_code == 200, response.text
    assert response.json()["theme"]["status"] == "awaiting_agreement"


def test_no_action_survives_a_conditional_amountless_purchase_alternative():
    turns = theme_turns()
    turns[-1].answer["alternatives"] = [
        {
            "action": "add",
            "reason": "A conditional addition could become attractive if primary demand evidence improves.",
        }
    ]
    response, _ = run_theme(turns=turns)
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "no_action"
    assert response.json()["recommendation"]["alternatives"][0]["action"] == "add"
    assert response.json()["recommendation"]["amount"] is None


def sized_theme_request():
    request = theme_request(True)
    portfolio = request["portfolio"]
    portfolio["positions"] = [
        portfolio["positions"][0],
        portfolio["positions"][3],
        portfolio["positions"][-1],
    ]
    portfolio["positions"][0]["account_id"] = "broker"
    portfolio["positions"][-1]["listing"] = "XNAS"
    portfolio["reporting_currency"] = "USD"
    request["theme"]["risk_context"] = "Can tolerate equity losses; no near-term withdrawals."
    request["settings"] = {
        "single_company_cap": "0.9",
        "active_budget": "0.95",
        "indirect_cap_policy": "direct_only",
        "cash_is_deliberate_tilt": False,
    }
    request["comparison"]["scope_position_ids"] = ["p1", "c2"]
    request["comparison"]["alternatives"][2]["position_id"] = "c2"
    request["comparison"]["effects"] = [
        {
            "alternative_id": row["id"],
            "transaction_cost": "0",
            "terminal_tax": "0",
            "as_of": "2026-09-30",
            "source": "Explicit effects fixture",
        }
        for row in request["comparison"]["alternatives"]
    ]
    return request


def run_sized_theme(request=None):
    from analyst.financial_data import FakeFinancialProvider
    from analyst.providers import ModelTurn, ToolCall
    from analyst.research import ReviewedResearchProvider
    from analyst.schemas import CompanyResearch, FinancialEvidence
    from tests.test_freshness import evidence_fixture
    from tests.test_stock import research_fixture

    request = request or sized_theme_request()
    turns = theme_turns(request)
    args = json.loads(turns[2].calls[0].arguments)
    args["conclusion"] = "supports"
    args["explanation"] = (
        "Primary automation demand evidence supports conditional revenue resilience while margin durability remains uncertain."
    )
    turns[2] = ModelTurn(calls=[ToolCall("mechanism", "test_theme_mechanism", json.dumps(args))])
    turns.insert(
        -1,
        ModelTurn(
            calls=[
                ToolCall(
                    "sizing",
                    "size_review",
                    json.dumps(
                        {
                            "position_id": "p1",
                            "cash_position_id": "c2",
                            "min_weight": "0.75",
                            "max_weight": "0.8",
                            "reason": "Conditional operating resilience and a retained cash reserve justify this exposure range.",
                        }
                    ),
                )
            ]
        ),
    )
    turns[-1].answer["preferred_action"] = "add"
    turns[-1].answer["reason"] = (
        "A conditional addition expresses automation upside within supplied portfolio limits while retaining cash. Downside and base company outcomes remain weaker than cash; diversified exposure or no action remains plausible if margin resilience fails."
    )
    source = evidence_fixture()
    source["identities"]["fund"] = {
        **source["identities"]["p1"],
        "ticker": "BROAD",
        "kind": "etf",
        "company_id": None,
        "company_name": None,
    }
    source["quotes"]["fund"] = {**source["quotes"]["p1"], "ticker": "BROAD", "value": "100"}
    record = research_fixture()
    for document in record["documents"]:
        document["excerpt"] += (
            " Automation product orders expanded; competitive pressure and reinvestment needs leave future margins uncertain."
        )
    return TestClient(
        create_app(
            model=ScriptedModel(turns),
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(FinancialEvidence.model_validate(source)),
            research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(record)}),
        )
    ).post("/api/analyze", json=request)


def test_supported_theme_purchase_reuses_checked_python_exposure_sizing():
    response = run_sized_theme()
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "add"
    assert result["recommendation"]["amount"] == {
        "minimum": "75",
        "maximum": "160",
        "currency": "USD",
        "position_id": "p1",
    }
    assert all(row["status"] == "within_limits" for row in result["theme"]["previews"])
    assert result["theme"]["tool_calls_used"] == 6


@pytest.mark.parametrize(
    "fault", ["risk", "cap", "budget", "tax", "cost", "funding", "nonzero_cost", "cap_breach"]
)
def test_theme_sizing_requires_decisive_inputs_and_both_guardrail_endpoints(fault):
    request = sized_theme_request()
    if fault == "risk":
        request["theme"]["risk_context"] = None
    elif fault in {"cap", "budget"}:
        request["settings"].pop("single_company_cap" if fault == "cap" else "active_budget")
    elif fault in {"tax", "cost"}:
        request["comparison"]["effects"][0][
            "terminal_tax" if fault == "tax" else "transaction_cost"
        ] = None
    elif fault == "funding":
        request["portfolio"]["positions"][1]["account_id"] = "tfsa"
    elif fault == "nonzero_cost":
        request["comparison"]["effects"][0]["transaction_cost"] = "1"
    else:
        request["settings"]["single_company_cap"] = "0.77"
    response = run_sized_theme(request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["recommendation"]["amount"] is None
    assert result["theme"]["amount"] is None
    assert result["theme"]["missing_inputs"]


def test_theme_fund_sizing_aggregates_same_listing_across_accounts():
    from analyst.financial_data import FakeFinancialProvider
    from analyst.providers import ModelTurn, ToolCall
    from analyst.schemas import FinancialEvidence
    from tests.test_freshness import evidence_fixture
    from tests.test_stock import stock_comparison_judgments

    request = sized_theme_request()
    fund = {**request["portfolio"]["positions"][-1], "shares": "10"}
    request["portfolio"]["positions"] = [
        fund,
        {**fund, "id": "fund-other", "account_id": "tfsa"},
        {"id": "c2", "kind": "cash", "cash": "8000", "currency": "USD", "account_id": "broker"},
    ]
    request["theme"]["shortlist"] = ["fund"]
    request["comparison"]["scope_position_ids"] = ["fund", "fund-other", "c2"]
    request["comparison"]["alternatives"] = [
        row for row in request["comparison"]["alternatives"] if row["kind"] != "stock"
    ]
    request["comparison"]["fund_facts"].append(
        {**request["comparison"]["fund_facts"][0], "position_id": "fund-other"}
    )
    request["comparison"]["effects"] = [
        row for row in request["comparison"]["effects"] if row["alternative_id"] != "company"
    ]
    answer = theme_turns()[-1].answer
    answer.update(preferred_action="add", evidence_ids=["fund-fund"])
    turns = [
        ModelTurn(calls=[ToolCall("portfolio", "review_portfolio", "{}")]),
        ModelTurn(
            calls=[
                ToolCall(
                    "mechanism",
                    "test_theme_mechanism",
                    json.dumps(
                        {
                            "position_id": "fund",
                            "conclusion": "supports",
                            "explanation": "Diversified industrial exposure participates in the named demand mechanism with lower issuer concentration.",
                            "evidence_ids": ["fund-fund"],
                        }
                    ),
                )
            ]
        ),
        ModelTurn(
            calls=[
                ToolCall(
                    "comparison",
                    "calculate_comparison",
                    json.dumps(stock_comparison_judgments(request)),
                )
            ]
        ),
        ModelTurn(
            calls=[
                ToolCall(
                    "sizing",
                    "size_review",
                    json.dumps(
                        {
                            "position_id": "fund",
                            "cash_position_id": "c2",
                            "min_weight": "0.3",
                            "max_weight": "0.4",
                            "reason": "Diversified exposure and a retained cash reserve justify this fund range.",
                        }
                    ),
                )
            ]
        ),
        ModelTurn(answer=answer),
    ]
    source = evidence_fixture()
    identity = {
        **source["identities"]["p1"],
        "ticker": "BROAD",
        "kind": "etf",
        "company_id": None,
        "company_name": None,
    }
    quote = {**source["quotes"]["p1"], "ticker": "BROAD", "value": "100"}
    source["identities"] = {key: identity for key in ["fund", "fund-other"]}
    source["quotes"] = {key: quote for key in ["fund", "fund-other"]}
    response = TestClient(
        create_app(
            model=ScriptedModel(turns),
            data=FakeDataProvider(),
            financial=FakeFinancialProvider(FinancialEvidence.model_validate(source)),
        )
    ).post("/api/analyze", json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "add"
    assert result["recommendation"]["amount"] == {
        "minimum": "1000",
        "maximum": "2000",
        "currency": "USD",
        "position_id": "fund",
    }


def test_theme_on_portfolio_with_no_cash_row_completes_and_omits_cash_alternative():
    from analyst.schemas import AnalysisRequest

    req = theme_request(confirmed=True)
    req["portfolio"]["positions"] = [
        p for p in req["portfolio"]["positions"] if p["kind"] != "cash"
    ]
    req.pop("comparison", None)

    # Schema validation generates default comparison without cash and passes validation
    parsed = AnalysisRequest.model_validate(req)
    assert parsed.comparison is not None
    assert "cash" not in {alt.kind for alt in parsed.comparison.alternatives}
    assert "no_action" in {alt.kind for alt in parsed.comparison.alternatives}

    # Model turns matching the generated comparison
    turns = theme_turns(parsed.model_dump(mode="json"))
    response, _ = run_theme(req, turns)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "no_action"
    assert {alt["selection"]["kind"] for alt in result["comparison"]["alternatives"]} == {
        "stock",
        "etf",
        "no_action",
    }


def test_unagreed_theme_on_portfolio_with_no_cash_completes_without_model():
    req = theme_request(confirmed=False)
    req["portfolio"]["positions"] = [
        p for p in req["portfolio"]["positions"] if p["kind"] != "cash"
    ]
    req.pop("comparison", None)
    model = ScriptedModel([])
    response = TestClient(create_app(model=model, data=FakeDataProvider())).post(
        "/api/analyze", json=req
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["recommendation"]["preferred_action"] == "wait_for_inputs"
    assert result["theme"]["status"] == "awaiting_agreement"
    assert model.requests == []


def test_theme_validation_requires_cash_only_when_portfolio_has_cash():
    from pydantic import ValidationError

    from analyst.schemas import AnalysisRequest

    # Portfolio with cash positions: requires cash and no action
    req_with_cash = theme_request(confirmed=True)
    req_with_cash["comparison"]["alternatives"] = [
        alt for alt in req_with_cash["comparison"]["alternatives"] if alt["kind"] != "cash"
    ]
    with pytest.raises(ValidationError, match="Theme comparison requires cash and no action."):
        AnalysisRequest.model_validate(req_with_cash)

    # Portfolio without cash positions: requires no action
    req_no_cash = theme_request(confirmed=True)
    req_no_cash["portfolio"]["positions"] = [
        p for p in req_no_cash["portfolio"]["positions"] if p["kind"] != "cash"
    ]
    req_no_cash["comparison"]["alternatives"] = [
        alt
        for alt in req_no_cash["comparison"]["alternatives"]
        if alt["kind"] not in {"cash", "no_action"}
    ]
    with pytest.raises(ValidationError, match="Theme comparison requires no action."):
        AnalysisRequest.model_validate(req_no_cash)
