import { test, expect } from "@playwright/test";

test("/classic returns 404 and no classic links or references exist in desk", async ({ page }) => {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );

  // 1. Navigating to /classic returns 404
  const res = await page.goto("/classic");
  expect(res?.status()).toBe(404);
  await expect(page.locator("body")).toContainText("404");

  // 2. Open desk
  await page.goto("/");

  // 3. Verify zero links to /classic exist in the DOM
  const classicLinks = page.locator('a[href="/classic"], a[href*="classic"]');
  await expect(classicLinks).toHaveCount(0);

  // 4. Verify Rail contains no "Classic view"
  const rail = page.getByRole("navigation", { name: "Portfolio, conversations and decisions" });
  await expect(rail).not.toContainText("Classic view");
  await expect(rail).not.toContainText("classic");

  // 5. Verify Slash menu workflows have no "classic view"
  const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
  await ask.fill("/");
  const slashMenu = page.getByRole("listbox", { name: "Workflows" });
  await expect(slashMenu).toBeVisible();
  await expect(slashMenu).not.toContainText("classic");
  await expect(slashMenu).not.toContainText("classic view");

  // 6. Verify Panel (Holdings tab) has no "classic view" copy
  await ask.fill("");
  const details = page.getByRole("complementary", { name: "Details" });
  await details.getByRole("tab", { name: "Holdings" }).click();
  await expect(details).not.toContainText("classic");
  await expect(details).not.toContainText("classic view");
});
