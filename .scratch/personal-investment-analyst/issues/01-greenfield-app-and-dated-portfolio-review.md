# 01: Full-stack bootstrap + dated portfolio review

**What to build:** Enter or load a dated portfolio and ask a question through a minimal Next.js chat/question input. Receive a basic portfolio review through the real application path: portfolio input + user question → frontend → FastAPI → OpenAI orchestration → deterministic Python portfolio tool(s) → validated structured recommendation → frontend result display.

**Blocked by:** None (can start immediately).

**Status:** implemented — verified locally

**Authority:** [Personal Investment Analyst specification](../../../specs/personal-investment-analyst.md).

## Shared implementation constraints

Implement within the single shared FastAPI decision pipeline and the authoritative Personal Investment Analyst specification. Preserve the approved numbered implementation priority and direct blockers; Ticket 01 supplies the minimal OpenAI runtime inherited by later AI-dependent workflows. The deployed path is Next.js frontend → Python/FastAPI backend → OpenAI API reasoning/tool orchestration → financial/research tools → deterministic Python calculations → validated structured response → frontend. Codex is used only to build the application. The backend owns OpenAI integration, tool execution, environment configuration, and secrets; API keys must never enter frontend code, assets, or response payloads. Keep finance arithmetic, scenario calculations, and guardrail enforcement in Python outside the model. Automated verification exercises the application request/result boundary with fake model and fake data providers, including scripted tool requests; never call live OpenAI or market-data services. Fixed answers alone do not establish reasoning quality; assess completed application answers separately. No automatic orders, invented trades, personal limits, probabilities, or unjustified allocation amounts.

## Implementation requirements

- Bootstrap a real Next.js frontend and Python/FastAPI backend from scratch and connect them for the question/portfolio request and structured response. Do not reuse the old frontend, Python request handling, persistence, helpers, tests, or architecture.
- Provide minimal manual portfolio entry, simple CSV loading, a chat/question input, and result display in Next.js. Chat is the primary interaction surface; this ticket needs only question submission with portfolio context and its answer, without a broader chat platform.
- Support every supplied account, holding, and cash balance, with an as-of date, security/listing identity, shares or cash, quote currency, labeled supplied marks, reporting currency, and supplied dated FX where needed. Live market refresh belongs to ticket 02.
- Implement one backend-owned OpenAI API integration and one small tool-calling/orchestration loop. Dispatch only the Python portfolio tool(s) required for basic review, pass their computed results back to the model, and return a completed answer for the submitted question.
- Calculate holdings values, cash, portfolio totals, weights, and basic direct-company exposure deterministically in Python across accounts, outside the model. Do not silently value unknown holdings at zero or invent missing marks, FX, targets, or personal limits.
- Validate tool inputs and the structured recommendation in the backend before returning it. Preserve deterministic tool results in the response; model prose must not replace computed finance values. Missing decisive facts produce explicit qualification or conditional direction.
- Own environment configuration and API secrets in FastAPI. Frontend code, assets, configuration delivered to the browser, and response payloads never contain OpenAI API keys; the frontend submits analysis only to the backend.
- Establish the single shared decision pipeline and application request/result boundary later tickets extend. Keep later evidence, alternatives, scenarios, and guardrail features limited to what is available at this milestone.
- Provide interchangeable fake model and fake data providers for automated tests. Script model tool requests and final structured outputs so tests exercise actual backend orchestration and Python tools rather than bypassing the backend with a canned frontend response.
- Keep the production OpenAI integration present while all automated tests run through fake providers. The deployed application runs independently of Codex.

## Tests and verification

- Exercise portfolio input + question → Next.js → FastAPI → fake model tool request → real deterministic Python portfolio tool(s) → fake model structured output → backend validation → frontend display, with fake data providers and no live OpenAI or market-data calls.
- Use equivalent manual and CSV snapshots with multiple accounts, holdings, cash, currencies, supplied marks, and dated FX. Check values, totals, weights, and direct-company aggregation against independently calculated expected outcomes.
- Verify the submitted question and portfolio context reach the backend and the displayed review corresponds to the returned validated response.
- Check missing marks, unresolved identity, unusable FX, baseline, and guardrails remain unknown and do not produce invented targets or confident allocation amounts.
- Check malformed structured output or invalid tool requests cannot be displayed as a completed validated recommendation, and model-supplied arithmetic cannot replace deterministic tool results.
- Use dummy test credentials to verify backend-only configuration and the absence of API keys from frontend code/assets and response payloads. Do not use or expose real secrets in automated tests.

## Acceptance criteria

- [x] A user can enter or load a dated portfolio, submit a question through the minimal chat input, and see accounts, holdings, cash, values, weights, and a qualified basic exposure review.
- [x] The deployed path uses a real Next.js frontend, connected FastAPI backend, one OpenAI API integration, one small orchestration loop, deterministic Python portfolio tools, backend structured-output validation, and frontend result display.
- [x] The OpenAI runtime is backend-owned and can use server-side configuration; Codex is used only for development.
- [x] OpenAI API keys never reach frontend code, assets, browser-delivered configuration, or response payloads.
- [x] Automated end-to-end checks use fake model/data providers while exercising the same application orchestration and real Python calculation path.
- [x] Later tickets extend this shared pipeline and boundary. The first milestone requires no live market-data provider, ETF look-through, Canadian evidence support, or saved-decision feature.

Verification and review: [Ticket 01 implementation review](../../../docs/ticket-01-review.md). Production credentials are intentionally unset; no live model or market-data calls were made.

## Non-goals

- No generic agent framework, multi-agent architecture, broad chat subsystem, queues, workflow engine, speculative services, or mandatory database.
- Only basic portfolio-review tools are included. Live quote refresh, stock/issuer research, bounded new-cash discovery, themes, sponsor-holdings look-through, and decision history stay in their assigned later tickets.
- No later research/discovery functionality, automatic orders, Codex runtime dependency, or live OpenAI/market-data calls in automated tests.
