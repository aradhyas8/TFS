# 05: Run ordinary conversation through the new engine

**What to build:** Existing thread questions can answer directly, call focused TFS tools and retrieve prior results through PydanticAI while retaining the current API and UI contracts.

**Blocked by:** 04 — Stock-analysis proof, with an affirmative adopt/adapt decision.

**Status:** complete

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [x] A conceptual question completes without financial tools; a focused financial/research question invokes existing capabilities and persists correct outcomes/artifact links.
- [x] Preserve idempotent submission, one active run per thread, run-bound revisions, missing-portfolio clarification and private continuation.
- [x] Preserve state-dependent tool advertising and handler-enforced prerequisites; existing tools remain available under their domain contracts.
- [x] Final-answer validation, empty/tool-shaped answer recovery, provider failures and failed-workflow notices match the audit fixes.
- [x] Persist narrative, tool outcomes and citations separately; public history exposes no raw private framework state or secrets.
- [x] Framework execution is not nested inside the old fixed eight-turn loop; each retry has one owner and uses the shared task budget.
- [x] Structured desk requests continue using direct dispatch. Unmigrated workflows remain available with all nested work accounted for and explicitly tracked for later removal.
- [x] Keep internal fallback to the original engine available during migration, selected before a run starts; never switch engines mid-run after effects occur.

## Verification

Run existing conversation, context retrieval, error and tool prerequisite tests against the migrated path with scripted providers. Verify unchanged thread response shapes; run TypeScript/browser checks only if frontend code actually changes. Run backend Ruff/mypy.
