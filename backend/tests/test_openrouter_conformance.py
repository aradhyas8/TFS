"""Tests for Ticket 02: OpenRouter conformance evaluation via PydanticAI.

Evaluates an isolated PydanticAI integration against TFS's existing transport
contracts using mocked OpenRouter HTTP.

Invariants & Constraints:
- Zero live/external network requests: strictly mocked HTTP (httpx.MockTransport).
- Server-side secrets: sk-or-* credentials are never echoed in errors or responses.
- Routing guards: allow_fallbacks=False, require_parameters=True, free price caps.
- Unset parameter omission: unset reasoning sends no reasoning parameter.
- Tool-call / result identity: deterministic review_portfolio execution matching baseline.
- Label: 'mocked contract verified; real-provider compatibility unverified'.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from analyst.config import Settings
from analyst.financial_data import PersonalFinancialProvider, refresh_financial_data
from analyst.openrouter_adapter import (
    OpenRouterEvaluationAdapter,
    ProtocolComparison,
    build_openrouter_model_settings,
    build_openrouter_provider_config,
)
from analyst.providers import ModelIncomplete, ModelServiceError
from analyst.schemas import Snapshot
from tests.test_analysis import snapshot as get_test_snapshot


class PortfolioAnalysisOutput(BaseModel):
    """Structured answer schema for portfolio review evaluation."""

    summary: str
    total_value: str
    acme_weight: str
    cash_weight: str
    breaches_found: bool = False


def make_openrouter_response(
    *,
    model: str = "vendor/model:free",
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    refusal: str | None = None,
    finish_reason: str = "stop",
    annotations: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
) -> httpx.Response:
    """Build a mock OpenRouter chat completion response."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    if refusal is not None:
        msg["refusal"] = refusal
    if annotations is not None:
        msg["annotations"] = annotations

    choice: dict[str, Any] = {
        "index": 0,
        "message": msg,
        "finish_reason": finish_reason,
    }

    effective_usage = usage or {
        "prompt_tokens": 120,
        "completion_tokens": 45,
        "total_tokens": 165,
        "cost": 0.0,
    }

    payload = {
        "id": "gen-mock-openrouter-1",
        "object": "chat.completion",
        "created": 1729000000,
        "model": model,
        "provider": "OpenAI",
        "choices": [choice],
        "usage": effective_usage,
    }
    return httpx.Response(200, json=payload)


@pytest.fixture
def bound_financial_data():
    """Build the canonical dated test snapshot and refreshed financial evidence."""
    snap = Snapshot.model_validate(get_test_snapshot())
    evidence = asyncio.run(refresh_financial_data(snap, PersonalFinancialProvider()))
    return snap, evidence


# ==============================================================================
# 1. Dependency & Version Pinning
# ==============================================================================


def test_conformance_pinned_dependencies():
    """AC 1: Pin the evaluated framework/provider versions and minimal extras."""
    import pydantic_ai
    import pydantic_ai.models.openrouter

    # Verify pydantic-ai-slim is installed and at evaluated version 2.57.x
    assert pydantic_ai.__version__.startswith("2.57.")
    assert hasattr(pydantic_ai.models.openrouter, "OpenRouterModel")


# ==============================================================================
# 2. Protocol Comparison
# ==============================================================================


def test_conformance_protocol_comparison_documented():
    """AC 2: Verify OpenRouter integration against TFS's existing Responses route."""
    comparison = ProtocolComparison()

    assert comparison.tfs_route == "https://openrouter.ai/api/v1/responses"
    assert comparison.pydantic_ai_route == "https://openrouter.ai/api/v1/chat/completions"
    assert "Response format" in comparison.tfs_wire_envelope
    assert "Chat Completion" in comparison.pydantic_ai_wire_envelope
    assert "function_call_output" in comparison.tfs_tool_continuation
    assert "role='tool'" in comparison.pydantic_ai_tool_continuation
    assert "extra_body" in comparison.pydantic_ai_reasoning_parameter
    assert "flat" in comparison.tfs_citations
    assert "nested" in comparison.pydantic_ai_citations


# ==============================================================================
# 3. Parameter Preservation & Invariants
# ==============================================================================


