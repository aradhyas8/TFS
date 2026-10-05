# Ticket 07 completion and review report

Reviewed October 4, 2026 (America/New_York). Implementation baseline: `265bc6b2c31c31ee8fe0ce91efa04f7b2fb228e1`. Work stays on the existing current branch, `ticket-06-new-cash-allocation-with-bounded-discovery`, as required by the implement workflow.

## Delivered behavior

Ticket 07 adds a portfolio-review request type to the existing Next.js question form and shared FastAPI decision pipeline. It reuses financial refresh, primary company research, sector-appropriate company cases, fund/cash/no-action comparison, baseline checks and proposed-change arithmetic. Backend configuration and secrets remain backend-owned.

The user selects **Review holdings and rebalance**, submits a question and can supply dated prior company theses, loss/withdrawal context, personal limits, a baseline and hypothetical changes. The response shows current whole-portfolio valuation, thesis assessments, supporting original links and dates, conditional cases, guardrails, proposed outcomes and the shared recommendation.

Current primary evidence is bound for held US companies. Research is deduplicated by issuer while compatible same-listing holdings in separate accounts receive their own calculated retained cases. Changed/unchanged status requires a dated supplied prior thesis; absence of a prior thesis leaves change status unknown while still allowing current-evidence review. Current facts that cannot support cases produce unknown results and conditional clarification. Past ownership cannot waive a cap, and price declines do not establish failure or justify averaging down.

Target-relative discussion uses only supplied baseline categories. Empty and all-null baselines authorize no targets. Partial baselines retain unknown categories and cannot justify an invented complete mix. These checks apply to thesis, scenario, sizing and recommendation prose. Baseline-free exposure review completes normally.

Conditional holding actions include add, hold, reduce, exit and no action. Explicit user previews show post-change cash and weights and reuse configured company-cap and active-budget enforcement. Existing above-cap positions display forward reduction paths. Review preview calls cannot invent or replace user-submitted trades. Optional analyst sizing uses an explained issuer exposure range, separate hypothetical deterministic previews and the same guardrails; it does not claim an order or transaction.

## Verified arithmetic

- Multi-account fixture: portfolio total **3,600 CAD**, holdings **2,300 CAD**, cash **1,300 CAD**, direct issuer weight **63.89%**.
- With a supplied **50% company cap**, the existing direct position has a minimum dated reduction-to-retained-cash path of **500 CAD**, before unknown effects.
- The explicit funded reduction preview leaves **1,950 CAD cash** and **45.833333% issuer weight**, within the supplied cap, and shows the supplied baseline comparison.
- The supported sizing fixture refreshes the quote to **120 USD**. Ten shares plus **500 USD cash** give a **1,700 USD** portfolio. A justified **40%?50% issuer exposure range** implies an approximate **350?520 USD reduction**. Both endpoints pass deterministic cap/budget checks.
- Duplicate holdings of the same US listing retain separate account shares and use shared per-share company cases. The controlled no-action base subtotal is **3,384.615312 CAD**, including the explicit changing cash-rate path.

These are fixture calculations, not advice or forecasts for a real investor.

## Automated verification

| Check | Result |
| --- | --- |
| Full backend suite | 255 passed |
| Dedicated portfolio-review request/result cases | 32 passed within the full suite |
| Full browser suite | 25 passed, including two new portfolio-review journeys |
| Backend strict mypy | Passed, 15 source files |
| Ruff | Passed |
| Frontend TypeScript | Passed |
| Next.js production build | Passed |
| Git whitespace/diff check | Passed |

Tests use the approved public API and whole-browser seams. Fake model tool requests run real backend calculations and validation. Coverage includes changed/unchanged theses, falling prices, absent prior theses, multiple companies and accounts, cash-only review, supplied/missing/partial baselines, source refresh, supported sizing, missing risk/cap/budget/funding/effects, cap breaches, unsupported outputs and same-listing case reuse. A fake HTTP transport exercises the production OpenAI SDK review tools and backend-only amount schema. The configured research-reference path is also tested.

No live OpenAI or market-data requests were made. The existing Starlette/httpx deprecation warning appeared; it did not fail verification.

