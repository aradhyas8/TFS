import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

// Direction 5 phase 2: the conversation, its results and sources, the growing composer, and the overlays that keep
// keyboard focus while they cover the page.
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
// A Canadian holding with a price in the case currency, so the cases and the price share one line.
const CANADA = {
  snapshot: { as_of: "2026-09-30", reporting_currency: "CAD", accounts: [{ id: "broker", name: "Brokerage" }],
    positions: [{ id: "p2", account_id: "broker", kind: "stock", currency: "CAD", ticker: "ACME", listing: "XTSE", company_id: "acme", company_name: "Acme",
      shares: "5", mark: { value: "200", as_of: "2026-09-30", source: "Manual mark" } }, { id: "c1", account_id: "broker", kind: "cash", currency: "CAD", cash: "1000" }], fx: [] },
  average_costs: {}, settings: { single_company_cap: "0.6", active_budget: "0.8", indirect_cap_policy: "direct_only" }, unresolved: [],
};

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });
const details = (page: Page) => page.getByRole("complementary", { name: "Details" });
const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await seedPortfolio(page, saved);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

/** Presses Tab (or Shift+Tab) a number of times and returns whether focus stayed inside the element each time. */
async function tabsStayIn(page: Page, selector: string, presses: number, shift = false) {
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press(shift ? "Shift+Tab" : "Tab");
    if (!(await page.evaluate(s => !!document.querySelector(s)?.contains(document.activeElement), selector))) return false;
  }
  return true;
}

for (const [name, width] of [["phone", 390], ["tablet", 768]] as const) {
  test(`the rail drawer keeps focus and makes the page inert on a ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    await page.getByRole("button", { name: "Open portfolio and decisions" }).click();
    await expect(rail(page).getByRole("button", { name: "Close menu" })).toBeFocused();
    await expect(page.getByRole("main")).toHaveAttribute("inert", "");
    expect(await tabsStayIn(page, "#rail", 25)).toBe(true);
    expect(await tabsStayIn(page, "#rail", 25, true)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("main")).not.toHaveAttribute("inert");
    await expect(page.getByRole("button", { name: "Open portfolio and decisions" })).toBeFocused();
  });

  test(`the details sheet keeps focus and makes the page inert on a ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    await page.getByRole("button", { name: "Details", exact: true }).click();
    await expect(details(page).getByRole("tab", { name: "Holdings" })).toBeFocused();
    await expect(page.getByRole("main")).toHaveAttribute("inert", "");
    expect(await tabsStayIn(page, "#details", 40)).toBe(true);
    expect(await tabsStayIn(page, "#details", 40, true)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(details(page)).toBeHidden();
    await expect(page.getByRole("main")).not.toHaveAttribute("inert");
    await expect(page.getByRole("button", { name: "Details", exact: true })).toBeFocused();
  });
}

test("on a wide screen open details are a column beside the page: nothing is inert and Tab can leave them", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await page.getByRole("button", { name: "Details", exact: true }).click();
  await expect(details(page)).toBeVisible();
  await expect(page.getByRole("main")).not.toHaveAttribute("inert");
  await expect(rail(page)).not.toHaveAttribute("inert");
  expect(await tabsStayIn(page, "#details", 60)).toBe(false);
});

test("a citation opens its source in the details, and closing them returns focus to that citation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  await ask(page).fill("Analyze a stock in chat");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await thread(page).getByRole("button", { name: "Analyze ACME" }).click();
  const card = thread(page).getByRole("article", { name: "Stock analysis result" });
  await expect(card.getByRole("region", { name: "Stock analysis" }).getByRole("heading", { level: 2 })).toBeVisible();
  // The card says what the result is and which portfolio it was computed from.
  await expect(card.locator("header")).toContainText("Stock analysis · ACME");
  await expect(card).toContainText("Portfolio revision 1");

  const source = card.getByRole("button", { name: "Source 1" }).first();
  await source.click();
  await expect(details(page)).toContainText("Stock analysis · ACME");
  await expect(details(page)).toContainText("Portfolio revision 1");
  const selected = details(page).locator(".ev.focus");
  await expect(selected).toContainText("SEC filing");
  await page.keyboard.press("Escape");
  await expect(details(page)).toBeHidden();
  await expect(source).toBeFocused();

  // The card's own control opens the details too.
  await card.getByRole("button", { name: "Open details" }).click();
  await expect(details(page).getByRole("tab", { name: /Evidence/ })).toHaveAttribute("aria-selected", "true");
});

