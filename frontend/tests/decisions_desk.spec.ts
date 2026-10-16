import { test, expect, type Page } from "@playwright/test";

const ACME = {
  id: "p1",
  account_id: "tfsa",
  kind: "stock",
  currency: "USD",
  ticker: "ACME",
  listing: "XNAS",
  company_id: "acme",
  company_name: "Acme Corp",
  shares: "10",
  mark: { value: "100", as_of: "2026-09-30", source: "Broker display" },
};

const BROAD = {
  id: "rrsp-broad",
  account_id: "rrsp",
  kind: "etf",
  currency: "USD",
  ticker: "BROAD",
  listing: "XNYS",
  etf_role: "diversified",
  shares: "20",
  mark: { value: "50", as_of: "2026-09-30", source: "Broker display" },
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
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { p1: "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

import { openDecisions, seedPortfolio, recordWorkflows } from "./test_helpers";

/** A saved decision is listed under Decisions; the conversation is restored afterwards. */
async function expectDecision(page: Page, label: string | RegExp) {
  await expect((await openDecisions(page)).getByTestId("decision-row").filter({ hasText: label })).toBeVisible();
  await page.getByRole("button", { name: "Back to chat" }).click();
}

async function open(page: Page) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await page.request.delete("/api/decisions");
  await seedPortfolio(page, SAVED);
  const sent = recordWorkflows(page);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  return sent;
}

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });

