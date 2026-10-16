# Baseline Evaluation Report: TFS Stock Analysis & Harness Invariants

**Authority:** [V2 specification](../../specs/tfs-agent-harness-v2.md) | [Ticket 01: Establish the evaluation baseline and invariants](../issues/01-evaluation-baseline.md)  
**Status:** Baseline Established  
**Date:** 2026-10-16  

---

## 1. Overview & Purpose

This evaluation baseline provides a reproducible, executable standard of current TFS stock-analysis execution, follow-up retrieval, and execution safety invariants. It establishes the benchmark against which PydanticAI (Harness V2) will be evaluated in Tickets 02, 03, and 04.

All comparisons use mocked HTTP and scripted model providers; no live model API calls or external network requests are made.

---

## 2. Repeatable Verification Commands

The baseline suite and nearby invariant regression tests can be run with the following repeatable commands:

```bash
# 1. Execute the dedicated evaluation baseline suite
pytest backend/tests/test_evaluation_baseline.py -v

# 2. Run existing invariant regression suites (cancellation, duplicates, proposals, stale confirmation)
pytest backend/tests/test_run_lifecycle.py backend/tests/test_chat_proposals.py backend/tests/test_portfolio_actions.py -k "test_stop_cancels or test_duplicate_submission or test_repeated_calls_and_requests_reuse_one_pending_preview or test_duplicate_confirmation or test_stale_base_revision" -v

# 3. Run stock workflow regression tests
pytest backend/tests/test_workflow_tools.py -k "test_complete_workflows_preserve_typed_results_and_explicit_inputs and run_stock_analysis" -v

# 4. Static checks (Ruff and mypy on changed/tested files)
ruff check backend/tests/test_evaluation_baseline.py
mypy backend/analyst
```

---

## 3. Fixture Matrix & Baseline Scenarios

The baseline covers six core execution scenarios defined in `backend/tests/test_evaluation_baseline.py`:

| Scenario ID | Test Case | Fixture / Inputs | Semantic Financial Result / Expected Invariant |
|---|---|---|---|
| **SC-01** | `test_baseline_dated_stock_analysis` | `stock_request()` (ACME, 10 shares USD @ 100, 5 shares CAD @ 200, cash CAD 650, cash USD 500, FX 1.3), `analysis_date="2026-09-30"` | Total: 3600 CAD, Holdings: 2300 CAD, Cash: 1300 CAD. Direct Acme: 2300 CAD (63.89%). Terminal prices: downside 50, base 100 (exit multiple 16.11, terminal value 1300), upside 150. Recommendation: `hold`, evidence `["filing", "issuer"]`. Stored artifact `basis="committed"`, `revision=1`, `source_artifact_id="art-rev-1"`. Visible answer matches `recommendation.reason`. |
| **SC-02** | `test_baseline_undated_stock_analysis` | `stock_request()` with current date (`as_of=today`), `analysis_date` omitted in `workflow_input` | Undated analysis resolves current date (`market_today`). Matches dated financial calculations: terminal prices 50, 100, 150, exit multiple 16.11, action `hold`. |
| **SC-03** | `test_baseline_missing_evidence_stock_analysis` | `research_fixture()` with `revenue` fact `value=None` | Terminal prices remain `None` for all cases. Recommendation falls back to `wait_for_inputs`, amount `None`. No unvalidated financial values are invented. |
| **SC-04** | `test_baseline_bound_revision_stock_analysis` | Run starts on revision 1 (10 shares). Concurrent commit advances DB to revision 2 (25 shares) during execution. | Run retains `bound_revision=1`. Stored artifact records `revision=1`, `basis="committed"`. Terminal value reflects 10 shares (1300 CAD), not 25 shares. Subsequent runs bind revision 2. |
| **SC-05** | `test_baseline_hypothetical_snapshot_stock_analysis` | Draft portfolio artifact with 99 shares (`hypothetical_artifact_id=draft_id`). | Run outcome carries `hypothetical_artifact_id`. Artifact reference carries `basis="hypothetical"`, `revision=None`, `source_artifact_id=draft_id`. Committed DB remains untouched at revision 1 (10 shares). |
| **SC-06** | `test_baseline_artifact_section_followup` | Follow-up turn in thread requesting `get_artifact(art_id, section="stock.cases")`. | Model receives `stock.cases` slice from model-safe projection. Private costs (`average_costs`) and continuation tokens are strictly redacted. |