test("the composer grows with Shift+Enter, sends with Enter, and keeps every line", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  const field = ask(page);
  await field.fill("First line");
  const one = (await field.boundingBox())!.height;
  await field.press("Shift+Enter");
  await field.pressSequentially("Second line");
  await field.press("Shift+Enter");
  await field.pressSequentially("Third line");
  await expect(field).toHaveValue("First line\nSecond line\nThird line");
  expect((await field.boundingBox())!.height).toBeGreaterThan(one + 30);
  const sent: { text: string }[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/messages$/.test(request.url())) sent.push(request.postDataJSON()); });
  await field.press("Enter");
  await expect(thread(page)).toContainText("Third line");
  await expect.poll(() => sent.map(body => body.text)).toEqual(["First line\nSecond line\nThird line"]);
  await expect(field).toHaveValue("");
  expect((await field.boundingBox())!.height).toBeLessThanOrEqual(one + 1);
});

test("a stock result draws its cases and today's price on one line, and the cap from the rules", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, CANADA);
  await ask(page).fill("/stock ACME");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const memo = page.getByRole("region", { name: "Stock analysis" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  // The chart repeats the backend's case values and the dated price, nothing else.
  const cases = await memo.getByRole("list", { name: "Downside, base and upside" }).innerText();
  const chart = memo.getByRole("img", { name: /^Per share, discounted to today/ });
  await expect(chart).toHaveAttribute("aria-label", /\. Price C\$200\./);
  const drawn = (await chart.innerText()).match(/C\$[\d,.]+/g)!;
  expect(drawn.length).toBeGreaterThanOrEqual(3);
  for (const value of drawn.filter(value => value !== "C$200")) expect(cases).toContain(value);
  await expect(memo.getByTestId("cap-insight")).toContainText("50% of your portfolio. Within your 60% company cap.");
});

test("web sources are numbered in the answer and marked as not verified", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page);
  // The stored answer quotes its source inside its own text, as the hosted search does.
  await page.route(/\/api\/threads\/[^/]+$/, async route => {
    const response = await route.fetch();
    const body = await response.json();
    for (const message of body.messages ?? [])
      if (message.role === "assistant" && message.text.startsWith("Plain answer"))
        message.citations = [
          // From the answering turn: offsets into the stored text.
          { type: "url_citation", url: "https://news.example/acme", title: "Acme lifts guidance", start_index: 0, end_index: 12, cited_text: "Plain answer" },
          // From the search turn: no offsets, and its words appear in the answer too. Listed, never placed by guessing.
          { type: "url_citation", url: "https://other.example/acme", title: "Other coverage", cited_text: "latest news" },
        ];
    await route.fulfill({ response, json: body });
  });
  await ask(page).fill("What is the latest news on Acme?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  // The number sits right after the quoted words, inside the sentence.
  const answer = thread(page).getByRole("listitem").filter({ has: page.getByRole("list", { name: "Sources for this answer" }) });
  await expect(answer.locator("p").first()).toHaveText("Plain answer1 to: What is the latest news on Acme?");
  const marker = answer.getByRole("link", { name: "Source 1" });
  await expect(answer.getByRole("link", { name: "Source 2" })).toHaveCount(0);
  await expect(answer.getByRole("list", { name: "Sources for this answer" })).toContainText("Other coverage");
  await expect(marker).toHaveAttribute("href", /#source-.+-1$/);
  await expect(answer.getByRole("list", { name: "Sources for this answer" })).toContainText("Acme lifts guidance");
  await expect(answer).toContainText("Web · not verified");
  await expect(thread(page).getByRole("article", { name: "Web research" })).toContainText("Web · not verified");
});

/** Inline source numbers: each target's box, and any pair of targets that overlap. */
async function citationTargets(page: Page) {
  return page.evaluate(() => {
    const boxes = [...document.querySelectorAll<HTMLElement>("main a[aria-label^='Source '], main sup button")]
      .filter(element => element.getClientRects().length)
      .map(element => element.getBoundingClientRect());
    const small = boxes.filter(box => box.width < 44 || box.height < 44).length;
    let overlaps = 0;
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++) {
        const [a, b] = [boxes[i], boxes[j]];
        if (a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5) overlaps++;
      }
    return { count: boxes.length, small, overlaps };
  });
}