## Standards

Two native Codex review passes found no hard documented-standard breach. Three material findings were resolved:

1. Company cost/tax inputs were inaccessible in review mode. Review comparisons now initialize held-US-company alternatives, and a complete browser journey supplies company effects and reaches supported sizing.
2. The unchanged-thesis helper ignored its selected research fixture. It now binds that fixture and asserts the actual returned excerpt.
3. The sizing rationale bypassed baseline validation. It now receives the same validation, with a request/result regression.

Final targeted recheck: no remaining material standards-axis findings.

## Spec

Three material spec findings were resolved:

1. Same-listing holdings in another account lost supported retained company outcomes. Per-position cases now reuse the bound issuer evidence and judgments without duplicate research.
2. The unchanged-thesis evidence fixture did not reach the completed response. The fixture and bound-evidence assertion now agree.
3. An empty baseline allowed an invented target mix. Empty/all-null baselines now behave as absent; partial baselines cannot authorize unspecified category targets or a complete mix.

A further implementation check found that production portfolio reviews did not load `RESEARCH_REFERENCE_FILE`; the shared API configuration path was extended and verified.

Final targeted recheck: no remaining spec-axis blocker. Summary: Standards 3 resolved findings; Spec 3 resolved findings; no unresolved finding on either axis.

## Separate completed-answer assessment

Seven completed application-response excerpts are recorded in [portfolio-review-answer-samples.json](portfolio-review-answer-samples.json). This was a separate inspection of final recommendations, evidence excerpts/dates, thesis assessments, common comparison capital and proposed outcomes, beyond assertions that fixed answers render.

| Scenario | Observed final direction | Assessment |
| --- | --- | --- |
| Weaker current demand, no baseline | Conditional reduce, no amount | Current evidence challenges prior resilience; no target invented |
| Resilient current evidence with falling price | Conditional hold, no amount | Decline does not determine thesis validity |
| No dated prior thesis | Conditional reduce, unknown change status | Current thesis review completes without fabricating history |
| Missing reported revenue | Clarify missing inputs, no amount | Company case stays unknown rather than becoming zero |
| Supported reduction inputs | Conditional reduce, 350?520 USD | Range corresponds to checked issuer exposure and both outcomes |
| Missing loss/withdrawal context | Conditional reduce, no amount | Missing context is explicit and prevents sizing |
| Supplied baseline and reduction preview | Conditional reduce, no recommendation amount | Actual post-change weights and supplied baseline remain visible |

The inspection caught generic fixture wording that made a baseline sound mandatory and implied supplied risk context in a missing-context case. Controlled responses were corrected to preserve baseline-free review and explicit missing inputs.

The excerpts use fictional controlled primary-source fixtures and scripted model judgments. This assessment supports consistency of these completed outputs with their supplied evidence and arithmetic. It does not establish live-model research quality, general investment judgment, source completeness or production answer quality across arbitrary questions.

## Acceptance and practical limits

All four ticket acceptance criteria are implemented and verified: a completed evidence-backed whole-portfolio journey; independent material thesis challenge; supplied-target-only baseline comparison with baseline-free review; and preservation of ticket 05 as the direct blocker. The review does not require a new-cash scan, theme discovery, Canadian support, ETF look-through or saving.

The runtime research adapter still consumes independently reviewed dated reference excerpts. It does not claim live filing retrieval. Unsupported Canadian or incompatible listing-specific retained cases remain unknown. ETF indirect overlap remains explicit; the supplied direct-only/include-known-indirect policy is preserved.

Supported sizing needs usable dated whole-portfolio values, verified identities, positive quotes and FX, numeric caps/budget, loss/withdrawal context, current company facts/cases, same-account/currency funding and known dated cost/tax effects. Nonzero cost/tax funding impacts are not modeled by this preview: those effects appear in comparison while the adjustment amount is withheld. A selected account cannot fund an issuer-wide exit when other accounts retain shares. Amounts and fractional-share previews are approximate and hypothetical.

For setup, contracts, formulas and usage, see [portfolio-reunderwriting.md](portfolio-reunderwriting.md).
