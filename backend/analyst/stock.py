"""Conditional company arithmetic; reviewed facts never come from the model."""
from datetime import date
from decimal import Decimal, localcontext

from .calculations import money
from .schemas import (
    CalculatedCompanyCase,
    CaseYear,
    CompanyJudgments,
    CompanyResearch,
    PortfolioReview,
    PositionResult,
    ResearchFact,
    StockResult,
    StockValuation,
    is_canadian_security,
)

BASIS = (
    "Five annual operating periods. Revenue grows by each judged growth rate; net earnings "
    "equal revenue times net margin. FCF additionally applies cash conversion and deducts "
    "explicit reinvestment. Book/FFO methods grow the reported total metric directly. "
    "Diluted shares compound each dilution assumption. Exit price equals the terminal "
    "metric times the exit multiple divided by terminal diluted shares. Distributions "
    "use each year's per-share earnings/FCF/FFO times payout; book-value cases use "
    "opening book value times explicit ROE to estimate earnings for payout. Distributions "
    "are held as idle reporting cash; "
    "Negative metrics imply zero equity exit value in this multiple method and no payout; an alternative positive recovery value needs a different supported case. They are not reinvested. Initial dated FX times each judged FX factor converts "
    "distributions and terminal price once. Present value discounts per-share distributions "
    "and terminal price in local currency. Reverse valuation solves the year-5 exit multiple "
    "at which the discounted exit price plus discounted payouts equals today's unadjusted quote "
    "under the named operating assumptions; it is not a unique market belief. Costs and personal tax "
    "consequences remain unknown. Values are nominal conditional cases, without probabilities, "
    "weighted expected values, modeled inflation or a mandatory exit date."
)


