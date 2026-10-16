"""Tests for Ticket 03: ChatGPT sign-in and streamed tool round trip conformance.

Evaluates an isolated PydanticAI integration against TFS's existing ChatGPT-plan
contracts, encrypted credential ownership, token audience/scopes, single refresh
ownership, streaming-only Responses route, store=false, parameter omission,
tool continuation, reasoning state, turn deadlines, and safe error mapping.

Invariants & Constraints:
- Zero live/external network requests: strictly mocked HTTP (httpx.MockTransport).
- Mock OS credential boundaries as needed; never read real credentials or call real API.
- Keep TFS DPAPI/Keychain credential ownership and account separation.
- Single refresh owner: concurrent refresh does not reuse invalidated tokens.
- Streaming-only, store=false, unsupported parameter omission, and turn deadlines.
- Secrets (Bearer tokens, JWTs, keys) are never exposed in errors or logs.
- Label: 'mocked contract verified; real-provider compatibility unverified'.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import httpx
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from analyst import chatgpt_plan
from analyst.chatgpt_plan import PLAN_SCOPE, PlanError
from analyst.chatgpt_plan_adapter import (
    ChatGPTPlanBudgetLimits,
    ChatGPTPlanComparison,
    ChatGPTPlanEvaluationAdapter,
    build_chatgpt_plan_model_settings,
    sanitize_secret_text,
)
from analyst.config import Settings
from analyst.financial_data import PersonalFinancialProvider, refresh_financial_data
from analyst.schemas import Snapshot
from tests.test_analysis import snapshot as get_test_snapshot


class PortfolioAnalysisOutput(BaseModel):
    """Structured answer schema for portfolio review evaluation."""

    summary: str
    total_value: str
    acme_weight: str
    cash_weight: str
    breaches_found: bool = False


def sse_event(event_type: str, data: dict[str, Any], sequence_number: int) -> str:
    """Format a single Server-Sent Event."""
    payload = {**data, "sequence_number": sequence_number}
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


def make_responses_sse_stream(*events: dict[str, Any]) -> str:
    """Join multiple event dictionaries into an SSE stream payload."""
    return "".join(sse_event(e["type"], e, idx) for idx, e in enumerate(events))


def make_function_call_events(
    *,
    model: str = "gpt-4o",
    tool_name: str = "review_portfolio",
    tool_args: dict[str, Any] | None = None,
    call_id: str = "call_mock_1",
    item_id: str = "fc_mock_1",
    response_id: str = "resp_mock_1",
) -> list[dict[str, Any]]:
    """Generate SSE events for a model tool call turn in Responses API."""
    args_json = json.dumps(tool_args or {})
    fc_item = {
        "type": "function_call",
        "id": item_id,
        "call_id": call_id,
        "name": tool_name,
        "arguments": args_json,
        "status": "completed",
    }
    return [
        {
            "type": "response.created",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 1729000001,
                "model": model,
                "status": "in_progress",
                "output": [],
            },
        },
        {"type": "response.output_item.added", "output_index": 0, "item": fc_item},
        {"type": "response.output_item.done", "output_index": 0, "item": fc_item},
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 1729000001,
                "model": model,
                "status": "completed",
                "output": [fc_item],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 6,
                    "total_tokens": 18,
                },
            },
        },
    ]


def make_text_response_events(
    text: str,
    *,
    model: str = "gpt-4o",
    response_id: str = "resp_mock_2",
    item_id: str = "msg_mock_2",
) -> list[dict[str, Any]]:
    """Generate SSE events for a model text completion turn in Responses API."""
    msg_item = {
        "type": "message",
        "id": item_id,
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }
    return [
        {
            "type": "response.created",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 1729000002,
                "model": model,
                "status": "in_progress",
                "output": [],
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": item_id,
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            },
        },
        {
            "type": "response.content_part.added",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": ""},
        },
        {
            "type": "response.output_text.delta",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "delta": text,
        },
        {
            "type": "response.output_text.done",
            "item_id": item_id,
            "output_index": 0,
            "content_index": 0,
            "text": text,
        },
        {"type": "response.output_item.done", "output_index": 0, "item": msg_item},
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "object": "response",
                "created_at": 1729000002,
                "model": model,
                "status": "completed",
                "output": [msg_item],
                "usage": {
                    "input_tokens": 25,
                    "output_tokens": 15,
                    "total_tokens": 40,
                },
            },
        },
    ]


@pytest.fixture
def bound_financial_data():
    """Build the canonical dated test snapshot and refreshed financial evidence."""
    snap = Snapshot.model_validate(get_test_snapshot())
    evidence = asyncio.run(refresh_financial_data(snap, PersonalFinancialProvider()))
    return snap, evidence


# ==============================================================================
# 1. Dependency Pinning & PydanticAI Version Check
# ==============================================================================


def test_conformance_pinned_dependencies():
    """AC 1: Pin the evaluated framework/provider versions and minimal extras."""
    import openai
    import pydantic_ai
    import pydantic_ai.models.openai_codex

    # Verify pydantic-ai-slim is at evaluated version 2.57.x
    assert pydantic_ai.__version__.startswith("2.57.")
    # Verify openai SDK version is kept in pinned range without requiring openai>=3.26
    assert openai.__version__.startswith("2.")
    assert hasattr(pydantic_ai.models.openai_codex, "OpenAICodexModel")


# ==============================================================================
# 2. Endpoint, Scopes, and Identity Comparison & Custom Adapter Verdict
# ==============================================================================


def test_conformance_comparison_and_adapter_verdict():
    """AC 1: Compare actual sign-in with framework Codex integration & document adapter verdict."""
    comparison = ChatGPTPlanComparison()

    # Verify TFS Sign in with ChatGPT contracts
    assert comparison.tfs_sign_in_endpoint == "https://auth.openai.com/api/accounts/authorize"
    assert comparison.tfs_token_endpoint == "https://auth.openai.com/api/accounts/oauth/token"
    assert comparison.tfs_resource_base_url == "https://api.openai.com/v1"
    assert "chatgpt.tokens.use.direct" in comparison.tfs_scopes
    assert "resource.invoke" in comparison.tfs_scopes
    assert "DPAPI" in comparison.tfs_credential_storage

    # Verify framework Codex integration differences
    assert comparison.pydantic_ai_codex_authorize_endpoint == "https://auth.openai.com/oauth/authorize"
    assert comparison.pydantic_ai_codex_token_endpoint == "https://auth.openai.com/oauth/token"
    assert comparison.pydantic_ai_codex_base_url == "https://chatgpt.com/backend-api/codex"
    assert "chatgpt.tokens.use.direct" not in comparison.pydantic_ai_codex_scopes
    assert ".codex" in comparison.pydantic_ai_codex_credential_storage

    # Document clear verdict: custom adapter is strictly required
    assert comparison.adapter_required is True
    assert "custom adapter is required" in comparison.adapter_rationale.lower()
    assert "~/.codex/auth.json" in comparison.adapter_rationale


# ==============================================================================
# 3. Credential Ownership, DPAPI/Keychain Storage, & Account Separation
# ==============================================================================


def test_conformance_preserves_dpapi_and_keychain_ownership_no_external_dependency(tmp_path, monkeypatch):
    """AC 2: Keep TFS's DPAPI/Keychain credential ownership and account separation."""
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "tfs-plan-dir"))

    # Verify directory is TFS-owned
    cred_dir = chatgpt_plan.home()
    assert "tfs-plan-dir" in str(cred_dir)
    assert not str(cred_dir).endswith(".codex")

    # Verify fake credential storage round trip through TFS's record serializer
    record = {
        "client_id": "eval-client-123",
        "subject": "sub-eval-user-1",
        "email": "investor@example.com",
        "access_token": "oauth-access-eval-token",
        "refresh_token": "eval-refresh-token",
        "id_token": "fake-id-token",
        "scopes": [PLAN_SCOPE, "openid", "profile"],
        "expires_in": 3600,
        "saved_at": time.time(),
    }

    # Mock OS DPAPI/Keychain encryption functions to verify file segregation
    encrypted_store: dict[str, bytes] = {}

    def mock_write_record(cid: str, data: bytes):
        encrypted_store[cid] = data

    def mock_read_record(cid: str):
        return encrypted_store.get(cid)

    monkeypatch.setattr(chatgpt_plan, "_write_record", mock_write_record)
    monkeypatch.setattr(chatgpt_plan, "_read_record", mock_read_record)

    chatgpt_plan._save_host({"ext_agent_host_id": "urn:uuid:eval", "client_id": record["client_id"]})
    chatgpt_plan.save(record)

    loaded = chatgpt_plan.load()
    assert loaded["client_id"] == "eval-client-123"
    assert loaded["subject"] == "sub-eval-user-1"
    assert loaded["access_token"] == "oauth-access-eval-token"


