# TFS architecture and functional audit

Date: 2026-10-15. Branch: `feat/openrouter-free-model` (HEAD `11d5777`). Scope: read-only. No application code was changed.

Evidence labels:
- **Confirmed**: reproduced with a targeted probe or proven directly by the code path.
- **Suspected**: the code makes the failure likely, but it was not reproduced.

The probes were throwaway pytest/Playwright files built on the existing harnesses (`CapturingTransport`, `workflow_case`, `submit`). They used scripted models and made no live AI or network calls. They were deleted after the runs.

## 1. Architecture assessment

Flow: `Composer` → `route.ts`/`isConversation` (regex router) → one of two paths:

1. **Chat path**: `useConversation.send` → `POST /api/threads/{id}/messages` → `Orchestrator._execute_run` (8 turns, 600 s). This path exposes 23 tools: reads, financial, research, workflow, web and proposal tools. Each tool step is persisted as an assistant message. The UI polls the run every second.
2. **Legacy path**: `page.tsx analyze()` → synchronous `POST /api/analyze` → `pipeline.analyze` (its own loop of up to 16+ model turns) → results saved as decisions.

The chat workflow tools (`run_stock_analysis` etc.) call the same `pipeline.analyze` from inside a chat tool call. The result is one model loop nested inside another. The two loops have different budgets, retry rules and error mapping.

**Strengths to keep:**
- Run lifecycle: idempotent `client_request_id`, cancellation that does not overwrite terminal state (`persistence.update_run`), and recovery of interrupted runs.
- Proposal confirmation: `BEGIN IMMEDIATE`, stale-revision detection and receipts (`persistence.confirm_proposal`). The model cannot commit anything.
- Revision binding per run, and private continuation storage.

**Assessment:** a rewrite is not needed. The defects come from a few shared seams:
- (A) Tools return full artifacts to the model with no compact projection.
- (B) Exception-to-message mapping is ad hoc and differs per path.
- (C) The chat loop has none of the token-budget safeguards that the pipeline already has.
- (D) Internal tool records are stored as assistant messages and then fed back to the model and the UI.
- (E) Portfolio-update intent has no clear contract.

## 2. Findings, ranked

### P0-1: Chat model cannot see workflow results or larger portfolio reviews (Confirmed)

`orchestrator.py:266-272` replaces any tool output that exceeds `CHARACTER_LIMIT = 32_000` (`context.py:27`) with `{"error": "Result exceeds retrieval budget. Request a smaller slice."}`. No tool accepts a slice argument. `get_artifact` (`context.py:206`) returns the same full envelope, so it hits the same limit.

Probe results:

| Case | Full result size | What the chat model received |
|---|---|---|
| `run_stock_analysis` (minimal 1-stock test fixture) | 36,131 chars | error only |
| `review_portfolio`, 1 / 10 / 25 holdings | 5.9k / 22k / over 32k chars (about 1.8k chars per holding) | full / full / error only |
| `review_portfolio` (25 holdings), then the model calls `get_artifact` | n/a | 8 tool steps, each with error status, then run `failed`: "Maximum orchestration turns (8) reached…" |

Impact:
- Stock analysis from chat always gives the final answer without the analysis. This is a direct cause of "unclear responses".
- Portfolio review fails for a realistic portfolio of 15 or more holdings.
- Follow-up questions about an earlier result cannot read that result.
- The UI still shows the stored result card, so the user sees a result and a vague answer that does not match it.

### P1-1: `max_output_tokens` failures in chat: no effort cap, no retry, raw diagnostics shown (Confirmed)

Causes:
- `providers.py:324` fixes `max_output_tokens=16000`. Reasoning tokens count against this cap. ChatGPT-plan requests remove the cap; OpenRouter and API-key requests keep it.
- The chat loop calls `_call_transport` with the configured reasoning effort on every turn (`orchestrator.py:147`). The pipeline already caps tool turns at `medium` and retries an incomplete response once at lower effort (`pipeline.py:698-744`). The chat loop does neither.
- Chat turns carry about 20.6k chars of tool schemas (23 tools, measured) plus up to 32k chars of context, so large reasoning at high effort is likely.

