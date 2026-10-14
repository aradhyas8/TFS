import { test, expect } from "@playwright/test";
import { changeRows, isStale, unchangedGroups } from "../components/desk/proposal";
import type { DifferenceItem, PortfolioProposal, Snapshot } from "../lib/contracts";

const ACME = { id: "p1", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp", shares: "10" };
const BROAD = { id: "e1", account_id: "rrsp", kind: "etf", currency: "USD", ticker: "BROAD", listing: "XNYS", shares: "20", etf_role: "diversified" };
const CASH = { id: "c1", account_id: "tfsa", kind: "cash", currency: "USD", cash: "200" };
const SNAPSHOT = {
  as_of: "2026-09-30", reporting_currency: "CAD", accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
  positions: [ACME, BROAD, CASH], fx: [],
} as unknown as Snapshot;
const diff = (kind: string, path: string, before: unknown, after: unknown): DifferenceItem => ({ kind, path, description: "", before, after });
const proposal = (differences: DifferenceItem[], extra: Partial<PortfolioProposal> = {}) =>
  ({ id: "prop-1", base_revision: 3, origin: "manual", differences, warnings: [], status: "pending", created_at: "", after_artifact: {}, ...extra }) as unknown as PortfolioProposal;
const rows = (...differences: DifferenceItem[]) =>
  changeRows(proposal(differences), SNAPSHOT).map(row => [row.subject, row.detail, row.before, row.after, row.change]);

test.describe("changeRows: what changes, before and after", () => {
  test("holdings added and removed", () => {
    expect(rows(
      diff("position_added", "snapshot.positions[p2]", null, { ...ACME, id: "p2", ticker: "BOLT", company_name: null, shares: "3" }),
      diff("position_added", "snapshot.positions[c2]", null, { ...CASH, id: "c2", account_id: "rrsp", currency: "CAD", cash: "50" }),
      diff("position_removed", "snapshot.positions[p1]", ACME, null),
    )).toEqual([
      ["BOLT", "TFSA · holding", "Not held", "3 shares", "added"],
      ["Cash CAD", "RRSP · cash balance", "None", "C$50", "added"],
      ["ACME", "TFSA · holding", "10 shares", "Removed", "removed"],
    ]);
  });

  test("a changed holding names each field that changed", () => {
    expect(rows(
      diff("position_changed", "snapshot.positions[p1]", ACME, { ...ACME, shares: "12.5", listing: "XNYS" }),
      diff("position_changed", "snapshot.positions[e1]", BROAD, { ...BROAD, etf_role: "sector_theme" }),
      diff("position_changed", "snapshot.positions[c1]", CASH, { ...CASH, cash: "5140" }),
    )).toEqual([
      ["ACME", "TFSA · listing", "XNAS", "XNYS", "changed"],
      ["ACME", "TFSA · shares", "10", "12.5", "changed"],
      ["BROAD", "RRSP · fund type", "Diversified", "Sector or theme", "changed"],
      ["Cash USD", "TFSA · balance", "US$200", "US$5,140", "changed"],
    ]);
  });

  test("an identity correction: the unresolved holding is identified", () => {
    const NOVA = { account_id: "tfsa", ticker: "NOVA", shares: "7", average_cost: null, currency: null, listing: null, kind: "stock", candidates: [] };
    expect(rows(
      diff("position_added", "snapshot.positions[p9]", null, { id: "p9", account_id: "tfsa", kind: "stock", currency: "CAD", ticker: "NOVA", listing: "XTSE", shares: "7" }),
      diff("unresolved_removed", "unresolved[tfsa:NOVA]", NOVA, null),
    )).toEqual([
      ["NOVA", "TFSA · holding", "Not held", "7 shares", "added"],
      ["NOVA", "TFSA · to identify", "7 shares", "Identified", "removed"],
    ]);
  });

  test("a partial identity correction names what was learned about the holding", () => {
    const XYZ = { account_id: "tfsa", ticker: "XYZ", shares: "4", average_cost: "50", currency: null, listing: null, kind: null, candidates: [] };
    expect(rows(diff("unresolved_changed", "unresolved[tfsa:XYZ]", XYZ, { ...XYZ, listing: "XTSE", currency: "CAD" }))).toEqual([
      ["XYZ", "TFSA · to identify, exchange", "Not known", "XTSE", "changed"],
      ["XYZ", "TFSA · to identify, currency", "Not known", "CAD", "changed"],
    ]);
  });

  test("average costs, exchange rates, the holdings date and the reporting currency", () => {
    expect(rows(
      diff("cost_changed", "average_costs[p1]", "80", "85.5"),
      diff("cost_added", "average_costs[e1]", null, "40"),
      diff("fx_changed", "snapshot.fx[USD->CAD]", { from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30" }, { from_currency: "USD", to_currency: "CAD", rate: "1.37", as_of: "2026-10-01" }),
      diff("date_changed", "snapshot.as_of", "2026-09-30", "2026-10-01"),
      diff("currency_changed", "snapshot.reporting_currency", "CAD", "USD"),
    )).toEqual([
      ["ACME", "Average cost", "US$80", "US$85.50", "changed"],
      ["BROAD", "Average cost", "Not given", "US$40", "added"],
      ["USD to CAD", "Exchange rate", "1.35 (Sep 30, 2026)", "1.37 (Oct 1, 2026)", "changed"],
      ["Holdings date", "Portfolio", "Sep 30, 2026", "Oct 1, 2026", "changed"],
      ["Reporting currency", "Portfolio", "CAD", "USD", "changed"],
    ]);
  });

  test("rules one by one, and the benchmark", () => {
    expect(rows(
      diff("settings_changed", "settings", { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only", baseline: null },
        { single_company_cap: "0.12", active_budget: "0.3", indirect_cap_policy: "include_known_indirect", baseline: { cash: "0.05" } }),
      diff("benchmark_changed", "benchmark", null, { ticker: "VFV", listing: "XTSE", currency: "CAD" }),
    )).toEqual([
      ["Company cap", "Rule", "15%", "12%", "changed"],
      ["Fund holdings count toward the cap", "Rule", "No", "Yes", "changed"],
      ["Target cash", "Rule", "Not set", "5%", "changed"],
      ["Benchmark", "Rule", "None", "VFV", "changed"],
    ]);
  });

  test("an account added", () => {
    expect(rows(diff("account_added", "snapshot.accounts[fhsa]", null, { id: "fhsa", name: "FHSA" })))
      .toEqual([["FHSA", "Account", "None", "Added", "added"]]);
  });
});

test.describe("unchangedGroups", () => {
  test("names the parts of the portfolio the proposal leaves alone", () => {
    expect(unchangedGroups(proposal([diff("position_changed", "snapshot.positions[p1]", ACME, { ...ACME, shares: "8" })])))
      .toEqual(["Holdings date", "Rules and benchmark", "Average costs"]);
    expect(unchangedGroups(proposal([diff("benchmark_changed", "benchmark", null, {}), diff("cost_added", "average_costs[p1]", null, "1")])))
      .toEqual(["Holdings date"]);
  });

  test("a first portfolio has nothing to leave alone", () => {
    expect(unchangedGroups(proposal([], { base_revision: null }))).toEqual([]);
  });
});

test.describe("isStale", () => {
  test("a pending proposal is stale once the committed revision moved past its base", () => {
    expect(isStale(proposal([]), 3)).toBe(false);
    expect(isStale(proposal([]), 4)).toBe(true);
    expect(isStale(proposal([], { status: "stale" }), 3)).toBe(true);
    // A first-portfolio proposal is stale once a portfolio exists.
    expect(isStale(proposal([], { base_revision: null }), 1)).toBe(true);
    // Before the committed revision has loaded, nothing is called stale.
    expect(isStale(proposal([]), null)).toBe(false);
  });
});
