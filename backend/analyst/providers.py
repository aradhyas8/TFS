import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from openai import AsyncOpenAI, omit
from openai.types.responses import FunctionToolParam, ResponseInputParam, ToolChoiceFunctionParam
from openai.types.responses.response_create_params import ToolChoice

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
    async def respond(
        self,
        messages: list[dict[str, Any]],
        *,
        require_tool: bool,
        forced_tool: str | None = None,
        reasoning_effort: str | None = None,
        final: bool = False,
    ) -> ModelTurn: ...


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

    def __init__(self, turns: list[ModelTurn], *, settings: Settings | None = None) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict[str, Any]]] = []
        self.reasoning_efforts: list[str | None] = []
        self.forced_tools: list[str | None] = []
        self.finals: list[bool] = []
        self.settings = settings

    async def respond(
        self,
        messages: list[dict[str, Any]],
        *,
        require_tool: bool,
        forced_tool: str | None = None,
        reasoning_effort: str | None = None,
        final: bool = False,
    ) -> ModelTurn:
        self.requests.append(copy.deepcopy(messages))
        self.reasoning_efforts.append(reasoning_effort)
        self.forced_tools.append(forced_tool)
        self.finals.append(final)
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

def sanitize_schema(schema: Any) -> Any:
    """Recursively strip regex lookaround patterns from JSON schema definitions.

    OpenAI's strict JSON schema validation rejects regular expressions containing
    lookaround assertions ((?=, (?!, (?<=, (?<!), which Pydantic generates for
    constrained Decimal types. Stripping these patterns leaves type constraints
    intact while satisfying OpenAI's schema validator. Python domain models continue
    to enforce complete Decimal precision and boundary checks during deserialization.
    """
    if isinstance(schema, dict):
        cleaned: dict[str, Any] = {}
        for k, v in schema.items():
            if k == "pattern" and isinstance(v, str) and any(x in v for x in ("(?=", "(?!", "(?<=", "(?<!")):
                continue
            cleaned[k] = sanitize_schema(v)
        return cleaned
    elif isinstance(schema, list):
        return [sanitize_schema(item) for item in schema]
    return schema


PROPOSAL_TOOL: FunctionToolParam = {
    "type": "function", "name": "check_proposed_changes",
    "description": "Check hypothetical share changes for submitted positions against backend-bound dated marks, FX and personal limits. new_cash must exactly match the explicitly supplied request preview (or be empty when absent); never invent cash. No execution or invented sizing. Cash funding must use the same account and currency as the security.",
    "parameters": sanitize_schema(ProposedChanges.model_json_schema()), "strict": True,
}

COMPARISON_TOOL: FunctionToolParam = {
    "type": "function", "name": "calculate_comparison",
    "description": "Calculate five-year conditional cases for exactly the alternatives in comparison_inputs. Call this tool only when comparison_inputs was explicitly provided in previous tool output (e.g. from review_portfolio, scan_opportunities, or calculate_company_cases). If comparison_inputs was not provided, do not call calculate_comparison. You MUST provide an entry in alternatives for EVERY alternative listed in comparison_inputs using its exact id as alternative_id. Do not omit any alternative. For each alternative, provide downside, base and upside cases where each driver's position_id matches exactly the alternative's position_id (or for kind='no_action', every ID in scope_position_ids). Do not guess position IDs. Starting capital, instrument identity, fund facts, known costs/tax and dated FX are backend bound. Supply explained future judgments only: annual five-element paths. ETF price-only paths use income multipliers against known yield; reinvested total-return paths include income and require null income_multipliers. Gross paths deduct known fund costs; net paths already include them. Cash/short-bill paths use annual_rates only. Researched stock drivers must have null annual_returns, annual_rates and income_multipliers, reinvest false, price_only/gross basis, and FX matching the company case. All rates, returns and multipliers must be valid decimal numbers; never pass text like 'unknown'. Explanations are qualitative with no probabilities, hurdles, numerical claims or trade instructions. Missing facts stay unknown in assumptions/uncertainty; never invent allocation amounts.",
    "parameters": sanitize_schema(ComparisonJudgments.model_json_schema()), "strict": True,
}


