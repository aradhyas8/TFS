import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

// Direction 5 acceptance across screens: the same layout rules hold on every view and width, the rail survives
// resizing, the draft bar never hides what it describes, and every confirmation receipt is read out.

const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
        shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
      ...["BROAD", "WIDE", "TECH", "BANK", "GOLD"].map((ticker, at) => ({
        id: `rrsp-${ticker.toLowerCase()}`, account_id: "rrsp", kind: "etf", currency: "CAD", ticker, listing: "XTSE", etf_role: "diversified",
        shares: String(20 + at), mark: { value: "50", as_of: "2026-09-30", source: "Broker display" } })),
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const menu = (page: Page) => page.getByRole("button", { name: "Open portfolio and decisions" });
const review = (page: Page) => page.getByRole("dialog", { name: "Review portfolio changes" });
const announced = (page: Page) => page.locator(".desk > p.sr-only[aria-live='polite']");

async function open(page: Page, width = 1440, height = 900) {
  await page.setViewportSize({ width, height });
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/decisions");
  await seedPortfolio(page, SAVED);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

/** Opens a rail destination, through the menu below 1024. */
async function go(page: Page, name: RegExp) {
  if (await menu(page).isVisible()) await menu(page).click();
  await rail(page).getByRole("button", { name }).click();
}

/** Pairs of the view header's visible title, chips and buttons that overlap one another. */
const headerOverlaps = (page: Page) => page.locator(".view-head").evaluate(head => {
  const items = [...head.querySelectorAll<HTMLElement>("h1, .chip, button")].filter(node => node.getClientRects().length).map(node => ({ text: node.textContent, box: node.getBoundingClientRect() }));
  return items.flatMap((a, i) => items.slice(i + 1).filter(b => a.box.left < b.box.right - 1 && b.box.left < a.box.right - 1 && a.box.top < b.box.bottom - 1 && b.box.top < a.box.bottom - 1).map(b => `${a.text} / ${b.text}`));
});
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test("the rail drawer stays open, and the page stays usable, while the window is resized", async ({ page }) => {
  await open(page, 768);
  await menu(page).click();
  await expect(rail(page)).toBeVisible();
  // Longer than the drawer's slide, so a drawer that closed itself would be caught.
  await page.waitForTimeout(600);
  await expect(rail(page)).toBeVisible();

  // Wider than 1024 the rail is a column: nothing is covered and nothing is inert.
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(rail(page)).toBeVisible();
  await expect(page.locator("[inert]")).toHaveCount(0);
  await expect(page.locator(".scrim")).toBeHidden();
  await expect(page.getByRole("textbox", { name: /type \/ for workflows/ })).toBeEditable();

  // Back below 1024 the rail is a drawer again, and it does not reopen by itself over the page.
  await page.setViewportSize({ width: 768, height: 900 });
  await expect(rail(page)).toBeHidden();
  await expect(page.locator("[inert]")).toHaveCount(0);
  await menu(page).click();
  await expect(rail(page)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(rail(page)).toBeHidden();
  await expect(menu(page)).toBeFocused();
});

for (const width of [320, 390, 768, 1024, 1279, 1440]) test(`nothing scrolls sideways at ${width}px: home, Portfolio, Decisions, a conversation`, async ({ page }) => {
  await open(page, width);
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  await go(page, /^Portfolio/);
  await expect(page.getByRole("region", { name: "Portfolio" })).toBeVisible();
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "Back to chat" }).click();
  await go(page, /^Decisions/);
  await expect(page.getByRole("heading", { name: "Decisions", level: 1 })).toBeVisible();
  expect(await sideways(page)).toBeLessThanOrEqual(0);
  await page.getByRole("button", { name: "Back to chat" }).click();
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("Set my ACME shares to 12");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("article", { name: "Proposed portfolio changes" })).toBeVisible();
  expect(await sideways(page)).toBeLessThanOrEqual(0);
});

for (const width of [320, 390, 768, 1440]) test(`at ${width}px the draft bar never covers the holding being edited`, async ({ page }) => {
  await open(page, width, 640);
  await go(page, /^Portfolio/);
  const view = page.getByRole("region", { name: "Portfolio" });
  await view.getByLabel("Fund type for BROAD").selectOption("sector_theme");
  const bar = view.getByRole("region", { name: "Unsaved draft" });
  await expect(bar).toBeVisible();
  // Every fund type, reached by keyboard, is in view above the bar.
  for (const ticker of ["BROAD", "WIDE", "TECH", "BANK", "GOLD"]) {
    const select = view.getByLabel(`Fund type for ${ticker}`);
    await select.focus();
    const field = (await select.boundingBox())!, over = (await bar.boundingBox())!;
    expect(field.y + field.height, `${ticker} is hidden behind the draft bar`).toBeLessThanOrEqual(over.y);
    expect(field.y).toBeGreaterThanOrEqual(0);
  }
  // Scrolled to the end, the last holding and the freshness line sit above the bar.
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await expect.poll(() => page.evaluate(() => Math.ceil(window.scrollY + innerHeight) >= document.documentElement.scrollHeight)).toBe(true);
  const fresh = (await view.locator(".pfresh").boundingBox())!, over = (await bar.boundingBox())!;
  expect(fresh.y + fresh.height).toBeLessThanOrEqual(over.y);
});

