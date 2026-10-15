import { test, expect, type Page, type Route } from "@playwright/test";
import { openDetails, seedPortfolio } from "./test_helpers";

// ACME has reviewed research fixtures on the test server; with the 15% cap it breaks a rule.
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

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });
const holdings = async (page: Page) => (await (await page.request.get("/api/portfolio")).json()) as { revision: number; portfolio: { snapshot: { positions: { id: string; shares?: string }[] } } };
const acmeShares = async (page: Page) => Number((await holdings(page)).portfolio.snapshot.positions.find(row => row.id === "tfsa-acme")?.shares);
const detail = async (page: Page) => {
  const [summary] = await (await page.request.get("/api/threads")).json();
  return (await page.request.get(`/api/threads/${summary.id}`)).json();
};

async function open(page: Page, saved: object | null = SAVED) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  if (saved) await seedPortfolio(page, saved);
  else await page.request.delete("/api/test/portfolio");
  await page.goto("/");
  if (saved) await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

async function send(page: Page, question: string) {
  await ask(page).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("a workflow run in the conversation shows the stored financial result, unchanged after a reload", async ({ page }) => {
  await open(page);
  const analyses: string[] = [];
  page.on("request", request => { if (request.url().includes("/api/analyze")) analyses.push(request.url()); });

  await send(page, "Review my exposure in chat");
  const result = thread(page).getByRole("article", { name: "Exposure review result" });
  const memo = result.getByRole("region", { name: "Analyst memo" });
  // The backend's computed values, in the existing review memo. The run is dated today, so the test server's
  // September marks are out of date and only the cash balance is a known value: nothing is filled in by prose.
  await expect(memo).toContainText("ACME · TFSA");
  await expect(memo).toContainText("Total unknown · known C$1,000 · holdings Unknown · cash C$1,000");
  await expect(result).toContainText("Portfolio revision 1");
  await expect(thread(page)).toContainText("Plain answer to: Review my exposure in chat");
  // The stored tool record is never shown as prose.
  await expect(thread(page)).not.toContainText("retrieval");

  const stored = (await detail(page)).runs[0].artifact_ids as string[];
  expect(stored).toHaveLength(1);
  const first = await result.innerText();

  // A changed portfolio (a renamed account, a looser rule) does not rewrite a result computed on revision 1.
  const renamed = { ...SAVED.snapshot, accounts: [{ id: "tfsa", name: "Tax free" }, { id: "rrsp", name: "RRSP" }] };
  const next = await page.request.put("/api/portfolio", { data: { base_revision: 1, portfolio: { ...SAVED, snapshot: renamed, settings: { single_company_cap: "0.5" } } } });
  await page.request.post(`/api/portfolio/proposals/${(await next.json()).proposal.id}/confirm`);
  expect((await holdings(page)).revision).toBe(2);

  await page.reload();
  await expect(memo).toContainText("known C$1,000");
  await expect(memo).toContainText("ACME · TFSA");
  expect(await result.innerText()).toBe(first);
  await expect(result).toContainText("Portfolio revision 1");
  expect((await detail(page)).runs[0].artifact_ids).toEqual(stored);
  // The thread never re-runs an analysis to draw a result.
  expect(analyses).toHaveLength(0);
});

test("a sourced web answer keeps clickable citations with its text after a reload", async ({ page }) => {
  await open(page);
  await send(page, "What is the latest news on Acme?");

  const answer = thread(page).getByRole("listitem").filter({ hasText: "Plain answer to: What is the latest news on Acme?" });
  const cited = answer.getByRole("list", { name: "Sources for this answer" });
  const link = cited.getByRole("link", { name: "Acme lifts guidance" });
  await expect(link).toHaveAttribute("href", "https://news.example/acme");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(cited).toContainText("Acme raised guidance");
  await expect(thread(page)).toContainText("Searched the web");
  // Every source the search consulted is listed with the stored research, cited or not.
  const research = thread(page).getByRole("article", { name: "Web research" });
  await expect(research.getByRole("list", { name: "Sources consulted" }).getByRole("link")).toHaveCount(2);
  await expect(research).toContainText("Acme guidance news");

  const before = await detail(page);
  await page.reload();
  await expect(answer.getByRole("list", { name: "Sources for this answer" }).getByRole("link", { name: "Acme lifts guidance" })).toHaveAttribute("href", "https://news.example/acme");
  expect((await detail(page)).messages).toEqual(before.messages);

  // The link is reachable and named from the keyboard.
  await link.focus();
  await expect(link).toBeFocused();
});

test("a failed web search says no lookup took place, and the answer claims no sources", async ({ page }) => {
  await open(page);
  await send(page, "Check the search outage for Acme");

  await expect(thread(page)).toContainText("Could not search the web");
  const research = thread(page).getByRole("article", { name: "Web search unavailable" });
  await expect(research.getByRole("status").or(research.getByRole("alert"))).toContainText("Web search was unavailable");
  await expect(research.getByRole("link")).toHaveCount(0);
  await expect(thread(page)).toContainText("Hosted web search was unavailable.");
  await expect(thread(page).getByRole("list", { name: "Sources for this answer" })).toHaveCount(0);
});

test("a direct calculation shows its stored values, and a result that no longer exists says so", async ({ page }) => {
  await open(page);
  const artifacts: string[] = [];
  page.on("response", async response => { if (response.url().includes("/api/artifacts/")) artifacts.push(await response.text().catch(() => "")); });
  await send(page, "Show my exposure numbers");

  const card = thread(page).getByRole("article", { name: "Portfolio exposure and guardrails" });
  await expect(card).toContainText("Portfolio revision 1");
  // The stored calculation, value for value: the cash balance the backend recorded, not a paraphrase.
  const values = card.getByRole("tree", { name: "Portfolio exposure and guardrails values" });
  await expect(values).toContainText('capability:"review_portfolio"');
  await expect(values).toContainText('holdings_as_of:"2026-09-30"');
  // Stored values reach the browser without the private cost basis or any credential.
  const shown = (await page.getByRole("main").innerHTML()) + JSON.stringify(await page.evaluate(() => ({ ...localStorage })));
  for (const secret of ["average_cost", "sk-test-backend-only-never-browser", "continuation"]) {
    expect(shown).not.toContain(secret);
    for (const body of artifacts) expect(body).not.toContain(secret);
  }
  expect(artifacts.length).toBeGreaterThan(0);

  await page.route("**/api/artifacts/*", route => route.fulfill({ status: 404, json: { detail: "Artifact not found." } }));
  await page.reload();
  await expect(thread(page)).toContainText("This stored result is no longer available");
  await expect(thread(page)).toContainText("Plain answer to: Show my exposure numbers");

  await page.unroute("**/api/artifacts/*");
  await page.route("**/api/artifacts/*", route => route.fulfill({ status: 503, json: { detail: "Database is temporarily locked. Please retry." } }));
  await page.reload();
  await expect(thread(page)).toContainText("This stored result could not be loaded");
  await page.unroute("**/api/artifacts/*");
  await thread(page).getByRole("button", { name: "Load result again" }).click();
  await expect(card).toContainText("Portfolio revision 1");
});

/** Every message the page submits to a thread, as sent. */
function submissions(page: Page) {
  const bodies: { text: string; workflow_input?: Record<string, any> | null }[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/messages$/.test(request.url())) bodies.push(request.postDataJSON()); });
  return bodies;
}