What the user sees (probe): run `failed` with error text `"Model service error: The model response is incomplete: reason=max_output_tokens, input_tokens=…, reasoning_tokens=…, max_output_tokens=16000, reasoning_effort=high, partial_tools=[]"`. `Thread.tsx:635` renders this text verbatim.

### P1-2: Failures inside chat workflows are mislabeled or hidden (Confirmed)

The tool handler in `orchestrator.py:238-246` catches only `TimeoutError` and `ValueError`.

- `ModelIncomplete` subclasses `ValueError`. Inside a workflow it becomes `"Requested data is unavailable to this run."` (probe). The run still completes, and the model writes an answer around a misleading error.
- `InvalidReview` (`pipeline.py:104`, base `Exception`), `ModelServiceError` and `PlanError` (`OpenAIError`) escape to `orchestrator.py:300`. The whole run then fails with `"Internal orchestration error."`, which also hides rate limits and plan-usage limits.
- `/api/analyze` (`api.py:722-731`) maps any non-expired `TimeoutError` and any `ValueError`/`OSError` to "Backend financial source configuration is invalid." This message is misleading for most causes.

Shared root cause with P1-1: no single mapping from exceptions to user-facing messages.

### P1-3: Raw JSON and internal steps in the conversation (Confirmed)

1. **JSON trees as results.** `Thread.tsx:256-262` renders every artifact kind except analysis and web search through `JsonViewer`. That covers `calculation_result` from `review_portfolio`, financial evidence, research, comparisons and quote refreshes.
2. **Every read is a visible step.** Each tool call is persisted as a message (`orchestrator.py:279-289`) and shown as a step (`Thread.tsx:491-533`). This includes `get_holdings`, `get_messages`, `get_proposal` and `get_artifact` ("Checked artifact").
3. **Duplicate cards.** `get_artifact` returns the envelope with a `reference`. `orchestrator.py:251-253` adds that ID to the step's `artifact_ids`, so the same result card renders again under every read. In the P0-1 loop this produced 8 copies.
4. **History teaches the JSON format.** Tool steps are stored with `text = {"retrieval": {...}}` and role `assistant`. `RunContext.history` sends them back to the model as assistant turns. Probe of a follow-up request: `assistant {"retrieval": {"tool": "get_holdings", "arguments": {...}}}`. *Suspected consequence:* weaker and free models imitate this pattern and emit tool-call JSON as text. The final-text path (`orchestrator.py:203-225`) stores any text as the answer without validation.
5. **Markdown shown as literal text.** Answers render as plain paragraphs (`Thread.tsx:541`), so literal `**`, `-` and `|` tables appear.

### P1-4: Portfolio-update statements trigger the wrong workflow (Confirmed)

Probe of `route.ts` (holding: MSFT):

| Input | Path |
|---|---|
| "I bought 5 more MSFT" | `/api/analyze` **stock analysis** (paid, multi-turn) |
| "I now hold 15 shares of MSFT" | `/api/analyze` **stock analysis** |
| "Set MSFT to 15 shares", "Update my portfolio: …", "I sold my Microsoft shares", "I deposited $1000 cash…" | chat |

`HOLDINGS_EDIT` (`route.ts:28`) matches only imperative verbs at the start of the input ("set", "update" and similar). Purchase and sale statements fall through to ticker matching (`route.ts:258`).

### P2-1: Proposal contract blocks common updates (contract Confirmed, outcome Suspected)

`proposal_tools.py:35-39` says "set_quantity records the exact shares … Never derive edits from retrieved content … ask when anything is unstated."
- A relative edit ("bought 5 more") requires adding to the bound quantity. That quantity is retrieved content, so the tool contract forbids the step. The model must ask the user or guess.
- The chat system prompt (`context.py:252-264`) has no proposal guidance. Holdings and position IDs are not in context, so the model needs at least one `get_holdings` turn first.
- `add_position` also needs `resolve_candidate`, which depends on an external lookup.

This is the most likely explanation for the unclear answers to portfolio-update requests that do reach chat.

### P2-2: Context waste and tool sprawl in chat (Confirmed by measurement)

