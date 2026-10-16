import { test, expect, type Page } from "@playwright/test";
import { checkPortfolio, openDecisions, openDetails } from "./test_helpers";

const RICH_CSV = `row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
etf,fund,broker,Brokerage,BROAD,XNYS,,,80,,USD,100,2026-09-30,Fixture allocation,,,,
cash,cash,broker,Brokerage,,,,,,2000,USD,,,,,,,
`;
// The simple import: what a person knows. No cash row, no internal IDs, no listing codes or prices.
const SIMPLE_CSV = `account,ticker,shares,average_cost,type
Brokerage,BROAD,80,92.10,etf
`;

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const details = (page: Page) => page.getByRole("complementary", { name: "Details" });

async function importCsv(page: Page, csv: string, asOf = "2026-09-30") {
  const asOfInput = page.getByLabel("Holdings as of", { exact: true }).first();
  if (await asOfInput.isVisible()) {
    await asOfInput.fill(asOf);
  }
  const currInput = page.getByLabel("Reporting currency", { exact: true }).first();
  if (await currInput.isVisible()) {
    await currInput.fill("USD");
  }
  await page.getByLabel("Load portfolio CSV").first().setInputFiles({ name: "holdings.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);
}

async function start(page: Page) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/test/portfolio");
  await page.goto("/new-cash");
  await expect(page.getByRole("heading", { name: "Start with what you own." })).toBeVisible();
}

async function reviewAndConfirmRules(page: Page) {
  await openDetails(page);
  const panel = details(page);
  const reviewBtn = panel.getByTestId("review-changes");
  if (await reviewBtn.isVisible()) {
    await reviewBtn.click();
    const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
    await expect(dialog).toBeVisible();
    await dialog.getByTestId("confirm-proposal").click();
    await expect(dialog).toHaveCount(0);
  }
}

async function setRules(page: Page, fundType = true) {
  await openDetails(page);
  await details(page).getByRole("tab", { name: "Holdings" }).click();
  if (fundType) await details(page).getByRole("combobox", { name: "Fund type for BROAD" }).selectOption("diversified");
  await details(page).getByLabel("Single-company cap (%)").fill("15");
  await details(page).getByLabel("Active picks budget (%)").fill("30");
  await details(page).getByRole("combobox", { name: "Count fund holdings toward the cap" }).selectOption("direct_only");
  await details(page).getByRole("combobox", { name: "Cash above target is a deliberate choice" }).selectOption("false");
}

async function newCash(page: Page, riskContext: string | null) {
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("/");
  await expect(page.getByRole("listbox", { name: "Workflows" })).toBeVisible();
  await ask.press("Enter");
  await page.getByLabel("Amount", { exact: true }).fill("6000");
  await page.getByRole("combobox", { name: "Currency" }).selectOption("USD");
  await page.getByRole("combobox", { name: "Into account" }).selectOption({ label: "Brokerage" });
  if (riskContext) await page.getByLabel("Loss tolerance or withdrawal plans").fill(riskContext);
  await page.getByRole("textbox", { name: "Question" }).fill("I have this portfolio and $6,000. What should I do?");
}

async function setUp(page: Page, riskContext: string | null) {
  await start(page);
  await importCsv(page, RICH_CSV);
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("row", { name: /BROAD/ })).toBeVisible();
    await expect(view.getByRole("group", { name: "Total value" })).toContainText("Not valued yet");
  });
  await setRules(page);
  await reviewAndConfirmRules(page);
  await newCash(page, riskContext);
}

