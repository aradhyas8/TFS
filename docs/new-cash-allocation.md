# New cash in the shared decision pipeline

Ticket 06 extends `POST /api/analyze` and the existing question form. Enable **Analyze new cash**, supply an amount additional to the snapshot, select an account cash balance (which supplies its currency), confirm both, and describe loss tolerance and withdrawal needs. Leave unknown personal limits blank; the answer then remains conditional. The structured request adds:

```json
"new_cash": {
  "amount": "6000",
  "cash_position_id": "cash",
  "confirmed": true,
  "risk_context": "Long horizon; equity losses are tolerable and no near-term withdrawal is planned."
}
```

The explicit fields are authoritative. A recognizable new-cash question alone triggers the same workflow with unresolved context; numbers in prose are never silently confirmed. Explicit stock/comparison/proposed-change modes are separate contexts in the same pipeline; they cannot be combined with `new_cash`. No new endpoint, persistence prerequisite, order service or allocation optimizer is added.

## Fresh bounded evidence

Every request calls the backend discovery provider once, then refreshes financial identities, quotes and FX through the existing source boundary. `scan_opportunities` exposes that request-local screen, its capture time, evidence dates, coverage limits and comparison inputs. There is no cached decision or reliance on old company research alone.

Without configured discovery records, the dated screen covers eligible supplied holdings and openly reports that additional opportunity coverage is unavailable. This is a newly performed lightweight screen, **not live market-wide discovery**. Missing broad ETF, source evidence or context yields conditional direction. For a wider bounded universe, configure backend-only `DISCOVERY_REFERENCE_FILE` with a `DiscoveryScan` JSON object:

- `scanned_at`: timezone-aware original capture time (the backend separately stamps the current screen request).
- `as_of`: decision basis date; a different date makes external candidates unavailable.
- `source`: source and coverage description.
- `candidates`: at most eight records, each containing a zero-share `position`, `as_of`, `source`, optional `source_url`, and a screening `signal`. Stocks require US listings in USD; fund candidates require diversified classification. Positions must reference a supplied account. Existing identifiers must match submitted listings/issuers.
- `fund_facts`: optional dated fund exposure, cost and income records using the shared `FundFacts` contract.
- `issues`: source limitations or missing coverage.

The file is re-read each request. These records do not establish verified identity, usable price or current primary evidence by themselves: `FINANCIAL_REFERENCE_FILE` and `RESEARCH_REFERENCE_FILE` remain the existing independent authorities. Old, missing or contradictory facts stay unknown. No arbitrary model URLs, model-supplied screening facts, new subscription or broad crawler is introduced.

After reviewing the scan, the model can stop without stock research or request `research_candidate` for one or two distinct scanned stocks, with a reason each could change the choice. The backend rejects a third candidate, duplicate research, unscanned targets and research after comparison. Each research request exposes original SEC and issuer material together; `calculate_company_cases` uses the same company calculations as ticket 05. All researched serious candidates receive cases before the shared comparison can run. Two stock candidates plus broad ETF, cash and no action fit the five-alternative bound.

## Amounts and guardrails

The comparison starts with only the new contribution, converted using dated FX. A separate `__new_cash__` scope prevents existing cash from being counted again. Cash and no action both retain that contribution; their explanations distinguish the deliberate cash choice from deferring action. All serious options share the reporting currency and decision date. Missing costs, taxes, fund facts and indirect overlap remain qualified.

`size_allocation` accepts a destination, justified minimum/maximum **post-contribution exposure weights**, and qualitative rationale. For stocks the weight is whole-company direct exposure across all accounts/listings; for funds it is the selected fund's weight. Python calculates:

```
post total = dated portfolio total + confirmed new cash ? dated FX
additional local amount = (judged weight ? post total ? existing reporting exposure) / dated FX
```

Amounts are rounded down to cents, converted to hypothetical fractional shares using the bound quote, and both endpoints are checked by the existing `preview_changes`. Only new cash may fund the range; existing cash is retained. The denominator includes the contribution exactly once. Company caps and the active budget remain explicit user settings. Deliberate cash tilts still require their supplied baseline.

The answer gets an `amount` object `{minimum, maximum, currency, position_id}` only when context, whole-portfolio valuation, selected identity/price/FX, comparison, applicable numeric limits and post-allocation checks support it. The model response must always have `amount: null`; the backend alone inserts calculated amounts. Unknown ETF overlap does not block a direct-only policy; an indirect-inclusive or unset policy remains unresolved where ETF exposure could matter. A breached/unknown endpoint, missing critical context or missing primary stock evidence replaces an add with completed conditional guidance. There is no conviction exception or claim of optimal sizing.

The response adds `allocation` with confirmed context, scan, bounded research, company cases, judged range, checked previews, missing inputs and qualifications. The existing recommendation, portfolio and comparison remain the shared contract. The frontend shows dated evidence, assumptions, downside, alternatives, uncertainty and facts that could change the view. No order or trade is confirmed by this analysis.

## Controlled answer review

Automated tests use fake model/data providers at the FastAPI and browser seams; they do not contact live OpenAI or financial services. The completed controlled answers were separately inspected for the following reasoning properties:

| Fixture | Completed answer assessment |
| --- | --- |
| USD 10,000 portfolio: 8,000 fund, 2,000 existing cash, plus confirmed 6,000 | Conditional diversified-fund addition retains a reserve. Judged 55?65% fund exposure becomes 800?2,400 new USD, checked against total 16,000. This range is an explained fixture judgment, not demonstrated optimal sizing. Unknown fund costs and indirect overlap remain disclosed. |
| Zero, one or two candidate companies with operating cases | No-action answer says candidates do not clearly improve the tradeoff. No forced stock purchase or amount; all researched candidates appear alongside fund/cash/no action. |
| Researched stock judged at 5?10% | Amount 800?1,600 USD; maximum company and active weights both 10%. When either supplied limit is 8%, the maximum preview is blocked and the completed answer requests clarification instead of granting a waiver. |
| Unconfirmed cash, missing risk/limits, unusable identity/price/FX | Completed conditional answer names unresolved inputs and retains cash pending clarification. No numeric recommendation. |

This review establishes coherence of the controlled completed answers and their safety qualifications. Scripted explanations do not establish production AI research or ranking quality; no live-model answer-quality claim is made. The frontend journeys verify the confirmed and unconfirmed 6,000 example through real orchestration and deterministic results.
