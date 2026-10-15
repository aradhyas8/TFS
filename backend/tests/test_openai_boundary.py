import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import FakeDataProvider
from analyst.research import ReviewedResearchProvider
from analyst.schemas import AnalysisRequest, CompanyResearch
from tests.test_analysis import recommendation, snapshot
from tests.test_comparison import comparison_request, judgments
from tests.test_stock import (
    company_judgments,
    research_fixture,
    stock_answer,
    stock_comparison_judgments,
    stock_request,
)


@pytest.mark.parametrize(
    "failure", [None, "financial_tools", "comparison", "stock", "refusal", "incomplete", "service_error", "malformed_json"]
)
@pytest.mark.parametrize("reasoning_effort", [None, "max"])
def test_production_sdk_request_path_with_fake_http_provider(monkeypatch, failure, reasoning_effort):
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        is_tool_turn = (
            len(sent) == 0
            or (failure == "stock" and len(sent) < 5)
            or (failure == "comparison" and len(sent) < 2)
        )
        assert payload["reasoning"] == {"effort": "medium" if is_tool_turn else reasoning_effort}
        sent.append(payload)
        if len(sent) == 1 or (failure == "financial_tools" and len(sent) <= 4) or (failure == "comparison" and len(sent) == 2) or (failure == "stock" and len(sent) <= 5):
            assert request.url == "https://api.openai.com/v1/responses"
            assert request.headers["Authorization"] == "Bearer sk-test-backend-only-never-browser"
            assert payload["tool_choice"] == ({"type": "function", "name": "review_portfolio"} if len(sent) == 1 else "auto")
            assert {tool["name"] for tool in payload["tools"]} == {
                "review_portfolio", "resolve_identities", "get_quotes", "get_fx", "check_proposed_changes", "calculate_comparison", "get_sec_filings", "get_issuer_material", "calculate_company_cases", "get_sponsor_holdings"
            }
            if failure == "stock":
                name = ["review_portfolio", "get_sec_filings", "get_issuer_material", "calculate_company_cases", "calculate_comparison"][len(sent) - 1]
            else:
                name = ["review_portfolio", "get_quotes", "resolve_identities", "get_fx"][len(sent) - 1]
            if failure == "comparison" and len(sent) == 2:
                name = "calculate_comparison"
            output = [
                {
                    "type": "function_call",
                    "id": "fc_test",
                    "call_id": f"sdk_tool_{len(sent)}",
                    "name": name,
                    "arguments": json.dumps(company_judgments()) if name == "calculate_company_cases" else json.dumps(stock_comparison_judgments(AnalysisRequest.model_validate(stock_request()).model_dump(mode="json"))) if failure == "stock" and name == "calculate_comparison" else json.dumps(judgments()) if name == "calculate_comparison" else "{}",
                    "status": "completed",
                }
            ]
        else:
            assert payload["tool_choice"] == "auto"
            assert payload["store"] is False
            assert payload["text"]["format"]["strict"] is True
            tool_output = next(json.loads(item["output"]) for item in payload["input"] if
                               item.get("type") == "function_call_output" and
                               "total_value" in json.loads(item["output"]))
            assert tool_output["total_value"] == "3600"
            if failure == "comparison":
                comparison = json.loads(payload["input"][-1]["output"])
                assert comparison["alternatives"][0]["cases"][1]["known_terminal_value"] == "2152.95756624"
            else:
                assert tool_output["direct_companies"][0]["weight"] == "0.63888889"
            if failure == "service_error":
                return httpx.Response(
                    401,
                    json={
                        "error": {
                            "message": "sk-test-backend-only-never-browser",
                            "type": "invalid_api_key",
                        }
                    },
                )
            content = [
                {
                    "type": "output_text",
                    "text": "broken"
                    if failure == "malformed_json"
                    else json.dumps(stock_answer() if failure == "stock" else recommendation()),
                    "annotations": [],
                }
            ]
            if failure == "refusal":
                content = [{"type": "refusal", "refusal": "Cannot complete."}]
            output = [
                {
                    "type": "message",
                    "id": "msg_test",
                    "role": "assistant",
                    "status": "completed",
                    "content": content,
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": f"resp_{len(sent)}",
                "object": "response",
                "created_at": 1,
                "model": "test-model",
                "status": "incomplete"
                if failure == "incomplete" and len(sent) == 2
                else "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    def local_sdk(**kwargs):
        return AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai))
        )

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", local_sdk)
    app = create_app(
        settings=Settings("sk-test-backend-only-never-browser", "test-model", reasoning_effort),
        data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())}),
    )
    response = TestClient(app).post(
        "/api/analyze", json=stock_request() if failure == "stock" else comparison_request() if failure == "comparison" else {"question": "Review my exposure", "portfolio": snapshot()}
    )
    assert response.status_code == (200 if failure in {None, "financial_tools", "comparison", "stock"} else 502), response.text
    assert "sk-test-backend-only-never-browser" not in response.text
    if failure in {None, "financial_tools", "comparison", "stock"}:
        assert response.json()["portfolio"]["total_value"] == "3600"
    else:
        assert "recommendation" not in response.json()

    if failure == "stock":
        assert response.json()["stock"]["cases"][1]["terminal_price"] == "100"
        assert response.json()["recommendation"]["evidence_ids"] == ["filing", "issuer"]
        assert "evidence_ids" in sent[-1]["text"]["format"]["schema"]["properties"]


