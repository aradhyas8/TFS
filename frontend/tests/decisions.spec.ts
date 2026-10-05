import { test, expect } from "@playwright/test";
import path from "node:path";

test("save and revisit a dated decision with historical evidence, reasoning, and user-confirmed action", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/");
  await page.getByLabel("As-of date").fill("2026-09-30");
  await page.getByLabel("Reporting currency", { exact: true }).fill("CAD");
  await page.getByLabel("Load portfolio CSV").setInputFiles(path.resolve("../examples/portfolio.csv"));
  await expect(page.getByRole("group", { name: "Position 2", exact: true })).toBeVisible();

  // 1. Submit an investment question
  await page.getByLabel("Investment question").fill("How concentrated is my portfolio across accounts?");
  await page.getByRole("button", { name: /Review portfolio/ }).click();

  const review = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(review).toBeVisible();
  await expect(review).toContainText("Review current exposure");

  // 2. Save the decision
  const savePanel = review.getByRole("region", { name: "Save decision panel" });
  await expect(savePanel).toBeVisible();
  await savePanel.getByTestId("save-decision-button").click();

  // Verify save confirmation
  await expect(savePanel.getByTestId("save-success-message")).toBeVisible();
  await expect(savePanel.getByTestId("save-success-message")).toContainText("Decision saved successfully.");

  // 3. Reopen the decision
  await savePanel.getByRole("button", { name: /Reopen saved decision/ }).click();


  const savedView = page.getByRole("region", { name: "Reopened saved decision" });
  await expect(savedView).toBeVisible();

  // Verify dated question, original as-of date, and historical date warnings
  await expect(savedView.getByLabel("Original dated question")).toContainText("How concentrated is my portfolio across accounts?");
  await expect(savedView.getByTestId("historical-date-warning")).toContainText("Historical decision as-of 2026-09-30");
  await expect(savedView.getByTestId("historical-date-warning")).toContainText("not a live or refreshed quote");
  await expect(savedView.getByTestId("historical-badge")).toBeVisible();

  // Verify conclusion
  await expect(savedView).toContainText("Preferred action: Review current exposure");

  // Verify reasoning (reason, downside, assumptions, uncertainty, what could change, alternatives)
  await expect(savedView).toContainText("Reasoning");
  await expect(savedView).toContainText("Downside:");
  await expect(savedView.getByRole("heading", { name: "Assumptions" })).toBeVisible();
  await expect(savedView.getByRole("heading", { name: "Uncertainty" })).toBeVisible();
  await expect(savedView.getByRole("heading", { name: "Alternatives" })).toBeVisible();

  // Verify evidence references are displayed with historical dates
  await expect(savedView.getByRole("heading", { name: "Evidence references" })).toBeVisible();
  const evidenceList = savedView.getByRole("list", { name: "Evidence references list" });
  await expect(evidenceList).toBeVisible();
  await expect(evidenceList).toContainText("2026-09-30");

  // Verify initial status: absent action stays unconfirmed without inferring execution
  const unconfirmedSection = savedView.getByTestId("unconfirmed-action-details");
  await expect(unconfirmedSection).toBeVisible();
  await expect(unconfirmedSection).toContainText("Status: Unconfirmed");
  await expect(unconfirmedSection).toContainText("Treat a recommendation as analysis. No action has been confirmed by the user. Execution details are not inferred.");

  // 4. User explicitly confirms an action
  await savedView.getByLabel("Select confirmed action").selectOption("no_action");
  await savedView.getByPlaceholder("e.g. Reviewed thesis, opted not to trade.").fill("Concluded after review that existing holdings should be maintained.");
  await savedView.getByRole("button", { name: "Confirm action button" }).click();

  // Verify confirmation is preserved without inferring execution details
  const confirmedSection = savedView.getByTestId("confirmed-action-details");
  await expect(confirmedSection).toBeVisible();
  await expect(confirmedSection).toContainText("Status: Confirmed user action");
  await expect(confirmedSection).toContainText("Action: No action");
  await expect(confirmedSection).toContainText("Concluded after review that existing holdings should be maintained.");
  await expect(confirmedSection).toContainText("Only explicitly user-confirmed actions are labeled confirmed. Recommendations never become claimed trades, and execution details are not inferred.");

  // 5. Navigate back and verify saved decisions list shows the confirmed decision
  await savedView.getByRole("button", { name: "Back to portfolio" }).click();
  await expect(savedView).not.toBeVisible();

  const historyTable = page.getByRole("region", { name: "Saved decisions" });
  await expect(historyTable).toBeVisible();
  await expect(historyTable).toContainText("2026-09-30");
  await expect(historyTable).toContainText("How concentrated is my portfolio across accounts?");
  await expect(historyTable).toContainText("Confirmed: No action");

  // Reopen again from the table to verify persisted state
  await historyTable.getByRole("button", { name: /Reopen decision/ }).first().click();
  await expect(savedView).toBeVisible();
  await expect(savedView.getByTestId("confirmed-action-details")).toContainText("Action: No action");


  // Secrets must never appear
  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");

  await page.screenshot({ path: "artifacts/save-and-revisit-decision.png", fullPage: true });
});

test("existing analysis journey works without saving", async ({ page }) => {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.goto("/");
  await page.getByLabel("As-of date").fill("2026-09-30");
  await page.getByLabel("Reporting currency", { exact: true }).fill("CAD");
  await page.getByLabel("Load portfolio CSV").setInputFiles(path.resolve("../examples/portfolio.csv"));
  await expect(page.getByRole("group", { name: "Position 2", exact: true })).toBeVisible();

  await page.getByLabel("Investment question").fill("Simple portfolio review without saving");
  await page.getByRole("button", { name: /Review portfolio/ }).click();

  const review = page.getByRole("region", { name: "Completed portfolio review" });
  await expect(review).toBeVisible();
  await expect(review).toContainText("Review current exposure");
  // Analysis completed normally without calling save
});
