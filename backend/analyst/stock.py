"""Conditional company arithmetic; reviewed facts never come from the model."""
from decimal import Decimal, localcontext

from .calculations import money
from .schemas import (
    CalculatedCompanyCase,
    CompanyJudgments,
    CompanyResearch,
    PortfolioReview,
    ResearchFact,
    StockResult,
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
    "and terminal price in local currency. Reverse valuation solves the exit multiple "
    "required to match today's unadjusted quote under the named operating assumptions "
    "before distributions; it is not a unique market belief. Costs and personal tax "
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
                or not all((candidate.filing_checked, candidate.notes_checked,
                            candidate.custom_tags_checked, candidate.segments_checked))
                or not any(doc.authority in required_filing and doc.available for doc in refs)
                or any(not doc.available or doc.published_on > portfolio.as_of or doc.as_of > portfolio.as_of or doc.as_of < candidate.period_end for doc in refs)):
            issues.append(f"{metric}: period, unit, definition or primary filing checks are unusable; fact remains unknown.")
            return None
        matches = [other for other in research.facts if other.metric == candidate.metric
                   and other.period_end == candidate.period_end and other.period_start == candidate.period_start]
        if any((other.value, other.unit, other.currency, other.definition) !=
               (candidate.value, candidate.unit, candidate.currency, candidate.definition) for other in matches):
            issues.append(f"{metric}: contradictory reported facts remain unknown.")
            return None
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
            missing_roe = judgment.method == "book_exit" and any(case.payout) and case.return_on_equity is None
            if missing_roe:
                case_issues.append("Return on equity is unknown; book capital cannot be treated as distributable earnings.")
            base_metric = initial.value if initial and not missing_roe else None
            diluted = shares.value if shares else None
            distributions = Decimal(0)
            discounted_income = Decimal(0)
            terminal_metric = None
            price = None
            terminal = None
            present = None
            required = None
            sensitivity: list[str | None] = [None] * 3
            if base_metric is not None and diluted is not None and diluted > 0 and fx is not None:
                for year in range(5):
                    opening_metric = base_metric
                    base_metric *= 1 + case.growth[year]
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
                assert terminal_metric is not None
                price = max(terminal_metric, Decimal(0)) * case.exit_multiple / diluted
                terminal = (price * fx * case.fx_multipliers[-1] + distributions) * (row.supplied.shares or Decimal(0))
                present = price / (1 + case.discount_rate) ** 5 + discounted_income
                sensitivity = [money(max(terminal_metric, Decimal(0)) * multiple / diluted) for multiple in case.exit_sensitivity]
                if terminal_metric > 0 and row.quote_used:
                    required = format(row.quote_used.value * diluted / terminal_metric, "f")
            cases.append(CalculatedCompanyCase(
                name=case.name, judgment=case,
                terminal_metric=money(terminal_metric) if terminal_metric is not None else None,
                terminal_shares=money(diluted) if diluted is not None else None,
                terminal_price=money(price) if price is not None else None,
                terminal_reporting_per_share=money(price * fx * case.fx_multipliers[-1] + distributions) if price is not None and fx is not None else None,
                known_terminal_value=money(terminal) if terminal is not None else None,
                present_value_per_share=money(present) if present is not None else None,
                sensitivity_prices=sensitivity, required_exit_multiple=required,
                qualifications=list(dict.fromkeys(case_issues)),
            ))
    return StockResult(position_id=position_id, as_of=portfolio.as_of,
                       reporting_currency=portfolio.reporting_currency, research=research,
                       judgments=judgment, cases=cases, calculation_basis=BASIS,
                       qualifications=list(dict.fromkeys([*issues,
                           "Company terminal value uses this position's actual shares; comparison capital must be rescaled explicitly.",
                           "Amounts remain undetermined; unknown costs, taxes and personal context are not inferred from account type."])))
