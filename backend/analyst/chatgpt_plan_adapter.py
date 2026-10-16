"""PydanticAI ChatGPT Plan conformance adapter for TFS V2 evaluation.

Provides the isolated compatibility adapter layer evaluating PydanticAI against
TFS's ChatGPT-plan connection contracts, encrypted credential ownership,
token audience/scopes, single refresh ownership, streaming-only Responses route,
store=false, unsupported parameter omission, tool continuation, reasoning state,
turn deadlines, and safe error mapping.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal, TypeVar, cast

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import (
    ContentFilterError,
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UserError,
)
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.models.openai_codex import OpenAICodexModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.profiles.openai_codex import openai_codex_model_profile
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ToolOrOutput
from pydantic_ai.usage import RunUsage

from . import chatgpt_plan
from .calculations import review_portfolio as calculate_review_portfolio
from .chatgpt_plan import USAGE_CODES, PlanError
from .config import Settings
from .providers import ModelIncomplete, ModelServiceError
from .schemas import FinancialEvidence, PortfolioReview, Snapshot

# Compatibility bridge for openai<2.55 where prompt_cache_diagnostics is not on Response model
if not hasattr(Response, "prompt_cache_diagnostics"):
    setattr(Response, "prompt_cache_diagnostics", None)

T = TypeVar("T", bound=BaseModel)

_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+[\w\-\.]+"),
    re.compile(r"ey[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+\.[A-Za-z0-9_\-\.]+"),  # JWT pattern
    re.compile(r"sk-[A-Za-z0-9_\-]+"),
    re.compile(r"oauth-access[\w\-\.]*"),
    re.compile(r"dummy-[\w\-\.]+"),
]


def sanitize_secret_text(text: str) -> str:
    """Scrub sensitive bearer tokens, JWTs, and API credentials from error messages."""
    sanitized = text
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


@dataclass(frozen=True)
class ChatGPTPlanComparison:
    """Comparison between TFS's Sign in with ChatGPT route and PydanticAI's Codex integration."""

    tfs_sign_in_endpoint: str = "https://auth.openai.com/api/accounts/authorize"
    pydantic_ai_codex_authorize_endpoint: str = "https://auth.openai.com/oauth/authorize"
    tfs_token_endpoint: str = "https://auth.openai.com/api/accounts/oauth/token"
    pydantic_ai_codex_token_endpoint: str = "https://auth.openai.com/oauth/token"
    tfs_resource_base_url: str = "https://api.openai.com/v1"
    pydantic_ai_codex_base_url: str = "https://chatgpt.com/backend-api/codex"
    tfs_scopes: str = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    pydantic_ai_codex_scopes: str = "openid profile email offline_access"
    tfs_credential_storage: str = (
        "Encrypted OS store: Windows DPAPI or macOS login Keychain (TFS-owned, zero external dependencies)"
    )
    pydantic_ai_codex_credential_storage: str = (
        "Plaintext file at ~/.codex/auth.json (reads external Codex CLI tool's credentials)"
    )
    tfs_client_id: str = "Dynamic agent client registration per host (dynamic_agent_client -> issued client_id)"
    pydantic_ai_codex_client_id: str = "Hardcoded static client ID (app_EMoamEEZ73f0CkXaXp7hrann)"
    tfs_refresh_ownership: str = (
        "Single refresh owner via credential_lock() (session.lock file lock + thread lock), "
        "atomic rotation and encrypted persistence"
    )
    pydantic_ai_codex_refresh_ownership: str = (
        "Separate refresh flow unaware of TFS DPAPI/Keychain, potential race condition"
    )
    adapter_required: bool = True
    adapter_rationale: str = (
        "A custom adapter is required because PydanticAI's built-in OpenAICodexProvider reads an "
        "unencrypted foreign CLI file (~/.codex/auth.json), uses unauthorized scopes (missing "
        "chatgpt.tokens.use.direct and resource.invoke), targets the wrong base URL "
        "(chatgpt.com/backend-api/codex instead of api.openai.com/v1), and cannot safely coordinate "
        "token refresh with TFS's DPAPI/Keychain secure store."
    )


@dataclass(frozen=True)
class ChatGPTPlanBudgetLimits:
    """Documented limits the ChatGPT plan endpoint cannot enforce, and TFS's compensating controls."""

    server_side_token_budget_enforced: bool = False
    server_side_cost_caps_enforced: bool = False
    server_side_turn_limit_enforced: bool = False
    rate_limits: str = "Account-wide subscription sharing usage limits (subscription_sharing_usage_limit_exceeded)"
    compensating_turn_deadline: str = (
        "Strict turn deadline (asyncio.timeout) bounding entire stream read; "
        "terminates stalled/infinite streams without retry"
    )
    compensating_attempt_budget: str = (
        "Task-scoped model-attempt allowance charged before every outer/nested/retry dispatch"
    )
    compensating_iteration_ceiling: str = "Emergency iteration ceiling enforced by run supervisor"
    compensating_cancellation: str = (
        "Explicit client-side cancellation via threading.Event / asyncio.CancelledError cleanly closing stream"
    )


