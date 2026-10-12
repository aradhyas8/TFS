import { test, expect, type Page } from "@playwright/test";
import { seedPortfolio } from "./test_helpers";

const SAVED = {
  snapshot: {
    as_of: "2026-09-30", reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [
      { id: "tfsa-acme", account_id: "tfsa", kind: "stock", currency: "USD", ticker: "ACME", listing: "XNAS", company_id: "acme", company_name: "Acme Corp",
        shares: "10", mark: { value: "100", as_of: "2026-09-30", source: "Broker display" } },
      { id: "tfsa-cash", account_id: "tfsa", kind: "cash", currency: "CAD", cash: "1000" },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "81.25" },
  settings: { single_company_cap: "0.15" },
  unresolved: [],
};

const ask = (page: Page) => page.getByRole("textbox", { name: /type \/ for workflows/ });
const thread = (page: Page) => page.getByRole("log", { name: "Conversation" });
const rail = (page: Page) => page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
const threads = async (page: Page) => (await page.request.get("/api/threads")).json();

async function open(page: Page, saved?: object) {
  await page.route("**/*", route => ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
  if (saved) await seedPortfolio(page, saved);
  else await page.request.delete("/api/test/portfolio");
  await page.goto("/");
}

async function send(page: Page, question: string) {
  await ask(page).fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

test("a conceptual question without a portfolio is answered in a durable thread, not a review", async ({ page }) => {
  await open(page);
  const analyses: string[] = [];
  page.on("request", request => { if (request.url().includes("/api/analyze")) analyses.push(request.url()); });

  await expect(rail(page)).toContainText("No conversations yet");
  await send(page, "What is an ETF?");

  await expect(thread(page)).toContainText("What is an ETF?");
  await expect(thread(page)).toContainText("Plain answer to: What is an ETF?");
  await expect(page.getByRole("region", { name: "Analyst memo" })).toHaveCount(0);
  expect(analyses).toHaveLength(0);

  const saved = await threads(page);
  expect(saved).toHaveLength(1);
  expect(saved[0].title).toBe("What is an ETF?");
  await expect(rail(page).getByRole("button", { name: "Conversation: What is an ETF?" })).toHaveAttribute("aria-current", "true");
  // The composer is ready for a follow-up in the same thread.
  await expect(ask(page)).toBeEnabled();
  await expect(ask(page)).toHaveValue("");
});

test("a running answer is polled with short requests and survives a reload from the backend", async ({ page }) => {
  await open(page, SAVED);
  const polls: string[] = [];
  page.on("request", request => { if (/\/api\/threads\/[^/]+\/runs\//.test(request.url())) polls.push(request.method()); });

  await send(page, "Slow question about diversification");
  await expect(thread(page)).toContainText("Slow question about diversification");
  await expect(thread(page)).toContainText("Thinking");
  await expect(ask(page)).toBeDisabled();

  await page.reload();
  // Restored from the backend while the run is still going: the question, and the wait, are both back.
  await expect(thread(page)).toContainText("Slow question about diversification");
  await expect(thread(page)).toContainText("Plain answer to: Slow question about diversification");
  await expect(thread(page)).not.toContainText("Thinking");
  await expect(ask(page)).toBeEnabled();
  expect(polls.length).toBeGreaterThan(0);
  expect(new Set(polls)).toEqual(new Set(["GET"]));

  const [summary] = await threads(page);
  const detail = await (await page.request.get(`/api/threads/${summary.id}`)).json();
  expect(detail.runs).toHaveLength(1);
  expect(detail.runs[0].status).toBe("completed");
  expect(detail.runs[0].bound_revision).toBe(1);
  expect(detail.messages.map((message: { role: string }) => message.role)).toEqual(["user", "assistant"]);
  expect(await page.evaluate(() => localStorage.getItem("desk.pendingSend"))).toBeNull();
});

test("a submission whose response is lost is retried with the same request ID and runs once", async ({ page }) => {
  await open(page);
  const bodies: { client_request_id: string }[] = [];
  await page.route("**/api/threads/*/messages", async route => {
    bodies.push(route.request().postDataJSON());
    if (bodies.length > 1) return route.continue();
    await route.fetch(); // the backend accepts the message, then the response never arrives
    await route.abort("connectionreset");
  });

  await send(page, "What is a dividend?");
  await expect(thread(page)).toContainText("Plain answer to: What is a dividend?");

  expect(bodies).toHaveLength(2);
  expect(bodies[1].client_request_id).toBe(bodies[0].client_request_id);
  const [summary, ...others] = await threads(page);
  expect(others).toHaveLength(0);
  const detail = await (await page.request.get(`/api/threads/${summary.id}`)).json();
  expect(detail.runs).toHaveLength(1);
  expect(detail.messages.filter((message: { role: string }) => message.role === "user")).toHaveLength(1);
  await expect(thread(page).getByText("What is a dividend?", { exact: true })).toHaveCount(1);
});

test("threads are listed, switched and reopened after reload, by pointer or keyboard", async ({ page }) => {
  await open(page);
  await send(page, "What is an index fund?");
  await expect(thread(page)).toContainText("Plain answer to: What is an index fund?");

  await rail(page).getByRole("button", { name: "New conversation" }).click();
  await expect(thread(page)).toHaveCount(0);
  await send(page, "What is a bond ladder?");
  await expect(thread(page)).toContainText("Plain answer to: What is a bond ladder?");
  await expect(thread(page)).not.toContainText("index fund");

  const items = rail(page).getByTestId("thread-item");
  await expect(items).toHaveCount(2);
  const first = rail(page).getByRole("button", { name: "Conversation: What is an index fund?" });
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(thread(page)).toContainText("Plain answer to: What is an index fund?");
  await expect(thread(page)).not.toContainText("bond ladder");
  await expect(first).toHaveAttribute("aria-current", "true");

  await page.reload();
  await expect(thread(page)).toContainText("Plain answer to: What is an index fund?");
  await expect(thread(page)).not.toContainText("bond ladder");

  // A follow-up continues the reopened thread rather than starting another.
  await ask(page).fill("And what about fees?");
  await ask(page).press("Enter");
  await expect(thread(page)).toContainText("Plain answer to: And what about fees?");
  expect(await threads(page)).toHaveLength(2);
});

test("a failed send and a failed answer stay distinct from the saved conversation, and drafts stay out of it", async ({ page }) => {
  await open(page, SAVED);
  // An unsaved rule edit: a draft, not part of the committed portfolio or of any answer.
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByLabel("Single-company cap (%)").fill("42");

  await page.route("**/api/threads/*/messages", route => route.fulfill({ status: 503, json: { detail: "The analyst service is offline." } }));
  await send(page, "How does diversification work?");
  const notSent = thread(page).getByRole("alert").filter({ hasText: "Not sent" });
  await expect(notSent).toContainText("The analyst service is offline.");
  await expect(page.getByTestId("draft-note")).toContainText("Unsaved draft edits are not included");
  const [empty] = await (await page.request.get("/api/threads")).json();
  expect((await (await page.request.get(`/api/threads/${empty.id}`)).json()).messages).toHaveLength(0);
  await expect(rail(page)).toContainText("No conversations yet");

  await page.unroute("**/api/threads/*/messages");
  await thread(page).getByRole("button", { name: "Send again" }).click();
  await expect(thread(page)).toContainText("Plain answer to: How does diversification work?");
  await expect(thread(page).getByRole("alert")).toHaveCount(0);

  await send(page, "Fail this answer about diversification");
  const failed = thread(page).getByRole("status").filter({ hasText: "This answer did not finish" });
  await expect(failed).toContainText("the fake model is unavailable");
  await thread(page).getByRole("button", { name: "Ask again" }).click();
  await expect(failed).toHaveCount(2);
  // The composer takes the keyboard back once the answer has ended.
  await expect(ask(page)).toBeFocused();

  const [summary] = await threads(page);
  const detail = await (await page.request.get(`/api/threads/${summary.id}`)).json();
  const runs = detail.runs as { status: string; client_request_id: string; bound_revision: number }[];
  expect(runs.map(run => run.status)).toEqual(["completed", "failed", "failed"]);
  expect(new Set(runs.map(run => run.client_request_id)).size).toBe(3);
  expect(JSON.stringify(detail)).not.toContain("0.42");
  // The draft is still a draft, and the committed portfolio is unchanged.
  await expect(details.getByLabel("Single-company cap (%)")).toHaveValue("42");
  expect((await (await page.request.get("/api/portfolio")).json()).portfolio.settings.single_company_cap).toBe("0.15");
});

test("slash shortcuts, natural workflow routes and the decision register still work, and none of them creates a thread", async ({ page }) => {
  await open(page, SAVED);
  await ask(page).fill("/rev");
  await ask(page).press("Enter");
  await expect(ask(page)).toHaveValue("/review ");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const memo = page.getByRole("region", { name: "Analyst memo" });
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(page.locator(".ctx")).toContainText("Portfolio review");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(rail(page).getByTestId("decision-item")).toHaveCount(1);

  // The cached last answer and the saved decision come back after a reload; neither becomes a conversation.
  await page.reload();
  await expect(memo.getByRole("heading", { level: 2 })).toBeVisible();
  await expect(rail(page)).toContainText("No conversations yet");
  expect(await (await page.request.get("/api/threads")).json()).toEqual([]);

  await send(page, "What do you think about ACME?");
  await expect(page.locator(".ctx")).toContainText("Stock analysis · ACME");
  await expect(page.getByRole("main").getByRole("heading", { level: 2 })).toContainText("ACME");
  await rail(page).getByTestId("all-decisions-link").click();
  await expect(page.getByRole("heading", { name: "Decisions Register" })).toBeVisible();
  expect(await (await page.request.get("/api/threads")).json()).toEqual([]);

  // From a conversation, a shortcut still opens its workflow and the conversation stays in the list.
  await send(page, "What is a stock split?");
  await expect(thread(page)).toContainText("Plain answer to: What is a stock split?");
  await ask(page).fill("/new-cash");
  await ask(page).press("Enter");
  await expect(page.getByLabel("Amount", { exact: true })).toBeVisible();
  await expect(rail(page).getByTestId("thread-item")).toHaveCount(1);
});

test("conversation state shows result references but never credentials, private continuation or average costs", async ({ page }) => {
  await open(page, SAVED);
  const payloads: string[] = [];
  page.on("response", async response => {
    if (response.url().includes("/api/threads")) payloads.push(await response.text().catch(() => ""));
  });

  await send(page, "List my holdings");
  await expect(thread(page)).toContainText("Checked holdings");
  await expect(thread(page).getByRole("list", { name: "Referenced results" })).toContainText("Portfolio revision 1");
  await expect(thread(page)).toContainText("Plain answer to: List my holdings");
  // The stored tool record is never shown as prose.
  await expect(thread(page)).not.toContainText("retrieval");

  const browserState = JSON.stringify(await page.evaluate(() => ({ ...localStorage, ...sessionStorage }))) + (await page.getByRole("main").innerHTML());
  for (const text of [...payloads, browserState]) {
    expect(text).not.toContain("sk-test-backend-only-never-browser");
    expect(text).not.toContain("81.25");
    expect(text).not.toContain("average_cost");
    expect(text).not.toContain("continuation");
  }
  expect(payloads.length).toBeGreaterThan(2);
});

test("ordinary questions stay in the conversation when a portfolio exists, and a hidden thread is never written to", async ({ page }) => {
  await open(page, SAVED);
  const lookups: string[] = [];
  page.on("request", request => { if (/\/api\/(candidates|analyze)/.test(request.url())) lookups.push(request.url()); });

  // A capitalised word is not a ticker lookup, and an unknown subject is not a stock clarification.
  await send(page, "What is an ETF?");
  await expect(thread(page)).toContainText("Plain answer to: What is an ETF?");
  await send(page, "How is inflation measured?");
  await expect(thread(page)).toContainText("Plain answer to: How is inflation measured?");
  await send(page, "What do you think about Shopify?");
  await expect(thread(page)).toContainText("Plain answer to: What do you think about Shopify?");
  expect(lookups).toEqual([]);
  expect(await threads(page)).toHaveLength(1);

  // Asked from the decision register, a question starts its own conversation.
  await rail(page).getByTestId("all-decisions-link").click();
  await expect(page.getByRole("heading", { name: "Decisions Register" })).toBeVisible();
  await send(page, "What is a bond?");
  await expect(thread(page)).toContainText("Plain answer to: What is a bond?");
  await expect(thread(page)).not.toContainText("What is an ETF?");
  expect(await threads(page)).toHaveLength(2);
});

test("a send that was never acknowledged is recovered after a reload without running twice", async ({ page }) => {
  await open(page);
  // The backend accepts the message; the browser never hears back and is reloaded mid-wait.
  await page.route("**/api/threads/*/messages", async route => {
    await route.fetch();
    await new Promise(() => {});
  });
  await send(page, "What is a mutual fund?");
  await expect(thread(page)).toContainText("Sending");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("desk.pendingSend"))).toContain("What is a mutual fund?");
  await expect.poll(async () => (await threads(page)).length).toBe(1);

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await page.reload();
  await expect(thread(page)).toContainText("Plain answer to: What is a mutual fund?");
  await expect(thread(page).getByText("What is a mutual fund?", { exact: true })).toHaveCount(1);
  const [summary] = await threads(page);
  expect((await (await page.request.get(`/api/threads/${summary.id}`)).json()).runs).toHaveLength(1);
  expect(await page.evaluate(() => localStorage.getItem("desk.pendingSend"))).toBeNull();
});

test("discarding a first message that could not be sent returns to the start, not to an empty conversation", async ({ page }) => {
  await open(page);
  await page.route("**/api/threads/*/messages", route => route.fulfill({ status: 503, json: { detail: "The analyst service is offline." } }));
  await send(page, "What is a share?");
  await expect(thread(page).getByRole("alert")).toContainText("Not sent");
  await thread(page).getByRole("button", { name: "Discard message" }).click();
  await expect(thread(page)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Start with what you own." })).toBeVisible();
  await expect(page.getByRole("status", { name: "Loading conversation" })).toHaveCount(0);
  await expect(ask(page)).toBeFocused();
});
