"""Conditional exposure/rate paths; facts and starting capital are backend bound."""

from datetime import date
from decimal import Decimal, localcontext

from .calculations import money
from .schemas import (
    CalculatedAlternative,
    CalculatedCase,
    ComparisonInput,
    ComparisonJudgments,
    ComparisonResult,
    FundFacts,
    PortfolioReview,
    PositionResult,
    ScenarioComponent,
    ScenarioDriver,
)

BASIS = (
    "Five annual periods, nominal reporting-currency outcomes. Each ETF period uses "
    "opening capital times the conditional price/total-return factor. Price-only income "
    "uses opening capital times the supplied income yield and judged income multiplier; "
    "total-return paths already include income and never add it again. Gross ETF paths "
    "deduct the supplied annual fund cost multiplicatively after reinvestment; net paths "
    "never deduct it again. Cash/short-bill interest uses each explicit annual rate; "
    "reinvestment compounds only when selected. Unreinvested income converts at that "
    "year's dated starting FX times the judged multiplier and remains idle reporting "
    "cash. Terminal invested local capital converts using the final FX multiplier. "
    "Known transaction costs reduce starting reporting capital; supplied terminal tax "
    "is deducted at the end. Unknown consequences remain unquantified; known terminal "
    "subtotals exclude them and are not complete forecasts. No company exit valuation, "
    "probabilities, weighted expected value or modeled inflation."
)


def component(
    row: PositionResult, starting: Decimal | None, driver: ScenarioDriver,
    reporting_currency: str, fact: FundFacts | None, as_of: date,
) -> ScenarioComponent:
    issues: list[str] = []
    kind = row.supplied.kind
    if kind == "cash":
        if driver.annual_returns is not None or driver.income_multipliers is not None or driver.annual_rates is None or driver.return_basis != "price_only" or driver.cost_basis != "gross":
            raise ValueError("Cash needs only explicit annual rates and reinvestment.")
    elif kind == "etf":
        if driver.annual_rates is not None or driver.annual_returns is None:
            raise ValueError("ETF paths need exposure returns, not cash rates.")
        if driver.return_basis == "total_return" and (driver.income_multipliers is not None or not driver.reinvest):
            raise ValueError("Reinvested total returns already include income; do not add it twice.")
        if driver.return_basis == "price_only" and driver.income_multipliers is None:
            issues.append("Future income path is unknown; not assumed zero.")
        if fact is None or fact.as_of != as_of:
            fact = None
            issues.append("Same-date ETF exposure, costs and income facts are unavailable.")
        if driver.cost_basis == "gross" and (fact is None or fact.annual_cost is None):
            issues.append("Fund cost is unknown and unquantified.")
        if driver.return_basis == "price_only" and (fact is None or fact.income_yield is None):
            issues.append("Income yield is unknown and unquantified.")
    else:
        issues.append("Retained stock outcomes are unknown; this comparison does not model company exit value.")

    fx: Decimal | None
    if row.supplied.currency == reporting_currency:
        if any(value != 1 for value in driver.fx_multipliers):
            raise ValueError("Same-currency paths must use identity FX multipliers.")
        fx = Decimal(1)
    else:
        fx = row.fx_used.rate if row.fx_used and row.fx_used.as_of == as_of and row.fx_used.status != "stale" else None
    if row.value is None or fx is None:
        issues.append("Starting identity, unadjusted quote or dated FX is unusable; outcome is unknown.")
        starting = None
    if not row.source_inputs_usable:
        issues.append("Starting valuation is provisional and cannot support confident sizing.")

    local = starting / fx if starting is not None and fx is not None else None
    capital = local
    distributions = Decimal(0)
    if capital is not None and kind in {"cash", "etf"} and fx is not None:
        for year in range(5):
            income = Decimal(0)
            if kind == "cash":
                assert driver.annual_rates is not None
                income = capital * driver.annual_rates[year]
            else:
                assert driver.annual_returns is not None
                if driver.return_basis == "price_only" and fact and fact.income_yield is not None and driver.income_multipliers is not None:
                    income = capital * fact.income_yield * driver.income_multipliers[year]
                capital *= 1 + driver.annual_returns[year]
            if driver.reinvest:
                capital += income
            else:
                distributions += income * fx * driver.fx_multipliers[year]
            if kind == "etf" and driver.cost_basis == "gross" and fact and fact.annual_cost is not None:
                capital *= 1 - fact.annual_cost
        terminal = capital * fx * driver.fx_multipliers[-1] + distributions
    else:
        capital = None
        terminal = None
    return ScenarioComponent(
        position_id=row.supplied.id, local_currency=row.supplied.currency,
        starting_local_value=money(local) if local is not None else None,
        fx_used=row.fx_used, terminal_local_value=money(capital) if capital is not None else None,
        known_terminal_value=money(terminal) if terminal is not None else None,
        fully_specified=terminal is not None and (kind == "cash" or bool(
            kind == "etf" and fact
            and (driver.cost_basis == "net_of_fund_cost" or fact.annual_cost is not None)
            and (driver.return_basis == "total_return" or fact.income_yield is not None and driver.income_multipliers is not None)
        )),
        qualifications=issues,
    )