@dataclass
class ChatGPTPlanConformanceResult:
    """Outcome of a PydanticAI ChatGPT Plan evaluation run."""

    success: bool
    output: Any
    usage: RunUsage | None = None
    raw_turns: int = 0
    error: Exception | None = None
    compatibility_status: str = "mocked contract verified; real-provider compatibility unverified"


def build_chatgpt_plan_model_settings(
    settings: Settings,
    *,
    reasoning_effort: str | None = None,
    forced_tool: str | None = None,
    timeout: float | None = None,
) -> OpenAIResponsesModelSettings:
    """Build OpenAIResponsesModelSettings adhering to TFS ChatGPT Plan invariants.

    Invariants:
    1. Unset reasoning effort sends no reasoning parameter (omitted).
    2. Explicit reasoning effort maps to openai_reasoning_effort.
    3. Forced tool choice is set explicitly when required.
    4. Unsupported parameters (temperature, top_p, max_tokens) are omitted/dropped
       automatically by the openai_codex_model_profile.
    """
    effort = reasoning_effort if reasoning_effort is not None else settings.reasoning_effort
    model_settings: OpenAIResponsesModelSettings = {}

    if effort is not None:
        model_settings["openai_reasoning_effort"] = cast(
            Literal["high", "medium", "low", "minimal", "none"], effort
        )

    if forced_tool is not None:
        model_settings["tool_choice"] = ToolOrOutput([forced_tool])

    if timeout is not None:
        model_settings["timeout"] = timeout

    return model_settings


def map_pydanticai_chatgpt_plan_error(exc: Exception, model: str) -> Exception:
    """Map PydanticAI ChatGPT Plan exceptions to TFS domain exceptions.

    Guarantees:
    - No upstream bearer tokens, JWTs, or raw continuation bodies leak to the user or logs.
    - PlanError codes (subscription_sharing_usage_limit_exceeded, not_signed_in, no_terminal_event, etc.)
      are preserved.
    - Stream timeouts, cancellations, refusals, and malformed streams are accurately classified.
    """
    if isinstance(exc, PlanError):
        # Scrub error message just in case
        return PlanError(exc.code, sanitize_secret_text(str(exc)))

    if isinstance(exc, APITimeoutError | TimeoutError | asyncio.TimeoutError):
        logging.error("CHATGPT PLAN TIMEOUT on model %s", model)
        return APITimeoutError(request=httpx.Request("POST", f"{chatgpt_plan.RESOURCE}/responses"))

    if isinstance(exc, APIConnectionError):
        logging.error("CHATGPT PLAN CONNECTION ERROR on model %s: %s", model, sanitize_secret_text(str(exc)))
        return PlanError("request_failed", "HTTP connection failure; check AI connection and retry.")

    if isinstance(exc, ContentFilterError):
        refusal_msg = getattr(exc, "message", "") or str(exc)
        if "Refusal:" in refusal_msg:
            refusal_text = refusal_msg.split("Refusal:", 1)[1].split(", body:", 1)[0].strip(" '\"")
        else:
            refusal_text = refusal_msg
        return ValueError(f"Model refused: {sanitize_secret_text(refusal_text)}")

    if isinstance(exc, IncompleteToolCall):
        return ModelIncomplete(
            "The model response is incomplete (reason: incomplete_tool_call).",
            reason="incomplete_tool_call",
        )

    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        upstream = sanitize_secret_text(str(exc.body or "")).lower()

        if status == 401:
            logging.error("CHATGPT PLAN 401 UNAUTHORIZED on model %s", model)
            return PlanError("not_signed_in", "ChatGPT plan access token expired or revoked.")

        if status == 429:
            logging.error("CHATGPT PLAN 429 RATE LIMIT on model %s", model)
            for code in USAGE_CODES:
                if code in upstream:
                    return PlanError(code)
            return PlanError("rate_limit_exceeded")

        for code in chatgpt_plan.SAFE_RESPONSE_CODES:
            if code in upstream:
                return PlanError(code)

        logging.error("CHATGPT PLAN HTTP ERROR status=%s model=%s", status, model)
        return PlanError("request_failed", f"token or model endpoint HTTP {status}")

    if isinstance(exc, UnexpectedModelBehavior):
        msg = str(exc).lower()
        cause = str(getattr(exc, "__cause__", "")).lower()
        if (
            "without content or tool calls" in msg
            or "streamed response ended" in msg
            or "please return text or call a tool" in cause
            or "no terminal event" in msg
        ):
            return PlanError("no_terminal_event", "The stream ended without response.completed.")
        if "validation error" in msg or "json" in msg or "output retries" in msg:
            return ValueError(f"ChatGPT plan {model}: invalid structured output.")
        return ModelServiceError(f"ChatGPT plan {model}: unexpected model behavior: {sanitize_secret_text(str(exc))}")

    if isinstance(exc, UserError):
        return ModelServiceError(f"ChatGPT plan {model}: configuration error: {sanitize_secret_text(str(exc))}")

    return exc