def test_conformance_sign_out_and_account_separation(tmp_path, monkeypatch):
    """AC 2: Verify sign-out revokes credentials, deletes secrets, and preserves account separation."""
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "plan"))
    revocation_called = []

    def mock_post(url, **kwargs):
        revocation_called.append({"url": url, "data": kwargs.get("data")})
        return httpx.Response(200, json={"status": "revoked"})

    def mock_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"revocation_endpoint": "https://auth.openai.com/api/accounts/revoke"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "post", mock_post)
    monkeypatch.setattr(httpx, "get", mock_get)

    record = {
        "client_id": "eval-client-sep",
        "subject": "investor-sub-primary",
        "access_token": "secret-access-token",
        "refresh_token": "secret-refresh-token",
        "id_token": "id-token",
        "scopes": [PLAN_SCOPE],
        "expires_in": 3600,
        "saved_at": time.time(),
    }
    chatgpt_plan._save_host({"ext_agent_host_id": "urn:uuid:1", "client_id": "eval-client-sep"})

    records: dict[str, bytes] = {"eval-client-sep": json.dumps(record).encode()}
    monkeypatch.setattr(chatgpt_plan, "_write_record", lambda cid, d: records.update({cid: d}))
    monkeypatch.setattr(chatgpt_plan, "_read_record", lambda cid: records.get(cid))
    monkeypatch.setattr(chatgpt_plan, "_delete_record", lambda cid: records.pop(cid, None))

    assert chatgpt_plan.load()["subject"] == "investor-sub-primary"

    # Execute logout
    revoked = chatgpt_plan.logout()
    assert revoked is True
    assert len(revocation_called) == 1
    assert revocation_called[0]["url"] == "https://auth.openai.com/api/accounts/revoke"

    # Credentials deleted from store
    with pytest.raises(PlanError) as exc_info:
        chatgpt_plan.load()
    assert exc_info.value.code == "not_signed_in"

    # Host mapping retains subject so account separation is enforced on reconnect
    host = chatgpt_plan._host()
    assert host.get("subject") == "investor-sub-primary"