for (const [name, width] of [["phone", 390], ["tablet", 768]] as const)
  test(`inline source numbers are full touch targets that never overlap on a ${name}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page);
    // Two sources on the same words put two numbers side by side.
    await page.route(/\/api\/threads\/[^/]+$/, async route => {
      const response = await route.fetch();
      const body = await response.json();
      for (const message of body.messages ?? [])
        if (message.role === "assistant" && message.text.startsWith("Plain answer"))
          message.citations = ["https://news.example/acme", "https://other.example/acme"].map(url =>
            ({ type: "url_citation", url, title: url, start_index: 0, end_index: 12, cited_text: "Plain answer" }));
      await route.fulfill({ response, json: body });
    });
    await ask(page).fill("Analyze a stock in chat");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await thread(page).getByRole("button", { name: "Analyze ACME" }).click();
    await expect(thread(page).getByRole("region", { name: "Stock analysis" }).getByRole("heading", { level: 2 })).toBeVisible();
    const targets = await citationTargets(page);
    expect(targets.count).toBeGreaterThanOrEqual(4);
    expect(targets).toEqual({ count: targets.count, small: 0, overlaps: 0 });

    // From the keyboard a number still opens its source.
    const marker = thread(page).getByRole("link", { name: "Source 2" }).first();
    await marker.focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#source-.+-2$/);
    const filing = thread(page).getByRole("button", { name: "Source 1" }).first();
    await filing.focus();
    await page.keyboard.press("Enter");
    await expect(details(page).locator(".ev.focus")).toBeVisible();
  });

/** The case line's parts: whether each stays inside the line, and whether the line clears its legend. */
async function caseLineLayout(page: Page) {
  return page.locator(".range-wrap").evaluate(wrap => {
    const line = wrap.querySelector(".range")!.getBoundingClientRect();
    const legend = wrap.querySelector(".range-legend")!.getBoundingClientRect();
    const parts = [...wrap.querySelectorAll(".range > span")].map(part => part.getBoundingClientRect());
    return {
      marker: !!wrap.querySelector(".px"),
      outside: parts.filter(part => part.top < line.top - 0.5 || part.bottom > line.bottom + 0.5).length,
      clearsLegend: line.bottom <= legend.top + 0.5,
    };
  });
}

for (const [width, priced] of [[390, true], [390, false], [1440, false]] as const)
  test(`the case line keeps its track, ticks and legend apart at ${width}px ${priced ? "with" : "without"} a price`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page, CANADA);
    if (!priced)
      // The same result with its price quoted in another currency: there is no comparable price to mark.
      await page.route(/\/api\/artifacts\/[^/?]+(\?|$)/, async route => {
        const response = await route.fetch();
        const envelope = await response.json();
        const body = envelope.payload ?? {};
        if (body.stock?.valuation) body.stock.valuation.currency = "USD";
        for (const row of body.portfolio?.positions ?? []) if (row.quote_used) row.quote_used.currency = "USD";
        await route.fulfill({ response, json: envelope });
      });
    await ask(page).fill("/stock ACME");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByRole("region", { name: "Stock analysis" }).getByRole("heading", { level: 2 })).toBeVisible();
    expect(await caseLineLayout(page)).toEqual({ marker: priced, outside: 0, clearsLegend: true });
  });

test("an undecided stock result shows its cases and cap check without reading as approval", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await open(page, CANADA);
  await ask(page).fill("/stock ACME");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const memo = page.getByRole("region", { name: "Stock analysis" });
  // The test server cannot conclude on this holding, so the verdict is undecided.
  await expect(memo.getByRole("heading", { level: 2 })).toHaveText("Not enough to decide on ACME yet.");
  // Every value is still shown, in neutral colours and with what they are not.
  const chart = memo.getByRole("img", { name: /^Per share, discounted to today/ });
  await expect(chart).toHaveAttribute("data-tone", "neutral");
  await expect(chart).toContainText("No call yet: these are the cases, not a target.");
  await expect(chart).toHaveAttribute("aria-label", /No call yet/);
  const cap = memo.getByTestId("cap-insight");
  await expect(cap).toHaveAttribute("data-tone", "neutral");
  await expect(cap).toHaveText("50% of your portfolio. Within your 60% company cap. A rules check only, not a call on ACME.");
  // The status dot beside the verdict is muted too, not green.
  await expect(memo.locator(".meta .sage")).toHaveCount(0);
});
