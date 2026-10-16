"""Model-aware resource policy and shared budget ledger for TFS Agent Harness V2 (Ticket 06).

Provides a task-scoped model-attempt, token, cost, and wall-clock deadline ledger shared across
outer conversation turns, nested workflow inference, validation retries, and compaction.

Enforces:
1. Finite model-attempt allowance (concrete proof allowance).
2. Absolute wall-clock deadline that caps per-request/tool timeouts.
3. Advance charging: each attempt is charged BEFORE dispatch; failed attempts still consume a slot.
4. Cumulative consumption: nested work and retries consume the same shared allowance.
5. Incomplete terminal outcome upon exhaustion (attempts, deadline, tokens, or cost).
6. Capability-aware context/generation headroom and supported reasoning choices.
7. Model pricing metadata: computes cost when known; preserves unknown cost as None without fabricating $0.00.
8. Compaction accounting: tracks compaction count and char savings.
9. Separate budget for follow-up user requests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

PhaseType = Literal[
    "outer_conversation",
    "nested_workflow",
    "validation_retry",
    "compaction",
]

ExhaustionReason = Literal[
    "attempts_exceeded",
    "deadline_exceeded",
    "tokens_exceeded",
    "cost_exceeded",
]


class BudgetExhaustedError(RuntimeError):
    """Raised when the task-scoped model-attempt allowance, tokens, cost, or wall-clock deadline is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        reason: ExhaustionReason,
    ) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ModelCapabilityProfile:
    """Capability facts and pricing for a supported model."""

    context_window: int
    generation_headroom: int
    pricing_known: bool
    input_price_per_million: float | None
    output_price_per_million: float | None
    supported_reasoning: tuple[str, ...] = ()


MODEL_PROFILES: dict[str, ModelCapabilityProfile] = {
    "gpt-4o": ModelCapabilityProfile(
        context_window=128_000,
        generation_headroom=16_384,
        pricing_known=True,
        input_price_per_million=2.50,
        output_price_per_million=10.00,
        supported_reasoning=(),
    ),
    "gpt-4o-mini": ModelCapabilityProfile(
        context_window=128_000,
        generation_headroom=16_384,
        pricing_known=True,
        input_price_per_million=0.15,
        output_price_per_million=0.60,
        supported_reasoning=(),
    ),
    "o1": ModelCapabilityProfile(
        context_window=200_000,
        generation_headroom=100_000,
        pricing_known=True,
        input_price_per_million=15.00,
        output_price_per_million=60.00,
        supported_reasoning=("low", "medium", "high"),
    ),
    "o3-mini": ModelCapabilityProfile(
        context_window=200_000,
        generation_headroom=100_000,
        pricing_known=True,
        input_price_per_million=1.10,
        output_price_per_million=4.40,
        supported_reasoning=("low", "medium", "high"),
    ),
    "chatgpt_plan": ModelCapabilityProfile(
        context_window=128_000,
        generation_headroom=16_384,
        pricing_known=False,
        input_price_per_million=None,
        output_price_per_million=None,
        supported_reasoning=("low", "medium", "high"),
    ),
}

DEFAULT_PROFILE = ModelCapabilityProfile(
    context_window=128_000,
    generation_headroom=4_096,
    pricing_known=False,
    input_price_per_million=None,
    output_price_per_million=None,
    supported_reasoning=("low", "medium", "high"),
)


