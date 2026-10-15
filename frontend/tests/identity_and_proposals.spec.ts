import { test, expect, type Page } from "@playwright/test";
import { openDetails, seedPortfolio } from "./test_helpers";

const INITIAL_UNRESOLVED_PORTFOLIO = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [
      {
        id: "tfsa-cash-cad",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "5000",
      },
    ],
    fx: [],
  },
  average_costs: {},
  settings: { single_company_cap: "0.20" },
  unresolved: [
    {
      account_id: "tfsa",
      ticker: "BMO",
      shares: "50",
      currency: "CAD",
      listing: null,
      kind: null,
      average_cost: "125.50",
      candidates: ["XTSE", "XNYS"],
    },
  ],
};

const UNRESOLVED_UNKNOWN_PORTFOLIO = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [
      {
        id: "tfsa-cash-cad",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "5000",
      },
    ],
    fx: [],
  },
  average_costs: {},
  settings: { single_company_cap: "0.20" },
  unresolved: [
    {
      account_id: "tfsa",
      ticker: "XYZ",
      shares: "100",
      currency: "CAD",
      listing: null,
      kind: null,
      average_cost: "50.00",
      candidates: [],
    },
  ],
};

async function openDesk(page: Page, portfolio: object = INITIAL_UNRESOLVED_PORTFOLIO) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname)
      ? route.continue()
      : route.abort()
  );
  await seedPortfolio(page, portfolio);
  await page.goto("/");
}

test("browser unresolved holding: partial correction -> preview -> confirm -> complete resolution -> confirm", async ({
  page,
}) => {
  await openDesk(page, UNRESOLVED_UNKNOWN_PORTFOLIO);

  // Initial state has 1 unresolved holding
  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Holdings" }).click();

  const needsGroup = details.getByRole("group", { name: "Holdings to identify" });
  await expect(needsGroup).toBeVisible();
  await expect(needsGroup).toContainText("Needs you · 1 holding to identify");

  // Partial correction: select exchange only
  const exchangeSelect = needsGroup.getByRole("combobox", { name: "Exchange for XYZ" });
  await exchangeSelect.selectOption("XTSE");

  // Click Save XYZ to prepare preview
  const saveBtn = needsGroup.getByRole("button", { name: "Save XYZ" });
  await saveBtn.click();

  // Preview dialog opens
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Identity correction · prepared from revision 1");
  await expect(dialog.getByRole("table", { name: "What changes" }).getByRole("row", { name: /XYZ/ }).first()).toContainText("XTSE");

  // Verify backend committed state remains revision 1 before confirmation
  const checkRev1 = await (await page.request.get("/api/portfolio")).json();
  expect(checkRev1.revision).toBe(1);
  expect(checkRev1.portfolio.unresolved[0].listing).toBeNull();

  // Confirm changes
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);

  // Committed state is now revision 2, holding remains unresolved with listing XTSE
  await expect.poll(async () => {
    const res = await (await page.request.get("/api/portfolio")).json();
    return [res.revision, res.portfolio.unresolved[0]?.listing, res.portfolio.unresolved[0]?.kind];
  }).toEqual([2, "XTSE", null]);

  // In the browser, Exchange dropdown is no longer shown, but Type dropdown is now available
  await expect(needsGroup.getByRole("combobox", { name: "Exchange for XYZ" })).toHaveCount(0);
  const typeSelect = needsGroup.getByRole("combobox", { name: "Type for XYZ" });
  await expect(typeSelect).toBeVisible();

  // Complete correction: select Type
  await typeSelect.selectOption("stock");
  await needsGroup.getByRole("button", { name: "Save XYZ" }).click();

  // Preview dialog opens for complete resolution
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Identity correction · prepared from revision 2");

  // Confirm complete resolution
  await dialog.getByTestId("confirm-proposal").click();
  await expect(dialog).toHaveCount(0);

  // Committed state is now revision 3, 0 unresolved, holding in positions with average cost
  await expect.poll(async () => {
    const res = await (await page.request.get("/api/portfolio")).json();
    return [
      res.revision,
      res.portfolio.unresolved.length,
      res.portfolio.snapshot.positions.some((p: { ticker?: string }) => p.ticker === "XYZ"),
      res.portfolio.average_costs["tfsa-xyz"],
    ];
  }).toEqual([3, 0, true, "50.00"]);

  // Unresolved group is gone from holdings tab
  await expect(needsGroup).toHaveCount(0);
  await expect(details).toContainText("Average cost C$50");
});

