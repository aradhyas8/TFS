import { test, expect } from "@playwright/test";
import path from "node:path";

test("named US stock completes research, company cases and cash comparison in the shared journey", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/");
  await page.getByLabel("As-of date").fill("2026-09-30");
  await page.getByLabel("Reporting currency", { exact: true }).fill("CAD");
  await page.getByLabel("Load portfolio CSV").setInputFiles(path.resolve("../examples/portfolio.csv"));
  await expect(page.getByRole("group", { name: "Position 1", exact: true })).toBeVisible();
  await page.getByLabel("US stock to analyze").selectOption("p1");
  await page.getByLabel("Investment question").fill("Should I hold Acme in my portfolio?");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const stock = page.getByRole("region", { name: "Stock analysis" });
  await expect(stock).toContainText("Acme annual filing");
  await expect(stock.getByRole("link", { name: "Acme annual filing" })).toHaveAttribute("href", /sec.gov\/Archives\/edgar/);
  await expect(stock).toContainText("2025-12-31");
  await expect(stock).toContainText("Consolidated annual revenue");
  await expect(stock.getByRole("table", { name: "Company conditional cases" })).toContainText("100 USD");
  await expect(stock).toContainText("Required exit multiple");
  await expect(page.getByRole("region", { name: "Five-year conditional comparison" }).getByRole("heading", { name: "Researched stock", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toContainText("Hold conditionally");
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toContainText("Allocation amount: not determined");
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
  await page.screenshot({ path: "artifacts/stock-analysis.png", fullPage: true });
});


test("missing stock research returns a completed conditional answer with unknown cases", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/");
  await page.getByLabel("As-of date", { exact: true }).fill("2026-09-30");
  await page.getByLabel("Load portfolio CSV").setInputFiles(path.resolve("../examples/portfolio.csv"));
  await expect(page.getByRole("group", { name: "Position 1", exact: true })).toBeVisible();
  await page.getByRole("group", { name: "Position 1", exact: true }).getByLabel("Mark source label", { exact: true }).fill("Fixture missing_research");
  await page.getByLabel("US stock to analyze").selectOption("p1");
  await page.getByLabel("Investment question").fill("Review unavailable primary facts");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const result = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(result.getByRole("heading", { name: "Clarify missing inputs", exact: true })).toBeVisible();
  await expect(result.getByRole("region", { name: "Stock analysis" })).toContainText("Primary evidence is unavailable");
  await expect(result.getByRole("region", { name: "Stock analysis" }).getByRole("table", { name: "Company conditional cases", exact: true })).toContainText("Unknown");
  await expect(result).toContainText("Allocation amount: not determined");
});
