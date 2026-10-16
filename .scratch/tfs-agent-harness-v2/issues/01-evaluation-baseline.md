# 01: Establish the evaluation baseline and invariants

**What to build:** A reproducible comparison of current stock-analysis execution and follow-up retrieval that lets maintainers judge V2 against actual TFS behavior.

**Blocked by:** None (can start immediately).

**Status:** complete

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).
**Baseline Report:** [01 Evaluation Baseline Report](../reports/01-evaluation-baseline.md).

## Acceptance criteria

- [x] Capture current accepted workflow input, semantic financial result, evidence/artifact references, visible answer contract and persisted run outcome using existing fixtures and scripted models.
- [x] Cover dated/undated stock analysis, missing evidence, bound revision, a hypothetical snapshot and an artifact-section follow-up without adding live data calls.
- [x] Establish cancellation, duplicate submission, proposal deduplication and stale-confirmation expectations using existing tests rather than duplicating them.
- [x] Record current provider settings, continuation semantics, credential ownership and limits; distinguish documented framework support from verified compatibility.
- [x] Define the evaluation's per-request bounds, shared task budget and completion/error expectations; justify initial defaults and state unknown pricing behavior.
- [x] Preserve all production behavior and audit Phase 7 direct dispatch.

## Verification

Run the focused fixture comparisons and existing relevant regression tests. Deliver a baseline report with repeatable commands, fixture IDs and the exact comparison exclusions; prose and generated IDs are not financial-parity assertions. (Delivered in `backend/tests/test_evaluation_baseline.py`, `.scratch/tfs-agent-harness-v2/reports/01-evaluation-baseline.md`, and `.scratch/tfs-agent-harness-v2/baseline_data.json`).

## Boundaries

This is an executable evaluation baseline, not a general test framework or production refactor. Provider and financial I/O remain mocked.
