# 04: Prove stock analysis end to end and decide adoption

**What to build:** Evaluation-only structured and conversational stock-analysis runs, plus artifact follow-up, that exercise PydanticAI's tool loop under TFS supervision and establish whether it simplifies maintenance.

**Blocked by:** 02 — OpenRouter conformance; 03 — ChatGPT sign-in conformance.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Execute structured stock input directly through the existing thread/run lifecycle; no extra outer model call selects or rewrites the validated workflow answer.
- [ ] Submit conversational text such as "Analyze MSFT" without structured workflow input, preselected tools or application keyword routing. Exercise PydanticAI's model-to-tool dispatch, existing stock workflow and final answer; verify the advertised tools and executed tool arguments in the trace. Include an unavailable/ambiguous security case that clarifies or fails safely.
- [ ] Use scripted model responses for deterministic tool-loop tests, and label them as dispatch/continuation evidence rather than proof of an upstream model's autonomous tool selection. Include this conversational case in the pre-release live gate.
- [ ] Let PydanticAI own the migrated inference/tool loop. Retain investment calculations, evidence prerequisites, validators and artifact storage, including missing-evidence outcomes.
- [ ] Persist the run/result and answer a follow-up using the model-safe artifact projection and section retrieval.
- [ ] Match baseline semantic financial outputs, evidence binding and hypothetical/revision behavior on both mocked provider paths.
- [ ] Prove supported reasoning settings are honored and the minimum shared proof budget below covers conversational and nested workflow work; resource exhaustion cannot pass as successful analysis.
- [ ] Prove cancellation drops late output, output correction does not repeat completed state-changing work, and existing proposal confirmation regressions pass.
- [ ] Keep the current runtime as default; evaluation selection remains internal and reversible.
- [ ] Compare the equivalent old/new slice: custom orchestration/adapter production lines (excluding tests, fixtures and vendored dependencies), independently maintained retry/dispatch/provider responsibilities, added dependencies and retained compatibility code. Identify concrete code/responsibilities replaced and provide a net maintenance assessment; line count alone is insufficient.
- [ ] Write an adopt/adapt/retain decision with pinned versions, focused test results, measured fixture overhead and remaining uncertainty. Adopt/adapt requires a demonstrated reduction in custom orchestration code or independently maintained responsibilities without offsetting complexity. A working integration with no net maintenance benefit recommends retain or revises the proof before downstream migration.
- [ ] Label both connections "mocked contract verified; real-provider compatibility unverified" until each passes the approved live release gate. No model-quality, authentication or entitlement claim may be inferred from mocked traffic.

## Minimum shared proof budget

Use one task-scoped ledger with a finite model-attempt allowance and one absolute wall-clock deadline. Charge each dispatched model attempt, including outer conversation, nested stock inference, validation/model retries and any compaction, before dispatch; failed attempts still consume a slot. Existing per-request/tool timeouts are capped by the remaining deadline. No internal run receives a fresh allowance. Record reported usage, marking unavailable usage unknown; advanced token/cost reservation and capability-aware context allocation belong to 06.

Document the concrete proof allowance. Demonstrate (1) outer plus nested work consumes it cumulatively, (2) retry cannot reset it, and (3) exhaustion prevents further dispatch and persists an honest incomplete outcome. A follow-up user request has its own task budget. Reuse this ledger in 06 rather than adding a second accounting system.

## Verification and exit gate

Run structured and conversational stock-analysis parity, artifact follow-up, minimum-budget exhaustion, boundary and safety tests from 01–03. Ruff/mypy cover changed backend code. Tickets 05–10 start only after an affirmative adopt/adapt decision supported by maintenance evidence. This is a provisional architecture decision, not release clearance: real compatibility remains unverified until 10's live gate passes.