# ==============================================================================
# 4. Single Refresh Owner & Concurrent Refresh Invalidation Safety
# ==============================================================================


def test_conformance_single_refresh_owner_and_concurrent_refresh_safety(tmp_path, monkeypatch):
    """AC 3: One refresh owner; rotated tokens persisted; concurrent refresh does not reuse invalidated tokens."""
    monkeypatch.setenv("CHATGPT_PLAN_DIR", str(tmp_path / "plan"))

    # Initial record near expiry (< 900s remaining)
    initial_record = {
        "client_id": "client-concurr",
        "subject": "sub-1",
        "access_token": "old-access-token",
        "refresh_token": "old-refresh-token-must-not-reuse",
        "id_token": "dummy-id",
        "scopes": [PLAN_SCOPE],
        "expires_in": 3600,
        "saved_at": time.time() - 3000,  # 600s left, inside REFRESH_MARGIN (900)
    }

    current_record = dict(initial_record)
    chatgpt_plan._save_host({"ext_agent_host_id": "urn:uuid:test", "client_id": "client-concurr"})

    monkeypatch.setattr(chatgpt_plan, "_write_record", lambda cid, d: current_record.update(json.loads(d.decode())))
    monkeypatch.setattr(chatgpt_plan, "_read_record", lambda cid: json.dumps(current_record).encode())

    refresh_counter = 0
    token_lock = threading.Lock()

    def mock_post_form(fields: dict[str, str]) -> dict[str, Any]:
        nonlocal refresh_counter
        with token_lock:
            refresh_counter += 1
            # If a refresh token was already used once, upstream would reject it:
            if fields.get("refresh_token") != "old-refresh-token-must-not-reuse" and refresh_counter > 1:
                raise PlanError("invalid_grant", "Refresh token was already invalidated")
            time.sleep(0.05)  # Simulate network latency during refresh
            return {
                "access_token": f"rotated-access-token-{refresh_counter}",
                "refresh_token": f"rotated-refresh-token-{refresh_counter}",
                "expires_in": 3600,
                "scope": f"{PLAN_SCOPE} openid profile",
            }

    monkeypatch.setattr(chatgpt_plan, "_post_form", mock_post_form)

    # Launch 5 concurrent threads attempting to get the access token simultaneously
    tokens_returned: list[str] = []
    errors: list[Exception] = []

    def worker():
        try:
            tok = chatgpt_plan.access_token()
            tokens_returned.append(tok)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Zero errors occurred: no invalid_grant or racing refresh
    assert len(errors) == 0
    assert len(tokens_returned) == 5

    # Exactly ONE HTTP refresh request was made!
    assert refresh_counter == 1

    # All threads received the identical rotated access token
    assert all(t == "rotated-access-token-1" for t in tokens_returned)

    # The persisted record was updated with the new rotated refresh token
    persisted = chatgpt_plan.load()
    assert persisted["access_token"] == "rotated-access-token-1"
    assert persisted["refresh_token"] == "rotated-refresh-token-1"


