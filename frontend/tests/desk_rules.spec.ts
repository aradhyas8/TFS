import { test, expect, type Page } from "@playwright/test";

const SAVED = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
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
        id: "tfsa-xeqt",
        account_id: "tfsa",
        kind: "etf",
        currency: "CAD",
        ticker: "XEQT",
        listing: "XTSE",
        company_id: "xeqt",
        company_name: "iShares Core Equity",
        shares: "50",
        mark: { value: "30", as_of: "2026-09-30", source: "Broker display" },
      },
      {
        id: "tfsa-cash-cad",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "1000",
      },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80", "tfsa-xeqt": "25" },
  settings: { single_company_cap: "0.5" },
  unresolved: [],
};

async function seedPortfolio(page: Page, saved: object = SAVED) {
  await page.request.delete("/api/test/portfolio");
  const prep = await page.request.put("/api/portfolio", {
    data: { base_revision: null, portfolio: saved },
  });
  expect(prep.ok()).toBeTruthy();
  const prepJson = await prep.json();
  if (prepJson.status === "prepared" && prepJson.proposal) {
    const confirm = await page.request.post(`/api/portfolio/proposals/${prepJson.proposal.id}/confirm`);
    expect(confirm.ok()).toBeTruthy();
  }
}

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await seedPortfolio(page, saved);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
}

test("typing changes draft state only without autosave; review opens differences and confirm adopts revision", async ({
  page,
}) => {
  await open(page);

  // Open details panel on Holdings tab
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  const stocksInput = details.getByRole("textbox", { name: "Stocks (%)" });
  const cashInput = details.getByRole("textbox", { name: "Cash (%)" });

  await expect(stocksInput).toBeVisible();
  await expect(cashInput).toBeVisible();

  // 1. Typing modifies draft state only
  await stocksInput.fill("20");
  await cashInput.fill("10");

  // Visible unsaved banner is displayed
  await expect(details.getByText("Unsaved draft edits")).toBeVisible();

  // Wait 600ms (autosave was previously 400ms); verify NO autosave request committed changes
  await page.waitForTimeout(600);
  const current = await (await page.request.get("/api/portfolio")).json();
  expect(current.revision).toBe(1);
  expect(current.portfolio.settings.baseline).toBeNull();

  // 2. Review changes opens proposal preview with exact differences
  const reviewBtn = details.getByTestId("review-changes");
  await reviewBtn.click();

  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("Portfolio settings modified")).toBeVisible();

  // 3. Confirm adopts returned committed revision
  const confirmBtn = dialog.getByTestId("confirm-proposal");
  await confirmBtn.click();
  await expect(dialog).toHaveCount(0);

  // Polling confirms committed revision advanced to 2 with new baseline
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return {
      revision: p?.revision,
      baseline: p?.portfolio?.settings?.baseline,
    };
  }).toEqual({
    revision: 2,
    baseline: {
      stocks: "0.2",
      diversified_etfs: null,
      sector_theme_etfs: null,
      cash: "0.1",
    },
  });

  // Draft banner is no longer shown
  await expect(details.getByText("Unsaved draft edits")).toHaveCount(0);
});

test("cancel leaves committed state unchanged and preserves draft", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  const divEtfInput = details.getByRole("textbox", { name: "Diversified ETFs (%)" });
  const secEtfInput = details.getByRole("textbox", { name: "Sector/theme ETFs (%)" });

  await divEtfInput.fill("50");
  await secEtfInput.fill("15");

  await details.getByTestId("review-changes").click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();

  // Cancel preview
  await dialog.getByTestId("cancel-proposal").click();
  await expect(dialog).toHaveCount(0);

  // Committed state remains unchanged (revision 1, baseline null)
  const current = await (await page.request.get("/api/portfolio")).json();
  expect(current.revision).toBe(1);
  expect(current.portfolio.settings.baseline).toBeNull();

  // Draft inputs are still preserved
  await expect(divEtfInput).toHaveValue("50");
  await expect(secEtfInput).toHaveValue("15");
});

test("enforces target mix sum <= 100% and disables review", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  const stocksInput = details.getByRole("textbox", { name: "Stocks (%)" });
  const cashInput = details.getByRole("textbox", { name: "Cash (%)" });

  await stocksInput.fill("60");
  await cashInput.fill("50");

  // Sum = 110% > 100%
  await expect(details.getByRole("alert")).toContainText("Target mix cannot exceed 100%");
  await expect(details.getByTestId("review-changes")).toBeDisabled();

  // Restore valid sum
  await cashInput.fill("20");
  await expect(details.getByRole("alert")).toHaveCount(0);
  await expect(details.getByTestId("review-changes")).toBeEnabled();
});

