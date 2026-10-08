import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
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

test("selected fund, cash and actual no action display conditional Python comparison", async ({ page }) => {
  await loadCSV(page);
  const fund = page.getByRole("group", { name: "Position 1", exact: true });
  await fund.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await fund.getByLabel("ETF classification for active budget").selectOption("diversified");
  await page.getByLabel("Include conditional comparison").check();
  await page.getByLabel("Comparison scope p1", { exact: true }).check();
  await page.getByLabel("Comparison scope c1", { exact: true }).check();
  await page.getByLabel("Diversified ETF alternative").selectOption("p1");
  await page.getByLabel("Cash or short-bill currency").selectOption("c1");
  await page.getByLabel("Portfolio exposure for p1").fill("Diversified global equity");
  await page.getByLabel("Annual fund cost (fraction) for p1").fill("0");
  await page.getByLabel("Income yield (fraction) for p1").fill("0.02");
  await page.getByLabel("Investment question").fill("Compare conditional alternatives");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().comparison.scope_position_ids).toEqual(["p1", "c1"]);
  const comparison = page.getByRole("region", { name: "Five-year conditional comparison", exact: true });
  await expect(comparison).toContainText("Common starting value: 1,950 CAD");
  await expect(comparison.getByRole("table", { name: "fund conditional cases" })).toContainText("2,152.95756624 CAD");
  await expect(comparison.getByRole("table", { name: "cash conditional cases" })).toContainText("2,151.922968 CAD");
  await expect(comparison.getByRole("table", { name: "keep conditional cases" })).toContainText("2,152.61270016 CAD");
  for (const name of ["fund", "cash", "keep"]) {
    const table = comparison.getByRole("table", { name: `${name} conditional cases` });
    await expect(table).toContainText("downside"); await expect(table).toContainText("base"); await expect(table).toContainText("upside");
    await expect(table).toContainText("Unknown");
  }
  const cash = comparison.getByRole("region", { name: "Comparison cash", exact: true });
  await cash.getByText("base: drivers, assumptions and uncertainty", { exact: true }).click();
  await expect(cash).toContainText("0.04, 0.03, 0.02, 0.01, 0");
  await expect(cash).toContainText("Tax consequences are unknown and unquantified");
  await expect(comparison).toContainText("not a mandatory exit date");
  await expect(comparison).toContainText("not real purchasing-power outcomes");
  await comparison.getByText("Dated supplied fund facts and effects", { exact: true }).click();
  await expect(comparison).toContainText("Diversified global equity");
  await expect(comparison).toContainText("User supplied fund facts");
  await page.screenshot({ path: "artifacts/conditional-comparison.png", fullPage: true });
});

