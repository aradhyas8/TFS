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
export type GuardrailReview = {
  settings: PortfolioSettings;
  companies: { company_id: string; company_name: string; current_weight: string | null; cap: string | null;
    status: string; excess_value: string | null; reduction_to_cash: string | null; explanation: string }[];
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
  baseline: Baseline | null; guardrails: GuardrailReview | null; indirect_exposure: "unknown"; qualifications: string[]; calculation_basis: string;
  source_inputs_usable: boolean; sizing_eligible: false;
};
export type Recommendation = {
  preferred_action: "review_only" | "wait_for_inputs" | "no_action"; amount: null;
  reason: string; alternatives: { action: "clarify_inputs" | "keep_snapshot" | "no_action"; reason: string }[];
  downside: string; assumptions: string[]; uncertainty: string[]; what_could_change: string[];
};
export type ProposalReview = { changes: ProposedChanges; source: "user" | "model"; status: string;
  post_total_value: string | null; post_cash_value: string | null; positions: Review["positions"];
  guardrails: GuardrailReview | null; qualifications: string[] };
export type Analysis = { status: "completed"; question: string; portfolio: Review; recommendation: Recommendation; proposals: ProposalReview[] };

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
