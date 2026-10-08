from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Quantity = Annotated[Decimal, Field(ge=0, le=Decimal("1e12"), max_digits=24, decimal_places=10)]
Rate = Annotated[Decimal, Field(gt=0, le=Decimal("1e6"), max_digits=24, decimal_places=10)]
# A reported amount that may be negative (operating income, net income, cash flows).
SignedAmount = Annotated[Decimal, Field(ge=Decimal("-1e15"), le=Decimal("1e15"), max_digits=28, decimal_places=10)]
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


US_LISTINGS = frozenset({"XNAS", "XNYS", "XASE"})
CANADIAN_LISTINGS = frozenset({"XTSE", "XTSX", "NEOE", "XCNQ"})


def is_canadian_security(currency: str | None, listing: str | None) -> bool:
    if listing in CANADIAN_LISTINGS:
        return True
    if listing in US_LISTINGS:
        return False
    return currency == "CAD"


def is_supported_stock(row: Position) -> bool:
    if row.kind != "stock":
        return False
    return (
        (row.currency == "USD" and row.listing in US_LISTINGS)
        or (row.currency == "CAD" and row.listing in CANADIAN_LISTINGS)
    )


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


HoldingsCoverage = Literal["full", "partial", "unknown", "stale"]


class HoldingConstituent(Contract):
    company_id: Identifier | None = None
    company_name: Text | None = None
    ticker: Identifier | None = None
    listing: Identifier | None = None
    kind: Literal["stock", "etf"] = "stock"
    weight: Fraction


class SponsorHoldings(Contract):
    as_of: date
    source: Identifier
    source_url: str | None = None
    coverage: HoldingsCoverage = "full"
    holdings: list[HoldingConstituent] = Field(default_factory=list)
    captured_at: AwareDatetime | None = None
    status: Literal["verified", "indicative", "stale", "partial", "unresolved"] | None = None


class FinancialEvidence(Contract):
    identities: dict[str, Identity] = Field(default_factory=dict)
    quotes: dict[str, Quote | None] = Field(default_factory=dict)
    fx: list[FX] = Field(default_factory=list)
    sponsor_holdings: dict[str, SponsorHoldings | None] = Field(default_factory=dict)
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
    kind: Literal["stock", "etf", "cash", "short_bill", "no_action"]
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
    scope_position_ids: list[Identifier] = Field(min_length=1, max_length=2000)
    alternatives: list[ComparisonAlternative] = Field(min_length=1, max_length=2003)
    fund_facts: list[FundFacts] = Field(default_factory=list, max_length=20)
    effects: list[KnownEffects] = Field(default_factory=list, max_length=5)

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
    drivers: list[ScenarioDriver] = Field(min_length=1, max_length=2000)
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
    alternatives: list[AlternativeJudgment] = Field(min_length=1, max_length=2003)


class StockInput(Contract):
    position_id: Identifier


NEW_CASH_DESTINATION = "new-cash-destination"


class NewCashInput(Contract):
    amount: Quantity | None = None
    cash_position_id: Identifier | None = None
    # Destination for money that is not yet in the portfolio. The request binds a
    # zero-balance cash row for it, so no existing cash holding is required.
    account_id: Identifier | None = None
    currency: Currency | None = None
    confirmed: bool = False
    risk_context: Text | None = None


class AllocationJudgment(Contract):
    position_id: Identifier
    min_weight: Fraction
    max_weight: Fraction
    reason: Text

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.min_weight > self.max_weight:
            raise ValueError("Exposure range must be ordered.")
        return self


class CandidateResearchInput(Contract):
    position_id: Identifier
    reason: Text


class CandidateCasesInput(Contract):
    position_id: Identifier
    judgments: CompanyJudgments


