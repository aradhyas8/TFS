# TFS Agent Harness V2 — PydanticAI evaluation and integration

**Status:** Proposed; tickets prepared for review. No implementation or framework adoption is authorized by this document alone.

**Tickets:** [Dependency-ordered local backlog](../.scratch/tfs-agent-harness-v2/README.md).

## Problem Statement

TFS users should be able to select a supported model and let it investigate using its supported reasoning, context and tools. Current execution spreads generation ceilings, effort reductions, retries and iteration counts across provider transport, conversational orchestration and financial workflows. Raising one limit does not remove the others, and unbounded execution would weaken reliability and spending control.

The seven completed audit phases improved result retrieval, error handling, conversation validation, presentation, portfolio proposals, context efficiency and unified thread execution. V2 must preserve those outcomes while testing whether PydanticAI reduces custom orchestration responsibilities.

## Solution

Evaluate PydanticAI core as the model/tool execution engine under TFS's existing run supervisor. Use selected Harness capabilities only where they replace necessary context or execution behavior. Keep investment services, financial arithmetic, evidence validation, revision binding, SQLite application records and human portfolio confirmation in TFS.

Prove structured stock analysis, a conversational "Analyze MSFT" request with agent-selected tools, and artifact follow-up first. Adopt further only after the decision gate records financial parity, mocked connection contracts, execution safety and a demonstrated net maintenance benefit. Real-provider compatibility remains unverified until separately approved live tests pass before release. Keep the current path available during evaluation and remove superseded loops after cutover.

## User Stories

1. As an investor, I want my selected model and supported reasoning setting respected so that TFS does not silently reduce capability.
2. As an investor, I want existing model connections to keep their sign-in and tool behavior so that migration does not require unnecessary reconnection.
3. As an investor, I want a structured desk request dispatched directly so that the agent does not spend extra turns selecting an already chosen workflow.
4. As an investor, I want recommendations to retain validated calculations and primary evidence so that a framework change does not alter financial correctness.
5. As an investor, I want detailed results retrievable in follow-up questions so that compaction does not make prior analysis inaccessible.
6. As an investor, I want cancellation and resource exhaustion reported honestly so that unfinished work is never presented as a completed recommendation.
7. As an investor, I want proposals tied to their original portfolio revision and committed only after my confirmation so that autonomous investigation cannot change holdings.
8. As a maintainer, I want one owner for tool-loop execution, retries and credential refresh so that recovery does not duplicate work.
9. As a maintainer, I want an evidence-based adoption decision and a reversible cutover so that adding a framework reduces rather than compounds maintenance.

## Implementation Decisions

### Ownership and existing contracts

- Keep the existing thread API, structured workflow inputs, artifact envelopes, result panels, decision register and proposal/receipt contracts. Preserve direct structured-workflow dispatch and direct validated workflow answers from audit Phase 7; the retired synchronous analysis endpoint stays retired.
- PydanticAI owns inference and tool-loop execution for each migrated path. TFS owns request idempotency, bound revision/hypothetical snapshot, run lifecycle, persistence, cancellation and authoritative terminal outcomes.
- Keep tool handlers, financial calculations, evidence prerequisites and domain validators. Separate orchestration from domain logic only as required for the slice being migrated; avoid a speculative rewrite of the entire pipeline.
- Serialize state-sensitive tools initially. Later concurrency requires a demonstrated safe boundary; model capability alone does not authorize concurrent mutations.
- Keep the capability registry and handler-enforced prerequisites. Preserve audit Phase 6's state-dependent tool advertising and dialogue-based history; schema visibility is not an authorization boundary.
- Keep proposal preparation separate from confirmation. The agent receives no commit/confirm capability. Existing stale-revision checks, transactions, deduplication and receipts remain authoritative.

### Providers and authentication