test("a missing stock is asked for, and the explicit choice resumes as a new run with the stock memo", async ({ page }) => {
  await open(page);
  const bodies = submissions(page);
  await send(page, "Analyze a stock in chat");

  const request = thread(page).getByTestId("clarification");
  await expect(request).toContainText("The analyst needs more from you");
  await expect(request.getByRole("list", { name: "Missing inputs" })).toHaveText("The stock to analyze");
  await expect(thread(page)).toContainText("Paused stock analysis");
  // Only stocks are offered; the fund and the cash balance are not.
  await expect(request.getByRole("button")).toHaveText(["Analyze ACME"]);

  await request.getByRole("button", { name: "Analyze ACME" }).focus();
  await page.keyboard.press("Enter");
  const memo = thread(page).getByRole("region", { name: "Stock analysis" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(memo.getByTestId("stock-position")).toContainText("10 shares in TFSA");

  expect(bodies).toHaveLength(2);
  expect(bodies[0].workflow_input ?? null).toBeNull();
  expect(bodies[1].workflow_input).toEqual({ workflow: "run_stock_analysis", stock: { position_id: "tfsa-acme" } });
  const stored = await detail(page);
  expect(stored.runs.map((run: { status: string }) => run.status)).toEqual(["completed", "completed"]);
  expect(stored.messages.filter((message: { clarification_required: unknown }) => message.clarification_required)).toHaveLength(1);
  // The answered request stays in the record, but can no longer be acted on.
  await expect(request.getByRole("button")).toHaveCount(0);

  await page.reload();
  await expect(memo.getByTestId("stock-position")).toContainText("10 shares in TFSA");
  await expect(request.getByRole("list", { name: "Missing inputs" })).toHaveText("The stock to analyze");
  expect(bodies).toHaveLength(2);
});

test("new cash is only run from the explicit form with its confirmation, as a new run", async ({ page }) => {
  await open(page);
  const bodies = submissions(page);
  await send(page, "Plan a deposit in chat");

  const request = thread(page).getByTestId("clarification");
  await expect(request.getByRole("list", { name: "Missing inputs" }).getByRole("listitem")).toHaveText([
    "The amount of new cash", "The account it arrives in", "Your loss tolerance or withdrawal plans", "Your confirmation that this is new money",
  ]);
  await request.getByRole("button", { name: "Open new-cash form" }).click();

  const form = page.getByRole("group", { name: "New cash inputs" });
  await form.getByLabel("Amount", { exact: true }).fill("500");
  // The test server's diversified fund is a USD holding in the RRSP, so USD cash arriving there can be compared with it.
  await form.getByLabel("Currency").selectOption("USD");
  await form.getByLabel("Into account").selectOption("rrsp");
  await form.getByLabel("Loss tolerance or withdrawal plans").fill("Could hold through a 30% drop");
  const analyze = page.getByRole("button", { name: "Analyze", exact: true });
  // Typing an agreement is not the agreement: the existing confirmation box is.
  await expect(analyze).toBeDisabled();
  expect(bodies).toHaveLength(1);
  await form.getByRole("checkbox").check();
  await analyze.click();

  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1].workflow_input).toMatchObject({
    workflow: "run_new_cash_allocation",
    new_cash: { amount: "500", currency: "USD", account_id: "rrsp", confirmed: true, risk_context: "Could hold through a 30% drop" },
  });
  await expect(ask(page)).toBeEnabled({ timeout: 30_000 });
  const stored = await detail(page);
  expect(stored.runs).toHaveLength(2);
  expect(stored.messages.filter((message: { run_id: string; clarification_required: unknown }) => message.run_id === stored.runs[1].id && message.clarification_required)).toHaveLength(0);
  expect(stored.runs[1].status).toBe("completed");
  const result = thread(page).getByRole("article", { name: "New Cash Allocation result" });
  await expect(result.getByRole("region", { name: "Analyst memo" })).toContainText("BROAD");
  // The stored result is what the investor reads; the note about the model's reading budget is not shown as a failure.
  await expect(thread(page)).toContainText("Checked new cash allocation");
  await expect(thread(page)).not.toContainText("retrieval budget");

  // Re-running from the stored result reopens the form: the earlier confirmation is not carried forward.
  await result.getByRole("button", { name: "Re-run with these" }).click();
  await expect(form.getByRole("checkbox")).not.toBeChecked();
  await expect(form.getByLabel("Amount", { exact: true })).toHaveValue("500");
  await expect(analyze).toBeDisabled();
  expect(bodies).toHaveLength(2);
});

