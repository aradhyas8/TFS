import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

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

type Run = { status: string; bound_revision: number | null; hypothetical_artifact_id: string | null };

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });
const saved = async (page: Page) => (await (await page.request.get("/api/portfolio")).json()) as { revision: number; portfolio: { settings: { single_company_cap: string } } };
const detail = async (page: Page) => {
  const [summary] = await (await page.request.get("/api/threads")).json();
  return (await (await page.request.get(`/api/threads/${summary.id}`)).json()) as { runs: Run[]; messages: unknown[] };
};
// Longer than the test server's slow answer takes, so anything late would have arrived.
const LATE = 3_500;

async function open(page: Page) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  await seedPortfolio(page, SAVED);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("As of Sep 30, 2026");
}

async function send(page: Page, question: string) {
  await ask(page).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

/** A portfolio change confirmed outside the conversation: the committed revision moves on by one. */
async function commit(page: Page, cap: string) {
  const { revision } = await saved(page);
  const prepared = await page.request.put("/api/portfolio", { data: { base_revision: revision, portfolio: { ...SAVED, settings: { ...SAVED.settings, single_company_cap: cap } } } });
  expect((await page.request.post(`/api/portfolio/proposals/${(await prepared.json()).proposal.id}/confirm`)).ok()).toBeTruthy();
}

test("stopping an answer cancels the run on the backend, and its late answer never appears", async ({ page }) => {
  await open(page);
  const cancels: string[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/runs\/[^/]+\/cancel$/.test(request.url())) cancels.push(request.method()); });

  await send(page, "Slow question about bonds");
  await expect(thread(page)).toContainText("Thinking");
  await thread(page).getByRole("button", { name: "Stop answer" }).click();

  const stopped = thread(page).getByRole("status").filter({ hasText: "This answer was stopped" });
  await expect(stopped).toBeVisible();
  await expect(thread(page)).not.toContainText("Thinking");
  await expect(ask(page)).toBeEnabled();
  expect(cancels).toEqual(["POST"]);
  expect((await detail(page)).runs.map(run => run.status)).toEqual(["cancelled"]);

  // The provider's answer arrives after the stop: it is not stored, shown, or run again after a reload.
  await page.waitForTimeout(LATE);
  await expect(thread(page)).not.toContainText("Plain answer");
  await page.reload();
  await expect(stopped).toBeVisible();
  await expect(thread(page)).not.toContainText("Plain answer");
  await expect(thread(page)).not.toContainText("Thinking");
  const after = await detail(page);
  expect(after.runs.map(run => run.status)).toEqual(["cancelled"]);
  expect(after.messages).toHaveLength(1);
  expect((await saved(page)).revision).toBe(1);
});

test("a stop that cannot reach the backend says so, and the run is still stoppable", async ({ page }) => {
  await open(page);
  await page.route("**/api/threads/*/runs/*/cancel", route => route.fulfill({ status: 503, json: { detail: "The analyst service is offline." } }));
  await send(page, "Slow question about bonds");
  await thread(page).getByRole("button", { name: "Stop answer" }).click();
  await expect(thread(page).getByRole("alert").filter({ hasText: "This answer could not be stopped" })).toContainText("The analyst service is offline.");
  expect((await detail(page)).runs.map(run => run.status)).toEqual(["running"]);

  // The failure belongs to that run: once it ends on its own, the next run does not inherit the error.
  await page.unroute("**/api/threads/*/runs/*/cancel");
  await expect(thread(page)).toContainText("Plain answer to: Slow question about bonds", { timeout: 15_000 });
  await send(page, "Slow follow-up about bonds");
  await expect(thread(page).getByRole("button", { name: "Stop answer" })).toBeVisible();
  await expect(thread(page).getByRole("alert")).toHaveCount(0);
  await thread(page).getByRole("button", { name: "Stop answer" }).click();
  await expect(thread(page).getByRole("status").filter({ hasText: "This answer was stopped" })).toBeVisible();
  await expect(thread(page).getByRole("alert")).toHaveCount(0);
});

