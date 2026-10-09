import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from openai.types.shared.reasoning_effort import ReasoningEffort


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    reasoning_effort: ReasoningEffort = None
    request_timeout: float = 170.0
    # OpenAI by default; LLM_PROVIDER=openrouter points the same Responses client at OpenRouter for development.
    base_url: str = "https://api.openai.com/v1"

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        effort = os.environ.get("OPENAI_REASONING_EFFORT") or None
        if effort not in {None, "none", "minimal", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Invalid OpenAI reasoning effort.")
        try:
            timeout = float(os.environ.get("OPENAI_TIMEOUT", "170"))
        except ValueError:
            raise ValueError("Invalid OpenAI timeout.")
        if timeout <= 0 or timeout > 170:
            raise ValueError("Invalid OpenAI timeout.")
        provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower() or "openai"
        if provider not in {"openai", "openrouter"}:
            raise ValueError("Invalid LLM provider.")
        if provider == "openrouter":
            return cls(api_key=os.environ.get("OPENROUTER_API_KEY", ""), model=os.environ.get("OPENROUTER_MODEL", ""),
                       reasoning_effort=cast(ReasoningEffort, effort), request_timeout=timeout, base_url="https://openrouter.ai/api/v1")
        return cls(
            api_key=os.environ.get("OPENAI_API_KEY", ""), model=os.environ.get("OPENAI_MODEL", ""),
            reasoning_effort=cast(ReasoningEffort, effort),
            request_timeout=timeout,
        )
