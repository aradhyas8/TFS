import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const csvPath = path.resolve("../examples/portfolio.csv");

test.beforeEach(async ({ page }) => {
  await page.route("**/*", route => {
    const host = new URL(route.request().url()).hostname;
    return ["127.0.0.1", "localhost"].includes(host) ? route.continue() : route.abort();
  });
  await page.goto("/");
  await page.getByLabel("As-of date", { exact: true }).fill("2026-09-30");
});

async function loadCSV(page: Page) {
  await page.getByLabel("Load portfolio CSV").setInputFiles(csvPath);
  await expect(page.getByLabel("Account 1 name", { exact: true })).toHaveValue("TFSA");
  await expect(page.getByLabel("Account 2 name", { exact: true })).toHaveValue("Brokerage");
}

test("CSV portfolio and submitted question travel through FastAPI tools to the displayed review", async ({ page }) => {
  await loadCSV(page);
  const question = "How concentrated am I across accounts?";
  await page.getByLabel("Investment question").fill(question);
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  const response = await returned;
  expect(response.status()).toBe(200);
  const sent = response.request().postDataJSON();
  expect(sent.question).toBe(question);
  expect(sent.portfolio.positions).toHaveLength(4);
  const answer = await response.json();
  expect(answer.portfolio.total_value).toBe("3600");
  expect(JSON.stringify(answer)).not.toContain("sk-test-backend-only-never-browser");
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result).toBeVisible();
  await expect(result.getByText(question, { exact: true })).toBeVisible();
  await expect(result.getByText(answer.recommendation.reason, { exact: true })).toBeVisible();
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD");
  await expect(result.getByRole("table", { name: "Direct company exposure", exact: true })).toContainText("2,300 CAD");
  await expect(result.getByRole("table", { name: "Direct company exposure", exact: true })).toContainText("63.89%");
  await expect(result).toContainText("Allocation amount: not determined");
  fs.mkdirSync("artifacts", { recursive: true });
  await page.screenshot({ path: "artifacts/portfolio-review.png", fullPage: true });
  // Fetch the actual loaded browser scripts, not just source files, for the dummy-key check.
  for (const src of await page.locator("script[src]").evaluateAll(elements => elements.map(element => (element as HTMLScriptElement).src))) {
    const script = await page.request.get(src);
    expect(await script.text()).not.toContain("sk-test-backend-only-never-browser");
  }
});

test("equivalent manually entered portfolio has the same complete values", async ({ page }) => {
  await page.getByLabel("Account 1 name", { exact: true }).fill("TFSA");
  await page.getByRole("button", { name: "+ Add account", exact: true }).click();
  await page.getByLabel("Account 2 name", { exact: true }).fill("Brokerage");
  for (const [index, kind, account, currency, quantity] of [
    [1, "stock", "TFSA", "USD", "10"], [2, "stock", "Brokerage", "CAD", "5"],
    [3, "cash", "TFSA", "CAD", "650"], [4, "cash", "Brokerage", "USD", "500"],
  ] as const) {
    await page.getByRole("button", { name: "+ Add position", exact: true }).click();
    const row = page.getByRole("group", { name: `Position ${index}`, exact: true });
    await row.getByRole("combobox", { name: "Kind", exact: true }).selectOption(kind);
    await row.getByRole("combobox", { name: "Account", exact: true }).selectOption({ label: account });
    await row.getByLabel("Quote / cash currency", { exact: true }).fill(currency);
    if (kind === "cash") await row.getByLabel("Cash balance", { exact: true }).fill(quantity);
    else {
      await row.getByLabel("Ticker", { exact: true }).fill("ACME");
      await row.getByLabel("Listing / exchange", { exact: true }).fill(index === 1 ? "XNAS" : "XTSE");
      await row.getByLabel("Company ID (shared across listings)", { exact: true }).fill("acme");
      await row.getByLabel("Company name", { exact: true }).fill("Acme");
      await row.getByLabel("Shares", { exact: true }).fill(quantity);
      await row.getByLabel("Supplied mark", { exact: true }).fill(index === 1 ? "100" : "200");
      await row.getByLabel("Mark source label", { exact: true }).fill(index === 1 ? "Broker display" : "Manual mark");
    }
  }
  await page.getByRole("button", { name: "+ Add FX rate", exact: true }).click();
  await page.getByLabel("FX rate", { exact: true }).fill("1.3");
  await page.getByLabel("FX source label", { exact: true }).fill("Supplied FX");
  await page.getByLabel("Investment question").fill("Review my whole portfolio exposure");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD");
  await expect(page.getByRole("table", { name: "Calculated holdings and cash", exact: true })).toContainText("36.11%");
});