test("a theme is researched only after the form is complete and then explicitly agreed", async ({ page }) => {
  await open(page);
  const bodies = submissions(page);
  await send(page, "Test an idea in chat");

  const first = thread(page).getByTestId("clarification").first();
  await expect(first.getByRole("list", { name: "Missing inputs" })).toContainText("Your agreement to the theme and shortlist");
  await first.getByRole("button", { name: "Open theme form" }).click();
  await page.getByLabel("Theme name").fill("Industrial automation");
  await page.getByLabel("Economic mechanism").fill("Automation demand lifts revenue and margins unless capital spending stalls.");
  await page.getByLabel("Add held position to shortlist").selectOption("tfsa-acme");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();

  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[1].workflow_input).toMatchObject({ workflow: "run_theme_exploration", theme: { name: "Industrial automation", shortlist: ["tfsa-acme"], confirmed: false } });
  const agree = thread(page).getByTestId("clarification").last();
  await expect(agree.getByRole("list", { name: "Missing inputs" })).toHaveText("Your agreement to the theme and shortlist");
  await expect(agree.getByLabel("Theme to agree")).toContainText("Industrial automation");
  await expect(agree.getByLabel("Theme to agree")).toContainText("ACME");
  await agree.getByRole("button", { name: "Agree and research" }).click();

  await expect.poll(() => bodies.length).toBe(3);
  expect(bodies[2].workflow_input).toMatchObject({ workflow: "run_theme_exploration", theme: { name: "Industrial automation", confirmed: true } });
  await expect(ask(page)).toBeEnabled({ timeout: 30_000 });
  const stored = await detail(page);
  expect(stored.runs.map((run: { status: string }) => run.status)).toEqual(["completed", "completed", "completed"]);
  await expect(thread(page).getByRole("article", { name: "Theme Exploration result" }).getByRole("region", { name: "Analyst memo" })).toContainText("Completed theme research · 5 of 16 calls used");
});

