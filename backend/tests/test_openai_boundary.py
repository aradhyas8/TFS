import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from analyst.api import create_app
from analyst.config import Settings
from analyst.providers import FakeDataProvider
from tests.test_analysis import recommendation, snapshot


@pytest.mark.parametrize(
    "failure", [None, "refusal", "incomplete", "service_error", "malformed_json"]
)
def test_production_sdk_request_path_with_fake_http_provider(monkeypatch, failure):
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if len(sent) == 1:
            assert request.url == "https://api.openai.com/v1/responses"
            assert request.headers["Authorization"] == "Bearer sk-test-backend-only-never-browser"
            assert payload["tool_choice"] == {"type": "function", "name": "review_portfolio"}
            output = [
                {
                    "type": "function_call",
                    "id": "fc_test",
                    "call_id": "sdk_tool",
                    "name": "review_portfolio",
                    "arguments": "{}",
                    "status": "completed",
                }
            ]
        else:
            assert payload["tool_choice"] == "none"
            assert payload["store"] is False
            assert payload["text"]["format"]["strict"] is True
            tool_output = json.loads(payload["input"][-1]["output"])
            assert tool_output["total_value"] == "3600"
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
                    else json.dumps(recommendation()),
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
    )
    response = TestClient(app).post(
        "/api/analyze", json={"question": "Review my exposure", "portfolio": snapshot()}
    )
    assert response.status_code == (200 if failure is None else 502), response.text
    assert "sk-test-backend-only-never-browser" not in response.text
    if failure is None:
        assert response.json()["portfolio"]["total_value"] == "3600"
    else:
        assert "recommendation" not in response.json()