STOCK_TOOLS: list[FunctionToolParam] = [
    {**PORTFOLIO_TOOL, "name": "get_sec_filings", "description": "Read backend-bound dated original SEC filing excerpts and checked facts for the selected issuer. No arguments."},
    {**PORTFOLIO_TOOL, "name": "get_issuer_material", "description": "Read backend-bound issuer investor-relations material with dates and availability. No arguments."},
    {"type": "function", "name": "calculate_company_cases", "description": "Calculate conditional company cases from bound reported facts and explained operating judgments. All paths have five annual elements. Net margin uses earnings, FCF applies cash conversion then reinvestment; book/FFO grow a reported metric with neutral operating transforms. Dilution changes shares; payout distributions remain idle cash. Book-value payout uses explicit annual return_on_equity earnings on opening book, never book capital itself. Use null return_on_equity for other methods. Exit multiple and sensitivities are explicit. All numeric rates, margins and multipliers must be valid decimal strings; never pass 'unknown' in numeric fields. State unknown facts in assumptions/uncertainty. No facts, probabilities or allocations may be supplied. In stock analysis, you MUST call calculate_comparison immediately after this tool using the returned comparison_inputs before returning your final recommendation.", "parameters": sanitize_schema(CompanyJudgments.model_json_schema()), "strict": True},
]

SEDAR_TOOL: FunctionToolParam = {
    **PORTFOLIO_TOOL,
    "name": "get_sedar_filings",
    "description": "Read backend-bound dated SEDAR+ filing verification links and checked facts for the selected Canadian issuer. No arguments.",
}


ALLOCATION_TOOLS: list[FunctionToolParam] = [
    {**PORTFOLIO_TOOL, "name": "scan_opportunities", "description": "Read the fresh backend-bound request-local opportunity screen and refreshed prices. No arguments; coverage is explicit."},
    {"type": "function", "name": "research_candidate", "description": "Read primary filing and issuer evidence for a scanned US or Canadian candidate that could change this decision. At most two distinct candidates; give a qualitative decision-changing reason. If called, you must immediately call calculate_company_cases for this candidate before calculate_comparison.", "parameters": sanitize_schema(CandidateResearchInput.model_json_schema()), "strict": True},
    {"type": "function", "name": "size_allocation", "description": "Propose a justified total post-contribution company or fund exposure range. Python converts it into approximate local-currency new-cash amounts and checks both endpoints against company cap and active budget; never waive limits.", "parameters": sanitize_schema(AllocationJudgment.model_json_schema()), "strict": True},
]


