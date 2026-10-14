import { BENCHMARK_FUND, NEW_CASH_DESTINATION, type Analysis, type ArtifactReference, type BenchmarkSetting, type GuardrailReview, type PortfolioSettings, type Position, type Review, type SavedPortfolio, type Snapshot } from "../../lib/contracts";
import { pct } from "./format";

/** Rows an analysis adds for its own purposes: the benchmark fund, a new-cash destination, a candidate. */
export const isSynthetic = (id: string) => id === BENCHMARK_FUND || id === NEW_CASH_DESTINATION || id.startsWith("candidate-");

const sameHolding = (a: Position, b: Position) =>
  a.account_id === b.account_id && a.kind === b.kind && a.currency === b.currency && (a.shares ?? null) === (b.shares ?? null) && (a.cash ?? null) === (b.cash ?? null);

/** The valuation the portfolio page may show for the committed portfolio: an analysis of exactly this revision.
 *  A stored result names its revision; a desk answer names none, so it counts only when it valued exactly these
 *  holdings. A hypothetical draft analysis never values the committed portfolio, whether the result or its stored
 *  reference says so. Nothing is estimated. */
export function valuationFor(result: Analysis | null, stored: Partial<Pick<ArtifactReference, "revision" | "basis">> | null, revision: number | null, snapshot: Snapshot | null): Review | null {
  if (!result || !snapshot) return null;
  if (result.artifact_reference?.basis === "hypothetical" || stored?.basis === "hypothetical") return null;
  if (stored?.revision != null) return stored.revision === revision ? result.portfolio : null;
  const valuedRows = result.portfolio.positions.map(row => row.supplied).filter(row => !isSynthetic(row.id));
  const held = snapshot.positions.filter(row => !isSynthetic(row.id));
  const matches = valuedRows.length === held.length && held.every(row => {
    const other = valuedRows.find(item => item.id === row.id);
    return !!other && sameHolding(row, other);
  });
  return matches ? result.portfolio : null;
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

/** The analysis rule checks, only while they were made against the rules committed now: an older answer checked
 *  against another cap or budget would put its verdict beside the new rule. */
export function ruleChecks(review: Review | null, settings: PortfolioSettings): GuardrailReview | null {
  const checks = review?.guardrails;
  if (!checks) return null;
  const same = (a: string | null | undefined, b: string | null | undefined) => (a ?? null) === (b ?? null);
  return same(checks.settings.single_company_cap, settings.single_company_cap) && same(checks.settings.active_budget, settings.active_budget) ? checks : null;
}
