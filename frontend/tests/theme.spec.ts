import { test, expect } from "@playwright/test";
import path from "node:path";

test("theme clarification and agreed bounded research complete through the shared form", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/classic");
  await page.getByLabel("As-of date").fill("2026-09-30");
  await page.getByLabel("Load portfolio CSV").setInputFiles(path.resolve("../examples/portfolio.csv"));
  await expect(page.getByRole("group", { name: "Position 1", exact: true })).toBeVisible();
  await page.getByLabel("Explore a theme", { exact: true }).check();
  await page.getByLabel("Theme name", { exact: true }).fill("Industrial automation");
  await page.getByLabel("Economic mechanism", { exact: true }).fill("Automation demand may expand revenue while competition compresses margins.");
  await page.getByLabel("Shortlist ACME / XNAS / p1").check();
  await page.getByLabel("Investment question").fill("Does the automation mechanism justify a portfolio change?");
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  const theme = page.getByRole("region", { name: "Theme discovery" });
  await expect(theme).toContainText("Awaiting agreement");
  await expect(theme).toContainText("Research calls used: 0");
  await page.getByLabel("I agree to this mechanism, shortlist and effort bound").check();
  await page.getByRole("button", { name: /Review portfolio/ }).click();
  await expect(theme).toContainText("Completed");
  await expect(theme).toContainText("challenges");
  await expect(theme.getByRole("link", { name: "Acme annual filing" })).toHaveAttribute("href", /sec.gov/);
  await expect(page.getByRole("region", { name: "Five-year conditional comparison" })).toContainText("No action");
  await expect(page.getByRole("region", { name: "Completed portfolio review" })).toContainText("No action");
  await page.getByLabel("Economic mechanism", { exact: true }).fill("A revised mechanism needs agreement.");
  await expect(page.getByLabel("I agree to this mechanism, shortlist and effort bound")).not.toBeChecked();
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
});