class DiscoveryCandidate(Contract):
    position: Position
    as_of: date
    source: Identifier
    source_url: str | None = None
    signal: Text

    @model_validator(mode="after")
    def prospective(self) -> Self:
        if self.position.kind == "cash" or self.position.shares != 0:
            raise ValueError("Discovery records must be zero-share prospective securities.")
        if self.position.kind == "stock" and not is_supported_stock(self.position):
            raise ValueError("Discovery stock research supports US and Canadian listings only.")
        if self.position.kind == "etf" and self.position.etf_role != "diversified":
            raise ValueError("Allocation fund alternatives require diversified exposure.")
        return self


class DiscoveryScan(Contract):
    source_captured_at: AwareDatetime | None = None
    scanned_at: AwareDatetime
    as_of: date
    source: Text
    candidates: list[DiscoveryCandidate] = Field(default_factory=list, max_length=8)
    fund_facts: list[FundFacts] = Field(default_factory=list, max_length=8)
    issues: list[Text] = Field(default_factory=list, max_length=20)


class AllocationAmount(Contract):
    minimum: str
    maximum: str
    currency: Currency
    position_id: Identifier


class PriorThesis(Contract):
    company_id: Identifier
    as_of: date
    thesis: Text


class PortfolioReviewInput(Contract):
    prior_theses: list[PriorThesis] = Field(default_factory=list, max_length=2000)
    risk_context: Text | None = None


class ThemeInput(Contract):
    risk_context: Text | None = None
    name: Text | None = None
    mechanism: Text | None = None
    shortlist: list[Identifier] = Field(default_factory=list, max_length=4)
    max_candidates: int = Field(default=2, ge=1, le=4)
    max_tool_calls: int = Field(default=16, ge=1, le=24)
    confirmed: bool = False

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(set(self.shortlist)) != len(self.shortlist) or len(self.shortlist) > self.max_candidates:
            raise ValueError("Shortlist must be unique and within the agreed candidate bound.")
        if self.confirmed and (not self.name or not self.mechanism or not self.shortlist):
            raise ValueError("Agreement requires a theme, mechanism and shortlist.")
        return self


class ThemeTestInput(Contract):
    position_id: Identifier
    conclusion: Literal["supports", "challenges", "unknown"]
    explanation: Text
    evidence_ids: list[Identifier] = Field(max_length=20)


