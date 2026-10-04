# 06: New-cash allocation with bounded discovery

**What to build:** Answer “I have this portfolio and $6,000. What should I do?” with a focused fresh opportunity scan, serious alternatives, and an approximate allocation only when justified.

**Blocked by:** 05: US stock analysis in portfolio context.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Accept the new-cash question in the existing chat input and extend the shared FastAPI/OpenAI tool orchestration with bounded discovery/research tools. Keep amount arithmetic and post-allocation checks in Python and return the same validated structured recommendation contract.
- Extend the same request/context, evidence, alternatives, scenario, guardrail, and answer stages established by tickets 01–05; do not create an allocation-specific architecture.
- Confirm the new-cash amount, intended account, and only decision-critical missing context. Use the dated whole-portfolio snapshot and applicable explicit settings.
- Perform a lightweight fresh scan on every new-cash request rather than relying only on old research.
- Limit deep research to no more than one or two candidates that could change the decision, and stop when no candidate warrants further work.
- Compare serious candidates with a broad-market ETF, cash, and no action using the shared five-year scenarios and common reporting-currency/as-of basis.
- Use basic direct exposure/concentration for the first working allocation recommendation. ETF look-through is not a blocker; missing indirect coverage stays partial or unknown, never zero. Respect any supplied indirect-cap policy and qualify sizing when a decisive fact remains unresolved.
- AI may propose a justified exposure range; deterministic calculations convert it to amounts and check post-allocation company-cap and active-budget weights. Do not claim objectively optimal sizing or waive configured limits.
- Give conditional direction or no allocation recommendation when required snapshot, identity, usable price/FX, applicable numeric cap/budget, or minimal context is unavailable. Incomplete ETF data alone does not categorically prohibit all sizing.
- Return the preferred action and reason, one or two alternatives, downside, assumptions, uncertainty, dated evidence, and facts that could change the view. Stop at analysis; order placement remains with the user.

## Tests and verification

- Exercise the shared journey with controlled portfolio/new-cash/candidate/ETF/cash fixtures, including the $6,000 example, using fake model responses/tool requests and fake data providers through the actual FastAPI orchestration, Python calculations, structured validation, and Next.js display.
- Verify post-allocation cap and budget weights, denominator treatment of new cash, required-input gating, and unknown indirect overlap.
- Script fake model/data scenarios where zero, one, or two candidates merit deep research and verify the fresh scan and research stop boundary through the application. Review completed application answers separately for reasoning quality.
- Check no forced stock buy, cap waiver, invented probability, automatic order, unconfirmed trade, or confident amount without required inputs.

## Acceptance criteria

- [ ] The core portfolio-plus-new-cash question produces a completed recommendation in the same application request, displayed in the frontend.
- [ ] A fresh bounded scan occurs each request, and deep research stops at at most two decision-changing candidates.
- [ ] Approximate amounts are justified and deterministically checked, or replaced with explicit conditional direction.
- [ ] Tickets 09–11 are not prerequisites; one shared decision pipeline powers the answer.

## Non-goals

- No unbounded market crawl, deep research on every result, separate discovery platform, automatic orders, saved-decision prerequisite, or ETF-look-through prerequisite.