test("a proposal from the conversation changes nothing until its review is confirmed with the dedicated button", async ({ page }) => {
  await open(page);
  const writes: string[] = [];
  page.on("request", request => { if (/\/api\/portfolio\/proposals\/[^/]+\/(confirm|cancel)$/.test(request.url())) writes.push(request.url().split("/").pop()!); });

  await send(page, "Set my ACME shares to 12");
  const card = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(card).toContainText("1 proposed change");
  await expect(card).toContainText("Waiting for your review");
  await expect(card).toContainText("Prepared from revision 1");
  await expect(thread(page)).toContainText("Prepared portfolio changes for your review");
  // Preparing and reading the proposal commits nothing.
  expect(await acmeShares(page)).toBe(10);
  expect((await holdings(page)).revision).toBe(1);

  // Reviewing and backing out: still nothing committed, and the conversation records the cancellation.
  await card.getByRole("button", { name: "Review changes" }).click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toContainText("From a conversation · prepared from revision 1");
  await expect(dialog.getByRole("table", { name: "What changes" }).getByRole("row", { name: /ACME/ })).toHaveText(/TFSA · shares.*10.*12/);
  expect(await acmeShares(page)).toBe(10);
  await dialog.getByRole("button", { name: "Cancel proposal" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(card).toContainText("Cancelled. Your saved portfolio was not changed.");
  await expect(card.getByRole("button")).toHaveCount(0);
  expect(await acmeShares(page)).toBe(10);
  expect(writes).toEqual(["cancel"]);

  // A fresh request prepares a fresh proposal; only the review's own button applies it.
  await send(page, "Set my ACME shares to 12 please");
  const fresh = thread(page).getByRole("article", { name: "Proposed portfolio changes" }).last();
  await fresh.getByRole("button", { name: "Review changes" }).focus();
  await page.keyboard.press("Enter");
  expect(await acmeShares(page)).toBe(10);
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);
  await expect(fresh).toContainText("Applied");
  await expect(fresh).toContainText("Your portfolio moved from revision 1 to revision 2.");
  expect(await acmeShares(page)).toBe(12);
  expect(writes).toEqual(["cancel", "confirm"]);

  // Reopened from the backend: both proposals keep the state the investor gave them.
  await page.reload();
  const cards = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(cards.first()).toContainText("Cancelled");
  await expect(cards.last()).toContainText("Your portfolio moved from revision 1 to revision 2.");
  await expect(cards.getByRole("button")).toHaveCount(0);
  expect(writes).toEqual(["cancel", "confirm"]);
});

test("a proposal prepared before the portfolio changed is shown as out of date and cannot be applied", async ({ page }) => {
  await open(page);
  await send(page, "Set my ACME shares to 12");
  const card = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(card).toContainText("Waiting for your review");

  // The saved portfolio moves on (a rule change confirmed elsewhere) while the proposal waits.
  const next = await page.request.put("/api/portfolio", { data: { base_revision: 1, portfolio: { ...SAVED, settings: { single_company_cap: "0.5" } } } });
  await page.request.post(`/api/portfolio/proposals/${(await next.json()).proposal.id}/confirm`);
  await page.reload();

  await expect(card).toContainText("Out of date");
  await expect(card).toContainText("Prepared from revision 1, which is no longer current");
  // It offers no review to confirm, and the backend refuses it too: a fresh preview is the only way forward.
  await expect(card.getByRole("button")).toHaveCount(0);
  const [stale] = await (await page.request.get("/api/portfolio/proposals?status=stale")).json();
  expect((await page.request.post(`/api/portfolio/proposals/${stale.id}/confirm`)).status()).toBe(409);
  expect(await acmeShares(page)).toBe(10);
  expect((await holdings(page)).revision).toBe(2);

  // An edit the saved holdings cannot take is explained, with the way to fix it, and prepares nothing.
  await send(page, "Set my GHOST shares to 5");
  const request = thread(page).getByTestId("clarification").last();
  await expect(request).toContainText("Select a position from the saved holdings.");
  await request.getByRole("button", { name: "Open holdings" }).click();
  await expect(page.getByRole("complementary", { name: "Details" }).getByRole("tab", { name: "Holdings" })).toHaveAttribute("aria-selected", "true");
  expect((await holdings(page)).revision).toBe(2);
});

test("a stored result is saved as a decision with its workflow and subject, journaled, reopened and re-run", async ({ page }) => {
  await open(page);
  const commits: string[] = [];
  const analyses: { stock?: { position_id: string } }[] = [];
  page.on("request", request => {
    if (/\/api\/portfolio\/proposals\/[^/]+\/confirm$/.test(request.url())) commits.push(request.url());
    if (request.url().endsWith("/api/analyze")) analyses.push(request.postDataJSON());
  });
  const rail = page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });

  await send(page, "Analyze a stock in chat");
  await thread(page).getByTestId("clarification").getByRole("button", { name: "Analyze ACME" }).click();
  const result = thread(page).getByRole("article", { name: "Stock Analysis result" });
  await expect(result.getByRole("region", { name: "Stock analysis" })).toBeVisible();

  await result.getByRole("button", { name: "Save decision" }).click();
  await expect(result).toContainText(/Saved to Decisions \d\d?:\d\d/);
  await expect(result.getByRole("button", { name: "Save decision" })).toHaveCount(0);
  await expect(rail.getByTestId("all-decisions-link")).toContainText("1 open");
  const [decision] = await (await page.request.get("/api/decisions")).json();
  expect(decision.workflow).toBe("stock");
  expect(decision.subject).toMatchObject({ label: "ACME", ticker: "ACME", listing: "XNAS" });
  expect(decision.question).toBe("Analyze ACME");

  // The journal entry records what the investor did. It is not a portfolio change.
  await result.getByRole("radio", { name: "Kept it as it is" }).check();
  await result.getByRole("button", { name: "Record action" }).click();
  await expect(result).toContainText("Saved and recorded");
  expect((await (await page.request.get(`/api/decisions/${decision.id}`)).json()).confirmed_action.action).toBe("hold");
  expect((await holdings(page)).revision).toBe(1);
  expect(commits).toHaveLength(0);

  // A reload retains the saved decision marker and prevents duplicate decisions
  await page.reload();
  const reloadedResult = thread(page).getByRole("article", { name: "Stock Analysis result" });
  await expect(reloadedResult).toContainText(/Saved to Decisions \d\d?:\d\d/);
  await expect(reloadedResult.getByRole("button", { name: "Save decision" })).toHaveCount(0);
  expect(await (await page.request.get("/api/decisions")).json()).toHaveLength(1);

  // The register still filters by workflow, and the saved decision reopens read-only.
  await rail.getByTestId("all-decisions-link").click();
  const register = page.getByRole("region", { name: "Decisions register" });
  await register.getByLabel("Filter by workflow").selectOption("stock");
  await expect(register.getByTestId("decision-row")).toHaveCount(1);
  await register.getByLabel("Filter by workflow").selectOption("review");
  await expect(register.getByTestId("decision-row")).toHaveCount(0);
  await register.getByLabel("Filter by workflow").selectOption("stock");
  await register.getByTestId("decision-row").click();
  await expect(page.locator(".ctx")).toContainText("Read-only");

  // Re-running is a fresh analysis of today's saved portfolio for the same subject.
  expect(analyses).toHaveLength(0);
  await page.getByRole("button", { name: "Re-run with today's portfolio" }).click();
  await expect(page.getByRole("region", { name: "Stock analysis" })).toBeVisible();
  expect(analyses.map(body => body.stock?.position_id)).toEqual(["tfsa-acme"]);

  // The conversation and its stored result are still there, untouched by the re-run.
  await rail.getByTestId("thread-item").first().click();
  await expect(result.getByRole("region", { name: "Stock analysis" })).toBeVisible();
  expect((await detail(page)).runs).toHaveLength(2);
});

