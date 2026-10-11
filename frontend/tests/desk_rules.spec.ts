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
        id: "tfsa-cash-cad",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "1000",
      },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.5" },
  unresolved: [],
};

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await page.request.delete("/api/test/portfolio");
  expect((await page.request.put("/api/portfolio", { data: saved })).ok()).toBeTruthy();
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
}

test("target mix baseline inputs are editable, persist partial targets, enforce sum <= 100%, and clearing restores unknown", async ({
  page,
}) => {
  await open(page);

  // Open details panel on Holdings tab
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();

  // Locate baseline inputs
  const stocksInput = details.getByRole("textbox", { name: "Stocks (%)" });
  const divEtfInput = details.getByRole("textbox", { name: "Diversified ETFs (%)" });
  const secEtfInput = details.getByRole("textbox", { name: "Sector/theme ETFs (%)" });
  const cashInput = details.getByRole("textbox", { name: "Cash (%)" });

  await expect(stocksInput).toBeVisible();
  await expect(divEtfInput).toBeVisible();
  await expect(secEtfInput).toBeVisible();
  await expect(cashInput).toBeVisible();

  // 1. Set partial baseline mix: Stocks 20%, Cash 10%
  await stocksInput.fill("20");
  await cashInput.fill("10");

  // Verify persistence via /api/portfolio polling
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.settings?.baseline;
  }).toEqual({
    stocks: "0.2",
    diversified_etfs: null,
    sector_theme_etfs: null,
    cash: "0.1",
  });

  // 2. Set Diversified ETFs 50% and Sector/theme ETFs 15% (total = 20 + 10 + 50 + 15 = 95% <= 100%)
  await divEtfInput.fill("50");
  await secEtfInput.fill("15");

  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.settings?.baseline;
  }).toEqual({
    stocks: "0.2",
    diversified_etfs: "0.5",
    sector_theme_etfs: "0.15",
    cash: "0.1",
  });

  // 3. Exceed 100%: Change Cash to 30% (total = 20 + 50 + 15 + 30 = 115%)
  await cashInput.fill("30");

  // Verify client alert is visible
  await expect(details.getByRole("alert")).toContainText("Target mix cannot exceed 100%");

  // Verify backend validation rejects sum > 100% if sent
  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    // The invalid 115% sum must NOT be persisted as valid portfolio settings
    const b = p?.settings?.baseline;
    if (!b) return true;
    const sum = (Number(b.stocks || 0) + Number(b.diversified_etfs || 0) + Number(b.sector_theme_etfs || 0) + Number(b.cash || 0));
    return sum <= 1.0001;
  }).toBe(true);

  // 4. Restore valid sum: set Cash back to 5% (total = 90%)
  await cashInput.fill("5");
  await expect(details.getByRole("alert")).toHaveCount(0);

  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.settings?.baseline?.cash;
  }).toBe("0.05");

  // 5. Clearing restores unknown
  await stocksInput.fill("");
  await divEtfInput.fill("");
  await secEtfInput.fill("");
  await cashInput.fill("");

  await expect.poll(async () => {
    const p = await (await page.request.get("/api/portfolio")).json();
    return p?.settings?.baseline;
  }).toBeNull();
});
