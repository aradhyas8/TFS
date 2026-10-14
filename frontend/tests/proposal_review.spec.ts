import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

// A proposal is reviewed in a side panel: what changes, before and after, the revision it was prepared from, and an
// explicit Confirm that saves a new revision. Closing the review keeps the proposal waiting; only Cancel proposal
// cancels it. Nothing here places a trade.

const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }, { id: "rrsp", name: "RRSP" }],
    positions: [
      { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
        shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
      { id: "rrsp-broad", account_id: "rrsp", kind: "etf", currency: "USD", ticker: "BROAD", listing: "XNYS", etf_role: "diversified",
        shares: "20", mark: { value: "50", as_of: "2026-09-30", source: "Broker display" } },
      { id: "rrsp-cash-cad", account_id: "rrsp", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80" },
  settings: { single_company_cap: "0.15", active_budget: "0.3", indirect_cap_policy: "direct_only" },
  unresolved: [],
};

const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const portfolio = (page: Page) => page.getByRole("region", { name: "Portfolio" });
const review = (page: Page) => page.getByRole("dialog", { name: "Review portfolio changes" });
const changes = (page: Page) => review(page).getByRole("table", { name: "What changes" });
const saved = async (page: Page) => (await (await page.request.get("/api/portfolio")).json()) as { revision: number; portfolio: typeof SAVED };
const writes = (page: Page) => {
  const seen: string[] = [];
  page.on("request", request => { if (/\/api\/portfolio\/proposals\/[^/]+\/(confirm|cancel)$/.test(request.url())) seen.push(request.url().split("/").pop()!); });
  return seen;
};

async function open(page: Page, width = 1440) {
  await page.setViewportSize({ width, height: 900 });
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await page.request.delete("/api/decisions");
  await seedPortfolio(page, SAVED);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

/** From the Portfolio view: change BROAD's fund type and ask to review it. Returns the Review changes button. */
async function prepareFromPortfolio(page: Page) {
  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  await portfolio(page).getByLabel("Fund type for BROAD").selectOption("sector_theme");
  const button = portfolio(page).getByRole("region", { name: "Unsaved draft" }).getByRole("button", { name: "Review changes" });
  await button.click();
  await expect(review(page)).toBeVisible();
  return button;
}

/** Another session commits a rule change, so any proposal prepared from the current revision is out of date. */
async function commitElsewhere(page: Page, base: number) {
  const next = await page.request.put("/api/portfolio", { data: { base_revision: base, portfolio: { ...SAVED, settings: { single_company_cap: "0.5" } } } });
  expect((await page.request.post(`/api/portfolio/proposals/${(await next.json()).proposal.id}/confirm`)).ok()).toBeTruthy();
}

test("prepare, review before and after, confirm: a new revision and its receipt", async ({ page }) => {
  await open(page);
  const seen = writes(page);
  await prepareFromPortfolio(page);
  const sheet = review(page);

  await expect(sheet).toContainText("Your edits · prepared from revision 1");
  await expect(sheet.getByText("Your portfolio is at revision 1")).toBeVisible();
  await expect(changes(page).getByRole("columnheader")).toHaveText(["What changes", "Before", "After"]);
  await expect(changes(page).getByRole("row", { name: /BROAD/ })).toHaveText(/BROAD.*RRSP · fund type.*Diversified.*Sector or theme/);
  await expect(sheet.getByRole("list", { name: "Left as they are" })).toContainText("Holdings date");
  await expect(sheet).toContainText("It updates your records only. It does not place a trade.");
  expect((await saved(page)).revision).toBe(1);

  await sheet.getByRole("button", { name: "Confirm changes" }).click();
  await expect(sheet).toHaveCount(0);
  const receipt = page.getByRole("status").filter({ hasText: "Saved as portfolio revision 2" });
  await expect(receipt).toContainText("Was revision 1");
  await expect(receipt).toContainText("No trade was placed");
  expect((await saved(page)).revision).toBe(2);
  expect((await saved(page)).portfolio.snapshot.positions.find(row => row.id === "rrsp-broad")?.etf_role).toBe("sector_theme");
  expect(seen).toEqual(["confirm"]);
  // The draft is the committed portfolio now.
  await expect(portfolio(page).getByRole("region", { name: "Unsaved draft" })).toBeHidden();
});

test("closing keeps the proposal waiting; Cancel proposal cancels it and changes nothing", async ({ page }) => {
  await open(page);
  const seen = writes(page);
  const opener = await prepareFromPortfolio(page);

  // Escape closes the review, returns focus to what opened it, and keeps the proposal.
  await page.keyboard.press("Escape");
  await expect(review(page)).toHaveCount(0);
  await expect(opener).toBeFocused();
  const [waiting] = await (await page.request.get("/api/portfolio/proposals?status=pending")).json();
  expect(waiting).toBeTruthy();

  // It waits in Changes; reviewing it again opens the same proposal.
  await portfolio(page).getByRole("tab", { name: /Changes/ }).click();
  await portfolio(page).getByTestId(`pending-proposal-item-${waiting.id}`).getByRole("button", { name: "Review" }).click();
  await review(page).getByRole("button", { name: "Cancel proposal" }).click();
  await expect(review(page)).toHaveCount(0);
  expect(seen).toEqual(["cancel"]);
  expect((await saved(page)).revision).toBe(1);
  expect((await saved(page)).portfolio.snapshot.positions.find(row => row.id === "rrsp-broad")?.etf_role).toBe("diversified");
  // The unsaved edit is still a draft.
  await expect(portfolio(page).getByRole("region", { name: "Unsaved draft" })).toContainText("BROAD fund type Diversified to Sector or theme");
});

test("a waiting proposal is reopened after a reload and confirmed", async ({ page }) => {
  await open(page);
  await prepareFromPortfolio(page);
  await review(page).getByRole("button", { name: "Close drawer" }).click();
  await page.reload();

  const waiting = page.getByRole("region", { name: "Changes waiting for you" });
  await expect(waiting).toContainText("Your edits");
  await expect(waiting).toContainText("Prepared from revision 1 · 1 change");
  await waiting.getByRole("button", { name: "Review" }).click();
  await expect(changes(page).getByRole("row", { name: /BROAD/ })).toContainText("Sector or theme");
  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved as portfolio revision 2" })).toBeVisible();
  await expect(waiting).toHaveCount(0);
});

test("an out-of-date proposal cannot be confirmed, says why, and keeps the draft", async ({ page }) => {
  await open(page);
  const seen = writes(page);
  await prepareFromPortfolio(page);
  // The portfolio moves on while the review is open; confirming is refused and never rebased.
  await commitElsewhere(page, 1);
  await review(page).getByRole("button", { name: "Confirm changes" }).click();

  const alert = review(page).getByRole("alert").filter({ hasText: "This proposal is out of date" });
  await expect(alert).toContainText("prepared from revision 1");
  await expect(alert).toContainText("It won't be moved onto the newer portfolio");
  await expect(review(page).getByRole("button", { name: "Confirm changes" })).toBeDisabled();
  expect((await saved(page)).revision).toBe(2);
  expect((await saved(page)).portfolio.snapshot.positions.find(row => row.id === "rrsp-broad")?.etf_role).toBe("diversified");
  expect(seen).toEqual(["confirm"]);

  // Closing it keeps the unsaved edit, and the refused proposal is no longer offered as waiting.
  await page.keyboard.press("Escape");
  await expect(review(page)).toHaveCount(0);
  await expect(portfolio(page).getByRole("region", { name: "Unsaved draft" })).toContainText("BROAD fund type Diversified to Sector or theme");
  await portfolio(page).getByRole("tab", { name: /Changes/ }).click();
  await expect(portfolio(page).getByRole("button", { name: "Review", exact: true })).toHaveCount(0);
});

test("a double click confirms once", async ({ page }) => {
  await open(page);
  const seen = writes(page);
  // Slow the reply so the second click lands while the first is in flight.
  await page.route("**/api/portfolio/proposals/*/confirm", async route => { await new Promise(done => setTimeout(done, 400)); await route.continue(); });
  await prepareFromPortfolio(page);
  const confirm = review(page).getByRole("button", { name: /Confirm changes|Confirming/ });
  await confirm.dblclick();
  await expect(review(page).getByRole("button", { name: "Confirming…" })).toBeDisabled();
  await expect(review(page).getByRole("button", { name: "Cancel proposal" })).toBeDisabled();
  await expect(review(page)).toHaveCount(0);
  expect(seen).toEqual(["confirm"]);
  expect((await saved(page)).revision).toBe(2);
});

test("a confirmation that gets no reply can be retried, and applies once", async ({ page }) => {
  await open(page);
  await prepareFromPortfolio(page);
  // The request reaches the server, but the reply is lost.
  await page.route("**/api/portfolio/proposals/*/confirm", async route => { await route.fetch(); await route.abort("failed"); }, { times: 1 });
  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(review(page).getByRole("alert")).toContainText("The confirmation got no reply");
  await expect(review(page).getByRole("button", { name: "Confirm changes" })).toBeEnabled();

  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved as portfolio revision 2" })).toBeVisible();
  expect((await saved(page)).revision).toBe(2);
});

test("a change asked for in the conversation is reviewed with its origin, before and after", async ({ page }) => {
  await open(page);
  await page.getByRole("textbox", { name: /type \/ for workflows/ }).fill("Set my ACME shares to 12");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const card = page.getByRole("log", { name: "Conversation" }).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(card).toContainText("ACME");
  await expect(card).toContainText("10 → 12");
  await card.getByRole("button", { name: "Review changes" }).click();
  await expect(review(page)).toContainText("From a conversation · prepared from revision 1");
  await expect(changes(page).getByRole("row", { name: /ACME/ })).toHaveText(/ACME.*TFSA · shares.*10.*12/);
  await review(page).getByRole("button", { name: "Confirm changes" }).click();
  await expect(card).toContainText("Your portfolio moved from revision 1 to revision 2.");
});

test("an import shows the holdings it adds and removes", async ({ page }) => {
  await open(page);
  await rail(page).getByRole("button", { name: /^Portfolio/ }).click();
  await portfolio(page).getByRole("button", { name: "Import CSV" }).click();
  await portfolio(page).getByLabel("Load portfolio CSV").setInputFiles({
    name: "replacement.csv", mimeType: "text/csv",
    buffer: Buffer.from("account,ticker,shares,average_cost,type\nTFSA,XEQT.TO,100,30,etf\n"),
  });
  await expect(review(page)).toContainText("Holdings import · prepared from revision 1");
  await expect(changes(page).getByRole("row", { name: /BROAD/ })).toContainText("Removed");
  await expect(changes(page).getByRole("row", { name: /XEQT/ }).first()).toContainText("Not held");
  await expect(changes(page).getByRole("row", { name: /XEQT/ }).first()).toContainText("Not held");
  await review(page).getByRole("button", { name: "Cancel proposal" }).click();
  expect((await saved(page)).revision).toBe(1);
});

test("the review fits a phone: every change and both actions are reachable without sideways scrolling", async ({ page }) => {
  await open(page, 390);
  await page.getByRole("button", { name: "Open portfolio and decisions" }).click();
  await prepareFromPortfolio(page);
  const sheet = review(page);
  // Measured once the panel has finished sliding in.
  await expect.poll(async () => (await sheet.boundingBox())!.x + (await sheet.boundingBox())!.width).toBeLessThanOrEqual(390);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(390);
  for (const name of ["Confirm changes", "Cancel proposal"]) {
    const box = (await sheet.getByRole("button", { name }).boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(900);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
  await expect(changes(page).getByRole("row", { name: /BROAD/ })).toBeInViewport();

  // Focus stays inside the review.
  for (let step = 0; step < 8; step++) {
    await page.keyboard.press("Tab");
    expect(await sheet.evaluate(node => node.contains(document.activeElement))).toBe(true);
  }
});
