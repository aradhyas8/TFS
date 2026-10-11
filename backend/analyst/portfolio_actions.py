"""Shared portfolio action preparation service, deterministic differences, and guardrail warnings."""


from .calculations import review_portfolio
from .guardrails import apply_guardrails
from .persistence import Database, StaleBaseRevisionError
from .schemas import (
    CancelProposalResult,
    ConfirmationResult,
    DifferenceItem,
    FinancialEvidence,
    PreparationResult,
    ProposalOrigin,
    SavedPortfolio,
)


def compute_differences(
    before: SavedPortfolio | None,
    after: SavedPortfolio,
) -> list[DifferenceItem]:
    """Compute deterministic field differences covering accounts, positions, dates, currencies, FX, costs, unresolved holdings, rules, and benchmark."""
    differences: list[DifferenceItem] = []

    if before is None:
        # Initial creation
        differences.append(
            DifferenceItem(
                kind="date_set",
                path="snapshot.as_of",
                description=f"As of date set to {after.snapshot.as_of}",
                before=None,
                after=str(after.snapshot.as_of),
            )
        )
        differences.append(
            DifferenceItem(
                kind="currency_set",
                path="snapshot.reporting_currency",
                description=f"Reporting currency set to {after.snapshot.reporting_currency}",
                before=None,
                after=after.snapshot.reporting_currency,
            )
        )
        for acc in sorted(after.snapshot.accounts, key=lambda a: a.id):
            differences.append(
                DifferenceItem(
                    kind="account_added",
                    path=f"snapshot.accounts[{acc.id}]",
                    description=f"Account '{acc.name}' added",
                    before=None,
                    after=acc.model_dump(mode="python"),
                )
            )
        for pos in sorted(after.snapshot.positions, key=lambda p: p.id):
            differences.append(
                DifferenceItem(
                    kind="position_added",
                    path=f"snapshot.positions[{pos.id}]",
                    description=f"Added {pos.shares or pos.cash or ''} of {pos.ticker or pos.company_name or pos.id}".strip(),
                    before=None,
                    after=pos.model_dump(mode="python"),
                )
            )
        for unres in sorted(after.unresolved, key=lambda u: (u.account_id, u.ticker)):
            differences.append(
                DifferenceItem(
                    kind="unresolved_added",
                    path=f"unresolved[{unres.account_id}:{unres.ticker}]",
                    description=f"Added unresolved holding {unres.ticker} in account {unres.account_id}",
                    before=None,
                    after=unres.model_dump(mode="python"),
                )
            )
        for pid, cost in sorted(after.average_costs.items()):
            differences.append(
                DifferenceItem(
                    kind="cost_added",
                    path=f"average_costs[{pid}]",
                    description=f"Average cost set for {pid} to {cost}",
                    before=None,
                    after=str(cost),
                )
            )
        for fx in sorted(after.snapshot.fx, key=lambda f: (f.from_currency, f.to_currency)):
            differences.append(
                DifferenceItem(
                    kind="fx_added",
                    path=f"snapshot.fx[{fx.from_currency}->{fx.to_currency}]",
                    description=f"FX rate {fx.from_currency}->{fx.to_currency} added",
                    before=None,
                    after=fx.model_dump(mode="python"),
                )
            )
        if after.settings is not None:
            differences.append(
                DifferenceItem(
                    kind="settings_set",
                    path="settings",
                    description="Portfolio settings set",
                    before=None,
                    after=after.settings.model_dump(mode="python"),
                )
            )
        if after.benchmark is not None:
            differences.append(
                DifferenceItem(
                    kind="benchmark_set",
                    path="benchmark",
                    description=f"Benchmark set to {after.benchmark.ticker}",
                    before=None,
                    after=after.benchmark.model_dump(mode="python"),
                )
            )
        return differences

    # 1. Date
    if before.snapshot.as_of != after.snapshot.as_of:
        differences.append(
            DifferenceItem(
                kind="date_changed",
                path="snapshot.as_of",
                description=f"As of date changed from {before.snapshot.as_of} to {after.snapshot.as_of}",
                before=str(before.snapshot.as_of),
                after=str(after.snapshot.as_of),
            )
        )

    # 2. Reporting currency
    if before.snapshot.reporting_currency != after.snapshot.reporting_currency:
        differences.append(
            DifferenceItem(
                kind="currency_changed",
                path="snapshot.reporting_currency",
                description=f"Reporting currency changed from {before.snapshot.reporting_currency} to {after.snapshot.reporting_currency}",
                before=before.snapshot.reporting_currency,
                after=after.snapshot.reporting_currency,
            )
        )

    # 3. Accounts
    before_acc = {a.id: a for a in before.snapshot.accounts}
    after_acc = {a.id: a for a in after.snapshot.accounts}
    for aid in sorted(set(before_acc) | set(after_acc)):
        if aid not in before_acc:
            differences.append(
                DifferenceItem(
                    kind="account_added",
                    path=f"snapshot.accounts[{aid}]",
                    description=f"Account '{after_acc[aid].name}' added",
                    before=None,
                    after=after_acc[aid].model_dump(mode="python"),
                )
            )
        elif aid not in after_acc:
            differences.append(
                DifferenceItem(
                    kind="account_removed",
                    path=f"snapshot.accounts[{aid}]",
                    description=f"Account '{before_acc[aid].name}' removed",
                    before=before_acc[aid].model_dump(mode="python"),
                    after=None,
                )
            )
        elif before_acc[aid].name != after_acc[aid].name:
            differences.append(
                DifferenceItem(
                    kind="account_changed",
                    path=f"snapshot.accounts[{aid}].name",
                    description=f"Account '{aid}' renamed from '{before_acc[aid].name}' to '{after_acc[aid].name}'",
                    before=before_acc[aid].name,
                    after=after_acc[aid].name,
                )
            )

    # 4. Positions
    before_pos = {p.id: p for p in before.snapshot.positions}
    after_pos = {p.id: p for p in after.snapshot.positions}
    for pid in sorted(set(before_pos) | set(after_pos)):
        if pid not in before_pos:
            pos = after_pos[pid]
            differences.append(
                DifferenceItem(
                    kind="position_added",
                    path=f"snapshot.positions[{pid}]",
                    description=f"Added position {pos.ticker or pos.company_name or pid} ({pos.shares or pos.cash or ''})".strip(),
                    before=None,
                    after=pos.model_dump(mode="python"),
                )
            )
        elif pid not in after_pos:
            pos = before_pos[pid]
            differences.append(
                DifferenceItem(
                    kind="position_removed",
                    path=f"snapshot.positions[{pid}]",
                    description=f"Removed position {pos.ticker or pos.company_name or pid}",
                    before=pos.model_dump(mode="python"),
                    after=None,
                )
            )
        else:
            bp = before_pos[pid].model_dump(mode="python")
            ap = after_pos[pid].model_dump(mode="python")
            if bp != ap:
                changed = [k for k in sorted(bp.keys()) if bp[k] != ap[k]]
                differences.append(
                    DifferenceItem(
                        kind="position_changed",
                        path=f"snapshot.positions[{pid}]",
                        description=f"Position {pid} modified ({', '.join(changed)})",
                        before=bp,
                        after=ap,
                    )
                )

    # 5. Unresolved holdings
    before_un = {(u.account_id, u.ticker): u for u in before.unresolved}
    after_un = {(u.account_id, u.ticker): u for u in after.unresolved}
    for key in sorted(set(before_un) | set(after_un)):
        if key not in before_un:
            differences.append(
                DifferenceItem(
                    kind="unresolved_added",
                    path=f"unresolved[{key[0]}:{key[1]}]",
                    description=f"Added unresolved holding {key[1]} in account {key[0]}",
                    before=None,
                    after=after_un[key].model_dump(mode="python"),
                )
            )
        elif key not in after_un:
            differences.append(
                DifferenceItem(
                    kind="unresolved_removed",
                    path=f"unresolved[{key[0]}:{key[1]}]",
                    description=f"Resolved or removed holding {key[1]} in account {key[0]}",
                    before=before_un[key].model_dump(mode="python"),
                    after=None,
                )
            )
        else:
            bu = before_un[key].model_dump(mode="python")
            au = after_un[key].model_dump(mode="python")
            if bu != au:
                changed = [k for k in sorted(bu.keys()) if bu[k] != au[k]]
                differences.append(
                    DifferenceItem(
                        kind="unresolved_changed",
                        path=f"unresolved[{key[0]}:{key[1]}]",
                        description=f"Unresolved holding {key[1]} in account {key[0]} modified ({', '.join(changed)})",
                        before=bu,
                        after=au,
                    )
                )

    # 6. Average costs
    for pid in sorted(set(before.average_costs) | set(after.average_costs)):
        if pid not in before.average_costs:
            differences.append(
                DifferenceItem(
                    kind="cost_added",
                    path=f"average_costs[{pid}]",
                    description=f"Average cost added for {pid}: {after.average_costs[pid]}",
                    before=None,
                    after=str(after.average_costs[pid]),
                )
            )
        elif pid not in after.average_costs:
            differences.append(
                DifferenceItem(
                    kind="cost_removed",
                    path=f"average_costs[{pid}]",
                    description=f"Average cost removed for {pid}",
                    before=str(before.average_costs[pid]),
                    after=None,
                )
            )
        elif before.average_costs[pid] != after.average_costs[pid]:
            differences.append(
                DifferenceItem(
                    kind="cost_changed",
                    path=f"average_costs[{pid}]",
                    description=f"Average cost changed for {pid} from {before.average_costs[pid]} to {after.average_costs[pid]}",
                    before=str(before.average_costs[pid]),
                    after=str(after.average_costs[pid]),
                )
            )

    # 7. FX
    before_fx = {(f.from_currency, f.to_currency): f for f in before.snapshot.fx}
    after_fx = {(f.from_currency, f.to_currency): f for f in after.snapshot.fx}
    for pair in sorted(set(before_fx) | set(after_fx)):
        if pair not in before_fx:
            differences.append(
                DifferenceItem(
                    kind="fx_added",
                    path=f"snapshot.fx[{pair[0]}->{pair[1]}]",
                    description=f"FX rate {pair[0]}->{pair[1]} added",
                    before=None,
                    after=after_fx[pair].model_dump(mode="python"),
                )
            )
        elif pair not in after_fx:
            differences.append(
                DifferenceItem(
                    kind="fx_removed",
                    path=f"snapshot.fx[{pair[0]}->{pair[1]}]",
                    description=f"FX rate {pair[0]}->{pair[1]} removed",
                    before=before_fx[pair].model_dump(mode="python"),
                    after=None,
                )
            )
        else:
            bf = before_fx[pair].model_dump(mode="python")
            af = after_fx[pair].model_dump(mode="python")
            if bf != af:
                differences.append(
                    DifferenceItem(
                        kind="fx_changed",
                        path=f"snapshot.fx[{pair[0]}->{pair[1]}]",
                        description=f"FX rate {pair[0]}->{pair[1]} modified",
                        before=bf,
                        after=af,
                    )
                )

    # 8. Settings / Rules
    bs = before.settings.model_dump(mode="python") if before.settings else None
    as_ = after.settings.model_dump(mode="python") if after.settings else None
    if bs != as_:
        differences.append(
            DifferenceItem(
                kind="settings_changed",
                path="settings",
                description="Portfolio settings modified",
                before=bs,
                after=as_,
            )
        )

    # 9. Benchmark
    bb = before.benchmark.model_dump(mode="python") if before.benchmark else None
    ab = after.benchmark.model_dump(mode="python") if after.benchmark else None
    if bb != ab:
        differences.append(
            DifferenceItem(
                kind="benchmark_changed",
                path="benchmark",
                description="Benchmark configuration modified",
                before=bb,
                after=ab,
            )
        )

    return differences


