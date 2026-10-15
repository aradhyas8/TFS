"""One mapping from exceptions to safe user-facing failures, shared by chat runs and /api/analyze.

Messages never carry token counts, upstream bodies or request details; callers log the exception itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
import openai

from .chatgpt_plan import USAGE_CODES, PlanError
from .pipeline import InvalidReview
from .providers import ModelIncomplete, ModelServiceError
from .sec_research import SecUnavailable

FailureKind = Literal["incomplete", "invalid_output", "rate_limit", "plan_limit", "timeout",
                      "unavailable", "model_service", "internal"]

STATUS: dict[FailureKind, int] = {
    # Rate and plan limits keep the 502 that /api/analyze has always returned for model-service failures.
    "incomplete": 502, "invalid_output": 502, "rate_limit": 502, "plan_limit": 502, "timeout": 504,
    "unavailable": 503, "model_service": 502, "internal": 500,
}


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    message: str
    retryable: bool

    @property
    def status(self) -> int:
        return STATUS[self.kind]


def describe_failure(exc: BaseException) -> Failure:
    """Classify an exception into a kind, a safe message and whether trying again can help."""
    if isinstance(exc, ModelIncomplete):
        # Its message names only the stop reason and step; token diagnostics are logged by the transport.
        if exc.reason in {None, "max_output_tokens"}:
            return Failure("incomplete", f"{exc} The model ran out of output space before it finished. Try again, "
                                         "or lower the reasoning effort in AI connection.", True)
        return Failure("incomplete", str(exc), False)
    if isinstance(exc, InvalidReview):
        return Failure("invalid_output", "The model did not produce a valid result. Nothing was completed; try again.", True)
    if isinstance(exc, PlanError):
        if exc.code in USAGE_CODES:
            return Failure("plan_limit", "Your ChatGPT plan usage limit is reached. Wait until it resets (ChatGPT "
                                         "Settings > Usage) or switch the AI connection.", False)
        if exc.code == "rate_limit_exceeded":
            return Failure("rate_limit", "The ChatGPT plan rate limit is reached. Nothing was retried; try again later.", True)
        return Failure("model_service", f"The ChatGPT plan request failed ({exc.code}). Check AI connection and try again.", True)
    if isinstance(exc, ModelServiceError):
        # Its message is safe to show by contract; the kind keeps rate limits and timeouts distinguishable.
        return Failure(exc.kind, str(exc), True)
    if isinstance(exc, openai.RateLimitError):
        return Failure("rate_limit", "The model service rate limit is reached. Nothing was retried; try again later.", True)
    if isinstance(exc, openai.APITimeoutError | TimeoutError):
        return Failure("timeout", "A request timed out. Nothing was completed; try again.", True)
    if isinstance(exc, openai.OpenAIError):
        return Failure("model_service", "The model service could not complete the request. Try again.", True)
    if isinstance(exc, ValueError | KeyError | OSError | httpx.HTTPError | SecUnavailable):
        return Failure("unavailable", "Requested data is unavailable.", False)
    return Failure("internal", "An internal error stopped this request. Nothing was completed.", False)
