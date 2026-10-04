from decimal import Decimal, localcontext

import numpy as np
import pandas as pd

from .schemas import AccountResult, CompanyExposure, PortfolioReview, PositionResult, Snapshot


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


def review_portfolio(snapshot: Snapshot) -> PortfolioReview:
    # Decimal keeps supplied financial precision; pandas groups across all accounts;
    # NumPy checks completeness without turning missing values into financial zeroes.
    with localcontext() as context:
        context.prec = 60
        return _review(snapshot)


def _review(snapshot: Snapshot) -> PortfolioReview:
    rows: list[PositionResult] = []
    values: list[Decimal | None] = []
    for position in snapshot.positions:
        issues: list[str] = []
        local: Decimal | None = None
        resolved = bool(position.ticker and position.listing)
        if position.kind == "stock":
            resolved = resolved and bool(position.company_id and position.company_name)
        if position.kind == "cash":
            local = position.cash
        else:
            if not resolved:
                issues.append("Security/listing or direct-company identity is unresolved.")
            if position.mark is None:
                issues.append("Supplied mark is missing.")
            elif position.mark.as_of != snapshot.as_of:
                issues.append("Supplied mark date differs from the snapshot; valuation is unknown.")
            elif resolved and position.shares is not None:
                local = position.shares * position.mark.value
        fx_used = None
        rate: Decimal | None = Decimal(1)
        if position.currency != snapshot.reporting_currency:
            fx_used = next(
                (
                    fx
                    for fx in snapshot.fx
                    if fx.from_currency == position.currency
                    and fx.to_currency == snapshot.reporting_currency
                ),
                None,
            )
            if fx_used is None or fx_used.as_of != snapshot.as_of:
                rate = None
                issues.append("A supplied FX rate on the snapshot date is required.")
            else:
                rate = fx_used.rate
        value = local * rate if local is not None and rate is not None else None
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
                    else "supplied"
                    if resolved
                    else "unresolved"
                ),
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
    stocks = [
        {"company_id": row.supplied.company_id, "index": index}
        for index, row in enumerate(rows)
        if row.supplied.kind == "stock" and row.supplied.company_id
    ]
    if stocks:
        table = pd.DataFrame(stocks)
        for company_id, group in table.groupby("company_id", sort=False):
            indices = group["index"].tolist()
            company_known, company_total = sum_values([values[index] for index in indices])
            companies.append(
                CompanyExposure(
                    company_id=str(company_id),
                    company_name=rows[indices[0]].supplied.company_name
                    or "Unresolved company name",
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
        "Marks, identities and FX are supplied by the user; they have not been independently verified.",
        "Baseline, company cap, active budget and personal risk context are unknown; no allocation amount is justified.",
        "Indirect ETF exposure, current evidence, tax effects and transaction costs are unknown.",
    ]
    if total is None:
        qualifications.append(
            "Incomplete valuation: known subtotal is not a portfolio total; all portfolio weights are unknown."
        )
    elif total == 0:
        qualifications.append("The supplied portfolio total is zero; weights are undefined.")
    return PortfolioReview(
        as_of=snapshot.as_of,
        reporting_currency=snapshot.reporting_currency,
        positions=rows,
        accounts=accounts,
        direct_companies=companies,
        total_value=money(total) if total is not None else None,
        known_value=money(known),
        holdings_value=money(holdings) if holdings is not None else None,
        cash_value=money(cash) if cash is not None else None,
        complete=total is not None,
        qualifications=qualifications,
        calculation_basis="Shares × supplied unadjusted mark; cash at supplied balance; multiply local value by supplied directed FX. No dividends, splits, taxes or costs are added. Weights use the complete whole-portfolio total.",
    )
