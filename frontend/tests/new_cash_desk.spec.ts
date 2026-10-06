import { test, expect, type Page } from "@playwright/test";

const CSV = `row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
etf,fund,broker,Brokerage,BROAD,XNYS,,,80,,USD,100,2026-09-30,Fixture allocation,,,,
cash,cash,broker,Brokerage,,,,,,2000,USD,,,,,,,
`;

async function setUp(page: Page, riskContext: string | null) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/new-cash");
  await page.getByLabel("As-of date", { exact: true }).fill("2026-09-30");
  await page.getByLabel("Reporting currency", { exact: true }).fill("USD");
  await page.getByLabel("Load portfolio CSV").setInputFiles({ name: "allocation.csv", mimeType: "text/csv", buffer: Buffer.from(CSV) });
  const rail = page.getByRole("navigation", { name: "Portfolio and decisions" });
  await expect(rail).toContainText("BROAD");
  await expect(rail).toContainText("Values and weights appear after the first analysis.");

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();
  await details.getByRole("combobox", { name: "Fund type for BROAD" }).selectOption("diversified");
  await details.getByLabel("Single-company cap (%)").fill("15");
  await details.getByLabel("Active picks budget (%)").fill("30");
  await details.getByRole("combobox", { name: "Count fund holdings toward the cap" }).selectOption("direct_only");
  await details.getByRole("combobox", { name: "Cash above target is a deliberate choice" }).selectOption("false");

  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("/");
  await expect(page.getByRole("listbox", { name: "Workflows" })).toBeVisible();
  await ask.press("Enter");
  await page.getByLabel("Amount", { exact: true }).fill("6000");
  await page.getByRole("combobox", { name: "Arrives in" }).selectOption("cash");
  if (riskContext) await page.getByLabel("Loss tolerance or withdrawal plans").fill(riskContext);
  await page.getByRole("textbox", { name: "Question" }).fill("I have this portfolio and $6,000. What should I do?");
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
  await expect(page.getByRole("navigation", { name: "Portfolio and decisions" })).toContainText(/Weights · .* analysis/);

  const details = page.getByRole("complementary", { name: "Details" });
  await expect(details).toContainText("No filings were researched for this answer");
  await memo.getByRole("button", { name: /Scenarios/ }).click();
  await expect(details.getByRole("table", { name: "Five-year comparison" })).toBeVisible();
  await expect(details).toContainText("Starting value US$6,000");
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Active picks");
  await expect(details).toContainText("Within");

  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByTestId("saved-message")).toContainText("Saved to Decisions");
  await page.getByRole("radio", { name: "Kept it as cash" }).check();
  await page.getByLabel("Note for future you · optional").fill("Waiting for the next statement.");
  await page.getByRole("button", { name: "Record action" }).click();
  await expect(memo).toContainText("Saved and recorded");

  const rail = page.getByRole("navigation", { name: "Portfolio and decisions" });
  await rail.getByRole("button", { name: /I have this portfolio and \$6,000/ }).first().click();
  const reopened = page.getByRole("region", { name: "Reopened decision" });
  await expect(reopened.getByTestId("historical-band")).toContainText("Historical");
  await expect(reopened).toContainText("Add US$800–2,400");
  await expect(reopened).toContainText("Waiting for the next statement.");
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
});

test("new cash: missing loss tolerance returns a needs-input answer that re-runs in place", async ({ page }) => {
  await setUp(page, null);
  await page.getByRole("checkbox", { name: /is new money/ }).check();
  await page.getByRole("button", { name: "Analyze", exact: true }).click();

  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Not enough to size this yet.");
  await expect(memo).toContainText("Supply decision-critical loss tolerance and withdrawal context.");
  await expect(memo).toContainText("weight after this cash is Unknown");
  await memo.getByLabel(/How large a drop could you hold through/).fill("Could hold through a 30% drop; no withdrawals planned.");
  await memo.getByRole("button", { name: "Re-run with these" }).click();
  await expect(page.getByRole("region", { name: "Analyst memo" }).getByRole("heading", { level: 2 })).toHaveText("Add US$800–2,400 to BROAD. Keep the rest as cash.");
});
