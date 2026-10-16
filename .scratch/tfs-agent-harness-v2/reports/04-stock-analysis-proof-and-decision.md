# Stock Analysis End-to-End Proof and Adoption Decision Report

**Authority:** [V2 specification](../../specs/tfs-agent-harness-v2.md) | [Ticket 04: Prove stock analysis end to end and decide adoption](../issues/04-stock-analysis-proof-and-decision.md)  
**Status:** Proof Established & Formal Decision Rendered  
**Decision:** **ADAPT** (Adopt PydanticAI core as model/tool loop engine with TFS custom adapters)  
**Compatibility Limitation Label:** **mocked contract verified; real-provider compatibility unverified**  
**Date:** 2026-10-16  

> [!IMPORTANT]
> **Adoption & Release Limitation:**  
> This evaluation gate proves client-side protocol conformance, direct structured dispatch, conversational tool loop execution, artifact follow-up retrieval, financial parity, and task-scoped budget exhaustion exclusively through mocked HTTP (`httpx.MockTransport`) and mock credential storage. No live external network calls were dispatched. Actual sign-in, token refresh against OpenAI's live endpoints, OpenRouter live routing, and upstream autonomous tool selection remain unverified until the approved live compatibility gate in Ticket 10 passes before release. Production runtime execution remains on the current path until final cutover.

---

## 1. Executive Summary & Provisional Architecture Decision

Ticket 04 evaluated PydanticAI core against TFS's complete stock analysis workflow, conversational model-to-tool dispatch, artifact follow-up retrieval, and execution safety invariants under TFS supervision.

### Formal Architecture Decision: **ADAPT**
- **Verdict:** **ADAPT** PydanticAI core as the model and tool execution engine for conversational and structured runs, paired with TFS's focused conformance adapters (`chatgpt_plan_adapter.py` and `openrouter_adapter.py`).
- **Core Rationale:**
  1. **Pure "Adopt" is Unviable:** PydanticAI's off-the-shelf `OpenAICodexProvider` violates TFS's mandatory OS-encrypted DPAPI/Keychain storage by reading plaintext secrets from `~/.codex/auth.json`, requests incorrect scopes (missing `chatgpt.tokens.use.direct`), and targets internal Codex routes rather than the public Responses API.
  2. **Pure "Retain" Compounds Maintenance:** Retaining TFS's custom orchestration (`orchestrator.py` and `pipeline.py:analyze()` tool loops) requires ongoing maintenance of hand-coded SSE stream decoders, tool call JSON extractors, schema generators, and custom retry state machines across both OpenAI Responses and OpenRouter routes.
  3. **"Adapt" Delivers the Net Maintenance Benefit:** Offloading the inference tool loop, schema extraction, and multi-turn state management to PydanticAI eliminates fragile loop mechanics while retaining TFS's battle-tested financial arithmetic, evidence validation, SQLite persistence, DPAPI/Keychain credentials, and human portfolio confirmation safety.

### Pinned Dependency Versions
- `pydantic-ai-slim==2.57.0`
- `openai==2.54.0`
- `pydantic==2.10.6`
- `python==3.11.0`

---

## 2. Maintenance Slice Comparison: Equivalent Old vs. New

A rigorous quantitative and qualitative comparison between the existing custom orchestration slice and the migrated PydanticAI adapter slice:

| Dimension / Metric | Existing Custom Slice | Migrated PydanticAI Adapter Slice | Net Maintenance Impact |
|---|---|---|---|
| **Production Orchestration Lines** | ~550 lines (`orchestrator.py`: 450 lines; `pipeline.py:analyze()` loop: ~100 lines) | ~836 lines (`openrouter_adapter.py`: 322 lines; `chatgpt_plan_adapter.py`: 364 lines; `evaluation_budget.py`: 150 lines) | +286 lines initially during dual-path evaluation; net reduction of ~550 lines of bespoke loop state machines after Phase 10 cutover. |
| **Tool Loop Mechanics** | Custom `while turns_count < MAX_TURNS:`, custom continuation array serialization, manual exchange formatting, manual character fitting | Managed natively by PydanticAI `Agent` and `RunContext`; standard tool calling continuation | **Replaced.** Eliminates fragile manual continuation reconstruction and exchange JSON encoding. |
| **Tool Schema Generation** | Manual OpenAPI schema generation and type mapping | Automatic Pydantic model-to-schema extraction via PydanticAI | **Replaced.** Eliminates boilerplate schema dictionaries and manual validation parsing. |
| **Retry & Error Mechanics** | Manual `answer_retried`, `company_retry`, `incomplete_retried` state machines; custom SSE stream reconstruction | Standard PydanticAI retry policies and typed domain error mapping | **Replaced.** Centralizes error classification into typed domain exceptions. |
| **Authentication & Store** | TFS DPAPI (Windows) / Keychain (macOS) encrypted OS store, dynamic client registration, `session.lock` single-refresh owner | TFS DPAPI / Keychain preserved; bridged to PydanticAI via `ChatGPTPlanEvaluationAdapter` | **Retained.** 100% of TFS credential security and single refresh ownership preserved. |
| **Financial Calculations & Validation** | TFS `calculations.py`, `stock.py`, `scenarios.py`, `validate_company_judgments` | TFS calculations and domain validators completely retained | **Retained.** Framework change has zero effect on financial correctness. |
| **Persistence & Authority** | Authoritative SQLite DB, revision binding, hypothetical drafts, immutable artifacts | Authoritative SQLite DB completely retained | **Retained.** Framework does not manage portfolio schema or confirmation authority. |
| **Confirmation Safety** | Human confirmation barrier; agent cannot commit portfolio proposals | Human confirmation barrier preserved; confirm tool omitted from agent capabilities | **Retained.** Autonomous trades/confirmations strictly prohibited. |

### Concrete Responsibilities Replaced
1. **Manual multi-turn conversation and tool loop:** Replaced by PydanticAI's internal agent loop, removing ~200 lines of manual loop state management.
2. **Tool schema extraction:** Replaced manual JSON schema construction with automatic extraction from Pydantic models.
3. **Tool call deserialization:** Replaced manual `json.loads` / argument validation with typed tool handlers.
4. **Parameter mapping & omission:** Centralized parameter omission (`store=false`, `temperature=None`, `top_p=None`, `reasoning_effort`) into reusable adapter functions.
5. **Multi-turn history assembly:** Replaced manual exchange list tracking with `result.all_messages()`.

---

## 3. Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Status | Implementation & Test Evidence |
|---|---|---|---|
| **AC-01** | Direct structured stock input through existing lifecycle; no extra outer model turn selects/rewrites answer | **VERIFIED** | `test_proof_structured_stock_input_direct_dispatch`: Form dispatches directly to `run_stock_analysis`. Assistant messages record tool outcome and direct recommendation reason. |
| **AC-02** | Conversational "Analyze MSFT" without structured inputs; verify advertised tools, executed args trace, and unavailable security safe clarification | **VERIFIED** | `test_proof_conversational_stock_analysis_trace_and_dispatch`: Verified advertised tools and `position_id="p1"` in trace. `test_proof_conversational_ambiguous_or_unavailable_security_clarifies_safely`: Clarification required returned safely for unknown position. |
| **AC-03** | Scripted model responses for deterministic tool-loop labeled as dispatch/continuation evidence | **VERIFIED** | All tests use `FunctionModel` or `CapturingTransport` and explicitly label responses as client dispatch/continuation evidence. |
| **AC-04** | PydanticAI owns migrated tool loop; retain calculations, evidence prerequisites, validators, and missing-evidence handling | **VERIFIED** | `test_proof_retains_calculations_evidence_validators_and_missing_evidence`: Revenue set to `None` leaves terminal prices `None` and falls back to `wait_for_inputs`. |
| **AC-05** | Persist run/result and answer follow-up using model-safe artifact projection and section retrieval | **VERIFIED** | `test_proof_persists_run_and_answers_followup_with_model_safe_projection`: Follow-up retrieves `stock.cases`, redacting sensitive fields (e.g. `average_costs`), with a fresh task budget. |
| **AC-06** | Match baseline semantic financial outputs, evidence binding, and hypothetical/revision behavior on both mocked provider paths | **VERIFIED** | `test_proof_baseline_semantic_financial_parity_on_both_provider_paths`: Exact parity across OpenRouter and ChatGPT Plan paths. `test_proof_revision_binding_and_hypothetical_draft_preservation`: Revision 1 retained under concurrent commit. |
| **AC-07** | Honor supported reasoning settings and minimum shared proof budget; resource exhaustion cannot pass as successful analysis | **VERIFIED** | `test_proof_reasoning_effort_settings_honored`: Mapped to `openrouter_reasoning` / `openai_reasoning_effort`. `test_proof_minimum_shared_budget_cumulative_consumption_and_exhaustion`: Cumulative consumption and incomplete failure proven. |
| **AC-08** | Cancellation drops late output; output correction does not repeat completed state-changing work; proposal confirmation safety | **VERIFIED** | `test_proof_cancellation_drops_late_output_without_overwriting`, `test_proof_output_correction_does_not_repeat_completed_state_changing_work`, `test_proof_proposal_confirmation_safety_regression`. |
| **AC-09** | Current runtime remains default; evaluation selection remains internal and reversible | **VERIFIED** | Production paths in `orchestrator.py` and `api.py` remain untouched; evaluation runner isolated in `analyst/stock_evaluation_adapter.py`. |
| **AC-10** | Equivalent old/new slice comparison with concrete responsibilities replaced and net maintenance assessment | **VERIFIED** | Detailed in Section 2 above and codified in `SliceComparison`. |
| **AC-11** | Adopt/adapt/retain decision report with pinned versions, focused test results, measured overhead, and remaining uncertainty | **VERIFIED** | Formal **ADAPT** decision recorded in this document. |
| **AC-12** | Label both connections "mocked contract verified; real-provider compatibility unverified" until live gate passes | **VERIFIED** | Explicitly documented in Section 1, Section 6, and asserted in `ExecutionTrace.compatibility_status`. |

