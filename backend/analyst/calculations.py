from datetime import UTC, datetime
from decimal import Decimal, localcontext

import numpy as np
import pandas as pd

from .schemas import (
    AccountResult,
    CompanyExposure,
    FinancialEvidence,
    PortfolioReview,
    PositionResult,
    Snapshot,
)


def money(value: Decimal) -> str:
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def weight(value: Decimal | None, total: Decimal | None) -> str | None:
    if value is None or total is None or total == 0:
        return None
    return str((value / total).quantize(Decimal("0.00000001")))


def sum_values(values: list[Decimal | None]) -> tuple[Decimal, Decimal | None]:
    known = sum((value for value in values if value is not None), Decimal(0))
    complete = bool(np.all([value is not None for value in values]))
    return known, known if complete else None


def review_portfolio(snapshot: Snapshot, evidence: FinancialEvidence) -> PortfolioReview:
    # Decimal keeps supplied financial precision; pandas groups across all accounts;
    # NumPy checks completeness without turning missing values into financial zeroes.
    with localcontext() as context:
        context.prec = 60
        return _review(snapshot, evidence)


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
                issues.append("Identity is user supplied, not independently verified; valuation is provisional.")
            if not resolved:
                issues.append("Security/listing or direct-company identity is unresolved.")
            if quote is None:
                issues.append("Supplied mark is missing.")
            elif quote.as_of != snapshot.as_of:
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
            if fx_used is None or fx_used.as_of != snapshot.as_of or fx_used.status == "stale":
                rate = None
                issues.append("A supplied FX rate on the snapshot date is required.")
            else:
                rate = fx_used.rate
                issues.append(f"FX is {fx_used.status}; indicative, not an execution quote.")
        value = local * rate if local is not None and rate is not None else None
        verified_security = position.kind == "cash" or bool(
            identity and identity.status == "verified" and quote
            and quote.status in {"indicative", "delayed"} and quote.captured_at
        )
        usable_fx = fx_used is None or bool(fx_used.status == "indicative" and fx_used.captured_at)
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
        issuer = row.identity if row.identity_status == "verified" and row.identity else row.supplied
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
    _, holdings = sum_values(
        [value for value, row in zip(values, rows, strict=True) if row.supplied.kind != "cash"]
    )
    _, cash = sum_values(
        [value for value, row in zip(values, rows, strict=True) if row.supplied.kind == "cash"]
    )
    qualifications = [
        "Source dates are compared with the requested snapshot date, without an invented freshness threshold. Older or future inputs remain unusable; historical snapshots are not current prices.",
        "Baseline, company cap, active budget and personal risk context are unknown; no allocation amount is justified.",
        "Indirect ETF exposure, current evidence, tax effects and transaction costs are unknown.",
    ]
    qualifications.extend(evidence.issues)
    qualifications.extend(f"{row.supplied.id}: {issue}" for row in rows for issue in row.issues)
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
        total_value=money(total) if total is not None else None,
        known_value=money(known),
        holdings_value=money(holdings) if holdings is not None else None,
        cash_value=money(cash) if cash is not None else None,
        complete=total is not None,
        source_inputs_usable=total is not None and all(row.source_inputs_usable for row in rows),
        qualifications=qualifications,
        calculation_basis="Snapshot-date shares multiplied by an unadjusted quote on the same date; cash at supplied balance; local value multiplied by dated directed FX. Shares must already reflect splits as of the snapshot date; no split factor is applied again. Adjusted prices are rejected. No dividends are added to prices or separately credited to cash; cash is the supplied balance. No historical or total returns are inferred, so adjusted-price returns and dividends cannot double count. Taxes and costs are unknown. Weights use the complete whole-portfolio total.",
    )
