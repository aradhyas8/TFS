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
def test_production_sdk_request_path_with_fake_http_provider(monkeypatch, failure):
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1 or (failure == "financial_tools" and len(sent) <= 4) or (failure == "comparison" and len(sent) == 2) or (failure == "stock" and len(sent) <= 5):
            assert request.url == "https://api.openai.com/v1/responses"
            assert request.headers["Authorization"] == "Bearer sk-test-backend-only-never-browser"
            assert payload["tool_choice"] == ({"type": "function", "name": "review_portfolio"} if len(sent) == 1 else "auto")
            assert {tool["name"] for tool in payload["tools"]} == {
                "review_portfolio", "resolve_identities", "get_quotes", "get_fx", "check_proposed_changes", "calculate_comparison", "get_sec_filings", "get_issuer_material", "calculate_company_cases"
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
        settings=Settings("sk-test-backend-only-never-browser", "test-model"),
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
