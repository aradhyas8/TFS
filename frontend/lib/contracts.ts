export type Account = { id: string; name: string };
export type Mark = { value: string; as_of: string; source: string; captured_at?: string | null;
  basis?: "unadjusted" | "split_adjusted" | "total_return_adjusted" | "unknown" };
export type Identity = { status: string; ticker: string | null; listing: string | null; currency: string | null;
  company_id: string | null; company_name: string | null; source: string; source_url: string | null;
  as_of: string | null; captured_at: string | null };
export type Quote = Mark & { ticker: string; listing: string; currency: string; status: string };
export type Position = {
  id: string; account_id: string; kind: "stock" | "etf" | "cash"; currency: string;
  ticker?: string | null; listing?: string | null; company_id?: string | null;
  company_name?: string | null; shares?: string | null; cash?: string | null; mark?: Mark | null;
  etf_role?: "diversified" | "sector_theme" | null;
};
export type FX = { from_currency: string; to_currency: string; rate: string; as_of: string; source: string;
  captured_at?: string | null; status?: string };
export type Snapshot = { as_of: string; reporting_currency: string; accounts: Account[]; positions: Position[]; fx: FX[] };
export type Baseline = { stocks?: string | null; diversified_etfs?: string | null; sector_theme_etfs?: string | null; cash?: string | null };
export type PortfolioSettings = { single_company_cap?: string | null; active_budget?: string | null;
  baseline?: Baseline | null; indirect_cap_policy?: "direct_only" | "include_known_indirect" | null;
  cash_is_deliberate_tilt?: boolean | null };
export type ProposedChanges = { new_cash: { cash_position_id: string; amount: string }[];
  trades: { position_id: string; shares_change: string; cash_position_id: string }[] };
export type HoldingsCoverage = "full" | "partial" | "unknown" | "stale";
export type FundOverlapContribution = {
  position_id: string;
  ticker: string | null;
  listing: string | null;
  fund_name: string | null;
  fund_weight: string | null;
  weight_in_fund: string;
  indirect_weight: string | null;
  indirect_value: string | null;
  as_of: string;
  source: string;
  coverage: HoldingsCoverage;
};
export type CompanyOverlap = {
  company_id: string;
  company_name: string;
  direct_value: string;
  direct_weight: string | null;
  indirect_value: string;
  indirect_weight: string | null;
  total_value: string | null;
  total_weight: string | null;
  coverage: HoldingsCoverage;
  source_dates: string[];
  contributing_funds: FundOverlapContribution[];
};
export type GuardrailReview = {
  settings: PortfolioSettings;
  companies: { company_id: string; company_name: string; current_weight: string | null; cap: string | null;
    status: string; excess_value: string | null; reduction_to_cash: string | null; explanation: string;
    direct_weight?: string | null; indirect_weight?: string | null; policy?: "direct_only" | "include_known_indirect" | null }[];
  active: { value: string | null; known_value: string; weight: string | null; budget: string | null; status: string;
    contributions: Record<string, string | null>; qualifications: string[] };
  baseline_comparison: { category: string; current_weight: string | null; baseline_weight: string | null; difference: string | null }[];
  qualifications: string[];
};
export type Review = {
  as_of: string; reviewed_at: string; reporting_currency: string; total_value: string | null; known_value: string;
  holdings_value: string | null; cash_value: string | null; complete: boolean;
  positions: { supplied: Position; value: string | null; local_value: string | null; weight: string | null;
    identity_status: string; identity: Identity | null; quote_used: Quote | null;
    quote_age_days: number | null; quote_age_at_capture_days: number | null; quote_age_at_request_days: number | null; fx_age_days: number | null;
    source_inputs_usable: boolean; fx_used: FX | null; issues: string[] }[];
  accounts: { id: string; name: string; total_value: string | null; known_value: string }[];
  direct_companies: { company_id: string; company_name: string; value: string | null; known_value: string;
    weight: string | null; position_ids: string[] }[];
  company_overlap: CompanyOverlap[];
  baseline: Baseline | null; guardrails: GuardrailReview | null; indirect_exposure: HoldingsCoverage | "none"; qualifications: string[]; calculation_basis: string;
  source_inputs_usable: boolean; sizing_eligible: false;
};
export type Recommendation = {
  preferred_action: "review_only" | "wait_for_inputs" | "no_action" | "add" | "hold" | "reduce" | "exit"; amount: AllocationAmount | null;
  evidence_ids?: string[]; reason: string; alternatives: { action: "clarify_inputs" | "keep_snapshot" | "no_action" | "add" | "hold" | "reduce" | "exit"; reason: string }[];
  downside: string; assumptions: string[]; uncertainty: string[]; what_could_change: string[];
};
export type ProposalReview = { changes: ProposedChanges; source: "user" | "model"; status: string;
  post_total_value: string | null; post_cash_value: string | null; positions: Review["positions"];
  guardrails: GuardrailReview | null; qualifications: string[] };