def calculate_company_cases(position_id: str, research: CompanyResearch,
                            judgment: CompanyJudgments, portfolio: PortfolioReview) -> StockResult:
    row = next(row for row in portfolio.positions if row.supplied.id == position_id)
    issues = list(research.issues)
    documents = {doc.id: doc for doc in research.documents}
    facts = {fact.id: fact for fact in research.facts}

    def fact(key: str | None, metric: str) -> ResearchFact | None:
        candidate = facts.get(key or "")
        if candidate is None:
            issues.append(f"{metric}: reported fact unavailable; not assumed zero.")
            return None
        expected_unit = "shares" if metric == "shares" else "currency"
        refs = [documents[ref] for ref in candidate.document_ids]
        is_canadian = is_canadian_security(row.supplied.currency, row.supplied.listing) or any(doc.authority in {"sedar", "sedar_plus"} for doc in research.documents)
        required_filing = {"sedar", "sedar_plus"} if is_canadian else {"sec"}
        if (candidate.metric != metric or candidate.unit != expected_unit
                or candidate.currency != (None if metric == "shares" else row.supplied.currency)
                or candidate.value is None or candidate.period_end > portfolio.as_of
                or candidate.period_start is not None and candidate.period_start > candidate.period_end
                or metric in {"revenue", "ffo"} and (candidate.period_start is None or not 350 <= (candidate.period_end - candidate.period_start).days <= 380)
                # Automated SEC XBRL facts pass on their automated checks; footnotes are not read, which is disclosed below.
                or not all((candidate.filing_checked, candidate.review in {"sec_xbrl", "issuer_report"} or candidate.notes_checked,
                            candidate.custom_tags_checked, candidate.segments_checked))
                # The issuer's own published report is the primary source for issuer_report facts; SEDAR+ is optional there.
                or not any(doc.available and (doc.authority in required_filing or candidate.review == "issuer_report" and doc.authority == "issuer")
                           for doc in refs)
                or any(not doc.available or doc.published_on > portfolio.as_of or doc.as_of > portfolio.as_of or doc.as_of < candidate.period_end for doc in refs)):
            issues.append(f"{metric}: period, unit, definition or primary filing checks are unusable; fact remains unknown.")
            return None
        matches = [other for other in research.facts if other.metric == candidate.metric
                   and other.period_end == candidate.period_end and other.period_start == candidate.period_start]
        if any((other.value, other.unit, other.currency, other.definition) !=
               (candidate.value, candidate.unit, candidate.currency, candidate.definition) for other in matches):
            issues.append(f"{metric}: contradictory reported facts remain unknown.")
            return None
        if candidate.review == "issuer_report":
            issues.append(f"{metric}: read automatically from the issuer's published report ({candidate.id}); period, unit and conflicts are "
                          "checked automatically, footnotes were not human-reviewed.")
        if candidate.review == "sec_xbrl":
            issues.append(f"{metric}: automated SEC XBRL fact ({candidate.id}); filing, period, unit, segment and alternative-tag checks are automated, footnotes and company extension tags were not human-reviewed.")
        return candidate

    shares = fact(judgment.shares_fact_id, "shares")
    operating = judgment.method in {"earnings_exit", "fcf_exit"}
    metric_name = "revenue" if operating else "book_value" if judgment.method == "book_exit" else "ffo"
    initial = fact(judgment.revenue_fact_id if operating else judgment.metric_fact_id, metric_name)
    if shares and initial and shares.period_end != initial.period_end:
        issues.append("Financial periods do not align; per-share value remains unknown.")
        shares = None
    if research.sector == "financial" and judgment.method != "book_exit" or research.sector == "reit" and judgment.method != "ffo_exit" or research.sector in {"industrial", "other"} and not operating:
        raise ValueError("Use a sector-appropriate company method.")
    if research.sector == "unknown":
        initial = None
        issues.append("Sector is unknown; an appropriate valuation method cannot be established.")
    if research.cyclical is not False and judgment.mid_cycle_context is None:
        initial = None
        issues.append("Cyclicality or mid-cycle context is unknown; peak operating facts cannot imply a normalized case.")
    if row.value is None or not row.quote_used or row.quote_used.value <= 0:
        initial = None
        issues.append("Usable dated identity, unadjusted quote or FX is missing; company value remains unknown.")
    if not row.source_inputs_usable:
        issues.append("Starting price/identity/FX is provisional; no confident allocation is justified.")
    fx = Decimal(1) if row.supplied.currency == portfolio.reporting_currency else row.fx_used.rate if row.fx_used else None
    cases = []
    ordered = {case.name: case for case in judgment.cases}
    with localcontext() as context:
        context.prec = 60
        for name in ("downside", "base", "upside"):
            case = ordered[name]
            if row.supplied.currency == portfolio.reporting_currency and any(value != 1 for value in case.fx_multipliers):
                raise ValueError("Same-currency company cases require identity FX.")
            if not operating and any(value != 1 for value in [*case.margins, *case.cash_conversion]) or not operating and any(case.reinvestment):
                raise ValueError("Book/FFO paths grow the reported metric directly; operating transforms must be neutral.")
            case_issues = list(issues)
            if judgment.method != "book_exit" and case.return_on_equity is not None:
                raise ValueError("ROE paths apply only to book-value cases.")
            # A bank's book grows by what it retains: growth = ROE x (1 - payout), derived here, never judged separately.
            missing_roe = judgment.method == "book_exit" and case.return_on_equity is None
            if missing_roe:
                case_issues.append("Return on equity is unknown; book-value growth and distributable earnings cannot be derived.")
            elif judgment.method == "book_exit" and any(case.growth):
                case_issues.append("Book-value growth is derived as ROE x (1 - payout); the supplied growth path is not used.")
            base_metric = initial.value if initial and not missing_roe else None
            diluted = shares.value if shares else None
            distributions = Decimal(0)
            discounted_income = Decimal(0)
            terminal_metric = None
            price = None
            terminal = None
            present = None
            required = None
            factor = None
            path: list[CaseYear] = []
            sensitivity: list[str | None] = [None] * 3
            if base_metric is not None and diluted is not None and diluted > 0 and fx is not None:
                for year in range(5):
                    opening_metric = base_metric
                    book_growth = case.return_on_equity[year] * (1 - case.payout[year]) if judgment.method == "book_exit" and case.return_on_equity else None
                    base_metric *= 1 + (case.growth[year] if book_growth is None else book_growth)
                    diluted *= 1 + case.dilution[year]
                    terminal_metric = base_metric * case.margins[year] if operating else base_metric
                    if judgment.method == "fcf_exit":
                        terminal_metric *= case.cash_conversion[year] * (1 - case.reinvestment[year])
                    elif any(value != 1 for value in case.cash_conversion) or any(case.reinvestment):
                        raise ValueError("Cash conversion/reinvestment applies only to the FCF method.")
                    distribution_metric = terminal_metric
                    if judgment.method == "book_exit":
                        distribution_metric = opening_metric * (case.return_on_equity[year] if case.return_on_equity else Decimal(0))
                    income = max(distribution_metric, Decimal(0)) / diluted * case.payout[year]
                    distributions += income * fx * case.fx_multipliers[year]
                    discounted_income += income / (1 + case.discount_rate) ** (year + 1)
                    path.append(CaseYear(year=year + 1, revenue=fixed(base_metric, 2) if operating else None, metric=fixed(terminal_metric, 2),
                                         metric_margin=fixed(terminal_metric / base_metric, 6) if operating and base_metric else None,
                                         diluted_shares=fixed(diluted, 0), metric_per_share=fixed(terminal_metric / diluted, 4),
                                         distribution_per_share=fixed(income, 4),
                                         **({"return_on_equity": fixed(case.return_on_equity[year], 4), "payout": fixed(case.payout[year], 4),  # type: ignore[index]
                                             "retention": fixed(1 - case.payout[year], 4), "book_growth": fixed(book_growth, 6)} if book_growth is not None else {})))
                assert terminal_metric is not None
                factor = (1 + case.discount_rate) ** 5
                price = max(terminal_metric, Decimal(0)) * case.exit_multiple / diluted
                terminal = (price * fx * case.fx_multipliers[-1] + distributions) * (row.supplied.shares or Decimal(0))
                present = price / factor + discounted_income
                sensitivity = [fixed(max(terminal_metric, Decimal(0)) * multiple / diluted, 4) for multiple in case.exit_sensitivity]
                if terminal_metric > 0 and row.quote_used:
                    # Solves quote = M x terminal per-share metric / factor + discounted payouts for M.
                    required = fixed(max(row.quote_used.value - discounted_income, Decimal(0)) * factor * diluted / terminal_metric, 2)
            # Values stay exact Decimals until here; the API gets cents, 4-decimal per-share figures and whole shares.
            cases.append(CalculatedCompanyCase(
                name=case.name, judgment=case,
                starting_metric=fixed(initial.value, 2) if initial and initial.value is not None else None,
                starting_shares=fixed(shares.value, 0) if shares and shares.value is not None else None,
                starting_per_share=fixed(initial.value / shares.value, 4) if initial and shares and initial.value is not None and shares.value else None,
                path=path,
                equity_value=fixed(max(terminal_metric, Decimal(0)) * case.exit_multiple, 2) if price is not None and terminal_metric is not None else None,
                discount_factor=fixed(factor, 6) if factor is not None and price is not None else None,
                present_value_of_exit=fixed(price / factor, 4) if price is not None and factor else None,
                present_value_of_distributions=fixed(discounted_income, 4) if price is not None else None,
                terminal_metric=fixed(terminal_metric, 2) if terminal_metric is not None else None,
                terminal_shares=fixed(diluted, 0) if diluted is not None else None,
                terminal_price=fixed(price, 4) if price is not None else None,
                # Kept at ten decimals: the five-year comparison scales it to the position.
                terminal_reporting_per_share=fixed(price * fx * case.fx_multipliers[-1] + distributions, 10) if price is not None and fx is not None else None,
                known_terminal_value=fixed(terminal, 2) if terminal is not None else None,
                present_value_per_share=fixed(present, 4) if present is not None else None,
                sensitivity_prices=sensitivity, required_exit_multiple=required,
                qualifications=list(dict.fromkeys(case_issues)),
            ))
    return StockResult(position_id=position_id, as_of=portfolio.as_of,
                       reporting_currency=portfolio.reporting_currency, research=research,
                       judgments=judgment, cases=cases, calculation_basis=BASIS,
                       valuation=valuation(research, judgment, cases, row, initial),
                       qualifications=list(dict.fromkeys([*issues,
                           "Company terminal value uses this position's actual shares; comparison capital must be rescaled explicitly.",
                           "Amounts remain undetermined; unknown costs, taxes and personal context are not inferred from account type."])))


