# 09: ETF indirect overlap/look-through

**What to build:** Improve existing portfolio reviews and recommendations with dated ETF company overlap and the user's chosen treatment of known indirect exposure.

**Blocked by:** 03: Simple portfolio guardrails.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Add sponsor-holdings access to the existing backend data tools and direct/indirect aggregation to deterministic Python calculations. Surface overlap and dates through the same structured response and Next.js display, reusing the Ticket 01 runtime.
- Extend the existing evidence, exposure, guardrail, and result stages; preserve the functioning core workflows.
- Use dated full sponsor holdings where available and underlying-fund holdings where possible. Preserve holding identifiers, weights, source dates, and coverage limits needed for company aggregation.
- Aggregate direct and available indirect company exposure across the dated whole portfolio without double counting fund and underlying positions.
- Label incomplete look-through partial or unknown rather than zero. A top-ten list or periodic SEC N-PORT data is not a complete current holdings file.
- Apply the user's explicit choice about counting known indirect exposure toward a hard company cap; do not silently alter that policy.
- Reflect known overlap in fit, concentration discussion, and sizing. Incomplete coverage qualifies the conclusion but does not by itself block all numeric sizing.
- Use only permitted available source material; no new subscriptions or broad holdings database.
- Keep this as a later enhancement: tickets 05–08 remain independently usable, and their blocker relationships do not change.

## Tests and verification

- Use fake dated sponsor-holdings fixtures for full, partial, missing, stale, and nested-fund coverage.
- Verify direct/indirect aggregation and no double counting through the existing exposure review.
- Exercise the explicit indirect-cap policy in both modes and ensure unknown coverage never becomes zero.
- Exercise the existing application exposure/guardrail journey with fake model and fake sponsor data, real Python overlap calculations, and structured frontend display, including a partial-data case. Verify overlap enhances allocation/review answers when those workflows are available; no live OpenAI or market-data calls in automated tests.

## Acceptance criteria

- [ ] Known company overlap and its source dates appear in the existing reviews and recommendations.
- [ ] Partial, unknown, nested, and stale coverage is represented honestly.
- [ ] Known indirect exposure affects configured cap checks only according to the user's explicit policy.
- [ ] Direct blocker remains ticket 03, and no core workflow gains a look-through dependency.

## Non-goals

- No general ETF analytics platform, mandatory current/full coverage, sponsor-holdings database, new subscription, optimizer, or mandatory gate on all sizing.