test("rules, benchmark, and ETF-role edits follow the same action model", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  // 1. Edit ETF role
  const etfSelect = details.getByRole("combobox", { name: /Fund type for XEQT/ });
  await etfSelect.selectOption("diversified");

  // 2. Set Benchmark
  const benchmarkInput = details.getByRole("textbox", { name: "Benchmark ETF" });
  await benchmarkInput.fill("SPY");
  await details.getByRole("button", { name: "Set" }).click();
  await expect(details.getByText(/Current benchmark: SPY/)).toBeVisible();

  // Review changes
  await details.getByTestId("review-changes").click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();

  // Verify both differences exist
  await expect(dialog.getByText(/Benchmark/)).toBeVisible();
  await expect(dialog.getByText(/etf_role/)).toBeVisible();

  // Confirm changes
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);

  // Verify backend committed state
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    const xeqt = p?.portfolio?.snapshot?.positions?.find((pos: any) => pos.ticker === "XEQT");
    return {
      revision: p?.revision,
      benchmarkTicker: p?.portfolio?.benchmark?.ticker,
      etfRole: xeqt?.etf_role,
    };
  }).toEqual({
    revision: 2,
    benchmarkTicker: "SPY",
    etfRole: "diversified",
  });
});

test("ordinary analysis uses committed state while draft differs", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  // Edit single company cap to 15% (committed is 50%)
  const capInput = details.getByRole("textbox", { name: "Single-company cap (%)" });
  await capInput.fill("15");

  // Draft is unsaved, do NOT review or confirm
  await expect(details.getByText("Unsaved draft edits")).toBeVisible();

  // Track analyze request
  const analyzeRequests: any[] = [];
  page.on("request", req => {
    if (req.url().endsWith("/api/analyze")) {
      analyzeRequests.push(req.postDataJSON());
    }
  });

  // Run ordinary analysis from composer
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("Review my portfolio");
  await page.keyboard.press("Enter");

  // Wait for analyze request and verify it used the COMMITTED single company cap (0.5), not the draft (0.15)
  await expect.poll(() => analyzeRequests.length).toBeGreaterThan(0);
  expect(analyzeRequests[0]?.settings?.single_company_cap).toBe("0.5");

  // Verify draft input is STILL "15" and unsaved
  await expect(capInput).toHaveValue("15");
  await expect(details.getByText("Unsaved draft edits")).toBeVisible();
});

test("stale previews preserve draft work and require fresh review", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  const stocksInput = details.getByRole("textbox", { name: "Stocks (%)" });
  await stocksInput.fill("35");

  // Open preview dialog (prepared against base_revision 1)
  await details.getByTestId("review-changes").click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();

  // In the background, advance the committed portfolio to revision 2
  const competingPrep = await page.request.put("/api/portfolio", {
    data: {
      base_revision: 1,
      portfolio: {
        ...SAVED,
        settings: { single_company_cap: "0.2" },
      },
    },
  });
  expect(competingPrep.ok()).toBeTruthy();
  const competingProposal = (await competingPrep.json()).proposal;
  const competingConfirm = await page.request.post(
    `/api/portfolio/proposals/${competingProposal.id}/confirm`
  );
  expect(competingConfirm.ok()).toBeTruthy();

  // Now click Confirm in the browser preview (which targets base_revision 1 and is now stale)
  await dialog.getByTestId("confirm-proposal").click();

  // Stale alert is displayed in dialog
  await expect(dialog.getByText("This proposal is stale.", { exact: true })).toBeVisible();

  // Dismiss dialog
  await dialog.getByTestId("cancel-proposal").click();
  await expect(dialog).toHaveCount(0);

  // Draft edits are preserved!
  await expect(stocksInput).toHaveValue("35");
});

test("confirmation controls support keyboard operation", async ({ page }) => {
  await open(page);

  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  const stocksInput = details.getByRole("textbox", { name: "Stocks (%)" });
  await stocksInput.fill("40");

  // Open preview dialog
  await details.getByTestId("review-changes").click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();

  // Press Escape to cancel
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  // Committed state remains unchanged
  const current = await (await page.request.get("/api/portfolio")).json();
  expect(current.revision).toBe(1);
});
