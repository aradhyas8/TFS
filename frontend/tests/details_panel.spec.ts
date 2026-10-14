import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

// Details are closed until asked for, at every width. Every opening path shows them, and Escape and the close
// buttons put focus back where it came from.
const ACME = { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
  shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } };
const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [ACME, { id: "tfsa-cash", account_id: "tfsa", kind: "cash", currency: "CAD", cash: "1000" }],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: {},
  settings: { single_company_cap: "0.15" },
  unresolved: [],
};

const details = (page: Page) => page.getByRole("complementary", { name: "Details" });
const detailsButton = (page: Page) => page.getByRole("button", { name: "Details", exact: true });
const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });

async function open(page: Page) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await seedPortfolio(page, SAVED);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

for (const [name, width, height] of [["desktop", 1440, 900], ["tablet", 768, 1024], ["phone", 390, 844]] as const)
  test(`details start closed on a ${name}, open from the Details button and close again`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page);
    await expect(details(page)).toBeHidden();
    await detailsButton(page).click();
    await expect(details(page)).toBeVisible();
    await expect(details(page).getByRole("tab", { name: "Holdings" })).toBeFocused();
    await details(page).getByRole("button", { name: "Close details" }).click();
    await expect(details(page)).toBeHidden();
    await expect(detailsButton(page)).toBeFocused();
    await detailsButton(page).click();
    await expect(details(page)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(details(page)).toBeHidden();
    await expect(detailsButton(page)).toBeFocused();
  });

test("details stay closed after a reload, and an explicit action still opens them", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await detailsButton(page).click();
  await details(page).getByRole("button", { name: "Close details" }).click();
  await page.reload();
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  await expect(details(page)).toBeHidden();
  await detailsButton(page).click();
  await expect(details(page).getByRole("tab", { name: "Holdings" })).toHaveAttribute("aria-selected", "true");
  await page.reload();
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
  await expect(details(page)).toBeHidden();
});

test("a citation opens the details on its source, even after they were closed", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("/stock ACME");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const memo = page.getByRole("region", { name: "Stock analysis" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(details(page)).toBeHidden();

  const source = memo.getByRole("button", { name: /^Source \d+$/ }).first();
  await source.click();
  await expect(details(page).getByRole("tab", { name: /Evidence/ })).toHaveAttribute("aria-selected", "true");
  await details(page).getByRole("button", { name: "Close details" }).click();
  await expect(details(page)).toBeHidden();
  await source.click();
  await expect(details(page)).toBeVisible();
  await expect(details(page).getByRole("tab", { name: /Evidence/ })).toHaveAttribute("aria-selected", "true");

  // A result tab opens them too.
  await details(page).getByRole("button", { name: "Close details" }).click();
  await memo.getByRole("button", { name: /Scenarios/ }).first().click();
  await expect(details(page).getByRole("tab", { name: "Scenarios" })).toHaveAttribute("aria-selected", "true");
});

test("the rail drawer takes focus, closes on Escape and returns focus to the menu button", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await open(page);
  const menu = page.getByRole("button", { name: "Open portfolio and decisions" });
  await expect(rail(page)).toBeHidden();
  await menu.click();
  await expect(rail(page)).toBeVisible();
  await expect(rail(page).getByRole("button", { name: "Close menu" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(rail(page)).toBeHidden();
  await expect(menu).toBeFocused();
  await menu.click();
  await rail(page).getByRole("button", { name: "Close menu" }).click();
  await expect(rail(page)).toBeHidden();
  await expect(menu).toBeFocused();
});

for (const width of [768, 1023])
  test(`touch targets on a ${width}px tablet are at least 44px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1024 });
    await open(page);
    const sizes = async () => page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>(".desk :is(.nav-item, .thread-item, .rail-new, .icon-btn, .btn, .send, .pill, .starter, .ditem)")]
        .filter(element => element.getClientRects().length && getComputedStyle(element).visibility !== "hidden")
        .map(element => [element.className, Math.round(element.getBoundingClientRect().height), Math.round(element.getBoundingClientRect().width)] as const)
        .filter(([, height, w]) => height < 44 || w < 44));
    expect(await sizes()).toEqual([]);
    await page.getByRole("button", { name: "Open portfolio and decisions" }).click();
    await expect(rail(page)).toBeVisible();
    expect(await sizes()).toEqual([]);
  });
