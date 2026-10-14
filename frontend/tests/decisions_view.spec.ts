import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD", accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [{ id: "p1", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", shares: "10" }],
    fx: [],
  },
  average_costs: {}, settings: null, unresolved: [],
};

const decision = (id: string, workflow: string, subject: object, question: string) => ({
  decision: {
    id, saved_at: "2026-09-15T12:00:00Z", as_of: "2026-09-12", question, workflow, subject,
    evidence_references: [{ id: "ev-1", title: "Form 10-Q, quarter ended Jun 30, 2026", source: "sec", as_of: "2026-08-06", url: null, excerpt: "Revenue grew." }],
    reasoning: { reason: "Revenue held up.", assumptions: [], uncertainty: [], what_could_change: [], downside: "Market risk.", alternatives: [] },
    conclusion: { preferred_action: "hold", amount: null },
    confirmed_action: null,
  },
});

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const register = (page: Page) => page.getByRole("region", { name: "Decisions register" });

async function open(page: Page) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await page.request.delete("/api/decisions");
  await seedPortfolio(page, SAVED);
  expect((await page.request.post("/api/decisions", { data: decision("d-stock", "stock", { label: "ACME", ticker: "ACME", listing: "XNAS" }, "Is ACME still worth holding?") })).ok()).toBeTruthy();
  expect((await page.request.post("/api/decisions", { data: decision("d-review", "review", { label: "Portfolio review" }, "Am I too concentrated?") })).ok()).toBeTruthy();
  expect((await page.request.post("/api/decisions/d-stock/confirm", { data: { action: "hold", notes: null } })).ok()).toBeTruthy();
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

test("decisions open as their own view, apart from what you recorded and from today's holdings", async ({ page }) => {
  await open(page);
  // The rail counts what is not confirmed; it no longer lists every decision.
  await expect(rail(page).getByTestId("all-decisions-link")).toContainText("1 open");
  await expect(rail(page).getByTestId("decision-item")).toHaveCount(0);
  await rail(page).getByTestId("all-decisions-link").click();
  await expect(page.getByRole("heading", { name: "Decisions", level: 1 })).toBeVisible();
  await expect(rail(page).getByTestId("all-decisions-link")).toHaveAttribute("aria-current", "page");

  const list = register(page);
  await expect(list).toContainText("does not change when your holdings do");
  const stock = list.getByTestId("decision-row").filter({ hasText: "Stock analysis · ACME" });
  await expect(stock).toContainText("Is ACME still worth holding?");
  await expect(stock).toContainText("Hold conditionally");
  await expect(stock).toContainText("You did: held");
  await expect(stock).toContainText("portfolio as of Sep 12, 2026");
  await expect(stock).toContainText("1 source");
  await expect(list.getByTestId("decision-row").filter({ hasText: "Portfolio review" })).toContainText("Not confirmed");

  // Filters.
  await list.getByRole("radio", { name: "Not confirmed" }).check();
  await expect(list).toContainText("Showing 1 of 2 decisions");
  await expect(stock).toHaveCount(0);
  await list.getByRole("radio", { name: "Recorded" }).check();
  await expect(list).toContainText("Showing 1 of 2 decisions");
  await list.getByRole("radio", { name: "All" }).check();
  await list.getByLabel("Filter by workflow").selectOption("review");
  await expect(list).toContainText("Showing 1 of 2 decisions");
  await list.getByLabel("Filter by workflow").selectOption("all");

  // Reopening shows the decision as it was saved, with its original evidence, and a way back to the list.
  await stock.click();
  const historical = page.getByRole("region", { name: "Reopened decision" });
  await expect(historical).toContainText("Historical");
  await historical.getByRole("button", { name: "1" }).first().click();
  await expect(page.getByRole("complementary", { name: "Details" })).toContainText("Form 10-Q, quarter ended Jun 30, 2026");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "All decisions" }).click();
  await expect(register(page)).toBeVisible();

  // Delete asks first, and the list survives a reload.
  page.once("dialog", dialog => void dialog.accept());
  await register(page).getByRole("button", { name: "Delete decision: Portfolio review" }).click();
  await expect(register(page).getByTestId("decision-row")).toHaveCount(1);
  await page.reload();
  await rail(page).getByTestId("all-decisions-link").click();
  await expect(register(page).getByTestId("decision-row")).toHaveCount(1);
  await expect(register(page)).toContainText("Stock analysis · ACME");

  await page.getByRole("button", { name: "Back to chat" }).click();
  await expect(page.getByRole("textbox", { name: /type \/ for workflows/ })).toBeFocused();
});

test("with no saved decisions the view says how to make one", async ({ page }) => {
  await open(page);
  await page.request.delete("/api/decisions");
  await page.reload();
  await rail(page).getByTestId("all-decisions-link").click();
  await expect(register(page)).toContainText("No saved decisions yet.");
});
