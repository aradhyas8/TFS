import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const csvPath = path.resolve("../examples/portfolio.csv");

test.beforeEach(async ({ page }) => {
  await page.route("**/*", route => {
    const host = new URL(route.request().url()).hostname;
    return ["127.0.0.1", "localhost"].includes(host) ? route.continue() : route.abort();
  });
  await page.goto("/classic");
  await page.getByLabel("As-of date", { exact: true }).fill("2026-09-30");
});

async function loadCSV(page: Page) {
  await page.getByLabel("Load portfolio CSV").setInputFiles(csvPath);
  await expect(page.getByLabel("Account 1 name", { exact: true })).toHaveValue("TFSA");
  await expect(page.getByLabel("Account 2 name", { exact: true })).toHaveValue("Brokerage");
}

test("full ETF sponsor holdings look-through surfaces overlap without double counting", async ({ page }) => {
  await loadCSV(page);
  const pos2 = page.getByRole("group", { name: "Position 2", exact: true });
  await pos2.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await pos2.getByLabel("ETF classification for active budget").selectOption("diversified");
  await pos2.getByLabel("Ticker", { exact: true }).fill("BROAD");
  await pos2.getByLabel("Mark source label", { exact: true }).fill("Fixture sponsor full");

  await page.getByLabel("Investment question").fill("Review indirect overlap");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);

  // Portfolio total remains 3,600 CAD (direct positions only, no double counting)
  await expect(page.getByTestId("portfolio-total")).toContainText("3,600 CAD");

  // Check indirect exposure badge
  await expect(page.getByTestId("indirect-exposure-badge")).toHaveText("full");

  // Check overlap table row for Acme
  const acmeRow = page.getByTestId("overlap-row-acme");
  await expect(acmeRow).toBeVisible();
  await expect(acmeRow).toContainText("Acme");
  await expect(acmeRow).toContainText("1,300 CAD"); // direct
  await expect(acmeRow).toContainText("100 CAD");   // indirect (10% of 1000 CAD)
  await expect(acmeRow).toContainText("1,400 CAD"); // total
  await expect(acmeRow).toContainText("full");      // coverage badge
  await expect(acmeRow).toContainText("BROAD (p2)");
  await expect(acmeRow).toContainText("2026-09-30");
});

test("indirect_cap_policy governs guardrail checks with known indirect exposure", async ({ page }) => {
  await loadCSV(page);
  const pos2 = page.getByRole("group", { name: "Position 2", exact: true });
  await pos2.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await pos2.getByLabel("ETF classification for active budget").selectOption("diversified");
  await pos2.getByLabel("Ticker", { exact: true }).fill("BROAD");
  await pos2.getByLabel("Mark source label", { exact: true }).fill("Fixture sponsor full");

  // Set single company cap to 0.37 (37%)
  await page.getByLabel("Single-company cap (fraction)").fill("0.37");

  // 1. include_known_indirect: total Acme is 1400 / 3600 = 38.89% > 37% -> breached
  await page.getByLabel("Company cap policy for indirect exposure").selectOption("include_known_indirect");
  await page.getByLabel("Investment question").fill("Review with include_known_indirect");
  let returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  let response = await returned;
  expect(response.status()).toBe(200);

  const guardrailsTable = page.getByRole("table", { name: /Current company cap checks/ });
  await expect(guardrailsTable).toContainText("breached");
  await expect(guardrailsTable).toContainText("Direct: 36.11%");
  await expect(guardrailsTable).toContainText("Indirect: 2.78%");
  await expect(guardrailsTable).toContainText("known indirect ETF overlap exceeds the configured cap");

  // 2. direct_only: direct Acme is 1300 / 3600 = 36.11% <= 37% -> within_limit
  await page.getByLabel("Company cap policy for indirect exposure").selectOption("direct_only");
  await page.getByLabel("Investment question").fill("Review with direct_only");
  returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  response = await returned;
  expect(response.status()).toBe(200);

  await expect(guardrailsTable).toContainText("within limit");
  await expect(guardrailsTable).toContainText("Direct exposure is within the configured cap");
});

test("partial coverage qualifies conclusion but does not block within_limit", async ({ page }) => {
  await loadCSV(page);
  const pos2 = page.getByRole("group", { name: "Position 2", exact: true });
  await pos2.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await pos2.getByLabel("ETF classification for active budget").selectOption("diversified");
  await pos2.getByLabel("Ticker", { exact: true }).fill("BROAD");
  await pos2.getByLabel("Mark source label", { exact: true }).fill("Fixture sponsor partial");

  await page.getByLabel("Single-company cap (fraction)").fill("0.45");
  await page.getByLabel("Company cap policy for indirect exposure").selectOption("include_known_indirect");

  await page.getByLabel("Investment question").fill("Review partial look-through");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);

  // Status badge should be partial
  await expect(page.getByTestId("indirect-exposure-badge")).toHaveText("partial");

  // Cap check should be within limit (38.89% <= 45%), qualified by partial coverage
  const guardrailsTable = page.getByRole("table", { name: /Current company cap checks/ });
  await expect(guardrailsTable).toContainText("within limit");
  await expect(guardrailsTable).toContainText("Incomplete ETF look-through");
});

test("stale coverage is honestly labeled", async ({ page }) => {
  await loadCSV(page);
  const pos2 = page.getByRole("group", { name: "Position 2", exact: true });
  await pos2.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await pos2.getByLabel("ETF classification for active budget").selectOption("diversified");
  await pos2.getByLabel("Ticker", { exact: true }).fill("BROAD");
  await pos2.getByLabel("Mark source label", { exact: true }).fill("Fixture sponsor stale");

  await page.getByLabel("Investment question").fill("Review stale look-through");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);

  // Status badge should be stale and explanatory note should be visible
  await expect(page.getByTestId("indirect-exposure-badge")).toHaveText("stale");
  await expect(page.getByText("ETF sponsor holdings are dated differently from snapshot; coverage is stale.")).toBeVisible();
});