- Evaluate OpenRouter through recorded/mocked HTTP. Preserve routing restrictions, explicit parameter support, selected model and structured-output/tool semantics. Generic compatibility is not proof of equivalence to TFS's current Responses route.
- TFS's ChatGPT-plan connection uses its own Sign in with ChatGPT flow and encrypted DPAPI/Keychain storage. PydanticAI's documented Codex OAuth provider is a candidate integration, not an assumed replacement. Verify endpoint, token audience/scopes and continuation compatibility. Retain the existing flow and use a minimal adapter if necessary.
- One component owns token refresh and persists rotated credentials; framework and TFS refreshers must not race. Preserve secret redaction and account isolation.
- Initial evaluation uses fake credentials and mocked HTTP. It verifies client contracts, not actual authentication, account entitlement or upstream tool selection. Reports label each connection's real compatibility unverified until its live gate passes.
- Before actual release, require a small explicitly approved live compatibility test for each existing connection, including conversational model-selected tool calling and continuation, actual authentication and applicable credential refresh. Specify synthetic inputs, account/endpoint/model, request/spending/time limits and sanitized evidence. Ticket 10 defines the exercise; this plan does not approve execution.
- Evaluation obeys Native Codex Only. OpenRouter live calls remain blocked unless the user explicitly authorizes that specific exception; verify subscription-test authorization as well. Missing permission, credentials or a passing result keeps release pending. These tickets authorize neither external runtimes nor credential/project-data sharing.

### Model settings and run policy

- Distinguish model capability facts, user preference and authorized resource policy. Record requested and effective model/settings, omitted unsupported parameters and the reason for any difference.
- Preserve the selected model; automatic fallback to another model is outside scope. Explicit unsupported reasoning choices produce a clear error or explanation rather than a hidden downgrade.
- Replace universal generation, effort and normal-completion turn limits on migrated paths with capability-aware request bounds and configurable cumulative run policy. Retain an emergency iteration ceiling, tool/request timeouts, cancellation and bounded recovery.
- Distinguish per-request generation allowance from visible-answer length, context capacity and cumulative usage. A large context window is not an unlimited output budget.
- One task/run ledger accounts for outer/nested model work, retries and compaction; starting an internal framework run must not reset that allowance. Define a new user request as a new task budget while preserving historical usage for audit. Failed network attempts without reliable usage are recorded as unknown, not zero-cost.
- Ticket 04's minimum is one finite task-scoped model-attempt allowance charged before every outer/nested/retry/compaction dispatch and one absolute deadline that caps request/tool timeouts. Failed dispatched attempts consume slots, and exhaustion prevents further calls with an honest incomplete outcome. Record reported usage without inventing missing values. Ticket 06 extends this same ledger with capability-aware context/token allocation and cost policy; those advanced features are not prerequisites for the proof.
- Cost estimates are best-effort and can overshoot by a response. Unknown pricing cannot silently disable spending controls; retain request/time/token boundaries and disclose unavailable estimates. Subscription usage need not have a fabricated dollar price.
- Choose concrete safe defaults during the evaluation, justify them with fixtures and capability evidence, and record remaining tuning needs. Maximum effort everywhere and unlimited execution are not defaults.

### Context, storage and recovery

- Keep complete immutable artifacts in TFS storage and use concise projections, section paths and pagination for model access. Preserve average-cost exclusion, private continuation and separation of tool outcomes from visible dialogue.
- Account for instructions, tools, history, evidence and output headroom according to the selected model. Compact before exhaustion and retain valid tool-call/result pairs and compatible native continuation state.
- Treat summaries as lossy; financial evidence, authorization and revisions remain in authoritative records. Framework messages or summaries cannot authorize confirmation or become qualified financial facts.
- Reuse existing SQLite migration/versioning and backups only if new private execution/usage records are necessary. Do not introduce a second application database or replace existing portfolio schemas with framework schemas.
- Preserve honest startup recovery: interrupted work is marked interrupted/failed under the existing contract. Transparent crash resumption is not required for V2. If a future durable engine is proposed, separately prove side-effect idempotency and replay semantics.
- Prefer existing public statuses and notices for budget exhaustion and partial work. Any new public status/API/UI change requires explicit contract coverage and a migration rationale; avoid adding one just because a framework has a matching enum.
- Final completion requires the existing output/domain validation. Budget exits, refusal, incomplete output and provider failure remain distinct outcomes in safe diagnostics.

## Testing Decisions

