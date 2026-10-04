# 04: ETF/cash five-year comparison

**What to build:** Compare the diversified ETF, cash or short-government-bill, and no-action alternatives needed for an investment decision using transparent conditional five-year cases.

**Blocked by:** 02: Quotes, FX, and source freshness.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Use the backend OpenAI integration for explained scenario-driver judgments and the existing Python tools for all scenario arithmetic. Return a validated structured comparison to the same Next.js result display; do not add another analysis pipeline.
- Extend the shared alternatives, scenario-calculation, and answer stages. Implement only alternatives selected for the investment question; do not build a general ETF-analysis platform.
- Show conditional downside, base, and upside cases on a consistent reporting-currency and as-of basis.
- Use ETF portfolio exposure, known fund costs and income where available, and currency where relevant. Keep missing costs or income unknown; do not apply a company exit-value method to a fund.
- Use simple explicit cash/short-bill rate and reinvestment paths; today's short-term yield is not assumed to persist for five years.
- Describe no action relative to the actual current holdings/cash rather than treating it as automatically identical to buying cash.
- Separate judgments about future exposure, income, rates, reinvestment, and FX from deterministic scenario arithmetic. Disclose pivotal assumptions, downside, and uncertainty without probabilities or a default weighted expected value.
- Include known transaction costs and tax effects only when supplied or supported; leave unknown consequences unquantified. Do not label cases as real purchasing-power outcomes without sourced, modeled inflation.
- Five years is a comparison horizon, not a mandatory exit date. The 20+ year real-wealth objective and historical return aspiration are not forecasts or required hurdles.

## Tests and verification

- Run the existing input/result journey with fixed ETF exposure/cost/income, dated FX, cash/short-bill reinvestment paths, and no-action fixtures.
- Verify deterministic case arithmetic, consistent dates/currencies, no dividend double counting, and explicit treatment of missing costs/income/tax information.
- Check the answer displays three conditional cases, assumptions, downside, and uncertainty without probabilities, forced buying, or an invented return hurdle.
- Use fake model responses/tool requests and fake data to exercise FastAPI orchestration, actual Python scenario calculations, structured validation, and frontend comparison display. Fixed answers test rendering/contract only; review completed application comparisons separately for reasoning quality. No live OpenAI or market-data calls in automated tests.

## Acceptance criteria

- [ ] The answer compares the selected ETF, cash/short-bill, and no-action alternatives with conditional downside/base/upside cases.
- [ ] Available costs, income, currency, and reinvestment assumptions are inspectable, and unavailable inputs are identified.
- [ ] Only the comparison needed by the investment workflow is implemented.
- [ ] Future stock/allocation/review/theme tickets can reuse these alternatives through the shared pipeline.

## Non-goals

- No portfolio optimization, fund-screening infrastructure, Monte Carlo, macro forecasting, broad ETF analytics, or general fund-analysis platform.
- No full sponsor-holdings look-through, historical performance engine, or new standalone workflow architecture.