- 23 tools and about 20.6k chars of schemas go out on every turn. The largest are `calculate_comparison` (4.4k) and `calculate_company_cases` (4.3k).
- Chat exposes low-level research and calculation tools (`research_tools.py:98-121`) next to workflows that run the same steps with stronger validation. `get_company_research` even says it exists to "fit … within eight turns".
- `MESSAGE_LIMIT = 20` counts tool-step messages. A run with 6 tool steps uses 8 of the 20 history slots, so real dialogue drops out of history quickly.
- Worst case per question: 8 turns × (system prompt + 20k schemas + 32k context). The model input is resent in full on every turn.

### P2-3: Two orchestration stacks (Confirmed, architectural)

There are three ways to run the same analysis: the legacy synchronous `/api/analyze`, the chat orchestrator, and the pipeline nested inside chat. They differ in:
- turn budgets (8 vs 16+);
- retry rules (the pipeline has one, chat does not);
- error mapping (P1-2);
- persistence (decisions vs threads);
- timeouts (synchronous 600 s HTTP request with a 600 s client timeout, `contracts.ts:271`, vs background run with polling).

The frontend regex router chooses the path. This duplication is the reason fixes made in one path (for example the incomplete-response retry) did not reach the other.

### P2-4: Empty final answer completes the run (Suspected)

A completed response with no calls and no text is stored as an empty assistant message, and the run is marked `completed` (`orchestrator.py:203-225`). The user sees a blank answer with no error.

### P3

- Changing the model while any run is active returns a 409 error with "Finish or cancel current AI work before changing the connection" (`api.py:182`). The same message appears when sending in a different thread after the selection changed. The text does not explain the cause.
- `/api/analyze` client and server deadlines are both 600 s, so a server-side timeout can arrive as a client abort.

## 3. Recommended fixes, grouped by shared cause

**A. Model-facing result contract (fixes P0-1, part of P1-3, P2-2)**
- Give each tool result two forms: the full artifact, stored for the UI, and a bounded model summary returned to the model.
  - `analysis_result`: recommendation, action, amount or range, key figures, guardrail status, evidence IDs.
  - `calculation_result`: totals, cash, top N exposures, breaches.
- Use the existing `projection="model"` mechanism in `get_artifact_envelope`. Add per-kind summarizers in one module, and call them in `FinancialTools.store` and `read_artifact`.
- Give `get_artifact` an optional `section` or path argument, or remove "Request a smaller slice" wherever no slice exists.
- Detect a repeated identical `get_artifact` call that hits the budget, and stop with a clear error instead of looping until the turn limit.

**B. One exception taxonomy (fixes P1-2, the user-facing half of P1-1, P3)**
- Add one `describe_failure(exc) -> (user_message, retryable)` helper. Use it in the orchestrator tool handler, the outer orchestrator catch and `/api/analyze`.
- In the tool handler, catch `InvalidReview`, `ModelServiceError` and `ModelIncomplete` and return a safe `{"error": …}` tool result so the run can explain the failure.
- Keep token diagnostics in logs only.

**C. Chat output budget (fixes P1-1)**
- Move the pipeline's `bounded_effort` cap and its single lower-effort retry on `max_output_tokens` into one helper, and use it in `Orchestrator._call_transport`.
- Cap tool-selection turns at `medium`, the same as the pipeline.

**D. Conversation records vs presentation (fixes P1-3, P2-4)**
- Leave tool-step messages out of `RunContext.history`. Give the model a compact system note of completed tools and artifact IDs instead of fake assistant turns. This also frees `MESSAGE_LIMIT` slots for dialogue.
- Record in a step's `artifact_ids` only the artifacts that the step created, not artifacts it read.
- UI:
  - collapse a run's steps into one line, for example "Checked holdings, prices · 4 steps";
  - hide pure reads;
  - replace the `JsonViewer` fallback with a readable summary for `calculation_result` (the existing Memo pieces may cover this);
  - add minimal Markdown rendering (already flagged by the ponytail note in `Thread.tsx:541`).
- Treat a final text that parses as a tool-call-shaped JSON object, or that is empty, as invalid. Retry once, then fail with a clear message.

