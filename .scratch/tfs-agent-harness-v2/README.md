# TFS Agent Harness V2 tickets

**Authority:** [Integration specification](../../specs/tfs-agent-harness-v2.md).

Local Markdown backlog, matching the configured tracker. All tickets are proposed and unimplemented. `ready-for-agent` means sufficiently specified, not permission to implement during this planning task; work only the frontier after implementation is requested.

| Ticket | Delivers | Blocked by |
|---|---|---|
| [01](issues/01-evaluation-baseline.md) | Reproducible current behavior and evaluation contracts | None |
| [02](issues/02-openrouter-conformance.md) | Mocked OpenRouter tool round trip | 01 |
| [03](issues/03-chatgpt-sign-in-conformance.md) | Existing encrypted sign-in and streaming conformance | 01 |
| [04](issues/04-stock-analysis-proof-and-decision.md) | Structured/conversational stock proof, minimum shared budget and maintenance-benefit decision | 02, 03 |
| [05](issues/05-conversation-execution.md) | Conversational execution through PydanticAI | 04 + affirmative decision |
| [06](issues/06-long-context-and-model-budgets.md) | Large-result follow-up and shared model-aware budgets | 05 |
| [07](issues/07-interruption-and-proposal-safety.md) | Cancellation/restart recovery and safe proposals | 05, 06 |
| [08](issues/08-exposure-and-new-cash-workflows.md) | Exposure and new-cash workflow migration | 07 |
| [09](issues/09-review-theme-and-hypothetical-workflows.md) | Re-underwriting, theme and hypothetical migration | 07 |
| [10](issues/10-cutover-and-remove-old-loops.md) | Approved live compatibility gate, default cutover and obsolete-code removal | 08, 09 |

02 and 03 may proceed independently after 01. 08 and 09 may proceed independently after 07; coordinate shared orchestration edits. Other dependencies are sequential. If 04 recommends retaining the current harness, stop the migration backlog and record the alternative rather than implementing 05–10.

Each ticket is a verifiable slice. Reuse existing test seams and preserve the seven audit phases. Tickets 02–04 use mocked HTTP and fake credentials; reports must mark real-provider compatibility unverified. Ticket 04 establishes shared attempt/deadline enforcement and requires net maintenance benefit; 06 extends that foundation with model-aware budgets. Before release, 10 requires a small explicitly approved live compatibility test for each connection. Under current Native Codex Only instructions, an OpenRouter live test requires a specific user-authorized exception; absent authorization, the release gate remains pending.