class AnalysisRequest(Contract):
    question: Text
    portfolio: Snapshot
    settings: PortfolioSettings | None = None
    proposed_changes: ProposedChanges | None = None
    comparison: ComparisonInput | None = None
    stock: StockInput | None = None
    new_cash: NewCashInput | None = None
    portfolio_review: PortfolioReviewInput | None = None
    theme: ThemeInput | None = None

    @model_validator(mode="after")
    def comparison_references(self) -> Self:
        import re
        if self.theme is not None:
            if self.stock or self.new_cash or self.portfolio_review:
                raise ValueError("Choose one decision request type.")
            rows = {row.id: row for row in self.portfolio.positions}
            for key in self.theme.shortlist:
                row = rows.get(key)
                if row is None or row.kind not in {"stock", "etf"}:
                    raise ValueError("Shortlist must reference supplied securities.")
                if row.kind == "stock" and not is_supported_stock(row):
                    raise ValueError("Shortlist stock research supports US and Canadian listings only.")
            if self.comparison is None and self.theme.shortlist:
                alternatives = [ComparisonAlternative(id=f"candidate-{key}", kind=rows[key].kind, position_id=key) for key in self.theme.shortlist]
                fund = next((row for row in rows.values() if row.kind == "etf" and row.etf_role == "diversified" and row.id not in self.theme.shortlist), None)
                cash = next((row for row in rows.values() if row.kind == "cash"), None)
                if fund:
                    alternatives.append(ComparisonAlternative(id="fund", kind="etf", position_id=fund.id))
                if cash:
                    alternatives.append(ComparisonAlternative(id="cash", kind="cash", position_id=cash.id))
                alternatives.append(ComparisonAlternative(id="keep", kind="no_action"))
                scope = [row.id for row in rows.values() if row.shares or row.cash]
                self.comparison = ComparisonInput(scope_position_ids=scope or self.theme.shortlist, alternatives=alternatives)
            if self.comparison:
                selected = {row.position_id for row in self.comparison.alternatives if row.kind in {"stock", "etf"}}
                if not set(self.theme.shortlist).issubset(selected) or any(key not in self.theme.shortlist and not (rows.get(str(key)) and rows[str(key)].kind == "etf" and rows[str(key)].etf_role == "diversified") for key in selected):
                    raise ValueError("Comparison must contain the shortlist and only diversified fund alternatives beyond it.")
                if not {"cash", "no_action"}.issubset({row.kind for row in self.comparison.alternatives}):
                    raise ValueError("Theme comparison requires cash and no action.")
        if self.portfolio_review is not None:
            if self.new_cash is not None or self.stock is not None:
                raise ValueError("Choose one decision request type.")
            priors = self.portfolio_review.prior_theses
            company_ids = {row.company_id for row in self.portfolio.positions if row.kind == "stock"}
            if len({row.company_id for row in priors}) != len(priors) or any(row.company_id not in company_ids or row.as_of > self.portfolio.as_of for row in priors):
                raise ValueError("Prior theses must uniquely reference current companies and cannot be future dated.")
            if self.comparison is None:
                representatives: dict[str, Position] = {}
                for held in self.portfolio.positions:
                    if held.kind == "stock" and held.shares and is_supported_stock(held):
                        representatives.setdefault(held.company_id or held.id, held)
                alternatives = [ComparisonAlternative(id=f"company-{row.id}", kind="stock", position_id=row.id) for row in representatives.values()]
                fund = next((row for row in self.portfolio.positions if row.kind == "etf" and row.etf_role == "diversified"), None)
                cash = next((row for row in self.portfolio.positions if row.kind == "cash"), None)
                if fund:
                    alternatives.append(ComparisonAlternative(id="fund", kind="etf", position_id=fund.id))
                if cash:
                    alternatives.append(ComparisonAlternative(id="cash", kind="cash", position_id=cash.id))
                alternatives.append(ComparisonAlternative(id="keep", kind="no_action"))
                self.comparison = ComparisonInput(scope_position_ids=[row.id for row in self.portfolio.positions], alternatives=alternatives)
        if self.theme is None and self.portfolio_review is None and self.new_cash is None and self.stock is None and self.comparison is None and self.proposed_changes is None and re.search(r"new cash|allocate.*cash|\$[\d,]+.*what should|what.*\$[\d,]+", self.question, re.I):
            self.new_cash = NewCashInput()
        if self.new_cash is not None:
            if self.stock is not None or self.comparison is not None or self.proposed_changes is not None:
                raise ValueError("New-cash decisions bind their own comparison and previews in the shared pipeline.")
            if self.new_cash.account_id is not None and self.new_cash.cash_position_id is None:
                if all(account.id != self.new_cash.account_id for account in self.portfolio.accounts):
                    raise ValueError("Choose a destination account from the portfolio.")
                if any(row.id == NEW_CASH_DESTINATION for row in self.portfolio.positions):
                    raise ValueError("Reserved new-cash destination identifier.")
                currency = self.new_cash.currency or self.portfolio.reporting_currency
                self.portfolio.positions.append(Position(id=NEW_CASH_DESTINATION, account_id=self.new_cash.account_id,
                                                         kind="cash", currency=currency, cash=Decimal(0)))
                self.new_cash.cash_position_id = NEW_CASH_DESTINATION
                self.new_cash.currency = currency
            if self.new_cash.cash_position_id is not None and not any(row.id == self.new_cash.cash_position_id and row.kind == "cash" for row in self.portfolio.positions):
                raise ValueError("Confirm an existing account cash balance for the new contribution.")
            if any(row.id == "__new_cash__" for row in self.portfolio.positions):
                raise ValueError("Reserved comparison cash identifier.")
            return self
        if self.stock is not None:
            target = next((row for row in self.portfolio.positions if row.id == self.stock.position_id), None)
            if target is None or target.kind != "stock" or not is_supported_stock(target):
                raise ValueError("Stock research requires a supplied US or Canadian stock listing.")
        if self.stock is not None and self.comparison is None:
            target = next(row for row in self.portfolio.positions if row.id == self.stock.position_id)
            cash = next((row for row in self.portfolio.positions if row.kind == "cash" and row.cash), None)
            fund = next((row for row in self.portfolio.positions if row.kind == "etf" and row.etf_role == "diversified"), None)
            alternatives = [ComparisonAlternative(id="company", kind="stock", position_id=target.id)]
            if fund is not None:
                alternatives.append(ComparisonAlternative(id="fund", kind="etf", position_id=fund.id))
            if cash is not None:
                alternatives.append(ComparisonAlternative(id="cash", kind="cash", position_id=cash.id))
            alternatives.append(ComparisonAlternative(id="keep", kind="no_action"))
            scope = [target.id] if target.shares else [cash.id] if cash else [target.id]
            self.comparison = ComparisonInput(scope_position_ids=scope, alternatives=alternatives)
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
            if alternative.kind == "stock" and self.theme is None and self.portfolio_review is None and (self.stock is None or alternative.position_id != self.stock.position_id):
                raise ValueError("Stock alternatives must use the selected researched listing.")
            if row is None or (alternative.kind == "etf" and (row.kind != "etf" or row.etf_role != "diversified" and not (self.theme and row.id in self.theme.shortlist))) or (alternative.kind in {"cash", "short_bill"} and row.kind != "cash"):
                raise ValueError("Select a supplied diversified ETF or a cash-currency row for cash/short bills.")
        if self.portfolio_review:
            if set(comparison.scope_position_ids) != set(rows) or not any(row.kind == "no_action" for row in comparison.alternatives):
                raise ValueError("Portfolio review comparison must retain the whole portfolio and include no action.")
            for alt in comparison.alternatives:
                if alt.kind == "stock":
                    row = rows[str(alt.position_id)]
                    if row.kind != "stock" or not is_supported_stock(row) or not row.shares:
                        raise ValueError("Review stock alternatives require held US or Canadian listings.")
        relevant = set(comparison.scope_position_ids) | {row.position_id for row in comparison.alternatives}
        for fact in comparison.fund_facts:
            if fact.position_id not in relevant or fact.position_id not in rows or rows[fact.position_id].kind != "etf":
                raise ValueError("Fund facts must reference ETFs used by the comparison.")
        return self