class OpenAIModel:
    def __init__(self, settings: Settings) -> None:
        if not settings.api_key or not settings.model:
            raise ValueError("Backend OpenAI configuration is missing.")
        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.request_timeout,
            max_retries=2,
        )

    async def respond(
        self,
        messages: list[dict[str, Any]],
        *,
        require_tool: bool,
        forced_tool: str | None = None,
        reasoning_effort: str | None = None,
        final: bool = False,
    ) -> ModelTurn:
        choice: ToolChoiceFunctionParam = {"type": "function", "name": forced_tool if forced_tool else "review_portfolio"}
        request = json.loads(messages[1]["content"])
        allocation = bool(request.get("new_cash"))
        review = bool(request.get("portfolio_review"))
        theme = bool(request.get("theme"))
        schema = sanitize_schema((StockRecommendation if request.get("stock") or allocation or review or theme else Recommendation).model_json_schema())
        schema["properties"]["amount"] = {"type": "null"}
        stock_tools: list[FunctionToolParam] = copy.deepcopy(STOCK_TOOLS)
        if request.get("stock"):
            target_id = request["stock"].get("position_id")
            target_pos = next((p for p in request.get("portfolio", {}).get("positions", []) if p.get("id") == target_id), None)
            if target_pos and (target_pos.get("currency") == "CAD" or target_pos.get("listing") in {"XTSE", "XTSX", "NEOE", "XCNQ"}):
                stock_tools = [SEDAR_TOOL, STOCK_TOOLS[1], STOCK_TOOLS[2]]
        if review:
            stock_tools = [{"type": "function", "name": "reunderwrite_holding", "description": "Challenge a bound current holding thesis against current primary evidence and any supplied prior thesis, then compute company cases. All numeric judgment fields (growth, margins, discount_rate, exit_multiple, etc.) must contain valid numeric decimal strings; never pass 'unknown' or text in numeric fields. State unknown facts in assumptions, uncertainty or assessment status. No price history or facts may be invented. Each bound US company runs once before comparison.", "parameters": sanitize_schema(HoldingReviewInput.model_json_schema()), "strict": True},
                           {"type": "function", "name": "size_review", "description": "Translate an explained whole-portfolio issuer exposure range into local adjustment amounts after evidence-backed holding review and comparison. Python checks both endpoints, funding, source inputs, supplied risk context, costs/tax effects and configured cap/budget. Unknown inputs never authorize an amount.", "parameters": sanitize_schema(ReviewSizingInput.model_json_schema()), "strict": True}]
        if allocation:
            stock_tools = [{**STOCK_TOOLS[-1], "parameters": sanitize_schema(CandidateCasesInput.model_json_schema())}]
        if theme:
            stock_tools = [{"type": "function", "name": "size_review", "description": "After completed agreed theme cases and comparison, propose a justified exposure range for a supported shortlisted addition funded by a supplied same-account/currency cash balance. Shared Python sizing enforces risk context, source inputs, dated costs/tax and cap/budget endpoints; missing inputs leave amounts unknown.", "parameters": sanitize_schema(ReviewSizingInput.model_json_schema()), "strict": True}, ALLOCATION_TOOLS[1], {**STOCK_TOOLS[-1], "parameters": sanitize_schema(CandidateCasesInput.model_json_schema())},
                           {"type": "function", "name": "test_theme_mechanism", "description": "Test the agreed economic mechanism for exactly one shortlisted candidate using its backend-bound evidence IDs. Explain support, challenge or unknown qualitatively; ETF citations use fund-POSITION_ID for dated sponsor facts. No numerical claims, probabilities or new facts.", "parameters": sanitize_schema(ThemeTestInput.model_json_schema()), "strict": True}]
        raw_tools = [PORTFOLIO_TOOL, *FINANCIAL_TOOLS, PROPOSAL_TOOL, COMPARISON_TOOL, *stock_tools, *(ALLOCATION_TOOLS if allocation else [])]
        tools: list[FunctionToolParam] = [
            cast(FunctionToolParam, {**tool, "parameters": sanitize_schema(tool["parameters"])} if "parameters" in tool else tool)
            for tool in raw_tools
        ]
        effort = cast(Any, reasoning_effort if reasoning_effort is not None else self.settings.reasoning_effort)
        openrouter = "openrouter.ai" in self.settings.base_url
        t0 = time.monotonic()
        response = await self.client.responses.create(
            model=self.settings.model,
            reasoning={"effort": effort},
            input=cast(ResponseInputParam, messages),
            # The final synthesis request offers no callable tools; the strict answer schema below is unchanged.
            tools=omit if final else tools,
            tool_choice=omit if final else cast(ToolChoice, choice if (require_tool or forced_tool) else "auto"),
            # OpenRouter cannot route parallel_tool_calls with require_parameters; the pipeline still rejects multiple calls.
            parallel_tool_calls=omit if final or openrouter else False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "portfolio_recommendation",
                    "schema": schema,
                    "strict": True,
                }
            },
            max_output_tokens=16000,
            store=False,
            include=["reasoning.encrypted_content"],
            # OpenRouter only: pin the first-party OpenAI endpoint, honour every parameter, never fall back to another host.
            extra_body={"provider": {"only": ["openai"], "allow_fallbacks": False, "require_parameters": True}} if openrouter else None,
        )
        duration = time.monotonic() - t0
        tool_names = [item.name for item in response.output if item.type == "function_call"]
        usage = response.usage
        in_tok = getattr(usage, "input_tokens", None) if usage else None
        out_tok = getattr(usage, "output_tokens", None) if usage else None
        details = getattr(usage, "output_tokens_details", None) if usage else None
        reasoning_tok = getattr(details, "reasoning_tokens", None) if details else None
        logging.info(
            "OPENAI RESPONSE id=%s model=%s reasoning_effort=%s status=%s duration=%.2fs tools=%s usage=(in=%s, out=%s, reasoning=%s) cost=%s",
            response.id,
            response.model,
            effort,
            response.status,
            duration,
            tool_names,
            in_tok,
            out_tok,
            reasoning_tok,
            getattr(usage, "cost", None),
        )
        if response.status != "completed":
            inc_reason = getattr(response.incomplete_details, "reason", None) if response.incomplete_details else None
            partial_tools = [getattr(item, "name", None) for item in response.output if getattr(item, "type", None) == "function_call"]
            msg = (
                f"The model response is incomplete: reason={inc_reason}, "
                f"input_tokens={in_tok}, output_tokens={out_tok}, reasoning_tokens={reasoning_tok}, "
                f"max_output_tokens=16000, reasoning_effort={effort}, partial_tools={partial_tools}"
            )
            logging.error("OPENAI INCOMPLETE RESPONSE: %s", msg)
            raise ValueError(msg)
        continuation = []
        for item in response.output:
            dumped = item.model_dump(mode="json", exclude_none=True)
            dumped.pop("status", None)
            dumped.pop("caller", None)
            dumped.pop("namespace", None)
            continuation.append(dumped)
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
