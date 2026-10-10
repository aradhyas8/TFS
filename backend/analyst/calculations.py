from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Literal

import numpy as np
import pandas as pd

from .financial_data import NOT_CACHED, RECENT_DAYS
from .schemas import (
    AccountResult,
    CompanyExposure,
    CompanyOverlap,
    CurrencyExposure,
    FinancialEvidence,
    FundOverlapContribution,
    HoldingConstituent,
    HoldingsCoverage,
    PortfolioReview,
    Position,
    PositionResult,
    Snapshot,
    SponsorHoldings,
)


def money(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def weight(value: Decimal | None, total: Decimal | None) -> str | None:
    if value is None or total is None or total == 0:
        return None
    return str((value / total).quantize(Decimal("0.00000001")))


def recent(source: date, snapshot: date, status: str) -> bool:
    """Same-day for manual inputs; a provider value may be the latest published within RECENT_DAYS."""
    return status != "stale" and (source == snapshot or status != "manual" and snapshot - timedelta(days=RECENT_DAYS) <= source < snapshot)


# Missing-value causes worded once for all holdings they affect.
CAUSES = {
    "Supplied mark is missing.": "could not be priced",
    "Price source failed; explicit broker-display fallback used if supplied.": "could not be priced",
    NOT_CACHED: "could not be priced from the cache; use Refresh prices",
    "Security/listing or direct-company identity is unresolved.": "could not be identified with confidence",
    "No usable dated FX rate; the converted value is unknown.": "could not be converted: no usable FX rate",
    "Supplied mark date differs from the snapshot; valuation is unknown.": "could not be valued: price is not from the snapshot date",
}


def summarize(rows: list[PositionResult], evidence_issues: list[str]) -> list[str]:
    """One line per cause, naming the holdings, instead of one line per holding."""
    names = {row.supplied.id: row.supplied.ticker or row.supplied.id for row in rows}
    causes: dict[str, list[str]] = {}
    general: list[str] = []
    for row in rows:
        for issue in row.issues:
            causes.setdefault(CAUSES.get(issue, issue), []).append(names[row.supplied.id])
    for item in evidence_issues:
        key, separator, issue = item.partition(": ")
        if separator and key in names:
            causes.setdefault(CAUSES.get(issue, issue), []).append(names[key])
        elif item not in general:
            general.append(item)
    # A holding with a specific pricing cause is not listed again under the generic one.
    specific = {name for cause, held in causes.items() if cause != "could not be priced" and cause.startswith("could not be priced") for name in held}
    if "could not be priced" in causes:
        causes["could not be priced"] = [name for name in causes["could not be priced"] if name not in specific]
    lines = []
    for issue, held in causes.items():
        if not held:
            continue
        held = list(dict.fromkeys(held))
        count = f"{len(held)} holding{'s' if len(held) != 1 else ''}"
        lines.append(f"{count} {issue} ({', '.join(held)})." if issue in CAUSES.values() else f"{count} ({', '.join(held)}): {issue}")
    return general + lines


def sum_values(values: list[Decimal | None]) -> tuple[Decimal, Decimal | None]:
    known = sum((value for value in values if value is not None), Decimal(0))
    complete = bool(np.all([value is not None for value in values]))
    return known, known if complete else None


def _get_holdings_for_etf(pos: Position, evidence: FinancialEvidence) -> SponsorHoldings | None:
    if pos.id in evidence.sponsor_holdings and evidence.sponsor_holdings[pos.id] is not None:
        return evidence.sponsor_holdings[pos.id]
    if pos.ticker and pos.listing:
        key = f"{pos.ticker}:{pos.listing}"
        if key in evidence.sponsor_holdings and evidence.sponsor_holdings[key] is not None:
            return evidence.sponsor_holdings[key]
    if pos.ticker and pos.ticker in evidence.sponsor_holdings and evidence.sponsor_holdings[pos.ticker] is not None:
        return evidence.sponsor_holdings[pos.ticker]
    return None


def _resolve_constituents(
    holdings: SponsorHoldings,
    parent_weight: Decimal,
    evidence: FinancialEvidence,
    snapshot_as_of: date,
    visited: set[str],
) -> tuple[list[tuple[HoldingConstituent, Decimal, date, str, HoldingsCoverage]], HoldingsCoverage]:
    items: list[tuple[HoldingConstituent, Decimal, date, str, HoldingsCoverage]] = []
    fund_coverage: HoldingsCoverage = holdings.coverage
    if holdings.as_of != snapshot_as_of or holdings.coverage == "stale" or holdings.status == "stale":
        fund_coverage = "stale"
    for c in holdings.holdings:
        effective_w = parent_weight * c.weight
        if c.kind == "etf":
            sub_key = c.ticker or c.company_id or ""
            sub_holdings = None
            if sub_key:
                sub_holdings = evidence.sponsor_holdings.get(sub_key)
                if sub_holdings is None and c.ticker and c.listing:
                    sub_holdings = evidence.sponsor_holdings.get(f"{c.ticker}:{c.listing}")
            if sub_holdings is not None and sub_key not in visited:
                visited_next = set(visited)
                visited_next.add(sub_key)
                sub_items, sub_cov = _resolve_constituents(sub_holdings, effective_w, evidence, snapshot_as_of, visited_next)
                items.extend(sub_items)
                if sub_cov in {"partial", "unknown", "stale"}:
                    fund_coverage = "partial" if fund_coverage != "stale" else "stale"
            else:
                fund_coverage = "partial" if fund_coverage != "stale" else "stale"
        else:
            items.append((c, effective_w, holdings.as_of, holdings.source, holdings.coverage))
    return items, fund_coverage


def review_portfolio(snapshot: Snapshot, evidence: FinancialEvidence, holdings_as_of: date | None = None) -> PortfolioReview:
    # Decimal keeps supplied financial precision; pandas groups across all accounts;
    # NumPy checks completeness without turning missing values into financial zeroes.
    with localcontext() as context:
        context.prec = 60
        review = _review(snapshot, evidence)
    review.holdings_as_of = holdings_as_of or snapshot.as_of
    return review


def _review(snapshot: Snapshot, evidence: FinancialEvidence) -> PortfolioReview:
    reviewed_at = datetime.now(UTC)
    rows: list[PositionResult] = []
    values: list[Decimal | None] = []
    for position in snapshot.positions:
        issues: list[str] = []
        local: Decimal | None = None
        identity = evidence.identities.get(position.id)
        quote = evidence.quotes.get(position.id)
        resolved = bool(identity and identity.ticker and identity.listing and identity.currency)
        if position.kind == "stock":
            resolved = resolved and bool(identity and identity.company_id and identity.company_name)
        if position.kind == "cash":
            local = position.cash
        else:
            resolved = resolved and identity is not None and identity.status in {"verified", "supplied"}
            if identity and identity.status == "supplied":
                issues.append("Identity is not verified by a primary source; valuation is provisional.")
            if not resolved:
                issues.append("Security/listing or direct-company identity is unresolved.")
            if quote is None:
                issues.append("Supplied mark is missing.")
            elif not recent(quote.as_of, snapshot.as_of, quote.status):
                issues.append("Supplied mark date differs from the snapshot; valuation is unknown.")
            elif quote.status == "stale" or quote.basis != "unadjusted":
                issues.append("Stale or adjusted quote is unusable for snapshot valuation.")
            elif not identity or (quote.ticker, quote.listing, quote.currency) != (identity.ticker, identity.listing, identity.currency):
                issues.append("Quote identity or currency contradicts the resolved listing.")
            elif resolved and position.shares is not None:
                local = position.shares * quote.value
            if quote:
                issues.append(f"Quote is {quote.status}; indicative valuation only, never an execution quote.")
                if quote.captured_at is None:
                    issues.append("Quote capture time is unknown; it was not inferred from submission time.")
                if quote.as_of < snapshot.as_of:
                    issues.append("Last available price predates the snapshot date (market closed or not yet traded).")
        if position.kind != "cash" and position.shares == 0:
            # An unheld candidate has zero current exposure even if its purchase
            # evidence is unusable; source_inputs_usable still gates any sizing.
            local = Decimal(0)
        fx_used = None
        rate: Decimal | None = Decimal(1)
        if position.currency != snapshot.reporting_currency:
            fx_used = next(
                (
                    fx
                    for fx in evidence.fx
                    if fx.from_currency == position.currency
                    and fx.to_currency == snapshot.reporting_currency
                ),
                None,
            )
            if fx_used is None or not recent(fx_used.as_of, snapshot.as_of, fx_used.status):
                rate = None
                issues.append("No usable dated FX rate; the converted value is unknown.")
            else:
                rate = fx_used.rate
                issues.append(f"FX is {fx_used.status}; indicative, not an execution quote.")
        value = Decimal(0) if position.kind != "cash" and position.shares == 0 else local * rate if local is not None and rate is not None else None
        verified_security = position.kind == "cash" or bool(
            identity and identity.status == "verified" and quote
            and quote.status in {"indicative", "delayed"} and quote.captured_at
            and quote.as_of == snapshot.as_of  # an earlier day's price values the portfolio but never sizes
        )
        usable_fx = position.currency == snapshot.reporting_currency or bool(rate is not None and fx_used and fx_used.status == "indicative" and fx_used.captured_at and fx_used.as_of == snapshot.as_of)
        values.append(value)
        rows.append(
            PositionResult(
                supplied=position,
                value=money(value) if value is not None else None,
                local_value=money(local) if local is not None else None,
                weight=None,
                identity_status=(
                    "not_applicable"
                    if position.kind == "cash"
                    else "verified" if resolved and identity and identity.status == "verified"
                    else "supplied"
                    if resolved
                    else "unresolved"
                ),
                identity=identity,
                quote_used=quote,
                quote_age_days=(snapshot.as_of - quote.as_of).days if quote else None,
                quote_age_at_capture_days=(quote.captured_at.date() - quote.as_of).days if quote and quote.captured_at else None,
                quote_age_at_request_days=(reviewed_at.date() - quote.as_of).days if quote else None,
                fx_age_days=(snapshot.as_of - fx_used.as_of).days if fx_used else None,
                source_inputs_usable=value is not None and verified_security and usable_fx,
                fx_used=fx_used,
                issues=issues,
            )
        )
    known, total = sum_values(values)
    for row, value in zip(rows, values, strict=True):
        row.weight = weight(value, total)
    accounts: list[AccountResult] = []
    for account in snapshot.accounts:
        account_values = [
            value
            for value, row in zip(values, rows, strict=True)
            if row.supplied.account_id == account.id
        ]
        account_known, account_total = sum_values(account_values)
        accounts.append(
            AccountResult(
                id=account.id,
                name=account.name,
                known_value=money(account_known),
                total_value=money(account_total) if account_total is not None else None,
            )
        )
    companies: list[CompanyExposure] = []
    stocks = []
    for index, row in enumerate(rows):
        # A resolved provider identity names the issuer even when only market-data search (not a primary source) supplied it.
        issuer = row.identity if row.identity and row.identity.company_id and row.identity_status != "unresolved" else row.supplied
        if row.supplied.kind == "stock" and issuer.company_id:
            stocks.append({"company_id": issuer.company_id, "company_name": issuer.company_name,
                           "index": index})
    if stocks:
        table = pd.DataFrame(stocks)
        for company_id, group in table.groupby("company_id", sort=False):
            indices = group["index"].tolist()
            company_known, company_total = sum_values([values[index] for index in indices])
            companies.append(
                CompanyExposure(
                    company_id=str(company_id),
                    company_name=str(group["company_name"].iloc[0] or "Unresolved company name"),
                    value=money(company_total) if company_total is not None else None,
                    known_value=money(company_known),
                    weight=weight(company_total, total),
                    position_ids=[rows[index].supplied.id for index in indices],
                )
            )

    etf_rows = [
        (index, row, values[index])
        for index, row in enumerate(rows)
        if row.supplied.kind == "etf" and (row.supplied.shares is None or row.supplied.shares != 0)
    ]
    etf_coverages: list[HoldingsCoverage] = []
    contributions_by_company: dict[str, list[FundOverlapContribution]] = {}
    names_by_company: dict[str, str] = {}
    dates_by_company: dict[str, set[date]] = {}

    for index, row, fund_val in etf_rows:
        h = _get_holdings_for_etf(row.supplied, evidence)
        if h is None:
            etf_coverages.append("unknown")
            continue
        if h.as_of != snapshot.as_of or h.coverage == "stale" or h.status == "stale":
            etf_coverages.append("stale")
            continue
        resolved_items, fund_cov = _resolve_constituents(
            h, Decimal(1), evidence, snapshot.as_of, {row.supplied.ticker or row.supplied.id}
        )
        etf_coverages.append(fund_cov)
        if fund_val is not None and fund_val > 0:
            for constituent, weight_in_fund, as_of_date, source_str, item_cov in resolved_items:
                comp_id = constituent.company_id or constituent.ticker
                if not comp_id:
                    continue
                comp_name = constituent.company_name or constituent.ticker or "Unresolved company name"
                names_by_company[comp_id] = comp_name
                dates_by_company.setdefault(comp_id, set()).add(as_of_date)
                ind_val = (fund_val * weight_in_fund).quantize(Decimal("0.0000000001"))
                contrib = FundOverlapContribution(
                    position_id=row.supplied.id,
                    ticker=row.supplied.ticker,
                    listing=row.supplied.listing,
                    fund_name=row.supplied.ticker,
                    fund_weight=weight(fund_val, total),
                    weight_in_fund=money(weight_in_fund.quantize(Decimal("0.00000001")) if isinstance(weight_in_fund, Decimal) else Decimal(str(weight_in_fund))),
                    indirect_value=money(ind_val),
                    indirect_weight=weight(ind_val, total),
                    as_of=as_of_date,
                    source=source_str,
                    coverage=item_cov,
                )
                contributions_by_company.setdefault(comp_id, []).append(contrib)

    portfolio_indirect_exposure: Literal["none", "full", "partial", "unknown", "stale"]
    if not etf_rows:
        portfolio_indirect_exposure = "none"
    elif not etf_coverages:
        portfolio_indirect_exposure = "none"
    elif all(c == "full" for c in etf_coverages):
        portfolio_indirect_exposure = "full"
    elif all(c == "stale" for c in etf_coverages):
        portfolio_indirect_exposure = "stale"
    elif all(c == "unknown" for c in etf_coverages):
        portfolio_indirect_exposure = "unknown"
    else:
        portfolio_indirect_exposure = "partial"

    direct_by_company: dict[str, tuple[str, Decimal | None, list[str]]] = {}
    for comp in companies:
        direct_by_company[comp.company_id] = (
            comp.company_name,
            Decimal(comp.value) if comp.value is not None else None,
            comp.position_ids,
        )

    all_overlap_company_ids = set(contributions_by_company.keys())
    company_overlap: list[CompanyOverlap] = []

    for comp_id in all_overlap_company_ids:
        direct_info = direct_by_company.get(comp_id)
        direct_val = direct_info[1] if direct_info else None
        comp_name = direct_info[0] if direct_info else names_by_company.get(comp_id, "Unresolved company name")
        contribs = contributions_by_company.get(comp_id, [])
        indirect_sum = sum((Decimal(c.indirect_value) for c in contribs if c.indirect_value is not None), Decimal(0))
        total_val = ((direct_val or Decimal(0)) + indirect_sum) if (direct_val is not None or indirect_sum > 0) else None

        comp_cov: HoldingsCoverage
        if any(c.coverage == "partial" for c in contribs) or portfolio_indirect_exposure == "partial":
            comp_cov = "partial"
        elif any(c.coverage == "stale" for c in contribs) or portfolio_indirect_exposure == "stale":
            comp_cov = "stale"
        elif all(c.coverage == "full" for c in contribs) and portfolio_indirect_exposure == "full":
            comp_cov = "full"
        else:
            comp_cov = "unknown"

        source_dates = sorted(list(dates_by_company.get(comp_id, set())))

        company_overlap.append(
            CompanyOverlap(
                company_id=comp_id,
                company_name=comp_name,
                direct_value=money(direct_val) if direct_val is not None else "0",
                direct_weight=weight(direct_val, total) if direct_val is not None else "0.00000000",
                indirect_value=money(indirect_sum),
                indirect_weight=weight(indirect_sum, total),
                total_value=money(total_val) if total_val is not None else None,
                total_weight=weight(total_val, total),
                coverage=comp_cov,
                source_dates=source_dates,
                contributing_funds=contribs,
            )
        )

    company_overlap.sort(
        key=lambda item: (
            -(Decimal(item.total_value) if item.total_value is not None else Decimal("-1")),
            item.company_id,
        )
    )

    _, holdings = sum_values(
        [value for value, row in zip(values, rows, strict=True) if row.supplied.kind != "cash"]
    )
    _, cash = sum_values(
        [value for value, row in zip(values, rows, strict=True) if row.supplied.kind == "cash"]
    )

    if portfolio_indirect_exposure == "full":
        overlap_note = "ETF company overlap is calculated from dated sponsor holdings; look-through coverage is full."
    elif portfolio_indirect_exposure == "partial":
        overlap_note = "ETF look-through is partial; known indirect overlap is included, but unreported fund holdings could contain additional exposure."
    elif portfolio_indirect_exposure == "stale":
        overlap_note = "ETF sponsor holdings dates differ from the snapshot date; indirect exposure is stale."
    elif portfolio_indirect_exposure == "none":
        overlap_note = "No ETF positions in portfolio; indirect exposure is not applicable."
    else:
        overlap_note = "Indirect ETF exposure, current evidence, tax effects and transaction costs are unknown."

    currency_exposure = []
    for code in dict.fromkeys(row.supplied.currency for row in rows):
        currency_known, currency_total = sum_values([value for value, row in zip(values, rows, strict=True) if row.supplied.currency == code])
        currency_exposure.append(CurrencyExposure(currency=code, value=money(currency_total) if currency_total is not None else None,
                                                  known_value=money(currency_known), weight=weight(currency_total, total)))

    qualifications = [
        f"Manual marks must be dated on the snapshot date; provider prices and FX may be the latest published within {RECENT_DAYS} days before it and keep their own dates. Future inputs are unusable.",
        "Valuation, weights and exposure need no personal rules. Only rule checks (company cap, active budget, baseline) and any allocation amount depend on them.",
        overlap_note,
    ]
    qualifications.extend(summarize(rows, evidence.issues))
    if total is None:
        qualifications.append(
            "Incomplete valuation: known subtotal is not a portfolio total; all portfolio weights are unknown."
        )
    elif total == 0:
        qualifications.append("The supplied portfolio total is zero; weights are undefined.")
    return PortfolioReview(
        as_of=snapshot.as_of,
        reviewed_at=reviewed_at,
        reporting_currency=snapshot.reporting_currency,
        positions=rows,
        accounts=accounts,
        direct_companies=companies,
        currency_exposure=currency_exposure,
        company_overlap=company_overlap,
        total_value=money(total) if total is not None else None,
        known_value=money(known),
        holdings_value=money(holdings) if holdings is not None else None,
        cash_value=money(cash) if cash is not None else None,
        complete=total is not None,
        source_inputs_usable=total is not None and all(row.source_inputs_usable for row in rows),
        indirect_exposure=portfolio_indirect_exposure,
        qualifications=qualifications,
        calculation_basis="Snapshot-date shares multiplied by an unadjusted quote on the same date; cash at supplied balance; local value multiplied by dated directed FX. Shares must already reflect splits as of the snapshot date; no split factor is applied again. Adjusted prices are rejected. No dividends are added to prices or separately credited to cash; cash is the supplied balance. No historical or total returns are inferred, so adjusted-price returns and dividends cannot double count. Taxes and costs are unknown. Weights use the complete whole-portfolio total.",
    )