# ==============================================================================
# 5. Streaming-Only, store=false, and Unsupported Parameter Omission
# ==============================================================================


@pytest.mark.parametrize(
    "effort, expected_reasoning",
    [
        (None, None),
        ("medium", {"effort": "medium"}),
        ("high", {"effort": "high"}),
        ("low", {"effort": "low"}),
    ],
)
def test_conformance_streaming_store_false_parameter_omission_and_reasoning(
    effort, expected_reasoning
):
    """AC 4: Verify streaming-only behavior, store=false, parameter omission, and reasoning state."""
    requests_received: list[dict[str, Any]] = []

    def plan_route(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        requests_received.append(data)
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer oauth-access-eval"

        # Wire assertions for ChatGPT plan route:
        assert data["stream"] is True, "The route must be streaming-only"
        assert data["store"] is False, "The route must enforce store=false"

        # Parameter omission assertions:
        assert "max_output_tokens" not in data, "max_output_tokens must be omitted"
        assert "max_tokens" not in data, "max_tokens must be omitted"
        assert "temperature" not in data, "temperature must be omitted"
        assert "top_p" not in data, "top_p must be omitted"

        # Reasoning assertions:
        if expected_reasoning is None:
            assert "reasoning" not in data or data.get("reasoning") is None
        else:
            assert data["reasoning"]["effort"] == expected_reasoning["effort"]

        events = make_text_response_events("Response completed successfully.")
        return httpx.Response(
            200,
            text=make_responses_sse_stream(*events),
            headers={"content-type": "text/event-stream"},
        )

    mock_client = AsyncOpenAI(
        api_key="oauth-access-eval",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(plan_route)),
    )

    settings = Settings(
        api_key="oauth-access-eval",
        model="o3-mini",
        provider="chatgpt_plan",
        reasoning_effort=effort,
        request_timeout=30.0,
    )
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-access-eval", client=mock_client)
    agent = adapter.create_portfolio_agent()
    model_settings = build_chatgpt_plan_model_settings(settings, reasoning_effort=effort)

    snap = Snapshot.model_validate(get_test_snapshot())
    evidence = asyncio.run(refresh_financial_data(snap, PersonalFinancialProvider()))
    from analyst.chatgpt_plan_adapter import PortfolioToolDeps

    deps = PortfolioToolDeps(snapshot=snap, evidence=evidence)

    result = asyncio.run(agent.run("Analyze status", deps=deps, model_settings=model_settings))
    assert result.output == "Response completed successfully."
    assert len(requests_received) == 1


# ==============================================================================
# 6. Tool Continuation Round Trip to Deterministic Handler (Financial Parity)
# ==============================================================================