def test_new_cash_uses_production_sdk_tools_and_backend_only_amounts(monkeypatch):
    from analyst.financial_data import FakeFinancialProvider
    from tests.test_allocation import (
        allocation_answer,
        allocation_evidence,
        allocation_request,
        comparison_judgments,
    )
    turns = [("review_portfolio", {}), ("scan_opportunities", {}),
             ("calculate_comparison", comparison_judgments()),
             ("size_allocation", {"position_id": "fund", "min_weight": "0.55", "max_weight": "0.65",
                                  "reason": "Diversification and a retained reserve justify this exposure range."})]
    sent = []
    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert payload["text"]["format"]["strict"] is True
        assert payload["text"]["format"]["schema"]["properties"]["amount"] == {"type": "null"}
        if len(sent) > len(turns):  # final synthesis after sizing: no callable tools, same strict answer schema
            assert not {"tools", "tool_choice", "parallel_tool_calls"} & payload.keys()
        else:
            names = {tool["name"] for tool in payload["tools"]}
            assert {"review_portfolio", "scan_opportunities", "research_candidate", "calculate_company_cases", "calculate_comparison", "size_allocation"} <= names
            assert "get_sec_filings" not in names
        if len(sent) <= len(turns):
            name, arguments = turns[len(sent) - 1]
            output = [{"type": "function_call", "id": f"fc_{len(sent)}", "call_id": f"call_{len(sent)}",
                       "name": name, "arguments": json.dumps(arguments), "status": "completed"}]
        else:
            sizing = json.loads(payload["input"][-1]["output"])
            assert sizing["amount"]["maximum"] == "2400"
            output = [{"type": "message", "id": "msg_final", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": json.dumps(allocation_answer()), "annotations": []}]}]
        return httpx.Response(200, json={"id": f"resp_{len(sent)}", "object": "response", "created_at": 1,
                                        "model": "test-model", "status": "completed", "output": output,
                                        "parallel_tool_calls": False, "error": None, "incomplete_details": None})
    def local_sdk(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)))
    monkeypatch.setattr("analyst.providers.AsyncOpenAI", local_sdk)
    response = TestClient(create_app(settings=Settings("sk-test-backend-only-never-browser", "test-model"),
                                    financial=FakeFinancialProvider(allocation_evidence()))).post("/api/analyze", json=allocation_request())
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["amount"]["maximum"] == "2400"
    assert "sk-test-backend-only-never-browser" not in response.text
    assert len(sent) == 5