class CSVRequest(Contract):
    csv: str = Field(min_length=1, max_length=1_000_000)
    as_of: date | None = None
    reporting_currency: Currency

    @field_validator("as_of", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> Any:
        if v == "" or v is None:
            return None
        return v


class Alternative(Contract):
    action: Literal["clarify_inputs", "keep_snapshot", "no_action", "add", "hold", "reduce", "exit"]
    reason: Text


class Recommendation(Contract):
    preferred_action: Literal["review_only", "wait_for_inputs", "no_action", "add", "hold", "reduce", "exit"]
    amount: AllocationAmount | None
    reason: Text
    alternatives: list[Alternative] = Field(min_length=1, max_length=2)
    downside: Text
    assumptions: list[Text] = Field(min_length=1, max_length=8)
    uncertainty: list[Text] = Field(min_length=1, max_length=8)
    what_could_change: list[Text] = Field(min_length=1, max_length=8)


class StockRecommendation(Recommendation):
    evidence_ids: list[Identifier] = Field(min_length=0, max_length=20)


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


class CurrencyExposure(Contract):
    currency: str
    value: str | None
    known_value: str
    weight: str | None


class CompanyExposure(Contract):
    company_id: str
    company_name: str
    value: str | None
    known_value: str
    weight: str | None
    position_ids: list[str]


class FundOverlapContribution(Contract):
    position_id: str
    ticker: str | None = None
    listing: str | None = None
    fund_name: str | None = None
    fund_weight: str | None = None
    weight_in_fund: str
    indirect_value: str | None = None
    indirect_weight: str | None = None
    as_of: date
    source: str
    coverage: HoldingsCoverage


class CompanyOverlap(Contract):
    company_id: str
    company_name: str
    direct_value: str | None = None
    direct_weight: str | None = None
    indirect_value: str | None = None
    indirect_weight: str | None = None
    total_value: str | None = None
    total_weight: str | None = None
    coverage: HoldingsCoverage = "full"
    source_dates: list[date] = Field(default_factory=list)
    contributing_funds: list[FundOverlapContribution] = Field(default_factory=list)


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
    direct_weight: str | None = None
    indirect_weight: str | None = None
    policy: str | None = None


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
    currency_exposure: list[CurrencyExposure] = Field(default_factory=list)
    company_overlap: list[CompanyOverlap] = Field(default_factory=list)
    total_value: str | None
    known_value: str
    holdings_value: str | None
    cash_value: str | None
    complete: bool
    source_inputs_usable: bool = False
    sizing_eligible: Literal[False] = False
    baseline: Baseline | None = None
    guardrails: GuardrailReview | None = None
    indirect_exposure: Literal["none", "full", "partial", "unknown", "stale"] = "unknown"
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
    # The value to compare: after costs and taxes when both are known, else before the adjustments listed in
    # `unmodeled`, which are unknown and never assumed to be zero.
    comparison_value: str | None = None
    unmodeled: list[str] = Field(default_factory=list)


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


class ResearchDocument(Contract):
    id: Identifier
    authority: Literal["sec", "sedar", "sedar_plus", "issuer", "macro"]
    company_id: Identifier
    url: str
    published_on: date
    as_of: date
    title: Text
    excerpt: str = Field(max_length=20000)
    available: bool
    qa_available: bool

    @model_validator(mode="after")
    def source_url(self) -> Self:
        from urllib.parse import urlsplit
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Primary evidence needs a public HTTPS reference.")
        if self.authority == "sec" and (parsed.hostname != "www.sec.gov" or not parsed.path.startswith("/Archives/edgar/data/")):
            raise ValueError("SEC evidence must reference the original EDGAR filing.")
        if self.authority in {"sedar", "sedar_plus"} and (parsed.hostname not in {"www.sedarplus.ca", "sedarplus.ca"} or not parsed.path or parsed.path == "/"):
            raise ValueError("SEDAR+ evidence must reference an exact sedarplus.ca filing verification link.")
        return self


class FactSource(Contract):
    """Where one reported number came from, exactly as SEC published it."""
    taxonomy: Identifier
    concept: Identifier
    unit: Identifier
    accession: Identifier
    form: Identifier
    fiscal_year: int | None = None
    fiscal_period: Identifier | None = None
    filed: date
    period_start: date | None
    period_end: date
    value: SignedAmount
    url: str


RESEARCH_METRICS = ("revenue", "shares", "book_value", "ffo", "operating_income", "net_income", "cash", "total_debt",
                    "operating_cash_flow", "capex", "free_cash_flow")
NON_NEGATIVE_METRICS = {"revenue", "shares", "book_value", "ffo", "cash", "total_debt", "capex"}


class ResearchFact(Contract):
    id: Identifier
    metric: Literal["revenue", "shares", "book_value", "ffo", "operating_income", "net_income", "cash", "total_debt",
                    "operating_cash_flow", "capex", "free_cash_flow"]
    value: SignedAmount | None
    unit: Literal["currency", "shares"]
    currency: Currency | None
    period_start: date | None
    period_end: date
    definition: Text
    document_ids: list[Identifier] = Field(min_length=1, max_length=5)
    filing_checked: bool
    notes_checked: bool
    custom_tags_checked: bool
    segments_checked: bool
    # "manual": independently reviewed extract. "sec_xbrl": read automatically from SEC CompanyFacts; the filing,
    # segment and alternative-tag checks are automated and footnotes are not read, so notes_checked stays false.
    review: Literal["manual", "sec_xbrl"] = "manual"
    sources: list[FactSource] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def sign(self) -> Self:
        if self.value is not None and self.value < 0 and self.metric in NON_NEGATIVE_METRICS:
            raise ValueError(f"A reported {self.metric} cannot be negative.")
        return self


class CompanyResearch(Contract):
    company_id: Identifier
    sector: Literal["industrial", "financial", "reit", "other", "unknown"]
    cyclical: bool | None
    documents: list[ResearchDocument] = Field(max_length=20)
    facts: list[ResearchFact] = Field(max_length=40)
    issues: list[Text] = Field(max_length=40)

    @model_validator(mode="after")
    def references(self) -> Self:
        for ids in ([row.id for row in self.documents], [row.id for row in self.facts]):
            if len(set(ids)) != len(ids):
                raise ValueError("Research IDs must be unique.")
        if any(row.company_id != self.company_id for row in self.documents):
            raise ValueError("Research must match the backend-bound issuer.")
        if any(key not in {row.id for row in self.documents} for fact in self.facts for key in fact.document_ids):
            raise ValueError("Facts must link to bound primary documents.")
        return self


class CompanyCase(Contract):
    name: CaseName
    growth: FiveReturns
    margins: Annotated[list[Annotated[Decimal, Field(ge=-1, le=1, max_digits=11, decimal_places=10)]], Field(min_length=5, max_length=5)]
    cash_conversion: Annotated[list[Fraction], Field(min_length=5, max_length=5)]
    reinvestment: Annotated[list[Fraction], Field(min_length=5, max_length=5)]
    dilution: FiveReturns
    payout: Annotated[list[Fraction], Field(min_length=5, max_length=5)]
    return_on_equity: FiveReturns | None
    fx_multipliers: FiveFX
    discount_rate: Fraction
    exit_multiple: Rate
    exit_sensitivity: Annotated[list[Rate], Field(min_length=3, max_length=3)]
    assumptions: list[Text] = Field(min_length=1, max_length=8)
    uncertainty: list[Text] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def positive_shares(self) -> Self:
        if any(value <= -1 for value in self.dilution):
            raise ValueError("Diluted shares must remain positive.")
        return self


class CompanyJudgments(Contract):
    method: Literal["earnings_exit", "fcf_exit", "book_exit", "ffo_exit"]
    revenue_fact_id: Identifier | None
    shares_fact_id: Identifier
    metric_fact_id: Identifier | None
    mid_cycle_context: Text | None
    cases: list[CompanyCase] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def three_cases(self) -> Self:
        if {row.name for row in self.cases} != {"downside", "base", "upside"}:
            raise ValueError("Exactly one downside/base/upside case is required.")
        return self


class CaseYear(Contract):
    """One modeled year of a company case, as calculated. Money is in the reported metric's currency."""
    year: int
    revenue: str | None
    metric: str
    metric_margin: str | None
    diluted_shares: str
    metric_per_share: str
    distribution_per_share: str


class CalculatedCompanyCase(Contract):
    name: CaseName
    judgment: CompanyCase
    # The deterministic chain, exposed for audit: starting facts, each year, the exit and the discounting.
    starting_metric: str | None = None
    starting_shares: str | None = None
    path: list[CaseYear] = Field(default_factory=list, max_length=5)
    equity_value: str | None = None
    discount_factor: str | None = None
    present_value_of_exit: str | None = None
    present_value_of_distributions: str | None = None
    terminal_metric: str | None
    terminal_shares: str | None
    terminal_price: str | None
    terminal_reporting_per_share: str | None
    known_terminal_value: str | None
    present_value_per_share: str | None
    sensitivity_prices: list[str | None]
    required_exit_multiple: str | None
    qualifications: list[str]


class StockValuation(Contract):
    """Where today's price sits against the calculated cases. Deterministic; the model interprets it."""
    price: str | None
    currency: Currency
    price_as_of: date | None
    downside: str | None
    base: str | None
    upside: str | None
    price_to_base: str | None
    position: Literal["below_downside", "downside_to_base", "base_to_upside", "above_upside", "unknown"]
    # Reported figures for the same period, so the modeled path can be checked against what the company reported.
    reported_margin: str | None = None
    modeled_first_year_margin: str | None = None
    cash: str | None = None
    total_debt: str | None = None
    balance_date: date | None = None
    notes: list[str] = Field(default_factory=list)


class StockResult(Contract):
    position_id: str
    as_of: date
    reporting_currency: Currency
    research: CompanyResearch
    judgments: CompanyJudgments
    cases: list[CalculatedCompanyCase]
    calculation_basis: str
    qualifications: list[str]
    valuation: StockValuation | None = None
    # Why exact position sizing is not given, stated apart from the investment view.
    sizing_withheld: list[str] = Field(default_factory=list)


class AllocationResult(Contract):
    context: NewCashInput
    scan: DiscoveryScan
    researched: list[CandidateResearchInput] = Field(default_factory=list, max_length=2)
    stocks: list[StockResult] = Field(default_factory=list, max_length=2)
    judgment: AllocationJudgment | None = None
    amount: AllocationAmount | None = None
    previews: list[ProposalReview] = Field(default_factory=list, max_length=2)
    missing_inputs: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=lambda: [
        "Approximate exposure is an analyst judgment, not an objectively optimal allocation. Amounts and post-allocation limits are calculated in Python.",
        "New cash is outside the dated snapshot and added once to the whole-portfolio denominator. Only new cash funds this recommendation; existing cash is retained.",
        "ETF indirect overlap is unknown, never zero. Supplied cap policy is preserved. Costs, tax and execution effects remain unquantified; orders remain with the user."])