@pytest.mark.parametrize(
    "model, is_free, is_openai",
    [
        ("vendor/model:free", True, False),
        ("vendor/model", False, False),
        ("openai/gpt-4o-mini", False, True),
        ("openai/gpt-4o:free", True, True),
    ],
)
def test_conformance_preserves_model_and_routing_restrictions(model, is_free, is_openai):
    """AC 3: Preserve selected model, routing/price restrictions, and zero fallbacks."""
    settings = Settings("sk-or-secret", model, base_url="https://openrouter.ai/api/v1", provider="openrouter")
    config = build_openrouter_provider_config(model)
    model_settings = build_openrouter_model_settings(settings)

    assert config["allow_fallbacks"] is False
    assert config["require_parameters"] is True

    if is_openai:
        assert config.get("only") == ["openai"]
    else:
        assert "only" not in config

    if is_free:
        assert config.get("max_price") == {"prompt": 0, "completion": 0}
    else:
        assert "max_price" not in config

    assert model_settings["openrouter_provider"] == config


@pytest.mark.parametrize(
    "effort, expected_in_settings",
    [
        (None, False),
        ("medium", True),
        ("low", True),
    ],
)
def test_conformance_unset_parameter_omission_for_reasoning(effort, expected_in_settings):
    """AC 3: Unset reasoning effort sends no reasoning parameter at all."""
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    model_settings = build_openrouter_model_settings(settings, reasoning_effort=effort)

    if expected_in_settings:
        assert "openrouter_reasoning" in model_settings
        assert model_settings["openrouter_reasoning"]["effort"] == effort
    else:
        assert "openrouter_reasoning" not in model_settings


# ==============================================================================
# 4. Tool Round Trip & TFS Handler Parity
# ==============================================================================


