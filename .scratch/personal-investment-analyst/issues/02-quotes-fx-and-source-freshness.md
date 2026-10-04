# 02: Quotes, FX, and source freshness

**What to build:** Refresh the existing portfolio review with verified security/listing identities, usable dated marks and FX, and visible source freshness so later weight-based decisions use a defensible valuation basis.

**Blocked by:** 01: Full-stack bootstrap + dated portfolio review.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Expose quote, identity, and FX access as backend financial-data tools used by the existing orchestration loop. Refresh valuations in Python and render the structured result in Next.js; extend the application boundary with fake model and fake data providers for tests.
- Extend the shared portfolio and evidence stages and existing result display; do not create a parallel quote-analysis application.
- Resolve security/listing identity and quote currency before treating a price as usable. Ambiguous or conflicting identity remains unresolved.
- Use a qualified personal-use indicative-price source only after checking coverage and terms. yfinance remains a candidate, not an assumed live or execution-price source; a labeled user-entered broker-display mark is the fallback.
- Record price source, capture time, applicable quote/as-of date, quote age, and delayed, cached, stale, or manual status. Never label these as live.
- Use suitable dated FX for the reporting currency, including Bank of Canada Valet for appropriate CAD conversions. Display local/reporting currencies, rate, date, and source; indicative FX is not an execution quote.
- Account for splits and dividends once and disclose the price/return calculation basis. Adjusted prices and separately counted dividends must not double count the same economic benefit.
- Refresh values and exposure in the same review. Missing or stale inputs must qualify results and prevent confident sizing when unusable; do not invent freshness thresholds or substitute unknowns with zero.

## Tests and verification

- Use fake model and fake source/data providers for verified, ambiguous, delayed, cached, stale, missing, contradictory, and user-entered quotes plus dated FX. Exercise tool requests through FastAPI and check the Next.js result.
- Verify currency conversions and quote/source-date display through the existing input/result journey.
- Check that unresolved identity or unusable quote/FX produces explicit qualification or conditional direction.
- Check the disclosed corporate-action/return basis prevents double-counting splits or dividends; no live OpenAI or market-data calls in automated tests.

## Acceptance criteria

- [ ] The same portfolio review displays refreshed values alongside verified identity, currencies, price/FX provenance, dates, and age.
- [ ] A user can use a clearly labeled broker mark when provider coverage or terms are insufficient.
- [ ] Stale, cached, manual, missing, and unresolved data remain visible and never appear as confirmed live data.
- [ ] Guardrail and later sizing stages can consume the usable dated values and their limitations.

## Non-goals

- No continuous refresh, background monitoring, execution quotes, new subscriptions, broad market-data platform, or a separate AI/provider orchestration layer.