- The main seam is accepted thread/workflow input through run execution to persisted outcomes and retrievable artifacts, with fake market/research providers and mocked model HTTP.
- Reuse scripted model sequences, existing workflow fixtures, temporary SQLite databases, provider-boundary tests and proposal confirmation tests. Compare semantic financial results and evidence references, not nondeterministic prose or framework-internal event order.
- Test request/continuation serialization at provider boundaries, including reasoning-only truncation, parallel calls, invalid schema, expired credentials and interrupted streams.
- Test cancellation before and after tool completion, budget exhaustion in nested work, late output rejection and restart recovery. Successful state-changing tool work is not repeated by a model/output retry.
- Verify both direct structured stock analysis and conversational "Analyze MSFT" without structured input, forced workflow selection or application keyword routing. Capture advertised tools, executed arguments, validated results and artifact follow-up. Scripted responses prove framework dispatch/continuation, not real model choice; the approved live gate separately tests autonomous tool selection.
- At the evaluation gate, verify stock-analysis parity and follow-up. Expand to exposure/new-cash and re-underwriting/theme/hypothetical workflows only after an affirmative decision.
- Run focused backend tests, Ruff and mypy for changed backend code. Run TypeScript and targeted browser coverage for actual frontend changes. Use the audit's financial parity fixtures for broader release-critical cutover checks; do not default to the entire test suite.

## Adoption Gate

Ticket 04 records **adopt**, **adapt**, or **retain current harness**, with pinned dependency versions, evidence and unresolved limitations. Adopt/adapt requires structured and conversational stock execution, mocked provider contracts, financial parity, artifact follow-up, the minimum shared budget, cancellation and safe proposal confirmation to pass.

It also requires demonstrated net maintenance benefit: compare equivalent old/new orchestration and adapter production code (excluding tests/fixtures/vendor code), independently maintained responsibilities, added dependencies and retained compatibility code. Identify specific code or responsibilities replaced and explain why added complexity does not offset the benefit. Working correctly alone is insufficient, and line count alone is insufficient. Otherwise retain the current harness or revise the proof before proceeding.

This architecture decision is provisional for release. Mocked conformance proves client behavior, not actual authentication, upstream tool selection, account entitlement or model quality. Ticket 10 requires separately approved live compatibility results for both connections before actual release; unavailable approval or failed/unexecuted tests leave compatibility unverified and release blocked.

Tickets 05–10 are conditional on an affirmative adopt/adapt decision that identifies the supported integration. Blocking issues revise the integration before migration proceeds; they do not justify silently dropping a current connection or safeguard.

## Out of Scope

- Implementation as part of this planning task; publishing remote issues or changing tracker configuration.
- New investment features, arithmetic changes, trading execution or autonomous portfolio confirmation.
- General coding/shell agents, multi-agent delegation, model fallback and new providers.
- A new workflow engine, transparent crash replay, database replacement or UI redesign.
- External AI calls/runtimes under the current repository instructions.

## Further Notes

The framework recommendation is selective adoption, not a wholesale rewrite. A framework reduces execution plumbing; TFS still owns financial correctness, product contracts and authority.

### Authority and references

- [Audit remediation, including Phase 7 consolidation](../AUDIT_REMEDIATION.md).
- [Conversation contracts](conversation-and-agent-orchestration-implementation.md) and [portfolio action safeguards](portfolio-actions-and-persistence.md). V2 changes only the orchestration policies explicitly identified above; preserve the remaining contracts.
- [PydanticAI core](https://pydantic.dev/docs/ai/core-concepts/agent/) and [Harness package](https://pydantic.dev/docs/ai/harness/).
- [OpenRouter integration](https://pydantic.dev/docs/ai/models/openrouter/) and [Codex integration](https://pydantic.dev/docs/ai/models/openai-codex/).
- [Conversation history and usage](https://pydantic.dev/docs/ai/core-concepts/message-history/), [compaction](https://pydantic.dev/docs/ai/harness/compaction/) and [durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/).
- [Framework source](https://github.com/pydantic/pydantic-ai). Verify documentation against the pinned version during evaluation; published support alone does not prove TFS compatibility.
