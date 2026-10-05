import copy
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from openai import AsyncOpenAI
from openai.types.responses import FunctionToolParam, ResponseInputParam, ToolChoiceFunctionParam

from .config import Settings
from .schemas import (
    AllocationJudgment,
    CandidateCasesInput,
    CandidateResearchInput,
    CompanyJudgments,
    ComparisonJudgments,
    HoldingReviewInput,
    ProposedChanges,
    Recommendation,
    ReviewSizingInput,
    Snapshot,
    StockRecommendation,
    ThemeTestInput,
)


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

FINANCIAL_TOOLS: list[FunctionToolParam] = [
    {
        **PORTFOLIO_TOOL,
        "name": name,
        "description": description,
    }
    for name, description in [
        ("resolve_identities", "Resolve the submitted security/listing identities with backend source provenance. Takes no arguments."),
        ("get_quotes", "Get qualified dated indicative quotes or labeled broker-display fallback for the submitted positions. Takes no arguments."),
        ("get_fx", "Get dated indicative FX into the submitted reporting currency. Takes no arguments."),
        ("get_sponsor_holdings", "Get dated ETF sponsor holdings and look-through coverage for submitted positions. Takes no arguments."),
    ]
]

PROPOSAL_TOOL: FunctionToolParam = {
    "type": "function", "name": "check_proposed_changes",
    "description": "Check hypothetical share changes for submitted positions against backend-bound dated marks, FX and personal limits. new_cash must exactly match the explicitly supplied request preview (or be empty when absent); never invent cash. No execution or invented sizing. Cash funding must use the same account and currency as the security.",
    "parameters": ProposedChanges.model_json_schema(), "strict": True,
}

COMPARISON_TOOL: FunctionToolParam = {
    "type": "function", "name": "calculate_comparison",
    "description": "Calculate five-year conditional cases for exactly the request-selected alternatives. Starting capital, instrument identity, fund facts, known costs/tax and dated FX are backend bound. Supply explained future judgments only: one downside/base/upside case per alternative, annual five-element paths. ETF price-only paths use income multipliers against known yield; reinvested total-return paths include income and require null income_multipliers. Gross paths deduct known fund costs; net paths already include them. Cash/short-bill paths use annual_rates only. No action drivers cover every actual scope position; retained unresearched stocks stay unquantified. Researched stock drivers must have null annual_returns, annual_rates and income_multipliers, reinvest false, price_only/gross basis, and FX matching the company case; use its operating outcomes rather than an ETF forecast. FX multipliers are relative to dated initial FX; same-currency multipliers must all be one. Explanations are qualitative with no probabilities, hurdles, numerical claims or trade instructions. Missing facts stay unknown; never invent allocation amounts.",
    "parameters": ComparisonJudgments.model_json_schema(), "strict": True,
}


STOCK_TOOLS: list[FunctionToolParam] = [
    {**PORTFOLIO_TOOL, "name": "get_sec_filings", "description": "Read backend-bound dated original SEC filing excerpts and checked facts for the selected issuer. No arguments."},
    {**PORTFOLIO_TOOL, "name": "get_issuer_material", "description": "Read backend-bound issuer investor-relations material with dates and availability. No arguments."},
    {"type": "function", "name": "calculate_company_cases", "description": "Calculate conditional company cases from bound reported facts and explained operating judgments. All paths have five annual elements. Net margin uses earnings, FCF applies cash conversion then reinvestment; book/FFO grow a reported metric with neutral operating transforms. Dilution changes shares; payout distributions remain idle cash. Book-value payout uses explicit annual return_on_equity earnings on opening book, never book capital itself. Use null return_on_equity for other methods. Exit multiple and sensitivities are explicit. No facts, probabilities or allocations may be supplied.", "parameters": CompanyJudgments.model_json_schema(), "strict": True},
]

SEDAR_TOOL: FunctionToolParam = {
    **PORTFOLIO_TOOL,
    "name": "get_sedar_filings",
    "description": "Read backend-bound dated SEDAR+ filing verification links and checked facts for the selected Canadian issuer. No arguments.",
}


