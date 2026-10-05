import { test, expect } from "@playwright/test";

async function enterNewCash(page: import("@playwright/test").Page) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/");
  await page.getByLabel("As-of date", { exact: true }).fill("2026-09-30");
  await page.getByLabel("Reporting currency", { exact: true }).fill("USD");
  const csv = `row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
etf,fund,broker,Brokerage,BROAD,XNYS,,,80,,USD,100,2026-09-30,Fixture allocation,,,,
cash,cash,broker,Brokerage,,,,,,2000,USD,,,,,,,
`;
  await page.getByLabel("Load portfolio CSV").setInputFiles({ name: "allocation.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await expect(page.getByRole("group", { name: "Position 1", exact: true })).toBeVisible();
  await page.getByRole("group", { name: "Position 1", exact: true }).getByRole("combobox", { name: "ETF classification for active budget", exact: true }).selectOption("diversified");
  await page.getByLabel("Single-company cap (fraction)", { exact: true }).fill("0.15");
  await page.getByLabel("Active budget (fraction)", { exact: true }).fill("0.3");
  await page.getByRole("combobox", { name: "Company cap policy for indirect exposure", exact: true }).selectOption("direct_only");
  await page.getByRole("combobox", { name: "Cash above baseline is a deliberate tilt", exact: true }).selectOption("false");
  await page.getByLabel("Analyze new cash", { exact: true }).check();
  await page.getByLabel("New cash amount", { exact: true }).fill("6000");
  await page.getByRole("combobox", { name: "New cash account and currency", exact: true }).selectOption("cash");
  await page.getByLabel("Loss tolerance and withdrawal needs", { exact: true }).fill("Long horizon; equity losses are tolerable and no near-term withdrawal is planned.");
  await page.getByLabel("Investment question", { exact: true }).fill("I have this portfolio and $6,000. What should I do?");
}

test("confirmed new cash displays a fresh bounded scan, comparisons and checked allocation range", async ({ page }) => {
  await enterNewCash(page);
  await page.getByLabel("I confirm this amount is new cash and this is its intended account").check();
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result).toContainText("Approximate allocation amount: 800 USD to 2,400 USD for fund");
  await expect(result.getByRole("region", { name: "New cash analysis", exact: true })).toContainText("Deep research: 0 candidates");
  await expect(result.getByRole("region", { name: "New cash analysis", exact: true })).toContainText("Fresh scan requested at");
  await expect(result.getByRole("region", { name: "Five-year conditional comparison" })).toContainText("6,000 USD");
  await expect(result.getByRole("region", { name: "Proposed change 2", exact: true })).toContainText("16,000 USD");
  await expect(result).toContainText("ETF indirect overlap is unknown");
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
});

test("unconfirmed new cash returns conditional direction without a confident amount", async ({ page }) => {
  await enterNewCash(page);
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result).toContainText("Allocation amount: not determined");
  await expect(result.getByRole("heading", { name: "Clarify missing inputs", exact: true })).toBeVisible();
  await expect(result).toContainText("Confirm a positive new-cash amount");
});
