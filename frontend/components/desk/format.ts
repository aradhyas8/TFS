// Display formatting only. Every value comes from the backend or the user's snapshot.
import { actionLabel, type Analysis, type NewCashInput, type Position, type Snapshot, type StockResult } from "../../lib/contracts";

const SYMBOL: Record<string, string> = { CAD: "C$", USD: "US$", EUR: "€", GBP: "£" };

export function money(value: string | null | undefined, currency: string): string {
  if (value === null || value === undefined) return "Unknown";
  const negative = value.startsWith("-");
  const [integer, fraction] = value.replace("-", "").split(".");
  const decimals = fraction?.replace(/0+$/, "").slice(0, 2);
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const number = `${grouped}${decimals ? `.${decimals.padEnd(2, "0")}` : ""}`;
  const symbol = SYMBOL[currency];
  return `${negative ? "−" : ""}${symbol ? `${symbol}${number}` : `${number} ${currency}`}`;
}

export function range(min: string, max: string, currency: string): string {
  return min === max ? money(min, currency) : `${money(min, currency)}–${money(max, currency).replace(SYMBOL[currency] || "", "")}`;
}

export const pct = (value: string | null | undefined) => value === null || value === undefined ? "Unknown" :
  new Intl.NumberFormat("en-CA", { style: "percent", maximumFractionDigits: 1 }).format(Number(value));

export function shortDate(value: string | null | undefined): string {
  if (!value) return "Unknown date";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(date.getTime())) return value;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleDateString("en-CA", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

/** Always with the year: the loaded portfolio date must be exact. */
export function fullDate(value: string | null | undefined): string {
  if (!value) return "Unknown date";
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });
}

/** "C$2,000 → TFSA" from the user's new-cash input. */
export function newCashLabel(newCash: NewCashInput, snapshot: Snapshot | null): string | null {
  if (!newCash.amount || !newCash.account_id) return null;
  return `${money(newCash.amount, newCash.currency || snapshot?.reporting_currency || "")} → ${accountName(snapshot, newCash.account_id)}`;
}

export const clock = (value: string) => new Date(value).toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit" });

export function positionName(position: Position | undefined, fallback: string): string {
  if (!position) return fallback;
  if (position.kind === "cash") return `${position.currency} cash`;
  return position.company_name || position.ticker || fallback;
}

export function accountName(snapshot: Snapshot | null, accountId: string | undefined): string {
  return snapshot?.accounts.find(account => account.id === accountId)?.name || "Account";
}

/** The memo's one-sentence answer, built only from the backend's action and amount. */
export function answerSentence(action: string, amount: Analysis["recommendation"]["amount"], target: string, newCashAmount: string | null): string {
  if (action === "add" && amount) {
    const rest = newCashAmount !== null && Number(amount.maximum) < Number(newCashAmount) ? " Keep the rest as cash." : "";
    return `Add ${range(amount.minimum, amount.maximum, amount.currency)} to ${target}.${rest}`;
  }
  if (action === "wait_for_inputs" || action === "clarify_inputs") return "Not enough to size this yet.";
  if (action === "no_action") return "Keep the new cash as cash for now.";
  if (action === "review_only") return "Here is where your portfolio stands.";
  return `${actionLabel(action)}.`;
}

/** Per-share case values are in the currency of the reported metric they were calculated from. */
export function caseCurrency(stock: StockResult, fallback: string): string {
  const id = stock.judgments.metric_fact_id || stock.judgments.revenue_fact_id;
  return stock.research.facts.find(fact => fact.id === id)?.currency || fallback;
}

/** Large reported figures for reading: US$63.9B, US$812M, 4.85B shares. Exact values stay in the data. */
export function compact(value: string | null | undefined, currency: string | null): string {
  if (value === null || value === undefined) return "Unknown";
  const number = Number(value);
  const size = Math.abs(number);
  const [scaled, suffix] = size >= 1e12 ? [number / 1e12, "T"] : size >= 1e9 ? [number / 1e9, "B"] : size >= 1e6 ? [number / 1e6, "M"] : [number, ""];
  const digits = suffix ? (Math.abs(scaled) >= 100 ? 0 : Math.abs(scaled) >= 10 ? 1 : 2) : 0;
  const text = `${scaled < 0 ? "−" : ""}${Math.abs(scaled).toLocaleString("en-CA", { maximumFractionDigits: digits })}${suffix}`;
  return currency ? `${SYMBOL[currency] || `${currency} `}${text}` : `${text} shares`;
}

/** The price against the discounted cases, in words. */
export const POSITION_LABEL: Record<string, string> = { below_downside: "below even the downside case", downside_to_base: "between the downside and base cases",
  base_to_upside: "between the base and upside cases", above_upside: "above even the upside case", unknown: "not comparable with the cases" };

/** Past-tense label for an action the user confirmed they took. */
export const doneLabel = (action: string) => ({ add: "added", no_action: "no action", hold: "held", reduce: "reduced", exit: "exited" }[action] || actionLabel(action).toLowerCase());

export const durationLabel = (ms: number) => {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
};
