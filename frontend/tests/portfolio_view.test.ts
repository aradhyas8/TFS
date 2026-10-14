import { test, expect } from "@playwright/test";
import { allocation, draftEdits, holdingStatus, ruleChecks, valuationFor } from "../components/desk/portfolio";
import type { Analysis, Position, Review, Snapshot } from "../lib/contracts";

const ACME: Position = { id: "p1", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", shares: "10" };
const CASH: Position = { id: "c1", account_id: "tfsa", kind: "cash", currency: "CAD", cash: "500" };
const SNAPSHOT: Snapshot = { as_of: "2026-09-30", reporting_currency: "CAD", accounts: [{ id: "tfsa", name: "TFSA" }], positions: [ACME, CASH], fx: [] };

const valued = (positions: Position[], basis?: "committed" | "hypothetical") =>
  ({
    portfolio: {
      as_of: "2026-09-30", reviewed_at: "2026-10-09T12:00:00Z", reporting_currency: "CAD", total_value: "1850", complete: true,
      positions: positions.map(supplied => ({ supplied, value: "1", weight: "0.5", issues: [] })),
    },
    artifact_reference: basis ? { id: "a", label: "x", created_at: "", basis } : undefined,
  }) as unknown as Analysis;

test.describe("valuationFor", () => {
  test("a result stored with this revision values the portfolio", () => {
    const result = valued([ACME, CASH]);
    expect(valuationFor(result, { revision: 14, basis: "committed" }, 14, SNAPSHOT)).toBe(result.portfolio);
  });

  test("a result stored with another revision does not", () => {
    expect(valuationFor(valued([ACME, CASH]), { revision: 13, basis: "committed" }, 14, SNAPSHOT)).toBeNull();
  });

  test("a hypothetical draft analysis never values the committed portfolio", () => {
    expect(valuationFor(valued([ACME, CASH], "hypothetical"), null, 14, SNAPSHOT)).toBeNull();
    // A stored conversation result names its basis on its reference, with no revision.
    expect(valuationFor(valued([ACME, CASH]), { revision: null, basis: "hypothetical" }, 14, SNAPSHOT)).toBeNull();
  });

  test("without a revision, only a result of exactly these holdings is used", () => {
    const result = valued([ACME, CASH, { ...ACME, id: "benchmark-fund", shares: "0" }]);
    expect(valuationFor(result, null, 14, SNAPSHOT)).toBe(result.portfolio);
    expect(valuationFor(valued([{ ...ACME, shares: "12" }, CASH]), null, 14, SNAPSHOT)).toBeNull();
    expect(valuationFor(valued([ACME]), null, 14, SNAPSHOT)).toBeNull();
  });

  test("nothing to value without a result or a portfolio", () => {
    expect(valuationFor(null, null, 14, SNAPSHOT)).toBeNull();
    expect(valuationFor(valued([ACME, CASH]), { revision: 14, basis: "committed" }, 14, null)).toBeNull();
  });
});

test.describe("draftEdits", () => {
  const XEQT: Position = { id: "e1", account_id: "tfsa", kind: "etf", currency: "CAD", ticker: "XEQT", listing: "XTSE", shares: "5", etf_role: null };
  const committed = {
    snapshot: { ...SNAPSHOT, positions: [ACME, XEQT, CASH] },
    average_costs: {}, settings: { single_company_cap: "0.1", active_budget: null }, benchmark: null, unresolved: [], saved_at: null,
  };

  test("no edits when the draft is the committed portfolio", () => {
    expect(draftEdits(committed, { snapshot: committed.snapshot, settings: { single_company_cap: "0.1" }, benchmark: null })).toEqual([]);
  });

  test("names each unsaved edit, from what is saved to what the draft says", () => {
    const draft = {
      snapshot: { ...committed.snapshot, positions: [ACME, { ...XEQT, etf_role: "diversified" as const }, CASH] },
      settings: { single_company_cap: "0.12", active_budget: "0.6", baseline: { cash: "0.05" } },
      benchmark: { ticker: "VFV", listing: "XTSE", currency: "CAD" },
    };
    expect(draftEdits(committed, draft)).toEqual([
      "XEQT fund type Unknown to Diversified",
      "Company cap 10% to 12%",
      "Active budget not set to 60%",
      "Target cash not set to 5%",
      "Benchmark none to VFV",
    ]);
  });
});

test.describe("holding status and allocation", () => {
  const XEQT: Position = { id: "e1", account_id: "tfsa", kind: "etf", currency: "CAD", ticker: "XEQT", listing: "XTSE", shares: "5" };
  const BOLT: Position = { id: "p2", account_id: "tfsa", kind: "stock", currency: "CAD", ticker: "BOLT", listing: "XTSE", shares: "3" };
  const review = (weights: (string | null)[]) =>
    ({
      positions: [ACME, BOLT, XEQT, CASH].map((supplied, i) => ({ supplied, value: weights[i] === null ? null : "1", weight: weights[i], issues: [] })),
      direct_companies: [{ company_id: "acme", position_ids: ["p1"] }, { company_id: "bolt", position_ids: ["p2"] }],
      guardrails: { companies: [{ company_id: "acme", status: "breached" }, { company_id: "bolt", status: "within_limit" }] },
    }) as unknown as Review;

  test("each holding against the cap, by what the analysis checked", () => {
    const r = review(["0.4", "0.1", "0.3", "0.2"]);
    expect([ACME, BOLT, XEQT, CASH].map(row => holdingStatus(row, r))).toEqual(["over", "within", "fund", "cash"]);
  });

  test("a holding the analysis could not value says so, and nothing is checked before an analysis", () => {
    expect(holdingStatus(BOLT, review(["0.4", null, "0.3", "0.2"]))).toBe("not_valued");
    expect(holdingStatus(BOLT, null)).toBe("unchecked");
    expect(holdingStatus(BOLT, { ...review(["0.4", "0.1", "0.3", "0.2"]), guardrails: null })).toBe("unchecked");
  });

  test("allocation adds the analysis weights by kind, only when every holding has one", () => {
    const mix = allocation(review(["0.4", "0.1", "0.3", "0.2"]))!;
    expect(mix.stocks).toBeCloseTo(0.5);
    expect(mix.etfs).toBeCloseTo(0.3);
    expect(mix.cash).toBeCloseTo(0.2);
    expect(allocation(review(["0.4", null, "0.3", "0.2"]))).toBeNull();
  });
});

test.describe("ruleChecks", () => {
  const checked = (cap: string | null, active: string | null) =>
    ({ guardrails: { settings: { single_company_cap: cap, active_budget: active }, companies: [], active: {} } }) as unknown as Review;

  test("the analysis checks count only for the rules committed now", () => {
    const review = checked("0.15", "0.3");
    expect(ruleChecks(review, { single_company_cap: "0.15", active_budget: "0.3" })).toBe(review.guardrails);
    expect(ruleChecks(review, { single_company_cap: "0.12", active_budget: "0.3" })).toBeNull();
    expect(ruleChecks(checked("0.15", null), { single_company_cap: "0.15", active_budget: "0.3" })).toBeNull();
    expect(ruleChecks(null, { single_company_cap: "0.15" })).toBeNull();
  });
});