class ThesisAssessment(Contract):
    position_id: Identifier
    status: Literal["changed", "unchanged", "unknown"]
    action: Literal["add", "hold", "reduce", "exit", "no_action", "wait_for_inputs"]
    current_thesis: Text
    change_reason: Text
    downside: Text
    what_could_change: list[Text] = Field(min_length=1, max_length=8)
    evidence_ids: list[Identifier] = Field(max_length=20)


class HoldingReviewInput(Contract):
    position_id: Identifier
    judgments: CompanyJudgments
    assessment: ThesisAssessment


class ReviewSizingInput(AllocationJudgment):
    cash_position_id: Identifier


class ReunderwritingResult(Contract):
    context: PortfolioReviewInput
    sizing: ReviewSizingInput | None = None
    amount: AllocationAmount | None = None
    previews: list[ProposalReview] = Field(default_factory=list, max_length=2)
    missing_inputs: list[str] = Field(default_factory=list)
    research: dict[str, CompanyResearch] = Field(default_factory=dict)
    stocks: list[StockResult] = Field(default_factory=list)
    assessments: list[ThesisAssessment] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=lambda: [
        "Price movement is context, never proof of thesis failure or a reason to average down. Prior ownership does not protect a weak thesis.",
        "Target-relative changes use only the supplied baseline. Without it, this is current-exposure and thesis review, without invented targets.",
        "ETF overlap, missing company coverage, costs and taxes remain unknown. Conditional actions are not orders or justified amounts."])


