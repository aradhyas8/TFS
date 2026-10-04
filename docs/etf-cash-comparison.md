# Conditional five-year alternatives

Ticket 04 extends `POST /api/analyze` and the existing decision pipeline. No comparison is performed unless the request selects it. The `comparison` object contains:

- `scope_position_ids`: actual snapshot positions whose combined reporting value is the common comparison capital; at most twenty positions.
- `alternatives`: at most three uniquely identified selections. `etf` references a supplied diversified ETF; `cash` and `short_bill` reference a cash row to establish currency. A zero-share ETF row can represent a candidate. `no_action` has no destination and retains every actual scoped holding/cash balance.
- `fund_facts`: optional supplied fund exposure, annual cost and income yield fractions, with position ID, source/link and as-of date. A missing or mismatched-date fact remains unknown.
- `effects`: optional supplied starting transaction cost and terminal tax in reporting currency, with alternative ID, source and date. Effects are explicit cash amounts, not presumed personal tax calculations. No action cannot incur a transaction cost. Missing or mismatched-date effects are unquantified.

The backend first resolves and values the dated snapshot using ticket 02. The existing OpenAI provider then requests `calculate_comparison`: one downside/base/upside judgment per selected alternative, with exactly the destination or retained position drivers. Each driver supplies five annual returns or cash rates, an income multiplier path where applicable, reinvestment choice, and future FX multipliers relative to dated starting FX. Qualitative assumptions, downside and uncertainty accompany each case. Extra facts, missing components, double-counted total-return income, nonidentity same-currency FX, and invalid financial numbers fail validation. A final recommendation cannot skip calculation.

These are scenario judgments, not forecasts or probabilities. No weighted expected value is calculated. Model prose cannot introduce numerical claims, trade directions or allocation amounts. Existing deterministic cap/budget enforcement still controls the final answer. A comparison capital basis does not establish justified sizing.

## Arithmetic and unknowns

Let `C` be opening invested local capital and `r` the judged annual ETF exposure return. A price-only path earns income `D = C × supplied_yield × judged_income_multiplier`, then computes `C × (1 + r)`. A total-return path already includes reinvested income and adds no separate dividend. With reinvestment, add `D` to capital. A gross path multiplies the resulting capital by `1 − supplied_annual_cost`; a net-of-fund-cost path deducts nothing again. Fund costs are a simple annual drag on modeled invested capital, not detailed daily fee accrual. Current yield is only an income anchor; future income multipliers remain judgments.

Cash/short-bill interest is `C × annual_rate`. Reinvested interest compounds each explicit year's rate. Distributed ETF/cash income is converted at each year's initial dated FX times its judged FX multiplier, then held as idle reporting cash. Terminal invested local capital converts at the final year's FX multiplier. Same-currency multipliers must be one. Short bills use a simple annual hold-to-maturity/reinvestment path; intra-year market pricing, early sales and credit losses are not modeled.

Known transaction costs reduce initial capital and known terminal tax is deducted at the end. Missing expense, income or tax effects never become explicit zero facts. `known_terminal_value` is a conditional subtotal before unknown effects; `terminal_value` stays null until all numeric inputs/effects are supplied or covered by an explicit net/total-return basis. If starting capital, dated quote or FX is unusable, the subtotal is also unknown. Retained stocks remain unquantified; this tool does not apply ETF exposure math as company exit valuation. Manual/cached provenance continues to qualify otherwise specified cases and cannot authorize confident sizing.

All alternatives share the same as-of date and reporting currency. Output includes supplied facts, model driver judgments, dated initial FX, component calculations and missing-input qualifications. No inflation model is supplied, so values are nominal, never real purchasing-power outcomes. Five years is a comparison horizon, not a required exit. The twenty-plus-year real-wealth objective and historical return aspiration do not become forecasts or hurdles.

## Completed fixture review

The application/browser fixture uses a CAD 1,950 scope: CAD 1,300 of a USD ETF plus CAD 650 cash, valued as of 2026-09-30 with supplied USD/CAD 1.3. These are fictional inputs. The base ETF path has flat prices, a known 2% income yield reinvested annually, explicit zero fund cost and flat FX. The base cash path uses 4%, 3%, 2%, 1%, 0% annual reinvestment rates. The downside/upside exposure and rate paths differ and remain inspectable judgments.

| Base alternative | Known terminal subtotal (CAD) | Interpretation |
| --- | ---: | --- |
| ETF | 2,152.95756624 | Income compounds once; no company exit-value method |
| Cash | 2,151.922968 | Falling reinvestment rates; current yield does not persist |
| No action | 2,152.61270016 | Retains the actual ETF/cash mix, rather than replacing it with cash |

The completed browser answer was separately inspected for explanatory consistency: it identifies equity/currency downside, falling cash reinvestment rates and retained-mix risk; qualifies provisional evidence; and leaves transaction/tax consequences unknown rather than ranking nearly indistinguishable known subtotals as a justified purchase. It returns conditional no action with clarification as an alternative, without an allocation amount, forced buy, probability or return hurdle. All fully specified terminal values remain unknown in this fixture.

This is a review of a completed application comparison with a scripted model, not evidence that a live model will choose well. Automated tests exercise FastAPI orchestration, real Python arithmetic, the production SDK with a fake HTTP transport, structured rejection and the actual frontend input/result display. No live model/data services are called. Production reasoning quality still requires separate review of completed answers with supported real evidence.

## Standards

Review against starting commit `de12ea4f956787bf7ef641f057cb233b591607c1` found one substantive UI issue: obsolete comparison references survived deletion or replacement of portfolio rows. The fix removes deleted or reclassified destinations, obsolete scope IDs and fund facts; clears effects when reporting currency changes; and prompts for empty selections. The browser regression covers deletion and CSV replacement followed by successful requests. Re-review found no remaining substantive issue or documented standards breach.

## Spec

The independent spec review found no missing requirement, incorrect implementation or unrelated scope expansion. It checked income/cost double counting, unknown inputs, actual retained no-action holdings, common capital/date/currency basis, the shared model/tool pipeline, and displayed conditional assumptions/downside/uncertainty. This was static code review without live services.

Review totals: Standards one finding fixed, zero remaining; Spec zero findings.
