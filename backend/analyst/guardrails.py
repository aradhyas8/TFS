"""Explicit personal settings applied to the same dated whole-portfolio valuation."""

from decimal import Decimal, localcontext
from typing import Literal

from .calculations import money, review_portfolio, sum_values, weight
from .schemas import (
    ActiveBudgetCheck,
    BaselineComparison,
    CheckStatus,
    CompanyCapCheck,
    FinancialEvidence,
    GuardrailReview,
    PortfolioReview,
    PortfolioSettings,
    Position,
    ProposalReview,
    ProposedChanges,
    Snapshot,
)


def check_limit(value: Decimal | None, total: Decimal | None, limit: Decimal | None) -> CheckStatus:
    if limit is None:
        return "unset"
    if value is None or total is None or total == 0:
        return "unknown"
    return "breached" if value > limit * total else "within_limit"


def has_etf_exposure(review: PortfolioReview) -> bool:
    return any(
        row.supplied.kind == "etf" and (row.value is None or Decimal(row.value) > 0)
        for row in review.positions
    )


def apply_guardrails(review: PortfolioReview, settings: PortfolioSettings | None) -> None:
    if settings is None:
        return
    with localcontext() as context:
        context.prec = 60
        review.guardrails = _checks(review, settings)
        review.baseline = settings.baseline
    review.qualifications = [
        note for note in review.qualifications if not note.startswith("Baseline, company cap")
    ]
    review.qualifications.append(
        "Only explicit personal settings are applied. Passing a check is not a justified allocation amount; research, risk context, costs and tax effects remain incomplete."
    )


def _checks(review: PortfolioReview, settings: PortfolioSettings) -> GuardrailReview:
    total = Decimal(review.total_value) if review.total_value is not None else None
    cap = settings.single_company_cap
    overlap_unknown = has_etf_exposure(review) and settings.indirect_cap_policy != "direct_only"
    companies = []
    for company in review.direct_companies:
        value = Decimal(company.value) if company.value is not None else None
        status = check_limit(value, total, cap)
        if overlap_unknown and status == "within_limit":
            status = "unknown"
        excess = (
            max(value - cap * total, Decimal(0))
            if value is not None and total is not None and total > 0 and cap is not None
            else None
        )
        explanation = (
            "No company cap was supplied."
            if cap is None
            else "Direct exposure is within the configured cap."
        )
        if status == "breached":
            explanation = "Existing direct exposure exceeds the configured cap; it is not an approved exception. A forward path is to reduce direct exposure to cash retained in the portfolio, or reassess with explicitly supplied new cash. The displayed reduction is a dated minimum, before unknown costs and taxes."
        elif status == "unknown":
            explanation = "A cap conclusion requires a complete usable valuation and the applicable indirect-exposure policy and evidence."
        if overlap_unknown:
            explanation += " ETF overlap is unknown; any direct reduction is only a lower bound for a cap that includes indirect exposure."
        companies.append(
            CompanyCapCheck(
                company_id=company.company_id,
                company_name=company.company_name,
                current_weight=company.weight,
                cap=money(cap) if cap is not None else None,
                status=status,
                excess_value=money(excess) if excess is not None else None,
                reduction_to_cash=money(excess) if excess is not None else None,
                explanation=explanation,
            )
        )

    categories: dict[str, list[Decimal | None]] = {
        "stocks": [],
        "diversified_etfs": [],
        "sector_theme_etfs": [],
        "cash": [],
    }
    unclassified = False
    for row in review.positions:
        value = Decimal(row.value) if row.value is not None else None
        if row.supplied.kind == "stock":
            categories["stocks"].append(value)
        elif row.supplied.kind == "cash":
            categories["cash"].append(value)
        elif row.supplied.etf_role:
            categories[f"{row.supplied.etf_role}_etfs"].append(value)
        elif value is None or value > 0:
            unclassified = True
    totals = {key: sum_values(values)[1] for key, values in categories.items()}
    notes = []
    if unclassified:
        totals["diversified_etfs"] = totals["sector_theme_etfs"] = None
        notes.append(
            "ETF classification is missing; diversified and sector/theme totals remain unknown."
        )
    cash_tilt: Decimal | None = None
    cash = totals["cash"]
    if cash == 0 or settings.cash_is_deliberate_tilt is False:
        cash_tilt = Decimal(0)
    elif (
        settings.cash_is_deliberate_tilt is True
        and settings.baseline is not None
        and settings.baseline.cash is not None
        and total is not None
        and total > 0
        and cash is not None
    ):
        cash_tilt = max(cash - settings.baseline.cash * total, Decimal(0))
    else:
        notes.append(
            "Deliberate excess cash needs an explicit cash-tilt classification and cash baseline; its active contribution is unknown."
        )
    contributions = {
        "stocks": totals["stocks"],
        "sector_theme_etfs": totals["sector_theme_etfs"],
        "excess_cash": cash_tilt,
    }
    _, active_value = sum_values(list(contributions.values()))
    known = (
        sum_values(categories["stocks"])[0]
        + sum_values(categories["sector_theme_etfs"])[0]
        + (cash_tilt if cash_tilt is not None else Decimal(0))
    )
    active_status = check_limit(active_value, total, settings.active_budget)
    # Missing contributions cannot clear a budget; a known lower bound can breach it.
    if active_value is None and check_limit(known, total, settings.active_budget) == "breached":
        active_status = "breached"
    comparisons = []
    if settings.baseline is not None:
        for category, value in totals.items():
            target = getattr(settings.baseline, category)
            current = weight(value, total)
            difference = (
                weight(value - target * total, total)
                if value is not None and target is not None and total is not None
                else None
            )
            comparisons.append(
                BaselineComparison(
                    category=category,
                    current_weight=current,
                    baseline_weight=money(target) if target is not None else None,
                    difference=difference,
                )
            )
    qualifications = []
    if cap is None:
        qualifications.append("Single-company cap is unknown.")
    if settings.active_budget is None:
        qualifications.append("Active budget is unknown.")
    if settings.baseline is None:
        qualifications.append("Baseline is unknown; no target allocation mix is inferred.")
    if overlap_unknown:
        qualifications.append(
            "Indirect exposure is unknown. The supplied cap policy is preserved; missing overlap is not zero. Supply a cap policy if unset, and look-through evidence when required."
        )
    return GuardrailReview(
        settings=settings,
        companies=companies,
        active=ActiveBudgetCheck(
            value=money(active_value) if active_value is not None else None,
            known_value=money(known),
            weight=weight(active_value, total),
            budget=money(settings.active_budget) if settings.active_budget is not None else None,
            status=active_status,
            contributions={
                key: money(value) if value is not None else None
                for key, value in contributions.items()
            },
            qualifications=notes,
        ),
        baseline_comparison=comparisons,
        qualifications=qualifications,
    )


