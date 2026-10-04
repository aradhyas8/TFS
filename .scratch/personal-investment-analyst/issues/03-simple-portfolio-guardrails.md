# 03: Simple portfolio guardrails

**What to build:** Apply explicit user-provided portfolio limits and settings to current and proposed exposures, using usable dated values, without inventing a risk profile.

**Blocked by:** 02: Quotes, FX, and source freshness.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Collect explicit settings through the existing Next.js interaction and apply them in the shared FastAPI pipeline. Python owns all exposure arithmetic and guardrail checks before structured responses are returned; model recommendations cannot waive or replace configured checks.
- Extend the shared request/context and deterministic guardrail stages using the dated marks and FX from ticket 02. Ticket 01 is a transitive prerequisite, not an additional direct blocker.
- Keep settings minimal: single-company cap, active budget if configured, baseline allocation if configured, known-indirect-exposure cap policy when supplied, and other explicit decision-critical values already required by the product spec.
- Store only those explicit supplied settings in a simple frontend input/backend context. Unknown values remain unknown; do not infer risk tolerance or introduce a questionnaire, scoring model, risk-profile subsystem, or policy engine.
- Aggregate direct-company exposure across accounts. Calculate current and post-change weights deterministically, including the relevant whole-portfolio cash and proposed new cash.
- Include individual stocks, sector/theme ETFs, and deliberate macro tilts such as excess cash in the active budget when their classification and baseline are supplied. If those inputs cannot distinguish deliberate excess cash, leave that contribution qualified or unknown.
- Compare with a baseline only when supplied; otherwise show current exposures without inventing allocation targets. Conviction cannot override a configured cap or budget.
- Explain existing above-cap direct positions and a forward reduction path rather than approving an exception.
- Keep indirect exposure partial or unknown until supported. Preserve the user's cap policy without treating missing overlap as zero or silently disabling it.

## Tests and verification

- Exercise the shared review with explicit caps/budgets/baselines present, absent, and partially supplied, using usable current or clearly labeled user marks.
- Verify cross-account company aggregation, pre/post-change weights, configured active-budget classification, and above-cap reduction behavior.
- Check that no baseline produces no invented target mix and missing values produce no inferred limits or risk score.
- Verify unusable marks/FX prevent unjustified weight-based conclusions. Script fake model proposals that breach supplied caps/budgets and verify backend Python checks enforce those limits in the displayed result; use fake data without live OpenAI or market-data calls.

## Acceptance criteria

- [ ] Only explicit user-provided values are stored and applied; unset settings stay unknown.
- [ ] Current and proposed exposures are checked against configured limits using the dated valuation basis.
- [ ] Configured caps and budgets cannot be waived by analyst conviction.
- [ ] The implementation contains no risk-profile subsystem, questionnaire, scoring model, or policy engine.

## Non-goals

- No inferred personal limits, hardcoded cap/budget defaults, risk profiling, optimization, institutional policy system, or complex tax advice.
- No ETF sponsor-holdings implementation or prerequisite; ticket 09 adds that capability.
