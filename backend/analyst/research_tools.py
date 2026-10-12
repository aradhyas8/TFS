"""Run-local primary research and calculations, using existing source adapters."""

from datetime import date
from typing import Any, Literal, cast

from pydantic import Field

from .allocation import DiscoveryProvider, ReviewedDiscoveryProvider, bind_scan
from .calculations import review_portfolio
from .context import ReadArguments, ReadCapability, RunContext
from .financial_data import MARKET_TIME, SOURCE_ERRORS, refresh_financial_data
from .financial_tools import FinancialTools
from .issuer_research import IssuerResearchProvider
from .pipeline import InvalidReview, validate_company_judgments, validate_prose
from .research import ResearchProvider
from .scenarios import calculate_comparison
from .schemas import (
    CompanyJudgments,
    CompanyResearch,
    ComparisonAlternative,
    ComparisonInput,
    ComparisonJudgments,
    ComparisonResult,
    Contract,
    DiscoveryScan,
    FinancialEvidence,
    Identifier,
    Position,
    StockResult,
    is_canadian_security,
    is_supported_stock,
)
from .sec_research import SecResearchProvider
from .stock import calculate_company_cases


class ResearchArguments(ReadArguments):
    position_id: Identifier


ResearchName = Literal["get_sec_filings", "get_sedar_filings", "get_issuer_material", "get_company_research"]


class PrimaryResearch(Contract):
    capability: ResearchName
    position_id: Identifier
    target: Position
    as_of: date
    document_ids: list[Identifier]
    research: CompanyResearch


class CaseArguments(ResearchArguments):
    judgments: CompanyJudgments


class CompanyCalculation(Contract):
    capability: Literal["calculate_company_cases"] = "calculate_company_cases"
    result: StockResult
    target: Position
    financial_evidence: FinancialEvidence


class ComparisonArguments(ReadArguments):
    scope_position_ids: list[Identifier] = Field(min_length=1, max_length=2000)
    alternatives: list[ComparisonAlternative] = Field(min_length=1, max_length=2003)
    judgments: ComparisonJudgments


class AlternativeCalculation(Contract):
    capability: Literal["calculate_comparison"] = "calculate_comparison"
    result: ComparisonResult
    source_artifact_ids: list[Identifier]
    financial_evidence: FinancialEvidence


class OpportunityEvidence(Contract):
    capability: Literal["scan_opportunities"] = "scan_opportunities"
    result: DiscoveryScan


