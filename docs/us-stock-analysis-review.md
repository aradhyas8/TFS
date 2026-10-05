# Ticket 05 implementation review

Fixed point: `b1bcdedc2d0aeb0947b827b5e6b521eeba8dd89f` (starting HEAD). Initial implementation: `87025bb`. Review command: `git diff b1bcdedc2d0aeb0947b827b5e6b521eeba8dd89f...HEAD`. Originating ticket: `.scratch/personal-investment-analyst/issues/05-us-stock-analysis-in-portfolio-context.md`; authority: `specs/personal-investment-analyst.md`.

## Standards

Native Codex Standards reviewer: no findings. The diff shows no documented-standard breaches: native Codex constraints are respected, the parent read the relevant installed Next.js guide, and the work follows the repository's API/browser seams and local specification workflow.

The new research provider and stock calculator have distinct responsibilities. Existing shared contracts and orchestration remain consistent with repository conventions; no baseline smell warrants an actionable finding.

Standards: 0 findings.

## Spec

The native Spec reviewer encountered a runtime usage limit and could not complete its review. The primary native Codex agent completed this axis directly; no external model or service was substituted.

Three findings were corrected:

1. **Book-value payout arithmetic.** The ticket requires "company-appropriate five-year cases" and "sector-appropriate ratios." Applying payout to book capital overstated modeled distributions. Book cases now use explicit annual ROE times opening book to calculate earnings for payout. A positive payout without ROE leaves the case unknown. API regressions independently verify the known dividend outcome and missing-input behavior.
2. **Comparison instrument display.** "Serious alternatives use instrument-appropriate five-year cases on a common date/currency basis." The generic comparison display labeled a researched stock as cash. The stock alternative now has its own label, checked by the browser journey.
3. **Stock comparison distribution assumptions.** "Preserve ... metric definitions" and "company-case arithmetic" require displayed assumptions to match calculation. Stock comparison drivers could claim reinvestment while the company case kept distributions idle. They now require the company FX path, null ETF/rate/income paths, a neutral basis and no reinvestment; Python uses the company case consistently.

No unresolved implementation finding remains in the reviewed bounded scope. Primary research is supplied through backend-owned independently reviewed extracts with original SEC/issuer links, rather than live filing discovery. This limitation is explicit in the application and source documentation. Allocation amounts remain null; passing a preview does not establish justified sizing. Automated fake answers verify the integration and arithmetic, not live model judgment.

Spec: 3 findings, all resolved; the most material was book-value payout arithmetic.

## Verification

- Final full backend suite: 188 passed; mypy and Ruff passed.
- Full browser suite: 21 passed. Both affected stock journeys were rerun after review corrections and passed.
- Frontend typecheck and production build passed.
- API and SDK tests use fake model/data/research boundaries with real Python calculations. Network guards prohibit live OpenAI and market-data requests.
- A browser screenshot of the completed stock result was inspected. Fixed-answer consistency and live answer-quality limitations are documented in `docs/us-stock-analysis.md`.

Standards: 0 findings. Spec: 3 resolved findings, 0 outstanding.
