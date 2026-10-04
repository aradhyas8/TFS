# 05: US stock analysis in portfolio context

**What to build:** Answer a named US-stock investment question through the Next.js/FastAPI application with primary evidence, company-appropriate five-year cases, relevant alternatives, and portfolio guardrails.

**Blocked by:** 03: Simple portfolio guardrails; 04: ETF/cash five-year comparison.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Extend the shared FastAPI decision pipeline and Next.js question/result boundary. Add issuer/SEC research tools to the existing backend-owned OpenAI orchestration loop, with Python company-scenario calculations and deterministic guardrail enforcement outside the model. Return a validated structured recommendation.
- Use issuer investor-relations material and SEC EDGAR as the primary US filing authority. An optional convenience parser does not replace the original filing.
- Preserve financial periods, units, currencies, metric definitions, and source dates; check custom tags, segments, and material notes against filings. Use sector-appropriate ratios and mid-cycle context for cyclicals.
- Build conditional downside/base/upside company cases from operating drivers and exit-value sensitivity, naming growth, margins, cash conversion, reinvestment, dilution, FX, discount rate, and exit valuation as applicable. Reverse valuation describes required performance under named assumptions, not a unique market belief.
- Review evidence-backed thesis strengths, failure modes, and uncertainty. Missing/contradictory facts stay unknown; do not imply unavailable transcript Q&A was reviewed. Macro evidence is used only to test a named mechanism.
- Compare with relevant holdings, diversified ETFs, cash, and valid adding/holding/reducing/exiting/no-action choices using ticket 04 and configured guardrails.
- Return preferred action, main reason, one or two plausible alternatives, downside, assumptions, uncertainty, evidence links/dates, and what could change the conclusion. Give amounts only when the dated snapshot, identity, usable quote/FX, applicable numeric cap/budget, and minimal explicit context justify them.
- Cost basis is relevant only to known tax/transaction consequences, not to the thesis or current recommendation. Account type is context; do not infer TFSA room or unknown personal tax effects.

## Tests and verification

- Use dated portfolio, filing/evidence, quote/FX, scenario-driver, and fixed-answer fixtures at the shared input/result boundary.
- Check period/unit/currency/definition provenance, company-case arithmetic, alternative comparison, guardrails, and missing/conflicting-fact behavior.
- Exercise the complete frontend stock question → FastAPI → fake model research/calculation tool requests → fake evidence/data and real Python calculations → validated structured recommendation → frontend journey. Review completed application answers separately for research/judgment quality.
- Verify no invented probability, confident amount with missing required inputs, claimed unavailable evidence, or executed trade; automated tests make no live OpenAI or market-data calls.

## Acceptance criteria

- [ ] A US stock question returns a completed portfolio-aware recommendation in the same application request, displayed in the frontend.
- [ ] Material claims link to dated primary evidence, and facts, calculations, and judgments are distinguishable.
- [ ] Serious alternatives use instrument-appropriate five-year cases on a common date/currency basis.
- [ ] The stock path shares the existing pipeline and can be extended to the other workflows.

## Non-goals

- No separate stock-analysis architecture, automatic trading, background research, unbounded discovery, optimizer, or separate workflow-specific agent/Codex app-server runtime.
- Canadian evidence rules and sponsor-holdings look-through remain later extensions, not prerequisites.
