import { test, expect, type Page } from "@playwright/test";

function createPortfolio(sponsorScenario: "full" | "partial" | "stale", policy: "direct_only" | "include_known_indirect" = "direct_only", cap = "0.37") {
  return {
    snapshot: {
      as_of: "2026-09-30",
      reporting_currency: "CAD",
      accounts: [
        { id: "tfsa", name: "TFSA" },
        { id: "rrsp", name: "Brokerage" },
      ],
      positions: [
        {
          id: "tfsa-acme",
          account_id: "tfsa",
          kind: "stock",
          currency: "USD",
          ticker: "ACME",
          listing: "XNAS",
          company_id: "acme",
          company_name: "Acme Corp",
          shares: "10",
          mark: { value: "100", as_of: "2026-09-30", source: "Broker display" },
        },
        {
          id: "rrsp-broad",
          account_id: "rrsp",
          kind: "etf",
          currency: "CAD",
          ticker: "BROAD",
          listing: "XTSE",
          etf_role: "diversified",
          shares: "10",
          mark: { value: "100", as_of: "2026-09-30", source: `Fixture sponsor ${sponsorScenario}` },
        },
        {
          id: "tfsa-cash-cad",
          account_id: "tfsa",
          kind: "cash",
          currency: "CAD",
          cash: "650",
        },
        {
          id: "rrsp-cash-usd",
          account_id: "rrsp",
          kind: "cash",
          currency: "USD",
          cash: "500",
        },
      ],
      fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.3", as_of: "2026-09-30", source: "Broker display" }],
    },
    average_costs: { "tfsa-acme": "80" },
    settings: {
      single_company_cap: cap,
      active_budget: "0.9",
      indirect_cap_policy: policy,
    },
    unresolved: [],
  };
}

import { openDetails, seedPortfolio } from "./test_helpers";

async function open(page: Page, portfolio: object) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await seedPortfolio(page, portfolio);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
}

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });

async function runReview(page: Page, question = "Review my portfolio") {
  await ask(page).fill(`/review ${question}`);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  return memo;
}

test("full ETF sponsor holdings look-through surfaces overlap without double counting", async ({ page }) => {
  await open(page, createPortfolio("full"));
  const memo = await runReview(page, "Review indirect overlap");

  // Portfolio total remains 3,600 CAD (direct positions only: 1300 + 1000 + 650 + 650 = 3600 CAD)
  await expect(memo.getByTestId("review-total")).toContainText("C$3,600");

  // Look-through exposure label
  await expect(memo).toContainText("fully looked through");

  // Overlap is surfaced
  await expect(memo).toContainText("Acme Corp");
  await expect(memo).toContainText("through funds");

  // Details panel Evidence tab shows fund holdings source
  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Evidence" }).click();
  await expect(details).toContainText("Full holdings file");
});

test("indirect_cap_policy governs guardrail checks with known indirect exposure", async ({ page }) => {
  // Single company cap 37%:
  // Direct Acme is 1,300 / 3,600 = 36.11% <= 37%
  // Total Acme with 10% fund overlap is 1,400 / 3,600 = 38.89% > 37%
  // 1. With include_known_indirect: breached
  await open(page, createPortfolio("full", "include_known_indirect", "0.37"));
  let memo = await runReview(page, "Review with include_known_indirect");

  await expect(memo).toContainText(/breaks (one|1) of your rules/i);
  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Single company · Acme Corp");
  await expect(details).toContainText("Over · 38.9% / 37%");
  await expect(details).toContainText("includes known fund overlap");

  // 2. With direct_only: within limit
  await open(page, createPortfolio("full", "direct_only", "0.37"));
  memo = await runReview(page, "Review with direct_only");

  await expect(memo).toContainText("Within your rules");
  await openDetails(page);
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Single company · Acme Corp");
  await expect(details).toContainText("Within · 36.1% / 37%");
  await expect(details).toContainText("direct holdings only");
});

test("partial coverage qualifies conclusion but does not block within_limit", async ({ page }) => {
  await open(page, createPortfolio("partial", "include_known_indirect", "0.45"));
  const memo = await runReview(page, "Review partial look-through");

  // Status is partly looked through
  await expect(memo).toContainText("partly looked through");
  await expect(memo).toContainText("Indirect exposure is not assumed to be zero.");

  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Within · 38.9% / 45%");
  await expect(details).toContainText("Fund look-through is partial; indirect exposure is not assumed to be zero.");
});

test("stale coverage is honestly labeled", async ({ page }) => {
  await open(page, createPortfolio("stale", "include_known_indirect", "0.45"));
  const memo = await runReview(page, "Review stale look-through");

  await expect(memo).toContainText("looked through with stale holdings");
  await expect(memo).toContainText("Indirect exposure is not assumed to be zero.");

  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Guardrails" }).click();
  await expect(details).toContainText("Fund look-through is stale; indirect exposure is not assumed to be zero.");
});
