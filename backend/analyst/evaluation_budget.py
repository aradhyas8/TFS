"""Minimum shared proof budget ledger for TFS Agent Harness V2 evaluation.

Provides a task-scoped model-attempt and wall-clock deadline ledger shared across
outer conversation turns, nested workflow inference, validation retries, and compaction.

Enforces:
1. Finite model-attempt allowance (concrete proof allowance).
2. Absolute wall-clock deadline that caps per-request/tool timeouts.
3. Advance charging: each attempt is charged BEFORE dispatch; failed attempts still consume a slot.
4. Cumulative consumption: nested work and retries consume the same shared allowance.
5. Incomplete terminal outcome upon exhaustion.
6. Usage recording: reported usage is recorded; unavailable usage is marked unknown (None), never zero.
7. Separate budget for follow-up user requests.
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


class BudgetExhaustedError(RuntimeError):
    """Raised when the task-scoped model-attempt allowance or wall-clock deadline is exhausted."""

    def __init__(
        self,
        message: str,
        *,
        reason: Literal["attempts_exceeded", "deadline_exceeded"],
    ) -> None:
        super().__init__(message)
        self.reason = reason


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
    """Task-scoped ledger accounting for model attempts and wall-clock execution time."""

    max_attempts: int = 10
    deadline_seconds: float = 600.0
    start_time: float = field(default_factory=time.time)
    attempts: int = 0
    attempt_log: list[AttemptRecord] = field(default_factory=list)

    @property
    def deadline(self) -> float:
        return self.start_time + self.deadline_seconds

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - time.time())

    def remaining_timeout(self, request_timeout: float) -> float:
        """Cap per-request/tool timeout by remaining wall-clock deadline."""
        return min(request_timeout, self.remaining_seconds())

    def is_exhausted(self) -> bool:
        return self.attempts >= self.max_attempts or time.time() >= self.deadline

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
        """Records the outcome of a dispatched attempt."""
        record.completed_at = time.time()
        record.success = success
        record.error = error
        if isinstance(usage, UsageRecord):
            record.usage = usage
        elif isinstance(usage, dict):
            record.usage = UsageRecord(
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                cost=usage.get("cost"),
            )
        else:
            record.usage = UsageRecord()

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