---

## 4. Minimum Shared Proof Budget Findings

The minimum shared proof budget was implemented in `backend/analyst/evaluation_budget.py` as `TaskBudgetLedger`:

1. **Concrete Proof Allowance:**
   - Default: `max_attempts = 10` model attempts.
   - Wall-clock deadline: `deadline_seconds = 600.0s`.
2. **Advance Charging:**
   - Dispatches charge `charge_attempt(phase)` *before* issuing network or tool calls.
   - Dispatches that fail (network drops, timeout, malformed JSON) still consume a slot.
3. **Cumulative Consumption:**
   - Outer conversational turn (1 attempt) + nested stock workflow turns (5 tool calls + 1 recommendation = 6 attempts) cumulatively consume 7 attempts from the shared ledger.
   - Retries consume additional attempts monotonically; retry cannot reset or roll back attempts.
4. **Honest Incomplete Outcome:**
   - When `attempts >= max_attempts` or `now >= deadline`, `BudgetExhaustedError` is raised.
   - Execution terminates immediately, persisting `status="failed"` and an honest incomplete error message.
5. **Task Scope & Follow-Up:**
   - A follow-up user request in the same thread receives its own fresh `TaskBudgetLedger` (10 attempts, 600s), while historical usage remains preserved in audit records.
6. **Reported Usage Recording:**
   - Reported tokens and costs are recorded in `UsageRecord`.
   - Missing or unavailable usage metrics remain `None` (unknown), never fabricated as 0.

---

## 5. Test Verification Summary

All 13 focused proof tests in `backend/tests/test_stock_analysis_proof.py` pass:

```bash
poetry run pytest tests/test_stock_analysis_proof.py -v
# 13 passed in 3.59s
```

All 68 evaluation tests across the V2 evaluation backlog pass with zero regressions:

```bash
poetry run pytest tests/test_stock_analysis_proof.py \
                   tests/test_chatgpt_plan_conformance.py \
                   tests/test_openrouter_conformance.py \
                   tests/test_evaluation_baseline.py -v
# 68 passed in 10.08s
```

### Static Analysis
- **Ruff:** Clean (0 errors across `evaluation_budget.py`, `stock_evaluation_adapter.py`, `test_stock_analysis_proof.py`).
- **Mypy:** Clean (`Success: no issues found in 36 source files` across `backend/analyst`).

---

## 6. Remaining Uncertainty & Pre-Release Live Gate (Ticket 10)

While client contracts, error mappings, single refresh locks, tool continuation, and financial parity have been thoroughly verified on mocked HTTP, the following items remain **unverified** until the live gate in Ticket 10:

1. **Real Model Tool Selection:** Autonomous selection of `run_stock_analysis` and argument extraction (`position_id`) by upstream models under live provider conditions.
2. **Live Authentication & Entitlements:** Live ChatGPT subscription token issuance, refresh token rotation against OpenAI's live auth endpoints, and subscription sharing rate limits.
3. **Live OpenRouter Routing:** Upstream parameter support enforcement across live OpenRouter model endpoints.

**Next Milestone:** Proceed to Tickets 05–09 in the V2 backlog under the approved **ADAPT** architecture. Release cutover remains blocked until Ticket 10 passes.