ALLOCATION_TOOLS: list[FunctionToolParam] = [
    {**PORTFOLIO_TOOL, "name": "scan_opportunities", "description": "Read the fresh backend-bound request-local opportunity screen and refreshed prices. No arguments; coverage is explicit."},
    {"type": "function", "name": "research_candidate", "description": "Read primary filing and issuer evidence for a scanned US or Canadian candidate that could change this decision. At most two distinct candidates; give a qualitative decision-changing reason.", "parameters": CandidateResearchInput.model_json_schema(), "strict": True},
    {"type": "function", "name": "size_allocation", "description": "Propose a justified total post-contribution company or fund exposure range. Python converts it into approximate local-currency new-cash amounts and checks both endpoints against company cap and active budget; never waive limits.", "parameters": AllocationJudgment.model_json_schema(), "strict": True},
]


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
        request = json.loads(messages[1]["content"])
        allocation = bool(request.get("new_cash"))
        review = bool(request.get("portfolio_review"))
        theme = bool(request.get("theme"))
        schema = (StockRecommendation if request.get("stock") or allocation or review or theme else Recommendation).model_json_schema()
        schema["properties"]["amount"] = {"type": "null"}
        stock_tools: list[FunctionToolParam] = copy.deepcopy(STOCK_TOOLS)
        if request.get("stock"):
            target_id = request["stock"].get("position_id")
            target_pos = next((p for p in request.get("portfolio", {}).get("positions", []) if p.get("id") == target_id), None)
            if target_pos and (target_pos.get("currency") == "CAD" or target_pos.get("listing") in {"XTSE", "XTSX", "NEOE", "XCNQ"}):
                stock_tools = [SEDAR_TOOL, STOCK_TOOLS[1], STOCK_TOOLS[2]]
        if review:
            stock_tools = [{"type": "function", "name": "reunderwrite_holding", "description": "Challenge a bound current holding thesis against current primary evidence and any supplied prior thesis, then compute company cases. No price history or facts may be invented. Each bound US company runs once before comparison.", "parameters": HoldingReviewInput.model_json_schema(), "strict": True},
                           {"type": "function", "name": "size_review", "description": "Translate an explained whole-portfolio issuer exposure range into local adjustment amounts after evidence-backed holding review and comparison. Python checks both endpoints, funding, source inputs, supplied risk context, costs/tax effects and configured cap/budget. Unknown inputs never authorize an amount.", "parameters": ReviewSizingInput.model_json_schema(), "strict": True}]
        if allocation:
            stock_tools = [{**STOCK_TOOLS[-1], "parameters": CandidateCasesInput.model_json_schema()}]
        if theme:
            stock_tools = [{"type": "function", "name": "size_review", "description": "After completed agreed theme cases and comparison, propose a justified exposure range for a supported shortlisted addition funded by a supplied same-account/currency cash balance. Shared Python sizing enforces risk context, source inputs, dated costs/tax and cap/budget endpoints; missing inputs leave amounts unknown.", "parameters": ReviewSizingInput.model_json_schema(), "strict": True}, ALLOCATION_TOOLS[1], {**STOCK_TOOLS[-1], "parameters": CandidateCasesInput.model_json_schema()},
                           {"type": "function", "name": "test_theme_mechanism", "description": "Test the agreed economic mechanism for exactly one shortlisted candidate using its backend-bound evidence IDs. Explain support, challenge or unknown qualitatively; ETF citations use fund-POSITION_ID for dated sponsor facts. No numerical claims, probabilities or new facts.", "parameters": ThemeTestInput.model_json_schema(), "strict": True}]
        response = await self.client.responses.create(
            model=self.settings.model,
            input=cast(ResponseInputParam, messages),
            tools=[PORTFOLIO_TOOL, *FINANCIAL_TOOLS, PROPOSAL_TOOL, COMPARISON_TOOL, *stock_tools, *(ALLOCATION_TOOLS if allocation else [])],
            tool_choice=choice if require_tool else "auto",
            parallel_tool_calls=False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "portfolio_recommendation",
                    "schema": schema,
                    "strict": True,
                }
            },
            max_output_tokens=8000,
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