class ThemeResult(Contract):
    context: ThemeInput
    status: Literal["awaiting_agreement", "completed"]
    researched: list[Identifier] = Field(default_factory=list)
    stocks: list[StockResult] = Field(default_factory=list)
    tests: list[ThemeTestInput] = Field(default_factory=list)
    tool_calls_used: int = 0
    sizing: ReviewSizingInput | None = None
    amount: AllocationAmount | None = None
    previews: list[ProposalReview] = Field(default_factory=list, max_length=2)
    missing_inputs: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=lambda: [
        "Research is confined to the user-agreed shortlist and tool-call effort bound; there is no market-wide discovery.",
        "Agency/macro coverage is unavailable in this workflow; a named mechanism without primary support remains unknown.",
        "Amounts remain undetermined without justified sizing. Costs, taxes and indirect overlap remain qualified; no orders are executed."])


class AnalysisResult(Contract):
    status: Literal["completed"] = "completed"
    question: str
    portfolio: PortfolioReview
    recommendation: StockRecommendation | Recommendation
    proposals: list[ProposalReview] = Field(default_factory=list)
    comparison: ComparisonResult | None = None
    stock: StockResult | None = None
    allocation: AllocationResult | None = None
    reunderwriting: ReunderwritingResult | None = None
    theme: ThemeResult | None = None