@pytest.mark.parametrize("compact", [False, True])
def test_portfolio_review_uses_production_sdk_tools_and_preserves_bound_evidence(monkeypatch, compact):
    from tests.test_portfolio_review import assessment, review_request, review_research_fixture
    bound = AnalysisRequest.model_validate(review_request()).model_dump(mode="json")
    turns = [("review_portfolio", {}), ("reunderwrite_holding", assessment()),
             ("calculate_comparison", stock_comparison_judgments(bound))]
    if compact:
        for case in turns[1][1]["judgments"]["cases"]:
            for field, value in list(case.items()):
                if isinstance(value, list) and len(value) == 5 and len(set(value)) == 1:
                    case[field] = value[0]
        turns[2][1]["alternatives"][0] = {"alternative_id": "company-p1", "use_company_cases": True}
        turns[2][1]["alternatives"] = {row["alternative_id"]: True if row.get("use_company_cases") else {"cases": row["cases"]}
                                       for row in turns[2][1]["alternatives"]}
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert request.url == "https://api.openai.com/v1/responses"
        assert payload["text"]["format"]["schema"]["properties"]["amount"] == {"type": "null"}
        names = {tool["name"] for tool in payload["tools"]}
        assert {"review_portfolio", "reunderwrite_holding", "calculate_comparison", "size_review", "check_proposed_changes"} <= names
        assert not {"scan_opportunities", "research_candidate", "get_sec_filings"} & names
        assert payload["parallel_tool_calls"] is False
        assert payload["store"] is False
        holding_tool = next(tool for tool in payload["tools"] if tool["name"] == "reunderwrite_holding")
        growth = holding_tool["parameters"]["$defs"]["CompanyCase"]["properties"]["growth"]
        assert any(choice.get("type") == "array" for choice in growth["anyOf"])
        assert any("anyOf" in choice for choice in growth["anyOf"])
        comparison_tool = next(tool for tool in payload["tools"] if tool["name"] == "calculate_comparison")
        alternatives = comparison_tool["parameters"]["properties"]["alternatives"]
        assert alternatives["type"] == "object"
        assert set(alternatives["required"]) == {"company-p1", "cash", "keep"}
        assert alternatives["properties"]["company-p1"] == {"type": "boolean", "enum": [True]}
        if len(sent) <= len(turns):
            name, args = turns[len(sent) - 1]
            output = [{"type": "function_call", "id": f"fc_{len(sent)}", "call_id": f"call_{len(sent)}",
                       "name": name, "arguments": json.dumps(args), "status": "completed"}]
        else:
            comparison = json.loads(payload["input"][-1]["output"])
            assert comparison["starting_value"] == "3600"
            answer = {**recommendation(), "preferred_action": "reduce", "evidence_ids": ["review-p1-filing", "review-p1-issuer"]}
            output = [{"type": "message", "id": "msg_final", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": json.dumps(answer), "annotations": []}]}]
        return httpx.Response(200, json={"id": f"resp_{len(sent)}", "object": "response", "created_at": 1,
            "model": "test-model", "status": "completed", "output": output, "parallel_tool_calls": False,
            "error": None, "incomplete_details": None})

    def local_sdk(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)))

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", local_sdk)
    response = TestClient(create_app(settings=Settings("sk-test-backend-only-never-browser", "test-model"),
        data=FakeDataProvider(), research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=review_request())
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == "reduce"
    assert response.json()["reunderwriting"]["assessments"][0]["status"] == "changed"
    assert len(sent) == 4


def test_all_tool_and_response_schemas_exclude_regex_lookarounds():
    import json

    from pydantic import ValidationError

    from analyst.providers import (
        ALLOCATION_TOOLS,
        COMPARISON_TOOL,
        FINANCIAL_TOOLS,
        PORTFOLIO_TOOL,
        PROPOSAL_TOOL,
        SEDAR_TOOL,
        STOCK_TOOLS,
        sanitize_schema,
    )
    from analyst.schemas import (
        CandidateCasesInput,
        HoldingReviewInput,
        ProposedChanges,
        Recommendation,
        ReviewSizingInput,
        StockRecommendation,
        ThemeTestInput,
    )

    all_tool_schemas = [
        PORTFOLIO_TOOL["parameters"],
        *[t["parameters"] for t in FINANCIAL_TOOLS],
        PROPOSAL_TOOL["parameters"],
        COMPARISON_TOOL["parameters"],
        *[t["parameters"] for t in STOCK_TOOLS if "parameters" in t],
        SEDAR_TOOL.get("parameters", {}),
        *[t["parameters"] for t in ALLOCATION_TOOLS if "parameters" in t],
        sanitize_schema(CandidateCasesInput.model_json_schema()),
        sanitize_schema(HoldingReviewInput.model_json_schema()),
        sanitize_schema(ReviewSizingInput.model_json_schema()),
        sanitize_schema(ThemeTestInput.model_json_schema()),
        sanitize_schema(Recommendation.model_json_schema()),
        sanitize_schema(StockRecommendation.model_json_schema()),
    ]

    for schema in all_tool_schemas:
        schema_json = json.dumps(schema)
        for lookaround in ("(?=", "(?!", "(?<=", "(?<!"):
            assert lookaround not in schema_json, f"Lookaround assertion {lookaround} found in schema"

    # Verify domain validation is strictly preserved in Python
    with pytest.raises(ValidationError):
        ProposedChanges.model_validate({"positions": [{"id": "p1", "shares": "-1e15"}]})
    with pytest.raises(ValidationError):
        ProposedChanges.model_validate({"new_cash": [{"account_id": "acc1", "currency": "CAD", "amount": "-50"}]})



def test_tiered_reasoning_pipeline_scripted_model():
    import asyncio

    from analyst.pipeline import analyze
    from analyst.providers import ModelTurn, ScriptedModel, ToolCall
    from analyst.schemas import AnalysisRequest
    from tests.test_analysis import snapshot
    from tests.test_stock import (
        company_judgments,
        research_fixture,
        stock_answer,
        stock_comparison_judgments,
        stock_request,
    )

    # 1. Normal review: tool turn uses medium, final synthesis uses configured max
    turns = [
        ModelTurn(calls=[ToolCall("c1", "review_portfolio", "{}")]),
        ModelTurn(answer={"preferred_action": "no_action", "amount": None, "reason": "Portfolio is balanced.",
                          "alternatives": [{"action": "no_action", "reason": "No changes needed."}],
                          "downside": "Market risk.", "assumptions": ["Marks are current."],
                          "uncertainty": ["General uncertainty."], "what_could_change": ["Market movements."]}),
    ]
    model = ScriptedModel(turns, settings=Settings("key", "model", reasoning_effort="max"))
    req = AnalysisRequest.model_validate({"question": "Review my exposure", "portfolio": snapshot()})
    result = asyncio.run(analyze(req, model, FakeDataProvider()))
    assert result.recommendation.preferred_action == "no_action"
    assert model.reasoning_efforts == ["medium", "max"]

    # 2. Existing ScriptedModel without settings remains compatible (defaults to None for synthesis)
    model_compat = ScriptedModel(turns)
    result_compat = asyncio.run(analyze(req, model_compat, FakeDataProvider()))
    assert result_compat.recommendation.preferred_action == "no_action"
    assert model_compat.reasoning_efforts == ["medium", None]

    # 3. Forced-tool turn in stock analysis: model returns answer prematurely before calculate_company_cases,
    # pipeline forces calculate_company_cases on next turn, verifying that forced-tool turn uses medium reasoning.
    stock_req = AnalysisRequest.model_validate(stock_request())
    forced_turns = [
        ModelTurn(calls=[ToolCall("c1", "review_portfolio", "{}")]),
        ModelTurn(calls=[ToolCall("c2", "get_sec_filings", "{}")]),
        ModelTurn(calls=[ToolCall("c3", "get_issuer_material", "{}")]),
        # Premature answer before company cases:
        ModelTurn(answer={"preferred_action": "hold", "amount": None, "reason": "Preliminary view.",
                          "alternatives": [{"action": "no_action", "reason": "No action."}],
                          "downside": "Downside risk.", "assumptions": ["Assumptions."],
                          "uncertainty": ["Uncertainty."], "what_could_change": ["Change."],
                          "evidence_ids": ["filing", "issuer"]}),
        # Forced tool turn (calculate_company_cases) dispatched by reprompt:
        ModelTurn(calls=[ToolCall("c4", "calculate_company_cases", json.dumps(company_judgments()))]),
        ModelTurn(calls=[ToolCall("c5", "calculate_comparison", json.dumps(stock_comparison_judgments(stock_req.model_dump(mode="json"))))]),
        ModelTurn(answer=stock_answer()),
    ]
    stock_model = ScriptedModel(forced_turns, settings=Settings("key", "model", reasoning_effort="max"))
    research = ReviewedResearchProvider({"acme": CompanyResearch.model_validate(research_fixture())})
    stock_result = asyncio.run(analyze(stock_req, stock_model, FakeDataProvider(), research=research))
    assert stock_result.recommendation.preferred_action == "hold"
    assert stock_model.reasoning_efforts == ["medium", "medium", "medium", "medium", "medium", "medium", "max"]


def test_incomplete_response_detailed_diagnostics(monkeypatch):
    import asyncio

    from analyst.providers import OpenAIModel
    sent = []

    def fake_openai(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_incomplete_123",
                "object": "response",
                "created_at": 1,
                "model": "gpt-6-luna",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {
                    "input_tokens": 20048,
                    "output_tokens": 16000,
                    "output_tokens_details": {"reasoning_tokens": 15413},
                    "total_tokens": 36048,
                },
                "output": [
                    {
                        "type": "function_call",
                        "id": "fc_incomplete",
                        "call_id": "call_inc_1",
                        "name": "calculate_comparison",
                        "arguments": '{"alternatives": [{"alternative_id": "candidate-p1",',
                    }
                ],
            },
        )

    def local_sdk(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)))

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", local_sdk)
    model = OpenAIModel(Settings("sk-test", "gpt-6-luna", reasoning_effort="max"))

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(model.respond([{"role": "system", "content": "instructions"}, {"role": "user", "content": json.dumps({"question": "test"})}], require_tool=True, reasoning_effort="medium"))

    err_str = str(excinfo.value)
    assert "reason=max_output_tokens" in err_str
    assert "input_tokens=20048" in err_str
    assert "output_tokens=16000" in err_str
    assert "reasoning_tokens=15413" in err_str
    assert "max_output_tokens=16000" in err_str
    assert "reasoning_effort=medium" in err_str
    assert "partial_tools=['calculate_comparison']" in err_str


