# 11: Save/revisit decisions

**What to build:** Save and reopen the dated question, evidence, reasoning, conclusion, and optional later user-confirmed action so the investor can revisit a decision honestly.

**Blocked by:** 01: Full-stack bootstrap + dated portfolio review.

**Status:** ready-for-agent

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Connect minimal Next.js save/reopen actions to FastAPI-owned plain local-file persistence of the shared structured decision. API keys and other backend secrets are not part of saved decisions or frontend responses.
- Add plain local-file storage to the existing result boundary; write it from scratch without reusing old persistence or introducing a mandatory database.
- Store only the dated question, as-of date, evidence references, reasoning, conclusion, and optional user-confirmed action needed for later review.
- Reopen the saved reasoning with its original dates, assumptions, evidence references, and uncertainty; do not relabel old quotes or conclusions as current.
- Treat a recommendation as analysis. An action is recorded as confirmed only after the user explicitly supplies that confirmation.
- Use the same shared decision output for every implemented request type. Do not create separate history stores or architectures for stock, allocation, review, or theme workflows.
- Keep saving optional and late in implementation priority. No analysis workflow depends on storage or action confirmation.

## Tests and verification

- Round-trip a controlled shared result through frontend save/reopen → FastAPI → plain local-file storage → frontend display, comparing the dated question, evidence references, reasoning, and conclusion. Where analysis is exercised, use fake model and fake data providers.
- Verify absent action stays unconfirmed and an explicitly supplied confirmation is preserved without inferring execution details.
- Check historical dates remain visible and old data is not presented as fresh.
- Verify the existing analysis journey works without saving; use fake model/data providers without live OpenAI or market-data calls.

## Acceptance criteria

- [ ] A user can save and reopen a dated decision with its evidence and reasoning.
- [ ] Only explicitly user-confirmed actions are labeled confirmed; recommendations never become claimed trades.
- [ ] Plain local files suffice and there is no reused persistence or mandatory database.
- [ ] Direct blocker remains ticket 01, priority remains last, and no analysis ticket gains a saving dependency.

## Non-goals

- No transaction ledger, portfolio-performance tracking, brokerage synchronization, automatic trade detection, database migration, multi-user system, or blocker on analysis.
