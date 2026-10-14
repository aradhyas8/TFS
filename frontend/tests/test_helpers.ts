import { expect, type Page } from "@playwright/test";

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
