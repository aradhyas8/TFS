import { test, expect, type Page } from "@playwright/test";

// The saved portfolio every rebalance runs against. p1 (ACME) has reviewed research fixtures on the test server,
// and the scripted model re-underwrites it. ACME is about a third of the portfolio, over the saved 15% cap.
const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      { id: "p1", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
        shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
      { id: "tfsa-cash-usd", account_id: "tfsa", kind: "cash", currency: "USD", cash: "200" },
      { id: "rrsp-broad", account_id: "rrsp", kind: "etf", currency: "USD", ticker: "BROAD", listing: "XNYS", etf_role: "diversified",
        shares: "20", mark: { value: "50", as_of: "2026-09-30", source: "Broker display" } },
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { p1: "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};
const NO_RULES = { ...SAVED, settings: null };

type Sent = { question: string; stock?: unknown; portfolio_review?: unknown; comparison?: { alternatives: { id: string }[] } };

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: saved })).ok()).toBeTruthy();
  const sent: Sent[] = [];
  page.on("request", request => { if (request.url().endsWith("/api/analyze")) sent.push(request.postDataJSON()); });
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
  return sent;
}

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });
async function send(page: Page, question: string) {
  await ask(page).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}
/** The saved portfolio as stored, minus the save time. */
async function stored(page: Page) {
  const { saved_at: _, ...rest } = await (await page.request.get("/api/portfolio")).json();
  return rest;
}
const memo = (page: Page) => page.getByRole("region", { name: "Rebalance" });
const details = (page: Page) => page.getByRole("complementary", { name: "Details" });

test("a natural rebalance question runs the rebalance memo in order and never changes the portfolio", async ({ page }) => {
  const sent = await open(page);
  const before = await stored(page);
  await send(page, "Should I rebalance my portfolio?");
  await expect(memo(page).getByRole("heading", { level: 2 })).toHaveText("Reduce ACME. Keep the rest.");
  await expect(page.locator(".ctx")).toContainText("Rebalance");
  await expect(page.locator(".echo")).toContainText("Rebalance · your whole saved portfolio as of Sep 30, 2026");
  expect(sent).toHaveLength(1);
  expect(sent[0].portfolio_review).toEqual({ prior_theses: [], risk_context: null });
  expect(sent[0].comparison?.alternatives.map(alt => alt.id)).toEqual(["company-p1", "fund", "cash", "keep"]);

  const labels = await memo(page).locator(":scope > .row > .lbl").allTextContents();
  expect(labels).toEqual(["Recommendation", "Portfolio today", "What I would change", "Before → after", "Why", "Trade-offs and risks", "Alternatives", "Would change this", "Notes"]);
  await expect(memo(page).getByTestId("rebalance-verdict")).toHaveText("Change recommended");
  await expect(memo(page).getByTestId("review-total")).toContainText("C$3,970");
  await expect(memo(page).getByTestId("plan-reduce")).toContainText("ACME · TFSA");
  await expect(memo(page).getByTestId("plan-keep")).toContainText("BROAD · RRSP");
  await expect(memo(page).getByTestId("rebalance-why")).toContainText("Over your cap: Acme Corp");
  await expect(memo(page).getByTestId("rebalance-why")).toContainText("Weaker operating demand challenges the resilience thesis.");
  await expect(memo(page).getByTestId("rebalance-why")).toContainText("Company cap 15%");

  // Right panel: Evidence | Proposed portfolio | Holdings | Guardrails, opened on the proposed portfolio.
  await expect(details(page).getByRole("tab")).toHaveText([/^Evidence/, "Proposed portfolio", "Holdings", "Guardrails"]);
  await expect(details(page).getByRole("tab", { name: "Proposed portfolio" })).toHaveAttribute("aria-selected", "true");
  const table = details(page).getByRole("table", { name: "Before and after" });
  await expect(table.getByRole("row", { name: /ACME · TFSA/ })).toContainText("Lower");
  await expect(table.getByRole("row", { name: /Cash USD · TFSA/ })).toContainText("Higher");
  await expect(table.getByRole("row", { name: /BROAD · RRSP/ })).toContainText("Unchanged");
  await details(page).getByRole("tab", { name: /Evidence/ }).click();
  await expect(details(page)).toContainText("Acme annual filing");
  await details(page).getByRole("tab", { name: "Guardrails" }).click();
  await expect(details(page)).toContainText("Single company · Acme Corp");

  // Analysis only: the saved holdings, costs and rules are untouched.
  await page.waitForTimeout(600); // longer than the desk's save debounce
  expect(await stored(page)).toEqual(before);
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
  await page.screenshot({ path: "artifacts/desk-rebalance.png", fullPage: true });
});