test("removing selected holdings clears obsolete comparison references", async ({ page }) => {
  await loadCSV(page);
  await page.getByLabel("Include conditional comparison").check();
  await page.getByLabel("Comparison scope c1", { exact: true }).check();
  await page.getByLabel("Cash or short-bill currency").selectOption("c1");
  await page.getByRole("group", { name: "Position 3", exact: true }).getByRole("button", { name: "Remove position 3" }).click();
  const inputs = page.getByRole("region", { name: "Comparison inputs", exact: true });
  await expect(inputs).toContainText("Select at least one current holding or cash balance");
  await expect(page.getByLabel("Cash or short-bill currency")).toHaveValue("");
  await page.getByLabel("Comparison scope c2", { exact: true }).check();
  await page.getByLabel("Cash or short-bill currency").selectOption("c2");
  await page.getByLabel("Investment question").fill("Compare remaining cash");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().comparison.scope_position_ids).toEqual(["c2"]);
  expect(response.request().postDataJSON().comparison.alternatives.some((alt: { position_id: string }) => alt.position_id === "c1")).toBe(false);
  const fund = page.getByRole("group", { name: "Position 1", exact: true });
  await fund.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
  await fund.getByLabel("ETF classification for active budget").selectOption("diversified");
  await page.getByLabel("Diversified ETF alternative").selectOption("p1");
  await page.getByLabel("Portfolio exposure for p1").fill("Diversified global equity");
  await loadCSV(page); // Replacement reclassifies the selected fund as a stock.
  await expect(page.getByLabel("Diversified ETF alternative")).toHaveValue("");
  await expect(page.getByLabel("Portfolio exposure for p1")).toHaveCount(0);
  const replaced = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const afterImport = await replaced;
  expect(afterImport.status()).toBe(200);
  expect(afterImport.request().postDataJSON().comparison.fund_facts).toEqual([]);
  expect(afterImport.request().postDataJSON().comparison.alternatives.some((alt: { id: string }) => alt.id === "fund")).toBe(false);
});

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
    for (const name of fs.readdirSync(folder, { recursive: true }) as string[]) {
      if (fs.statSync(path.join(folder, name)).isDirectory()) continue;
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

test("broker marks show source date, capture time, quote age and dated FX", async ({ page }) => {
  await loadCSV(page);
  const row = page.getByRole("group", { name: "Position 1", exact: true });
  await row.getByLabel("Mark capture time (optional, with timezone)").fill("2026-09-30T20:00:00Z");
  await page.getByLabel("Investment question").fill("Review broker-display valuation basis");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(page.getByTestId("portfolio-total")).toHaveText("3,600 CAD");
  await expect(result).toContainText("manual");
  await expect(result).toContainText("Quote captured: 2026-09-30T20:00:00Z");
  await expect(result).toContainText("Quote age vs review: 0 days");
  await expect(result).toContainText("FX: 1.3 CAD/USD");
  await expect(result).toContainText("Source inputs do not support confident sizing");
  await result.getByText("Calculation basis", { exact: true }).click();
  await expect(result).toContainText("No dividends are added");
});

for (const scenario of ["delayed", "cached", "stale", "ambiguous"] as const) {
  test(`refreshed ${scenario} source is visible in the existing review`, async ({ page }) => {
    await loadCSV(page);
    await page.getByRole("group", { name: "Position 1", exact: true }).getByLabel("Mark source label", { exact: true }).fill(`Fixture ${scenario}`);
    await page.getByLabel("Investment question").fill("Refresh the dated source basis");
    await page.getByRole("button", { name: /Review portfolio/ }).click();
    const result = page.getByRole("region", { name: "Completed portfolio review" });
    await expect(result).toContainText("Qualified fixture");
    await expect(result).toContainText("FX: 1.4 CAD/USD");
    await expect(result).toContainText("2026-09-30T20:00:00Z");
    if (["stale", "ambiguous"].includes(scenario)) {
      await expect(page.getByTestId("portfolio-total")).toHaveText("Unknown");
      await expect(result).toContainText(scenario === "stale" ? "stale" : "Source identity: ambiguous");
    } else {
      await expect(page.getByTestId("portfolio-total")).toHaveText("4,030 CAD");
      await expect(result.getByRole("table", { name: "Direct company exposure", exact: true })).toContainText("2,680 CAD");
      await expect(result).toContainText("Identity: verified");
      await expect(result).toContainText(scenario);
      if (scenario === "delayed") await page.screenshot({ path: "artifacts/source-freshness-review.png", fullPage: true });
    }
    await expect(result).toContainText("Allocation amount: not determined");
  });
}

test("explicit cap and funded new-cash preview display authoritative blocked checks", async ({ page }) => {
  await loadCSV(page);
  const cash = page.getByRole("group", { name: "Position 4", exact: true });
  await cash.getByLabel("Quote / cash currency", { exact: true }).fill("CAD");
  await cash.getByLabel("Cash balance", { exact: true }).fill("650");
  await page.getByLabel("Single-company cap (fraction)").fill("0.6");
  await page.getByLabel("Active budget (fraction)").fill("0.9");
  await page.getByLabel("Cash above baseline is a deliberate tilt").selectOption("false");
  await page.getByText("Preview explicit proposed changes", { exact: true }).click();
  await page.getByRole("button", { name: "+ Add proposed new cash", exact: true }).click();
  await page.getByLabel("New cash destination").selectOption("c2");
  await page.getByLabel("New cash amount").fill("1000");
  await page.getByRole("button", { name: "+ Add proposed share change", exact: true }).click();
  await page.getByLabel("Proposed security").selectOption("p2");
  await page.getByLabel("Share change").fill("5");
  await page.getByLabel("Funding cash balance").selectOption("c2");
  await page.getByLabel("Investment question").fill("Check my proposed change");
  const returned = page.waitForResponse(response => response.url().endsWith("/api/analyze"));
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const response = await returned;
  expect(response.status()).toBe(200);
  const answer = await response.json();
  expect(answer.proposals[0].status).toBe("blocked");
  expect(answer.recommendation.preferred_action).toBe("wait_for_inputs");
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result.getByRole("table", { name: "Current company cap checks", exact: true })).toContainText("breached");
  await expect(result).toContainText("Baseline is unknown");
  const preview = result.getByRole("region", { name: "Proposed change 1" });
  await expect(preview).toContainText("blocked");
  await expect(preview).toContainText("4,600 CAD");
  await expect(preview).toContainText("71.74%");
  await expect(preview).toContainText("540 CAD");
  await page.screenshot({ path: "artifacts/portfolio-guardrails.png", fullPage: true });
});