test("reload discovers pending proposal and stale state is identified when committed portfolio changes", async ({
  page,
}) => {
  await openDesk(page);

  const details = await openDetails(page);
  await details.getByRole("tab", { name: "Holdings" }).click();

  // Select exchange for BMO
  const needsGroup = details.getByRole("group", { name: "Holdings to identify" });
  await needsGroup.getByRole("combobox", { name: "Exchange for BMO" }).selectOption("XTSE");
  await needsGroup.getByRole("button", { name: "Save BMO" }).click();

  // Preview dialog opens (proposal prepared on base revision 1)
  const dialog = page.getByRole("dialog", { name: "Review portfolio changes" });
  await expect(dialog).toBeVisible();

  // Reload the page without confirming or cancelling
  await page.reload();

  // Discover pending proposal banner on the desk
  const pendingBanner = page.getByTestId("pending-proposals-banner");
  await expect(pendingBanner).toBeVisible();
  await expect(pendingBanner).toContainText("Identity correction");
  await expect(pendingBanner).toContainText("Prepared from revision 1");

  // Reopen the proposal preview
  await page.getByTestId("reopen-proposal").click();
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Identity correction · prepared from revision 1");

  // Advance committed revision in background to revision 2
  const competingPrep = await page.request.put("/api/portfolio", {
    data: {
      base_revision: 1,
      portfolio: {
        ...INITIAL_UNRESOLVED_PORTFOLIO,
        settings: { single_company_cap: "0.10" },
      },
    },
  });
  expect(competingPrep.ok()).toBeTruthy();
  const competingProp = (await competingPrep.json()).proposal;
  const competingConfirm = await page.request.post(
    `/api/portfolio/proposals/${competingProp.id}/confirm`
  );
  expect(competingConfirm.ok()).toBeTruthy();

  // Attempt to confirm the reopened proposal (which was prepared on base revision 1)
  await dialog.getByTestId("confirm-proposal").click();

  // Stale alert is clearly displayed and confirm button is hidden/gone
  await expect(dialog.getByRole("alert")).toContainText("This proposal is out of date");
  await expect(dialog.getByTestId("confirm-proposal")).toBeDisabled();

  // Cancel proposal closes the review
  await dialog.getByTestId("cancel-proposal").click();
  await expect(dialog).toHaveCount(0);
});

test("decision journal confirmation leaves portfolio revision unchanged", async ({ page }) => {
  await openDesk(page);

  // Confirm initial revision is 1
  const initial = await (await page.request.get("/api/portfolio")).json();
  expect(initial.revision).toBe(1);

  // Save a decision in the decision journal
  const saveRes = await page.request.post("/api/decisions", {
    data: {
      decision: {
        id: "dec-e2e-01",
        saved_at: "2026-09-30T12:00:00Z",
        as_of: "2026-09-30",
        question: "Is cash adequate?",
        evidence_references: [],
        reasoning: {
          reason: "Cash is adequate for near term.",
          assumptions: [],
          uncertainty: [],
          what_could_change: [],
          downside: "None",
          alternatives: [],
        },
        conclusion: { preferred_action: "hold", amount: null },
        confirmed_action: null,
      },
    },
  });
  expect(saveRes.ok()).toBeTruthy();

  // Confirm action in the decision journal
  const confirmDecision = await page.request.post("/api/decisions/dec-e2e-01/confirm", {
    data: {
      action: "hold",
      notes: "User confirmed hold",
    },
  });
  expect(confirmDecision.ok()).toBeTruthy();
  const confirmedData = await confirmDecision.json();
  expect(confirmedData.confirmed_action.action).toBe("hold");

  // Crucial check: portfolio revision remains 1 and holdings are unchanged!
  const finalPortfolio = await (await page.request.get("/api/portfolio")).json();
  expect(finalPortfolio.revision).toBe(1);
  expect(finalPortfolio.portfolio.snapshot.as_of).toBe("2026-09-30");
});
