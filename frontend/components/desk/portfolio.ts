import { BENCHMARK_FUND, NEW_CASH_DESTINATION, type Analysis, type ArtifactReference, type BenchmarkSetting, type GuardrailReview, type PortfolioSettings, type Position, type Review, type SavedPortfolio, type Snapshot } from "../../lib/contracts";
import { pct } from "./format";

/** Rows an analysis adds for its own purposes: the benchmark fund, a new-cash destination, a candidate. */
export const isSynthetic = (id: string) => id === BENCHMARK_FUND || id === NEW_CASH_DESTINATION || id.startsWith("candidate-");

/** Unset, empty and absent read alike; numbers compare by value, so "10" and "10.00" are the same holding. */
const text = (value: string | null | undefined) => value || null;
const amount = (value: string | null | undefined) => (value == null || value === "" ? null : Number(value));
const sameMark = (a: Position["mark"], b: Position["mark"]) =>
  !a || !b ? !a === !b
    : amount(a.value) === amount(b.value) && a.as_of === b.as_of && text(a.source) === text(b.source) && text(a.basis) === text(b.basis);
/** Every field of a holding the analysis reads to value, classify or check it: where it is held, what it is, how
 *  much of it and its own price. A change to any of them makes an older valuation stale. */
const sameHolding = (a: Position, b: Position) =>
  a.account_id === b.account_id && a.kind === b.kind && a.currency === b.currency &&
  text(a.ticker) === text(b.ticker) && text(a.listing) === text(b.listing) && text(a.company_id) === text(b.company_id) &&
  text(a.etf_role) === text(b.etf_role) && amount(a.shares) === amount(b.shares) && amount(a.cash) === amount(b.cash) && sameMark(a.mark, b.mark);

/** Without a revision, a result counts only when it shows it was made from exactly these inputs: the same holdings
 *  in every field above, the same reporting currency and holdings date, and, for each foreign currency held, the
 *  exchange rate on file now. A rate the analysis used that is not on file, or one on file it did not use, can not
 *  be shown to be the same input, so the result is not used. */
function sameInputs(review: Review, snapshot: Snapshot): boolean {
  if (review.reporting_currency !== snapshot.reporting_currency) return false;
  if (review.holdings_as_of && review.holdings_as_of !== snapshot.as_of) return false;
  const valued = review.positions.filter(row => !isSynthetic(row.supplied.id));
  const held = snapshot.positions.filter(row => !isSynthetic(row.id));
  if (valued.length !== held.length || !held.every(row => valued.some(item => item.supplied.id === row.id && sameHolding(row, item.supplied)))) return false;
  return valued.every(row => {
    if (row.supplied.currency === snapshot.reporting_currency) return true;
    const onFile = snapshot.fx.find(rate => rate.from_currency === row.supplied.currency && rate.to_currency === snapshot.reporting_currency);
    const used = row.fx_used;
    return !onFile || !used ? !onFile && !used : amount(onFile.rate) === amount(used.rate) && onFile.as_of === used.as_of;
  });
}

/** The valuation the portfolio page may show for the committed portfolio: an analysis of exactly this revision.
 *  A result bound to a revision counts only for that revision. A result with none counts only when its inputs are
 *  demonstrably the committed ones. A hypothetical draft analysis never values the committed portfolio, whether the
 *  result or its stored reference says so. Nothing is estimated. */
export function valuationFor(result: Analysis | null, stored: Partial<Pick<ArtifactReference, "revision" | "basis">> | null, revision: number | null, snapshot: Snapshot | null): Review | null {
  if (!result || !snapshot) return null;
  if (result.artifact_reference?.basis === "hypothetical" || stored?.basis === "hypothetical") return null;
  if (stored?.revision != null) return stored.revision === revision ? result.portfolio : null;
  return sameInputs(result.portfolio, snapshot) ? result.portfolio : null;
}