test("a calculation that needs a saved portfolio asks for it instead of showing an empty result", async ({ page }) => {
  await open(page, null);
  await send(page, "Show my exposure numbers");

  const request = thread(page).getByTestId("clarification");
  await expect(request).toContainText("Save a portfolio before calculating its exposure.");
  await expect(request.getByRole("button", { name: "Open holdings" })).toBeVisible();
  await expect(thread(page).getByRole("tree")).toHaveCount(0);
});

for (const [name, width, height] of [["phone", 390, 844], ["tablet", 768, 1024], ["desktop", 1440, 900]] as const)
  test(`results, sources, requests and proposals fit a ${name} without sideways scrolling`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await open(page);
    await send(page, "What is the latest news on Acme?");
    await expect(thread(page).getByRole("list", { name: "Sources for this answer" })).toBeVisible();
    await send(page, "Show my exposure numbers");
    await expect(thread(page).getByRole("article", { name: "Portfolio exposure and guardrails" }).getByRole("tree")).toBeVisible();
    await send(page, "Set my ACME shares to 12");
    const proposal = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
    await expect(proposal).toContainText("Waiting for your review");
    await send(page, "Analyze a stock in chat");
    const choice = thread(page).getByTestId("clarification").getByRole("button", { name: "Analyze ACME" });
    await expect(choice).toBeVisible();

    const fits = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(await fits()).toBe(true);
    // Controls a finger has to hit are at least 44px tall.
    for (const control of [choice, proposal.getByRole("button", { name: "Review changes" })])
      expect((await control.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/thread-${name}.png`, fullPage: false });

    await choice.click();
    await expect(thread(page).getByRole("region", { name: "Stock analysis" })).toBeVisible();
    expect(await fits()).toBe(true);
    if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/memo-${name}.png`, fullPage: false });
  });