@dataclass
class PortfolioToolDeps:
    """Runtime dependencies bound to the portfolio evaluation agent."""

    snapshot: Snapshot
    evidence: FinancialEvidence


class ChatGPTPlanEvaluationAdapter:
    """Isolated ChatGPT Plan evaluation adapter using PydanticAI.

    Provides a clean seam for running tool round trips against mocked ChatGPT Plan
    subscription Responses HTTP while preserving TFS credential ownership, DPAPI/Keychain
    encryption, single refresh ownership, streaming-only Responses route, store=false,
    unsupported parameter omission, and turn deadline handling.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        token: str | None = None,
        client: AsyncOpenAI | None = None,
        profile: ModelProfile | None = None,
    ) -> None:
        self.settings = settings
        self.token = token or self._resolve_access_token()
        self.client = client or AsyncOpenAI(
            api_key=self.token,
            base_url=chatgpt_plan.RESOURCE,
            timeout=settings.request_timeout,
            max_retries=0,  # Plan route never retries automatically on stream failures
        )
        self.provider = OpenAIProvider(openai_client=self.client)
        effective_profile = openai_codex_model_profile(settings.model)
        if profile:
            effective_profile.update(profile)
        self.model = OpenAICodexModel(settings.model, provider=self.provider, profile=effective_profile)

    def _resolve_access_token(self) -> str:
        """Obtain the current valid access token through TFS's single refresh owner."""
        try:
            return chatgpt_plan.access_token()
        except PlanError:
            if self.settings.api_key:
                return self.settings.api_key
            raise

    def create_portfolio_agent(
        self,
        output_type: type[T] | None = None,
    ) -> Agent[PortfolioToolDeps, Any]:
        """Create a PydanticAI Agent binding TFS's deterministic review_portfolio tool."""
        agent: Agent[PortfolioToolDeps, Any] = Agent(
            model=self.model,
            output_type=output_type if output_type is not None else str,
        )

        @agent.tool
        def review_portfolio(ctx: RunContext[PortfolioToolDeps]) -> dict[str, Any]:
            """Calculate the complete supplied dated portfolio across every account.

            Takes no arguments: the backend binds the submitted snapshot.
            """
            review: PortfolioReview = calculate_review_portfolio(
                ctx.deps.snapshot,
                ctx.deps.evidence,
                holdings_as_of=ctx.deps.snapshot.as_of,
            )
            return review.model_dump(mode="json")

        return agent

    async def run_portfolio_round_trip(
        self,
        user_prompt: str,
        snapshot: Snapshot,
        evidence: FinancialEvidence,
        *,
        output_type: type[T] | None = None,
        reasoning_effort: str | None = None,
        forced_tool: str | None = None,
        timeout: float | None = None,
    ) -> ChatGPTPlanConformanceResult:
        """Execute a tool round trip to the deterministic TFS handler through PydanticAI."""
        agent = self.create_portfolio_agent(output_type=output_type)
        turn_timeout = timeout if timeout is not None else self.settings.request_timeout
        model_settings = build_chatgpt_plan_model_settings(
            self.settings,
            reasoning_effort=reasoning_effort,
            forced_tool=forced_tool,
            timeout=turn_timeout,
        )
        deps = PortfolioToolDeps(snapshot=snapshot, evidence=evidence)

        try:
            async with asyncio.timeout(turn_timeout):
                run_result = await agent.run(
                    user_prompt,
                    deps=deps,
                    model_settings=model_settings,
                )

            return ChatGPTPlanConformanceResult(
                success=True,
                output=run_result.output,
                usage=run_result.usage,
                raw_turns=len(run_result.all_messages()),
            )
        except Exception as exc:
            mapped_exc = map_pydanticai_chatgpt_plan_error(exc, self.settings.model)
            return ChatGPTPlanConformanceResult(
                success=False,
                output=None,
                error=mapped_exc,
            )