def test_openrouter_is_an_opt_in_base_url_for_the_same_client(monkeypatch):
    from analyst.config import Settings
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("OPENROUTER_MODEL", "vendor/model:free")
    monkeypatch.setenv("LLM_PROVIDER", "")  # unset means OpenAI; set (not deleted) so a local .env cannot override it
    default = Settings.from_environment()
    assert (default.api_key, default.model, default.base_url) == ("sk-openai", "gpt-x", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    routed = Settings.from_environment()
    assert (routed.api_key, routed.model, routed.base_url) == ("sk-or", "vendor/model:free", "https://openrouter.ai/api/v1")
    assert routed.provider == "openrouter"
    monkeypatch.setenv("LLM_PROVIDER", "other")
    with pytest.raises(ValueError):
        Settings.from_environment()


def test_openrouter_omits_parallel_flag_but_multiple_tool_calls_are_still_rejected(monkeypatch):
    from tests.test_portfolio_review import review_request, review_research_fixture
    sent = []

    def fake_openrouter(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert request.url == "https://openrouter.ai/api/v1/responses"
        assert "parallel_tool_calls" not in payload
        assert payload["provider"] == {"only": ["openai"], "allow_fallbacks": False, "require_parameters": True}
        assert payload["tool_choice"] == {"type": "function", "name": "review_portfolio"}
        output = [{"type": "function_call", "id": f"fc_{n}", "call_id": f"call_{n}", "name": "review_portfolio",
                   "arguments": "{}", "status": "completed"} for n in (1, 2)]
        return httpx.Response(200, json={"id": "gen-1", "object": "response", "created_at": 1, "model": "openai/test",
            "status": "completed", "output": output, "parallel_tool_calls": True, "error": None, "incomplete_details": None})

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", lambda **kwargs: AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openrouter))))
    settings = Settings("sk-or-test", "openai/test", base_url="https://openrouter.ai/api/v1")
    response = TestClient(create_app(settings=settings, data=FakeDataProvider(),
        research=ReviewedResearchProvider({"acme": CompanyResearch.model_validate(review_research_fixture())}))).post("/api/analyze", json=review_request())
    assert response.status_code != 200
    assert "recommendation" not in response.json()
    assert len(sent) == 1


OPENROUTER_TOOL = {"type": "function", "name": "review_portfolio", "strict": True,
                   "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}


def openrouter_response(output, model="vendor/model:free"):
    return httpx.Response(200, json={"id": "gen-1", "object": "response", "created_at": 1, "model": model, "status": "completed",
                                     "output": output, "parallel_tool_calls": True, "error": None, "incomplete_details": None})


def openrouter_message(text):
    return [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
             "content": [{"type": "output_text", "text": text, "annotations": []}]}]


def openrouter_transport(monkeypatch, handler, model="vendor/model:free", provider="openrouter"):
    """The production transport and its own SDK client (so its retry policy is real) over mock HTTP only."""
    from analyst.providers import OpenAIResponseTransport
    sent = []

    def record(request):
        sent.append(json.loads(request.content))
        return handler(request)

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(record))))
    config = Settings("sk-or-secret", model, base_url="https://openrouter.ai/api/v1", provider=provider)
    return OpenAIResponseTransport(config), sent


