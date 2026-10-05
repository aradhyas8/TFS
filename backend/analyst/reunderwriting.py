"""Whole-portfolio primary evidence binding; shared company and guardrail arithmetic."""
from decimal import ROUND_DOWN, Decimal, localcontext

from .calculations import money
from .guardrails import preview_changes
from .research import ResearchProvider
from .schemas import (
    AllocationAmount,
    AnalysisRequest,
    FinancialEvidence,
    PortfolioReview,
    ProposedChanges,
    ProposedTrade,
    ReunderwritingResult,
    ReviewSizingInput,
    Snapshot,
)


async def bind_holdings(snapshot: Snapshot, current: PortfolioReview,
                        provider: ResearchProvider, result: ReunderwritingResult) -> None:
    seen: set[str] = set()
    for row in current.positions:
        pos = row.supplied
        if pos.kind != "stock" or not pos.shares:
            continue
        if pos.currency != "USD" or pos.listing not in {"XNAS", "XNYS", "XASE"}:
            result.qualifications.append(f"{pos.id}: listing-specific cases are unavailable in this core US review; retained outcomes remain unknown.")
            continue
        key = pos.company_id or pos.id
        if key in seen:
            continue
        seen.add(key)
        record = (await provider.company(pos, snapshot.as_of)).model_copy(deep=True)
        if record.company_id != key:
            raise ValueError("Holding evidence conflicts with the bound issuer.")
        ids = {doc.id: f"review-{pos.id}-{doc.id}" for doc in record.documents}
        for doc in record.documents:
            doc.id = ids[doc.id]
            if doc.company_id != key or doc.published_on > snapshot.as_of or doc.as_of > snapshot.as_of:
                doc.available = False
        for fact in record.facts:
            fact.document_ids = [ids[doc_id] for doc_id in fact.document_ids]
        result.research[pos.id] = record



def size_review(request: AnalysisRequest, snapshot: Snapshot, evidence: FinancialEvidence,
                current: PortfolioReview, result: ReunderwritingResult,
                judgment: ReviewSizingInput) -> None:
    """Translate an explained issuer exposure range into checked local amounts."""
    result.sizing = judgment
    target = next((row for row in current.positions if row.supplied.id == judgment.position_id), None)
    cash = next((row for row in current.positions if row.supplied.id == judgment.cash_position_id and row.supplied.kind == "cash"), None)
    assessment = next((row for row in result.assessments if row.position_id == judgment.position_id), None)
    missing = result.missing_inputs
    if assessment is None or assessment.action not in {"add", "reduce", "exit"}:
        missing.append("Sizing needs a supported current holding direction.")
    if not result.context.risk_context:
        missing.append("Supply decision-critical loss tolerance and withdrawal context.")
    held = [row for row in current.positions if row.supplied.kind == "cash" or row.supplied.shares != 0]
    if not current.complete or any(not row.source_inputs_usable for row in held):
        missing.append("Whole-portfolio sizing needs verified identities and usable dated quotes and FX.")
    if target is None or not target.source_inputs_usable or target.quote_used is None or target.quote_used.value <= 0:
        missing.append("Selected holding needs a verified listing and usable positive price and FX.")
    if cash is None or target is None or (cash.supplied.account_id, cash.supplied.currency) != (target.supplied.account_id, target.supplied.currency):
        missing.append("Funding/proceeds cash must be supplied in the same account and currency.")
    settings = request.settings
    if settings is None or settings.single_company_cap is None or settings.active_budget is None:
        missing.append("Supply applicable numeric company cap and active budget.")
    alternative = next((row for row in request.comparison.alternatives if row.position_id == judgment.position_id and row.kind == "stock"), None) if request.comparison else None
    effect = next((row for row in request.comparison.effects if alternative and row.alternative_id == alternative.id and row.as_of == snapshot.as_of), None) if request.comparison else None
    if effect is None or effect.transaction_cost is None or effect.terminal_tax is None:
        missing.append("Known dated cost and tax effects are needed for supported adjustment sizing.")
    elif effect.transaction_cost != 0 or effect.terminal_tax != 0:
        missing.append("Nonzero costs/tax effects are shown in comparison; their rebalance funding impact is not modeled, so sizing remains conditional.")
    if missing:
        return
    assert target and target.quote_used and cash and assessment and current.total_value
    with localcontext() as arithmetic:
        arithmetic.prec = 60
        fx = target.fx_used.rate if target.fx_used else Decimal(1)
        issuer = target.identity.company_id if target.identity else target.supplied.company_id
        existing = sum((Decimal(row.value or "0") for row in current.positions if row.supplied.kind == "stock" and (row.identity.company_id if row.identity else row.supplied.company_id) == issuer), Decimal(0))
        deltas = [((Decimal(current.total_value) * weight - existing) / fx).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
                  for weight in (judgment.min_weight, judgment.max_weight)]
        if assessment.action == "add" and any(delta <= 0 for delta in deltas) or assessment.action in {"reduce", "exit"} and any(delta >= 0 for delta in deltas):
            missing.append("Exposure range must agree with the supported holding direction.")
            return
        if assessment.action == "exit" and (judgment.min_weight != 0 or judgment.max_weight != 0):
            missing.append("An exit range must remove the whole issuer exposure; partial reductions are not exits.")
            return
        actual_amounts = []
        for delta in deltas:
            shares = (delta / target.quote_used.value).quantize(Decimal("0.0000000001"), rounding=ROUND_DOWN)
            changes = ProposedChanges(new_cash=[], trades=[ProposedTrade(position_id=target.supplied.id,
                shares_change=shares, cash_position_id=cash.supplied.id)])
            result.previews.append(preview_changes(snapshot, evidence, current, settings, changes, "model"))
            actual_amounts.append(abs(shares * target.quote_used.value).quantize(Decimal("0.01")))
        if any(row.status != "within_limits" for row in result.previews):
            missing.append("Both proposed outcomes must pass company-cap and active-budget checks; unknown indirect coverage preserves the supplied cap policy.")
            return
        result.amount = AllocationAmount(minimum=money(min(actual_amounts)), maximum=money(max(actual_amounts)),
                                         currency=target.supplied.currency, position_id=target.supplied.id)
        result.qualifications.append("Approximate adjustment range uses whole-portfolio issuer exposure and the selected account/currency balance. Fractional-share previews are hypothetical, use dated marks, and do not imply executable quantities or placed orders.")
