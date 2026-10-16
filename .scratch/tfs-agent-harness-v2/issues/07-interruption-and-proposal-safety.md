# 07: Preserve interruption recovery and portfolio confirmation safety

**What to build:** Users can cancel or reopen interrupted V2 work and review agent-prepared portfolio proposals without duplicate edits, late writes or weakened confirmation safeguards.

**Blocked by:** 05 — Conversation execution; 06 — Long-context and model budgets.

**Status:** complete

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [x] Cancellation before/during/after a tool call keeps the authoritative terminal status and rejects late model or handler output.
- [x] Reopening SQLite after process interruption preserves conversation, accepted artifacts, proposals and usage; interrupted runs are reported honestly under existing recovery behavior.
- [x] Persist any required framework state privately with version/engine identity; incompatible state fails safely and does not replay effects.
- [x] If storage changes are necessary, reuse the existing versioned migration/backup path and prove existing portfolio, receipt, decision and thread data survives.
- [x] Agent proposals use the committed bound revision even in hypothetical runs; identical previews deduplicate, ambiguous accounts clarify, and output retries do not stack quantity changes.
- [x] Only the existing user-confirmation endpoint can commit; stale revisions, concurrent confirmation and repeated requests preserve transactional receipts.
- [x] No transparent crash replay or new durable engine is introduced. A fresh user retry uses the existing idempotency/revision rules and cannot silently reapply previous effects.

## Verification

Run temporary-database restart, cancellation races, migration tests if applicable, proposal deduplication and concurrent/stale confirmation regressions. These are financial/release-critical checks; include nearby persistence regressions plus Ruff/mypy.

### Verification Evidence
- **AC 1 (Cancellation before/during/after tool call)**: Verified by `test_cancellation_before_tool_call_halts_and_rejects_late_output`, `test_cancellation_during_active_tool_call_halts_and_rejects_output`, and `test_cancellation_after_tool_call_drops_late_model_answer`. Terminal `cancelled` state is preserved in DB and late outputs/messages are dropped.
- **AC 2 (Reopening after process interruption)**: Verified by `test_reopening_sqlite_preserves_conversation_artifacts_and_usage_without_replay`. Running runs transition to `interrupted`, usage and artifacts survive intact.
- **AC 3 (Framework state persistence & incompatible engine/version safety)**: Verified by `test_incompatible_framework_state_fails_safely`. Validated on direct check, restart recovery (`recover_interrupted_runs`), idempotent reuse (`submit_message`), and orchestration execution (`Orchestrator._execute_run`), safely failing without side-effect replay.
- **AC 4 (Versioned migration v4 -> v5 and automated backup)**: Verified by `test_migration_v4_to_v5_creates_backup_and_preserves_data` and `test_data_survival_across_reopen_and_initialization`. Automated pre-migration backup database created at user_version 4; upgraded database migrated to user_version 5 with `private_execution_state` table; all existing threads, runs, messages, proposals, and portfolio revisions survive intact.
- **AC 5 (Bound revision, deduplication, ambiguous account clarification & non-stacking retries)**: Verified by `test_agent_proposal_uses_committed_bound_revision_in_hypothetical_run`, `test_identical_previews_deduplicate_and_preserve_references`, `test_ambiguous_or_unknown_account_clarifies_without_creating_proposal`, and `test_output_retry_with_repeated_edit_does_not_stack_shares`.
- **AC 6 (User confirmation safeguards, stale revision 409 & concurrent serialization)**: Verified by `test_only_user_confirmation_commits_revision`, `test_concurrent_confirmations_serialize_safely`, and `test_stale_revision_confirmation_returns_409`.
- **AC 7 (No transparent crash replay; revision/idempotency rules preserved)**: Verified across `test_reopening_sqlite_preserves_conversation_artifacts_and_usage_without_replay` and `test_only_user_confirmation_commits_revision`.

### Test Suite Execution
- `tests/test_interruption_and_proposal_safety.py`: 14/14 passed.
- Nearby regressions:
  - `tests/test_run_lifecycle.py`, `tests/test_chat_proposals.py`, `tests/test_persistence.py`, `tests/test_conversation.py`: 84/84 passed.
  - `tests/test_legacy_migration.py`, `tests/test_conversation_execution.py`, `tests/test_portfolio_actions.py`: 67/67 passed.
- Static analysis:
  - `ruff check`: All checks passed.
  - `mypy`: Success (0 issues found across all modified files).