test("a run in flight when the analyst restarts is shown as interrupted and is never replayed", async ({ page }) => {
  await open(page);
  const sends: string[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/messages$/.test(request.url())) sends.push(request.url()); });

  await send(page, "Slow question before the restart");
  await expect(thread(page)).toContainText("Thinking");
  expect((await page.request.post("/api/test/restart")).ok()).toBeTruthy();

  const interrupted = thread(page).getByRole("status").filter({ hasText: "The analyst restarted before answering" });
  await expect(interrupted).toBeVisible();
  await expect(ask(page)).toBeEnabled();
  await page.reload();
  await expect(interrupted).toBeVisible();
  await expect(thread(page)).not.toContainText("Thinking");
  await page.waitForTimeout(LATE);
  await expect(thread(page)).not.toContainText("Plain answer");
  expect(sends).toHaveLength(1);
  expect((await detail(page)).runs.map(run => run.status)).toEqual(["interrupted"]);

  // Asking again is the investor's choice, and it is a new run.
  await thread(page).getByRole("button", { name: "Ask again" }).click();
  await expect(thread(page)).toContainText("Plain answer to: Slow question before the restart", { timeout: 15_000 });
  expect((await detail(page)).runs.map(run => run.status)).toEqual(["interrupted", "completed"]);
});

test("a portfolio change is announced on the next run, while earlier and in-flight runs keep their revision", async ({ page }) => {
  await open(page);
  const notices = thread(page).getByTestId("revision-notice");
  const revisions = thread(page).getByRole("list", { name: "Referenced results" });

  await send(page, "Show my holdings");
  await expect(revisions).toHaveText(["Portfolio revision 1"]);
  await expect(notices).toHaveCount(0);

  // Committed between runs: the next run uses it and says so.
  await commit(page, "0.2");
  await send(page, "Slow look at my holdings");
  await expect(thread(page)).toContainText("Thinking");
  await expect(notices).toHaveCount(1);
  await expect(notices).toContainText("Your saved portfolio changed");
  await expect(notices).toContainText("This question uses revision 2. Earlier questions used revision 1, and their results are unchanged.");
  // Committed while that run is still going: the run keeps the revision it started with.
  await commit(page, "0.25");
  await expect(revisions).toHaveText(["Portfolio revision 1", "Portfolio revision 2"], { timeout: 15_000 });
  await expect(thread(page)).toContainText("Plain answer to: Slow look at my holdings", { timeout: 15_000 });
  await expect(notices).toHaveCount(1);

  await send(page, "Show my holdings again");
  await expect(revisions).toHaveText(["Portfolio revision 1", "Portfolio revision 2", "Portfolio revision 3"]);
  await expect(notices).toHaveCount(2);
  await expect(notices.last()).toContainText("This question uses revision 3. Earlier questions used revision 2, and their results are unchanged.");

  await page.reload();
  await expect(revisions).toHaveText(["Portfolio revision 1", "Portfolio revision 2", "Portfolio revision 3"]);
  await expect(notices).toHaveCount(2);
  expect((await detail(page)).runs.map(run => run.bound_revision)).toEqual([1, 2, 3]);
});

