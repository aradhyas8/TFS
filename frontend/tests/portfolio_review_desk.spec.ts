import { test, expect } from "@playwright/test";

// A portfolio saved earlier: the app should open straight into it.
const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
        shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
      { id: "rrsp-broad", account_id: "rrsp", kind: "etf", currency: "USD", ticker: "BROAD", listing: "XNYS", etf_role: "diversified",
        shares: "20", mark: { value: "50", as_of: "2026-09-30", source: "Broker display" } },
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

test("portfolio review: the app opens on the saved portfolio and an ordinary question runs a review", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: SAVED })).ok()).toBeTruthy();
  const stored = await (await page.request.get("/api/portfolio")).json();

  await page.goto("/");
  const rail = page.getByRole("navigation", { name: "Portfolio and decisions" });
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
  await expect(rail).toContainText("ACME");
  await expect(rail).toContainText("BROAD");
  await expect(rail).toContainText("Rules · cap 15% · active 30%");
  await expect(page.getByRole("heading", { name: "Start with what you own." })).toHaveCount(0);
  await expect(page.locator(".ctx")).toContainText("Portfolio as of Sep 30, 2026");

  // No slash command: a plain question is a portfolio review.
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("Review my portfolio");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Your portfolio breaks 2 of your rules.");
  await expect(page.locator(".ctx")).toContainText("Portfolio review");
  await expect(page.locator(".echo")).toContainText("Portfolio review · your whole portfolio as of Sep 30, 2026");
  // Breached rules make the deterministic checks, not the model, set the reason.
  await expect(memo).toContainText("Existing exposures breach configured limits");
  await expect(memo.getByTestId("review-total")).toContainText("C$3,700");
  const weights = memo.getByRole("list", { name: "Weights" });
  await expect(weights.getByRole("listitem")).toHaveCount(3);
  await expect(weights.getByRole("listitem").first()).toContainText("36.5%");
  await expect(memo).toContainText("Breaks 2 of your rules");
  await expect(memo).toContainText("Largest companies you own directly: Acme Corp 36.5%");
  await expect(memo).toContainText("Inside your funds: unknown");
  await expect(memo).toContainText("configured limits cannot be waived by conviction");
  await expect(rail).toContainText(/Weights · .* analysis/);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Evidence" }).click();
  await expect(details.getByRole("list", { name: "Prices and rates used" })).toContainText("ACME");
  await expect(details.getByRole("list", { name: "Prices and rates used" })).toContainText("Broker display");
  await expect(details.getByRole("list", { name: "Prices and rates used" })).toContainText("USD/CAD");
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Your portfolio today (no amount was tested)");
  await expect(details).toContainText("Single company · Acme Corp");
  await expect(details).toContainText("Over · 36.5% / 15%");
  await expect(details).not.toContainText("before this cash");
  await details.getByRole("tab", { name: "Holdings" }).click();
  await expect(details).toContainText("Average cost US$80");
  await expect(details).toContainText("36.5%");
  await details.getByRole("tab", { name: "Scenarios" }).click();
  await expect(details).toContainText("doesn't project scenarios");

  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByTestId("saved-message")).toContainText("Saved to Decisions");
  await page.getByRole("radio", { name: "Left the portfolio as it is" }).check();
  await page.getByRole("button", { name: "Record action" }).click();
  await expect(memo).toContainText("Saved and recorded");

  // A review never changes the saved portfolio.
  expect(await (await page.request.get("/api/portfolio")).json()).toEqual(stored);
});

test("portfolio review: /review is a shortcut, and unidentified holdings block the question", async ({ page }) => {
  await page.request.delete("/api/test/portfolio");
  await page.request.put("/api/portfolio", { data: { ...SAVED, unresolved: [{ account_id: "tfsa", ticker: "MYST", shares: "5", average_cost: null, currency: null, listing: null, kind: null }] } });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Identify 1 holding" })).toBeVisible();
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("/rev");
  await ask.press("Enter");
  await expect(ask).toHaveValue("Review my portfolio");
  await expect(page.getByText("Identify 1 holding first")).toBeVisible();
  await expect(page.getByRole("button", { name: "Ask", exact: true })).toBeDisabled();
});

