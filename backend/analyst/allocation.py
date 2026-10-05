"""Bounded opportunity evidence and sizing within the shared decision pipeline."""
import os
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path
from typing import Protocol

from .calculations import money, review_portfolio
from .guardrails import preview_changes
from .schemas import (
    AllocationAmount,
    AllocationJudgment,
    AllocationResult,
    AnalysisRequest,
    CashContribution,
    ComparisonAlternative,
    ComparisonInput,
    DiscoveryCandidate,
    DiscoveryScan,
    FinancialEvidence,
    PortfolioReview,
    Position,
    ProposedChanges,
    ProposedTrade,
    Snapshot,
)


class DiscoveryProvider(Protocol):
    async def scan(self, snapshot: Snapshot) -> DiscoveryScan: ...


class ReviewedDiscoveryProvider:
    """Re-read a bounded reviewed universe every request, then refresh its prices.

    This is a lightweight dated screen, not a live market crawl. Missing or old
    screening records remain explicitly unavailable. Deep research is separate.
    """
    async def scan(self, snapshot: Snapshot) -> DiscoveryScan:
        path = os.environ.get("DISCOVERY_REFERENCE_FILE")
        scan = DiscoveryScan.model_validate_json(Path(path).read_text(encoding="utf-8")) if path else DiscoveryScan(
            scanned_at=datetime.now(UTC), as_of=snapshot.as_of,
            source="Request-local screen of current holdings; additional reviewed opportunity universe unavailable.")
        if path:
            scan.source_captured_at = scan.source_captured_at or scan.scanned_at
            scan.issues.append("Screening signals retain their original source capture time; re-reading them is not newly published evidence.")
        scan.scanned_at = datetime.now(UTC)
        if scan.as_of != snapshot.as_of:
            scan.candidates = []
            scan.fund_facts = []
            scan.issues.append("Opportunity universe date differs from the decision date; no current coverage is claimed.")
        ids = {row.position.id for row in scan.candidates}
        for row in snapshot.positions:
            eligible = row.kind == "etf" and row.etf_role == "diversified" or row.kind == "stock" and row.currency == "USD" and row.listing in {"XNAS", "XNYS", "XASE"}
            if eligible and row.id not in ids and len(scan.candidates) < 8:
                candidate = row.model_copy(update={"shares": Decimal(0)}, deep=True)
                scan.candidates.append(DiscoveryCandidate(position=candidate, as_of=snapshot.as_of,
                    source="Submitted holdings screen", signal="Assess current exposure against freshly requested dated identity and quote evidence."))
        if not path:
            scan.issues.append("Screen coverage is limited to supplied holdings; no broader fresh market coverage is available.")
        return scan


def bind_scan(snapshot: Snapshot, scan: DiscoveryScan) -> Snapshot:
    bound = snapshot.model_copy(deep=True)
    ids = {row.id: row for row in bound.positions}
    seen = set()
    for candidate in scan.candidates:
        row = candidate.position
        if row.id in seen or candidate.as_of != snapshot.as_of:
            raise ValueError("Discovery identities must be unique and dated to the snapshot.")
        seen.add(row.id)
        existing = ids.get(row.id)
        if existing:
            if (existing.ticker, existing.listing, existing.currency, existing.company_id, existing.kind) != (row.ticker, row.listing, row.currency, row.company_id, row.kind):
                raise ValueError("Discovery conflicts with a submitted position.")
        else:
            bound.positions.append(row)
    return Snapshot.model_validate(bound.model_dump())


def comparison_context(request: AnalysisRequest, snapshot: Snapshot, evidence: FinancialEvidence,
                       allocation: AllocationResult) -> tuple[PortfolioReview, ComparisonInput]:
    context = allocation.context
    cash = next((row for row in snapshot.positions if row.id == context.cash_position_id), None)
    funded = snapshot.model_copy(deep=True)
    # Missing account/amount still completes conditional cases with an unknown scope.
    fallback = cash or next((row for row in snapshot.positions if row.kind == "cash"), None)
    if fallback is None:
        fallback = Position(id="__new_cash__", account_id=snapshot.accounts[0].id,
                            kind="cash", currency=snapshot.reporting_currency, cash=Decimal(0))
    contribution = fallback.model_copy(update={"id": "__new_cash__", "cash": context.amount or Decimal(0)}, deep=True)
    funded.positions.append(contribution)
    review = review_portfolio(funded, evidence)
    if context.amount is None or cash is None:
        next(row for row in review.positions if row.supplied.id == "__new_cash__").value = None
    fund = next((row for row in snapshot.positions if row.kind == "etf" and row.etf_role == "diversified"
                 and (cash is None or (row.account_id, row.currency) == (cash.account_id, cash.currency))), None)
    alternatives = [ComparisonAlternative(id=row.position_id, kind="stock", position_id=row.position_id) for row in allocation.stocks]
    if fund:
        alternatives.append(ComparisonAlternative(id=fund.id, kind="etf", position_id=fund.id))
    alternatives.extend([ComparisonAlternative(id="cash", kind="cash", position_id="__new_cash__"),
                         ComparisonAlternative(id="keep", kind="no_action")])
    relevant = {row.position_id for row in alternatives}
    selection = ComparisonInput(scope_position_ids=["__new_cash__"], alternatives=alternatives,
        fund_facts=[row for row in allocation.scan.fund_facts if row.position_id in relevant])
    return review, selection


