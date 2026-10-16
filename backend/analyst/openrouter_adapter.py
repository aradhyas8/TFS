"""PydanticAI OpenRouter conformance adapter for TFS V2 evaluation.

Provides the compatibility adapter layer evaluating PydanticAI against TFS's
OpenRouter transport contracts, settings preservation, error mapping,
tool round trips, and protocol differences.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, TypeVar, cast

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import (
    ContentFilterError,
    IncompleteToolCall,
    ModelHTTPError,
    UnexpectedModelBehavior,
    UserError,
)
from pydantic_ai.models.openrouter import (
    OpenRouterModel,
    OpenRouterModelSettings,
    OpenRouterProviderConfig,
    OpenRouterReasoning,
)
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.settings import ToolOrOutput
from pydantic_ai.usage import RunUsage

from .calculations import review_portfolio as calculate_review_portfolio
from .config import Settings
from .providers import ModelIncomplete, ModelServiceError, openrouter_routing
from .schemas import FinancialEvidence, PortfolioReview, Snapshot

T = TypeVar("T", bound=BaseModel)


@dataclass
class ProtocolComparison:
    """Comparison between TFS's existing Responses route and PydanticAI's Chat Completions route."""

    tfs_route: str = "https://openrouter.ai/api/v1/responses"
    pydantic_ai_route: str = "https://openrouter.ai/api/v1/chat/completions"
    tfs_wire_envelope: str = "Response format with input messages, output function_call items"
    pydantic_ai_wire_envelope: str = "Chat Completion with messages array, choices[0].message.tool_calls"
    tfs_tool_continuation: str = "Input contains function_call followed by function_call_output"
    pydantic_ai_tool_continuation: str = "Messages contains assistant tool_calls followed by role='tool'"
    tfs_reasoning_parameter: str = "Top-level reasoning: {effort: str}"
    pydantic_ai_reasoning_parameter: str = "extra_body: {reasoning: {effort: str}}"
    tfs_routing_parameter: str = "extra_body: {provider: ...}"
    pydantic_ai_routing_parameter: str = "extra_body: {provider: ...}"
    tfs_structured_output: str = "text.format.type = 'json_schema'"
    pydantic_ai_structured_output: str = "response_format.type = 'json_schema'"
    tfs_citations: str = "output_text annotations with flat {type: 'url_citation', url: str, title: str}"
    pydantic_ai_citations: str = "message annotations with nested {type: 'url_citation', url_citation: {url: str, title: str}}"


@dataclass
class ConformanceResult:
    """Outcome of a PydanticAI OpenRouter evaluation run."""

    success: bool
    output: Any
    usage: RunUsage | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    raw_turns: int = 0
    error: Exception | None = None
    compatibility_status: str = "mocked contract verified; real-provider compatibility unverified"


def build_openrouter_provider_config(model: str) -> OpenRouterProviderConfig:
    """Convert TFS routing rules into PydanticAI OpenRouterProviderConfig.

    Preserves:
    - allow_fallbacks=False (never fall back to another host or model)
    - require_parameters=True (honour every sent parameter)
    - only=['openai'] for OpenAI models
    - max_price={prompt: 0, completion: 0} for :free models
    """
    raw_routing = openrouter_routing(model)
    config: OpenRouterProviderConfig = {
        "allow_fallbacks": raw_routing.get("allow_fallbacks", False),
        "require_parameters": raw_routing.get("require_parameters", True),
    }
    if "only" in raw_routing:
        config["only"] = raw_routing["only"]
    if "max_price" in raw_routing:
        raw_price = raw_routing["max_price"]
        config["max_price"] = {
            "prompt": raw_price["prompt"],
            "completion": raw_price["completion"],
        }
    return config


def build_openrouter_model_settings(
    settings: Settings,
    *,
    reasoning_effort: str | None = None,
    forced_tool: str | None = None,
) -> OpenRouterModelSettings:
    """Build OpenRouterModelSettings adhering to TFS invariants.

    Invariants:
    1. Unset reasoning effort sends no reasoning parameter at all (unset-parameter omission).
    2. Model routing restrictions (zero fallbacks, require_parameters, free price caps) are enforced.
    3. Forced tool choice is set explicitly when required.
    """
    effort = reasoning_effort if reasoning_effort is not None else settings.reasoning_effort
    model_settings: OpenRouterModelSettings = {
        "openrouter_provider": build_openrouter_provider_config(settings.model),
    }

    # OpenRouter requires every sent parameter, so an unset effort sends no reasoning parameter.
    if effort is not None:
        model_settings["openrouter_reasoning"] = OpenRouterReasoning(
            effort=cast(Literal["xhigh", "high", "medium", "low", "minimal", "none"], effort)
        )

    if forced_tool is not None:
        model_settings["tool_choice"] = ToolOrOutput([forced_tool])

    return model_settings


def map_pydanticai_openrouter_error(exc: Exception, model: str) -> Exception:
    """Map PydanticAI OpenRouter exceptions to TFS domain exceptions.

    Guarantees:
    - No upstream secrets or raw bodies leak to the user.
    - Specific failures (rate limit, unavailable, refusal, incomplete, timeout) are preserved.
    - Single attempt without automatic retries for rate-limited requests.
    """
    if isinstance(exc, APITimeoutError | TimeoutError):
        return ModelServiceError(
            f"OpenRouter {model}: the model did not respond in time. Nothing was retried; try again later.",
            kind="timeout",
        )

    if isinstance(exc, APIConnectionError):
        return ModelServiceError(
            f"OpenRouter {model}: could not be reached. Nothing was retried; check the connection and try again",
            kind="model_service",
        )

    if isinstance(exc, ContentFilterError):
        # Model refusal
        refusal_msg = getattr(exc, "message", "") or str(exc)
        if "Refusal:" in refusal_msg:
            refusal_text = refusal_msg.split("Refusal:", 1)[1].split(", body:", 1)[0].strip(" '\"")
        else:
            refusal_text = refusal_msg
        return ValueError(f"Model refused: {refusal_text}")

    if isinstance(exc, IncompleteToolCall):
        return ModelIncomplete(
            "The model response is incomplete (reason: incomplete_tool_call).",
            reason="incomplete_tool_call",
        )

    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        upstream = str(exc.body or "").lower()
        kind: Literal["rate_limit", "timeout", "model_service"] = "model_service"

        if status == 429:
            kind = "rate_limit"
            reason = "rate limit reached (free-tier models have per-minute and daily limits). Nothing was retried; try again later"
        elif status == 404 and "that support" in upstream and "reasoning" in upstream:
            reason = (
                "does not support the configured reasoning effort at the allowed price. "
                "Unset OPENAI_REASONING_EFFORT or choose a model that supports reasoning; no other model was used"
            )
        elif status == 404 and ("that support" in upstream or "can handle the requested parameters" in upstream):
            reason = (
                "does not support every parameter TFS requires (tools, named tool choice, strict structured outputs "
                "and any configured reasoning effort) at the allowed price. Retrying will not help; choose a model whose "
                "provider supports all of them. No other model was used"
            )
        elif status == 402:
            reason = "rejected the request for insufficient credits. No other model was used"
        elif status == 400:
            reason = (
                "rejected the request (HTTP 400): the model does not accept a parameter or schema TFS requires. "
                "Retrying will not help; choose another model"
            )
        else:
            reason = f"is unavailable (HTTP {status}). No other model was used; try again later"

        logging.error("OPENROUTER PYDANTIC_AI REQUEST FAILED status=%s model=%s", status, model)
        return ModelServiceError(f"OpenRouter {model}: {reason}.", kind=kind)

    if isinstance(exc, UnexpectedModelBehavior):
        msg = str(exc).lower()
        if "validation error" in msg or "json" in msg or "output retries" in msg:
            return ValueError(f"OpenRouter {model}: invalid structured output.")
        return ModelServiceError(f"OpenRouter {model}: unexpected model behavior: {exc}")

    if isinstance(exc, UserError):
        return ModelServiceError(f"OpenRouter {model}: configuration conflict: {exc}")

    return exc


@dataclass
class PortfolioToolDeps:
    """Runtime dependencies bound to the portfolio evaluation agent."""

    snapshot: Snapshot
    evidence: FinancialEvidence


class OpenRouterEvaluationAdapter:
    """Isolated OpenRouter evaluation adapter using PydanticAI.

    Provides a clean seam for running tool round trips against mocked OpenRouter HTTP
    while preserving TFS settings, server-side secrets, and error mappings.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
        profile: ModelProfile | None = None,
    ) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("Backend OpenAI/OpenRouter configuration is missing.")
        self.settings = settings
        self.client = client or AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url or "https://openrouter.ai/api/v1",
            timeout=settings.request_timeout,
            max_retries=0,  # OpenRouter rate limits or parameter failures must not be retried automatically
        )
        self.provider = OpenRouterProvider(openai_client=self.client)
        effective_profile: ModelProfile = {
            "default_structured_output_mode": "native",
            "supports_json_schema_output": True,
            **(profile or {}),
        }
        self.model = OpenRouterModel(settings.model, provider=self.provider, profile=effective_profile)

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
    ) -> ConformanceResult:
        """Execute a tool round trip to the deterministic TFS handler through PydanticAI."""
        agent = self.create_portfolio_agent(output_type=output_type)
        model_settings = build_openrouter_model_settings(
            self.settings,
            reasoning_effort=reasoning_effort,
            forced_tool=forced_tool,
        )
        deps = PortfolioToolDeps(snapshot=snapshot, evidence=evidence)

        try:
            run_result = await agent.run(
                user_prompt,
                deps=deps,
                model_settings=model_settings,
            )
            # Extract citations if annotations were captured in provider details
            citations: list[dict[str, Any]] = []
            for msg in run_result.all_messages():
                provider_details = getattr(msg, "provider_details", None)
                if isinstance(provider_details, dict) and "annotations" in provider_details:
                    ann = provider_details["annotations"]
                    if isinstance(ann, list):
                        citations.extend(ann)

            return ConformanceResult(
                success=True,
                output=run_result.output,
                usage=run_result.usage,
                citations=citations,
                raw_turns=len(run_result.all_messages()),
            )
        except Exception as exc:
            mapped_exc = map_pydanticai_openrouter_error(exc, self.settings.model)
            return ConformanceResult(
                success=False,
                output=None,
                error=mapped_exc,
            )
