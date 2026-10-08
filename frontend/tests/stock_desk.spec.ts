import { test, expect, type Page } from "@playwright/test";

// The saved portfolio every question runs against. ACME has reviewed research fixtures on the test server.
const ACME = { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
  shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } };
const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      ACME,
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

/** Opens the desk on a saved portfolio and records every analysis request the page sends. */
async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: saved })).ok()).toBeTruthy();
  const sent: { question: string; stock?: { position_id: string } }[] = [];
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

// With the saved 15% cap, ACME (about 36%) breaks a rule, so the backend's checks make the answer review-only.
async function expectStockAnalysis(page: Page, headline = "ACME is over your 15% company cap.") {
  const memo = page.getByRole("region", { name: "Stock analysis" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText(headline);
  await expect(page.locator(".ctx")).toContainText("Stock analysis · ACME");
  await expect(page.locator(".echo")).toContainText("Stock analysis · ACME against your saved portfolio as of Sep 30, 2026");
  return memo;
}

/** The saved portfolio as stored, minus the save time. */
async function stored(page: Page) {
  const { saved_at: _, ...rest } = await (await page.request.get("/api/portfolio")).json();
  return rest;
}

test("a natural ticker question becomes Stock Analysis with every section, and never changes the portfolio", async ({ page }) => {
  const sent = await open(page);
  const before = await stored(page);
  await send(page, "What do you think about ACME?");
  const memo = await expectStockAnalysis(page);
  expect(sent.map(body => body.stock?.position_id)).toEqual(["tfsa-acme"]);

  // Conclusion first, then the sections in order.
  const labels = await memo.locator(":scope > .row > .lbl").allTextContents();
  expect(labels.slice(0, 13)).toEqual(["Recommendation", "Your position", "Thesis", "What changed", "Business quality", "Valuation", "Cases",
    "Portfolio impact", "Sizing", "Risks", "Alternatives", "Would change this", "Notes"]);
  await expect(memo.getByTestId("stock-valuation")).toContainText(/Today's price US\$100 is (below|between|above)/);
  await expect(memo.getByTestId("sizing-withheld")).toContainText("Stock Analysis gives a direction, not an amount");
  await expect(memo.getByTestId("stock-position")).toContainText(/10 shares in TFSA · C\$1,350 · \d+(\.\d)?% of your portfolio/);
  await expect(memo.getByTestId("stock-position")).toContainText("Your cap 15% per company");
  await expect(memo).toContainText("Existing exposures breach configured limits"); // the backend's checks, not the model
  await expect(memo).toContainText("Acme annual filing");
  await expect(memo).toContainText(/Base case US\$[\d.]+ per share, discounted to today/);
  await expect(memo.getByRole("list", { name: "Downside, base and upside" }).getByRole("listitem")).toHaveText([/^Downside/, /^Base/, /^Upside/]);
  await expect(memo.getByRole("list", { name: "Options compared" })).toContainText("ACME");
  await expect(memo).toContainText("Review the supplied settings and missing inputs");

  // Right panel: Evidence | Scenarios | Holdings | Guardrails.
  const details = page.getByRole("complementary", { name: "Details" });
  await expect(details.getByRole("tab")).toHaveText([/^Evidence/, "Scenarios", "Holdings", "Guardrails"]);
  await details.getByRole("tab", { name: /Evidence/ }).click();
  await expect(details.getByRole("list", { name: "Prices and rates used" })).toContainText("ACME");
  await details.getByRole("tab", { name: "Scenarios" }).click();
  await expect(details).toContainText("Acme Corp · per share, discounted to today");
  await expect(details.locator(".case")).toHaveCount(3);
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Single company · Acme Corp");

  // A stock question is analysis only: the saved holdings, costs and rules are untouched.
  await page.waitForTimeout(600); // longer than the desk's save debounce
  expect(await stored(page)).toEqual(before);
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
  await page.screenshot({ path: "artifacts/desk-stock-analysis.png", fullPage: true });
});

test("a company-name question becomes Stock Analysis for that holding", async ({ page }) => {
  const sent = await open(page, { ...SAVED, settings: null }); // no rules: the model's own call stands
  await send(page, "What changed with Acme?");
  const memo = await expectStockAnalysis(page, "Hold ACME.");
  await expect(memo.getByTestId("stock-position")).not.toContainText("Your cap");
  await expect(memo).toContainText("Hold conditionally while the company cases are assessed");
  await expect(memo).toContainText("A conditional reduction could lower company exposure.");
  expect(sent.map(body => [body.question, body.stock?.position_id])).toEqual([["What changed with Acme?", "tfsa-acme"]]);
});

test("/stock with a ticker runs Stock Analysis", async ({ page }) => {
  const sent = await open(page);
  await ask(page).fill("/stock");
  await expect(page.getByRole("listbox", { name: "Workflows" })).toContainText("/stock");
  await ask(page).press("Enter");
  await expect(ask(page)).toHaveValue("/stock ");
  await ask(page).pressSequentially("acme");
  await expect(page.getByRole("listbox", { name: "Workflows" })).toHaveCount(0);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expectStockAnalysis(page);
  expect(sent.map(body => body.stock?.position_id)).toEqual(["tfsa-acme"]);
});

test("clicking a holding in the rail offers its Stock Analysis", async ({ page }) => {
  const sent = await open(page);
  const rail = page.getByRole("navigation", { name: "Portfolio and decisions" });
  await expect(rail.getByRole("button", { name: "Analyze BROAD" })).toHaveCount(0); // funds aren't single companies
  await rail.getByRole("button", { name: "Analyze ACME" }).click();
  await expect(ask(page)).toHaveValue("/stock ACME");
  await expect(ask(page)).toBeFocused();
  expect(sent).toEqual([]); // offered, not sent
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expectStockAnalysis(page);
  expect(sent.map(body => body.stock?.position_id)).toEqual(["tfsa-acme"]);
});

test("review questions and /review still run a Portfolio Review", async ({ page }) => {
  const sent = await open(page);
  await send(page, "Review my portfolio");
  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Your portfolio breaks 2 of your rules.");
  await expect(page.locator(".ctx")).toContainText("Portfolio review");
  await ask(page).fill("/review");
  await ask(page).press("Enter");
  await expect(ask(page)).toHaveValue("Review my portfolio");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Your portfolio breaks 2 of your rules.");
  expect(sent.map(body => [body.question, body.stock ?? null])).toEqual([["Review my portfolio", null], ["Review my portfolio", null]]);
});

test("an ambiguous or unknown company asks one short question instead of guessing", async ({ page }) => {
  const robotics = { ...ACME, id: "tfsa-acmr", ticker: "ACMR", company_id: "acmr", company_name: "Acme Robotics Inc", shares: "5" };
  const sent = await open(page, { ...SAVED, snapshot: { ...SAVED.snapshot, positions: [...SAVED.snapshot.positions, robotics] } });
  await send(page, "What do you think about Acme?");
  const clarification = page.getByRole("region", { name: "Clarification" });
  await expect(clarification).toContainText('"Acme" matches more than one holding. Which one should I analyze?');
  await expect(clarification.getByRole("button")).toHaveText([/^ACME · Acme Corp/, /^ACMR · Acme Robotics Inc/]);

  await send(page, "What do you think about NVDA?");
  await expect(clarification).toContainText(`I couldn't match "NVDA" to a holding in your saved portfolio.`);
  await expect(clarification.getByRole("button", { name: "Review my whole portfolio instead" })).toBeVisible();
  expect(sent).toEqual([]); // nothing was guessed or sent

  await clarification.getByRole("button", { name: /^ACME/ }).click();
  await expectStockAnalysis(page);
  expect(sent.map(body => [body.question, body.stock?.position_id])).toEqual([["What do you think about NVDA?", "tfsa-acme"]]);
});
