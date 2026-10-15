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
    const dated = (result: Analysis) => { result.portfolio.holdings_as_of = SNAPSHOT.as_of; return result; };
    const result = dated(valued([ACME, CASH, { ...ACME, id: "benchmark-fund", shares: "0" }]));
    expect(valuationFor(result, null, 14, SNAPSHOT)).toBe(result.portfolio);
    expect(valuationFor(dated(valued([{ ...ACME, shares: "12" }, CASH])), null, 14, SNAPSHOT)).toBeNull();
    expect(valuationFor(dated(valued([ACME])), null, 14, SNAPSHOT)).toBeNull();
  });

  test("a legacy result with neither a revision nor a holdings date never values the portfolio", () => {
    // Same holdings, but nothing shows which holdings date it was made from, so equivalence can not be established.
    expect(valuationFor(valued([ACME, CASH]), null, 14, SNAPSHOT)).toBeNull();
    expect(valuationFor(valued([ACME, CASH]), { revision: null, basis: "committed" }, 14, SNAPSHOT)).toBeNull();
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

test.describe("valuationFor, without a revision, after a committed change", () => {
  const XEQT: Position = { id: "e1", account_id: "tfsa", kind: "etf", currency: "CAD", ticker: "XEQT", listing: "XTSE", shares: "5", etf_role: "diversified",
    mark: { value: "30", as_of: "2026-09-30", source: "Broker display" } };
  const RATE = { from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" };
  const committed: Snapshot = { ...SNAPSHOT, positions: [ACME, XEQT, CASH], fx: [RATE] };
  const answer = (snapshot: Snapshot, fx: typeof RATE | null = RATE) => {
    const result = valued(snapshot.positions);
    result.portfolio.reporting_currency = snapshot.reporting_currency;
    result.portfolio.holdings_as_of = snapshot.as_of;
    for (const row of result.portfolio.positions) row.fx_used = row.supplied.currency === "USD" ? fx : null;
    return result;
  };
  const changed = (positions: Position[], extra: Partial<Snapshot> = {}): Snapshot => ({ ...committed, positions, ...extra });

  test("the same inputs, written differently, still value the portfolio", () => {
    const result = answer(committed);
    expect(valuationFor(result, null, 15, committed)).toBe(result.portfolio);
    const rewritten = changed([{ ...ACME, shares: "10.00", company_id: undefined }, { ...XEQT, mark: { ...XEQT.mark!, value: "30.0" } }, { ...CASH, cash: "500.00" }]);
    expect(valuationFor(result, null, 15, rewritten)).toBe(result.portfolio);
  });

  test("a fund type change", () => {
    expect(valuationFor(answer(committed), null, 15, changed([ACME, { ...XEQT, etf_role: "sector_theme" }, CASH]))).toBeNull();
  });

  test("an identity or listing correction", () => {
    expect(valuationFor(answer(committed), null, 15, changed([{ ...ACME, listing: "XNYS" }, XEQT, CASH]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed([{ ...ACME, ticker: "ACMX" }, XEQT, CASH]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed([{ ...ACME, company_id: "acme-inc" }, XEQT, CASH]))).toBeNull();
  });

  test("a new price or mark", () => {
    expect(valuationFor(answer(committed), null, 15, changed([ACME, { ...XEQT, mark: { ...XEQT.mark!, value: "31" } }, CASH]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed([ACME, { ...XEQT, mark: { ...XEQT.mark!, as_of: "2026-10-01" } }, CASH]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed([{ ...ACME, mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } }, XEQT, CASH]))).toBeNull();
  });

  test("an exchange rate change, or a rate the analysis did not use", () => {
    expect(valuationFor(answer(committed), null, 15, changed(committed.positions, { fx: [{ ...RATE, rate: "1.40" }] }))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed(committed.positions, { fx: [{ ...RATE, as_of: "2026-10-01" }] }))).toBeNull();
    const before: Snapshot = { ...committed, fx: [] };
    expect(valuationFor(answer(before, null), null, 15, before)).not.toBeNull();
    expect(valuationFor(answer(before, null), null, 15, committed)).toBeNull();
  });

  test("a holdings, holdings date or reporting currency change", () => {
    expect(valuationFor(answer(committed), null, 15, changed([ACME, XEQT, { ...CASH, cash: "600" }]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed([ACME, XEQT, { ...CASH, account_id: "rrsp" }]))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed(committed.positions, { as_of: "2026-10-01" }))).toBeNull();
    expect(valuationFor(answer(committed), null, 15, changed(committed.positions, { reporting_currency: "USD" }))).toBeNull();
  });

  test("a revision-bound answer counts only for its own revision, whatever the holdings say", () => {
    const result = answer(committed);
    expect(valuationFor(result, { revision: 14 }, 15, committed)).toBeNull();
    expect(valuationFor(result, { revision: 15 }, 15, committed)).toBe(result.portfolio);
  });
});

test.describe("ruleChecks, after any guardrail setting changes", () => {
  const RULES = { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" as const, cash_is_deliberate_tilt: true,
    baseline: { stocks: "0.3", diversified_etfs: "0.5", sector_theme_etfs: "0.1", cash: "0.1" } };
  const checkedUnder = (settings: object) =>
    ({ guardrails: { settings, companies: [{ company_id: "acme", status: "within_limit" }], active: { status: "within_limit" } } }) as unknown as Review;

  test("the same rules, written differently, keep the checks", () => {
    const review = checkedUnder(RULES);
    expect(ruleChecks(review, { ...RULES, single_company_cap: "0.150", active_budget: "0.30" })).toBe(review.guardrails);
    const unset = checkedUnder({ single_company_cap: "0.15", baseline: null });
    expect(ruleChecks(unset, { single_company_cap: "0.15", active_budget: undefined, baseline: {} })).toBe(unset.guardrails);
    expect(ruleChecks(unset, { single_company_cap: "0.15", indirect_cap_policy: null, baseline: { cash: null } })).toBe(unset.guardrails);
  });

  test("any changed rule drops the older checks", () => {
    const review = checkedUnder(RULES);
    for (const settings of [
      { ...RULES, indirect_cap_policy: "include_known_indirect" as const },
      { ...RULES, indirect_cap_policy: null },
      { ...RULES, cash_is_deliberate_tilt: false },
      { ...RULES, cash_is_deliberate_tilt: null },
      { ...RULES, baseline: { ...RULES.baseline, stocks: "0.35" } },
      { ...RULES, baseline: { ...RULES.baseline, diversified_etfs: "0.45" } },
      { ...RULES, baseline: { ...RULES.baseline, sector_theme_etfs: null } },
      { ...RULES, baseline: { ...RULES.baseline, cash: "0.05" } },
      { ...RULES, baseline: null },
    ]) expect(ruleChecks(review, settings), JSON.stringify(settings)).toBeNull();
  });
});