export type FundFacts = { position_id: string; as_of: string; source: string; source_url: string | null;
  exposure: string; annual_cost: string | null; income_yield: string | null };
export type ComparisonAlternative = { id: string; kind: "stock" | "etf" | "cash" | "short_bill" | "no_action"; position_id: string | null };
export type KnownEffects = { alternative_id: string; transaction_cost: string | null; terminal_tax: string | null; as_of: string; source: string };
export type ComparisonInput = { scope_position_ids: string[]; alternatives: ComparisonAlternative[];
  fund_facts: FundFacts[]; effects: KnownEffects[] };
export type ScenarioDriver = { position_id: string; annual_returns: string[] | null; return_basis: string; cost_basis: string;
  annual_rates: string[] | null; income_multipliers: string[] | null; reinvest: boolean; fx_multipliers: string[] };
export type ComparisonResult = { as_of: string; reporting_currency: string; horizon_years: number; starting_value: string | null;
  inputs: ComparisonInput; qualifications: string[]; calculation_basis: string;
  alternatives: { selection: ComparisonAlternative; position_ids: string[]; cases: { name: string;
    known_terminal_value: string | null; terminal_value: string | null; qualifications: string[];
    judgment: { name: string; drivers: ScenarioDriver[]; assumptions: string[]; downside: string; uncertainty: string[] };
    components: { position_id: string; local_currency: string; starting_local_value: string | null;
      fx_used: FX | null; terminal_local_value: string | null; known_terminal_value: string | null; fully_specified: boolean; qualifications: string[] }[];
  }[] }[] };
export type StockResult = { position_id: string; as_of: string; reporting_currency: string;
  research: { company_id: string; sector: string; cyclical: boolean | null; issues: string[];
    documents: { id: string; authority: string; url: string; title: string; published_on: string; as_of: string;
      excerpt: string; available: boolean; qa_available: boolean }[];
    facts: { id: string; metric: string; value: string | null; unit: string; currency: string | null;
      period_start: string | null; period_end: string; definition: string; document_ids: string[];
      filing_checked: boolean; notes_checked: boolean; custom_tags_checked: boolean; segments_checked: boolean }[] };
  judgments: { method: string; mid_cycle_context: string | null };
  cases: { name: string; terminal_metric: string | null; terminal_shares: string | null;
    terminal_price: string | null; known_terminal_value: string | null; present_value_per_share: string | null;
    sensitivity_prices: (string | null)[]; required_exit_multiple: string | null; qualifications: string[];
    judgment: { growth: string[]; margins: string[]; cash_conversion: string[]; reinvestment: string[];
      dilution: string[]; payout: string[]; return_on_equity: string[] | null; fx_multipliers: string[]; discount_rate: string;
      exit_multiple: string; exit_sensitivity: string[]; assumptions: string[]; uncertainty: string[] } }[];
  qualifications: string[]; calculation_basis: string };
export type NewCashInput = { amount: string | null; cash_position_id: string | null; confirmed: boolean; risk_context: string | null };
export type AllocationAmount = { minimum: string; maximum: string; currency: string; position_id: string };
export type AllocationResult = { context: NewCashInput;
  scan: { source_captured_at: string | null; scanned_at: string; as_of: string; source: string; issues: string[];
    candidates: { position: Position; as_of: string; source: string; source_url: string | null; signal: string }[] };
  researched: { position_id: string; reason: string }[]; stocks: StockResult[];
  judgment: { position_id: string; min_weight: string; max_weight: string; reason: string } | null;
  amount: AllocationAmount | null; previews: ProposalReview[]; missing_inputs: string[]; qualifications: string[] };
export type PortfolioReviewInput = { prior_theses: { company_id: string; as_of: string; thesis: string }[]; risk_context: string | null };
export type ReunderwritingResult = { context: PortfolioReviewInput; research: Record<string, StockResult["research"]>;
  stocks: StockResult[]; sizing: { position_id: string; cash_position_id: string; min_weight: string; max_weight: string; reason: string } | null; amount: AllocationAmount | null; previews: ProposalReview[]; missing_inputs: string[]; assessments: { position_id: string; status: "changed" | "unchanged" | "unknown";
    action: string; current_thesis: string; change_reason: string; downside: string; what_could_change: string[]; evidence_ids: string[] }[];
  qualifications: string[] };
