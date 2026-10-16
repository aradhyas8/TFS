# 07: Preserve interruption recovery and portfolio confirmation safety

**What to build:** Users can cancel or reopen interrupted V2 work and review agent-prepared portfolio proposals without duplicate edits, late writes or weakened confirmation safeguards.

**Blocked by:** 05 — Conversation execution; 06 — Long-context and model budgets.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Cancellation before/during/after a tool call keeps the authoritative terminal status and rejects late model or handler output.
- [ ] Reopening SQLite after process interruption preserves conversation, accepted artifacts, proposals and usage; interrupted runs are reported honestly under existing recovery behavior.
- [ ] Persist any required framework state privately with version/engine identity; incompatible state fails safely and does not replay effects.
- [ ] If storage changes are necessary, reuse the existing versioned migration/backup path and prove existing portfolio, receipt, decision and thread data survives.
- [ ] Agent proposals use the committed bound revision even in hypothetical runs; identical previews deduplicate, ambiguous accounts clarify, and output retries do not stack quantity changes.
- [ ] Only the existing user-confirmation endpoint can commit; stale revisions, concurrent confirmation and repeated requests preserve transactional receipts.
- [ ] No transparent crash replay or new durable engine is introduced. A fresh user retry uses the existing idempotency/revision rules and cannot silently reapply previous effects.

## Verification

Run temporary-database restart, cancellation races, migration tests if applicable, proposal deduplication and concurrent/stale confirmation regressions. These are financial/release-critical checks; include nearby persistence regressions plus Ruff/mypy.
