import { test, expect, type Page } from "@playwright/test";

const ACME = {
  id: "p1",
  account_id: "tfsa",
  kind: "stock",
  currency: "USD",
  ticker: "ACME",
  listing: "XNAS",
  company_id: "acme",
  company_name: "Acme Corp",
  shares: "10",
  mark: { value: "100", as_of: "2026-09-30", source: "Broker display" },
};

const SAVED = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      ACME,
      { id: "tfsa-cash-usd", account_id: "tfsa", kind: "cash", currency: "USD", cash: "200" },
      {
        id: "rrsp-broad",
        account_id: "rrsp",
        kind: "etf",
        currency: "USD",
        ticker: "BROAD",
        listing: "XNYS",
        etf_role: "diversified",
        shares: "20",
        mark: { value: "50", as_of: "2026-09-30", source: "Broker display" },
      },
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { p1: "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: saved })).ok()).toBeTruthy();
  const sentAnalyze: any[] = [];
  page.on("request", request => {
    if (request.url().endsWith("/api/analyze")) sentAnalyze.push(request.postDataJSON());
  });
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
  return sentAnalyze;
}

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });

async function send(page: Page, question: string) {
  await ask(page).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("I have C$2,000 to invest opens New Cash form prefilled with C$2,000 without sending an API request", async ({ page }) => {
  const sent = await open(page);

  await send(page, "I have C$2,000 to invest");

  // New cash form should be visible and prefilled
  const amountInput = page.getByLabel("Amount", { exact: true });
  await expect(amountInput).toBeVisible();
  await expect(amountInput).toHaveValue("2000");

  const currencySelect = page.getByRole("combobox", { name: "Currency" });
  await expect(currencySelect).toHaveValue("CAD");

  // The question itself was retained
  const questionInput = page.getByRole("textbox", { name: "Question" });
  await expect(questionInput).toHaveValue("I have C$2,000 to invest");

  // No analysis API call was made
  expect(sent).toEqual([]);
});

test("money phrasing without an amount opens empty New Cash form immediately", async ({ page }) => {
  const sent = await open(page);

  await send(page, "I have new cash to invest");

  const amountInput = page.getByLabel("Amount", { exact: true });
  await expect(amountInput).toBeVisible();
  await expect(amountInput).toHaveValue("");

  const questionInput = page.getByRole("textbox", { name: "Question" });
  await expect(questionInput).toHaveValue("I have new cash to invest");

  expect(sent).toEqual([]);
});

test("theme phrasing opens Theme form prefilled with the theme name", async ({ page }) => {
  const sent = await open(page);

  await send(page, "Is AI infrastructure worth a bet?");

  const themeNameInput = page.getByLabel("Theme name");
  await expect(themeNameInput).toBeVisible();
  await expect(themeNameInput).toHaveValue("AI infrastructure");

  const questionInput = page.getByRole("textbox", { name: "Question" });
  await expect(questionInput).toHaveValue("Is AI infrastructure worth a bet?");

  expect(sent).toEqual([]);
});

test("unowned ticker queries route directly to candidate lookup and Stock Analysis", async ({ page }) => {
  const sent = await open(page);

  await send(page, "What do you think about NVDA?");

  const memo = page.getByRole("region", { name: "Stock analysis" });
  await expect(memo).toBeVisible();
  await expect(page.locator(".ctx")).toContainText("Stock analysis · NVDA");
  await expect(page.locator(".echo")).toContainText("What do you think about NVDA?");

  // Verify analyze was called with candidate position in portfolio
  expect(sent.length).toBe(1);
  expect(sent[0].stock?.position_id).toContain("candidate-NVDA");
  expect(sent[0].portfolio.positions.some((p: any) => p.ticker === "NVDA")).toBe(true);
});

test("unknown company names with no ticker shape ask one short clarification question", async ({ page }) => {
  const sent = await open(page);

  await send(page, "What do you think about Shopify?");

  const clarification = page.getByRole("region", { name: "Clarification" });
  await expect(clarification).toBeVisible();
  await expect(clarification).toContainText('I couldn\'t match "Shopify" to a holding in your saved portfolio.');
  await expect(clarification).toContainText("Stock Analysis works on companies you own. Which one did you mean?");

  expect(sent).toEqual([]);
});

test("rebalance, owned stock, and portfolio review routes maintain precedence and behavior", async ({ page }) => {
  const sent = await open(page);

  // Owned stock query
  await send(page, "What do you think about ACME?");
  const stockMemo = page.getByRole("region", { name: "Stock analysis" });
  await expect(stockMemo).toBeVisible();
  await expect(page.locator(".ctx")).toContainText("Stock analysis · ACME");
  expect(sent.length).toBe(1);
  expect(sent[0].stock?.position_id).toBe("p1");

  // Rebalance query
  await page.getByRole("textbox", { name: /Ask about your portfolio or a holding/ }).fill("Should I rebalance my portfolio?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const rebalanceMemo = page.getByRole("region", { name: "Rebalance" });
  await expect(rebalanceMemo).toBeVisible();
  await expect(page.locator(".ctx")).toContainText("Rebalance");
  expect(sent.length).toBe(2);
  expect(sent[1].portfolio_review).toBeTruthy();

  // Portfolio review query
  await page.getByRole("textbox", { name: /Ask about your portfolio or a holding/ }).fill("Review my portfolio");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.locator(".ctx")).toContainText("Portfolio review");
  expect(sent.length).toBe(3);
});