test("a question about the unsaved draft runs as a labeled hypothetical, and ordinary questions never see the draft", async ({ page }) => {
  await open(page);
  const bodies: { hypothetical_artifact_id?: string }[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/messages$/.test(request.url())) bodies.push(request.postDataJSON()); });
  const details = page.getByRole("complementary", { name: "Details" });
  const cap = details.getByLabel("Single-company cap (%)");
  const useDraft = page.getByRole("checkbox", { name: "Ask about my unsaved draft" });

  // With no draft edits there is nothing hypothetical to select.
  await expect(useDraft).toHaveCount(0);
  await cap.fill("20");
  await expect(page.getByTestId("draft-note")).toContainText("Answers use your saved portfolio");
  await useDraft.check();
  await send(page, "exposure numbers for this draft");

  const labels = thread(page).getByText("Hypothetical draft", { exact: true });
  await expect(labels).toHaveCount(1);
  await expect(thread(page)).toContainText("Based on your unsaved draft, not your saved portfolio");
  await expect(thread(page).getByRole("article").first()).toContainText("Hypothetical portfolio");
  // The selection is for one question: the next one is ordinary unless the draft is chosen again.
  await expect(useDraft).not.toBeChecked();

  // The draft is edited after the capture, and an ordinary question is asked with the draft still open.
  await cap.fill("99");
  await send(page, "Show my holdings");
  await expect(thread(page).getByRole("list", { name: "Referenced results" })).toHaveText(["Portfolio revision 1"]);
  await expect(labels).toHaveCount(1);

  expect(bodies[0].hypothetical_artifact_id).toMatch(/^art-hypo-/);
  expect(bodies[1].hypothetical_artifact_id).toBeUndefined();
  const captured = await (await page.request.get(`/api/portfolio/artifacts/${bodies[0].hypothetical_artifact_id}/payload`)).json();
  expect(captured.settings.single_company_cap).toBe("0.2");
  const stored = await detail(page);
  expect(stored.runs.map(run => run.hypothetical_artifact_id)).toEqual([bodies[0].hypothetical_artifact_id, null]);
  expect(JSON.stringify(stored)).not.toContain("0.99");

  // Reopened from the backend, the hypothetical run and its result are still labeled; nothing was saved or proposed.
  await page.reload();
  await expect(labels).toHaveCount(1);
  await expect(thread(page)).toContainText("Based on your unsaved draft, not your saved portfolio");
  await expect(thread(page).getByRole("article").first()).toContainText("Hypothetical portfolio");
  const committed = await saved(page);
  expect([committed.revision, committed.portfolio.settings.single_company_cap]).toEqual([1, "0.15"]);
  expect(await (await page.request.get("/api/portfolio/proposals?status=pending")).json()).toEqual([]);

  await page.setViewportSize({ width: 390, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("a draft question is sent to the conversation it was asked in, even if another one is opened meanwhile", async ({ page }) => {
  await open(page);
  const rail = page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
  await send(page, "First topic");
  await expect(thread(page)).toContainText("Plain answer to: First topic");
  await rail.getByRole("button", { name: "New conversation" }).click();
  await send(page, "Second topic");
  await expect(thread(page)).toContainText("Plain answer to: Second topic");

  // Storing the draft is slow, and the investor opens the other conversation before it finishes.
  let release = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/portfolio/artifacts", async route => { await held; await route.continue(); });
  await page.getByRole("complementary", { name: "Details" }).getByLabel("Single-company cap (%)").fill("20");
  await page.getByRole("checkbox", { name: "Ask about my unsaved draft" }).check();
  await send(page, "Draft question for the second topic");
  await rail.getByRole("button", { name: "Conversation: First topic" }).click();
  await expect(thread(page)).toContainText("Plain answer to: First topic");
  release();

  const questions = async (title: string) => {
    const summaries = (await (await page.request.get("/api/threads")).json()) as { id: string; title: string }[];
    const stored = await (await page.request.get(`/api/threads/${summaries.find(item => item.title === title)!.id}`)).json();
    return (stored.messages as { role: string; text: string }[]).filter(message => message.role === "user").map(message => message.text);
  };
  await expect.poll(() => questions("Second topic")).toEqual(["Second topic", "Draft question for the second topic"]);
  expect(await questions("First topic")).toEqual(["First topic"]);
  // The conversation on screen is still the one the investor opened.
  await expect(thread(page)).not.toContainText("Draft question for the second topic");

  // A draft that cannot be stored after the investor has moved on does not put its question into the
  // conversation now on screen, where asking it would send it to the wrong place.
  await page.unroute("**/api/portfolio/artifacts");
  let fail = () => {};
  const failing = new Promise<void>(resolve => { fail = resolve; });
  await page.route("**/api/portfolio/artifacts", async route => { await failing; await route.fulfill({ status: 503, json: { detail: "offline" } }); });
  await page.getByRole("checkbox", { name: "Ask about my unsaved draft" }).check();
  await send(page, "Draft question for the first topic");
  await rail.getByRole("button", { name: "Conversation: Second topic" }).click();
  await expect(thread(page)).toContainText("Plain answer to: Second topic");
  fail();
  await expect(page.getByText("Your draft could not be stored, so the question was not sent.")).toBeVisible();
  await expect(ask(page)).toHaveValue("");
  expect(await questions("First topic")).toEqual(["First topic"]);
  expect(await questions("Second topic")).toEqual(["Second topic", "Draft question for the second topic"]);
});
