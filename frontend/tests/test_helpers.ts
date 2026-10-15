import { expect, type Locator, type Page } from "@playwright/test";

/** Details are closed until asked for. Opens them with the Details button if nothing else has, and returns them. */
export async function openDetails(page: Page) {
  const details = page.getByRole("complementary", { name: "Details" });
  if (!(await details.isVisible())) await page.getByRole("button", { name: "Details", exact: true }).click();
  await expect(details).toBeVisible();
  return details;
}

export async function seedPortfolio(page: Page, saved: object) {
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

/** Opens the Portfolio view from the rail, runs the checks on it, and goes back to the conversation as it was. */
export async function checkPortfolio(page: Page, check: (view: Locator) => Promise<void>) {
  await page.getByRole("navigation", { name: "Portfolio, conversations and decisions" }).getByRole("button", { name: /^Portfolio/ }).click();
  const view = page.getByRole("region", { name: "Portfolio" });
  await expect(view).toBeVisible();
  await check(view);
  await page.getByRole("button", { name: "Back to chat" }).click();
  await expect(view).toBeHidden();
}

/** Opens the Decisions view from the rail and returns its list. */
export async function openDecisions(page: Page) {
  await page.getByRole("navigation", { name: "Portfolio, conversations and decisions" }).getByTestId("all-decisions-link").click();
  const register = page.getByRole("region", { name: "Decisions register" });
  await expect(register).toBeVisible();
  return register;
}
