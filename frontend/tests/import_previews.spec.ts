import { test, expect, type Page } from "@playwright/test";

const SIMPLE_CSV = `account,ticker,shares,average_cost,type
TFSA,XEQT.TO,100,30,etf
TFSA,CASH,1000,,cash
`;

const REPLACEMENT_CSV = `account,ticker,shares,average_cost,type
TFSA,XEQT.TO,100,30,etf
TFSA,CASH,500,,cash
`;

const MALFORMED_CSV = `ticker,shares
ACME,10
`;

const INITIAL_PORTFOLIO = {
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
        id: "tfsa-cash",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "1000",
      },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.15" },
  benchmark: {
    ticker: "SPY",
    listing: "XNYS",
    currency: "USD",
    name: "SPDR S&P 500 ETF Trust",
  },
  unresolved: [],
};

async function seed(page: Page, saved: object) {
  await page.request.delete("/api/test/portfolio");
  const prep = await page.request.put("/api/portfolio", {
    data: { base_revision: null, portfolio: saved },
  });
  if (!prep.ok()) {
    throw new Error(`seed PUT failed: ${prep.status()} ${await prep.text()}`);
  }
  const prepJson = await prep.json();
  if (prepJson.status === "prepared" && prepJson.proposal) {
    const confirm = await page.request.post(`/api/portfolio/proposals/${prepJson.proposal.id}/confirm`);
    expect(confirm.ok()).toBeTruthy();
  }
}

async function routeLocalOnly(page: Page) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
}

