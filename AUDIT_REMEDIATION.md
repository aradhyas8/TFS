# Audit remediation

Tracks fixes for the findings in `AUDIT.md` (2026-10-15). `AUDIT.md` stays unchanged. One section is added per phase.

A finding is marked **Fixed** only after its acceptance tests pass. **Open** means not yet addressed. **Deferred** means intentionally left for a later phase.

## Tracking

| Finding | Summary | Status | Phase |
|---|---|---|---|
| P0-1 | Chat model cannot see workflow results or larger portfolio reviews | **Fixed** | 1 |
| P1-1 | `max_output_tokens` failures in chat: no effort cap, no retry, raw diagnostics | **Fixed** | 2 |
| P1-2 | Failures inside chat workflows are mislabeled or hidden | **Fixed** | 2 |
| P1-3 | Raw JSON and internal steps in the conversation | Open | 3–4 (D) |
| P1-4 | Portfolio-update statements trigger the wrong workflow | Open | 5 (E) |
| P2-1 | Proposal contract blocks common updates | Open | 5 (E) |
| P2-2 | Context waste and tool sprawl in chat | Open (result size reduced in Phase 1) | 6 (F) |
| P2-3 | Two orchestration stacks | Deferred | 6 (F) |
| P2-4 | Empty final answer completes the run | Open | 3 (D) |
| P3-a | (first P3 item) 409 "Finish or cancel current AI work" message does not explain the cause | Open | Unassigned (outside groups B and C) |
| P3-b | (second P3 item) `/api/analyze` client and server deadlines are both 600 s | Open | 6 (F; ends when `/api/analyze` is retired) |

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

## Phase 2: Error handling and model reliability (fix groups B and C)

Shared decisions for both findings:
- `backend/analyst/failures.py` (new) has one function, `describe_failure(exc) -> Failure(kind, message, retryable)`. Its kinds are `incomplete`, `invalid_output`, `rate_limit`, `plan_limit`, `timeout`, `unavailable`, `model_service` and `internal`. `Failure.status` gives the HTTP status for `/api/analyze`. It reuses the existing exception types (`ModelIncomplete`, `ModelServiceError`, `InvalidReview`, `PlanError`, OpenAI SDK errors) and adds no new exception classes.
- `ModelServiceError` gains a `kind` attribute (`rate_limit`, `timeout` or `model_service`). `openrouter_failure` and the transport timeout set it, so rate limits are classified by type, not by matching message text.
- The transport now raises `ModelIncomplete` with a safe message that names only the stop reason. It still logs the full token diagnostics, as it did before.
- Rate and plan limits keep HTTP 502 on `/api/analyze`. This preserves the existing contract, and the browser only shows `detail`.

### P1-1: `max_output_tokens` failures in chat

**Status:** Fixed.

**Root cause.** The chat loop sent the configured reasoning effort (up to `high` or `max`) on every turn, without the pipeline's `medium` cap for tool turns or its single lower-effort retry. An incomplete response failed the run with `"Model service error: " + str(exc)`. That string carried token counts, `max_output_tokens` and partial tool names from the transport.

**Technical decision and rationale.**
- Every chat turn offers tools, so `Orchestrator._call_transport` caps each turn with the pipeline's own `bounded_effort(configured, "medium")`. An unset effort stays unset, as it does in the pipeline, so OpenRouter models without configurable reasoning still work. Hosted web-search turns use the same helper and get the same cap.
- On `ModelIncomplete` with reason `max_output_tokens` (or no reason given), the turn is retried once at `bounded_effort(effort, "low")`, within the same request timeout. This uses the pipeline's retry rule. There is no retry when the effort cannot be lowered or the stop has another reason, such as `content_filter`. The retry happens before any tool call in that turn runs, so a successful tool or proposal step is never repeated.
- If the retry fails too, the run fails with the `describe_failure` message: the stop reason plus "The model ran out of output space before it finished. Try again, or lower the reasoning effort in AI connection." Token counts appear only in the log.
- Pipeline behavior is unchanged. The pipeline already had its own cap and retry, and only the wording of the transport's exception message changed.

**Files changed.** `backend/analyst/orchestrator.py`, `backend/analyst/providers.py`, `backend/analyst/failures.py`; tests in `backend/tests/test_conversation.py` and `backend/tests/test_openai_boundary.py`.

**Tests and results.**

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. A chat response at `max_output_tokens` retries once at lower effort | `test_conversation.py::test_chat_incomplete_turn_retries_once_at_lower_effort_without_repeating_tools`. With effort `high`, the requests are sent at `medium`, `medium`, then `low`. The run completes, and the earlier `get_holdings` step runs once. | Pass |
| 2. Two failures give a clear message without token diagnostics | `test_conversation.py::test_chat_incomplete_twice_fails_with_a_clear_message`. Exactly two attempts (`medium`, then `low`). The run error explains the cause and contains no `tokens=` and no "Model service error". | Pass |
| 5. Tool-selection turns use the expected cap | Both tests above (`high` is sent as `medium`). Existing pipeline cap and retry tests in `test_stock_openrouter_e2e.py` still pass. | Pass |
| Diagnostics are preserved in logs | `test_openai_boundary.py::test_incomplete_response_detailed_diagnostics`, now asserted on the log, with the exception message reduced to the stop reason. | Pass |