Canonical baseline outputs are serialized in `.scratch/tfs-agent-harness-v2/baseline_data.json`.

---

## 4. Comparison Exclusions & Financial Parity Contract

To avoid false negatives from non-deterministic generation or runtime IDs, the evaluation baseline separates financial facts from metadata:

### Exact Semantic Financial Assertions (Must Match)
1. **Portfolio Totals & Positions:** `portfolio.total_value`, `portfolio.holdings_value`, `portfolio.cash_value`, `portfolio.direct_companies` (values and weights), position values and weights.
2. **Stock Cases & Research:** `stock.reporting_currency`, `stock.cases` (name, `terminal_price`, `sensitivity_prices`, `required_exit_multiple`, `known_terminal_value`), research facts (metric, value, unit, currency, period_end).
3. **Recommendation Contract:** `recommendation.preferred_action`, `recommendation.amount`, `recommendation.evidence_ids`.
4. **Artifact Contract:** `reference.basis` (`committed` vs `hypothetical`), `reference.revision`.
5. **Visible Message Contract:** Direct recommendation answer from workflow (`messages[2].text == recommendation.reason` under Phase 7 direct dispatch).

### Comparison Exclusions (Ignored for Parity)
- **Generated IDs:** `id`, `run_id`, `thread_id`, `artifact_id`, `client_request_id`, `call_id`.
- **Timestamps:** `as_of`, `created_at`, `updated_at`, `saved_at`, `reviewed_at`, `captured_at`.
- **Prose Variations:** Free-form LLM prose text in `reason`, `downside`, `uncertainty`, `assumptions`, `what_could_change`, provided the underlying semantic action and numbers match.

The comparison helper `assert_semantic_financial_parity(actual, baseline)` in `backend/tests/test_evaluation_baseline.py` programmatically enforces this distinction.

---

## 5. System Invariants & Existing Tests

Rather than duplicating tests, Ticket 01 establishes and verifies system invariants using existing regression suites:

### Cancellation
- **Expectation:** Cancelling a run stops execution immediately. Nested provider operations abort, late assistant messages are dropped, and no unconfirmed proposals or portfolio modifications are saved.
- **Authoritative Tests:**
  - `backend/tests/test_run_lifecycle.py::test_stop_cancels_the_run_and_nothing_is_answered_for_it`
  - `backend/tests/test_run_lifecycle.py::test_stop_landing_as_the_provider_answers_stores_no_late_answer`
  - `backend/tests/test_run_lifecycle.py::test_stopped_run_cannot_prepare_a_portfolio_change`
  - `backend/tests/test_workflow_tools.py::test_cancellation_closes_nested_work_without_publishing_results`
  - `backend/tests/test_workflow_tools.py::test_late_nested_answer_cannot_publish_after_cancellation`

### Duplicate Submission
- **Expectation:** Submitting an identical `client_request_id` within a thread returns HTTP 202 with the existing run (`is_new=False`) idempotently, without re-executing model turns.
- **Authoritative Tests:**
  - `backend/tests/test_conversation.py::test_duplicate_submission_with_identical_payload_returns_existing_run`
  - `backend/tests/test_run_lifecycle.py::test_restart_shows_the_run_as_interrupted_and_never_replays_it`

### Proposal Deduplication
- **Expectation:** Multiple calls to `prepare_portfolio_proposal` with identical edits in chat return `status="already_pending"` and reuse the existing pending proposal ID.
- **Authoritative Tests:**
  - `backend/tests/test_chat_proposals.py::test_repeated_calls_and_requests_reuse_one_pending_preview`
  - `backend/tests/test_evaluation_baseline.py::test_baseline_invariants_proposal_deduplication_and_stale_confirmation`

