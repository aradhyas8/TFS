"""Direct financial capabilities using immutable run holdings and existing services."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import Field

from .calculations import review_portfolio
from .context import ReadArguments, ReadCapability, RunContext
from .financial_data import (
    MARKET_TIME,
    SOURCE_ERRORS,
    FinancialProvider,
    PersonalFinancialProvider,
    refresh_financial_data,
)
from .guardrails import apply_guardrails, preview_changes
from .model_view import model_envelope
from .portfolio import listing_currency, split_ticker
from .schemas import (
    ArtifactBasis,
    ArtifactKind,
    ArtifactReference,
    CandidateListing,
    CandidateResolution,
    CandidateResult,
    CandidateUnresolved,
    ChangeCalculation,
    Contract,
    DatedFinancialData,
    FinancialClarification,
    FinancialClarificationCode,
    FinancialClarificationResult,
    Identifier,
    Identity,
    PortfolioCalculation,
    Position,
    ProposedChanges,
    ProposedTrade,
    QuoteRefresh,
    QuoteRefreshResult,
    Snapshot,
)


class InstrumentArguments(ReadArguments):
    position_ids: list[Identifier] = Field(min_length=1, max_length=100)


class DataArguments(InstrumentArguments):
    as_of: date | None


class RefreshArguments(InstrumentArguments):
    force: bool


class CandidateArguments(ReadArguments):
    ticker: Identifier
    listing: Identifier | None
    account_id: Identifier | None


class ChangeArguments(ReadArguments):
    trades: list[ProposedTrade] = Field(min_length=1, max_length=100)


class FinancialTools:
    def __init__(self, provider: FinancialProvider | None) -> None:
        self._provider = provider
        self.candidates: dict[str, Position] = {}
        # Only rows resolve_candidate accepted (verified or supplied identity, no mark);
        # opportunity screens also fill `candidates`.
        self.resolved: dict[str, Position] = {}
        self.capabilities = {
            "review_portfolio": ReadCapability(ReadArguments,
                "Calculate cash, exposure, ETF look-through and saved guardrails from bound holdings.",
                "bound_revision", self.review),
            "get_financial_data": ReadCapability(DataArguments,
                "Read qualified dated identities, quotes, FX and sponsor holdings for bound or request-local instruments. "
                "Use null as_of for the run's valuation date. No prices are fetched from market sources.",
                "known_instruments", self.data),
            "resolve_candidate": ReadCapability(CandidateArguments,
                "Resolve a ticker/listing into a temporary zero-share row. Specify an existing account if ambiguous. "
                "This does not add holdings or refresh prices.", "none", self.candidate),
            "refresh_quotes": ReadCapability(RefreshArguments,
                "Explicitly refresh the quote cache for selected bound or request-local instruments.",
                "known_instruments", self.refresh, effect="cache_refresh"),
            "check_proposed_changes": ReadCapability(ChangeArguments,
                "Check hypothetical trades using saved settings and existing cash in the named account/currency. "
                "No new funding, transfers, FX execution or portfolio commits are inferred.",
                "bound_revision", self.preview),
        }

    @property
    def provider(self) -> FinancialProvider:
        if self._provider is None:
            self._provider = PersonalFinancialProvider.from_environment()
        return self._provider

    def snapshot(self, context: RunContext) -> Snapshot:
        if context.portfolio is None:
            raise ValueError("A saved portfolio is required.")
        snapshot = context.portfolio.snapshot.model_copy(deep=True)
        snapshot.as_of = max(snapshot.as_of, context.run.created_at.astimezone(MARKET_TIME).date())
        return snapshot

    def clarification(self, context: RunContext, code: FinancialClarificationCode, message: str,
                      choices: list[str] | None = None) -> dict[str, Any]:
        return self.store(context, FinancialClarificationResult(clarification_required=FinancialClarification(
            code=code, message=message, choices=choices or [],
        )), "financial_evidence", "Financial input required")

    def select(self, context: RunContext, ids: list[str]) -> list[Position] | None:
        positions = {p.id: p for p in context.portfolio.snapshot.positions} if context.portfolio else {}
        positions.update(self.candidates)
        if len(set(ids)) != len(ids) or any(id not in positions for id in ids):
            return None
        return [positions[id].model_copy(deep=True) for id in ids]

    def store(self, context: RunContext, payload: Contract, kind: ArtifactKind,
              label: str, basis: ArtifactBasis = "committed") -> dict[str, Any]:
        revision = context.run.bound_revision
        draft = context.run.hypothetical_artifact_id
        reference = ArtifactReference(
            id=f"art-fin-{uuid4().hex}", kind=kind, label=label, created_at=datetime.now(UTC),
            # Everything a hypothetical run derives is labeled with the draft, never a committed revision.
            revision=None if draft else revision,
            basis="hypothetical" if draft else basis if revision else "none",
            source_artifact_id=draft or (f"art-rev-{revision}" if revision else None),
            originating_run_id=context.run.id,
        )
        context.db.store_artifact(reference, payload)
        context.artifact_ids.add(reference.id)
        envelope = context.db.get_artifact_envelope(reference.id, projection="model")
        assert envelope is not None
        return model_envelope(envelope.model_dump(mode="json"))

    async def review(self, context: RunContext, parsed: ReadArguments) -> dict[str, Any]:
        if context.portfolio is None:
            return self.clarification(context, "portfolio_required", "Save a portfolio before calculating its exposure.")
        if context.portfolio.unresolved:
            return self.clarification(context, "unresolved_identity", "Identify all saved unresolved holdings before calculating complete portfolio exposure and guardrails.")
        snapshot = self.snapshot(context)
        evidence = await refresh_financial_data(snapshot, self.provider)
        assert context.portfolio is not None
        result = review_portfolio(snapshot, evidence, holdings_as_of=context.portfolio.snapshot.as_of)
        apply_guardrails(result, context.portfolio.settings)
        return self.store(context, PortfolioCalculation(result=result), "calculation_result", "Portfolio exposure and guardrails")

    async def data(self, context: RunContext, parsed: DataArguments) -> dict[str, Any]:
        positions = self.select(context, parsed.position_ids)
        if positions is None:
            return self.clarification(context, "unknown_instrument", "Select distinct bound or request-local position IDs.")
        if context.portfolio is None:
            return self.clarification(context, "portfolio_required", "Save a portfolio to establish the reporting currency and dated financial context.")
        snapshot = self.snapshot(context)
        snapshot.positions = positions
        if parsed.as_of is not None:
            if parsed.as_of > context.run.created_at.astimezone(MARKET_TIME).date():
                return self.clarification(context, "unavailable", "Future dated financial inputs are unavailable.")
            snapshot.as_of = parsed.as_of
        result = await refresh_financial_data(snapshot, self.provider)
        qualifications = review_portfolio(snapshot, result, holdings_as_of=context.portfolio.snapshot.as_of).qualifications
        return self.store(context, DatedFinancialData(as_of=snapshot.as_of, result=result, qualifications=qualifications),
                          "financial_evidence", "Dated financial evidence",
                          "hypothetical" if any(id in self.candidates for id in parsed.position_ids) else "committed")

    async def candidate(self, context: RunContext, parsed: CandidateArguments) -> dict[str, Any]:
        accounts = [a.id for a in context.portfolio.snapshot.accounts] if context.portfolio else ["candidate"]
        account = parsed.account_id
        if account is None and len(accounts) == 1:
            account = accounts[0]
        if account not in accounts:
            return self.clarification(context, "select_account", "Select the account for this temporary candidate.", accounts)
        ticker, suffix_listing = split_ticker(parsed.ticker.upper())
        if suffix_listing and parsed.listing and suffix_listing != parsed.listing:
            return self.clarification(context, "unresolved_identity", "Ticker suffix and selected listing conflict.")
        listing = parsed.listing or suffix_listing
        lookup = getattr(self.provider, "lookup", None)
        if lookup is None:
            return self.clarification(context, "unavailable", "Candidate lookup is unavailable from the configured financial source.")
        as_of = context.run.created_at.astimezone(MARKET_TIME).date()
        try:
            matches = [Identity.model_validate(row) for row in await lookup(ticker, listing, listing_currency(listing), as_of)]
        except SOURCE_ERRORS:
            return self.clarification(context, "unavailable", "Candidate lookup failed; the identity remains unknown.")
        matches = [row for row in matches if row.ticker == ticker and (listing is None or row.listing == listing)]
        matches = list({(row.listing, row.currency, row.kind, row.company_id, row.company_name, row.status): row
                        for row in matches}.values())
        if len(matches) != 1:
            result = CandidateResult(unresolved=CandidateUnresolved(
                reason="multiple" if matches else "not_found",
                listings=[CandidateListing(ticker=ticker, listing=row.listing, currency=row.currency,
                                           kind=row.kind, company_name=row.company_name) for row in matches],
            ))
        else:
            row = matches[0]
            if (row.status not in {"verified", "supplied"} or not row.listing or not row.currency or not row.kind
                    or listing_currency(row.listing) not in {None, row.currency}
                    or row.kind == "stock" and (not row.company_id or not row.company_name)):
                return self.clarification(context, "unresolved_identity", "Candidate identity, listing, type or currency is unresolved.")
            position = Position(id=f"candidate-{uuid4().hex}", account_id=account, kind=row.kind,
                                ticker=ticker, listing=row.listing, currency=row.currency,
                                company_id=row.company_id, company_name=row.company_name, shares=Decimal(0))
            self.candidates[position.id] = position
            self.resolved[position.id] = position.model_copy(deep=True)
            result = CandidateResult(position=position, is_fund=row.kind == "etf")
        return self.store(context, CandidateResolution(result=result), "financial_evidence", "Candidate identity", "hypothetical")

    async def refresh(self, context: RunContext, parsed: RefreshArguments) -> dict[str, Any]:
        positions = self.select(context, parsed.position_ids)
        if positions is None or any(p.kind == "cash" for p in positions):
            return self.clarification(context, "unknown_instrument", "Select distinct known securities for quote refresh.")
        refresh = getattr(self.provider, "refresh_quotes", None)
        if refresh is None:
            return self.clarification(context, "unavailable", "Quote-cache refresh is unavailable from the configured financial source.")
        try:
            result = QuoteRefreshResult.model_validate(await refresh(positions, force=parsed.force))
        except SOURCE_ERRORS:
            return self.clarification(context, "unavailable", "Quote-cache refresh failed; committed holdings are unchanged.")
        return self.store(context, QuoteRefresh(result=result), "financial_evidence", "Quote-cache refresh",
                          "hypothetical" if any(id in self.candidates for id in parsed.position_ids) else "committed")

    async def preview(self, context: RunContext, parsed: ChangeArguments) -> dict[str, Any]:
        if context.portfolio is None:
            return self.clarification(context, "portfolio_required", "Save a portfolio before checking funded changes.")
        if context.portfolio.unresolved:
            return self.clarification(context, "unresolved_identity", "Identify all saved unresolved holdings before checking portfolio changes.")
        changes = ProposedChanges(new_cash=[], trades=parsed.trades)
        ids = list(dict.fromkeys([id for trade in changes.trades for id in (trade.position_id, trade.cash_position_id)]))
        if self.select(context, ids) is None:
            return self.clarification(context, "unknown_instrument", "Trades must select bound holdings or request-local candidates and saved cash.")
        snapshot = self.snapshot(context)
        snapshot.positions.extend(self.candidates[id] for id in ids if id in self.candidates)
        snapshot = Snapshot.model_validate(snapshot.model_dump())
        evidence = await refresh_financial_data(snapshot, self.provider)
        current = review_portfolio(snapshot, evidence, holdings_as_of=context.portfolio.snapshot.as_of)
        apply_guardrails(current, context.portfolio.settings)
        result = preview_changes(snapshot, evidence, current, context.portfolio.settings, changes, "model")
        return self.store(context, ChangeCalculation(result=result), "calculation_result", "Hypothetical funded changes", "hypothetical")