const ROLE: Record<string, string> = { diversified: "Diversified", sector_theme: "Sector or theme" };
const percent = (value: string | null | undefined) => (value == null ? "not set" : pct(value));
const yesNo = (value: boolean | null | undefined) => (value == null ? "not set" : value ? "Yes" : "No");
const SETTINGS: [string, (s: PortfolioSettings) => string][] = [
  ["Company cap", s => percent(s.single_company_cap)],
  ["Active budget", s => percent(s.active_budget)],
  ["Fund holdings count toward the cap", s => (s.indirect_cap_policy ? (s.indirect_cap_policy === "include_known_indirect" ? "Yes" : "No") : "not set")],
  ["Cash above target is deliberate", s => yesNo(s.cash_is_deliberate_tilt)],
  ["Target stocks", s => percent(s.baseline?.stocks)],
  ["Target diversified ETFs", s => percent(s.baseline?.diversified_etfs)],
  ["Target sector and theme ETFs", s => percent(s.baseline?.sector_theme_etfs)],
  ["Target cash", s => percent(s.baseline?.cash)],
];

/** Each unsaved edit in words, from what is committed to what the draft says. The draft can change fund types,
 *  rules and the benchmark; holdings themselves change only through an import or an identity correction. */
export function draftEdits(
  committed: SavedPortfolio | null,
  draft: { snapshot: Snapshot | null; settings: PortfolioSettings; benchmark: BenchmarkSetting | null }
): string[] {
  if (!committed || !draft.snapshot) return [];
  const edits: string[] = [];
  for (const row of draft.snapshot.positions) {
    const before = committed.snapshot.positions.find(item => item.id === row.id);
    if (before && row.kind === "etf" && (before.etf_role ?? null) !== (row.etf_role ?? null))
      edits.push(`${row.ticker || row.id} fund type ${ROLE[before.etf_role ?? ""] || "Unknown"} to ${ROLE[row.etf_role ?? ""] || "Unknown"}`);
  }
  const saved = committed.settings || {};
  for (const [label, show] of SETTINGS) if (show(saved) !== show(draft.settings)) edits.push(`${label} ${show(saved)} to ${show(draft.settings)}`);
  const was = committed.benchmark?.ticker ?? "none", now = draft.benchmark?.ticker ?? "none";
  if (was !== now) edits.push(`Benchmark ${was} to ${now}`);
  return edits;
}

export type HoldingStatus = "over" | "within" | "fund" | "cash" | "not_valued" | "unchecked";

/** A holding against the company cap, as the analysis checked it. Before an analysis nothing is checked. */
export function holdingStatus(row: Position, review: Review | null): HoldingStatus {
  if (row.kind === "cash") return "cash";
  const valued = review?.positions.find(item => item.supplied.id === row.id);
  if (review && (!valued || valued.value === null)) return "not_valued";
  if (row.kind === "etf") return "fund";
  const company = review?.direct_companies.find(item => item.position_ids.includes(row.id));
  const checked = company && review?.guardrails?.companies.find(item => item.company_id === company.company_id);
  return checked?.status === "breached" ? "over" : checked?.status === "within_limit" ? "within" : "unchecked";
}

/** Stocks, ETFs and cash as shares of the portfolio, added up from the analysis weights. Shown only when every
 *  holding was weighted, so a partial valuation is never drawn as the whole. */
export function allocation(review: Review): { stocks: number; etfs: number; cash: number } | null {
  const rows = review.positions.filter(row => !isSynthetic(row.supplied.id));
  if (!rows.length || rows.some(row => row.weight === null)) return null;
  const sum = (kind: Position["kind"]) => rows.filter(row => row.supplied.kind === kind).reduce((total, row) => total + Number(row.weight), 0);
  return { stocks: sum("stock"), etfs: sum("etf"), cash: sum("cash") };
}

/** Every setting the guardrail checks read, in one form: unset and absent read alike, an empty target mix is no
 *  mix, and numbers compare by value. A policy or tilt left unset is not the same as one set to No: the checks
 *  treat them differently. */
const guardrailSettings = (s: PortfolioSettings) => {
  const mix = [s.baseline?.stocks, s.baseline?.diversified_etfs, s.baseline?.sector_theme_etfs, s.baseline?.cash].map(amount);
  return JSON.stringify([amount(s.single_company_cap), amount(s.active_budget), s.indirect_cap_policy ?? null, s.cash_is_deliberate_tilt ?? null,
    mix.every(value => value === null) ? null : mix]);
};

/** The analysis rule checks, only while they were made under every rule committed now: an older answer checked
 *  under another cap, budget, fund policy, cash tilt or target mix would put its verdict beside the new rules. */
export function ruleChecks(review: Review | null, settings: PortfolioSettings): GuardrailReview | null {
  const checks = review?.guardrails;
  return checks && guardrailSettings(checks.settings) === guardrailSettings(settings) ? checks : null;
}