test.describe("Ticket 06: First import and replacement-import previews", () => {
  test("first-time import opens preview without adopting, cancel leaves onboarding intact, confirm commits revision 1", async ({
    page,
  }) => {
    await routeLocalOnly(page);
    await page.request.delete("/api/test/portfolio");
    await page.goto("/");

    // 1. Initial state: Onboarding hero is visible
    await expect(page.getByRole("heading", { name: "Start with what you own." })).toBeVisible();

    // 2. Upload CSV
    await page.getByLabel("Holdings as of", { exact: true }).fill("2026-09-30");
    await page.getByLabel("Reporting currency", { exact: true }).fill("CAD");
    await page.getByLabel("Load portfolio CSV").setInputFiles({
      name: "holdings.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(SIMPLE_CSV),
    });

    // 3. Preview modal opens without adopting into committed state
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByText("Holdings import · Initial portfolio creation")).toBeVisible();

    // Check backend directly: committed portfolio is STILL absent (null)
    const committedCheck = await page.request.get("/api/portfolio");
    expect(committedCheck.status()).toBe(200);
    const body = await committedCheck.json();
    expect(body).toBeNull();

    // 4. Cancel the preview
    await page.getByTestId("cancel-proposal").click();
    await expect(dialog).toBeHidden();

    // Still in first-time setup state
    await expect(page.getByRole("heading", { name: "Start with what you own." })).toBeVisible();
    const stillNull = await page.request.get("/api/portfolio");
    expect(await stillNull.json()).toBeNull();

    // 5. Upload again and confirm
    await page.getByLabel("Load portfolio CSV").setInputFiles({
      name: "holdings.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(SIMPLE_CSV),
    });
    await expect(dialog).toBeVisible();

    // Confirm changes
    await page.getByTestId("confirm-proposal").click();
    await expect(dialog).toBeHidden();

    // Now committed state is adopted: hero is gone, desk is shown
    await expect(page.getByRole("heading", { name: "Start with what you own." })).toBeHidden();
    await expect(page.getByRole("complementary", { name: "Details" })).toBeVisible();

    // Check backend: Revision 1 committed
    const adoptedCheck = await page.request.get("/api/portfolio");
    const adopted = await adoptedCheck.json();
    expect(adopted.revision).toBe(1);
    expect(adopted.portfolio.snapshot.positions.some((p: any) => p.ticker === "XEQT")).toBeTruthy();
  });

  test("replacement import shows removed holdings, preserves rules and benchmark upon confirmation", async ({
    page,
  }) => {
    await routeLocalOnly(page);
    await seed(page, INITIAL_PORTFOLIO);
    await page.goto("/");

    // Open Panel -> Holdings tab
    const panel = page.getByRole("complementary", { name: "Details" });
    await expect(panel).toBeVisible();
    await panel.getByRole("tab", { name: "Holdings" }).click();

    // Verify initial rules and benchmark are present
    await expect(panel.getByLabel("Single-company cap (%)")).toHaveValue("15");

    // Upload replacement CSV (replaces ACME with XEQT)
    await panel.getByLabel("Load portfolio CSV").setInputFiles({
      name: "replacement.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(REPLACEMENT_CSV),
    });

    // Preview dialog opens
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(page.getByText("Holdings import · Base revision 1")).toBeVisible();

    // Exact differences must show removals (ACME was removed)
    await expect(dialog.getByText(/Removed position ACME/i)).toBeVisible();
    await expect(dialog.getByText(/Added position XEQT/i)).toBeVisible();

    // Confirm replacement
    await page.getByTestId("confirm-proposal").click();
    await expect(dialog).toBeHidden();

    // Verify committed portfolio in backend: revision 2, ACME gone, rules and benchmark preserved
    const adoptedCheck = await page.request.get("/api/portfolio");
    const adopted = await adoptedCheck.json();
    expect(adopted.revision).toBe(2);
    expect(adopted.portfolio.snapshot.positions.some((p: any) => p.ticker === "ACME")).toBeFalsy();
    expect(adopted.portfolio.snapshot.positions.some((p: any) => p.ticker === "XEQT")).toBeTruthy();
    expect(adopted.portfolio.settings.single_company_cap).toBe("0.15");
    expect(adopted.portfolio.benchmark.ticker).toBe("SPY");

    // UI panel still displays preserved single-company cap (15%)
    await expect(panel.getByLabel("Single-company cap (%)")).toHaveValue("15");
  });

  test("invalid CSV reports error leaving committed state and drafts intact", async ({
    page,
  }) => {
    await routeLocalOnly(page);
    await seed(page, INITIAL_PORTFOLIO);
    await page.goto("/");

    // Open panel and make a draft edit to single-company cap
    const panel = page.getByRole("complementary", { name: "Details" });
    await panel.getByRole("tab", { name: "Holdings" }).click();
    await panel.getByLabel("Single-company cap (%)").fill("35");

    // Upload malformed CSV
    await panel.getByLabel("Load portfolio CSV").setInputFiles({
      name: "bad.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(MALFORMED_CSV),
    });

    // Alert displays invalid CSV error
    await expect(page.locator("main").getByRole("alert")).toContainText("Invalid portfolio CSV");

    // Preview dialog did NOT open
    await expect(page.getByRole("dialog")).toBeHidden();

    // Draft edit (35%) is preserved in input
    await expect(panel.getByLabel("Single-company cap (%)")).toHaveValue("35");

    // Committed portfolio in backend remains at revision 1 with original cap (0.15)
    const check = await page.request.get("/api/portfolio");
    const json = await check.json();
    expect(json.revision).toBe(1);
    expect(json.portfolio.settings.single_company_cap).toBe("0.15");
  });

  test("pending import preview survives reload and can be reopened or cancelled", async ({
    page,
  }) => {
    await routeLocalOnly(page);
    await page.request.delete("/api/test/portfolio");
    await page.goto("/");

    // Upload CSV
    await page.getByLabel("Holdings as of", { exact: true }).fill("2026-09-30");
    await page.getByLabel("Reporting currency", { exact: true }).fill("CAD");
    await page.getByLabel("Load portfolio CSV").setInputFiles({
      name: "holdings.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(SIMPLE_CSV),
    });

    // Preview dialog appears
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // Reload the page without confirming or cancelling
    await page.reload();

    // Dialog is not currently open, but pending proposal banner is displayed
    const banner = page.getByTestId("pending-proposal-banner");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Pending review: Holdings import");

    // Click Reopen preview
    await banner.getByTestId("reopen-pending-proposal").click();
    await expect(dialog).toBeVisible();
    await expect(page.getByText("Holdings import · Initial portfolio creation")).toBeVisible();

    // Cancel from preview dialog
    await page.getByTestId("cancel-proposal").click();
    await expect(dialog).toBeHidden();

    // Reload again: proposal is cancelled, so banner is gone
    await page.reload();
    await expect(page.getByTestId("pending-proposal-banner")).toBeHidden();
  });
});
