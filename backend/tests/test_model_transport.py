import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, AsyncOpenAI

from analyst.config import Settings
from analyst.providers import (
    ModelTurn,
    OpenAIResponseTransport,
    ScriptedModel,
    ToolCall,
    WorkflowModelAdapter,
)


def test_transport_plain_prose_with_url_citations():
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        output = [
            {
                "type": "message",
                "id": "msg_prose",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Diversification reduces unsystematic risk across positions.",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url": "https://example.com/diversification",
                                "title": "Diversification Overview",
                                "start_index": 0,
                                "end_index": 15,
                            }
                        ],
                    }
                ],
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp_prose",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    messages = [{"role": "user", "content": "Explain diversification."}]
    turn = asyncio.run(transport.respond(messages))

    assert turn.text == "Diversification reduces unsystematic risk across positions."
    assert len(turn.citations) == 1
    assert turn.citations[0]["type"] == "url_citation"
    assert turn.citations[0]["url"] == "https://example.com/diversification"
    assert turn.citations[0]["title"] == "Diversification Overview"
    assert len(turn.calls) == 0
    assert turn.answer is None
    assert len(turn.continuation) == 1
    assert turn.continuation[0]["type"] == "message"
    assert sent[0]["model"] == "gpt-4o"
    assert "tools" not in sent[0]
    assert "text" not in sent[0]