@pytest.mark.parametrize("model, price_cap", [("vendor/model:free", True), ("vendor/model", False)])
def test_openrouter_routes_any_vendor_model_without_fallback_and_caps_free_models(monkeypatch, model, price_cap):
    output = [{"type": "reasoning", "id": "rs_1", "summary": [], "content": [{"type": "reasoning_text", "text": "Call it."}]},
              {"type": "function_call", "id": "fc_1", "call_id": "call-1", "name": "review_portfolio", "arguments": "{}", "status": "completed"}]
    transport, sent = openrouter_transport(monkeypatch, lambda _request: openrouter_response(output, model), model=model)
    turn = asyncio.run(transport.respond([{"role": "user", "content": "hi"}], tools=[OPENROUTER_TOOL],
                                         tool_choice={"type": "function", "name": "review_portfolio"}))
    # No first-party-OpenAI pin for a non-OpenAI model, never another model or host, and a free model can never be billed.
    expected: dict = {"allow_fallbacks": False, "require_parameters": True}
    if price_cap:
        expected["max_price"] = {"prompt": 0, "completion": 0}
    assert sent[0]["provider"] == expected
    assert "models" not in sent[0] and sent[0]["model"] == model
    assert "parallel_tool_calls" not in sent[0]
    assert [call.name for call in turn.calls] == ["review_portfolio"]
    assert [item["type"] for item in turn.continuation] == ["reasoning", "function_call"]