/** Answers the page's proposal-list requests with a fixed response, as a failing or lagging backend would. */
async function proposalList(page: Page, response: { status?: number; json: unknown }) {
  const handler = (route: Route) => (route.request().method() === "GET" ? route.fulfill(response) : route.continue());
  await page.route("**/api/portfolio/proposals", handler);
  return handler;
}

/** A stock result stored in the conversation, ready to save. */
async function stockResult(page: Page) {
  await send(page, "Analyze a stock in chat");
  await thread(page).getByTestId("clarification").getByRole("button", { name: "Analyze ACME" }).click();
  const result = thread(page).getByRole("article", { name: "Stock analysis result" });
  await expect(result.getByRole("region", { name: "Stock analysis" })).toBeVisible();
  return result;
}

/** Commits a changed saved portfolio through the backend, as a confirmation elsewhere would. */
async function commit(page: Page, portfolio: object, baseRevision: number) {
  const next = await page.request.put("/api/portfolio", { data: { base_revision: baseRevision, portfolio } });
  expect((await page.request.post(`/api/portfolio/proposals/${(await next.json()).proposal.id}/confirm`)).ok()).toBeTruthy();
}

test("a deleted decision no longer shows its stored result as saved, one at a time or all at once", async ({ page }) => {
  await open(page);
  page.on("dialog", dialog => void dialog.accept());
  const rail = page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
  const result = await stockResult(page);

  await result.getByRole("button", { name: "Save decision" }).click();
  await expect(result).toContainText(/Saved to Decisions \d\d?:\d\d/);
  await expect(result.getByRole("button", { name: "Record action" })).toBeVisible();

  await rail.getByTestId("all-decisions-link").click();
  const register = page.getByRole("region", { name: "Decisions register" });
  await register.getByRole("button", { name: /^Delete decision: / }).click();
  await expect(register.getByTestId("decision-row")).toHaveCount(0);
  await page.getByRole("button", { name: "Back to chat" }).click();
  // Nothing is saved any more, so nothing can be journaled against it.
  await expect(result).not.toContainText("Saved to Decisions");
  await expect(result.getByRole("button", { name: "Record action" })).toHaveCount(0);
  await expect(result.getByRole("button", { name: "Save decision" })).toBeVisible();
  expect(await (await page.request.get("/api/decisions")).json()).toHaveLength(0);

  // Saved again, then every decision cleared at once.
  await result.getByRole("button", { name: "Save decision" }).click();
  await expect(result.getByRole("button", { name: "Record action" })).toBeVisible();
  await rail.getByTestId("all-decisions-link").click();
  await register.getByRole("button", { name: "Delete all decisions" }).click();
  await expect(register).toContainText("No saved decisions yet.");
  await page.getByRole("button", { name: "Back to chat" }).click();
  await expect(result).not.toContainText("Saved to Decisions");
  await expect(result.getByRole("button", { name: "Record action" })).toHaveCount(0);
  await expect(result.getByRole("button", { name: "Save decision" })).toBeVisible();
});