test("model conviction cannot waive displayed cap and active-budget breaches", async ({ page }) => {
  await loadCSV(page);
  const cash = page.getByRole("group", { name: "Position 4", exact: true });
  await cash.getByLabel("Quote / cash currency", { exact: true }).fill("CAD");
  await cash.getByLabel("Cash balance", { exact: true }).fill("650");
  await page.getByLabel("Single-company cap (fraction)").fill("0.7");
  await page.getByLabel("Active budget (fraction)").fill("0.7");
  await page.getByLabel("Cash above baseline is a deliberate tilt").selectOption("false");
  await page.getByLabel("Investment question").fill("Check model proposal");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result.getByRole("heading", { name: "Clarify missing inputs", exact: true })).toBeVisible();
  const preview = result.getByRole("region", { name: "Proposed change 1" });
  await expect(preview).toContainText("Source: model");
  await expect(preview).toContainText("blocked");
  await expect(preview).toContainText("75%");
  await expect(preview).toContainText("Active-budget check: breached");
  await expect(result).not.toContainText("Strong conviction permits an exception");
});

test("explicit ETF classifications and cash baseline determine the active budget", async ({ page }) => {
  await loadCSV(page);
  for (const [index, role] of [[1, "diversified"], [2, "sector_theme"]] as const) {
    const position = page.getByRole("group", { name: `Position ${index}`, exact: true });
    await position.getByRole("combobox", { name: "Kind", exact: true }).selectOption("etf");
    await position.getByLabel("ETF classification for active budget").selectOption(role);
  }
  await page.getByLabel("Active budget (fraction)").fill("0.5");
  await page.getByLabel("Cash baseline (fraction)").fill("0.1");
  await page.getByLabel("Cash above baseline is a deliberate tilt").selectOption("true");
  await page.getByLabel("Investment question").fill("Check explicit active classifications");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result).toContainText("Active-budget check: breached");
  await expect(result).toContainText("1,940 CAD / 53.89%");
  await expect(result).toContainText("Deliberate excess cash: 940 CAD");
  const baseline = result.getByRole("table", { name: "Current baseline comparison", exact: true });
  await expect(baseline.getByRole("row").filter({ hasText: "Cash" })).toContainText("10%");
  await expect(baseline.getByRole("row").filter({ hasText: "Individual stocks" })).toContainText("Unknown");
});