@pytest.mark.parametrize("effort, expected", [(None, None), ("none", {"effort": "none"}), ("medium", {"effort": "medium"})])
def test_openrouter_sends_reasoning_only_when_an_effort_is_chosen(monkeypatch, effort, expected):
    transport, sent = openrouter_transport(monkeypatch, lambda _request: openrouter_response(openrouter_message("Answer.")))
    asyncio.run(transport.respond([{"role": "user", "content": "hi"}], reasoning_effort=effort))
    # require_parameters would otherwise demand reasoning support for an empty effort.
    assert sent[0].get("reasoning") == expected


def failing(status, message="upstream detail sk-or-secret"):
    return lambda _request: httpx.Response(status, json={"error": {"message": message, "code": status}})


def raising(error):
    def handler(request):
        raise error("upstream detail sk-or-secret", request=request)
    return handler


@pytest.mark.parametrize("provider", ["openrouter", "openai"])  # an OpenRouter URL is OpenRouter whatever the label
@pytest.mark.parametrize("handler, expected", [
    (failing(429), "rate limit reached"),
    (failing(404, "No endpoints found that support the provided 'tool_choice' value"), "does not support the required"),
    (failing(404, "No endpoints found for vendor/model:free."), "is unavailable"),
    (failing(400), "rejected the request (HTTP 400)"),
    (failing(402), "insufficient credits"),
    (failing(503), "is unavailable (HTTP 503)"),
    (raising(httpx.ReadTimeout), "timed out"),
    (raising(httpx.ConnectError), "could not be reached"),
])
def test_openrouter_failures_are_explicit_single_attempts_without_secrets(monkeypatch, provider, handler, expected):
    from analyst.providers import ModelServiceError
    transport, sent = openrouter_transport(monkeypatch, handler, provider=provider)
    with pytest.raises(ModelServiceError) as failure:
        asyncio.run(transport.respond([{"role": "user", "content": "hi"}]))
    assert expected in str(failure.value) and "OpenRouter vendor/model:free" in str(failure.value)
    assert "sk-or-secret" not in str(failure.value) and "upstream detail" not in str(failure.value)
    assert len(sent) == 1  # never retried automatically


