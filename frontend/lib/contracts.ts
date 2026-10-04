export type Account = { id: string; name: string };
export type Mark = { value: string; as_of: string; source: string };
export type Position = {
  id: string; account_id: string; kind: "stock" | "etf" | "cash"; currency: string;
  ticker?: string | null; listing?: string | null; company_id?: string | null;
  company_name?: string | null; shares?: string | null; cash?: string | null; mark?: Mark | null;
};
export type FX = { from_currency: string; to_currency: string; rate: string; as_of: string; source: string };
export type Snapshot = { as_of: string; reporting_currency: string; accounts: Account[]; positions: Position[]; fx: FX[] };
export type Review = {
  as_of: string; reporting_currency: string; total_value: string | null; known_value: string;
  holdings_value: string | null; cash_value: string | null; complete: boolean;
  positions: { supplied: Position; value: string | null; local_value: string | null; weight: string | null;
    identity_status: string; fx_used: FX | null; issues: string[] }[];
  accounts: { id: string; name: string; total_value: string | null; known_value: string }[];
  direct_companies: { company_id: string; company_name: string; value: string | null; known_value: string;
    weight: string | null; position_ids: string[] }[];
  baseline: null; guardrails: null; indirect_exposure: "unknown"; qualifications: string[]; calculation_basis: string;
};
export type Recommendation = {
  preferred_action: "review_only" | "wait_for_inputs" | "no_action"; amount: null;
  reason: string; alternatives: { action: "clarify_inputs" | "keep_snapshot" | "no_action"; reason: string }[];
  downside: string; assumptions: string[]; uncertainty: string[]; what_could_change: string[];
};
export type Analysis = { status: "completed"; question: string; portfolio: Review; recommendation: Recommendation };

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