### Duplicate Confirmation & Stale Confirmation
- **Expectation:** Confirming an already-confirmed proposal returns the exact same receipt idempotently. Confirming a proposal whose `base_revision` does not match the committed database revision returns HTTP 409 Conflict.
- **Authoritative Tests:**
  - `backend/tests/test_portfolio_actions.py::test_duplicate_confirmation_returns_same_receipt`
  - `backend/tests/test_portfolio_actions.py::test_stale_base_revision_returns_409`
  - `backend/tests/test_chat_proposals.py::test_stale_base_conflicts_and_only_a_new_run_gets_a_fresh_preview`

---

## 6. Provider Settings, Limits & Credential Ownership

| Provider / Seam | Transport / Protocol | Settings & Limits | Continuation & Privacy Semantics | Credential Ownership | Framework Status |
|---|---|---|---|---|---|
| **OpenRouter** | HTTP / REST (`OpenAIResponseTransport`) | `base_url="https://openrouter.ai/api/v1"`, models (e.g. `openai/gpt-4o`, free models), `reasoning_effort` (`low`/`medium`/`high`), `request_timeout` (5s test, 60s/120s prod), `CHARACTER_LIMIT` (32,000 chars). | Multi-turn tool loop using `function_call` / `function_call_output` messages linked by `call_id`. Large artifacts summarized (threshold 12,000 chars) or fitted with section pointers. Private state (`average_costs`, reasoning continuation) redacted from model projection. | TFS owns API keys server-side in encrypted local configuration. Framework must not manage credentials. | **Documented support only; compatibility unverified** until mocked tests (Ticket 02) and approved live tests (Ticket 10) pass. |
| **ChatGPT-plan** | OAuth PKCE / Internal transport | DPAPI/Keychain encrypted storage in OS keyring. Refresh token mutex. Request timeouts capped by task deadline. | Reasoning continuation tokens (`encrypted_content`) preserved across tool turns within the active run, but excluded from stored public dialogue and artifact projections. | TFS owns OAuth token storage, refresh locks, and rotation. Framework must not race token refresh. | **Documented candidate only; compatibility unverified** until mocked tests (Ticket 03) and approved live tests (Ticket 10) pass. |

---

## 7. Evaluation Bounds & Shared Task Budget

### Per-Request Bounds
- **HTTP Request Timeout:** 5.0 s (test fixtures), 60.0 s (default production), 120.0 s (research runs).
- **Turn Envelope Limit:** Max 32,000 characters per message / tool result (`CHARACTER_LIMIT`).
- **Reasoning Effort Bounds:** Tool-calling turns capped at `medium` effort; single retry at `low` effort on `max_output_tokens`.

### Shared Task Budget (Ticket 04 Minimum Proof)
- **Model Attempt Allowance:** Initial justified default of **10 model attempts** per user request.
  - *Justification:* Standard stock analysis requires 6 turns (`review_portfolio`, `get_sec_filings`, `get_issuer_material`, `calculate_company_cases`, `calculate_comparison`, `final_answer`). An allowance of 10 provides headroom for up to 2 premature-answer reprompts and 2 lower-effort retries without allowing unbounded loops.
- **Wall-Clock Deadline:** Absolute ceiling of 180 s capping all attempts and tool timeouts.
- **Exhaustion Behavior:** Dispatched attempts consume allowance before dispatch. Failed attempts consume slots. Exhaustion halts execution and returns an honest `incomplete` / HTTP 502 status; unfinished work is never presented as completed analysis.
- **Unknown Pricing:** When model token costs or pricing metadata are unavailable upstream, cost is recorded as `unknown` (never assumed as $0.00). Attempt and deadline bounds remain authoritative.

---

## 8. Preservation of Production Behavior & Phase 7 Direct Dispatch

1. **Direct Workflow Dispatch:** Desk forms submitted with `workflow_input` continue to dispatch directly to `run_stock_analysis` without conversational outer model turns.
2. **Direct Answer Contract:** The workflow's validated recommendation summary (`recommendation.reason`) is used directly as the final assistant message, completing in 2 orchestration turns with zero extra outer model calls.
3. **No Production Refactoring:** Production runtime code in `backend/analyst/` remains unmodified. The evaluation baseline is purely additive.

---

## 9. Conclusion & Readiness

The evaluation baseline and invariants are established and verified. Tickets 02 (OpenRouter Conformance) and 03 (ChatGPT Sign-in Conformance) are unblocked and may proceed.
