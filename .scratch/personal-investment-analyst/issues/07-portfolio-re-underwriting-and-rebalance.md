# 07: Portfolio re-underwriting and rebalance

**What to build:** Review the whole portfolio and re-underwrite material thesis changes before proposing holding, adding, reducing, exiting, or rebalancing actions.

**Blocked by:** 05: US stock analysis in portfolio context.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Submit review/rebalance questions through the existing Next.js chat and reuse the shared FastAPI/OpenAI research and comparison orchestration. Run financial arithmetic and proposed-outcome checks in Python before validating and displaying the structured response.
- Extend the shared pipeline and existing portfolio/stock/alternative calculations, with a portfolio-review request type rather than a separate architecture.
- Use all supplied accounts, holdings, cash, refreshed dated marks, and relevant evidence. Keep research focused on current holdings and alternatives unless another candidate could change the decision.
- Re-underwrite material thesis changes against current evidence; prior ownership or research must not protect a weak thesis.
- Treat a falling price as context, not proof of thesis failure or a reason to average down.
- Compare target-relative changes only with a user-supplied baseline. Without a baseline, review current exposure and justified actions without inventing rebalance targets.
- Apply configured company-cap and active-budget checks to proposed outcomes; explain above-cap positions and forward reduction paths. Unknown settings and indirect coverage remain explicit.
- Use common reporting-currency/date comparisons and instrument-appropriate conditional five-year cases for serious options. Provide supported amounts only when all applicable sizing inputs are usable.
- Include known costs/tax effects only when available and return the shared recommendation contract, including downside, assumptions, evidence dates, alternatives, and what could change the view.
- Reuse Canadian evidence and ETF-overlap extensions when subsequently available; do not make them prerequisites for the core US/portfolio journey.

## Tests and verification

- Exercise the shared review with multi-account holdings, cash, changed and unchanged theses, falling-price cases, and supplied/missing baselines.
- Check proposed weights, guardrail enforcement, above-cap reduction paths, unknown indirect coverage, and no invented target mix.
- Exercise the complete application portfolio question-to-recommendation journey using fake model tool requests and fake data, with real Python calculations and backend validation. Review completed application answers separately for reasoning quality; fixed answers alone test rendering/contract.
- Verify unsupported data does not become zero, confident sizing, a presumed tax consequence, or a claimed trade.

## Acceptance criteria

- [ ] A whole-portfolio review returns a completed evidence-backed recommendation through the existing pipeline.
- [ ] Material thesis changes are challenged independently of past ownership and price movement.
- [ ] Baseline-relative rebalancing occurs only with supplied targets; baseline-free exposure review still works.
- [ ] Direct blocker remains ticket 05; new-cash, ETF-look-through, Canadian-support, and saving tickets do not gate this workflow.

## Non-goals

- No portfolio optimizer, transaction-history/performance engine, automatic rebalancing, invented targets, background monitoring, or separate portfolio-review architecture.