def test_transport_explicit_tools_and_function_call():
    sent = []

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        output = [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "lookup_ticker",
                "arguments": json.dumps({"ticker": "AAPL"}),
                "status": "completed",
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp_call",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    explicit_tools = [
        {
            "type": "function",
            "name": "lookup_ticker",
            "description": "Look up ticker details.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]

    messages = [{"role": "user", "content": "Look up AAPL"}]
    turn = asyncio.run(
        transport.respond(
            messages,
            tools=explicit_tools,
            tool_choice="auto",
        )
    )

    assert len(turn.calls) == 1
    assert turn.calls[0] == ToolCall("call_1", "lookup_ticker", json.dumps({"ticker": "AAPL"}))
    assert turn.text is None
    assert turn.answer is None
    assert len(turn.continuation) == 1
    assert turn.continuation[0]["type"] == "function_call"
    assert sent[0]["tools"] == explicit_tools
    assert sent[0]["tool_choice"] == "auto"


def test_transport_strict_workflow_json_schema():
    sent = []
    schema = {
        "type": "object",
        "properties": {"action": {"type": "string"}},
        "required": ["action"],
        "additionalProperties": False,
    }

    def fake_openai(request):
        payload = json.loads(request.content)
        sent.append(payload)
        output = [
            {
                "type": "message",
                "id": "msg_schema",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps({"action": "buy"}),
                        "annotations": [],
                    }
                ],
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp_schema",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    messages = [{"role": "user", "content": "Synthesize final recommendation"}]
    turn = asyncio.run(
        transport.respond(
            messages,
            output_schema=schema,
            output_schema_name="portfolio_recommendation",
        )
    )

    assert turn.answer == {"action": "buy"}
    assert turn.text == json.dumps({"action": "buy"})
    assert sent[0]["text"]["format"] == {
        "type": "json_schema",
        "name": "portfolio_recommendation",
        "schema": schema,
        "strict": True,
    }


def test_transport_refusal_raises_error():
    def fake_openai(_request):
        output = [
            {
                "type": "message",
                "id": "msg_refusal",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "refusal", "refusal": "Cannot process financial questions."}],
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp_refusal",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    with pytest.raises(ValueError, match="Model refused"):
        asyncio.run(transport.respond([{"role": "user", "content": "hi"}]))


def test_transport_malformed_json_raises_error():
    schema = {
        "type": "object",
        "properties": {"action": {"type": "string"}},
        "required": ["action"],
        "additionalProperties": False,
    }

    def fake_openai(_request):
        output = [
            {
                "type": "message",
                "id": "msg_bad",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "not valid json", "annotations": []}],
            }
        ]
        return httpx.Response(
            200,
            json={
                "id": "resp_bad",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "completed",
                "output": output,
                "parallel_tool_calls": False,
                "error": None,
                "incomplete_details": None,
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    with pytest.raises(ValueError, match="invalid structured output"):
        asyncio.run(
            transport.respond(
                [{"role": "user", "content": "hi"}],
                output_schema=schema,
            )
        )


def test_transport_incomplete_response_raises_diagnostic_error():
    def fake_openai(_request):
        return httpx.Response(
            200,
            json={
                "id": "resp_inc",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 16000,
                    "output_tokens_details": {"reasoning_tokens": 15000},
                },
                "output": [],
            },
        )

    settings = Settings("sk-test", "gpt-4o")
    transport = OpenAIResponseTransport(
        settings,
        client=AsyncOpenAI(
            api_key="sk-test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)),
        ),
    )

    with pytest.raises(ValueError, match="The model response is incomplete"):
        asyncio.run(transport.respond([{"role": "user", "content": "hi"}]))


def test_transport_chatgpt_plan_streaming_deadline_timeout():
    class FakeTimeoutStream:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(10)
            return SimpleNamespace(type="response.in_progress")

        async def close(self):
            self.closed = True

    class FakeTimeoutClient:
        def __init__(self):
            self.responses = SimpleNamespace(create=self.create)

        async def create(self, **_params):
            return FakeTimeoutStream()

    settings = Settings("sk-token", "gpt-4o", request_timeout=0.1, provider="chatgpt_plan")
    transport = OpenAIResponseTransport(
        settings,
        client=FakeTimeoutClient(),  # type: ignore[arg-type]
    )

    with pytest.raises(APITimeoutError):
        asyncio.run(transport.respond([{"role": "user", "content": "hi"}], timeout=0.05))


def test_workflow_model_adapter_delegates_to_transport():
    turns = [
        ModelTurn(calls=[ToolCall("call_1", "review_portfolio", "{}")]),
        ModelTurn(answer={"preferred_action": "no_action"}, text=json.dumps({"preferred_action": "no_action"})),
    ]
    scripted = ScriptedModel(turns)
    adapter = WorkflowModelAdapter(scripted)

    # First turn: requires tool
    req_payload = {"question": "Review my exposure", "portfolio": {"positions": []}}
    messages = [
        {"role": "system", "content": "Instructions"},
        {"role": "user", "content": json.dumps(req_payload)},
    ]

    turn1 = asyncio.run(adapter.respond(messages, require_tool=True))
    assert len(turn1.calls) == 1
    assert turn1.calls[0].name == "review_portfolio"

    turn2 = asyncio.run(adapter.respond(messages, require_tool=False, final=True))
    assert turn2.answer == {"preferred_action": "no_action"}


def test_a_tool_call_turn_drops_preamble_prose_from_its_continuation():
    """OpenRouter models narrate before a call ("Let me check"). The call turn keeps only reasoning and the call."""
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Let me check your holdings.", "annotations": []}]},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_holdings", "arguments": "{}", "status": "completed"},
    ]

    def fake_openai(request):
        return httpx.Response(200, json={"id": "resp_call", "object": "response", "created_at": 1, "model": "m", "status": "completed",
                                         "output": output, "parallel_tool_calls": False, "error": None, "incomplete_details": None})

    transport = OpenAIResponseTransport(Settings("sk-test", "m"), client=AsyncOpenAI(
        api_key="sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai))))
    turn = asyncio.run(transport.respond([{"role": "user", "content": "Update my portfolio"}], tools=[], tool_choice="auto"))
    assert turn.calls == [ToolCall("call_1", "get_holdings", "{}")] and turn.text is None
    assert [item["type"] for item in turn.continuation] == ["reasoning", "function_call"]


def test_an_openrouter_turn_with_parallel_calls_keeps_only_the_first_call():
    """OpenRouter cannot be told parallel_tool_calls=false, so extra calls are dropped; the model asks again next turn."""
    output = [
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_messages", "arguments": "{}", "status": "completed"},
        {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "review_portfolio", "arguments": "{}", "status": "completed"},
    ]

    def fake_openai(request):
        return httpx.Response(200, json={"id": "resp_call", "object": "response", "created_at": 1, "model": "m", "status": "completed",
                                         "output": output, "parallel_tool_calls": True, "error": None, "incomplete_details": None})

    def turn_for(settings):
        transport = OpenAIResponseTransport(settings, client=AsyncOpenAI(
            api_key="sk-test", http_client=httpx.AsyncClient(transport=httpx.MockTransport(fake_openai))))
        return asyncio.run(transport.respond([{"role": "user", "content": "Update my portfolio"}], tools=[], tool_choice="auto"))

    turn = turn_for(Settings("sk-or", "vendor/m", provider="openrouter", base_url="https://openrouter.ai/api/v1"))
    assert turn.calls == [ToolCall("call_1", "get_messages", "{}")]
    assert [(item["type"], item.get("call_id")) for item in turn.continuation] == [("reasoning", None), ("function_call", "call_1")]
    # OpenAI is asked for one call at a time, so a violation there still reaches the orchestrator's check.
    assert len(turn_for(Settings("sk-test", "m")).calls) == 2