def calculate_comparison(
    selection: ComparisonInput, judgments: ComparisonJudgments, portfolio: PortfolioReview,
) -> ComparisonResult:
    with localcontext() as context:
        context.prec = 60
        return _calculate(selection, judgments, portfolio)


def _calculate(
    selection: ComparisonInput, judgments: ComparisonJudgments, portfolio: PortfolioReview,
) -> ComparisonResult:
    judged = {row.alternative_id: row for row in judgments.alternatives}
    if len(judged) != len(judgments.alternatives) or set(judged) != {row.id for row in selection.alternatives}:
        raise ValueError("Judgments must cover exactly the user-selected alternatives.")
    rows = {row.supplied.id: row for row in portfolio.positions}
    scope = [rows[key] for key in selection.scope_position_ids]
    starting = None if any(row.value is None for row in scope) else sum((Decimal(row.value or "0") for row in scope), Decimal(0))
    facts = {row.position_id: row for row in selection.fund_facts}
    effects = {row.alternative_id: row for row in selection.effects}
    alternatives: list[CalculatedAlternative] = []
    for alternative in selection.alternatives:
        ids = selection.scope_position_ids if alternative.kind == "no_action" else [str(alternative.position_id)]
        effect = effects.get(alternative.id)
        if effect and effect.as_of != portfolio.as_of:
            effect = None
        transaction = effect.transaction_cost if effect else None
        tax = effect.terminal_tax if effect else None
        case_results: list[CalculatedCase] = []
        cases = {row.name: row for row in judged[alternative.id].cases}
        for name in ("downside", "base", "upside"):
            case = cases[name]
            drivers = {row.position_id: row for row in case.drivers}
            if len(drivers) != len(case.drivers) or set(drivers) != set(ids):
                raise ValueError("Drivers must cover the selected destination or actual retained holdings exactly.")
            issues: list[str] = []
            if transaction is None:
                issues.append("Transaction costs are unknown and unquantified.")
            if tax is None:
                issues.append("Tax consequences are unknown and unquantified; account name does not establish tax treatment.")
            if starting is None:
                issues.append("Comparison starting capital is unknown; all alternatives share the incomplete scope.")
            if alternative.kind == "no_action" and transaction not in {None, Decimal(0)}:
                raise ValueError("No action cannot include an invented transaction.")
            if starting is not None and transaction is not None and transaction > starting:
                raise ValueError("Known transaction cost exceeds comparison capital.")
            components: list[ScenarioComponent] = []
            for key in ids:
                initial = starting
                if alternative.kind == "no_action":
                    initial = Decimal(rows[key].value or "0") if starting is not None and rows[key].value is not None else None
                elif initial is not None and transaction is not None:
                    initial -= transaction
                components.append(component(rows[key], initial, drivers[key], portfolio.reporting_currency, facts.get(key), portfolio.as_of))
            complete = all(row.known_terminal_value is not None for row in components)
            terminal = sum((Decimal(row.known_terminal_value or "0") for row in components), Decimal(0)) - (tax or Decimal(0)) if complete else None
            issues.extend(issue for row in components for issue in row.qualifications)
            # Provisional source provenance qualifies an otherwise fully specified
            # conditional result; missing numeric effects must never become zero.
            fully_specified = transaction is not None and tax is not None and all(row.fully_specified for row in components)
            case_results.append(CalculatedCase(
                name=case.name, judgment=case, components=components,
                known_terminal_value=money(terminal) if terminal is not None else None,
                terminal_value=money(terminal) if terminal is not None and fully_specified else None,
                qualifications=list(dict.fromkeys(issues)),
            ))
        alternatives.append(CalculatedAlternative(selection=alternative, position_ids=ids, cases=case_results))
    return ComparisonResult(
        as_of=portfolio.as_of, reporting_currency=portfolio.reporting_currency,
        starting_value=money(starting) if starting is not None else None,
        inputs=selection, alternatives=alternatives, calculation_basis=BASIS,
        qualifications=[
            "All alternatives use the same actual selected holdings/cash value; this is a comparison basis, not a recommended allocation or an executed transaction.",
            "No action retains the actual scope, including each holding and cash balance; it is not automatically the cash alternative.",
            "Fund facts and effects are dated user-supplied inputs; future exposure, income, rates, reinvestment and FX paths are model judgments.",
            "Cash/short-bill paths are simple annual reinvestment cases; current short yields are not assumed to persist. Bill price, credit and early-sale effects are outside this simple hold-to-maturity path.",
            "Five years is a comparison horizon, not a mandatory exit date. The real-wealth objective over twenty or more years and historical return aspiration are neither forecasts nor required hurdles.",
            "Cases are conditional, without probabilities or a weighted expected value. These nominal values are not real purchasing-power outcomes; inflation is not modeled.",
        ],
    )