@pytest.mark.anyio
async def test_conformance_tool_round_trip_to_deterministic_handler(bound_financial_data):
    """AC 3 & 4: Full tool round trip calling deterministic review_portfolio handler."""
    snap, evidence = bound_financial_data
    requests_sent: list[dict[str, Any]] = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_sent.append(body)

        if len(requests_sent) == 1:
            # Turn 1: Model requests review_portfolio tool call
            return make_openrouter_response(
                model="vendor/model:free",
                tool_calls=[
                    {
                        "id": "call_rev_1",
                        "type": "function",
                        "function": {
                            "name": "review_portfolio",
                            "arguments": "{}",
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
        else:
            # Turn 2: Model receives tool output and generates final structured response
            structured_data = {
                "summary": "Portfolio successfully reviewed.",
                "total_value": "3600",
                "acme_weight": "0.63888889",
                "cash_weight": "0.36111111",
                "breaches_found": False,
            }
            return make_openrouter_response(
                model="vendor/model:free",
                content=json.dumps(structured_data),
                finish_reason="stop",
                annotations=[
                    {
                        "type": "url_citation",
                        "url_citation": {
                            "url": "https://example.com/filing",
                            "title": "SEDAR+ Filing",
                            "start_index": 0,
                            "end_index": 10,
                        },
                    }
                ],
                usage={"prompt_tokens": 150, "completion_tokens": 50, "total_tokens": 200, "cost": 0.0},
            )

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip(
        "Review my portfolio exposure and cash balance",
        snap,
        evidence,
        output_type=PortfolioAnalysisOutput,
    )

    # 1. Verification of execution success
    assert result.success is True, f"Run failed: {result.error}"
    assert result.error is None
    assert isinstance(result.output, PortfolioAnalysisOutput)

    # 2. Invariant parity with Ticket 01 baseline
    assert result.output.total_value == "3600"
    assert result.output.acme_weight == "0.63888889"
    assert result.output.cash_weight == "0.36111111"
    assert result.output.breaches_found is False

    # 3. Turn verification and tool continuation integrity
    assert len(requests_sent) == 2
    req1 = requests_sent[0]
    req2 = requests_sent[1]

    # Verify model and provider routing sent on request 1
    assert req1["model"] == "vendor/model:free"
    assert req1["provider"]["allow_fallbacks"] is False
    assert req1["provider"]["require_parameters"] is True
    assert req1["provider"]["max_price"] == {"prompt": 0, "completion": 0}

    # Verify tool continuation in request 2:
    # Contains: user message, assistant tool_call message, and role='tool' message with call_rev_1
    messages2 = req2["messages"]
    assert len(messages2) == 3
    assert messages2[0]["role"] == "user"
    assert messages2[1]["role"] == "assistant"
    assert messages2[1]["tool_calls"][0]["id"] == "call_rev_1"
    assert messages2[2]["role"] == "tool"
    assert messages2[2]["tool_call_id"] == "call_rev_1"

    # Verify tool execution output sent to model matches deterministic review
    tool_result_content = json.loads(messages2[2]["content"])
    assert tool_result_content["total_value"] == "3600"
    assert tool_result_content["direct_companies"][0]["company_id"] == "acme"
    assert tool_result_content["direct_companies"][0]["weight"] == "0.63888889"

    # 4. Usage and citations
    assert result.usage is not None
    assert result.usage.total_tokens == 365
    assert result.usage.requests == 2
    assert result.usage.tool_calls == 1
    assert len(result.citations) == 1
    assert result.citations[0]["type"] == "url_citation"
    assert result.compatibility_status == "mocked contract verified; real-provider compatibility unverified"


# ==============================================================================
# 5. Schema Validation & Rejection
# ==============================================================================


@pytest.mark.anyio
async def test_conformance_invalid_structured_output_fails_validation(bound_financial_data):
    """AC 3: Invalid structured output schema fails clearly."""
    snap, evidence = bound_financial_data
    requests_sent = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        requests_sent.append(request)
        if len(requests_sent) == 1:
            return make_openrouter_response(
                tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "review_portfolio", "arguments": "{}"}}],
                finish_reason="tool_calls",
            )
        # Return invalid JSON
        return make_openrouter_response(content="{invalid-json-response", finish_reason="stop")

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip(
        "Review my portfolio", snap, evidence, output_type=PortfolioAnalysisOutput
    )

    assert result.success is False
    assert result.error is not None
    assert isinstance(result.error, ValueError)
    assert "invalid structured output" in str(result.error)


# ==============================================================================
# 6. Error Mapping & Server-side Secrets Preservation
# ==============================================================================


@pytest.mark.parametrize(
    "status, upstream_msg, expected_kind, expected_text",
    [
        (429, "Rate limit reached for free tier", "rate_limit", "rate limit reached"),
        (404, "No endpoints found that support the provided 'reasoning' value", "model_service", "OPENAI_REASONING_EFFORT"),
        (404, "No endpoints found that can handle the requested parameters.", "model_service", "does not support every parameter TFS requires"),
        (404, "No endpoints found for vendor/model.", "model_service", "is unavailable"),
        (400, "Invalid schema provided", "model_service", "rejected the request (HTTP 400)"),
        (402, "Insufficient credits", "model_service", "insufficient credits"),
        (503, "Cloudflare upstream gateway error", "model_service", "is unavailable (HTTP 503)"),
    ],
)
@pytest.mark.anyio
async def test_conformance_error_mapping_without_secrets(
    bound_financial_data, status, upstream_msg, expected_kind, expected_text
):
    """AC 4 & 6: Verify error mapping strips secrets and classifies correctly."""
    snap, evidence = bound_financial_data
    secret = "sk-or-super-secret-token"

    def mock_failing(request: httpx.Request) -> httpx.Response:
        assert secret not in str(request.url)
        return httpx.Response(status, json={"error": {"message": f"{upstream_msg} - {secret}", "code": status}})

    client = AsyncOpenAI(
        api_key=secret,
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_failing)),
    )
    settings = Settings(secret, "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip("Review my portfolio", snap, evidence)

    assert result.success is False
    assert result.error is not None
    assert isinstance(result.error, ModelServiceError)
    assert result.error.kind == expected_kind
    assert expected_text in str(result.error)
    # Critical security invariant: secret must never leak into exception message
    assert secret not in str(result.error)


@pytest.mark.anyio
async def test_conformance_refusal_mapping(bound_financial_data):
    """AC 4: Model refusal is mapped to ValueError without leaking tokens."""
    snap, evidence = bound_financial_data

    def mock_refusal(request: httpx.Request) -> httpx.Response:
        return make_openrouter_response(
            refusal="I cannot provide investment advice or process this portfolio.",
            finish_reason="stop",
        )

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_refusal)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip("Review my portfolio", snap, evidence)

    assert result.success is False
    assert result.error is not None
    assert isinstance(result.error, ValueError)
    assert "Model refused:" in str(result.error)
    assert "cannot provide investment advice" in str(result.error)


@pytest.mark.anyio
async def test_conformance_incomplete_tool_call_mapping(bound_financial_data):
    """AC 4: Incomplete / truncated tool call maps to ModelIncomplete."""
    snap, evidence = bound_financial_data

    def mock_incomplete_call(request: httpx.Request) -> httpx.Response:
        # Tool call with malformed arguments truncated mid-stream
        return make_openrouter_response(
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "review_portfolio", "arguments": "{\"cut"}}]
        )

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_incomplete_call)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip("Review my portfolio", snap, evidence)

    assert result.success is False
    assert result.error is not None
    # Truncated tool JSON causes validation error or ModelIncomplete
    assert isinstance(result.error, (ModelIncomplete, ValueError, ModelServiceError))


