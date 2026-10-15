import { test, expect, type Page } from "@playwright/test";
import { openDetails } from "./test_helpers";

const SAVED = {
  snapshot: {
    as_of: "2026-09-30",
    reporting_currency: "CAD",
    accounts: [{ id: "tfsa", name: "TFSA" }],
    positions: [
      {
        id: "tfsa-acme",
        account_id: "tfsa",
        kind: "stock",
        currency: "USD",
        ticker: "ACME",
        listing: "XNAS",
        company_id: "acme",
        company_name: "Acme Corp",
        shares: "10",
        mark: { value: "100", as_of: "2026-09-30", source: "Broker display" },
      },
      {
        id: "tfsa-xeqt",
        account_id: "tfsa",
        kind: "etf",
        currency: "CAD",
        ticker: "XEQT",
        listing: "XTSE",
        company_id: "xeqt",
        company_name: "iShares Core Equity",
        shares: "50",
        mark: { value: "30", as_of: "2026-09-30", source: "Broker display" },
      },
      {
        id: "tfsa-cash-cad",
        account_id: "tfsa",
        kind: "cash",
        currency: "CAD",
        cash: "1000",
      },
    ],
    fx: [{ from_currency: "USD", to_currency: "CAD", rate: "1.35", as_of: "2026-09-30", source: "Broker display" }],
  },
  average_costs: { "tfsa-acme": "80", "tfsa-xeqt": "25" },
  settings: { single_company_cap: "0.5" },
  unresolved: [],
};