DecisionAction = Literal[
    "review_only",
    "wait_for_inputs",
    "no_action",
    "add",
    "hold",
    "reduce",
    "exit",
    "clarify_inputs",
    "keep_snapshot",
]


class EvidenceReference(Contract):
    id: Identifier
    title: Text
    source: Identifier
    as_of: date | None = None
    url: str | None = None
    excerpt: Text | None = None


DecisionAlternative = Alternative


class DecisionReasoning(Contract):
    reason: Text
    assumptions: list[Text] = Field(default_factory=list)
    uncertainty: list[Text] = Field(default_factory=list)
    what_could_change: list[Text] = Field(default_factory=list)
    downside: Text
    alternatives: list[DecisionAlternative] = Field(default_factory=list)


class DecisionConclusion(Contract):
    preferred_action: Literal["review_only", "wait_for_inputs", "no_action", "add", "hold", "reduce", "exit"]
    amount: AllocationAmount | None = None


class UserConfirmedAction(Contract):
    action: DecisionAction
    confirmed_at: AwareDatetime
    notes: Text | None = None


class SavedDecision(Contract):
    id: Identifier
    saved_at: AwareDatetime
    as_of: date
    question: Text
    evidence_references: list[EvidenceReference] = Field(default_factory=list)
    reasoning: DecisionReasoning
    conclusion: DecisionConclusion
    confirmed_action: UserConfirmedAction | None = None


