# 10: Canadian-stock evidence support

**What to build:** Extend the established stock-assessment path to Canadian issuers using permitted issuer evidence and exact user-opened SEDAR+ verification links.

**Blocked by:** 05: US stock analysis in portfolio context.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Extend issuer-specific backend research tools inside the existing FastAPI/OpenAI orchestration; preserve common Python calculations, guardrails, structured validation, and Next.js question/result display.
- Extend the existing stock evidence stage and shared pipeline; retain common portfolio, valuation, scenario, guardrail, and answer behavior.
- Use permitted issuer investor-relations material, including available filings/releases/remarks/webcasts as appropriate, with source and publication/as-of dates.
- Provide exact SEDAR+ filing verification links for the user to open. Do not automate SEDAR+ scraping, construct its database, or imply automated access/reuse rights.
- Material claims remain evidence-backed and inspectable. If a decisive filing fact is unavailable, state it as unresolved and return conditional direction rather than implying verification.
- Preserve financial periods, units, currencies, and definitions; distinguish reported facts from derived values and analyst judgments.
- Use suitable dated quotes/FX and the same company scenarios, serious alternatives, cap/budget constraints, and amount-eligibility rules.
- Make the extension available to stock assessment, new cash, portfolio review, and theme exploration through the common evidence boundary as those request types exist.
- Follow implementation priority after the main four workflows; Canadian support must not become a prerequisite for the core US/portfolio experience.

## Tests and verification

- Use fixed Canadian issuer-material and exact-filing-link fixtures with conflicting/missing evidence, dated financials, quotes, and FX.
- Exercise the existing input/result boundary and confirm Canadian evidence replaces only issuer-specific sourcing rules.
- Exercise Canadian-stock assessment and applicable existing-workflow cases through the application with fake model/tool responses and fake permitted issuer data. Review completed application answers separately for research quality; fixed answers check display/contract only.
- Verify no SEDAR+ scraping/database construction, claim of unavailable evidence review, or fabricated tax effect; automated tests make no live OpenAI or market-data calls.

## Acceptance criteria

- [ ] A Canadian stock can be assessed through the same pipeline using permitted evidence and exact SEDAR+ verification links.
- [ ] Evidence limits and unresolved facts remain visible and affect confidence/sizing appropriately.
- [ ] Existing US and portfolio journeys remain usable and unchanged in architecture.
- [ ] Only ticket 05 directly blocks this extension; priority remains after the main four workflows.

## Non-goals

- No automated SEDAR+ scraping, SEDAR+ database, transcript-ingestion platform, separate country-specific AI runtime, new country-specific architecture, or core-US workflow prerequisite.