test("missing mark displays unknown total and qualifies the completed review", async ({ page }) => {
  await loadCSV(page);
  await page.getByRole("group", { name: "Position 1", exact: true }).getByLabel("Supplied mark", { exact: true }).fill("");
  await page.getByLabel("Investment question").fill("Review the incomplete portfolio");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("Unknown");
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toContainText("Incomplete valuation");
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toContainText("Known subtotal: 2,300 CAD");
});

test("invalid model output clears the previous result and cannot display as a completed recommendation", async ({ page }) => {
  await loadCSV(page);
  await page.getByLabel("Investment question").fill("Review the portfolio");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD");
  await page.getByLabel("Investment question").fill("Return invalid output");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("No recommendation was completed");
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toHaveCount(0);
});

test("malformed CSV reports a clear import error", async ({ page }) => {
  await page.getByLabel("Load portfolio CSV").setInputFiles({ name: "bad.csv", mimeType: "text/csv", buffer: Buffer.from("ticker,shares\nACME,10") });
  await expect(page.locator("main").getByRole("alert")).toContainText("Invalid portfolio CSV");
});

test("frontend source has no backend credential configuration", () => {
  for (const folder of ["app", "components", "lib"]) {
    for (const name of fs.readdirSync(folder)) {
      const source = fs.readFileSync(path.join(folder, name), "utf8");
      expect(source).not.toContain("OPENAI_API_KEY");
      expect(source).not.toContain("sk-test-backend-only-never-browser");
    }
  }
});

test("changing stock to ETF preserves the shared supplied security fields", async ({ page }) => {
  await loadCSV(page);
  const row = page.getByRole("group", { name: "Position 1", exact: true });
  await row.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await expect(row.getByLabel("Ticker", { exact: true })).toHaveValue("ACME");
  await expect(row.getByLabel("Listing / exchange", { exact: true })).toHaveValue("XNAS");
  await expect(row.getByLabel("Shares", { exact: true })).toHaveValue("10");
  await expect(row.getByLabel("Supplied mark", { exact: true })).toHaveValue("100");
  await page.getByLabel("Investment question").fill("Review my corrected ETF classification");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD");
  await expect(page.getByRole("table", { name: "Direct company exposure", exact: true })).toContainText("1,000 CAD");
});

test("large financial values keep every authoritative decimal digit in the display", async ({ page }) => {
  await loadCSV(page);
  const row = page.getByRole("group", { name: "Position 1", exact: true });
  await row.getByLabel("Quote / cash currency", { exact: true }).fill("CAD");
  await row.getByLabel("Shares", { exact: true }).fill("1000000000000");
  await row.getByLabel("Supplied mark", { exact: true }).fill("999999999999.9999999999");
  await page.getByLabel("Investment question").fill("Review my supplied large values");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("1,000,000,000,000,000,000,002,200 CAD");
  await expect(page.getByRole("table", { name: "Calculated holdings and cash", exact: true })).toContainText("999,999,999,999,999,999,999,900 CAD");
});

test("a valid review taking longer than the default proxy timeout completes", async ({ page }) => {
  test.setTimeout(60_000);
  await loadCSV(page);
  await page.getByLabel("Investment question").fill("Slow model review");
  await page.getByRole("button", { name: "Review portfolio →" }).click();
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD", { timeout: 40_000 });
});
