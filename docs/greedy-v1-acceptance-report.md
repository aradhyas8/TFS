# Greedy V1 acceptance: real portfolio

**Result: ACCEPTED — all seven workflows / nine cases completed inside 170 seconds per case.**

Tested October 11, 2026 on branch `ticket-08-real-portfolio-acceptance-and-hardening`, using the saved nine-stock portfolio: AVGO, F, GOOG, HDB, IBN, INFY, NBIS, SHOP and CM. Benchmark: XEQT.TO. Portfolio rules and target mix were unset. Every recorded recommendation amount remained null; conditional completed results count under Ticket 08.

Provider: `chatgpt_plan`; model: `gpt-6-luna`. Configured reasoning effort remained `max`. The pipeline caps plan synthesis at `high` (`medium` for Rebalance); tool turns use `medium`, except Rebalance judgment/comparison turns use `none`. No timeout was increased.

These are retained passing cases from targeted live runs after isolated fixes, rather than one uninterrupted final run. Successful cases were not researched again. The final Theme fix is confined to that workflow; its live rerun completed both agreed stocks. Private artifacts identify each source run below.

## Timings and model usage

Durations include UI interaction and memo rendering. Input tokens count repeated streamed request context. Reasoning tokens are included within output tokens.

| Case | Status | Seconds | Turns | Input tokens | Output tokens | Reasoning tokens | Corrections |
|---|---|---:|---:|---:|---:|---:|---|
| 1 Portfolio Review | PASS | 24.5 | 3 | 24361 | 932 | 212 | Final answer rejected |
| 2 Rebalance | PASS | 155.2 | 12 | 384027 | 6963 | 0 | None |
| 3a Owned stock (US holding: AVGO) | PASS | 70.5 | 6 | 200075 | 3111 | 347 | None |
| 3b Owned stock (Foreign filer: HDB) | PASS | 74.6 | 7 | 202114 | 3438 | 299 | reprompting in loop |
| 3c Owned stock (TSX: CM) | PASS | 68.6 | 6 | 156372 | 3180 | 488 | None |
| 4 Unowned candidate (US: NVDA) | PASS | 75.4 | 6 | 168632 | 3244 | 455 | None |
| 5 New Cash allocation | PASS | 42.5 | 4 | 61581 | 1849 | 245 | None |
| 6 Theme discovery | PASS | 143.5 | 9 | 459284 | 6395 | 272 | None |
| 7 Save, reopen, re-run, and confirm | PASS | 30.3 | 4 | 29458 | 1150 | 226 | None |

## Observed behavior

- Review rendered current exposure, price/source qualifications and a completed memo. One invalid final-prose answer was rejected and corrected.
- Rebalance assessed all nine holdings and calculated nine stock results, followed by all eleven selected comparison alternatives. No holding was dropped to meet the deadline. The final result was review-only with no authorized amount.
- Owned stock analysis completed for US AVGO, foreign filer HDB and TSX CM with calculated cases. HDB needed one reminder to complete required tools before its conditional answer.
- Unowned NVDA selected the US XNAS listing from the dual-listing clarification and completed a conditional candidate memo.
- New Cash submitted an amount and loss tolerance, scanned opportunities and completed comparison. Missing personal rules left the answer conditional without authorizing an allocation.
- Theme used only the agreed AVGO/GOOG shortlist, passed the agreement gate, researched both, calculated both cases, tested each mechanism and completed comparison and its conditional memo in nine model turns.
- Save/reopen preserved a completed review. The rail reopened it, recorded an explicit no-action confirmation, and re-ran against the current portfolio with unchanged position identities and shares.

## Failures diagnosed and fixes

| Failure before hardening | Cause and correction |
|---|---|
| CM synthesis nearly exhausted the workflow deadline | Configured maximum reasoning consumed most of the budget. Cap plan synthesis effort while retaining every calculation and validator. |
| Nine-holding Rebalance exceeded the deadline | Repeated annual paths and comparison cases dominated streamed output. Expand constant paths locally before validation and reuse already calculated company paths through exact alternative keys; retain every holding and alternative. |
| Rebalance comparison or prose returned HTTP 502 | Missing/mismatched alternatives, same-currency FX errors and invented target-mix wording violated existing checks. Bind all selected alternative keys in the model schema and clarify FX, sector and absent-rule instructions. |
| Review prose returned HTTP 502 | Probability, invested-capital or account-room wording tripped unchanged prose checks. Correct prompts and allow one validated final-answer correction; repeated invalid answers still fail closed. |
| NVDA clarification or final prose blocked acceptance | The runner now selects NVDA / XNAS. The prompt forbids claiming unavailable Q&A/transcripts and requires available evidence citations. |
| Theme returned HTTP 502 | Earnings valuation received FCF-style conversion/reinvestment assumptions. State the method invariants and return one rejected candidate calculation for correction within the unchanged agreed tool budget. The successful rerun used valid inputs without a correction. |
| Cross-workflow runner state selected the wrong form | Scope composer submission to its form and reset the page for Theme and saved-decision journeys. This changed only the test runner. |

Financial arithmetic, canonical schemas, prose checks and guardrail validators were not loosened. Compact paths expand to the same five annual values; comparison references resolve to existing calculated stock cases and explicit non-stock drivers before the original validation. Fake-provider API tests cover exact equivalence, invalid scalar rejection, missing drivers, correction limits and Theme budget enforcement.

## Verification

- Backend: `pytest backend/tests -q` — 550 passed.
- Ruff and mypy — passed.
- Runner: `node --test --test-isolation=none frontend/live_acceptance.test.cjs` — 4 passed.
- Frontend typecheck and production build — passed.
- Browser suite: `npm test` — 65 passed. The invalid-output journey was strengthened to require HTTP 502 and rerun separately after updating its fake model for final-answer correction.

## Evidence and limits

- Private local evidence: `.scratch/live-1791693496818/report.json` (passing entries retained).
- Private local evidence: `.scratch/live-1791694232522/report.json` (passing entries retained).
- Private local evidence: `.scratch/live-1791694682943/report.json` (passing entries retained).
- Private local evidence: `.scratch/live-1791695155335/report.json` (passing entries retained).

Raw requests, response bodies, backend logs, screenshots and traces stay local and are excluded from this report and the PR. No portfolio valuations, holding sizes, account balances, proposed cash amounts or other sensitive dollar figures are reproduced.

Rebalance had 14.8 seconds of deadline headroom; model/network latency can vary, so these measurements demonstrate this acceptance run rather than a latency guarantee. Missing facts and unset personal rules remain conditional. No live trade was executed. An extra full live rerun and research of additional stocks were skipped at the user’s request for efficient testing.