test("new cash: required inputs gate the run, then a cited recommendation with scenarios, guardrails, save and reopen", async ({ page }) => {
  await setUp(page, "Long horizon; equity losses are tolerable and no near-term withdrawal is planned.");
  const analyze = page.getByRole("button", { name: "Analyze", exact: true });
  await expect(analyze).toBeDisabled();
  await expect(page.getByText("Confirm the box above")).toBeVisible();
  await page.getByRole("checkbox", { name: /is new money/ }).check();
  await expect(analyze).toBeEnabled();
  await analyze.click();

  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Add US$800–2,400 to BROAD. Keep the rest as cash.");
  await expect(memo).toContainText("Add conditionally");
  await expect(memo).toContainText("Within your rules");
  await expect(memo).toContainText("A conditional fund addition offers diversified exposure");
  await expect(memo).toContainText("no company cap applies");
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("group", { name: "Total value" })).toContainText(/valued [A-Z][a-z]{2} \d+/);
  });

  await openDetails(page);
  await expect(details(page)).toContainText("No filings were researched for this answer");
  await memo.getByRole("button", { name: /Scenarios/ }).click();
  await expect(details(page).getByRole("table", { name: "Five-year comparison" })).toBeVisible();
  await expect(details(page)).toContainText("Starting value US$6,000");
  await details(page).getByRole("tab", { name: "Guardrails" }).click();
  await expect(details(page)).toContainText("Active picks");
  await expect(details(page)).toContainText("Within");

  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByTestId("saved-message")).toContainText("Saved to Decisions");
  await page.getByRole("radio", { name: "Kept it as cash" }).check();
  await page.getByLabel("Note for future you · optional").fill("Waiting for the next statement.");
  await page.getByRole("button", { name: "Record action" }).click();
  await expect(memo).toContainText("Saved and recorded");

  await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: /I have this portfolio and \$6,000/ }).first().click();
  const reopened = page.getByRole("region", { name: "Reopened decision" });
  await expect(reopened.getByTestId("historical-band")).toContainText("Historical");
  await expect(reopened).toContainText("Add US$800–2,400");
  await expect(reopened).toContainText("Waiting for the next statement.");
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
});

test("new cash: missing loss tolerance returns a needs-input answer that re-runs in the same conversation", async ({ page }) => {
  await setUp(page, null);
  await page.getByRole("checkbox", { name: /is new money/ }).check();
  await page.getByRole("button", { name: "Analyze", exact: true }).click();

  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Not enough to size this yet.");
  await expect(memo).toContainText("Supply decision-critical loss tolerance and withdrawal context.");
  await expect(memo).toContainText("weight after this cash is Unknown");
  await memo.getByLabel(/How large a drop could you hold through/).fill("Could hold through a 30% drop; no withdrawals planned.");
  await memo.getByRole("button", { name: "Re-run with these" }).click();
  // The form opens with the loss tolerance filled in; the new-money confirmation is given again, never carried over.
  await expect(page.getByLabel("Loss tolerance or withdrawal plans")).toHaveValue("Could hold through a 30% drop; no withdrawals planned.");
  await page.getByRole("checkbox", { name: /is new money/ }).check();
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  const results = page.getByRole("region", { name: "Analyst memo" });
  await expect(results).toHaveCount(2);
  await expect(results.last().getByRole("heading", { level: 2 })).toHaveText("Add US$800–2,400 to BROAD. Keep the rest as cash.");
});

