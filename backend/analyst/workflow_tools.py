"""High-level workflow adapters; the existing pipeline owns financial validation."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, cast

from . import pipeline
from .allocation import DiscoveryProvider
from .config import Settings
from .context import ReadArguments, ReadCapability, RunContext
from .evaluation_budget import BudgetExhaustedError, TaskBudgetLedger
from .financial_data import FinancialProvider
from .financial_tools import CandidateArguments, FinancialTools
from .pipeline import analyze
from .providers import DataProvider, ModelTurn, WorkflowModelAdapter
from .research import ResearchProvider
from .research_tools import ResearchTools
from .schemas import (
    BENCHMARK_FUND,
    AnalysisRequest,
    Identifier,
    NewCashInput,
    PortfolioReviewInput,
    PortfolioSettings,
    Position,
    StockInput,
    ThemeInput,
    WorkflowClarification,
    WorkflowInput,
    WorkflowName,
)


def rules(settings: PortfolioSettings | None) -> PortfolioSettings | None:
    """Only rules that were set: a rule set with nothing in it is no rules, so nothing is checked."""
    if settings is None:
        return None
    values = settings.model_dump(exclude_none=True)
    if values.pop("baseline", None):
        return settings
    # A baseline with no value set is no baseline.
    return settings.model_copy(update={"baseline": None}) if values else None


class StockArguments(ReadArguments):
    position_id: Identifier | None = None


WORKFLOWS: dict[WorkflowName, tuple[str, Any]] = {
    "run_exposure_review": ("/review", None),
    "run_stock_analysis": ("/stock", "stock"),
    "run_new_cash_allocation": ("/new-cash", "new_cash"),
    "run_portfolio_reunderwriting": ("/rebalance", "portfolio_review"),
    "run_theme_exploration": ("/theme", "theme"),
}


class DeadlineProvider:
    """Cap nested provider operations by the configured timeout and shared deadline."""

    def __init__(
        self,
        transport: Any,
        settings: Settings,
        deadline: float,
        ledger: TaskBudgetLedger | None = None,
    ) -> None:
        self.transport = transport
        self.settings = settings
        self.deadline = deadline
        self.ledger = ledger

    def timeout(self) -> float:
        if self.ledger is not None:
            return self.ledger.remaining_timeout(self.settings.request_timeout)
        return min(
            self.settings.request_timeout,
            max(0, self.deadline - asyncio.get_running_loop().time()),
        )

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        async def bounded(*args: Any, **kwargs: Any) -> Any:
            return await asyncio.wait_for(getattr(self.transport, name)(*args, **kwargs), timeout=self.timeout())
        return bounded


class DeadlineTransport(DeadlineProvider):

    async def respond(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
        **kwargs: Any,
    ) -> ModelTurn:
        timeout = self.timeout()
        parameters = inspect.signature(self.transport.respond).parameters
        if "timeout" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            kwargs["timeout"] = timeout

        attempt = None
        if self.ledger is not None:
            if self.ledger.remaining_seconds() <= 0:
                raise BudgetExhaustedError("Run deadline of 600 seconds exceeded.", reason="deadline_exceeded")
            attempt = self.ledger.charge_attempt("nested_workflow")

        try:
            turn = cast(
                ModelTurn,
                await asyncio.wait_for(
                    self.transport.respond(messages, tools=tools, **kwargs),
                    timeout=timeout,
                ),
            )
            if self.ledger is not None and attempt is not None:
                usage_dict = None
                if hasattr(turn, "usage") and turn.usage:
                    usage_dict = turn.usage if isinstance(turn.usage, dict) else turn.usage.model_dump()
                self.ledger.record_attempt_result(attempt, success=True, usage=usage_dict)
            return turn
        except Exception as exc:
            if self.ledger is not None and attempt is not None:
                self.ledger.record_attempt_result(attempt, success=False, error=str(exc))
            raise


class WorkflowTools:
    def __init__(
        self,
        financial: FinancialTools,
        research: ResearchTools,
        data: DataProvider,
        transport: Any,
        settings: Settings,
        deadline: float,
        user_text: str,
        workflow_input: dict[str, Any] | None,
        ledger: TaskBudgetLedger | None = None,
    ) -> None:
        self.financial = financial
        self.research = research
        self.data = data
        self.settings = settings
        self.deadline = deadline
        self.ledger = ledger
        self.model = WorkflowModelAdapter(
            DeadlineTransport(transport, settings, deadline, ledger=ledger),
            settings=settings,
        )
        self.model.v2_accounting = ledger is not None
        self.user_text = user_text
        self.inputs = WorkflowInput.model_validate(workflow_input or {})
        # The recommendation of the last completed workflow: a form submission's answer, with no model turn.
        self.answer: str | None = None
        self.capabilities: dict[str, ReadCapability] = {
            name: ReadCapability(
                StockArguments if name == "run_stock_analysis" else ReadArguments,
                f"Execute {name} through the full validated workflow using bound holdings, rules and benchmark. "
                "Missing fields return form requirements. Theme/new-cash agreement requires explicit workflow form submission; "
                "conversation and tool arguments cannot authorize it.",
                "bound_revision_and_explicit_inputs",
                self.handler(name),
                effect="workflow",
            )
            for name in WORKFLOWS
        }

    def handler(self, name: WorkflowName) -> Any:
        async def run(context: RunContext, parsed: ReadArguments) -> dict[str, Any]:
            return await self.execute(context, parsed, name)
        return run

    def clarify(self, name: WorkflowName, fields: list[str], *, code: str = "missing_inputs",
                message: str = "Complete the explicit workflow form, then submit a new message/run.") -> dict[str, Any]:
        clarification = WorkflowClarification.model_validate({
            "workflow": name, "code": code, "fields": fields,
            "form": "holdings" if code in {"portfolio_required", "unresolved_identity"} else WORKFLOWS[name][0],
            "message": message,
        })
        return {"clarification_required": clarification.model_dump(mode="json")}

    async def bind_candidates(self, context: RunContext, name: WorkflowName) -> dict[str, Any] | None:
        """Resolve each unowned security the form named through the same lookup resolve_candidate uses, under the
        form's ID. Only the ticker and listing come from the browser; identity, listing and currency are re-verified."""
        assert context.portfolio is not None
        held = {row.id for row in context.portfolio.snapshot.positions}
        account = context.portfolio.snapshot.accounts[0].id if context.portfolio.snapshot.accounts else None
        for choice in self.inputs.candidates:
            if choice.id in self.financial.candidates:
                continue
            before = set(self.financial.candidates)
            if choice.id not in held:
                await self.financial.candidate(context, CandidateArguments(
                    ticker=choice.ticker, listing=choice.listing, account_id=account))
            added = set(self.financial.candidates) - before
            if len(added) != 1:
                return self.clarify(name, ["candidates"], code="invalid_inputs",
                                    message=f"{choice.ticker} could not be resolved to one supported listing; select it again.")
            row = self.financial.candidates.pop(added.pop())
            self.financial.resolved.pop(row.id, None)
            row = row.model_copy(update={"id": choice.id})
            self.financial.candidates[choice.id] = row
            self.financial.resolved[choice.id] = row.model_copy(deep=True)
        return None

    async def execute(self, context: RunContext, parsed: ReadArguments,
                      name: WorkflowName) -> dict[str, Any]:
        if context.portfolio is None:
            return self.clarify(name, ["portfolio"], code="portfolio_required")
        if context.portfolio.unresolved:
            return self.clarify(name, ["holdings.identity"], code="unresolved_identity")
        unresolved = await self.bind_candidates(context, name)
        if unresolved is not None:
            return unresolved
        snapshot = context.portfolio.snapshot.model_copy(deep=True)
        selected: StockInput | NewCashInput | ThemeInput | PortfolioReviewInput | None = None
        if name == "run_stock_analysis":
            selected = self.inputs.stock
            if isinstance(parsed, StockArguments) and parsed.position_id:
                if selected is not None and selected.position_id != parsed.position_id:
                    return self.clarify(name, ["stock.position_id"], code="invalid_inputs")
                selected = StockInput(position_id=parsed.position_id)
            if selected is None:
                return self.clarify(name, ["stock.position_id"])
        elif name == "run_new_cash_allocation":
            selected = self.inputs.new_cash or NewCashInput()
            missing = []
            if selected.amount is None or selected.amount <= 0:
                missing.append("new_cash.amount")
            if not selected.cash_position_id and not selected.account_id:
                missing.append("new_cash.account_id")
            # A submitted form may leave loss tolerance out: the analysis then stays conditional on it, as the desk's
            # new-cash answer always was, and offers a re-run with it.
            if not selected.risk_context and self.inputs.workflow != name:
                missing.append("new_cash.risk_context")
            if not selected.confirmed or self.inputs.workflow != name:
                missing.append("new_cash.confirmed")
            if missing:
                return self.clarify(name, missing)
        elif name == "run_theme_exploration":
            selected = self.inputs.theme or ThemeInput()
            missing = [f"theme.{field}" for field in ("name", "mechanism", "shortlist") if not getattr(selected, field)]
            if not selected.confirmed or self.inputs.workflow != name:
                missing.append("theme.confirmed")
            if missing:
                return self.clarify(name, missing)
        elif name == "run_portfolio_reunderwriting":
            selected = self.inputs.portfolio_review or PortfolioReviewInput()
        benchmark = context.portfolio.benchmark
        if benchmark and snapshot.accounts:
            account = snapshot.accounts[0].id
            if isinstance(selected, NewCashInput):
                # The benchmark is a destination for new money only in that money's currency and account.
                account = selected.account_id or account
                if benchmark.currency != (selected.currency or snapshot.reporting_currency):
                    benchmark = None
            if benchmark:
                snapshot.positions.append(Position(id=BENCHMARK_FUND, account_id=account,
                    kind="etf", etf_role="diversified", currency=benchmark.currency,
                    ticker=benchmark.ticker, listing=benchmark.listing, company_name=benchmark.name, shares=Decimal(0)))
        snapshot.positions.extend(row.model_copy(deep=True) for row in self.financial.candidates.values())
        values: dict[str, Any] = {
            # The selected adapter, rather than the legacy question regex, chooses the workflow.
            "question": name, "portfolio": snapshot, "settings": rules(context.portfolio.settings),
            "analysis_date": self.inputs.analysis_date or max(snapshot.as_of, pipeline.market_today()),
            "comparison": self.inputs.comparison,
            "proposed_changes": self.inputs.proposed_changes,
            # A run on a stored draft names it in the result, so the answer is labelled hypothetical wherever it is shown.
            "artifact_reference": (context.db.get_artifact_reference(context.run.hypothetical_artifact_id)
                                   if context.run.hypothetical_artifact_id else None),
        }
        field = WORKFLOWS[name][1]
        if field:
            values[field] = selected
        try:
            request = AnalysisRequest.model_validate(values)
        except ValueError:
            return self.clarify(name, [field or "comparison"], code="invalid_inputs",
                                message="The selected inputs conflict with workflow validation; revise the explicit form.")
        request.question = self.user_text
        research = self.research.provider if selected is not None else None
        result = await analyze(
            request,
            self.model,
            self.data,
            secret=self.settings.api_key,
            financial=cast(FinancialProvider, DeadlineProvider(self.financial.provider, self.settings, self.deadline, ledger=self.ledger)),
            research=cast(ResearchProvider, DeadlineProvider(research, self.settings, self.deadline, ledger=self.ledger)) if research else None,
            discovery=cast(DiscoveryProvider, DeadlineProvider(self.research.discovery, self.settings, self.deadline, ledger=self.ledger)),
        )
        current = context.db.get_run(context.run.thread_id, context.run.id)
        if current is None or current.status != "running":
            raise asyncio.CancelledError
        if self.ledger is not None and self.ledger.remaining_seconds() <= 0:
            raise BudgetExhaustedError("Run deadline of 600 seconds exceeded.", reason="deadline_exceeded")
        if asyncio.get_running_loop().time() >= self.deadline:
            raise TimeoutError
        self.answer = result.recommendation.reason
        return self.financial.store(context, result, "analysis_result", name.removeprefix("run_").replace("_", " ").title())