**Remaining risks.**
- A final answer turn is also capped at `medium`, because the loop cannot know in advance whether a turn will call a tool. A configured `high` effort now affects only nested workflow synthesis.
- The retry is once per turn, not once per run. A run that is repeatedly out of space can use up to two requests per turn within its 8 turns and 600-second deadline.

### P1-2: Failures inside chat workflows are mislabeled or hidden

**Status:** Fixed.

**Root cause.** The tool handler caught only `TimeoutError` and `ValueError`. `ModelIncomplete` (a `ValueError`) became "Requested data is unavailable to this run.". `InvalidReview`, `ModelServiceError` and `PlanError` escaped to the outer handler, which failed the whole run with "Internal orchestration error.", hiding rate and plan limits. `/api/analyze` mapped every non-deadline `TimeoutError`, `ValueError` and `OSError` to "Backend financial source configuration is invalid.".

**Technical decision and rationale.**
- The tool handler catches `InvalidReview`, `OpenAIError` and `ValueError`, then returns `{"error": message, "retryable": bool}` from `describe_failure`. The model sees the real category and whether retrying can help. The exception and its kind are logged. The call is also recorded for the Phase 1 repeat stop, so an identical retry of a failed workflow ends the run instead of spending turns.
- A failed workflow is never shown as a success. Its tool outcome stays `error`, it adds no artifact, and the final answer always ends with "<Workflow> did not complete: <message>" unless the same workflow later succeeds in the run. This follows the existing pattern that appends the web-search-unavailable note.
- The outer orchestrator catch now uses `describe_failure` too. A truly unknown exception still reports "Internal orchestration error.", and the traceback is logged.
- In `/api/analyze`, the configuration message now applies only when constructing the financial source raises `ValueError` or `OSError`. Everything else goes through `describe_failure`: a non-deadline timeout is a 504 timeout, a source `OSError`, `httpx` error or `SecUnavailable` is a 503 "Requested data is unavailable.", and model failures keep 502 with their real category. Logs keep the type, status, request ID and detail.

**Files changed.** `backend/analyst/orchestrator.py`, `backend/analyst/api.py`, `backend/analyst/failures.py`, `backend/analyst/providers.py`; tests in `backend/tests/test_workflow_tools.py` and `backend/tests/test_analysis.py`.

**Tests and results.**

| Acceptance criterion | Test | Result |
|---|---|---|
| 3. Nested `ModelIncomplete`, `InvalidReview` and rate-limit errors report their real category | `test_workflow_tools.py::test_nested_model_failures_keep_their_category` (three cases). The outcome error names "ran out of output space", "did not produce a valid result" or "rate limit reached", never "unavailable". The run completes with no artifact, and the answer ends with "Exposure review did not complete: …". | Pass |
| 4. `/api/analyze` no longer mislabels unrelated failures | `test_analysis.py::test_analyze_reports_the_real_failure_not_a_configuration_error`. A financial `TimeoutError` gives 504 "timed out", and a `ConnectionResetError` gives 503 "Requested data is unavailable.". Neither mentions configuration, and neither leaks the exception text. The existing 503 configuration test still passes. | Pass |
| 6. Existing workflows and Phase 1 summaries stay compatible | Regression run over `test_analysis.py`, `test_openai_boundary.py`, `test_chatgpt_plan.py`, `test_stock_openrouter_e2e.py`, `test_model_transport.py`, `test_conversation.py`, `test_workflow_tools.py`, `test_context_retrieval.py`, `test_run_lifecycle.py`, `test_web_search.py`, `test_financial_tools.py` and `test_chat_proposals.py`. This includes all Phase 1 acceptance tests. | 363 passed |

Static checks: Ruff on `backend/analyst` and `backend/tests` passed. mypy on `api.py`, `orchestrator.py`, `failures.py` and `providers.py` passed.

**Remaining risks.**
- `/api/analyze` now shows the safe incomplete message, which names the step and reason, plus advice. It no longer shows the transport's raw text, which held token counts. Its 502 status is unchanged.
- The chat tool error for a plain `ValueError` from a read tool is now "Requested data is unavailable." (previously it ended with "to this run"). No test or UI code depends on the old text.
- The browser renders `run.error` verbatim (`Thread.tsx:635`). The text is now safe, but the UI presentation is unchanged. That is left for fix group D.
- P3-a (the 409 connection-change message) and P3-b (equal 600-second deadlines) are not addressed and stay Open.

## Recommended next phase

Phase 3: fix group D, backend part. Leave tool-step messages out of `RunContext.history`. Record in a step's `artifact_ids` only the artifacts that the step created. Treat an empty final answer, or one shaped like a tool call, as invalid: retry once, then fail clearly. This addresses P2-4 and the backend part of P1-3.
