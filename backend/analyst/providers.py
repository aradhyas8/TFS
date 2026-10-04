import copy
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from openai import AsyncOpenAI
from openai.types.responses import FunctionToolParam, ResponseInputParam, ToolChoiceFunctionParam

from .config import Settings
from .schemas import Recommendation, Snapshot


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class ModelTurn:
    calls: list[ToolCall] = field(default_factory=list)
    answer: dict[str, Any] | None = None
    continuation: list[dict[str, Any]] = field(default_factory=list)


class ModelProvider(Protocol):
    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool) -> ModelTurn: ...


class DataProvider(Protocol):
    def snapshot(self, supplied: Snapshot) -> Snapshot: ...


class SuppliedDataProvider:
    """This milestone has no market data I/O: only the explicitly supplied snapshot."""

    def snapshot(self, supplied: Snapshot) -> Snapshot:
        return supplied.model_copy(deep=True)


class FakeDataProvider:
    def __init__(self, fixture: Snapshot | None = None) -> None:
        self.fixture = fixture
        self.snapshots: list[Snapshot] = []

    def snapshot(self, supplied: Snapshot) -> Snapshot:
        self.snapshots.append(supplied.model_copy(deep=True))
        return (self.fixture or supplied).model_copy(deep=True)


class ScriptedModel:
    """Replace only the external model boundary; never replace portfolio tools."""

    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict[str, Any]]] = []

    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool) -> ModelTurn:
        self.requests.append(copy.deepcopy(messages))
        if not self.turns:
            raise ValueError("Scripted model has no remaining turn.")
        return self.turns.pop(0)


PORTFOLIO_TOOL: FunctionToolParam = {
    "type": "function",
    "name": "review_portfolio",
    "description": "Calculate the complete supplied dated portfolio across every account. Takes no arguments: the backend binds the submitted snapshot.",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    "strict": True,
}


class OpenAIModel:
    def __init__(self, settings: Settings) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("Backend OpenAI configuration is missing.")
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url="https://api.openai.com/v1",
            timeout=45.0,
            max_retries=0,
        )

    async def respond(self, messages: list[dict[str, Any]], *, require_tool: bool) -> ModelTurn:
        choice: ToolChoiceFunctionParam = {"type": "function", "name": "review_portfolio"}
        response = await self.client.responses.create(
            model=self.settings.model,
            input=cast(ResponseInputParam, messages),
            tools=[PORTFOLIO_TOOL],
            tool_choice=choice if require_tool else "none",
            parallel_tool_calls=False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "portfolio_recommendation",
                    "schema": Recommendation.model_json_schema(),
                    "strict": True,
                }
            },
            max_output_tokens=4000,
            store=False,
            include=["reasoning.encrypted_content"],
        )
        if response.status != "completed":
            raise ValueError("The model response is incomplete.")
        continuation = [item.model_dump(mode="json") for item in response.output]
        calls = [
            ToolCall(item.call_id, item.name, item.arguments)
            for item in response.output
            if item.type == "function_call"
        ]
        if calls:
            return ModelTurn(calls=calls, continuation=continuation)
        if not response.output_text:
            raise ValueError("The model did not return a recommendation.")
        answer = json.loads(response.output_text)
        if not isinstance(answer, dict):
            raise ValueError("Expected a recommendation object.")
        return ModelTurn(answer=answer, continuation=continuation)