test("/rebalance from the slash menu runs the rebalance", async ({ page }) => {
  const sent = await open(page);
  await ask(page).fill("/reb");
  await expect(page.getByRole("listbox", { name: "Workflows" })).toContainText("/rebalance");
  await ask(page).press("Enter");
  await expect(ask(page)).toHaveValue("/rebalance ");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(memo(page).getByRole("heading", { level: 2 })).toHaveText("Reduce ACME. Keep the rest.");
  expect(sent.map(body => [body.question, !!body.portfolio_review])).toEqual([["Should I rebalance my portfolio?", true]]);
});

test("no action is a complete answer, and no rules still gets a portfolio-level view", async ({ page }) => {
  const sent = await open(page, NO_RULES);
  await send(page, "Am I too concentrated?");
  await expect(memo(page).getByRole("heading", { level: 2 })).toHaveText("No change. Keep your portfolio as it is.");
  await expect(memo(page).getByTestId("rebalance-verdict")).toHaveText("No change recommended");
  await expect(memo(page)).toContainText("Nothing. No trade is proposed just because you asked.");
  await expect(memo(page)).toContainText("No trades proposed, so every weight stays where it is.");
  await expect(memo(page).getByTestId("rebalance-why")).toContainText("None set. This view doesn't depend on them; no limit was assumed or checked.");
  await expect(memo(page).getByTestId("plan-reduce")).toHaveCount(0);
  await expect(details(page).getByRole("table", { name: "Before and after" }).getByRole("row", { name: /ACME · TFSA/ })).toContainText("Unchanged");
  await expect(details(page)).toContainText("Nothing would move");
  expect(sent[0].portfolio_review).toBeTruthy();
});

test("a configured cap overrides a keep-everything answer with the path back within it", async ({ page }) => {
  await open(page);
  await send(page, "Am I too concentrated?");
  // Same scripted "no action" as above; the backend's guardrail check replaces it because ACME breaks the saved rules.
  await expect(memo(page).getByRole("heading", { level: 2 })).toHaveText("Your portfolio breaks 2 of your rules: bring ACME back within them.");
  await expect(memo(page).getByTestId("rebalance-verdict")).toHaveText("Change recommended");
  await expect(memo(page)).toContainText("Existing exposures breach configured limits");
  // The holding-level hold was also turned into a reduction by the cap check.
  await expect(memo(page).getByTestId("rebalance-why")).toContainText("Configured exposure limits require a forward reduction review");
  await expect(memo(page).getByTestId("plan-reduce")).toContainText("ACME · TFSA");
  await expect(details(page).getByRole("table", { name: "Before and after" }).getByRole("row", { name: /ACME · TFSA/ })).toContainText(/about C\$[\d,.]+ to cash/);
});

test("a directional reduction shows why no exact size is given", async ({ page }) => {
  const before = await (async () => { await open(page); return stored(page); })();
  await send(page, "What should I reduce?");
  await expect(memo(page).getByRole("heading", { level: 2 })).toHaveText("Reduce ACME. Keep the rest.");
  const withheld = memo(page).getByTestId("sizing-withheld");
  await expect(withheld).toContainText("Direction only: no exact size could be justified, so none is invented.");
  await expect(withheld).toContainText("Supply decision-critical loss tolerance and withdrawal context.");
  await expect(memo(page).getByRole("table", { name: "Before and after" }).getByRole("row", { name: /ACME · TFSA/ })).toContainText("direction only");
  await expect(details(page)).toContainText("Why there are no target weights");
  await page.waitForTimeout(600);
  expect(await stored(page)).toEqual(before);
});

test("descriptive or company questions are not routed to rebalance", async ({ page }) => {
  const sent = await open(page);
  await send(page, "How concentrated is my portfolio?");
  await expect(page.getByRole("region", { name: "Analyst memo" }).getByRole("heading", { level: 2 })).toHaveText("Your portfolio breaks 2 of your rules.");
  await expect(page.locator(".ctx")).toContainText("Portfolio review");
  await send(page, "Should I trim ACME?");
  await expect(page.getByRole("region", { name: "Stock analysis" })).toBeVisible();
  expect(sent.map(body => [body.question, !!body.portfolio_review, !!body.stock])).toEqual([
    ["How concentrated is my portfolio?", false, false], ["Should I trim ACME?", false, true]]);
});
