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


class AnalysisRequest(Contract):
    question: Text
    portfolio: Snapshot
    settings: PortfolioSettings | None = None
    proposed_changes: ProposedChanges | None = None


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


class AnalysisResult(Contract):
    status: Literal["completed"] = "completed"
    question: str
    portfolio: PortfolioReview
    recommendation: Recommendation
    proposals: list[ProposalReview] = Field(default_factory=list)