test("a proposal list that fails to refresh shows the error and a retry, not the earlier list", async ({ page }) => {
  await open(page);
  await send(page, "Set my ACME shares to 12");
  const cards = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(cards).toContainText("Waiting for your review");

  // The list loaded once. The refresh for the next proposal fails.
  const offline = await proposalList(page, { status: 503, json: { detail: "Database is temporarily locked. Please retry." } });
  await send(page, "Set my ACME shares to 12 again");
  await expect(thread(page)).toContainText("Plain answer to: Set my ACME shares to 12 again");
  const failed = thread(page).getByText("The proposed changes could not be loaded");
  await expect(failed).toHaveCount(2);
  // No card speaks for a list that could not be read, and none offers a review.
  await expect(cards).toHaveCount(0);
  await expect(thread(page).getByRole("button", { name: "Review changes" })).toHaveCount(0);

  // The same after a reload while the list still cannot be read.
  await page.reload();
  await expect(failed).toHaveCount(2);
  await expect(cards).toHaveCount(0);

  await page.unroute("**/api/portfolio/proposals", offline);
  await thread(page).getByRole("button", { name: "Load proposed changes again" }).first().click();
  await expect(failed).toHaveCount(0);
  await expect(cards).toHaveCount(2);
  expect(await acmeShares(page)).toBe(10);
});

