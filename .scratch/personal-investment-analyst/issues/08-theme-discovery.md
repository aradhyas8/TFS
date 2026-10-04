# 08: Theme discovery

**What to build:** Explore a theme through an agreed economic mechanism and bounded shortlist, then return a portfolio-aware comparison that may conclude no action.

**Blocked by:** 05: US stock analysis in portfolio context.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Use the existing Next.js question/clarification interaction to agree the mechanism, shortlist, and effort bound, then extend the shared FastAPI/OpenAI orchestration to assess that bounded set. Reuse backend Python calculations and the validated response display.
- Extend the shared request/context, evidence, comparison, guardrail, and recommendation stages; do not implement a theme-specific architecture.
- Clarify the economic mechanism and obtain agreement on a bounded shortlist and effort bound before conducting candidate research.
- Stay within that shortlist and effort bound. Use thesis-specific agency/macro evidence only when it tests the named mechanism.
- Assess candidates using the established stock/ETF methods and compare with suitable ETF, cash, and no-action alternatives on a consistent dated reporting-currency basis.
- Show instrument-appropriate five-year downside/base/upside cases, pivotal assumptions, known costs, evidence provenance, and uncertainty without invented probabilities.
- Apply the same explicit portfolio guardrails and amount eligibility rules. Unknowns remain unknown and missing decisive inputs lead to conditional direction.
- Return preferred action, one or two plausible alternatives, downside, uncertainty, and what could change the view. A valid theme can lead to no buy or no action.
- Review Canadian candidates through the later Canadian evidence extension when available; core theme verification can use US candidates and existing alternatives.

## Tests and verification

- Use controlled theme/mechanism/shortlist/evidence/portfolio/alternative fixtures with the shared input/result boundary.
- Exercise the complete application theme journey with fake model responses/tool requests and fake data, checking agreement, shortlist containment, effort limits, mechanism testing, alternatives, and stopping. Review completed application answers separately for reasoning quality.
- Check no forced buy, unbounded discovery, invented probability, unsupported sizing, or claimed execution; automated checks make no live OpenAI or market-data calls.

## Acceptance criteria

- [ ] The theme is clarified and a bounded shortlist/effort agreed before research.
- [ ] Research and the recommendation remain within the agreed bounds.
- [ ] The completed answer includes serious alternatives and conditional cases and can recommend no action.
- [ ] Only ticket 05 directly blocks this workflow; the shared pipeline is retained.

## Non-goals

- No always-on candidate crawl, macro-forecasting platform, unbounded theme discovery, standalone screening system, or separate analysis architecture.
