// Display formatting only. Every value comes from the backend or the user's snapshot.
import { actionLabel, type Analysis, type NewCashInput, type Position, type Snapshot } from "../../lib/contracts";

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
  return `${actionLabel(action)}.`;
}

/** Past-tense label for an action the user confirmed they took. */
export const doneLabel = (action: string) => ({ add: "added", no_action: "no action", hold: "held", reduce: "reduced", exit: "exited" }[action] || actionLabel(action).toLowerCase());

export const durationLabel = (ms: number) => {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
};