def fixed(value: Decimal, places: int) -> str:
    """A value rounded for the API; calculations keep the exact Decimal."""
    return money(value.quantize(Decimal(1).scaleb(-places)))


def valuation(research: CompanyResearch, judgment: CompanyJudgments, cases: list[CalculatedCompanyCase],
              row: PositionResult, initial: ResearchFact | None) -> StockValuation | None:
    """Today's price against the discounted per-share cases, plus reported figures for the same period to check the
    modeled path against. Nothing here is a judgment: a price in another currency or an unknown case stays unknown."""
    if initial is None:
        return None
    currency = initial.currency or row.supplied.currency
    quote = row.quote_used
    values: dict[str, str | None] = {case.name: case.present_value_per_share for case in cases}
    price = quote.value if quote and quote.currency == currency else None
    notes = [] if price is not None or quote is None else [f"The price is in {quote.currency} and the cases in {currency}; they are not compared."]
    position: str = "unknown"
    if price is not None and all(values.values()):
        low, mid, high = (Decimal(values[name] or "0") for name in ("downside", "base", "upside"))
        position = "below_downside" if price < low else "downside_to_base" if price < mid else "base_to_upside" if price <= high else "above_upside"
    by_metric: dict[tuple[str, date | None, date], Decimal | None] = {(fact.metric, fact.period_start, fact.period_end): fact.value for fact in research.facts}
    reported_name = "free_cash_flow" if judgment.method == "fcf_exit" else "net_income" if judgment.method == "earnings_exit" else None
    reported = by_metric.get((reported_name, initial.period_start, initial.period_end)) if reported_name else None
    reported_margin = reported / initial.value if reported is not None and initial.value else None
    base = next((case.judgment for case in cases if case.name == "base"), None)
    modeled = None
    if base is not None and reported_name:
        modeled = base.margins[0] * (base.cash_conversion[0] * (1 - base.reinvestment[0]) if judgment.method == "fcf_exit" else 1)
    if reported_name and reported_margin and modeled is not None and abs(modeled - reported_margin) > abs(reported_margin) / 4:
        label = reported_name.replace("_", " ")
        notes.append(f"The base case's first-year {label} margin ({modeled:.1%}) differs from the reported {reported_margin:.1%} "
                     f"for {initial.period_start} to {initial.period_end} by more than a quarter; that gap is a judgment, not a reported fact.")
    if judgment.method == "book_exit":
        # The reported payout anchors the bank cases' payout judgment: dividends per share over diluted EPS, same fiscal year.
        years = sorted({(fact.period_start, fact.period_end) for fact in research.facts if fact.metric == "eps_diluted" and fact.id.split("-")[1] == "fy"},
                       key=lambda period: period[1], reverse=True)
        for start, end in years[:1]:
            eps, dps = by_metric.get(("eps_diluted", start, end)), by_metric.get(("dividend_per_share", start, end))
            if eps and dps is not None and eps > 0:
                payouts = ", ".join(f"{case.name} {case.judgment.payout[0]:.0%}" for case in cases)
                notes.append(f"Reported payout for the year ended {end}: dividends {dps} / diluted EPS {eps} = {dps / eps:.1%}. "
                             f"First-year payout judgments: {payouts}. Book value grows by ROE x (1 - payout).")
    cash = by_metric.get(("cash", None, initial.period_end))
    debt = by_metric.get(("total_debt", None, initial.period_end))
    if cash is not None or debt is not None:
        notes.append("Cash and debt are context, not part of the case value: the exit multiple is applied to earnings or free cash flow, "
                     "which are after interest, so it values the equity directly.")
    mid_value = values.get("base")
    return StockValuation(
        price=fixed(price, 4) if price is not None else None, currency=currency, price_as_of=quote.as_of if quote else None,
        downside=values.get("downside"), base=mid_value, upside=values.get("upside"),
        price_to_base=fixed(price / Decimal(mid_value) - 1, 4) if price is not None and mid_value and Decimal(mid_value) > 0 else None,
        position=position,  # type: ignore[arg-type]
        reported_margin=fixed(reported_margin, 4) if reported_margin is not None else None,
        modeled_first_year_margin=fixed(modeled, 4) if modeled is not None else None,
        cash=fixed(cash, 2) if cash is not None else None, total_debt=fixed(debt, 2) if debt is not None else None,
        balance_date=initial.period_end if cash is not None or debt is not None else None, notes=notes)
