import asyncio
import json
import time
from types import SimpleNamespace

import openai
import pytest

from analyst.chatgpt_plan import PlanError, rs256_valid, stream_response


class FakeStream:
    def __init__(self, events, *, forever=False, stall=0.0):
        self.events, self.forever, self.stall, self.closed = list(events), forever, stall, False

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        index = 0
        while self.forever or index < len(self.events):
            if self.stall:
                await asyncio.sleep(self.stall)
            else:
                await asyncio.sleep(0)
            yield self.events[index % len(self.events)] if self.events else SimpleNamespace(type="response.in_progress")
            index += 1

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, stream):
        self.stream, self.calls = stream, []
        self.responses = SimpleNamespace(create=self.create)

    async def create(self, **params):
        self.calls.append(params)
        return self.stream


def item_done(index, name):
    return SimpleNamespace(type="response.output_item.done", output_index=index, item=SimpleNamespace(type="function_call", name=name))


def completed():
    return SimpleNamespace(type="response.completed", response=SimpleNamespace(output=[], status="completed"))


def run(stream, timeout=0.2):
    client = FakeClient(stream)
    started = time.monotonic()
    try:
        return asyncio.run(stream_response(client, {"model": "m", "store": False}, timeout)), client, time.monotonic() - started
    finally:
        assert stream.closed, "the stream must be closed whatever happens"


def test_a_normal_stream_rebuilds_output_from_item_events_and_closes():
    response, client, _ = run(FakeStream([item_done(1, "b"), item_done(0, "a"), completed()]))
    assert [item.name for item in response.output] == ["a", "b"]
    assert client.calls == [{"model": "m", "store": False, "stream": True}]  # one request: no automatic retry


def test_a_stream_without_a_terminal_event_is_an_error_not_a_response():
    with pytest.raises(PlanError, match="no_terminal_event"):
        run(FakeStream([item_done(0, "a")]))


def test_a_stream_that_emits_forever_is_cut_off_at_the_turn_deadline():
    stream = FakeStream([SimpleNamespace(type="response.reasoning_text.delta")], forever=True)
    started = time.monotonic()
    with pytest.raises(openai.APITimeoutError):
        run(stream, timeout=0.2)
    assert time.monotonic() - started < 2


def test_a_stream_that_stalls_between_events_is_cut_off_at_the_turn_deadline():
    started = time.monotonic()
    with pytest.raises(openai.APITimeoutError):
        run(FakeStream([item_done(0, "a"), completed()], stall=30), timeout=0.2)
    assert time.monotonic() - started < 2


def test_a_failed_response_surfaces_its_code_without_retrying():
    failed = SimpleNamespace(type="response.failed", response=SimpleNamespace(error=SimpleNamespace(code="subscription_sharing_usage_limit_exceeded", message="limit")))
    client = FakeClient(FakeStream([failed]))
    with pytest.raises(PlanError) as raised:
        asyncio.run(stream_response(client, {}, 1))
    assert raised.value.code == "subscription_sharing_usage_limit_exceeded" and len(client.calls) == 1


def test_rs256_check_rejects_a_forged_signature():
    assert not rs256_valid(b"payload", b"\x00" * 256, (1 << 2047) + 1, 65537)
    assert not rs256_valid(b"payload", b"\x00" * 10, (1 << 2047) + 1, 65537)


def test_llm_provider_chatgpt_plan_uses_the_oauth_token_and_keeps_openai_available(monkeypatch):
    from analyst import chatgpt_plan
    from analyst.config import Settings
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    monkeypatch.setenv("CHATGPT_PLAN_MODEL", "gpt-6-luna")
    monkeypatch.setattr(chatgpt_plan, "access_token", lambda: "oauth-access")
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt_plan")
    plan = Settings.from_environment()
    assert (plan.provider, plan.api_key, plan.model, plan.base_url, plan.request_timeout) == ("chatgpt_plan", "oauth-access", "gpt-6-luna", "https://api.openai.com/v1", 170.0)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert (Settings.from_environment().provider, Settings.from_environment().api_key) == ("openai", "sk-openai")

    def signed_out():
        raise PlanError("not_signed_in")
    monkeypatch.setattr(chatgpt_plan, "access_token", signed_out)
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt_plan")
    assert Settings.from_environment().api_key == ""  # the API then reports the model as unconfigured (503)


def sse(*events):
    return "".join(f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': index})}\n\n" for index, event in enumerate(events))


def completed_event(output_items):
    response = {"id": "resp_1", "object": "response", "created_at": 1, "model": "gpt-6-luna", "status": "completed", "output": [],
                "parallel_tool_calls": False, "tool_choice": "auto", "tools": [], "error": None, "incomplete_details": None,
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12, "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 0}}}
    return [*({"type": "response.output_item.done", "output_index": index, "item": item} for index, item in enumerate(output_items)),
            {"type": "response.completed", "response": response}]


def test_portfolio_review_through_chatgpt_plan_streams_the_same_strict_request(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from openai import AsyncOpenAI

    from analyst.api import create_app
    from analyst.config import Settings
    from analyst.providers import FakeDataProvider
    from tests.test_analysis import recommendation, snapshot
    sent = []

    def plan_route(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer oauth-access"
        assert payload["stream"] is True and payload["store"] is False and "max_output_tokens" not in payload
        assert payload["parallel_tool_calls"] is False and all(tool["strict"] for tool in payload["tools"])
        assert payload["text"]["format"]["strict"] is True
        if len(sent) == 1:
            assert payload["tool_choice"] == {"type": "function", "name": "review_portfolio"}
            items = [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "review_portfolio", "arguments": "{}", "status": "completed"}]
        else:
            items = [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                      "content": [{"type": "output_text", "text": json.dumps(recommendation()), "annotations": []}]}]
        return httpx.Response(200, text=sse(*completed_event(items)), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr("analyst.providers.AsyncOpenAI", lambda **kwargs: AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(plan_route))))
    settings = Settings("oauth-access", "gpt-6-luna", provider="chatgpt_plan")
    response = TestClient(create_app(settings=settings, data=FakeDataProvider())).post("/api/analyze", json={"question": "How concentrated am I?", "portfolio": snapshot()})
    assert response.status_code == 200, response.text
    assert response.json()["recommendation"]["preferred_action"] == recommendation()["preferred_action"]
    assert len(sent) == 2
