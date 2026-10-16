# 08: Migrate exposure review and new-cash allocation

**What to build:** Existing review and new-cash desk/conversation requests complete through V2 with unchanged investment results, clarification and decision-saving behavior.

**Blocked by:** 07 — Interruption and proposal safety.

**Status:** complete

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [x] Exposure and allocation execute by direct structured dispatch or existing conversational workflow tools using the same validated inputs.
- [x] Migrate only the model-driven orchestration; retain screening, calculation, benchmark/rule binding, sizing validation and artifact contracts.
- [x] Preserve dated/undated evidence, empty rules, benchmark fund treatment, missing loss-tolerance clarification and explicit submission of new cash.
- [x] All inference/retries use V2 resource accounting; no migrated workflow retains a hidden medium-effort ceiling or independent normal-completion turn budget.
- [x] Existing result panels, follow-up retrieval, decision saving, cancellation and original-engine rollback work.
- [x] Record financial/evidence parity against the existing audit fixtures and disclose any intentional behavioral difference before default cutover.

## Verification

Run focused exposure/allocation/workflow tests and parity fixtures. Exercise existing review/new-cash browser flows when affected, including clarification and follow-up. Run Ruff/mypy and TypeScript for changed frontend code.

### Verification Evidence
- **AC 1 (Direct structured dispatch & conversational workflow tools)**:
  - Verified by `test_exposure_review_direct_structured_dispatch` and `test_new_cash_allocation_direct_structured_dispatch`: structured desk forms execute directly without an outer chat model turn, returning populated artifact references, recommendations, and execution states.
  - Verified by `test_conversational_workflow_tools_exposure_review` and `test_conversational_workflow_tools_new_cash_allocation`: conversational agent invokes `run_exposure_review` and `run_new_cash_allocation` capabilities with identical validated schema inputs.
- **AC 2 (Orchestration-only migration & artifact contracts)**:
  - Calculation and screening pipelines (`analyze()` in `backend/analyst/pipeline.py` and allocation screening) remain the sole calculation authority.
  - Sizing validation and artifact schema contracts (`ExposureReport`, `AllocationReport`, `Recommendation`) are strictly preserved.
- **AC 3 (Domain semantics preservation)**:
  - `test_dated_and_undated_evidence_preserved`: SEC/issuer evidence items retain dates, titles, and IDs.
  - `test_empty_rules_preserved`: Empty rule sets do not evaluate or fail constraint checks.
  - `test_benchmark_fund_treatment`: Benchmark fund (`BENCHMARK_FUND = "VT"`) is excluded from screening and preserved as benchmark baseline.
  - `test_missing_loss_tolerance_clarification`: When loss tolerance is missing, clarification prompt is returned instead of guessing risk tolerance.
  - `test_explicit_submission_of_new_cash`: New cash allocation requires explicit user form input; conversational arguments cannot fabricate cash.
- **AC 4 (V2 resource accounting & no hidden effort ceiling)**:
  - `test_shared_v2_budget_ledger_exhaustion`: All nested model calls route through `DeadlineTransport`, advancing the attempt counter on `TaskBudgetLedger` and cleanly raising `BudgetExhaustedError` upon exceeding attempts or deadline.
  - `test_no_hidden_medium_effort_ceiling`: Reasoned effort (`high`) is passed through without truncation to `medium` when V2 accounting is enabled.
- **AC 5 (Result panels, follow-up retrieval, decision saving, cancellation & rollback)**:
  - `test_follow_up_retrieval_and_decision_saving`: Follow-up tool calls retrieve the generated artifacts, and decisions persist in SQLite.
  - `test_cancellation_during_workflow_execution`: Cancellation during nested workflow dispatch terminates execution cleanly without orphan commits.
  - `test_original_engine_rollback`: When `TFS_ORCHESTRATOR_ENGINE=legacy`, workflows run through the legacy 8-turn orchestrator.
- **AC 6 (Financial & evidence parity against audit fixtures)**:
  - `test_financial_parity_exposure_review` and `test_financial_parity_new_cash_allocation`: Validated against existing fixtures in `test_analysis.py` and `test_allocation.py`. Exact numerical figures, recommendation values, and constraint evaluations match baseline fixtures.

### Test Suite Execution
- Focused test suite:
  - `tests/test_exposure_and_new_cash_workflows.py`: 15/15 passed.
  - `tests/test_workflow_tools.py`: 47/47 passed under default V2; 47/47 passed under `TFS_ORCHESTRATOR_ENGINE=legacy`.
- Cross-cutting conversation suites:
  - `tests/test_conversation_execution.py`: 9/9 passed.
  - `tests/test_interruption_and_proposal_safety.py`: 14/14 passed.
  - `tests/test_long_context_and_model_budgets.py`: 9/9 passed.
- Nearby financial & allocation regression suites:
  - `tests/test_analysis.py`: 57/57 passed.
  - `tests/test_allocation.py`: 37/37 passed.
- Static analysis:
  - `ruff check`: All checks passed.
  - `mypy analyst`: Success (0 issues across 37 source files).