def test_openrouter_invalid_structured_output_is_rejected(monkeypatch):
    transport, _sent = openrouter_transport(
        monkeypatch, lambda _request: openrouter_response(openrouter_message("Here is my review: {not json")))
    with pytest.raises(ValueError, match="invalid structured output"):
        asyncio.run(transport.respond([{"role": "user", "content": "hi"}], output_schema={
            "type": "object", "properties": {}, "required": [], "additionalProperties": False}))


def openrouter_app(monkeypatch, handler):
    monkeypatch.setattr("analyst.providers.AsyncOpenAI", lambda **kwargs: AsyncOpenAI(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    return TestClient(create_app(settings=settings, data=FakeDataProvider()))


def test_analyze_keeps_the_specific_openrouter_failure(monkeypatch):
    response = openrouter_app(monkeypatch, failing(429)).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()})
    assert response.status_code == 502
    assert "rate limit reached" in response.json()["detail"] and "sk-or-secret" not in response.text


def test_analyze_rejects_invalid_openrouter_structured_output(monkeypatch):
    sent = []

    def handler(request):
        sent.append(request)
        if len(sent) == 1:
            return openrouter_response([{"type": "function_call", "id": "fc_1", "call_id": "call-1",
                                         "name": "review_portfolio", "arguments": "{}", "status": "completed"}])
        return openrouter_response(openrouter_message("{not json"))

    response = openrouter_app(monkeypatch, handler).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()})
    assert response.status_code == 502, response.text
    assert "recommendation" not in response.json()


@pytest.mark.anyio
async def test_conversation_run_records_the_specific_openrouter_failure(monkeypatch, tmp_path):
    from analyst.orchestrator import Orchestrator
    from analyst.persistence import Database
    from analyst.schemas import SubmitMessageRequest
    transport, sent = openrouter_transport(monkeypatch, failing(429))
    db = Database(tmp_path / "test.db")
    db.initialize()
    db.create_thread("th-1")
    request = SubmitMessageRequest(client_request_id="req-1", text="What is intrinsic value?")
    run, _ = db.submit_message("th-1", request, bound_revision=None)
    await Orchestrator(db=db, transport=transport, settings=transport.settings)._execute_run("th-1", run, request.text, None, None)
    failed = db.get_run("th-1", run.id)
    assert failed is not None and failed.status == "failed"
    assert "OpenRouter vendor/model:free: rate limit reached" in (failed.error or "")
    assert len(sent) == 1
