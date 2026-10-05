# Ticket 08 completion and review report

Reviewed October 4, 2026. Baseline: `8bcb2e4bc45e5c59101c2f10d091cef36ceba83c`. The implement workflow used the existing current branch and the approved public API and browser journey test seams.

## Delivered behavior

The existing question form accepts a theme mechanism, supplied shortlist, candidate/tool-call bounds, explicit agreement and optional loss/withdrawal context. Unconfirmed requests clarify without model or candidate research. Changes clear agreement. The shared FastAPI/OpenAI pipeline binds primary company evidence, records evidence-linked mechanism tests, runs instrument-appropriate Python cases, compares ETF/cash/actual no action and enforces portfolio guards. Research cannot leave the shortlist, exceed effort or continue after comparison.

A plausible theme can lead to no action. Conditional purchase alternatives stay qualified without changing a supported no-action conclusion. Eligible preferred additions reuse deterministic `size_review`; missing source evidence, context, limits, funding or costs/tax effects leaves amounts unknown. Company exposure and same-listing fund exposure aggregate across accounts. Both amount endpoints receive hypothetical funding and guardrail checks; no orders are placed.

Agency/macro evidence is unavailable through the core theme adapter, and Canadian company evidence awaits the later extension. Missing data remains unknown. Evidence adapters use independently reviewed files and supplied sponsor facts rather than claiming live verification.

## Verification

| Check | Result |
| --- | --- |
| Full backend suite | 293 passed, including 38 theme boundary tests |
| Full browser suite | 26 passed |
| Backend mypy / Ruff | Passed |
| Frontend TypeScript | Passed |
| Next.js production build | Passed |
| Git whitespace check | Passed |

Fake model turns run real calculations and validations. A fake HTTP transport exercises the production OpenAI theme tool/schema path. Coverage includes agreement, shortlist containment, candidate/tool-call effort, evidence IDs, missing primary data, ETF methods, multiple companies, stopped research, no forced purchase, no invented previews/probabilities/execution, supported sizing, decisive missing inputs and multiple-account fund exposure. The complete browser journey covers clarification, agreement, evidence/cases/comparison rendering and cleared agreement after editing.

No live model or market-data requests were made. The existing Starlette/httpx deprecation warning did not fail checks.

## Standards

Parallel native Codex review found no hard documented-standard breach. A duplicated candidate eligibility predicate was extracted into `isThemeCandidate`. An additional correctness finding identified selected-row-only ETF sizing; the shared helper now aggregates the same fund listing across accounts, with a worked API regression: a portfolio worth USD 10,000 holding USD 1,000 of the same fund in each of two accounts needs USD 1,000?2,000 of additions for a judged total exposure range of 30%?40%.

Final review recheck: no remaining material standards findings.

## Spec

Review identified that every purchase was downgraded and an amountless purchase alternative replaced an otherwise valid no-action result. Both were corrected: justified additions reuse the existing Python sizing helper; no-action conclusions retain independently qualified conditional alternatives. The reviewer rechecked agreement, bounds, mechanism evidence, shared calculations and sizing eligibility and found no remaining spec blocker.

Summary: Standards one possible smell and one correctness finding resolved; Spec one conclusion/alternative finding and one sizing gap resolved. No unresolved material finding on either axis.

## Separate completed-answer assessment

[theme-answer-samples.json](theme-answer-samples.json) records compact excerpts from six completed application responses, including recommendations, mechanism tests, dated source excerpts, conditional case outcomes, common comparison basis and checked amount previews. These were inspected separately from test assertions.

| Controlled scenario | Final direction | Assessment |
| --- | --- | --- |
| Agreed mechanism challenged | No action, no amount | Reported revenue does not prove durable automation margin gains; fund/cash alternatives remain serious |
| Same case with a conditional purchase alternative | No action, no amount | Conditional alternative remains qualified without forcing purchase |
| Unconfirmed agreement | Clarify inputs | No candidate research or model reasoning claimed |
| Missing primary evidence | Clarify inputs, unknown company cases | Absent facts are not substituted with judgments |
| Existing company-cap breach | Review required, no amount | Conviction cannot waive the supplied cap |
| Supported primary demand/context/exposure fixture | Conditional add, USD 75?160 | Hypothetical amount endpoints pass checks; explanation acknowledges downside/base cases weaker than cash and the dependence on upside resilience |

The supported-add fixture is a plausible risk-tolerant conditional choice, not a demonstration that it dominates cash or that the exposure range is optimal. Qualitative automation order evidence does not establish durable margins; this remains explicit in the answer. Fixture excerpts are invented controlled company evidence for testing, not market facts. Reviewing scripted completed answers does not establish live model research or ranking quality; that remains a separate future evaluation with real completed answers.
