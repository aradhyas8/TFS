# Audit remediation

Tracks fixes for the findings in `AUDIT.md` (2026-10-15). `AUDIT.md` stays unchanged. One section is added per phase.

A finding is marked **Fixed** only after its acceptance tests pass. **Open** means not yet addressed. **Deferred** means intentionally left for a later phase.

## Tracking

| Finding | Summary | Status | Phase |
|---|---|---|---|
| P0-1 | Chat model cannot see workflow results or larger portfolio reviews | **Fixed** | 1 |
| P1-1 | `max_output_tokens` failures in chat: no effort cap, no retry, raw diagnostics | **Fixed** | 2 |
| P1-2 | Failures inside chat workflows are mislabeled or hidden | **Fixed** | 2 |
| P1-3 | Raw JSON and internal steps in the conversation | **Fixed** | 3 (backend), 4 (frontend) |
| P1-4 | Portfolio-update statements trigger the wrong workflow | **Fixed** | 5 (E) |
| P2-1 | Proposal contract blocks common updates | **Fixed** | 5 (E) |
| P2-2 | Context waste and tool sprawl in chat | **Fixed** | 1 (result size), 6 |
| P2-3 | Two orchestration stacks | Deferred | 6 (F) |
| P2-4 | Empty final answer completes the run | **Fixed** | 3 |
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

## Phase 3: Conversation state and response validation (fix group D, backend)

### P1-3: Raw JSON and internal steps in the conversation

**Status:** Partially fixed. Backend items 3 (duplicate cards) and 4 (history teaches the JSON format) are fixed. Frontend items 1 (`JsonViewer` results), 2 (every read shown as a step) and 5 (literal Markdown) remain for Phase 4.

**Root cause.**
- Each tool call is persisted as an assistant message with text `{"retrieval": {...}}`. `RunContext.history` sent those records back to the model as assistant turns, which taught weaker models to write tool calls as text.
- The orchestrator linked every artifact that a result mentioned (`reference`, `before_artifact`, `after_artifact`, `artifact_ids`) to the step and the run. So `get_artifact`, `get_holdings` and `get_proposal` re-linked artifacts they only read, and the UI rendered a second result card under each read.

**Technical decision and rationale.**
- Persistence is unchanged. Tool steps are still stored with their outcomes, so the UI activity and debugging keep them. Only the model-facing history changes, so no memory or summarization system is added.
- `RunContext.history` skips tool-step messages, meaning assistant messages with `tool_outcomes`. Their artifact IDs and proposals are still registered, through the new `remember` helper, so earlier results stay retrievable. They also stay listed in the system context's `artifact_references`.
- A run's answer carries that run's tool outcomes in its existing `completed_tools` field (name, status, error, created artifact IDs). Follow-up questions therefore still see which results exist and where they came from.
- Tool steps from a run with no answer in the window (for example a failed run) appear as a short `earlier_tool_outcomes` list in the system context (at most 20, with the hypothetical label kept). They are not sent as fake assistant turns.
- The tool-call and tool-output items of the active run are unchanged.
- A step links only the artifacts it created. The orchestrator takes a snapshot of the run's known artifact IDs before the handler runs and drops any linked ID that was already known. Created artifacts are added to that set by `FinancialTools.store`, web search and proposal preparation, so their links are unchanged.
- Artifact IDs, storage, retrieval and the Phase 1 summaries are unchanged.

**Implementation and changed files.**
- `backend/analyst/context.py`: `is_tool_step`, `RunContext.remember`, tool-step filtering in `history`, run outcomes in `message_content`, and `earlier_tool_outcomes` in `messages`.
- `backend/analyst/orchestrator.py`: links only created artifacts.
- `backend/tests/test_run_lifecycle.py`: the hypothetical-history test now expects user, answer, user (no tool step), with the answer still labeled hypothetical and carrying its `get_holdings` outcome.

**Remaining risks.**
- The 20-message history window still counts tool steps when reading from the database (P2-2). Dialogue is no longer crowded out of the content, but a run with many steps still uses window slots. Filtering in SQL would need a schema-aware query, so it is left for fix group F.
- `get_holdings` steps no longer link `art-rev-N`, because it was already known. Saved and hypothetical portfolio cards are still shown by their own UI paths.

### P2-4: Empty final answer completes the run

**Status:** Fixed.

**Root cause.** The final-text path stored any text, including an empty string or a tool call written as JSON, as the assistant answer and marked the run `completed`.

