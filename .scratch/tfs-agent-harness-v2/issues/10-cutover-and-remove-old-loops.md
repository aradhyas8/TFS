# 10: Cut over verified paths and remove obsolete orchestration

**What to build:** All supported TFS requests use one documented V2 execution design, with proven safeguards and no superseded model loops or hidden policy limits.

**Blocked by:** 08 — Exposure/new-cash migration; 09 — Review/theme/hypothetical migration.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Search all callers before deleting old transports/loops/retry helpers. Retain domain calculation/validation code and any provider adapter still required for compatibility.
- [ ] Make V2 the default and remove the temporary runtime selector only after automated verification and the live release gate below pass; a documented release rollback preserves application data and credentials.
- [ ] Confirm there is one loop owner per path, one refresh owner per connection, and one task-budget owner across nested operations.
- [ ] Verify all seven audit improvements, existing thread/workflow contracts, financial/evidence parity, interruption recovery and proposal confirmation protections.
- [ ] Cover conceptual chat, focused research, stock, exposure, new-cash, re-underwriting, theme and hypothetical flows on both mocked provider routes.
- [ ] Document requested/effective settings, budget defaults, subscription restrictions, unknown-pricing behavior, dependency versions, recovery limits and operator rollback steps.
- [ ] Publish an acceptance report with separate mocked-contract and live-compatibility results for each connection. Unexecuted or failed live checks leave real-provider compatibility unverified and release blocked; a small passing check does not establish broad model quality.
- [ ] Introduce no unapproved external model calls, new investment features, retired endpoint restoration or framework-owned portfolio database.

## Live release gate — separately approved

Before actual release, obtain explicit approval for a small compatibility test on **each** supported connection: OpenRouter and TFS's existing ChatGPT/subscription sign-in. Approval names the endpoint/account/model, permitted authentication steps, request/spending/time bounds and synthetic data to send. This planning ticket is not that approval.

Use synthetic portfolio/research fixtures and one conversational "Analyze MSFT" request without preselected workflow/tool, plus a short artifact follow-up. Verify actual authentication, streamed model-selected tool calling, validated tool results, continuation and final output; distinguish live model work from mocked financial data. Verify each connection's applicable credential refresh path in the smallest approved exercise; if refresh cannot be exercised, report it unverified and do not claim full authentication compatibility. Capture sanitized evidence with versions, model/endpoint, date, usage and result; keep credentials and project/source data out of logs/prompts.

The repository's current Native Codex Only rule forbids external model/provider calls. An OpenRouter live test therefore remains blocked unless the user explicitly authorizes the specific exception to that rule. Verify permission for the subscription test as well. Missing permission or credentials keeps the release gate pending; it does not turn mocked coverage into live compatibility or authorize silently dropping a connection.

## Verification

Run the relevant financial parity matrix and focused release-critical integration/browser checks. Expand testing for newly discovered risk, not by default to every suite. Run Ruff/mypy on changed backend code and frontend TypeScript if changed. Validate the installed dependency lock and clean package installation without model API calls.