def test_conformance_tool_round_trip_to_deterministic_handler(bound_financial_data):
    """AC 4: Run a multi-turn tool round trip matching Ticket 01 baseline data."""
    snapshot, evidence = bound_financial_data
    requests_received: list[dict[str, Any]] = []

    def plan_route(request: httpx.Request) -> httpx.Response:
        data = json.loads(request.content)
        requests_received.append(data)
        idx = len(requests_received)

        if idx == 1:
            # Turn 1: Model calls the review_portfolio tool
            assert data["stream"] is True and data["store"] is False
            fc_events = make_function_call_events(
                tool_name="review_portfolio",
                tool_args={},
                call_id="call_eval_1",
                item_id="fc_eval_1",
            )
            return httpx.Response(
                200,
                text=make_responses_sse_stream(*fc_events),
                headers={"content-type": "text/event-stream"},
            )

        # Turn 2: PydanticAI returns tool output and requests final structured analysis
        input_items = data.get("input", [])
        item_types = [it.get("type") for it in input_items]
        assert "function_call" in item_types
        assert "function_call_output" in item_types

        # Verify tool continuation pair
        fc_out = next(it for it in input_items if it.get("type") == "function_call_output")
        assert fc_out["call_id"] == "call_eval_1"
        tool_result = json.loads(fc_out["output"])

        # Baseline arithmetic assertions (from Ticket 01):
        assert float(tool_result["total_value"]) == 3600.0
        assert tool_result["reporting_currency"] == "CAD"
        assert abs(float(tool_result["direct_companies"][0]["weight"]) - 0.63888889) < 1e-6
        assert abs(float(tool_result["cash_value"]) - 1300.0) < 1e-6

        # Synthesize final structured answer matching schema
        output_payload = PortfolioAnalysisOutput(
            summary="Portfolio reviewed successfully with deterministic handler.",
            total_value="3600.00 CAD",
            acme_weight="63.89%",
            cash_weight="36.11%",
            breaches_found=False,
        )
        ans_events = make_text_response_events(output_payload.model_dump_json())
        return httpx.Response(
            200,
            text=make_responses_sse_stream(*ans_events),
            headers={"content-type": "text/event-stream"},
        )

    mock_client = AsyncOpenAI(
        api_key="oauth-access-eval",
        base_url="https://api.openai.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(plan_route)),
    )

    settings = Settings(
        api_key="oauth-access-eval",
        model="gpt-4o",
        provider="chatgpt_plan",
        request_timeout=30.0,
    )
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-access-eval", client=mock_client)

    result = asyncio.run(
        adapter.run_portfolio_round_trip(
            "Review my portfolio holdings and allocation.",
            snapshot,
            evidence,
            output_type=PortfolioAnalysisOutput,
        )
    )

    assert result.success is True
    assert result.error is None
    assert isinstance(result.output, PortfolioAnalysisOutput)
    assert result.output.total_value == "3600.00 CAD"
    assert result.output.acme_weight == "63.89%"
    assert result.output.cash_weight == "36.11%"
    assert result.raw_turns == 4
    assert result.usage is not None
    assert result.usage.requests == 2
    assert result.usage.tool_calls == 1
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.compatibility_status == "mocked contract verified; real-provider compatibility unverified"


# ==============================================================================
# 7. Turn Deadline Handling (Stalled / Infinite Stream Cut-off)
# ==============================================================================


def test_conformance_deadline_handling_stream_cutoff(bound_financial_data):
    """AC 4: Stalled/infinite streams are cut off cleanly at turn deadline without retry."""
    snapshot, evidence = bound_financial_data
    attempts = 0

    async def slow_stream(request: httpx.Request):
        nonlocal attempts
        attempts += 1

        async def endless_gen():
            yield b"event: response.created\ndata: {\"type\": \"response.created\", \"response\": {\"id\": \"r1\", \"model\": \"gpt-4o\", \"output\": []}}\n\n"
            while True:
                await asyncio.sleep(0.05)
                yield b"event: response.output_text.delta\ndata: {\"type\": \"response.output_text.delta\", \"item_id\": \"msg_1\", \"output_index\": 0, \"content_index\": 0, \"delta\": \"thinking...\"}\n\n"

        return httpx.Response(200, content=endless_gen(), headers={"content-type": "text/event-stream"})

    mock_client = AsyncOpenAI(
        api_key="oauth-access-eval",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow_stream)),
    )

    settings = Settings(
        api_key="oauth-access-eval",
        model="gpt-4o",
        provider="chatgpt_plan",
        request_timeout=0.2,  # Tight 200ms turn timeout
    )
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-access-eval", client=mock_client)

    started = time.monotonic()
    result = asyncio.run(
        adapter.run_portfolio_round_trip(
            "Review portfolio with deadline",
            snapshot,
            evidence,
            timeout=0.2,
        )
    )
    duration = time.monotonic() - started

    assert result.success is False
    assert isinstance(result.error, openai.APITimeoutError)
    # Proves single attempt without automatic retry
    assert attempts == 1
    assert duration < 2.0, "Stream cut-off must be bounded by turn deadline"