test("a proposal that this page knows is out of date offers no review, whatever the cached list says", async ({ page }) => {
  await open(page);
  await send(page, "Set my ACME shares to 12");
  const card = thread(page).getByRole("article", { name: "Proposed portfolio changes" });
  await expect(card.getByRole("button", { name: "Review changes" })).toBeVisible();

  // The proposal list keeps answering with what it said before the portfolio moved on.
  const cached = await proposalList(page, { json: await (await page.request.get("/api/portfolio/proposals")).json() });

  // A rule change is reviewed and confirmed in the holdings panel: the saved portfolio is now revision 2.
  const details = await openDetails(page);
  await details.getByLabel("Single-company cap (%)").fill("42");
  await details.getByTestId("review-changes").click();
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toContainText("Your edits · prepared from revision 1");
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);
  expect((await holdings(page)).revision).toBe(2);

  await expect(card).toContainText("Out of date");
  await expect(card).toContainText("Prepared from revision 1, which is no longer current");
  await expect(card.getByRole("button")).toHaveCount(0);
  expect(await acmeShares(page)).toBe(10);

  // The portfolio's list of changes reaches the same review: it opens as out of date, with nothing to confirm.
  await page.getByRole("navigation", { name: "Portfolio, conversations and decisions" }).getByRole("button", { name: /^Portfolio/ }).click();
  await page.getByRole("region", { name: "Portfolio" }).getByRole("tab", { name: /Changes/ }).click();
  await page.getByRole("region", { name: "Portfolio" }).getByRole("button", { name: "Review", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("This proposal is out of date");
  await expect(dialog.getByTestId("confirm-proposal")).toBeDisabled();
  await dialog.getByRole("button", { name: "Cancel proposal" }).click();
  await expect(dialog).toHaveCount(0);

  // Reloaded while the list still answers from before: the page compares revisions itself.
  await page.reload();
  await expect(card).toContainText("Out of date");
  await expect(card.getByRole("button")).toHaveCount(0);

  // With the real list back after a reload: cancelling the out-of-date review cancelled it, and nothing was applied.
  await page.unroute("**/api/portfolio/proposals", cached);
  await page.reload();
  await expect(card).toContainText("Cancelled. Your saved portfolio was not changed.");
  await expect(card.getByRole("button")).toHaveCount(0);
  expect(await acmeShares(page)).toBe(10);
});

test("a stored stock result is saved under its own listing, and a re-run never switches to another listing", async ({ page }) => {
  await open(page);
  const analyses: unknown[] = [];
  page.on("request", request => { if (/\/api\/(analyze|candidates)$/.test(request.url())) analyses.push(request.postDataJSON()); });
  const rail = page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
  const result = await stockResult(page);

  // The same position ID is later saved with a different listing.
  const moved = { ...SAVED.snapshot, positions: SAVED.snapshot.positions.map(row => (row.id === "tfsa-acme" ? { ...row, listing: "XNYS" } : row)) };
  await commit(page, { ...SAVED, snapshot: moved }, 1);
  await page.reload();
  await expect(result.getByRole("region", { name: "Stock analysis" })).toBeVisible();

  await result.getByRole("button", { name: "Save decision" }).click();
  await expect(result).toContainText(/Saved to Decisions \d\d?:\d\d/);
  const [decision] = await (await page.request.get("/api/decisions")).json();
  expect(decision.subject).toMatchObject({ label: "ACME", ticker: "ACME", listing: "XNAS" });

  // Re-running never analyzes the XNYS holding under a decision made about XNAS: the recorded listing is looked up.
  await rail.getByTestId("all-decisions-link").click();
  await page.getByRole("region", { name: "Decisions register" }).getByTestId("decision-row").click();
  await page.getByRole("button", { name: "Re-run with today's portfolio" }).click();
  await expect.poll(() => analyses.length).toBeGreaterThan(0);
  expect(analyses[0]).toEqual({ ticker: "ACME", listing: "XNAS" });
  for (const body of analyses.slice(1)) expect(body).not.toMatchObject({ stock: { position_id: "tfsa-acme" } });
  await expect(ask(page)).toBeEnabled({ timeout: 30_000 });
  analyses.length = 0;

  // Once the holding is back on the decision's listing, the same re-run goes ahead for that holding.
  await commit(page, SAVED, 2);
  await page.reload();
  await rail.getByTestId("all-decisions-link").click();
  await page.getByRole("region", { name: "Decisions register" }).getByTestId("decision-row").click();
  await page.getByRole("button", { name: "Re-run with today's portfolio" }).click();
  await expect(page.getByRole("region", { name: "Stock analysis" })).toBeVisible();
  expect(analyses).toEqual([expect.objectContaining({ stock: { position_id: "tfsa-acme" } })]);
});
