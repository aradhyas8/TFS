from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Quantity = Annotated[Decimal, Field(ge=0, le=Decimal("1e12"), max_digits=24, decimal_places=10)]
Rate = Annotated[Decimal, Field(gt=0, le=Decimal("1e6"), max_digits=24, decimal_places=10)]
Fraction = Annotated[Decimal, Field(ge=0, le=1, max_digits=11, decimal_places=10)]
SharesChange = Annotated[Decimal, Field(ge=Decimal("-1e12"), le=Decimal("1e12"), max_digits=24, decimal_places=10)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Account(Contract):
    id: Identifier
    name: Identifier


class Mark(Contract):
    value: Quantity
    as_of: date
    source: Identifier
    captured_at: AwareDatetime | None = None
    basis: Literal["unadjusted", "split_adjusted", "total_return_adjusted", "unknown"] = "unadjusted"


class Position(Contract):
    id: Identifier
    account_id: Identifier
    kind: Literal["stock", "etf", "cash"]
    currency: Currency
    ticker: Identifier | None = None
    listing: Identifier | None = None
    company_id: Identifier | None = None
    company_name: Identifier | None = None
    shares: Quantity | None = None
    cash: Quantity | None = None
    mark: Mark | None = None
    etf_role: Literal["diversified", "sector_theme"] | None = None

    @model_validator(mode="after")
    def check_quantity(self) -> Self:
        if self.kind != "etf" and self.etf_role is not None:
            raise ValueError("Only ETFs have an ETF classification.")
        if self.kind == "cash":
            if self.cash is None or self.shares is not None or self.mark is not None:
                raise ValueError("Cash rows need a balance, without shares or a mark.")
        elif self.shares is None or self.cash is not None:
            raise ValueError("Security rows need shares, without a cash balance.")
        return self


class FX(Contract):
    from_currency: Currency
    to_currency: Currency
    rate: Rate
    as_of: date
    source: Identifier
    captured_at: AwareDatetime | None = None
    status: Literal["indicative", "manual", "cached", "stale"] = "manual"


class Identity(Contract):
    status: Literal["verified", "supplied", "ambiguous", "conflicting", "unresolved"]
    ticker: Identifier | None = None
    listing: Identifier | None = None
    currency: Currency | None = None
    kind: Literal["stock", "etf"] | None = None
    company_id: Identifier | None = None
    company_name: Identifier | None = None
    source: Identifier
    source_url: str | None = None
    as_of: date | None = None
    captured_at: AwareDatetime | None = None


class SourceQualification(Contract):
    source: Identifier
    terms_url: str
    checked_on: date
    personal_use_permitted: bool
    covered_listings: list[Identifier]


class Quote(Mark):
    ticker: Identifier
    listing: Identifier
    currency: Currency
    status: Literal["indicative", "delayed", "cached", "stale", "manual"]
    qualification: SourceQualification | None = None


class FinancialEvidence(Contract):
    identities: dict[str, Identity] = Field(default_factory=dict)
    quotes: dict[str, Quote | None] = Field(default_factory=dict)
    fx: list[FX] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class Snapshot(Contract):
    as_of: date
    reporting_currency: Currency
    accounts: list[Account] = Field(min_length=1, max_length=100)
    positions: list[Position] = Field(max_length=2000)
    fx: list[FX] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def check_references(self) -> Self:
        account_ids = [account.id for account in self.accounts]
        if len(set(account_ids)) != len(account_ids):
            raise ValueError("Account IDs must be unique.")
        position_ids = [position.id for position in self.positions]
        if len(set(position_ids)) != len(position_ids):
            raise ValueError("Position IDs must be unique; aggregate lots explicitly if needed.")
        if any(position.account_id not in account_ids for position in self.positions):
            raise ValueError("Every position must reference a supplied account.")
        pairs = [(fx.from_currency, fx.to_currency) for fx in self.fx]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Supply only one FX rate per directed currency pair.")
        company_names: dict[str, str] = {}
        identities: dict[tuple[str, str], tuple[str | None, str]] = {}
        for position in self.positions:
            if position.company_id and position.company_name:
                previous = company_names.setdefault(position.company_id, position.company_name)
                if previous != position.company_name:
                    raise ValueError("A company ID must have a consistent supplied name.")
            if position.ticker and position.listing:
                key = (position.ticker, position.listing)
                identity = (position.company_id, position.kind)
                if identities.setdefault(key, identity) != identity:
                    raise ValueError("A listing must have a consistent supplied issuer and kind.")
        return self


class Baseline(Contract):
    stocks: Fraction | None = None
    diversified_etfs: Fraction | None = None
    sector_theme_etfs: Fraction | None = None
    cash: Fraction | None = None

    @model_validator(mode="after")
    def check_total(self) -> Self:
        if sum((value for value in (self.stocks, self.diversified_etfs, self.sector_theme_etfs, self.cash) if value is not None), Decimal(0)) > 1:
            raise ValueError("Supplied baseline weights cannot exceed the whole portfolio.")
        return self


class PortfolioSettings(Contract):
    single_company_cap: Fraction | None = None
    active_budget: Fraction | None = None
    baseline: Baseline | None = None
    indirect_cap_policy: Literal["direct_only", "include_known_indirect"] | None = None
    cash_is_deliberate_tilt: bool | None = None


class CashContribution(Contract):
    cash_position_id: Identifier
    amount: Quantity


class ProposedTrade(Contract):
    position_id: Identifier
    shares_change: SharesChange
    cash_position_id: Identifier


class ProposedChanges(Contract):
    new_cash: list[CashContribution] = Field(max_length=2000)
    trades: list[ProposedTrade] = Field(max_length=2000)

    @model_validator(mode="after")
    def check_duplicates(self) -> Self:
        for ids in ([row.cash_position_id for row in self.new_cash], [row.position_id for row in self.trades]):
            if len(ids) != len(set(ids)):
                raise ValueError("Aggregate proposed changes explicitly; duplicate rows are not allowed.")
        return self


ScenarioReturn = Annotated[Decimal, Field(ge=-1, le=10, max_digits=13, decimal_places=10)]
FiveReturns = Annotated[list[ScenarioReturn], Field(min_length=5, max_length=5)]
FiveFactors = Annotated[list[Quantity], Field(min_length=5, max_length=5)]
FiveFX = Annotated[list[Rate], Field(min_length=5, max_length=5)]
CaseName = Literal["downside", "base", "upside"]


class FundFacts(Contract):
    position_id: Identifier
    as_of: date
    source: Identifier
    source_url: str | None = None
    exposure: Text
    annual_cost: Fraction | None = None
    income_yield: Fraction | None = None


class ComparisonAlternative(Contract):
    id: Identifier
    kind: Literal["etf", "cash", "short_bill", "no_action"]
    position_id: Identifier | None = None

    @model_validator(mode="after")
    def check_position(self) -> Self:
        if (self.kind == "no_action") != (self.position_id is None):
            raise ValueError("Only no action uses the actual scope without a destination position.")
        return self


class KnownEffects(Contract):
    alternative_id: Identifier
    transaction_cost: Quantity | None = None
    terminal_tax: Quantity | None = None
    as_of: date
    source: Identifier


class ComparisonInput(Contract):
    scope_position_ids: list[Identifier] = Field(min_length=1, max_length=20)
    alternatives: list[ComparisonAlternative] = Field(min_length=1, max_length=3)
    fund_facts: list[FundFacts] = Field(default_factory=list, max_length=20)
    effects: list[KnownEffects] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def unique_inputs(self) -> Self:
        for ids in (self.scope_position_ids, [row.id for row in self.alternatives],
                    [row.position_id for row in self.fund_facts],
                    [row.alternative_id for row in self.effects]):
            if len(ids) != len(set(ids)):
                raise ValueError("Comparison inputs must have unique references.")
        if len({(row.kind, row.position_id) for row in self.alternatives}) != len(self.alternatives):
            raise ValueError("Duplicate comparison alternatives are not allowed.")
        if any(row.alternative_id not in {alt.id for alt in self.alternatives} for row in self.effects):
            raise ValueError("Effects must reference selected alternatives.")
        return self


class ScenarioDriver(Contract):
    position_id: Identifier
    annual_returns: FiveReturns | None
    return_basis: Literal["price_only", "total_return"]
    cost_basis: Literal["gross", "net_of_fund_cost"]
    annual_rates: FiveReturns | None
    income_multipliers: FiveFactors | None
    reinvest: bool
    fx_multipliers: FiveFX


class ScenarioCase(Contract):
    name: CaseName
    drivers: list[ScenarioDriver] = Field(min_length=1, max_length=20)
    assumptions: list[Text] = Field(min_length=1, max_length=8)
    downside: Text
    uncertainty: list[Text] = Field(min_length=1, max_length=8)


class AlternativeJudgment(Contract):
    alternative_id: Identifier
    cases: list[ScenarioCase] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def three_cases(self) -> Self:
        if {row.name for row in self.cases} != {"downside", "base", "upside"}:
            raise ValueError("Exactly one downside, base and upside case is required.")
        return self


class ComparisonJudgments(Contract):
    alternatives: list[AlternativeJudgment] = Field(min_length=1, max_length=3)


class AnalysisRequest(Contract):
    question: Text
    portfolio: Snapshot
    settings: PortfolioSettings | None = None
    proposed_changes: ProposedChanges | None = None
    comparison: ComparisonInput | None = None

    @model_validator(mode="after")
    def comparison_references(self) -> Self:
        if self.comparison is None:
            return self
        rows = {row.id: row for row in self.portfolio.positions}
        comparison = self.comparison
        if any(key not in rows for key in comparison.scope_position_ids):
            raise ValueError("Comparison scope must reference actual current holdings or cash.")
        for alternative in comparison.alternatives:
            if alternative.position_id is None:
                continue
            row = rows.get(alternative.position_id)
            if row is None or (alternative.kind == "etf" and (row.kind != "etf" or row.etf_role != "diversified")) or (alternative.kind in {"cash", "short_bill"} and row.kind != "cash"):
                raise ValueError("Select a supplied diversified ETF or a cash-currency row for cash/short bills.")
        relevant = set(comparison.scope_position_ids) | {row.position_id for row in comparison.alternatives}
        for fact in comparison.fund_facts:
            if fact.position_id not in relevant or fact.position_id not in rows or rows[fact.position_id].kind != "etf":
                raise ValueError("Fund facts must reference ETFs used by the comparison.")
        return self


class CSVRequest(Contract):
    csv: str = Field(min_length=1, max_length=1_000_000)
    as_of: date
    reporting_currency: Currency


class Alternative(Contract):
    action: Literal["clarify_inputs", "keep_snapshot", "no_action"]
    reason: Text


class Recommendation(Contract):
    preferred_action: Literal["review_only", "wait_for_inputs", "no_action"]
    amount: None
    reason: Text
    alternatives: list[Alternative] = Field(min_length=1, max_length=2)
    downside: Text
    assumptions: list[Text] = Field(min_length=1, max_length=8)
    uncertainty: list[Text] = Field(min_length=1, max_length=8)
    what_could_change: list[Text] = Field(min_length=1, max_length=8)


class PositionResult(Contract):
    supplied: Position
    value: str | None
    local_value: str | None
    weight: str | None
    identity_status: Literal["verified", "supplied", "unresolved", "not_applicable"]
    identity: Identity | None = None
    quote_used: Quote | None = None
    quote_age_days: int | None = None
    quote_age_at_capture_days: int | None = None
    quote_age_at_request_days: int | None = None
    fx_age_days: int | None = None
    source_inputs_usable: bool = False
    fx_used: FX | None
    issues: list[str]


class AccountResult(Contract):
    id: str
    name: str
    total_value: str | None
    known_value: str


class CompanyExposure(Contract):
    company_id: str
    company_name: str
    value: str | None
    known_value: str
    weight: str | None
    position_ids: list[str]


CheckStatus = Literal["unset", "unknown", "within_limit", "breached"]


class CompanyCapCheck(Contract):
    company_id: str
    company_name: str
    current_weight: str | None
    cap: str | None
    status: CheckStatus
    excess_value: str | None
    reduction_to_cash: str | None
    explanation: str


class ActiveBudgetCheck(Contract):
    value: str | None
    known_value: str
    weight: str | None
    budget: str | None
    status: CheckStatus
    contributions: dict[str, str | None]
    qualifications: list[str]


class BaselineComparison(Contract):
    category: str
    current_weight: str | None
    baseline_weight: str | None
    difference: str | None


class GuardrailReview(Contract):
    settings: PortfolioSettings
    companies: list[CompanyCapCheck]
    active: ActiveBudgetCheck
    baseline_comparison: list[BaselineComparison]
    qualifications: list[str]


class PortfolioReview(Contract):
    as_of: date
    reviewed_at: AwareDatetime
    reporting_currency: str
    positions: list[PositionResult]
    accounts: list[AccountResult]
    direct_companies: list[CompanyExposure]
    total_value: str | None
    known_value: str
    holdings_value: str | None
    cash_value: str | None
    complete: bool
    source_inputs_usable: bool = False
    sizing_eligible: Literal[False] = False
    baseline: Baseline | None = None
    guardrails: GuardrailReview | None = None
    indirect_exposure: Literal["unknown"] = "unknown"
    qualifications: list[str]
    calculation_basis: str


class ProposalReview(Contract):
    changes: ProposedChanges
    source: Literal["user", "model"]
    status: Literal["within_limits", "blocked", "unknown"]
    post_total_value: str | None = None
    post_cash_value: str | None = None
    positions: list[PositionResult] = Field(default_factory=list)
    guardrails: GuardrailReview | None = None
    qualifications: list[str]


class ScenarioComponent(Contract):
    position_id: str
    local_currency: str
    starting_local_value: str | None
    fx_used: FX | None
    terminal_local_value: str | None
    known_terminal_value: str | None
    fully_specified: bool
    qualifications: list[str]


class CalculatedCase(Contract):
    name: CaseName
    judgment: ScenarioCase
    components: list[ScenarioComponent]
    known_terminal_value: str | None
    terminal_value: str | None
    qualifications: list[str]


class CalculatedAlternative(Contract):
    selection: ComparisonAlternative
    position_ids: list[str]
    cases: list[CalculatedCase]


class ComparisonResult(Contract):
    as_of: date
    reporting_currency: Currency
    horizon_years: Literal[5] = 5
    starting_value: str | None
    inputs: ComparisonInput
    alternatives: list[CalculatedAlternative]
    qualifications: list[str]
    calculation_basis: str


class AnalysisResult(Contract):
    status: Literal["completed"] = "completed"
    question: str
    portfolio: PortfolioReview
    recommendation: Recommendation
    proposals: list[ProposalReview] = Field(default_factory=list)
    comparison: ComparisonResult | None = None