class ResearchTools:
    def __init__(self, financial: FinancialTools, research: ResearchProvider | None,
                 workflow_input: dict[str, Any] | None = None,
                 discovery: DiscoveryProvider | None = None) -> None:
        self.financial = financial
        self._research = research
        self.records: dict[str, CompanyResearch] = {}
        self.read: dict[str, set[str]] = {}
        self.stocks: dict[str, StockResult] = {}
        self.stock_artifacts: dict[str, str] = {}
        supplied_comparison = (workflow_input or {}).get("comparison")
        self.comparison = ComparisonInput.model_validate(supplied_comparison) if supplied_comparison is not None else None
        self.discovery = discovery or ReviewedDiscoveryProvider()
        self.scan_result: DiscoveryScan | None = None
        self.screen_researched: set[str] = set()
        self.capabilities = {
            name: ReadCapability(ResearchArguments,
                "Retrieve backend primary issuer evidence for a known US/Canadian stock, including temporary candidates. "
                "No full analysis or portfolio change. Missing evidence stays unknown.",
                "known_instruments", self.handler(cast(ResearchName, name)))
            for name in ("get_sec_filings", "get_sedar_filings", "get_issuer_material")
        }
        self.capabilities["get_company_research"] = ReadCapability(
            ResearchArguments, "Retrieve primary filings and issuer material together for one known US/Canadian stock. "
            "Use this before company cases, especially for screened candidates, to fit both companies and a final answer "
            "within eight turns. Calls execute sequentially; facts and jurisdiction come from the backend provider.",
            "known_instruments", self.handler("get_company_research"))
        self.capabilities["calculate_company_cases"] = ReadCapability(
            CaseArguments, "Calculate conditional company cases using retrieved primary facts and existing explained judgment schemas. "
            "Requires get_company_research (or separate filing and issuer retrieval) plus a saved reporting-currency context. Does not authorize sizing.",
            "primary_research", self.cases)
        self.capabilities["calculate_comparison"] = ReadCapability(
            ComparisonArguments, "Compare selected alternatives on the same actual held capital, including no action. "
            "Supply explained future judgments only. Costs, tax and fund facts come from explicit user inputs or backend evidence; "
            "unknowns remain unknown. Stock alternatives require completed company cases. No sizing authorization.",
            "bound_revision", self.compare)
        self.capabilities["scan_opportunities"] = ReadCapability(
            ReadArguments, "Retrieve the existing dated opportunity screen once per run (at most eight candidates). "
            "Candidates stay temporary; primary research is limited to two screened stock candidates. "
            "Use get_company_research and then calculate_company_cases for each candidate before comparison and a final answer.",
            "bound_revision", self.scan)

    def handler(self, name: ResearchName) -> Any:
        async def retrieve(context: RunContext, parsed: ResearchArguments) -> dict[str, Any]:
            return await self.primary(context, parsed, name)
        return retrieve

    def target(self, context: RunContext, id: str) -> Position | None:
        positions = self.financial.select(context, [id])
        if not positions:
            return None
        target = positions[0]
        if not is_supported_stock(target) or not target.company_id or not target.company_name:
            return None
        return target

    def screened(self, target: Position) -> bool:
        return self.scan_result is not None and any(
            (row.position.ticker, row.position.listing, row.position.currency, row.position.company_id)
            == (target.ticker, target.listing, target.currency, target.company_id)
            for row in self.scan_result.candidates)

    async def primary(self, context: RunContext, parsed: ResearchArguments,
                      name: ResearchName) -> dict[str, Any]:
        target = self.target(context, parsed.position_id)
        if target is None:
            return self.financial.clarification(context, "unresolved_identity", "Resolve a supported US or Canadian stock identity first.")
        canadian = is_canadian_security(target.currency, target.listing)
        expected = "get_sedar_filings" if canadian else "get_sec_filings"
        if name in {"get_sec_filings", "get_sedar_filings"} and name != expected:
            return self.financial.clarification(context, "unavailable", f"Use {expected} for this issuer jurisdiction.")
        as_of = context.run.created_at.astimezone(MARKET_TIME).date()
        if target.id not in self.records:
            screened = self.screened(target)
            if screened and len(self.screen_researched) >= 2:
                return self.financial.clarification(context, "unavailable", "The screen's two-candidate primary research budget is exhausted.")
            if self._research is None:
                self._research = IssuerResearchProvider.from_environment(SecResearchProvider.from_environment())
            try:
                record = CompanyResearch.model_validate(await self._research.company(target, as_of)).model_copy(deep=True)
            except SOURCE_ERRORS:
                return self.financial.clarification(context, "unavailable", "Primary issuer research is unavailable.")
            if record.company_id != target.company_id:
                return self.financial.clarification(context, "unresolved_identity", "Primary evidence conflicts with the selected issuer.")
            for doc in record.documents:
                if doc.published_on > as_of or doc.as_of > as_of or not doc.excerpt.strip():
                    doc.available = False
                    record.issues.append(f"{doc.id}: evidence is unavailable or later than the decision date.")
            self.records[target.id] = record
            if screened:
                self.screen_researched.add(target.id)
        record = self.records[target.id]
        if not canadian and any(doc.authority in {"sedar", "sedar_plus"} for doc in record.documents):
            return self.financial.clarification(context, "unresolved_identity", "Research jurisdiction conflicts with the selected listing.")
        authorities = {"sec"} if name == "get_sec_filings" else {"sedar", "sedar_plus"} if name == "get_sedar_filings" else {"issuer"}
        if name == "get_company_research":
            authorities = {"sedar", "sedar_plus", "issuer"} if canadian else {"sec", "issuer"}
        selected = record.model_copy(deep=True)
        documents = [doc for doc in selected.documents if doc.authority in authorities]
        if not any(doc.available for doc in documents):
            selected.issues.append("Requested primary material is unavailable; no current coverage is claimed.")
        self.read.setdefault(target.id, set()).update(
            {expected, "get_issuer_material"} if name == "get_company_research" else {name})
        return self.financial.store(context, PrimaryResearch(
            capability=name, position_id=target.id, target=target, as_of=as_of,
            document_ids=[doc.id for doc in documents], research=selected,
        ), "company_research", "Primary issuer research",
            "hypothetical" if target.id in self.financial.candidates else "committed")

    async def cases(self, context: RunContext, parsed: CaseArguments) -> dict[str, Any]:
        target = self.target(context, parsed.position_id)
        if target is None:
            return self.financial.clarification(context, "unresolved_identity", "Resolve a supported stock identity first.")
        filing = "get_sedar_filings" if is_canadian_security(target.currency, target.listing) else "get_sec_filings"
        if not {filing, "get_issuer_material"}.issubset(self.read.get(target.id, set())):
            return self.financial.clarification(context, "unavailable", "Retrieve this issuer's primary filing and issuer material before calculating cases.")
        if context.portfolio is None:
            return self.financial.clarification(context, "portfolio_required", "Save a portfolio to establish the reporting currency for company cases.")
        try:
            validate_company_judgments(parsed.judgments)
        except InvalidReview:
            return {"error": "Company judgments contain unsupported claims or execution direction; supply explained conditional assumptions."}
        snapshot = self.financial.snapshot(context)
        snapshot.positions = [target]
        evidence = await refresh_financial_data(snapshot, self.financial.provider)
        computed = review_portfolio(snapshot, evidence, holdings_as_of=context.portfolio.snapshot.as_of)
        result = calculate_company_cases(target.id, self.records[target.id], parsed.judgments, computed)
        envelope = self.financial.store(context, CompanyCalculation(result=result, target=target, financial_evidence=evidence), "calculation_result", "Conditional company cases",
            "hypothetical" if target.id in self.financial.candidates else "committed")
        self.stocks[target.id] = result
        self.stock_artifacts[target.id] = envelope["reference"]["id"]
        return envelope

    async def compare(self, context: RunContext, parsed: ComparisonArguments) -> dict[str, Any]:
        if context.portfolio is None:
            return self.financial.clarification(context, "portfolio_required", "Save a portfolio to establish actual comparison capital.")
        selection = ComparisonInput(scope_position_ids=parsed.scope_position_ids, alternatives=parsed.alternatives)
        if self.comparison is not None:
            explicit = self.comparison
            if (selection.scope_position_ids, selection.alternatives) != (explicit.scope_position_ids, explicit.alternatives):
                return self.financial.clarification(context, "unavailable", "Compare exactly the explicitly selected scope and alternatives.")
            selection = explicit
        elif self.scan_result is not None:
            relevant = set(selection.scope_position_ids) | {row.position_id for row in selection.alternatives}
            selection.fund_facts = [fact for fact in self.scan_result.fund_facts if fact.position_id in relevant]
        held = {row.id for row in context.portfolio.snapshot.positions}
        ids = set(selection.scope_position_ids) | {str(row.position_id) for row in selection.alternatives if row.position_id}
        positions = self.financial.select(context, list(ids))
        if positions is None or not set(selection.scope_position_ids).issubset(held):
            return self.financial.clarification(context, "unknown_instrument", "Comparison capital must use actual bound holdings; destinations must be known instruments.")
        if not any(row.kind == "no_action" for row in selection.alternatives):
            return self.financial.clarification(context, "unavailable", "Include no action retaining the actual selected scope.")
        rows = {row.id: row for row in positions}
        for alternative in selection.alternatives:
            if alternative.position_id is None:
                continue
            row = rows[alternative.position_id]
            valid = (alternative.kind == "stock" and row.kind == "stock" and is_supported_stock(row)
                     or alternative.kind == "etf" and row.kind == "etf" and row.etf_role == "diversified"
                     or alternative.kind in {"cash", "short_bill"} and row.kind == "cash")
            if not valid:
                return self.financial.clarification(context, "unavailable", "Select supported stocks, diversified ETFs or cash/short-bill destinations.")
        stock_ids = {row.id for row in positions if row.kind == "stock"}
        if not stock_ids.issubset(self.stocks):
            return self.financial.clarification(context, "unavailable", "Complete company cases for every stock in the comparison, including retained holdings.")
        if any(fact.position_id not in rows or rows[fact.position_id].kind != "etf" for fact in selection.fund_facts):
            raise ValueError("Fund facts must reference comparison ETFs.")
        try:
            for judged_alternative in parsed.judgments.alternatives:
                for case in judged_alternative.cases:
                    validate_prose(" ".join([*case.assumptions, case.downside, *case.uncertainty]))
        except InvalidReview:
            return {"error": "Comparison judgments contain unsupported claims or execution direction; supply explained conditional assumptions."}
        snapshot = self.financial.snapshot(context)
        snapshot.positions = positions
        evidence = await refresh_financial_data(snapshot, self.financial.provider)
        computed = review_portfolio(snapshot, evidence, holdings_as_of=context.portfolio.snapshot.as_of)
        result = calculate_comparison(selection, parsed.judgments, computed, list(self.stocks.values()))
        return self.financial.store(context, AlternativeCalculation(
            result=result, source_artifact_ids=[self.stock_artifacts[id] for id in sorted(stock_ids)],
            financial_evidence=evidence,
        ), "calculation_result", "Conditional alternatives", "hypothetical")

    async def scan(self, context: RunContext, parsed: ReadArguments) -> dict[str, Any]:
        if context.portfolio is None:
            return self.financial.clarification(context, "portfolio_required", "Save a portfolio to establish the opportunity screen context.")
        if self.scan_result is None:
            snapshot = self.financial.snapshot(context)
            try:
                result = DiscoveryScan.model_validate(await self.discovery.scan(snapshot))
                if result.as_of != snapshot.as_of:
                    return self.financial.clarification(context, "unavailable", "The opportunity universe date differs from the decision date.")
                bound = bind_scan(snapshot, result)
            except SOURCE_ERRORS:
                return self.financial.clarification(context, "unavailable", "The bounded opportunity screen is unavailable or invalid.")
            held = {row.id for row in snapshot.positions}
            additions = {row.id: row for row in bound.positions if row.id not in held}
            if any(id in self.financial.candidates and self.financial.candidates[id] != row for id, row in additions.items()):
                return self.financial.clarification(context, "unresolved_identity", "Screen identities conflict with request-local candidates.")
            self.financial.candidates.update(additions)
            self.scan_result = result
            self.screen_researched.update(id for id in self.records
                if (target := self.target(context, id)) is not None and self.screened(target))
        return self.financial.store(context, OpportunityEvidence(result=self.scan_result), "financial_evidence", "Bounded opportunity screen", "hypothetical")
