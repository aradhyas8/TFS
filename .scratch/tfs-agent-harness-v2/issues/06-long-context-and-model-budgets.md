# 06: Support large-result follow-ups with model-aware resource policy

**What to build:** A user can investigate a large portfolio result across many tool steps, within an explicit task allowance, without losing access to evidence or silently reducing model capability.

**Blocked by:** 05 — Conversation execution.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Extend 04's existing shared attempt/deadline ledger; preserve its charging and exhaustion guarantees. Add capability-aware token/context allowances, cost estimates and compaction accounting here rather than deferring the proof's basic shared-budget enforcement to this ticket.
- [ ] Use capability-aware context/generation headroom and supported reasoning choices rather than inherited universal 16K/medium/eight-turn policies on migrated paths.
- [ ] Keep complete artifacts in SQLite with concise model projections, omitted-section pointers and pagination; earlier dialogue and artifacts remain discoverable.
- [ ] Add only necessary native or Harness compaction; maintain tool-call/result pairing, compatible continuation and authoritative revision/evidence constraints.
- [ ] Demonstrate a scripted task requiring more than eight model turns can finish within its authorized budget.
- [ ] Count nested workflows, retries and compaction against one run allowance. Continuing internal framework execution does not reset usage; a new user task receives its own policy budget.
- [ ] Record effective settings and reasons for unsupported omissions; unknown pricing/usage is visible and retains secondary request/time/token controls.
- [ ] Exhaustion and repeated no-progress retrieval end with an honest partial/failure notice and saved artifact references under the existing public contract.
- [ ] Average costs and private continuation remain excluded from public/model projections as required; model summaries never grant financial authority.

## Verification

Exercise oversized workflow results, section follow-up after compaction, valid call pairing, nested-budget exhaustion, unknown pricing and repeated failed retrieval. No live model calls. Run focused context/provider/conversation tests and backend Ruff/mypy; cover any necessary public UI contract change.