test("a receipt is read out after confirming from the conversation and from Portfolio", async ({ page }) => {
  await open(page);
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("Set my ACME shares to 12");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByRole("article", { name: "Proposed portfolio changes" }).getByRole("button", { name: "Review changes" }).click();
  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(announced(page)).toHaveText("Saved as portfolio revision 2. No trade was placed.");

  await go(page, /^Portfolio/);
  const view = page.getByRole("region", { name: "Portfolio" });
  await view.getByLabel("Fund type for BROAD").selectOption("sector_theme");
  await view.getByRole("region", { name: "Unsaved draft" }).getByRole("button", { name: "Review changes" }).click();
  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(announced(page)).toHaveText("Saved as portfolio revision 3. No trade was placed.");
});

test("stored prices and valuations are never called live", async ({ page }) => {
  await open(page);
  await go(page, /^Portfolio/);
  const view = page.getByRole("region", { name: "Portfolio" });
  await expect(view).toContainText("No analysis of this revision yet, so nothing is valued.");
  await expect(view).not.toContainText(/\blive\b(?! research)/i);
  await expect(page.locator("body")).not.toContainText(/real[- ]time|live price|live quote/i);
});

test("a saved analysis is dated as an analysis, and the holdings it used keep their own date", async ({ page }) => {
  await open(page);
  // The analysis runs today against holdings dated Sep 30: nothing may call those holdings or prices today's.
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("Analyze a stock in chat");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await page.getByRole("log", { name: "Conversation" }).getByRole("button", { name: "Analyze ACME" }).click();
  const card = page.getByRole("article", { name: "Stock analysis result" });
  await card.getByRole("button", { name: "Open details" }).click();
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: /Holdings/ }).click();
  await expect(details).toContainText("from your portfolio as of Sep 30, 2026");
  await page.keyboard.press("Escape");
  await card.getByRole("button", { name: "Save decision" }).click();

  await go(page, /^Decisions/);
  const row = page.getByTestId("decision-row").first();
  await expect(row).toContainText(/analysis as of \w+ \d+, \d{4}/);
  await expect(row).not.toContainText("portfolio as of");
  await row.click();
  const band = page.getByTestId("historical-band");
  await expect(band).toContainText(/Historical\. An analysis as of \w+ \d+; its prices, filings and rules are as they were then and are not updated\./);
  await expect(page.getByRole("region", { name: "Reopened decision" })).not.toContainText("snapshot");
});

for (const width of [320, 390]) test(`at ${width}px the view headers and the draft bar show every control whole`, async ({ page }) => {
  await open(page, width, 700);
  const clipped = (selector: string) => page.locator(selector).evaluateAll(nodes =>
    nodes.filter(node => node.getClientRects().length && node.scrollWidth > node.clientWidth + 1).map(node => node.textContent));
  await go(page, /^Portfolio/);
  expect(await headerOverlaps(page)).toEqual([]);
  const back = (await page.getByRole("button", { name: "Back to chat" }).boundingBox())!;
  expect(Math.min(back.width, back.height)).toBeGreaterThanOrEqual(44);
  await page.getByRole("region", { name: "Portfolio" }).getByLabel("Fund type for BROAD").selectOption("sector_theme");
  await expect(page.getByRole("region", { name: "Unsaved draft" })).toBeVisible();
  expect(await clipped(".draft-act > *")).toEqual([]);
  await page.getByRole("button", { name: "Back to chat" }).click();
  await go(page, /^Decisions/);
  expect(await headerOverlaps(page)).toEqual([]);
});

for (const width of [768, 1024, 1100, 1200, 1279, 1440]) test(`at ${width}px the holdings table and its header keep every value whole`, async ({ page }) => {
  await open(page, width);
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("/review Review my portfolio");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByRole("region", { name: "Analyst memo" })).toBeVisible();
  await go(page, /^Portfolio/);
  const view = page.getByRole("region", { name: "Portfolio" });
  await expect(view.getByRole("row", { name: /Cash CAD/ })).toContainText("12.7%");
  // No value, weight or control spills out of its column (the holding name alone is shortened on purpose).
  const spilled = await view.locator(".hr:not(.hh) > [role=cell]:not(.c-hold)").evaluateAll(cells =>
    cells.filter(cell => cell.getClientRects().length && cell.scrollWidth > cell.clientWidth + 1).map(cell => `${cell.className}: ${cell.textContent}`));
  expect(spilled).toEqual([]);
  expect(await headerOverlaps(page)).toEqual([]);
  // The holdings date is always on screen: in the header chip, or above the holdings when the header has no room.
  await expect(view.getByText(/Revision 1 · holdings as of Sep 30, 2026/).locator("visible=true")).toHaveCount(1);
});

test("a server error without a JSON body is reported in words, never as a parsing error", async ({ page }) => {
  await open(page);
  await page.route("**/api/threads**", route => route.request().method() === "POST"
    ? route.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" }) : route.continue());
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("How concentrated is my portfolio?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page.getByText("The analyst service had an error (HTTP 500). Try again.").first()).toBeVisible();
  await expect(page.locator("body")).not.toContainText("JSON");
});