# ==============================================================================
# 7. Forced-Tool vs Reasoning Conflicts & Parallel Calls
# ==============================================================================


@pytest.mark.anyio
async def test_conformance_forced_tool_and_reasoning_handling(bound_financial_data):
    """AC 5: Forced-tool and reasoning settings are explicitly configured and conflict-safe."""
    snap, evidence = bound_financial_data
    requests_sent = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_sent.append(body)
        if len(requests_sent) == 1:
            return make_openrouter_response(
                tool_calls=[{"id": "c1", "type": "function", "function": {"name": "review_portfolio", "arguments": "{}"}}],
                finish_reason="tool_calls",
            )
        return make_openrouter_response(content="Done.", finish_reason="stop")

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip(
        "Review my portfolio",
        snap,
        evidence,
        forced_tool="review_portfolio",
        reasoning_effort="medium",
    )

    assert result.success is True
    # Verify request payload carried explicit tool filtering and reasoning
    assert len(requests_sent) >= 1
    req1 = requests_sent[0]
    assert [t["function"]["name"] for t in req1.get("tools", [])] == ["review_portfolio"]
    assert req1.get("reasoning") == {"effort": "medium"}


@pytest.mark.anyio
async def test_conformance_unsupported_forced_tool_and_reasoning_conflict_fails_clearly(bound_financial_data):
    """AC 5: Unsupported forced-tool + reasoning combinations fail clearly.

    When a model profile specifies that forced tool choice with thinking is unsupported,
    the adapter fails clearly instead of silently disabling reasoning or switching models.
    """
    snap, evidence = bound_financial_data

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: make_openrouter_response(content="ok"))),
    )
    # Model profile explicitly declaring that forcing is unsupported with thinking
    profile = {"supports_forced_tool_choice_with_thinking": False}
    settings = Settings("sk-or-secret", "vendor/reasoning-model", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client, profile=profile)

    # Attempting to run with both explicit forcing and reasoning effort
    agent = adapter.create_portfolio_agent()
    from analyst.openrouter_adapter import PortfolioToolDeps
    deps = PortfolioToolDeps(snapshot=snap, evidence=evidence)

    with pytest.raises(Exception) as exc_info:
        await agent.run(
            "Review portfolio",
            deps=deps,
            model_settings={
                "tool_choice": "required",
                "openrouter_reasoning": {"effort": "medium"},
            },
        )

    # Verify clear failure without silent downgrades
    error_msg = str(exc_info.value)
    assert "tool_choice=" in error_msg or "required" in error_msg or "thinking" in error_msg


@pytest.mark.anyio
async def test_conformance_parallel_calls_deterministic_execution(bound_financial_data):
    """AC 5: When multiple tool calls are returned, they execute deterministically."""
    snap, evidence = bound_financial_data
    requests_sent = []

    def mock_transport(request: httpx.Request) -> httpx.Response:
        requests_sent.append(json.loads(request.content))
        if len(requests_sent) == 1:
            # Model returns two tool calls (parallel)
            return make_openrouter_response(
                tool_calls=[
                    {"id": "call_1", "type": "function", "function": {"name": "review_portfolio", "arguments": "{}"}},
                    {"id": "call_2", "type": "function", "function": {"name": "review_portfolio", "arguments": "{}"}},
                ],
                finish_reason="tool_calls",
            )
        return make_openrouter_response(content="Executed both calls successfully.", finish_reason="stop")

    client = AsyncOpenAI(
        api_key="sk-or-secret",
        base_url="https://openrouter.ai/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    settings = Settings("sk-or-secret", "vendor/model:free", base_url="https://openrouter.ai/api/v1", provider="openrouter")
    adapter = OpenRouterEvaluationAdapter(settings, client=client)

    result = await adapter.run_portfolio_round_trip("Review my portfolio", snap, evidence)

    assert result.success is True
    assert len(requests_sent) == 2
    # Second turn must contain responses for both tool calls matching their respective IDs
    messages2 = requests_sent[1]["messages"]
    tool_messages = [m for m in messages2 if m.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert {m["tool_call_id"] for m in tool_messages} == {"call_1", "call_2"}