test.describe("Decisions workflow, re-run, and register", () => {
  test("saved decisions display workflow and subject in rail, open cleanly, and support decisions register", async ({
    page,
  }) => {
    const sent = await open(page);

    // 1. Run and save a Review decision
    await ask(page).fill("/review Review my portfolio");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("region", { name: "Analyst memo" })).toBeVisible();
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText(/Saved to Decisions \d\d?:\d\d/)).toBeVisible();
    await expectDecision(page, "Portfolio review");

    // 2. Run and save a Stock decision
    await ask(page).fill("/stock ACME");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("region", { name: "Stock analysis" })).toBeVisible();
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText(/Saved to Decisions \d\d?:\d\d/)).toBeVisible();
    await expectDecision(page, "Stock analysis · ACME");

    // 3. Run and save a Rebalance decision
    await ask(page).fill("Should I rebalance my portfolio?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("region", { name: "Rebalance" })).toBeVisible();
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText(/Saved to Decisions \d\d?:\d\d/)).toBeVisible();
    await expectDecision(page, "Rebalance");

    // 4. Seed a legacy decision without workflow or subject directly via backend
    const legacyResp = await page.request.post("/api/decisions", {
      data: {
        decision: {
          id: "dec-legacy-pre-ticket-06",
          saved_at: "2026-09-15T12:00:00Z",
          as_of: "2026-09-15",
          question: "How diversified is my portfolio?",
          evidence_references: [],
          reasoning: {
            reason: "Concentration is moderate across accounts.",
            assumptions: ["Supplied marks hold."],
            uncertainty: ["No rules configured."],
            what_could_change: ["Fresh marks."],
            downside: "Market risk.",
            alternatives: [],
          },
          conclusion: {
            preferred_action: "review_only",
            amount: null,
          },
          confirmed_action: null,
        },
      },
    });
    expect(legacyResp.ok()).toBeTruthy();

    // Reload page to reflect all saved decisions in the rail
    await page.reload();
    await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");

    // Verify all 4 decisions in the rail with their workflow · subject labels
    await expectDecision(page, "Portfolio review");
    await expectDecision(page, "Stock analysis · ACME");
    await expectDecision(page, "Rebalance");
    await expectDecision(page, "Unknown workflow");

    // 5. Open legacy decision and verify "Unknown workflow" display
    await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: "Unknown workflow" }).click();
    const historicalView = page.getByRole("region", { name: "Reopened decision" });
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText("Unknown workflow");
    await expect(historicalView).toContainText("Saved Sep 15");
    await expect(historicalView).toContainText("An analysis as of Sep 15");

    // Test legacy Re-run: "Unknown workflow pre-fills the question in the composer"
    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    await expect(historicalView).not.toBeVisible();
    await expect(ask(page)).toHaveValue("How diversified is my portfolio?");

    // 6. Test Decisions Register ("All decisions")
    await rail(page).getByTestId("all-decisions-link").click();
    const register = page.getByRole("region", { name: "Decisions register" });
    await expect(register).toBeVisible();
    await expect(page.getByRole("heading", { name: "Decisions", level: 1 })).toBeVisible();
    await expect(register).toContainText("Showing 4 of 4 decisions");

    // Filter by workflow: Stock analysis
    const workflowFilter = register.getByLabel("Filter by workflow");
    await workflowFilter.selectOption("stock");
    await expect(register).toContainText("Showing 1 of 4 decisions");
    await expect(register.getByText("Stock analysis · ACME")).toBeVisible();
    await expect(register.getByText("Portfolio review")).not.toBeVisible();

    // Filter by workflow: Unknown workflow
    await workflowFilter.selectOption("unknown");
    await expect(register).toContainText("Showing 1 of 4 decisions");
    await expect(register.getByTestId("decision-row").getByText("Unknown workflow")).toBeVisible();

    // Reset workflow filter to all
    await workflowFilter.selectOption("all");
    await expect(register).toContainText("Showing 4 of 4 decisions");

    // 7. Test confirming an action and "Not confirmed" filter
    // Click Stock decision row in register to open it
    await register.getByTestId("decision-row").filter({ hasText: "Stock analysis · ACME" }).click();
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText("Stock analysis · ACME");

    // Confirm action on Stock decision
    await historicalView.getByRole("radio", { name: "Kept it as it is" }).check();
    await historicalView.getByPlaceholder(/split into two buys/).fill("Holding position through Q3 earnings.");
    await historicalView.getByRole("button", { name: "Record action" }).click();
    await expect(historicalView.getByText(/You did/)).toBeVisible();
    await expect(historicalView.getByText("Held")).toBeVisible();

    // Reopen Decisions Register
    await rail(page).getByTestId("all-decisions-link").click();
    await expect(register).toBeVisible();

    // Stock decision row now shows "You did: held"
    await expect(register.getByText("You did: held")).toBeVisible();

    // Filter by "Not confirmed"
    await register.getByRole("radio", { name: "Not confirmed" }).check();
    await expect(register).toContainText("Showing 3 of 4 decisions");
    await expect(register.getByText("Stock analysis · ACME")).not.toBeVisible();

    // Uncheck "Not confirmed"
    await register.getByRole("radio", { name: "All" }).check();
    await expect(register).toContainText("Showing 4 of 4 decisions");
    await expect(register.getByText("Stock analysis · ACME")).toBeVisible();

    // 8. Test Stock Re-run
    await register.getByText("Stock analysis · ACME").click();
    await expect(historicalView).toBeVisible();
    sent.length = 0;
    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].stock).toEqual({ position_id: "p1" });

    // 9. Test Rebalance Re-run
    await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: "Rebalance" }).click();
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText("Rebalance");
    sent.length = 0;
    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].portfolio_review).toEqual({ prior_theses: [], risk_context: null });

    // 10. Test Review Re-run
    await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: "Portfolio review" }).click();
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText("Portfolio review");
    sent.length = 0;
    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0].question).toBe("Review my portfolio");
  });

  test("new cash and theme decisions re-run with original inputs and reset confirmation/agreement", async ({
    page,
  }) => {
    const sent = await open(page);

    // 1. Create and save a New Cash decision
    await ask(page).fill("/new-cash");
    await ask(page).press("Enter");
    await page.getByLabel("Amount", { exact: true }).fill("2500");
    await page.getByRole("combobox", { name: "Currency" }).selectOption("USD");
    await page.getByRole("combobox", { name: "Into account" }).selectOption({ label: "RRSP" });
    await page.getByRole("checkbox", { name: /new money/i }).check();
    await page.getByRole("textbox", { name: "Question" }).fill("Where should new cash go?");
    await page.getByRole("button", { name: "Analyze", exact: true }).click();
    await expect(page.getByRole("region", { name: "Analyst memo" })).toBeVisible();
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText(/Saved to Decisions \d\d?:\d\d/)).toBeVisible();
    await expectDecision(page, /New cash · US\$2,500 → RRSP/);

    // 2. Create and save a Theme decision
    await page.getByRole("button", { name: "New conversation" }).click();
    await ask(page).fill("/theme");
    await ask(page).press("Enter");
    await page.getByLabel("Theme name").fill("Robotics");
    await page.getByLabel("Economic mechanism").fill("Automation secular tailwinds");
    await page.getByLabel("Add held position to shortlist").selectOption("p1");
    await page.getByRole("button", { name: "Analyze", exact: true }).click();
    // The conversation asks for agreement, then researches once it is given
    const agreement = page.getByRole("log", { name: "Conversation" }).getByTestId("clarification");
    await expect(agreement).toContainText("Your agreement to the theme and shortlist");
    await agreement.getByRole("button", { name: "Agree and research" }).click();
    const memo = page.getByRole("region", { name: "Analyst memo" });
    await expect(memo).toContainText("Completed theme research");
    await page.getByRole("button", { name: "Save decision" }).click();
    await expect(page.getByText(/Saved to Decisions \d\d?:\d\d/)).toBeVisible();
    await expectDecision(page, "Theme · Robotics");

    // 3. Test New Cash Re-run
    await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: /New cash · US\$2,500 → RRSP/ }).click();
    const historicalView = page.getByRole("region", { name: "Reopened decision" });
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText(/New cash · US\$2,500 → RRSP/);

    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    // New cash form reopens with original amount and account, and unconfirmed state (not sent yet)
    await expect(page.getByRole("group", { name: "New cash inputs" })).toBeVisible();
    await expect(page.getByLabel("Amount", { exact: true })).toHaveValue("2500");
    await expect(page.getByRole("combobox", { name: "Currency" })).toHaveValue("USD");
    await expect(page.getByRole("combobox", { name: "Into account" })).toHaveValue("rrsp");
    await expect(page.getByRole("checkbox", { name: /new money/i })).not.toBeChecked();
    await expect(page.getByRole("button", { name: "Analyze", exact: true })).toBeDisabled();

    // 4. Test Theme Re-run
    await (await openDecisions(page)).getByTestId("decision-row").filter({ hasText: "Theme · Robotics" }).click();
    await expect(historicalView).toBeVisible();
    await expect(historicalView.getByTestId("decision-workflow")).toHaveText("Theme · Robotics");

    await historicalView.getByRole("button", { name: "Re-run with today's portfolio" }).click();
    // Theme form reopens with name, mechanism, and shortlist, with agreement reset to unagreed
    await expect(page.getByRole("group", { name: "Theme inputs" })).toBeVisible();
    await expect(page.getByLabel("Theme name")).toHaveValue("Robotics");
    await expect(page.getByLabel("Economic mechanism")).toHaveValue("Automation secular tailwinds");
    await expect(page.getByRole("list", { name: "Candidate chips" })).toContainText("ACME");
    await expect(page.getByRole("button", { name: "Analyze", exact: true })).toBeVisible();
  });
});
