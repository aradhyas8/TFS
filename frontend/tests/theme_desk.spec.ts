import { test, expect, type Page } from "@playwright/test";

const ACME = {
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
      BROAD,
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.50", active_budget: "0.9", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: saved })).ok()).toBeTruthy();
  const sent: any[] = [];
  page.on("request", request => {
    if (request.url().endsWith("/api/analyze")) sent.push(request.postDataJSON());
  });
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
  return sent;
}

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });

test("/theme is live in the slash menu and does not redirect to /classic", async ({ page }) => {
  await open(page);
  await ask(page).fill("/");
  const slashMenu = page.getByRole("listbox", { name: "Workflows" });
  await expect(slashMenu).toBeVisible();

  const themeOption = slashMenu.getByRole("option", { name: /\/theme/ });
  await expect(themeOption).toBeVisible();
  await expect(themeOption).not.toContainText("classic view");

  await themeOption.click();
  // Ensure we are still on the desk page and haven't redirected to /classic
  expect(new URL(page.url()).pathname).toBe("/");
  await expect(page.getByRole("group", { name: "Theme inputs" })).toBeVisible();
  await expect(page.getByLabel("Theme name")).toBeVisible();
  await expect(page.getByLabel("Economic mechanism")).toBeVisible();
});

test("theme discovery full flow: form, agreement card, edit clearing, agreed research, citations, scenarios tab, and save", async ({
  page,
}) => {
  const sent = await open(page);

  // 1. Open theme workflow via slash command
  await ask(page).fill("/theme");
  await ask(page).press("Enter");
  await expect(page.getByRole("group", { name: "Theme inputs" })).toBeVisible();

  // 2. Fill theme details
  await page.getByLabel("Theme name").fill("Industrial automation");
  await page.getByLabel("Economic mechanism").fill(
    "Automation demand may expand revenue while competition compresses margins."
  );

  // Add held candidate ACME via the dropdown
  await page.getByLabel("Add held position to shortlist").selectOption("tfsa-acme");
  await expect(page.getByRole("list", { name: "Candidate chips" })).toContainText("ACME");

  // Add looked up candidate ETF (SPY)
  await page.getByPlaceholder("Ticker (e.g. NVDA)").fill("SPY");
  await page.getByRole("button", { name: "+ Add" }).click();
  await expect(page.getByRole("list", { name: "Candidate chips" })).toContainText("SPY");

  // Adjust max tool calls
  await page.getByLabel("Maximum research calls").fill("12");

  // Submit initial analysis (should send confirmed: false)
  const analyzeBtn = page.getByRole("button", { name: "Analyze", exact: true });
  await expect(analyzeBtn).toBeEnabled();
  await analyzeBtn.click();

  // Verify first sent request had confirmed: false
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].theme).toBeDefined();
  expect(sent[0].theme.confirmed).toBe(false);
  expect(sent[0].theme.name).toBe("Industrial automation");
  expect(sent[0].theme.shortlist).toEqual(["tfsa-acme", "candidate-SPY-XNYS"]);

  // 3. Verify "Needs you" agreement state renders
  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText(
    "Agree the theme mechanism and shortlist before researching."
  );
  await expect(memo).toContainText("Needs your agreement");
  await expect(memo).toContainText("Industrial automation");
  await expect(memo).toContainText("Automation demand may expand revenue while competition compresses margins.");
  await expect(memo).toContainText("ACME, SPY");
  await expect(memo.getByRole("button", { name: "Agree and research" })).toBeVisible();

  // 4. Test that edits in composer clear agreement and update the agreement card
  await page.getByRole("button", { name: "Edit inputs" }).click();
  await page.getByLabel("Economic mechanism").fill("Revised mechanism demand expanding.");
  await expect(memo).toContainText("Revised mechanism demand expanding.");

  // Restore original mechanism for test fixture alignment
  await page.getByLabel("Economic mechanism").fill(
    "Automation demand may expand revenue while competition compresses margins."
  );
  await expect(memo).toContainText("Automation demand may expand revenue while competition compresses margins.");

  // Submitting edited form from composer sends confirmed: false and collapses composer
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect.poll(() => sent.length).toBe(2);
  expect(sent[1].theme.confirmed).toBe(false);
  await expect(memo).toContainText("Needs your agreement");

  // 5. Agree and research submits confirmed: true
  await memo.getByRole("button", { name: "Agree and research" }).click();

  // Verify third request had confirmed: true
  await expect.poll(() => sent.length).toBe(3);
  expect(sent[2].theme.confirmed).toBe(true);

  // 6. Verify completed Theme memo renders (conditional because SPY fund facts are unknown)
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Not enough to size this yet.");
  await expect(memo).toContainText("Completed theme research");
  await expect(memo).toContainText("Economic mechanism");
  await expect(memo).toContainText("Automation demand may expand revenue while competition compresses margins.");

  // Candidate verdicts: ACME challenges with primary citation
  const acmeTest = page.getByTestId("test-tfsa-acme");
  await expect(acmeTest).toBeVisible();
  await expect(acmeTest.locator(".verdict")).toHaveText("challenges");
  await expect(acmeTest).toContainText("Reported revenue does not establish that automation demand improves margins.");
  // Citation button
  await expect(acmeTest.getByRole("button", { name: "Source 1" })).toBeAttached();

  // SPY candidate without dated fund facts honestly renders verdict as unknown
  const spyTest = page.getByTestId("test-candidate-SPY-XNYS");
  await expect(spyTest).toBeVisible();
  await expect(spyTest.locator(".verdict")).toHaveText("unknown");
  await expect(spyTest).toContainText("Fund facts and dated sponsor holdings are missing; verdict and overlap remain unknown.");

  // Exposure row contains unknown look-through note for SPY
  await expect(memo).toContainText("SPY: look-through and overlap are unknown (no dated fund facts).");

  // Alternatives and downside
  await expect(memo).toContainText("Alternatives");
  await expect(memo).toContainText("Risks");

  // 7. Check Scenarios tab renders company cases
  await memo.getByRole("button", { name: /Scenarios/ }).click();
  const details = page.getByRole("complementary", { name: "Details" });
  await expect(details.getByRole("tab", { name: "Scenarios" })).toHaveAttribute("aria-selected", "true");
  await expect(details).toContainText("Acme Corp · per share, discounted to today");
  await expect(details.locator(".case")).toHaveCount(3);

  // Check Evidence tab
  await details.getByRole("tab", { name: /Evidence/ }).click();
  await expect(details).toContainText("Acme annual filing");

  // 8. Save decision and record action
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByTestId("saved-message")).toContainText("Saved to Decisions");
  await page.getByRole("radio", { name: "Took no action" }).check();
  await page.getByRole("button", { name: "Record action" }).click();
  await expect(memo).toContainText("Saved and recorded. You: no action.");

  await expect(page.locator("body")).not.toContainText("sk-test-backend-only-never-browser");
});

test("starters button opens /theme workflow in composer", async ({ page }) => {
  await open(page);
  const starter = page.getByRole("button", { name: /\/theme Test an idea/ });
  await expect(starter).toBeVisible();
  await starter.click();

  await expect(page.getByRole("group", { name: "Theme inputs" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("What theme should we explore?");
});