def compute_warnings(
    candidate: SavedPortfolio,
    evidence: FinancialEvidence | None = None,
) -> list[str]:
    """Extract guardrail warnings, concentration qualifications, and unresolved holding alerts."""
    warnings: list[str] = []

    if candidate.unresolved:
        count = len(candidate.unresolved)
        tickers = ", ".join(sorted({u.ticker for u in candidate.unresolved}))
        warnings.append(
            f"{count} unresolved holding(s) require identification ({tickers})."
        )

    ev = evidence or FinancialEvidence()
    try:
        review = review_portfolio(candidate.snapshot, ev)
        apply_guardrails(review, candidate.settings)

        if review.guardrails is not None:
            for c in review.guardrails.companies:
                if c.status == "breached":
                    warnings.append(
                        f"Company cap breached for {c.company_name or c.company_id}: weight {c.current_weight} exceeds cap of {c.cap}."
                    )
                elif c.status == "unknown" and candidate.settings and candidate.settings.single_company_cap is not None:
                    warnings.append(
                        f"Company cap qualification for {c.company_name or c.company_id}: {c.explanation}"
                    )

            if review.guardrails.active.status == "breached":
                warnings.append(
                    f"Active budget breached: weight {review.guardrails.active.weight} exceeds budget of {review.guardrails.active.budget}."
                )
            elif review.guardrails.active.status == "unknown" and candidate.settings and candidate.settings.active_budget is not None:
                for note in review.guardrails.active.qualifications:
                    warnings.append(f"Active budget qualification: {note}")

        for q in review.qualifications:
            if not q.startswith("Only explicit personal settings are applied"):
                warnings.append(q)
    except Exception:
        # Factual recording must not fail even if valuation fails
        pass

    return warnings