async function seedPortfolio(page: Page, saved: object = SAVED) {
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

async function open(page: Page, saved: object = SAVED) {
  await page.route("**/*", route =>
    ["127.0.0.1", "localhost"].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort()
  );
  await seedPortfolio(page, saved);
  await page.goto("/");
  await expect(page.getByTestId("portfolio-date")).toHaveText("Holdings as of Sep 30, 2026");
}

test.describe("Ticket 08: Immutable hypothetical draft analysis", () => {
  test("analyzes draft snapshot and settings without advancing revision, survives typing and reload", async ({
    page,
  }) => {
    const artifactRequests: any[] = [];
    const analyzeRequests: any[] = [];

    page.on("request", request => {
      if (request.url().includes("/api/portfolio/artifacts") || request.url().includes("/api/artifacts")) {
        if (request.method() === "POST") {
          artifactRequests.push(request.postDataJSON());
        }
      }
      if (request.url().endsWith("/api/analyze")) {
        analyzeRequests.push(request.postDataJSON());
      }
    });

    await open(page);

    // 1. Initial committed state has revision 1
    const initialPortfolioRes = await page.request.get("/api/portfolio");
    const initialPortfolio = await initialPortfolioRes.json();
    expect(initialPortfolio.revision).toBe(1);

    // Open details panel on Holdings tab
    const details = await openDetails(page);
    await details.getByRole("tab", { name: "Holdings" }).click();

    const capInput = details.getByRole("textbox", { name: "Single-company cap (%)" });
    await expect(capInput).toBeVisible();

    // 2. Modify rule in draft
    await capInput.fill("20");

    // "Unsaved draft edits" banner appears with "Analyze draft"
    await expect(details.getByText("Unsaved draft edits")).toBeVisible();
    const analyzeDraftBtn = details.getByTestId("analyze-draft");
    await expect(analyzeDraftBtn).toBeVisible();

    // 3. Trigger hypothetical draft analysis
    await analyzeDraftBtn.click();

    // 4. Wait for hypothetical analysis memo to appear
    await expect(page.getByTestId("hypothetical-banner")).toBeVisible();
    await expect(page.getByTestId("hypothetical-badge")).toHaveText("Hypothetical analysis");
    await expect(page.getByTestId("artifact-source-ref")).toContainText("Source: art-hypo-");
    await expect(page.getByTestId("artifact-source-ref")).toContainText("Hypothetical Draft Portfolio");
    await expect(page.getByText("Committed portfolio revision unchanged")).toBeVisible();

    // 5. Subsequent edits: switch back to Holdings tab and type further edits
    await details.getByRole("tab", { name: "Holdings" }).click();
    await capInput.fill("99");

    // Memo and its artifact reference are NOT altered by later typing into draft inputs
    await expect(page.getByTestId("hypothetical-banner")).toBeVisible();
    await expect(page.getByTestId("artifact-source-ref")).toContainText("Hypothetical Draft Portfolio");

    // 6. Verify captured artifact request and analysis request
    expect(artifactRequests.length).toBeGreaterThanOrEqual(1);
    const sentArtifact = artifactRequests[0];
    expect(sentArtifact.label).toBe("Hypothetical Draft Portfolio");
    expect(sentArtifact.source_artifact_id).toBe("art-rev-1");
    expect(sentArtifact.portfolio.settings.single_company_cap).toBe("0.2");
    // Captured snapshot at click time: single_company_cap was 0.2, NOT subsequent edit 0.99
    expect(sentArtifact.portfolio.settings.single_company_cap).not.toBe("0.99");

    expect(analyzeRequests.length).toBeGreaterThanOrEqual(1);
    const sentAnalyze = analyzeRequests[0];
    expect(sentAnalyze.artifact_reference).toBeDefined();
    expect(sentAnalyze.artifact_reference.basis).toBe("hypothetical");
    expect(sentAnalyze.artifact_reference.label).toBe("Hypothetical Draft Portfolio");
    expect(sentAnalyze.settings.single_company_cap).toBe("0.2");
    expect(sentAnalyze.settings.single_company_cap).not.toBe("0.99");

    // Check stored artifact directly from backend endpoint
    const artifactLookup = await page.request.get(`/api/portfolio/artifacts/${sentAnalyze.artifact_reference.id}`);
    expect(artifactLookup.ok()).toBeTruthy();
    const artifactRefData = await artifactLookup.json();
    expect(artifactRefData.id).toBe(sentAnalyze.artifact_reference.id);
    expect(artifactRefData.basis).toBe("hypothetical");

    const payloadLookup = await page.request.get(`/api/portfolio/artifacts/${sentAnalyze.artifact_reference.id}/payload`);
    expect(payloadLookup.ok()).toBeTruthy();
    const artifactRecord = await payloadLookup.json();
    expect(artifactRecord.settings.single_company_cap).toBe("0.2");
    expect(artifactRecord.settings.single_company_cap).not.toBe("0.99");

    // Average costs must stay OUTSIDE analysis requests
    expect((sentAnalyze as any).average_costs).toBeUndefined();
    expect(sentAnalyze.portfolio.average_costs).toBeUndefined();

    // 7. Verify committed revision did not advance and no proposals were created/confirmed
    const afterHypoRes = await page.request.get("/api/portfolio");
    const afterHypo = await afterHypoRes.json();
    expect(afterHypo.revision).toBe(1);
    expect(afterHypo.portfolio.settings.single_company_cap).toBe("0.5"); // original committed value

    const proposalsRes = await page.request.get("/api/portfolio/proposals");
    const proposals = await proposalsRes.json();
    const pendingProposals = proposals.filter((p: any) => p.status === "pending");
    expect(pendingProposals.length).toBe(0);
    // Only the initial seed proposal exists, no new proposal was created
    expect(proposals.length).toBe(1);

    // 8. Survives reload: cached hypothetical result retains banner and metadata
    await page.reload();
    await expect(page.getByTestId("hypothetical-banner")).toBeVisible();
    await expect(page.getByTestId("hypothetical-badge")).toHaveText("Hypothetical analysis");
    await expect(page.getByTestId("artifact-source-ref")).toContainText("Source: art-hypo-");
    await expect(page.getByTestId("artifact-source-ref")).toContainText("Hypothetical Draft Portfolio");

    // Revision still unchanged after reload
    const afterReloadRes = await page.request.get("/api/portfolio");
    const afterReload = await afterReloadRes.json();
    expect(afterReload.revision).toBe(1);

    // 9. Ordinary analysis continues using committed state
    // Ask ordinary question from composer
    const ask = page.getByRole("textbox", { name: /type \/ for workflows/ });
    await ask.fill("/review Review my portfolio");
    await page.getByRole("button", { name: "Ask", exact: true }).click();

    // Wait for ordinary analysis
    await expect(page.getByRole("region", { name: "Analyst memo" })).toBeVisible();
    // Ordinary analysis does not have hypothetical banner
    await expect(page.getByTestId("hypothetical-banner")).toHaveCount(0);

    const ordinaryAnalyze = analyzeRequests[analyzeRequests.length - 1];
    expect(ordinaryAnalyze.artifact_reference).toBeUndefined();
    expect(ordinaryAnalyze.settings.single_company_cap).toBe("0.5"); // uses committed state
  });
});