**Technical decision and rationale.**
- `valid_answer(text)` in `orchestrator.py` rejects only two cases:
  - empty or whitespace-only text;
  - text that is entirely a JSON object, or a list of objects, whose keys are all tool-call keys (`name`, `arguments`, `tool`, `function`, `retrieval`, `call_id`, …) and that includes a call-naming key. A single surrounding ```` ``` ```` fence is accepted around it.
- Prose, code blocks, JSON quoted inside prose, and data JSON such as `{"total_value": …}` or `{"name": "Acme", "weight": …}` are all accepted.
- An invalid answer is never stored. The first time, the same context is resent with one system instruction to reply in plain language or use the tool interface. Completed tool and proposal steps are not re-executed, because their outputs are already in that context.
- A second invalid answer fails the run with the Phase 2 mapping, `describe_failure(InvalidReview)`: "The model did not produce a valid result. Nothing was completed; try again." No answer message is stored, and the run is not marked completed.

**Implementation and changed files.** `backend/analyst/orchestrator.py` (`valid_answer`, `ANSWER_RETRY`, the retry in the run loop).

### Phase 3 tests and results

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. History holds real dialogue without retrieval records | `test_financial_tools.py::test_followup_reads_prior_result_without_tool_records_or_duplicate_links`: the follow-up history is user, assistant, user, with no `"retrieval"` text. `test_run_lifecycle.py::test_later_ordinary_run_sees_earlier_hypothetical_messages_labeled` (updated). | Pass |
| 2. Follow-ups can still reach prior results | The same follow-up test: the earlier answer carries `completed_tools` with the created artifact ID, the artifact is in `artifact_references`, and `get_artifact` returns its cash figure. `test_context_retrieval.py::test_oversized_historical_message_reference_still_excludes_private_fields`: an orphan tool step's outcome still reaches the model through `earlier_tool_outcomes`, with private fields removed. | Pass |
| 3. Current-run tool execution is unchanged | All existing `test_context_retrieval.py`, `test_workflow_tools.py` and `test_chat_proposals.py` cases. | Pass |
| 4. `get_artifact` creates no duplicate link | The same follow-up test: the read step and its outcome have empty `artifact_ids`, and the second run's `artifact_ids` is `[]`. | Pass |
| 5. An empty answer gets one recovery, then a clear failure | `test_conversation.py::test_invalid_final_answer_retries_once_without_repeating_tools[""]` and `::test_invalid_final_answer_twice_fails_clearly_and_is_never_stored[""]`. | Pass |
| 6. A tool-call-shaped answer gets the same treatment | The same two tests with `{"name":"get_holdings","arguments":{}}`, plus fenced `retrieval` JSON and list cases in `test_final_answer_validation_rejects_only_empty_or_tool_call_shaped_text`. | Pass |
| 7. Valid JSON or code is not rejected | `test_final_answer_validation_rejects_only_empty_or_tool_call_shaped_text`: data JSON, a Python block, JSON inside prose, and `{"name": "Acme", "weight": …}` all pass. | Pass |
| 8. Recovery repeats no tools | The retry test: exactly one tool outcome, and the earlier `function_call_output` is still present in the retried request. | Pass |
| 9. Phase 1 and Phase 2 behavior is unchanged | Regression run over `test_context_retrieval.py`, `test_conversation.py`, `test_workflow_tools.py`, `test_financial_tools.py`, `test_web_search.py`, `test_chat_proposals.py`, `test_run_lifecycle.py`, `test_research_tools.py`, `test_saved_run_replay.py`, `test_openai_boundary.py`, `test_analysis.py` and `test_stock_openrouter_e2e.py`. | 406 passed |

Static checks: Ruff on `backend/analyst` and `backend/tests` passed. mypy on `context.py` and `orchestrator.py` passed.

**Remaining risks.**
- The retry uses one of the 8 orchestration turns. An invalid answer on the last turn still ends at the turn limit, not at the validation message.
- When a run fails after its tools created artifacts, the message "Nothing was completed" understates what happened. The artifacts stay stored but no answer is shown. The UI wording is left for Phase 4.
- A reply that is only an object such as `{"name": "Acme"}` is treated as a tool call. A legitimate answer of that exact shape would get one retry.
- The browser still renders every tool step and uses `JsonViewer` for some kinds (P1-3 frontend items). The Playwright check in `thread_artifacts.spec.ts` for duplicate cards was not run in this backend-only phase.

## Phase 4: Chat UI and result presentation (fix group D, frontend)

### P1-3: Raw JSON and internal steps in the conversation (frontend items 1, 2 and 5)

**Status:** Fixed. Backend items 3 and 4 were fixed in Phase 3. Phase 4 fixes frontend items 1, 2 and 5, and all P1-3 acceptance checks pass.

**Root cause.**
- `Thread.tsx` rendered every artifact kind except analysis and web research through `JsonViewer` as an expanded tree.
- Each persisted tool step was drawn as its own conversation entry, including routine reads.
- Assistant answers were split into plain paragraphs (the "ponytail: plain paragraphs only" note), so `**`, `-` and `|` tables appeared literally.
- Threads stored before Phase 3 still link read artifacts to `get_artifact` steps, which drew duplicate cards.

**Technical decision and rationale.**
- **Grouped activity.**
  - Consecutive tool steps of one run render as one `Activity` entry: a status line with the meaningful steps ("Checked review portfolio", "Searched the web", "Prepared portfolio changes for your review").
  - Failures always appear in that line with their safe message.
  - Routine reads (`get_holdings`, `get_artifact`, `get_proposal`, `get_messages`) are left out of the line. When a run has only reads, the line says "Read saved data".
  - The full step-by-step list is in a native `<details>` ("N steps"). It is keyboard accessible and needs no new component, since Arc Free has no disclosure component.
  - Results, requests for inputs and proposals still appear in the conversation, under the step that created them.
  - A holdings read shows the run's own bound revision ("Portfolio revision N"). Phase 3 stopped linking `art-rev-N` to read steps, and this keeps the reference visible.
- **No duplicate cards.** A result card is drawn once, under the first step that stored it. A later read of the same artifact draws nothing, which also covers older threads stored before Phase 3.
- **Readable results.** These reuse the desk's `Row` and `bullets` memo pattern, the existing format helpers (`money`, `pct`, `fullDate`) and Arc `Alert`. The frontend never computes or rounds a value beyond formatting the stored string.
  - Portfolio review (`calculation_result` from `review_portfolio`):
    - total value, with the known value when incomplete; holdings value and cash;
    - valuation and holdings-confirmed dates;
    - a warning when not every holding has a usable value;
    - the sizing restriction when `source_inputs_usable` is false;
    - guardrail breaches as a danger alert, and unknown checks as a warning;
    - a holdings table (holding, account, value, weight, stored issues);
    - company and currency exposure, and all stored notes.
  - Checked changes (`check_proposed_changes`): the result status, the stored post-change totals, breaches and notes.
  - Stock analysis and other workflows already use the existing analysis memo and are unchanged.
  - Any other kind gets a readable fallback: scalar fields as rows, text lists as notes, nested records counted.
  - The exact stored values are still available under "Show stored values" (the same `JsonViewer`, collapsed by default).
- **Markdown.** `components/desk/Markdown.tsx` is a small renderer with no dependency. It supports headings, paragraphs, bold, italic, bullet and numbered lists, pipe tables, links and inline or fenced code.
  - It produces only React elements, never `dangerouslySetInnerHTML`, so model HTML shows as text and cannot run.
  - Links are drawn only for `http`/`https` addresses (the existing `webAddress` check) and open with `rel="noopener noreferrer"`.
  - An answer with placed citation numbers keeps its existing paragraph split and gets inline formatting only, because citations are placed at stored character offsets.
  - No Markdown dependency was added. None was installed, Arc has no renderer, and the needed subset is small.
- **Unchanged.** Loading, cancellation, run notices and proposal confirmation are untouched. `Message` lost only its now-unused tool-step branch and props.

**Changed files.**
- `frontend/components/desk/Thread.tsx`: `Activity`, `stepLine`, result ownership, `ReviewSummary`, `ChangeSummary`, `Breaches`, `Notes`, `Fields`, Markdown in answers.
- `frontend/components/desk/Markdown.tsx` (new).
- `frontend/components/desk/Thread.module.css`: Markdown, table, activity and stored-values styles, using Arc tokens.
- `frontend/tests/thread_artifacts.spec.ts`: the calculation test now checks the readable summary, a collapsed tree, and a read-back that adds no card. There is a new Markdown and HTML-safety test, and the responsive test now checks the summary region.
- `frontend/tests/conversation_desk.spec.ts`: the failed-answer test expects the Phase 2 safe message instead of the raw exception text. That spec was not run in Phase 2.
- `backend/tests/e2e_server.py` (browser fixture only): scripted "Recall the exposure result" (`get_artifact` on the earlier calculation) and a Markdown answer.

**Verification.** No live model calls. Playwright ran against the scripted e2e backend from an isolated copy of the frontend, because a developer `next dev` server was already running in the working folder. `tsc --noEmit` passed.

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. A portfolio review shows a readable summary, not a JSON tree | `thread_artifacts.spec.ts` "a direct calculation shows its stored values…": summary region with total, cash, holdings table and dates; no tree until "Show stored values" is opened | Pass |
| 2. Internal reads do not clutter the conversation | Same test (the read-back shows only "Read saved data"); `conversation_desk.spec.ts` holdings test | Pass |
| 3. Activity is grouped and inspectable | Same test: "1 step" opens the "Analyst steps" list with "Checked artifact" | Pass |
| 4. `get_artifact` adds no duplicate card | Same test: still exactly one "Portfolio exposure and guardrails" card after the read-back | Pass |
| 5. Markdown renders, including tables | New "an answer's Markdown is drawn…" test: heading, bold, italic, link, table headers and rows, list, inline and block code; no literal `**`; `<script>` and `<img onerror>` show as text and never run | Pass |
| 6. Warnings, dates and incomplete data stay visible | Calculation test: "Holdings last confirmed Sep 30, 2026" and the "Not every holding has a usable value" warning | Pass |
| 7. Loading, cancellation and error states still work | `conversation_desk.spec.ts` (13 tests), `run_lifecycle.spec.ts`; the missing (404) and failed (503) artifact states in the calculation test | Pass |
| 8. Proposal confirmation is unchanged | `thread_artifacts.spec.ts` proposal tests, `identity_and_proposals.spec.ts`, `proposal_review.spec.ts` | Pass |

Totals: `thread_artifacts.spec.ts` and `conversation_desk.spec.ts` 32 passed; `identity_and_proposals.spec.ts`, `run_lifecycle.spec.ts` and `proposal_review.spec.ts` 29 passed.

**Remaining risks.**
- The Markdown renderer covers one list level and has no block quotes, images or nested tables. A cited answer gets inline formatting only, so its tables or lists show as text.
- Checked-change totals (`post_total_value`, `post_cash_value`) are shown as stored strings, without a currency symbol, because the stored `ProposalReview` does not carry its currency.
- A very large portfolio review draws every holding in the table. It scrolls inside the card, but it is long.
- The other desk specs (screenshots, workflow desks) were not run. They do not render `Thread`'s tool steps or artifact fallback.

## Phase 5: Portfolio-update intent and proposal actions (fix group E)

### P1-4: Portfolio-update statements trigger the wrong workflow

**Status:** Fixed.

**Root cause.** `isConversation` sent input to chat only when `HOLDINGS_EDIT` matched, and that pattern accepted only imperative verbs at the start ("set", "update" and similar). Statements such as "I bought 5 more MSFT" or "I now hold 15 shares of MSFT" fell through to ticker matching in `route()` and opened a paid stock analysis through `/api/analyze`.

**Technical decision and rationale.**
- A new `HOLDINGS_UPDATE` pattern in `route.ts` matches statements that the holdings already changed. `isConversation` checks it, together with `HOLDINGS_EDIT`, before `route()` runs, so ticker matching never sees these statements.
  - Relative changes: "I bought", "I've just bought", "purchased", "sold", "trimmed", and the same verbs without a subject ("Bought 5 ACME today").
  - Absolute totals: "I now hold", "I currently have", "I own 12 shares".
  - Cash: "deposited", "withdrew", "transferred".
- `HOLDINGS_EDIT` also accepts the imperatives "increase", "decrease", "reduce", "add" and "subtract" when the input names shares, a position, holdings or the portfolio. This covers "Increase my MSFT position by 5 shares".
- The pattern is anchored at the start of the input. Hypotheticals ("What if I bought 5 more MSFT?", "If I sold 3 shares…") and questions ("Should I buy more MSFT?") keep their existing routes. "I have C$2,000 to invest" still opens New Cash, because "have" counts only with "now", "currently" or a share count.
- `route()` itself is unchanged, so slash commands, stock analysis, comparisons, themes, rebalancing and new cash keep their precedence.

### P2-1: Proposal contract blocks common updates

**Status:** Fixed.

**Root cause.**
- `prepare_portfolio_proposal` had no relative action. A stated purchase or sale needed the model to add to the bound quantity, which the tool contract forbade ("never derive edits from retrieved content"). The model had to ask or guess.
- The chat system prompt had no proposal guidance. Position IDs were not in context, so every update needed a `get_holdings` turn first.

**Technical decision and rationale.**
- **`change_quantity` action** (`proposal_tools.py`). The model passes only the signed share change the user stated (5 for "bought 5", -3 for "sold 3"). Python adds it to the quantity in the run's bound revision (`context.committed`), and the result goes through the existing `SavedPortfolio` validation, `prepare_portfolio_action`, stale-revision check and human confirmation.
  - `quantity` is now signed, within the same bounds and precision as `Quantity`. Every other action rejects a negative value with an `invalid_edit` clarification.
  - A change of zero, a missing change, a result below zero shares and an unknown position ID are rejected with a clarification. The negative-result message states the recorded quantity.
  - A change that leaves exactly zero shares removes the holding and its average cost, the same as `remove_position`. A `set_quantity` of zero is still rejected as ambiguous.
  - `change_quantity` on a cash row is rejected: "Deposits and withdrawals are not recorded as changes: state the new cash balance." Cash keeps the existing capability, `set_quantity` with a stated balance. No cash accounting was added.
  - A purchase or sale changes shares only. Prices, costs, fees, taxes and cash are never inferred, and average costs are carried forward unchanged.
  - Absolute updates still use `set_quantity`.
- **Clear previews.** The tool result now carries `changes` (position ID, account name, holding, field, previous and proposed quantity, computed by Python) and `committed: false`, so the answer can state exactly what will change.
- **Repeated calls and requests.** Before storing a preview, `prepare` compares the candidate with the pending proposals this run knows on the same base revision. An identical one is returned with status `already_pending`, and no second proposal is stored. Every change is computed from the bound revision, never from another pending preview, so repeating "I bought 5 more" cannot stack to +10. If two different previews exist, confirming one marks the others stale (existing `confirm_proposal` behavior).
- **Holdings index** (`context.py`). The run context includes `saved_holdings_index` when the saved portfolio has 60 positions or fewer: position ID, account name, and ticker with shares. Cash rows show only their ID, account and currency. Balances, average costs and marks are left out. A larger portfolio leaves the index out, and the model uses `get_holdings` as before. The index always comes from the committed revision, because proposals edit saved holdings even in a hypothetical run.
- **Prompt guidance.** One added passage in the system prompt: prepare stated changes with `change_quantity` or `set_quantity`; a hypothetical is not a change; the model cannot commit; state what will change, the previous and proposed quantity, the account, and that the portfolio stays unchanged until the user confirms. The tool description also says to ask for the account when a ticker is held in more than one account, and to ask for the new cash balance on a deposit or withdrawal.
- **Unchanged.** Confirmation, cancellation, stale-revision protection, the proposal store, financial calculations and the UI.

**Changed files.**
- `frontend/components/desk/route.ts`: `HOLDINGS_UPDATE`, wider `HOLDINGS_EDIT`, one line in `isConversation`.
- `frontend/tests/route.test.ts`: a new describe block for update statements and kept routes.
- `backend/analyst/proposal_tools.py`: `change_quantity`, signed `quantity`, `changes` in the result, reuse of identical pending previews, updated tool description.
- `backend/analyst/context.py`: `holdings_index`, `HOLDINGS_INDEX_LIMIT`, proposal guidance in the system prompt.
- `backend/tests/test_chat_proposals.py`: new cases and tests listed below.

### Phase 5 tests and results

No live model calls. All backend tests use the scripted transport and the existing `portfolio()` fixture (ACME, 10 shares, TFSA). The route unit tests ran with Playwright's runner and a temporary config without the web server, because a developer `next dev` server was already running.

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. Purchase and sale statements route to chat, not `/api/analyze` | `route.test.ts` "stated purchases, sales, totals and cash movements go to chat…": all eight prompt phrasings plus "I've just bought…", "We own 12 shares…", "Bought 5 ACME today" | Pass |
| 2. Stock analysis requests still route correctly | `route.test.ts` "hypotheticals, analysis requests and new-cash shortcuts keep their routes", and the existing 22 route tests | Pass |
| 3. +5 on 10 shares proposes 15 | `test_stated_share_change_is_added_server_side_and_applied_once[5-15]` | Pass |
| 4. -3 on 10 shares proposes 7 | `test_stated_share_change_is_added_server_side_and_applied_once[-3-7]` | Pass |
| 5. Absolute updates stay correct | Existing `set_quantity` tests in `test_chat_proposals.py` | Pass |
| 6. Ambiguous accounts, invalid quantities and negative results are handled safely | New parametrized cases in `test_unknown_or_ambiguous_edits_ask_instead_of_guessing` (negative set, -11 on 10, zero change, missing change, unknown position, cash change); `test_same_ticker_in_two_accounts_is_indexed_by_account_and_only_the_named_row_changes`; `test_selling_every_share_removes_the_holding_like_remove_position` | Pass |
| 7. Repeated requests or retries create no unintended changes | `test_repeated_calls_and_requests_reuse_one_pending_preview`: two calls in one run and a second identical request leave one pending proposal for 15 shares | Pass |
| 8. No revision changes before confirmation | The delta test compares `/api/portfolio` before and after the run | Pass |
| 9. Confirmation updates the correct position exactly once | The delta test confirms twice: the same receipt, revision 2, the expected shares, and costs carried forward | Pass |
| 10. Phases 1 to 4 stay compatible | `test_context_retrieval.py`, `test_conversation.py`, `test_workflow_tools.py` (context and prompt changes); full `test_chat_proposals.py` | Pass |

Totals: `test_chat_proposals.py` 41 passed; `test_context_retrieval.py`, `test_conversation.py` and `test_workflow_tools.py` 105 passed; `route.test.ts` 24 passed. Ruff and mypy pass on the changed backend files. `tsc --noEmit` passes.

**Remaining risks.**
- The backend cannot see which account the user meant. It edits only the exact position ID the model passes, so it never picks an account on its own. Asking when a ticker is held in several accounts depends on the model following the prompt and tool guidance. Scripted tests cannot prove that a real model asks.
- The route patterns are heuristics. A statement that starts differently ("Just picked up 5 MSFT") still falls through to ticker matching and opens analysis. A sentence such as "I sold 3 ACME, should I sell more?" goes to chat, which is acceptable.
- Deposits and withdrawals route to chat, but the model must ask for the new cash balance. A relative cash change is not supported, by design in this phase.
- A user who really bought 5 more twice before confirming the first preview gets the same "+5 from the bound revision" preview twice. Confirming then records only one purchase. The preview shows previous and proposed quantities, so the user can correct it, but this is not detected automatically.
- The holdings index is left out above 60 positions. Those portfolios still need one `get_holdings` turn.
- The Playwright browser specs for proposals were not rerun. Confirmation and the proposal UI were not changed.

## Phase 6: AI tool and context efficiency (P2-2)

### P2-2: Context waste and tool sprawl in chat

**Status:** Fixed. Phase 1 already bounded result sizes. Phase 6 fixes the remaining items: every tool schema sent on every turn, and tool steps using up the history window. Desk-form migration and the retirement of `/api/analyze` (P2-3, P3-b) are out of scope and stay open.

**Inspection.**
- `Orchestrator._execute_run` merged the capabilities of `READ_CAPABILITIES`, `FinancialTools`, `ResearchTools`, `WorkflowTools`, `WebSearchTools` and `ProposalTools` into one registry of 23 tools. It sent `read_tools(capabilities)`, the full registry, on every outer turn. The list never changed within a run.
- Nested workflow calls (inside `run_*` tools) use their own pipeline tool sets of 9 to 11 tools. They were not changed.
- `RunContext.history` called `Database.read_messages(limit=20)`. That returned the 20 most recent rows, tool steps included. Phase 3 already kept tool steps out of the model's dialogue. But the SQL window still counted them, so a run with 8 tool steps used 8 of the 20 slots. Earlier questions and answers, and the artifacts recorded on their steps, dropped out after two or three tool-heavy runs.
- The system context (bound revision, settings, pending proposals, artifact references and, since Phase 5, the holdings index) is small. It was 2.3k characters in the simple-question fixture and is not changed.

**Root cause.**
- Tool exposure ignored run state. The two largest schemas, `calculate_company_cases` (4.3k characters) and `calculate_comparison` (4.4k), were sent on every turn of every run. That includes turns where they could not succeed: company cases need this run's primary retrieval first, and a simple question needs neither.
- The history window was a row count, not a dialogue count.

**Architecture decisions and rationale.**
- **State-based tool advertising, not routing.** The registry stays complete and every capability stays callable. Only the list of schemas sent on a turn is filtered. `ResearchTools.unadvertised(user_text)` names the schemas this run cannot use yet:
  - `calculate_company_cases` is left out until this run has retrieved primary research (`ResearchTools.read`). This is the handler's own prerequisite, so a model can never need the tool before it appears. It appears on the turn after the retrieval, and no extra turn is spent.
  - `calculate_comparison` is left out unless the run has an explicit comparison selection, a completed company case, a completed opportunity screen, or comparison phrasing in the user's message ("compare", "versus", "vs", "instead", "switch", "swap", "replace", "alternative", "rather", "trade-off", "or keep", "or hold").
  - The list is recomputed each turn, so a schema appears as soon as the run reaches its prerequisite.
  - A call to a registered tool that was not advertised still runs. Its handler enforces its own rules as before, so a model that knows the name from history is never refused for that reason alone.
- **What stays the same.** All 21 other tools are sent on every turn: reads, `review_portfolio`, financial data, quotes, `resolve_candidate`, `check_proposed_changes`, the low-level filing and issuer retrieval tools, `get_company_research`, `scan_opportunities`, the five `run_*` workflows, `web_search` and `prepare_portfolio_proposal`. No capability was removed. The proposal tool and its safety checks are unchanged, and no commit, confirm or cancel tool exists, as before.
- **Not done, on purpose.**
  - A keyword router or a "load more tools" meta-tool was not added. A meta-tool costs an extra turn, and the two-company research flow already uses all 8 turns.
  - Workflow and proposal tools were not hidden from users without a saved portfolio. They return a "save a portfolio first" clarification that is still useful.
  - Tool descriptions were not shortened.
- **History counts dialogue.** `Database.read_messages` now applies `limit` to dialogue rows (user messages and answers). It returns every row from just after the first dialogue row outside the window, up to the cursor. Tool steps inside the window come along without using it up. The oldest answer in the window keeps its own tool steps, so their artifacts are still made known to the run by `RunContext.remember`, and follow-up `get_artifact` calls work. A tool step is identified as an assistant row whose `tool_outcomes_json` is not `[]`, the same rule as `is_tool_step`. Tool steps are still stored and shown in the UI. The character budget (`CHARACTER_LIMIT`) and the `get_messages` cursor are unchanged.

**Changed files.**
- `backend/analyst/research_tools.py`: `COMPARISON_REQUEST`, `ResearchTools.unadvertised`.
- `backend/analyst/orchestrator.py`: the per-turn tool list leaves out unadvertised schemas.
- `backend/analyst/persistence.py`: `read_messages` counts dialogue rows.
- `backend/tests/test_research_tools.py`: the strict-schema test checks every advertised schema across turns, including both calculation tools. The eight-turn test checks when each schema appears. There is a new test for advertising and callability, and a parametrized test for comparison phrasing and explicit selection.
- `backend/tests/test_context_retrieval.py`: the expected first-turn tool set no longer has the two calculation schemas. There is a new test for the dialogue window with 24 tool steps and an earlier artifact.

### Phase 6 measurements

Method: a temporary pytest plugin (not committed) wrapped `CapturingTransport.respond` and recorded every outer orchestration request in representative scripted tests. It counted tools, the characters of the JSON tool list, and the characters of the JSON message list. Nested workflow requests were excluded. Values are characters of the request payload, not provider tokens. Message characters were identical before and after in every case, so all savings come from the tool list. Turn counts were identical in every case.

| Scripted flow | Turns (before / after) | Tools per turn before | Tools per turn after | Tool-list chars per run | Total input chars per run (tools + messages) |
|---|---|---|---|---|---|
| Simple question, first turn ("What is an ETF?", saved portfolio) | 1 / 1 | 23 | 21 | 21,698 → 13,039 (-40%) | 24,034 → 15,375 (-36%) |
| Chat proposal (`set_quantity`) | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 50,098 → 32,780 (-35%) |
| Chat proposal (`change_quantity`) | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 50,050 → 32,732 (-35%) |
| `run_stock_analysis` workflow (outer turns) | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 71,979 → 54,661 (-24%) |
| `run_new_cash_allocation` | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 68,584 → 51,266 (-25%) |
| `run_portfolio_reunderwriting` | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 58,696 → 41,378 (-30%) |
| `run_theme_exploration` (question uses comparison phrasing) | 2 / 2 | 23 | 22 | 43,396 → 34,812 | 77,301 → 68,717 (-11%) |
| Direct company cases (research, cases) | 4 / 4 | 23 | 21, 22, 22, 23 | 86,792 → 69,399 | 121,032 → 103,639 (-14%) |
| Direct comparison (no comparison phrasing; called unadvertised) | 2 / 2 | 23 | 21 | 43,396 → 26,078 | 61,320 → 44,002 (-28%) |
| Two-company screen, cases and comparison | 8 / 8 | 23 | 21, 21, 22, 23 … 23 | 173,584 → 151,974 | 323,380 → 301,770 (-7%) |
| Same, with an explicit comparison selection | 8 / 8 | 23 | 22, 22, 22, 23 … 23 | 173,584 → 160,708 | 326,164 → 313,288 (-4%) |

Notes on these figures:
- Savings are largest for simple questions and single-workflow runs, which are the common case. Long research runs save less, because both calculation schemas are needed once the research is done.
- Provider token counts and prompt caching were not measured, since that would need live calls. Where a provider caches the request prefix, the tool block may already be billed at a discount. A turn whose tool list grows also starts a new cached prefix once.
- The history fix can make the model input larger, not smaller, in threads with many tool steps. Those threads now keep up to 20 dialogue messages instead of losing them. The 32,000-character history budget still caps the size.

### Phase 6 tests and results

No live or paid model calls. All flows use scripted transports.

| Acceptance criterion | Test | Result |
|---|---|---|
| 1. Simple requests no longer get all 23 tools | `test_followup_gets_compact_bound_context_without_full_holdings` (exact 21-tool set); `test_calculation_schemas_wait_for_their_prerequisites_but_stay_callable` | Pass |
| 2. Complex analysis and research keep the capabilities they need | `test_two_company_screen_cases_and_comparison_deliver_final_answer_in_eight_turns` (both variants: cases advertised after research, comparison advertised before it is called, still 8 turns); `test_comparison_schema_is_offered_for_comparison_requests`; the workflow tests in `test_workflow_tools.py` | Pass |
| 3. Follow-up questions can retrieve earlier results | `test_tool_steps_do_not_use_the_dialogue_window_and_earlier_results_stay_retrievable`: `get_artifact` on an artifact from the oldest exchange succeeds. It fails without the fix ("Artifact reference is not known to this run"). | Pass |
| 4. Tool steps do not use up the dialogue limit | Same test: 3 exchanges with 24 tool steps; all 6 earlier dialogue messages reach the model, and all 33 rows stay stored | Pass |
| 5. Proposal workflows and safety checks unchanged | `test_chat_proposals.py` (41), including the no-commit-capability and injection tests | Pass |
| 6. Model-provider compatibility preserved | `test_openai_boundary.py`, `test_chatgpt_plan.py`, `test_model_transport.py`; strict-schema test over every advertised schema | Pass |
| 7. Representative workflows pass with scripted models | `test_workflow_tools.py`, `test_research_tools.py`, `test_allocation.py`, `test_theme.py`, `test_web_search.py` | Pass |
| 8. Tool and context sizes improve without extra orchestration calls | Measurement table above: identical turn counts, smaller tool lists in every flow | Pass |

Totals:
- `test_research_tools.py` and `test_web_search.py`: 75 passed.
- `test_context_retrieval.py`: 30 passed.
- Regression run over `test_chat_proposals.py`, `test_conversation.py`, `test_workflow_tools.py`, `test_web_search.py`, `test_financial_tools.py`, `test_run_lifecycle.py`, `test_saved_run_replay.py`, `test_openai_boundary.py`, `test_chatgpt_plan.py`, `test_model_transport.py`, `test_allocation.py` and `test_theme.py`: 358 passed and 1 failed. The failure was `test_hosted_search_obeys_the_run_deadline`, a wall-clock deadline test. It passed on two reruns with the change, and it behaves the same without the change.
- Ruff and mypy pass on the changed backend files.

**Remaining risks.**
- A comparison request typed without any of the listed phrases ("Should I move my cash into BROAD?") does not see `calculate_comparison` on its first turn. The model can still run the `run_*` workflows, or screen or research first, which makes the schema appear. A real model rarely calls a tool it was not shown. The phrase list is a heuristic and may need widening from real transcripts.
- The tool list can grow within a run (at most twice), which starts a new provider prompt-cache prefix at that turn.
- Workflow and research schemas (`run_*`, filing tools, `get_company_research`) are still sent on every turn, about 13k characters in total. Shrinking or merging them belongs with retiring `/api/analyze` and moving the desk forms to thread runs (P2-3).
- Messages written by older code are classified by `tool_outcomes_json = '[]'`. Every writer in the codebase uses `json.dumps`, but a hand-edited database with other spacing would count tool steps as dialogue. That is harmless: it is the previous behavior.

## Recommended next phase

Phase 7: fix group F remainder (P2-3, P3-b).
- Send the desk forms (`/review`, `/stock`, `/new-cash`, `/theme`, `/rebalance`) through thread runs with `workflow_input`.
- Retire `/api/analyze` once parity is reached.
- Then consider merging the three granular filing tools into `get_company_research` and shortening the `run_*` descriptions, with a fresh measurement.