class SaveDecisionRequest(Contract):
    result: AnalysisResult | None = None
    decision: SavedDecision | None = None
    confirmed_action: UserConfirmedAction | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.result is None and self.decision is None:
            raise ValueError("Provide either a completed analysis result or a saved decision record.")
        return self


class ConfirmActionRequest(Contract):
    action: DecisionAction
    notes: Text | None = None


# Resolve forward references used by the shared tool contracts.
CandidateCasesInput.model_rebuild()
AnalysisRequest.model_rebuild()
SaveDecisionRequest.model_rebuild()



class UnresolvedHolding(Contract):
    """An imported holding whose listing or security type could not be resolved. Only what is known is kept."""
    account_id: Identifier
    ticker: Identifier  # as entered, including any exchange suffix
    shares: Quantity
    average_cost: Quantity | None = None
    currency: Currency | None = None
    listing: Identifier | None = None
    kind: Literal["stock", "etf"] | None = None
    # Listings the market-data provider found when more than one matched; the user picks one.
    candidates: list[Identifier] = Field(default_factory=list, max_length=8)


class SavedPortfolio(Contract):
    """The user's current portfolio and rules. Average cost is kept for the user only and never sent to analysis."""
    snapshot: Snapshot
    average_costs: dict[Identifier, Quantity] = Field(default_factory=dict)
    settings: PortfolioSettings | None = None
    unresolved: list[UnresolvedHolding] = Field(default_factory=list, max_length=2000)
    saved_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def check_costs(self) -> Self:
        securities = {row.id for row in self.snapshot.positions if row.kind != "cash"}
        if any(key not in securities for key in self.average_costs):
            raise ValueError("Average cost must reference a held security.")
        if any(row.id == NEW_CASH_DESTINATION for row in self.snapshot.positions):
            raise ValueError("Reserved new-cash destination identifier.")
        accounts = {account.id for account in self.snapshot.accounts}
        if any(row.account_id not in accounts for row in self.unresolved):
            raise ValueError("Every unresolved holding must reference a supplied account.")
        return self


class RefreshPrices(Contract):
    """Refresh the market-data cache; force fetches again even if today's prices are cached."""
    force: bool = False


class IdentifyHolding(Contract):
    """The user's answer for one unresolved holding: only the missing listing and/or type."""
    account_id: Identifier
    ticker: Identifier
    listing: Identifier | None = None
    kind: Literal["stock", "etf"] | None = None
