import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

const ACME = {
  id: "p1", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme",
  company_name: "Acme Corp", shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" },
};
const BROAD = {
  id: "rrsp-broad", account_id: "rrsp", kind: "etf", currency: "USD", ticker: "BROAD", listing: "XNYS",
  etf_role: "diversified", shares: "20", mark: { value: "50", as_of: "2026-09-30", source: "Broker display" },
};
const SAVED = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      ACME,
      { id: "tfsa-cash-usd", account_id: "tfsa", kind: "cash", currency: "USD", cash: "200" },
      BROAD,
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "0" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { p1: "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [{ account_id: "tfsa", ticker: "NOVA", shares: "7", average_cost: null, currency: null, listing: null, kind: "stock", candidates: [] }],
};

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const portfolio = (page: Page) => page.getByRole("region", { name: "Portfolio" });
const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });

const IDENTIFIED = { ...SAVED, unresolved: [] };

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await page.request.delete("/api/decisions");
  await seedPortfolio(page, saved);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  await expect(portfolio(page)).toBeVisible();
}

test("the portfolio opens as its own view with holdings, cash and what is not valued yet", async ({ page }) => {
  await open(page);
  const view = portfolio(page);
  await expect(rail(page).getByRole("button", { name: /^Portfolio/ })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Portfolio", level: 1 })).toBeVisible();
  await expect(view.getByText(/^Revision \d+ · holdings as of Sep 30, 2026$/)).toBeVisible();
  // Details and the composer belong to the conversation, not to this view.
  await expect(ask(page)).toBeHidden();

  // No analysis of this revision yet: no total is shown, and nothing is estimated from cost or a broker mark.
  const total = view.getByRole("group", { name: "Total value" });
  await expect(total).toContainText("Not valued yet");
  await expect(total).toContainText("2 holdings · 2 cash balances · 2 accounts");

  const table = view.getByRole("table", { name: "Holdings" });
  const acme = table.getByRole("row", { name: /ACME/ });
  await expect(acme).toContainText("Acme Corp");
  await expect(acme).toContainText("TFSA");
  await expect(acme).toContainText("10");
  await expect(acme).toContainText("US$80");
  await expect(acme).toContainText("Not valued");
  // Cash is its own amount, in its own currency; a zero balance is a real zero, not a missing value.
  await expect(table.getByRole("row", { name: /Cash USD/ })).toContainText("US$200");
  await expect(table.getByRole("row", { name: /Cash CAD/ })).toContainText("C$0");
  await expect(table.getByRole("row", { name: /Cash CAD/ })).not.toContainText("Not valued");

  // The holding that still needs an exchange is named, and identifying it prepares a reviewed change.
  await expect(view.getByRole("status").filter({ hasText: "1 holding needs you" })).toContainText("NOVA needs an exchange");

  // By account.
  await view.getByRole("radio", { name: "RRSP" }).check();
  await expect(table.getByRole("row", { name: /BROAD/ })).toBeVisible();
  await expect(table.getByRole("row", { name: /ACME/ })).toHaveCount(0);
  await view.getByRole("radio", { name: "All accounts" }).check();
  await expect(table.getByRole("row", { name: /ACME/ })).toBeVisible();

  // Rules and benchmark sit in their own tab.
  await view.getByRole("tab", { name: "Rules and benchmark" }).click();
  await expect(view.getByLabel("Single-company cap (%)")).toHaveValue("15");

  // Back to the conversation.
  await page.getByRole("button", { name: "Back to chat" }).click();
  await expect(portfolio(page)).toBeHidden();
  await expect(ask(page)).toBeVisible();
});

test("a supported stock opens Stock Analysis from its row", async ({ page }) => {
  await open(page, IDENTIFIED);
  await portfolio(page).getByRole("button", { name: "Analyze ACME" }).click();
  await expect(portfolio(page)).toBeHidden();
  await expect(ask(page)).toHaveValue("/stock ACME");
  // Funds and cash are not offered Stock Analysis.
  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  await expect(portfolio(page).getByRole("button", { name: "Analyze BROAD" })).toHaveCount(0);
});

test("draft edits stay separate from the committed portfolio until they are reviewed and confirmed", async ({ page }) => {
  await open(page);
  const view = portfolio(page);
  await view.getByLabel("Fund type for BROAD").selectOption("sector_theme");
  const draft = view.getByRole("region", { name: "Unsaved draft" });
  await expect(draft).toContainText("1 unsaved edit");
  await expect(draft).toContainText("BROAD fund type Diversified to Sector or theme");
  await expect(draft).toContainText("Nothing is saved yet");
  await expect(view.getByRole("row", { name: /BROAD/ })).toHaveAttribute("data-draft", "true");

  // Discard puts the committed fund type back.
  await draft.getByRole("button", { name: "Discard" }).click();
  await expect(draft).toBeHidden();
  await expect(view.getByLabel("Fund type for BROAD")).toHaveValue("diversified");

  // Review prepares a proposal; nothing changes until it is confirmed in the review dialog.
  await view.getByRole("tab", { name: "Rules and benchmark" }).click();
  await view.getByLabel("Single-company cap (%)").fill("12");
  await expect(draft).toContainText("Company cap 15% to 12%");
  await draft.getByRole("button", { name: "Review changes" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const before = await (await page.request.get("/api/portfolio")).json();
  expect(before.portfolio.settings.single_company_cap).toBe("0.15");
  await dialog.getByRole("button", { name: /Cancel|Discard/ }).first().click();
  await expect(dialog).toBeHidden();
  await expect(view.getByRole("tab", { name: /Changes/ })).toBeVisible();
});

test("a valuation of this exact revision fills in value, weight and the rules", async ({ page }) => {
  await open(page, IDENTIFIED);
  await page.getByRole("button", { name: "Back to chat" }).click();
  await ask(page).fill("/review Review my portfolio");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("region", { name: "Analyst memo" })).toBeVisible();

  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  const view = portfolio(page);
  const total = view.getByRole("group", { name: "Total value" });
  await expect(total).not.toContainText("Not valued yet");
  await expect(total).toContainText(/C\$[\d,]+/);
  await expect(total).toContainText(/valued [A-Z][a-z]{2} \d+/);
  await expect(view.getByRole("group", { name: "Allocation" })).toContainText(/Stocks [\d.]+%/);
  await expect(view.getByRole("row", { name: /ACME/ })).toContainText(/[\d.]+%/);
  await expect(view.getByRole("group", { name: "Rules" })).toContainText("Company cap 15%");
});

test("the portfolio fits a phone without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await seedPortfolio(page, SAVED);
  await page.goto("/");
  await page.getByRole("button", { name: "Open portfolio and decisions" }).click();
  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  await expect(portfolio(page)).toBeVisible();
  await expect(rail(page)).toBeHidden();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(portfolio(page).getByRole("row", { name: /ACME/ })).toBeVisible();
});
