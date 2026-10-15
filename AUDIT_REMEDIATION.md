# Audit remediation

Tracks fixes for the findings in `AUDIT.md` (2026-10-15). `AUDIT.md` stays unchanged. One section is added per phase.

A finding is marked **Fixed** only after its acceptance tests pass. **Open** means not yet addressed. **Deferred** means intentionally left for a later phase.

## Tracking

| Finding | Summary | Status | Phase |
|---|---|---|---|
| P0-1 | Chat model cannot see workflow results or larger portfolio reviews | **Fixed** | 1 |
| P1-1 | `max_output_tokens` failures in chat: no effort cap, no retry, raw diagnostics | Open | 2 (B + C) |
| P1-2 | Failures inside chat workflows are mislabeled or hidden | Open | 2 (B) |
| P1-3 | Raw JSON and internal steps in the conversation | Open | 3–4 (D) |
| P1-4 | Portfolio-update statements trigger the wrong workflow | Open | 5 (E) |
| P2-1 | Proposal contract blocks common updates | Open | 5 (E) |
| P2-2 | Context waste and tool sprawl in chat | Open (result size reduced in Phase 1) | 6 (F) |
| P2-3 | Two orchestration stacks | Deferred | 6 (F) |
| P2-4 | Empty final answer completes the run | Open | 3 (D) |
| P3-a | (first P3 item) 409 "Finish or cancel current AI work" message does not explain the cause | Open | 2 (B) |
| P3-b | (second P3 item) `/api/analyze` client and server deadlines are both 600 s | Open | 2 (B) |

## Phase 1: Model-facing result contract (fix group A)

### P0-1: Chat model cannot see workflow results or larger portfolio reviews

**Status:** Fixed.

**Root cause.** Tools returned the full artifact envelope to the chat model. `projection="model"` only removed private fields, so it did not reduce size. When the serialized output exceeded `CHARACTER_LIMIT` (32,000 characters), `orchestrator.py` replaced the whole result with `"Result exceeds retrieval budget. Request a smaller slice."`. No tool accepted a slice argument. `get_artifact` returned the same full envelope and failed the same way. A model that retried the identical call looped until the eight-turn limit.

**Technical decision and rationale.**
- The stored artifact is unchanged. Persistence, `/api/artifacts/{id}` and the UI still get the full payload. Only the model-facing view changes.
- The existing `projection="model"` payload is the input to the new view, so private fields are still removed first.
- A large `analysis_result` or `calculation_result` (over 12,000 characters) gets a deterministic summary. Smaller results stay in full, because they fit and are clearer. The summary:
  - keeps every scalar field: recommendation, action, amount, valuation, totals, cash, dates, guardrail status, qualifications and evidence IDs;
  - replaces model-written case inputs and step-by-step paths (`judgment`, `judgments`, `path`, `components`, `contributing_funds`) with a pointer to their section;
  - replaces per-holding rows with the count, the top 10 rows by weight, and the IDs of rows with issues or unresolved identity;
  - caps other lists at 20 items, ranking breaches first, then unknown checks, then the largest weight. Each capped list records its total count and section path.
- `get_artifact` takes optional `section` (dotted path), `offset` and `limit` arguments. A follow-up question can read one section, or one page of a long list, from the full model projection.
- The orchestrator still applies the budget to the complete serialized `function_call_output` item, measured as it is sent. An oversized result is no longer replaced by an error. Instead, whole subtrees are replaced by pointers (`{"omitted": path, "chars": n, ...}`) until the result fits. Each pointer path can be passed back to `get_artifact` as a `section`. The 32,000-character limit is unchanged, and no JSON text is truncated.
- A call that returned an error, or that had to be cut to fit, is recorded by tool name and arguments. If the model repeats that identical call, the run stops with a clear error instead of looping until the eight-turn limit. Timeouts are not recorded, so a transient failure can still be retried.

**Implementation and changed files.**
- `backend/analyst/model_view.py` (new): `summarize`, `select`, `fit`, `outline` and `model_envelope`.
- `backend/analyst/financial_tools.py`: `FinancialTools.store` returns `model_envelope(...)`. Workflow tools also store through this method, so workflow results are covered too.
- `backend/analyst/context.py`: `ArtifactArguments` gains the nullable `section`, `offset` and `limit` arguments. `read_artifact` returns the summary or the requested section. The tool description says how to use them.
- `backend/analyst/orchestrator.py`: an oversized result is fitted instead of replaced. The error text no longer suggests a slice that does not exist. Identical repeats of limited calls are stopped.
- `backend/tests/test_context_retrieval.py`, `backend/tests/test_workflow_tools.py`: acceptance tests (below). The existing oversized-read test now expects a pointer instead of an error.

**Compatibility.**
- The stored payloads, `/api/artifacts/{id}` (human and model projections), message and run records, and `ToolOutcome` are unchanged.
- The `get_artifact` schema adds three nullable arguments. Calls that send only `artifact_id` still validate.
- `frontend/tests/thread_artifacts.spec.ts` only asserts that "retrieval budget" is absent from the thread, which still holds.

**Targeted tests and results.**

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. Stock analysis gives meaningful model-facing results within the budget | `test_workflow_tools.py::test_complete_workflows_preserve_typed_results_and_explicit_inputs` (stock, new cash, reunderwriting, theme). The output the chat model sees has no error, has nothing cut to fit, matches the stored `recommendation` and stock `valuation`, and keeps the request within 32,000 characters. | Pass |
| 2. 50-holding portfolio review gives a bounded summary and keeps the full artifact | `test_context_retrieval.py::test_fifty_holding_review_is_summarized_and_details_are_retrievable`. The full artifact has 51 rows and is over 32,000 characters. The model receives totals, cash, a position count, the top 10 rows and all guardrail breaches ranked first. Private costs are absent. | Pass |
| 3. `get_artifact` retrieves useful details | The same test. A follow-up `get_artifact(section="result.positions", offset=40, limit=10)` returns those 10 full rows. | Pass |
| 4. Repeated retrieval failures stop with a clear error | `test_context_retrieval.py::test_repeated_oversized_retrieval_stops_with_a_clear_error`. The run fails after one repeat (2 model requests) with "The model repeated a retrieval that already failed or was cut to fit…". | Pass |
| 5. Storage, retrieval and UI contracts stay compatible | Regression run over `test_context_retrieval.py`, `test_workflow_tools.py`, `test_financial_tools.py`, `test_web_search.py`, `test_conversation.py`, `test_chat_proposals.py`, `test_run_lifecycle.py`, `test_research_tools.py` and `test_persistence.py`. | 237 passed |

Static checks: Ruff on `backend/analyst` and the changed tests passed. mypy on the four changed modules passed.

**Remaining risks and deferred work.**
- The summary thresholds (12,000-character summary trigger, top 10 positions, 20-item list cap) are heuristics. They hold for the fixtures and 50 holdings. A very large theme exploration with many stocks may still need fitting, which omits sections behind pointers rather than failing.
- A single string longer than the budget (for example, one huge excerpt) cannot be split. Reading it returns a clear error, and repeating the read stops the run.
- `get_artifact` steps still add duplicate result cards in the UI (P1-3, item 3). This is left for fix group D.
- Tool schemas still take about 20,000 characters per turn (P2-2). This is left for fix group F.

## Recommended next phase

Phase 2: fix groups B and C. Add one `describe_failure` mapping for the orchestrator tool handler, the outer orchestrator catch and `/api/analyze`. Move the pipeline's effort cap and its single lower-effort retry on `max_output_tokens` into the chat loop. This addresses P1-1, P1-2 and P3.