def preview_changes(
    snapshot: Snapshot,
    evidence: FinancialEvidence,
    current: PortfolioReview,
    settings: PortfolioSettings | None,
    changes: ProposedChanges,
    source: Literal["user", "model"],
) -> ProposalReview:
    with localcontext() as context:
        context.prec = 60
        return _preview(snapshot, evidence, current, settings, changes, source)


def _preview(
    snapshot: Snapshot,
    evidence: FinancialEvidence,
    current: PortfolioReview,
    settings: PortfolioSettings | None,
    changes: ProposedChanges,
    source: Literal["user", "model"],
) -> ProposalReview:
    preview = snapshot.model_copy(deep=True)
    positions = {row.id: row for row in preview.positions}
    valued = {row.supplied.id: row for row in current.positions}

    def cash_row(position_id: str) -> Position:
        row = positions.get(position_id)
        if row is None or row.kind != "cash" or row.cash is None:
            raise ValueError("Proposed changes must reference a supplied cash balance.")
        return row

    try:
        for contribution in changes.new_cash:
            cash = cash_row(contribution.cash_position_id)
            assert cash.cash is not None
            cash.cash += contribution.amount
        for trade in changes.trades:
            position = positions.get(trade.position_id)
            cash = cash_row(trade.cash_position_id)
            if position is None or position.kind == "cash" or position.shares is None:
                raise ValueError("Proposed changes must reference a supplied security.")
            if (position.account_id, position.currency) != (cash.account_id, cash.currency):
                raise ValueError(
                    "Each proposed trade needs cash in the same account and quote currency; no account transfer or FX execution is inferred."
                )
            row = valued[position.id]
            if row.value is None or row.quote_used is None:
                return ProposalReview(
                    changes=changes,
                    source=source,
                    status="unknown",
                    qualifications=[
                        "Unusable mark, identity or FX prevents a funded proposed-change valuation."
                    ],
                )
            position.shares += trade.shares_change
            assert cash.cash is not None
            cash.cash -= trade.shares_change * row.quote_used.value
        if any(
            row.shares is not None and row.shares < 0 or row.cash is not None and row.cash < 0
            for row in preview.positions
        ):
            raise ValueError(
                "Proposed changes exceed supplied shares or available account cash, including explicitly supplied new cash."
            )
        # Revalidate mutated quantities and references before valuing the preview.
        preview = Snapshot.model_validate(preview.model_dump())
    except ValueError as error:
        return ProposalReview(
            changes=changes,
            source=source,
            status="blocked",
            qualifications=[
                str(error)
                if type(error) is ValueError
                else "Proposed balances exceed the supported input bounds."
            ],
        )
    post = review_portfolio(preview, evidence)
    apply_guardrails(post, settings)
    status: Literal["within_limits", "blocked", "unknown"] = "unknown"
    if post.guardrails is not None:
        checks = [row.status for row in post.guardrails.companies] + [post.guardrails.active.status]
        if "breached" in checks:
            status = "blocked"
        elif (
            post.complete
            and post.total_value != "0"
            and settings is not None
            and settings.single_company_cap is not None
            and settings.active_budget is not None
            and all(check == "within_limit" for check in checks)
        ):
            # Unknown ETF-only overlap still prevents clearing the company cap.
            if not has_etf_exposure(post) or settings.indirect_cap_policy == "direct_only":
                status = "within_limits"
    notes = [
        "Hypothetical preview only; no order or executed action. Uses the original dated marks and FX, including whole-portfolio cash and explicit new cash. Costs, taxes and execution prices are unknown. Passing supplied limits does not establish justified sizing."
    ]
    if status == "blocked":
        notes.append(
            "Configured caps and budgets cannot be waived by conviction. This proposed change is blocked by the deterministic checks."
        )
    elif status == "unknown":
        notes.append(
            "Missing valuation, classification, overlap policy/evidence or personal limits prevent clearing this proposed change."
        )
    return ProposalReview(
        changes=changes,
        source=source,
        status=status,
        post_total_value=post.total_value,
        post_cash_value=post.cash_value,
        positions=post.positions,
        guardrails=post.guardrails,
        qualifications=notes,
    )