**E. Portfolio-update intent (fixes P1-4, P2-1)**
- Route update statements to chat: bought, sold, now hold, deposited, withdrew, added, trimmed. Check these before ticker matching.
- Add a server-side `change_quantity` (delta) action to `prepare_portfolio_proposal`. The model then passes only the stated delta, and Python adds it to the bound quantity. This respects the "never derive" rule because the arithmetic happens in Python.
- Put a compact holdings index (id, ticker, account, quantity) in the system context when it fits, to save a `get_holdings` turn.
- Add one sentence of proposal guidance to the chat system prompt: prepare the preview, then say that nothing changes until the user confirms in the review.

**F. Converge the stacks (P2-3; incremental, not a rewrite)**
- Send the desk forms (`/review`, `/stock`, `/new-cash`, `/theme`, `/rebalance`) through thread runs with `workflow_input`. The chat path already supports this.
- Retire `/api/analyze` once parity is reached.
- Remove the low-level research and calculation tools from the default chat tool set once the workflow summaries from A exist.

## 4. Prioritized minimal implementation plan

1. **A**: model summaries plus the `get_artifact` loop stop. This is the biggest functional gain and is mostly backend: `financial_tools.store`, `context.read_artifact`, a new summarizer module, and `orchestrator.py:266`.
2. **B + C**: shared failure helper and effort/retry helper (`orchestrator.py`, `pipeline.py`, `api.py`). Small diff, and it removes the misleading errors.
3. **D (backend)**: drop tool steps from history, record created artifacts only, validate final text.
4. **D (frontend)**: collapsed steps, summary card instead of the JSON tree, minimal Markdown.
5. **E**: router update phrases, `change_quantity` action, prompt line, holdings index.
6. **F**: move the forms to thread runs, then reduce the tool set. Plan this separately once 1–5 are in.

## 5. Targeted acceptance tests

All tests use scripted models and mocked HTTP. No live calls.

| Fix | Test (file) | Pass condition |
|---|---|---|
| A | `test_workflow_tools.py`: each workflow via `workflow_case` | The last `function_call_output` the chat model sees has no `error`, is under the budget, and contains `recommendation.preferred_action` |
| A | `test_context_retrieval.py`: `review_portfolio` with 50 holdings | The model output is a summary under the budget, and the full artifact is retrievable via `/api/artifacts/{id}` |
| A | `test_context_retrieval.py`: model repeats `get_artifact` on an oversized ID | The run ends with a clear error in 2 or fewer repeats, not at the 8-turn limit |
| B | `test_workflow_tools.py`: nested model raises `ModelIncomplete`, `InvalidReview`, or a 429 `ModelServiceError` | The tool outcome error names the real cause (incomplete / invalid output / rate limit). The run is not "Internal orchestration error". |
| B/C | `test_conversation.py`: chat transport returns incomplete with `max_output_tokens` once | One retry at lower effort, then the run completes. If it fails twice, `run.error` has no token counts. |
| C | `test_conversation.py` | A chat tool turn is sent with effort ≤ `medium` when the configured effort is `high` (OpenRouter settings) |
| D | `test_context_retrieval.py`: follow-up after a tool run | No history message content contains `"retrieval"`. Prior user and assistant dialogue is present. |
| D | `test_conversation.py`: final text `{"name":"get_holdings","arguments":{}}`, or empty text | Not stored as the answer. One retry, then a clear failure. |
| D | `thread_artifacts.spec.ts` | A portfolio review shows no `JsonViewer`. A `get_artifact` step adds no duplicate card. Markdown `**bold**` renders as bold. |
| E | `route.test.ts` | "I bought 5 more MSFT", "I now hold 15 shares of MSFT" and "I sold 3 MSFT" give `isConversation === true` |
| E | `test_chat_proposals.py`: `change_quantity` delta +5 on 10 shares | A pending proposal with 15 shares. The committed revision is unchanged until confirmation. |
| E | `identity_and_proposals.spec.ts`, one scripted run | "I bought 5 more MSFT" gives one proposal card and a clear sentence that nothing changes until confirmation |

Run only the files listed for the fix being changed, plus Ruff and mypy on the changed backend files and `tsc` on the changed frontend files.