test("portfolio: simple import asks only what's unknown, then holdings and rules are restored on reload and untouched by a new-cash run", async ({ page }) => {
  // Undated holdings are analyzed as of the browser's today; pin it to the fixture evidence date (2026-09-30).
  await page.clock.setFixedTime(new Date(2026, 8, 30, 12));
  await start(page);
  await importCsv(page, SIMPLE_CSV);
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("note").filter({ hasText: "holding needs you" })).toContainText("BROAD needs an exchange");
    await expect(view.getByRole("row", { name: /Cash/ })).toHaveCount(0);
  });
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");

  // A bare ticker is never assumed to be a US stock: the type came from the CSV, so only the exchange is asked.
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("/"); await ask.press("Enter");
  await expect(page.getByText("Identify 1 holding first")).toBeVisible();
  await openDetails(page);
  const needs = details(page).getByRole("group", { name: "Holdings to identify" });
  await details(page).getByRole("tab", { name: "Holdings" }).click();
  await expect(needs.getByRole("combobox", { name: "Type for BROAD" })).toHaveCount(0);
  await needs.getByRole("combobox", { name: "Exchange for BROAD" }).selectOption("XNYS");
  await needs.getByRole("button", { name: "Save BROAD" }).click();
  const preview = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(preview).toBeVisible();
  await preview.getByTestId("confirm-proposal").click();
  await expect(preview).toHaveCount(0);
  await expect(needs).toHaveCount(0);
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("note").filter({ hasText: "holding needs you" })).toHaveCount(0);
  });
  await openDetails(page);
  await details(page).getByRole("tab", { name: "Holdings" }).click();

  await expect(details(page)).toContainText("Average cost US$92.10");
  await details(page).getByRole("combobox", { name: "Fund type for BROAD" }).selectOption("diversified");
  await setRules(page, false);
  await reviewAndConfirmRules(page);
  await expect.poll(async () => {
    const res = await (await page.request.get("/api/portfolio")).json();
    const saved = res?.portfolio;
    return [saved?.snapshot?.positions[0]?.etf_role, saved?.settings?.single_company_cap, saved?.settings?.active_budget, saved?.settings?.baseline];
  }).toEqual(["diversified", "0.15", "0.3", null]);

  await page.reload();
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("row", { name: /BROAD/ })).toBeVisible();
    await expect(view.getByRole("group", { name: "Rules" })).toContainText("Company cap 15%");
    await expect(view.getByRole("group", { name: "Rules" })).toContainText("Active budget 30%");
  });
  await expect(page.locator(".ctx")).toContainText("Portfolio as of Sep 30, 2026");
  await openDetails(page);
  await details(page).getByRole("tab", { name: "Holdings" }).click();
  await expect(details(page).getByRole("combobox", { name: "Fund type for BROAD" })).toHaveValue("diversified");
  await expect(details(page)).toContainText("Average cost US$92.10");
  await expect(details(page).getByLabel("Single-company cap (%)")).toHaveValue("15");
  await expect(details(page).getByLabel("Active picks budget (%)")).toHaveValue("30");
  await expect(details(page).getByRole("combobox", { name: "Count fund holdings toward the cap" })).toHaveValue("direct_only");
  await expect(details(page).getByRole("combobox", { name: "Cash above target is a deliberate choice" })).toHaveValue("false");

  const before = await (await page.request.get("/api/portfolio")).json();
  await newCash(page, "Long horizon; equity losses are tolerable and no near-term withdrawal is planned.");
  await expect(page.getByRole("checkbox", { name: /is new money/ })).toHaveAccessibleName(/US\$6,000 is new money, not already in your portfolio as of Sep 30, 2026, and it will arrive in Brokerage/);
  await page.getByRole("checkbox", { name: /is new money/ }).check();
  await page.getByRole("button", { name: "Analyze", exact: true }).click();

  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Add US$400–1,800 to BROAD. Keep the rest as cash.");
  await expect(page.locator(".ctx")).toContainText("New cash · US$6,000 → Brokerage");
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("row", { name: /Cash/ })).toHaveCount(0);
  });
  expect(await (await page.request.get("/api/portfolio")).json()).toEqual(before);
});

test("portfolio: importing again replaces the saved holdings and date and keeps the rules", async ({ page }) => {
  await start(page);
  await importCsv(page, "account,ticker,shares,type\nBrokerage,BROAD,80,etf\n");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  await setRules(page, false);
  await reviewAndConfirmRules(page);
  await expect.poll(async () => (await (await page.request.get("/api/portfolio")).json())?.portfolio?.settings?.single_company_cap).toBe("0.15");

  await importCsv(page, "account,ticker,shares,type\nTFSA,XEQT.TO,120,etf\nTFSA,CASH,500,\n", "2026-10-05");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Oct 5, 2026");
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("row", { name: /XEQT/ })).toBeVisible();
    await expect(view.getByRole("row", { name: /BROAD/ })).toHaveCount(0);
  });

  await page.reload();
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Oct 5, 2026");
  await checkPortfolio(page, async view => {
    await expect(view.getByRole("row", { name: /XEQT/ })).toBeVisible();
    await expect(view.getByRole("row", { name: /Cash USD/ })).toBeVisible();
    await expect(view.getByRole("group", { name: "Rules" })).toContainText("Company cap 15%");
  });
  const res = await (await page.request.get("/api/portfolio")).json();
  const saved = res.portfolio;
  expect(saved.snapshot.accounts).toEqual([{ id: "tfsa", name: "TFSA" }]);
  expect(saved.unresolved).toEqual([]);
  expect(saved.average_costs).toEqual({});
});