@dataclass
class UsageRecord:
    """Reported model token usage and estimated cost."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None


@dataclass
class AttemptRecord:
    """Record of an individual model attempt charged against the task budget."""

    attempt_id: int
    phase: PhaseType
    dispatched_at: float
    completed_at: float | None = None
    success: bool | None = None
    error: str | None = None
    usage: UsageRecord | None = None


@dataclass
class TaskBudgetLedger:
    """Task-scoped ledger accounting for model attempts, tokens, cost, and wall-clock execution time."""

    max_attempts: int = 15
    deadline_seconds: float = 600.0
    start_time: float = field(default_factory=time.time)
    attempts: int = 0
    attempt_log: list[AttemptRecord] = field(default_factory=list)

    # Capability-aware resource boundaries
    max_total_tokens: int | None = None
    max_cost: float | None = None
    context_window: int = 128_000
    generation_headroom: int = 4_096

    # Pricing & model metadata
    pricing_known: bool = True
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    model_id: str | None = None
    provider: str | None = None
    supported_reasoning: list[str] = field(default_factory=list)
    effective_settings: dict[str, Any] = field(default_factory=dict)
    omitted_settings: dict[str, str] = field(default_factory=dict)

    # Compaction accounting
    compaction_count: int = 0
    compaction_log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def deadline(self) -> float:
        return self.start_time + self.deadline_seconds

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.time())

    def remaining_timeout(self, request_timeout: float) -> float:
        """Cap per-request/tool timeout by remaining wall-clock deadline."""
        return min(request_timeout, self.remaining_seconds())

    def estimate_cost(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> float | None:
        """Estimate dollar cost from reported token counts. Unknown pricing returns None, never 0.0."""
        if (
            not self.pricing_known
            or self.input_price_per_million is None
            or self.output_price_per_million is None
            or prompt_tokens is None
            or completion_tokens is None
        ):
            return None
        cost = (
            prompt_tokens * self.input_price_per_million
            + completion_tokens * self.output_price_per_million
        ) / 1_000_000.0
        return round(cost, 6)

    def is_exhausted(self) -> bool:
        if self.attempts >= self.max_attempts or time.time() >= self.deadline:
            return True
        usage = self.total_usage()
        if (
            self.max_total_tokens is not None
            and usage.total_tokens is not None
            and usage.total_tokens >= self.max_total_tokens
        ):
            return True
        if (
            self.max_cost is not None
            and usage.cost is not None
            and usage.cost >= self.max_cost
        ):
            return True
        return False

    def charge_attempt(self, phase: PhaseType) -> AttemptRecord:
        """Charges a model attempt BEFORE dispatch.

        Failed attempts still consume the slot. If already exhausted,
        raises BudgetExhaustedError.
        """
        now = time.time()
        if now >= self.deadline:
            raise BudgetExhaustedError(
                f"Task wall-clock deadline exceeded ({self.deadline_seconds:.1f}s limit).",
                reason="deadline_exceeded",
            )
        if self.attempts >= self.max_attempts:
            raise BudgetExhaustedError(
                f"Model attempt allowance exhausted ({self.attempts}/{self.max_attempts} attempts used).",
                reason="attempts_exceeded",
            )
        usage = self.total_usage()
        if (
            self.max_total_tokens is not None
            and usage.total_tokens is not None
            and usage.total_tokens >= self.max_total_tokens
        ):
            raise BudgetExhaustedError(
                f"Task token allowance exceeded ({usage.total_tokens}/{self.max_total_tokens} tokens used).",
                reason="tokens_exceeded",
            )
        if (
            self.max_cost is not None
            and usage.cost is not None
            and usage.cost >= self.max_cost
        ):
            raise BudgetExhaustedError(
                f"Task cost allowance exceeded (${usage.cost:.4f}/${self.max_cost:.4f} used).",
                reason="cost_exceeded",
            )

        self.attempts += 1
        record = AttemptRecord(
            attempt_id=self.attempts,
            phase=phase,
            dispatched_at=now,
        )
        self.attempt_log.append(record)
        return record

    def record_attempt_result(
        self,
        record: AttemptRecord,
        *,
        success: bool,
        error: str | None = None,
        usage: dict[str, Any] | UsageRecord | None = None,
    ) -> None:
        """Records the outcome of a dispatched attempt and verifies cumulative allowances."""
        record.completed_at = time.time()
        record.success = success
        record.error = error
        if isinstance(usage, UsageRecord):
            record.usage = usage
        elif isinstance(usage, dict):
            prompt_tok = usage.get("prompt_tokens")
            comp_tok = usage.get("completion_tokens")
            total_tok = usage.get("total_tokens")
            if total_tok is None and prompt_tok is not None and comp_tok is not None:
                total_tok = prompt_tok + comp_tok
            cost_val = usage.get("cost")
            if cost_val is None:
                cost_val = self.estimate_cost(prompt_tok, comp_tok)
            record.usage = UsageRecord(
                prompt_tokens=prompt_tok,
                completion_tokens=comp_tok,
                total_tokens=total_tok,
                cost=cost_val,
            )
        else:
            record.usage = UsageRecord()

        # Check cumulative allowances after recording this attempt's usage
        total = self.total_usage()
        if (
            self.max_total_tokens is not None
            and total.total_tokens is not None
            and total.total_tokens >= self.max_total_tokens
        ):
            raise BudgetExhaustedError(
                f"Task token allowance exceeded ({total.total_tokens}/{self.max_total_tokens} tokens used).",
                reason="tokens_exceeded",
            )
        if (
            self.max_cost is not None
            and total.cost is not None
            and total.cost >= self.max_cost
        ):
            raise BudgetExhaustedError(
                f"Task cost allowance exceeded (${total.cost:.4f}/${self.max_cost:.4f} used).",
                reason="cost_exceeded",
            )

    def record_compaction(
        self,
        chars_before: int,
        chars_after: int,
        phase: PhaseType = "compaction",
    ) -> None:
        """Records a compaction event against the ledger."""
        self.compaction_count += 1
        self.compaction_log.append(
            {
                "compaction_id": self.compaction_count,
                "phase": phase,
                "chars_before": chars_before,
                "chars_after": chars_after,
                "saved_chars": max(0, chars_before - chars_after),
                "timestamp": time.time(),
            }
        )

    def total_usage(self) -> UsageRecord:
        """Sum reported usage. Unreported fields remain None."""
        prompt_sum: int | None = None
        comp_sum: int | None = None
        total_sum: int | None = None
        cost_sum: float | None = None

        for record in self.attempt_log:
            if record.usage:
                if record.usage.prompt_tokens is not None:
                    prompt_sum = (prompt_sum or 0) + record.usage.prompt_tokens
                if record.usage.completion_tokens is not None:
                    comp_sum = (comp_sum or 0) + record.usage.completion_tokens
                if record.usage.total_tokens is not None:
                    total_sum = (total_sum or 0) + record.usage.total_tokens
                if record.usage.cost is not None:
                    cost_sum = (cost_sum or 0.0) + record.usage.cost

        return UsageRecord(
            prompt_tokens=prompt_sum,
            completion_tokens=comp_sum,
            total_tokens=total_sum,
            cost=cost_sum,
        )


def create_task_budget(
    settings: Any | None = None,
    *,
    max_attempts: int = 15,
    deadline_seconds: float = 600.0,
    max_total_tokens: int | None = None,
    max_cost: float | None = None,
) -> TaskBudgetLedger:
    """Builds a model-aware TaskBudgetLedger from settings and known capability profiles."""
    model_name = getattr(settings, "model", None) or "gpt-4o"
    provider = getattr(settings, "provider", None) or "openai"
    reasoning_effort = getattr(settings, "reasoning_effort", None)

    profile = MODEL_PROFILES.get(model_name)
    if profile is None and provider == "chatgpt_plan":
        profile = MODEL_PROFILES["chatgpt_plan"]
    if profile is None:
        profile = DEFAULT_PROFILE

    effective_settings: dict[str, Any] = {
        "model": model_name,
        "provider": provider,
    }
    omitted_settings: dict[str, str] = {}

    if reasoning_effort is not None:
        if profile.supported_reasoning and reasoning_effort in profile.supported_reasoning:
            effective_settings["reasoning_effort"] = reasoning_effort
        elif not profile.supported_reasoning:
            omitted_settings["reasoning_effort"] = (
                f"Model '{model_name}' does not support configurable reasoning effort."
            )
        else:
            omitted_settings["reasoning_effort"] = (
                f"Reasoning effort '{reasoning_effort}' is not in supported options: {profile.supported_reasoning}."
            )

    return TaskBudgetLedger(
        max_attempts=max_attempts,
        deadline_seconds=deadline_seconds,
        max_total_tokens=max_total_tokens,
        max_cost=max_cost,
        context_window=profile.context_window,
        generation_headroom=profile.generation_headroom,
        pricing_known=profile.pricing_known,
        input_price_per_million=profile.input_price_per_million,
        output_price_per_million=profile.output_price_per_million,
        model_id=model_name,
        provider=provider,
        supported_reasoning=list(profile.supported_reasoning),
        effective_settings=effective_settings,
        omitted_settings=omitted_settings,
    )