# ==============================================================================
# 8. Error Mapping Without Secrets
# ==============================================================================


@pytest.mark.parametrize(
    "status_code, response_body, expected_code, expected_msg_part",
    [
        (401, '{"error": {"code": "invalid_api_key", "message": "Bearer secret-oauth-token-invalid"}}', "not_signed_in", "expired or revoked"),
        (429, '{"error": {"code": "subscription_sharing_usage_limit_exceeded"}}', "subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_limit_exceeded"),
        (429, '{"error": {"code": "subscription_sharing_usage_unavailable"}}', "subscription_sharing_usage_unavailable", "subscription_sharing_usage_unavailable"),
        (429, '{"error": {"code": "rate_limit_exceeded"}}', "rate_limit_exceeded", "rate_limit_exceeded"),
        (404, '{"error": {"code": "model_not_found", "message": "Model not found"}}', "model_not_found", "model_not_found"),
        (400, '{"error": {"message": "bad parameter"}}', "request_failed", "HTTP 400"),
        (503, '{"error": {"message": "Service unavailable"}}', "request_failed", "HTTP 503"),
    ],
)
def test_conformance_error_mapping_without_secrets(
    bound_financial_data, status_code, response_body, expected_code, expected_msg_part
):
    """AC 5: Error outcomes produce safe domain failures; logs/messages contain no secrets."""
    snapshot, evidence = bound_financial_data
    attempts = 0

    def mock_error_route(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, text=response_body, headers={"content-type": "application/json"})

    mock_client = AsyncOpenAI(
        api_key="secret-bearer-token-12345",
        base_url="https://api.openai.com/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(mock_error_route)),
    )

    settings = Settings(
        api_key="secret-bearer-token-12345",
        model="gpt-4o",
        provider="chatgpt_plan",
        request_timeout=30.0,
    )
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="secret-bearer-token-12345", client=mock_client)

    result = asyncio.run(adapter.run_portfolio_round_trip("Test error", snapshot, evidence))

    assert result.success is False
    assert isinstance(result.error, PlanError)
    assert result.error.code == expected_code
    assert expected_msg_part in str(result.error)

    # Invariant: Secret Bearer token is scrubbed and never present in error strings
    err_str = str(result.error)
    assert "secret-bearer-token-12345" not in err_str
    assert "secret-oauth-token-invalid" not in err_str
    assert attempts == 1


def test_conformance_stream_interruption_and_malformed_handling(bound_financial_data):
    """AC 5: Interrupted/malformed streams produce safe existing error outcomes."""
    snapshot, evidence = bound_financial_data

    # Stream ends abruptly without response.completed
    events = [
        {
            "type": "response.created",
            "response": {
                "id": "r1",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4o",
                "status": "in_progress",
                "output": [],
            },
        },
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "message",
                "id": "msg_1",
                "role": "assistant",
                "status": "in_progress",
                "content": [],
            },
        },
    ]
    truncated_sse = make_responses_sse_stream(*events)

    mock_client = AsyncOpenAI(
        api_key="oauth-token",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text=truncated_sse, headers={"content-type": "text/event-stream"})
            )
        ),
    )

    settings = Settings(api_key="oauth-token", model="gpt-4o", provider="chatgpt_plan", request_timeout=30.0)
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-token", client=mock_client)

    result = asyncio.run(adapter.run_portfolio_round_trip("Test truncated", snapshot, evidence))

    assert result.success is False
    assert isinstance(result.error, PlanError)
    assert result.error.code == "no_terminal_event"
    assert "ended without response.completed" in str(result.error)