export type Analysis = { status: "completed"; question: string; portfolio: Review; recommendation: Recommendation;
  proposals: ProposalReview[]; comparison: ComparisonResult | null; stock: StockResult | null; allocation: AllocationResult | null; reunderwriting: ReunderwritingResult | null; theme: ThemeResult | null };

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    signal: AbortSignal.timeout(150_000),
  });
  const data = await response.json();
  if (!response.ok) {
    const fields = Array.isArray(data.fields) ? ` Fields: ${data.fields.join(", ")}` : "";
    throw new Error((typeof data.detail === "string" ? data.detail : "The request could not be completed.") + fields);
  }
  return data as T;
}

export const actionLabel = (action: string) => ({
  review_only: "Review current exposure", wait_for_inputs: "Clarify missing inputs", no_action: "No action",
  add: "Add conditionally", hold: "Hold conditionally", reduce: "Reduce conditionally", exit: "Exit conditionally",
  clarify_inputs: "Clarify inputs", keep_snapshot: "Keep the dated snapshot",
}[action] || action);

// Display formatting only; all financial calculations come from the Python result.
export const valueLabel = (value: string | null, currency: string) => {
  if (value === null) return "Unknown";
  const [integer, fraction] = value.split(".");
  const decimals = fraction?.replace(/0+$/, "");
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${grouped}${decimals ? `.${decimals}` : ""} ${currency}`;
};
export const weightLabel = (value: string | null) => value === null ? "Unknown" :
  new Intl.NumberFormat("en-CA", { style: "percent", maximumFractionDigits: 2 }).format(Number(value));


export function portfolioReviewComparison(snapshot: Snapshot): ComparisonInput {
  const seen = new Set<string>();
  const companies = snapshot.positions.filter(row => {
    const key = row.company_id || row.id;
    if (row.kind !== "stock" || !(Number(row.shares) > 0) || row.currency !== "USD" || !["XNAS", "XNYS", "XASE"].includes(row.listing || "") || seen.has(key)) return false;
    seen.add(key); return true;
  });
  const alternatives: ComparisonAlternative[] = companies.map(row => ({ id: `company-${row.id}`, kind: "stock", position_id: row.id }));
  const fund = snapshot.positions.find(row => row.kind === "etf" && row.etf_role === "diversified");
  const cash = snapshot.positions.find(row => row.kind === "cash");
  if (fund) alternatives.push({ id: "fund", kind: "etf", position_id: fund.id });
  if (cash) alternatives.push({ id: "cash", kind: "cash", position_id: cash.id });
  alternatives.push({ id: "keep", kind: "no_action", position_id: null });
  return { scope_position_ids: snapshot.positions.map(row => row.id), alternatives, fund_facts: [], effects: [] };
}


export type ThemeInput = { risk_context?: string | null; name: string | null; mechanism: string | null; shortlist: string[]; max_candidates: number; max_tool_calls: number; confirmed: boolean };
export type ThemeTest = { position_id: string; conclusion: "supports" | "challenges" | "unknown"; explanation: string; evidence_ids: string[] };
export type ThemeResult = { context: ThemeInput; status: "awaiting_agreement" | "completed"; researched: string[]; stocks: StockResult[]; tests: ThemeTest[]; tool_calls_used: number; sizing: ReunderwritingResult["sizing"]; amount: AllocationAmount | null; previews: ProposalReview[]; missing_inputs: string[]; qualifications: string[] };


export function themeComparison(snapshot: Snapshot, theme: ThemeInput): ComparisonInput {
  const alternatives: ComparisonAlternative[] = snapshot.positions.filter(row => theme.shortlist.includes(row.id) && row.kind !== "cash").map(row => ({ id: `candidate-${row.id}`, kind: row.kind as "stock" | "etf", position_id: row.id }));
  const fund = snapshot.positions.find(row => row.kind === "etf" && row.etf_role === "diversified" && !theme.shortlist.includes(row.id));
  const cash = snapshot.positions.find(row => row.kind === "cash");
  if (fund) alternatives.push({ id: "fund", kind: "etf", position_id: fund.id });
  if (cash) alternatives.push({ id: "cash", kind: "cash", position_id: cash.id });
  alternatives.push({ id: "keep", kind: "no_action", position_id: null });
  const scope = snapshot.positions.filter(row => Number(row.shares) > 0 || Number(row.cash) > 0).map(row => row.id);
  return { scope_position_ids: scope.length ? scope : theme.shortlist, alternatives, fund_facts: [], effects: [] };
}

export function isThemeCandidate(row: Position): boolean {
  return row.kind === "etf" || row.kind === "stock" && row.currency === "USD" && ["XNAS", "XNYS", "XASE"].includes(row.listing || "");
}