def size_allocation(request: AnalysisRequest, snapshot: Snapshot, evidence: FinancialEvidence,
                    current: PortfolioReview, allocation: AllocationResult,
                    judgment: AllocationJudgment) -> None:
    allocation.judgment = judgment
    prior_missing = list(allocation.missing_inputs)
    context = allocation.context
    target = next((row for row in current.positions if row.supplied.id == judgment.position_id), None)
    cash = next((row for row in current.positions if row.supplied.id == context.cash_position_id), None)
    settings = request.settings
    missing = prior_missing
    if not context.confirmed or context.amount is None or context.amount <= 0 or cash is None:
        missing.append("Confirm a positive new-cash amount and its intended account/currency cash balance.")
    if context.risk_context is None:
        missing.append("Supply decision-critical loss tolerance and withdrawal context.")
    held = [row for row in current.positions if row.supplied.kind == "cash" or row.supplied.shares != 0]
    if not current.complete or any(not row.source_inputs_usable for row in held):
        missing.append("Whole-portfolio snapshot needs usable dated identity, quote and FX evidence.")
    if target is None or target.supplied.kind == "cash" or not target.source_inputs_usable or target.quote_used is None or target.quote_used.value <= 0:
        missing.append("Selected security needs a verified identity and usable positive price and FX.")
    if target and cash and (target.supplied.account_id, target.supplied.currency) != (cash.supplied.account_id, cash.supplied.currency):
        missing.append("Selected security and confirmed new cash must share account and currency; transfers and FX execution are not inferred.")
    if settings is None or settings.single_company_cap is None or settings.active_budget is None:
        missing.append("Supply the applicable numeric company cap and active budget.")
    if target and target.supplied.kind == "stock":
        company = next((row for row in allocation.stocks if row.position_id == target.supplied.id), None)
        if company is None or any(row.terminal_price is None for row in company.cases):
            missing.append("Selected stock requires usable primary research and company cases.")
    if missing:
        allocation.missing_inputs = missing
        return
    assert target and target.quote_used and cash and context.amount is not None and current.total_value is not None
    with localcontext() as arithmetic:
        arithmetic.prec = 60
        fx = target.fx_used.rate if target.fx_used else Decimal(1)
        total = Decimal(current.total_value) + context.amount * fx
        if target.supplied.kind == "stock":
            issuer = target.identity.company_id if target.identity else target.supplied.company_id
            existing = sum((Decimal(row.value or "0") for row in current.positions if row.supplied.kind == "stock" and (row.identity.company_id if row.identity else row.supplied.company_id) == issuer), Decimal(0))
            if settings and settings.indirect_cap_policy == "include_known_indirect":
                overlap = next((row for row in current.company_overlap if row.company_id == issuer), None)
                if overlap and overlap.indirect_value:
                    existing += Decimal(overlap.indirect_value)
        else:
            existing = Decimal(target.value or "0")
        amounts = [((total * fraction - existing) / fx).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                   for fraction in (judgment.min_weight, judgment.max_weight)]
        if amounts[0] <= 0 or amounts[1] > context.amount:
            allocation.missing_inputs = ["The proposed exposure range does not fit a positive allocation funded solely by confirmed new cash; revise the judgment."]
            return
        for amount in amounts:
            shares = (amount / target.quote_used.value).quantize(Decimal("0.0000000001"), rounding=ROUND_DOWN)
            changes = ProposedChanges(new_cash=[CashContribution(cash_position_id=cash.supplied.id, amount=context.amount)],
                trades=[ProposedTrade(position_id=target.supplied.id, shares_change=shares, cash_position_id=cash.supplied.id)])
            allocation.previews.append(preview_changes(snapshot, evidence, current, settings, changes, "model"))
        if any(row.status != "within_limits" for row in allocation.previews):
            allocation.missing_inputs = ["Post-allocation company cap or active budget is breached or unknown; configured limits cannot be waived. Unknown indirect overlap preserves the supplied cap policy."]
            return
        allocation.amount = AllocationAmount(minimum=money(amounts[0]), maximum=money(amounts[1]),
                                             currency=target.supplied.currency, position_id=target.supplied.id)