def test_conformance_refusal_mapping(bound_financial_data):
    """AC 5: Verify model refusal events map to safe ValueError without secrets."""
    snapshot, evidence = bound_financial_data
    refusal_events = [
        {"type": "response.created", "response": {"id": "r_refusal", "object": "response", "created_at": 1, "model": "gpt-4o", "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "id": "m_ref", "role": "assistant", "status": "in_progress", "content": []}},
        {"type": "response.refusal.delta", "item_id": "m_ref", "output_index": 0, "content_index": 0, "delta": "I cannot provide financial advice on this portfolio."},
        {"type": "response.refusal.done", "item_id": "m_ref", "output_index": 0, "content_index": 0, "refusal": "I cannot provide financial advice on this portfolio."},
        {"type": "response.output_item.done", "output_index": 0, "item": {"type": "message", "id": "m_ref", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "I cannot provide financial advice on this portfolio."}]}},
        {"type": "response.completed", "response": {"id": "r_refusal", "object": "response", "created_at": 1, "model": "gpt-4o", "status": "completed", "output": []}},
    ]

    mock_client = AsyncOpenAI(
        api_key="oauth-token",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text=make_responses_sse_stream(*refusal_events), headers={"content-type": "text/event-stream"})
            )
        ),
    )

    settings = Settings(api_key="oauth-token", model="gpt-4o", provider="chatgpt_plan")
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-token", client=mock_client)

    result = asyncio.run(adapter.run_portfolio_round_trip("Test refusal", snapshot, evidence))
    assert result.success is False
    assert isinstance(result.error, ValueError)
    assert "Model refused" in str(result.error)
    assert "financial advice" in str(result.error)


def test_conformance_invalid_structured_output_fails_validation(bound_financial_data):
    """AC 5: Malformed JSON output fails validation gracefully."""
    snapshot, evidence = bound_financial_data
    # Return malformed JSON that fails PortfolioAnalysisOutput schema validation
    events = make_text_response_events("This is plain text, not JSON.")

    mock_client = AsyncOpenAI(
        api_key="oauth-token",
        base_url="https://api.openai.com/v1",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(200, text=make_responses_sse_stream(*events), headers={"content-type": "text/event-stream"})
            )
        ),
    )

    settings = Settings(api_key="oauth-token", model="gpt-4o", provider="chatgpt_plan")
    adapter = ChatGPTPlanEvaluationAdapter(settings, token="oauth-token", client=mock_client)

    result = asyncio.run(
        adapter.run_portfolio_round_trip(
            "Test invalid JSON",
            snapshot,
            evidence,
            output_type=PortfolioAnalysisOutput,
        )
    )
    assert result.success is False
    assert isinstance(result.error, ValueError)
    assert "invalid structured output" in str(result.error)


# ==============================================================================
# 9. Documented Request-Budget Limits & Compensating Controls
# ==============================================================================


def test_conformance_documented_request_budget_limits_and_compensating_controls():
    """AC 6: Document request-budget limits endpoint cannot enforce and compensating controls."""
    limits = ChatGPTPlanBudgetLimits()

    # The subscription sharing endpoint cannot enforce server-side budget limits
    assert limits.server_side_token_budget_enforced is False
    assert limits.server_side_cost_caps_enforced is False
    assert limits.server_side_turn_limit_enforced is False
    assert "subscription_sharing_usage_limit_exceeded" in limits.rate_limits

    # TFS compensating run/time controls:
    assert "asyncio.timeout" in limits.compensating_turn_deadline
    assert "model-attempt allowance" in limits.compensating_attempt_budget
    assert "iteration ceiling" in limits.compensating_iteration_ceiling
    assert "cancellation" in limits.compensating_cancellation.lower()


def test_conformance_sanitize_secrets_helper():
    """AC 5: Unit test secret text sanitizer."""
    raw = "Failed with Bearer eyJhbGciOi.eyJzdWIiOi.sig and token sk-proj-12345 or dummy-access-secret"
    clean = sanitize_secret_text(raw)
    assert "Bearer" not in clean or "[REDACTED_SECRET]" in clean
    assert "sk-proj-12345" not in clean
    assert "dummy-access-secret" not in clean
    assert "eyJhbGciOi" not in clean