test("portfolio review: without rules the analyst's own answer is shown and nothing is checked", async ({ page }) => {
  await page.request.delete("/api/test/portfolio");
  await page.request.put("/api/portfolio", { data: { ...SAVED, settings: null } });
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("How concentrated am I across accounts?");
  await ask.press("Enter");
  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo).toContainText("Regarding 'How concentrated am I across accounts?'");
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Here is where your portfolio stands.");
  await expect(memo).toContainText("No rules set");
  await expect(memo.getByTestId("review-total")).toContainText("C$3,700");
  // A follow-up is a new review of the same saved portfolio.
  await expect(ask).toHaveAttribute("placeholder", "Ask a follow-up. Each question is a fresh analysis of your saved portfolio.");
});

test("benchmark fund: configure in holdings rules, verify comparison alternative, absent from rail and weights, persist and clear", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  const stockOnly = {
    snapshot: {
      as_of: "2026-09-30", reporting_currency: "CAD",
      accounts: [{ id: "tfsa", name: "TFSA" }],
      positions: [
        { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
          shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
        { id: "tfsa-cash-cad", account_id: "tfsa", kind: "cash", currency: "CAD", cash: "1000" },
      ],
      fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
    },
    average_costs: { "tfsa-acme": "80" },
    settings: { single_company_cap: "0.5" },
    unresolved: [],
  };
  expect((await page.request.put("/api/portfolio", { data: stockOnly })).ok()).toBeTruthy();

  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");

  // Open details panel on Holdings tab
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  // Benchmark fund rule input
  const benchmarkInput = page.getByRole("textbox", { name: "Benchmark ETF" });
  await expect(benchmarkInput).toBeVisible();
  await benchmarkInput.fill("SPY");
  await page.getByRole("button", { name: "Set" }).click();

  // Verify resolved benchmark info appears in rules
  await expect(details).toContainText("Current benchmark: SPY · SPDR S&P 500 ETF Trust");

  // Verify persistence outside settings
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.benchmark?.ticker;
  }).toBe("SPY");
  const stored = await (await page.request.get("/api/portfolio")).json();
  expect(stored.benchmark).toEqual({
    ticker: "SPY",
    listing: "XNYS",
    currency: "USD",
    name: "SPDR S&P 500 ETF Trust",
  });
  expect(stored.settings.benchmark).toBeUndefined();

  // Benchmark is absent from rail
  const rail = page.getByRole("navigation", { name: "Portfolio and decisions" });
  await expect(rail).not.toContainText("SPY");
  await expect(rail).toContainText("ACME");

  // Run stock analysis for ACME
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("What do you think about ACME?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  const memo = page.getByRole("region", { name: "Stock analysis" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();

  // Verify SPY appears as an option compared in the memo with unknown costs/facts
  const optionsCompared = memo.getByRole("list", { name: "Options compared" });
  await expect(optionsCompared.getByRole("listitem").filter({ hasText: "SPY" })).toContainText("Unknown");

  // Rail shows updated weights from analysis, which sum ACME and cash only (SPY is absent)
  await expect(rail).not.toContainText("SPY");
  await expect(rail).toContainText("57.4%");
  await expect(rail).toContainText("42.6%");

  // Check scenarios tab: benchmark SPY is present as comparison alternative
  await details.getByRole("tab", { name: "Scenarios" }).click();
  const compTable = details.getByRole("table", { name: "Five-year comparison" });
  await expect(compTable).toContainText("SPY");
  await expect(details).toContainText(/Values are before .* which are unknown and not assumed to be zero|Unknown where costs, fund facts or drivers weren't supplied/);

  // Clear benchmark in Holdings rules
  await details.getByRole("tab", { name: "Holdings" }).click();
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.benchmark;
  }).toBeNull();
});