def prepare_portfolio_action(
    database: Database,
    candidate: SavedPortfolio,
    base_revision: int | None,
    origin: ProposalOrigin,
    thread_id: str | None = None,
    run_id: str | None = None,
    evidence: FinancialEvidence | None = None,
) -> PreparationResult:
    """Prepare a portfolio proposal without committing it to the database."""
    current_rev_record = database.get_current_portfolio()
    current_revision = current_rev_record.revision if current_rev_record else None
    before = current_rev_record.portfolio if current_rev_record else None

    if base_revision != current_revision:
        raise StaleBaseRevisionError(
            f"Base revision {base_revision} is stale; current revision is {current_revision}."
        )

    differences = compute_differences(before, candidate)
    if not differences:
        return PreparationResult(status="unchanged", proposal=None)

    warnings = compute_warnings(candidate, evidence)

    proposal = database.store_proposal(
        base_revision=base_revision,
        origin=origin,
        before_portfolio=before,
        after_portfolio=candidate,
        differences=differences,
        warnings=warnings,
        thread_id=thread_id,
        run_id=run_id,
    )
    return PreparationResult(status="prepared", proposal=proposal)


def confirm_portfolio_action(
    database: Database,
    proposal_id: str,
) -> ConfirmationResult:
    """Explicitly apply a prepared portfolio proposal and return the committed portfolio and receipt."""
    rev_portfolio, receipt = database.confirm_proposal(proposal_id)
    return ConfirmationResult(portfolio=rev_portfolio, receipt=receipt)


def cancel_portfolio_action(
    database: Database,
    proposal_id: str,
) -> CancelProposalResult:
    """Explicitly cancel a prepared portfolio proposal and return the resulting status."""
    status = database.cancel_proposal(proposal_id)
    return CancelProposalResult(status=status)

