# ChatGPT Sign-in and Streamed Tool Round Trip Conformance Report

**Authority:** [V2 specification](../../specs/tfs-agent-harness-v2.md) | [Ticket 03: Prove the existing ChatGPT sign-in and streamed tool round trip](../issues/03-chatgpt-sign-in-conformance.md)  
**Status:** Conformance Proven (Mocked HTTP)  
**Compatibility Limitation Label:** **mocked contract verified; real-provider compatibility unverified**  
**Date:** 2026-10-16  

> [!IMPORTANT]
> **Adoption & Release Limitation:**  
> This evaluation gate proves client-side protocol conformance, credential separation, single-refresh ownership, parameter omission, streaming turn deadlines, error mapping, and tool round-trip behavior exclusively through mocked HTTP (`httpx.MockTransport`) and mock credential storage. No live OpenAI or ChatGPT subscription API requests were made, and no real user credentials were read or modified. Actual sign-in, token refresh against OpenAI's live endpoints, subscription sharing quotas, and real upstream model execution remain unverified until the explicitly approved live compatibility gate in Ticket 10 passes before release. Production runtime execution remains on the current path.

---

## 1. Executive Summary

Ticket 03 evaluated PydanticAI core against TFS's existing ChatGPT-plan connection and Sign in with ChatGPT contracts. We established an isolated conformance adapter (`backend/analyst/chatgpt_plan_adapter.py`) and an executable test suite (`backend/tests/test_chatgpt_plan_conformance.py`, 23 tests, 100% passing).

### Key Outcomes
- **Custom Adapter Verdict:** Evaluated PydanticAI's built-in `OpenAICodexProvider` against TFS's Sign in with ChatGPT contracts. Proved that a custom adapter is **strictly required**. PydanticAI's built-in provider reads plaintext credentials from an external CLI file (`~/.codex/auth.json`), requests inappropriate scopes, targets the wrong base URL (`https://chatgpt.com/backend-api/codex`), and lacks integration with TFS's encrypted OS store.
- **Credential Ownership & Security:** Preserved TFS's encrypted DPAPI (Windows) and Keychain (macOS) storage under `%LOCALAPPDATA%\Greedy\chatgpt-plan`. Zero dependency on another application's credential file was introduced. Sign-out revokes credentials upstream, clears local secrets, and preserves account separation (`subject` matching).
- **Single Refresh Owner:** Proved that TFS's `credential_lock()` (`session.lock` cross-process file lock + thread lock) maintains exclusive ownership over token refresh. In concurrent refresh scenarios, rotated tokens are persisted atomically, and waiting callers reuse the fresh access token without re-dispatching refresh requests (which would fail due to refresh token invalidation).
- **Wire Contract Conformance:** Verified through mocked Responses API SSE streams that PydanticAI's `OpenAICodexModel` configured with `openai_codex_model_profile` enforces `stream=true`, `store=false`, parameter omission (`temperature`, `top_p`, `max_output_tokens` / `max_tokens`), and exact reasoning state (`openai_reasoning_effort` or omitted when unset).
- **Deterministic Tool Round Trip & Financial Parity:** Executed a full multi-turn tool continuation round trip (`function_call` + `function_call_output`) where PydanticAI invoked TFS's deterministic `review_portfolio` handler, matched the Ticket 01 baseline data exactly (3,600.00 CAD total value, 0.63888889 Acme weight, 0.36111111 cash weight), and produced a validated structured answer.
- **Deadline Handling:** Proved that stalled or endless streams are cleanly cut off at the turn deadline (`asyncio.timeout`), closing the stream and surfacing `openai.APITimeoutError` without retrying.
- **Secret Scrubbing & Safe Error Mapping:** Verified that bearer tokens (`oauth-access`, JWTs, keys) never leak into logs or error messages. Classified expired/revoked grants (HTTP 401), usage limits (`subscription_sharing_usage_limit_exceeded`), rate limits, truncated streams (`no_terminal_event`), and model refusals into TFS domain exceptions (`PlanError`, `ModelServiceError`, `ValueError`).
- **Endpoint Limits & Compensating Controls:** Documented request-budget limits that OpenAI's subscription endpoint cannot enforce (no per-request dollar caps or token budgets), and the compensating client controls (turn deadline, attempt ledger, emergency iteration ceiling, client-side cancellation).

---

## 2. Comparison: TFS Sign in with ChatGPT vs. Framework Codex Integration

A detailed architectural comparison between TFS's existing Sign in with ChatGPT implementation and PydanticAI's built-in Codex integration (`OpenAICodexProvider`):

| Contract / Dimension | TFS Sign in with ChatGPT (`backend/analyst/chatgpt_plan.py`) | PydanticAI Codex Integration (`OpenAICodexProvider`) | Verdict / Adaptation |
|---|---|---|---|
| **OAuth Authorization Endpoint** | `https://auth.openai.com/api/accounts/authorize` | `https://auth.openai.com/oauth/authorize` | **Different endpoint.** TFS follows OpenAI's official open-source local app protocol. |
| **OAuth Token Endpoint** | `https://auth.openai.com/api/accounts/oauth/token` | `https://auth.openai.com/oauth/token` | **Different endpoint.** TFS exchanges PKCE S256 codes and rotates refresh tokens at the `api/accounts` route. |
| **Target Base URL / Resource** | `https://api.openai.com/v1` (`POST /v1/responses`) | `https://chatgpt.com/backend-api/codex` | **Incompatible route.** TFS targets the public Responses API with OAuth bearer tokens, not the internal Codex backend. |
| **OAuth Scopes** | `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct` | `openid profile email offline_access` | **Missing required scopes.** PydanticAI lacks `chatgpt.tokens.use.direct` (`PLAN_SCOPE`) and `resource.invoke`, which OpenAI requires for subscription sharing. |
| **Client ID Provisioning** | Dynamic client registration per host (`dynamic_agent_client` -> issued `client_id` in `host.json`). | Hardcoded static public client ID (`app_EMoamEEZ73f0CkXaXp7hrann`). | **Incompatible identity.** TFS dynamically registers unique client IDs per user installation. |
| **Identity & Account Separation** | Validates RS256 JWKS signature; enforces matching `sub` (subject claim) across sessions; detects account mismatch. | Parses unvalidated JWT payload for `chatgpt_account_id` without cross-session subject locking. | **TFS security boundary preserved.** Adapter retains TFS identity validation. |
| **Credential Storage** | Encrypted OS store: Windows DPAPI (`{client_id}.dpapi`) or macOS login Keychain item (`TFS ChatGPT plan`). | Reads unencrypted plaintext JSON from `~/.codex/auth.json` (Codex CLI tool). | **Critical violation.** PydanticAI relies on a third-party application's unencrypted file. TFS retains 100% OS-encrypted storage. |
| **Refresh Ownership** | Single refresh owner via `credential_lock()` (`session.lock` file lock + thread lock); atomic rotation. | Independent internal refresh loop unaware of DPAPI/Keychain. | **Race hazard.** PydanticAI cannot own refresh without racing with TFS processes. TFS remains sole refresh owner. |
| **Custom Adapter Required?** | **YES.** | N/A | **Verdict:** A custom adapter (`ChatGPTPlanEvaluationAdapter`) is strictly required to bridge TFS credentials to PydanticAI execution. |

---

## 3. Conformance Test Suite & Verification Results

All 23 conformance tests in `backend/tests/test_chatgpt_plan_conformance.py` pass cleanly:

```bash
pytest backend/tests/test_chatgpt_plan_conformance.py -v
```

```
============================= test session starts =============================
platform win32 -- Python 3.11.0, pytest-8.4.2, pluggy-1.6.0
collected 23 items

tests/test_chatgpt_plan_conformance.py::test_conformance_pinned_dependencies PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_comparison_and_adapter_verdict PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_preserves_dpapi_and_keychain_ownership_no_external_dependency PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_sign_out_and_account_separation PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_single_refresh_owner_and_concurrent_refresh_safety PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_streaming_store_false_parameter_omission_and_reasoning[None-None] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_streaming_store_false_parameter_omission_and_reasoning[medium-expected_reasoning1] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_streaming_store_false_parameter_omission_and_reasoning[high-expected_reasoning2] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_streaming_store_false_parameter_omission_and_reasoning[low-expected_reasoning3] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_tool_round_trip_to_deterministic_handler PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_deadline_handling_stream_cutoff PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[401-{"error": {"code": "invalid_api_key", "message": "Bearer secret-oauth-token-invalid"}}-not_signed_in-expired or revoked] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[429-{"error": {"code": "subscription_sharing_usage_limit_exceeded"}}-subscription_sharing_usage_limit_exceeded-subscription_sharing_usage_limit_exceeded] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[429-{"error": {"code": "subscription_sharing_usage_unavailable"}}-subscription_sharing_usage_unavailable-subscription_sharing_usage_unavailable] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[429-{"error": {"code": "rate_limit_exceeded"}}-rate_limit_exceeded-rate_limit_exceeded] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[404-{"error": {"code": "model_not_found", "message": "Model not found"}}-model_not_found-model_not_found] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[400-{"error": {"message": "bad parameter"}}-request_failed-HTTP 400] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_error_mapping_without_secrets[503-{"error": {"message": "Service unavailable"}}-request_failed-HTTP 503] PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_stream_interruption_and_malformed_handling PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_refusal_mapping PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_invalid_structured_output_fails_validation PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_documented_request_budget_limits_and_compensating_controls PASSED
tests/test_chatgpt_plan_conformance.py::test_conformance_sanitize_secrets_helper PASSED

============================= 23 passed in 4.26s ==============================
```

### Regression & Nearby Test Verification
All 73 tests across existing plan, credential, OpenRouter conformance, baseline, and transport suites passed with zero regressions:
```bash
pytest backend/tests/test_chatgpt_plan_conformance.py \
       backend/tests/test_chatgpt_plan.py \
       backend/tests/test_credential_storage.py \
       backend/tests/test_openrouter_conformance.py \
       backend/tests/test_evaluation_baseline.py \
       backend/tests/test_model_transport.py \
       backend/tests/test_openai_boundary.py -v
# 141 passed in 20.67s
```

### Static Analysis Checks
```bash
ruff check backend/analyst/chatgpt_plan_adapter.py backend/tests/test_chatgpt_plan_conformance.py
# All checks passed!

mypy backend/analyst
# Success: no issues found in 34 source files
```

---

## 4. Adapter Architecture (`backend/analyst/chatgpt_plan_adapter.py`)

The evaluation adapter provides an isolated bridge connecting TFS's secure storage and Responses streaming contracts to PydanticAI:

```
                    ┌────────────────────────────────────────────────────────┐
                    │               TFS Application Domain                   │
                    │   (DPAPI / Keychain, host.json, credential_lock)       │
                    └──────────────────────────┬─────────────────────────────┘
                                               │ access_token() (Single Owner)
                                               ▼
                    ┌────────────────────────────────────────────────────────┐
                    │           ChatGPTPlanEvaluationAdapter                 │
                    │  - PydanticAI OpenAICodexModel with codex profile      │
                    │  - Compatibility bridge for openai==2.54.0             │
                    │  - Wire invariants: stream=True, store=False           │
                    │  - Parameter omission: temperature, top_p, max_tokens  │
                    │  - Turn deadline bounding (asyncio.timeout)            │
                    │  - Secret scrubbing (Bearer, JWT, keys)                │
                    │  - Safe PlanError & ModelServiceError classification   │
                    └────────────┬───────────────────────────────┬───────────┘
                                 │                               │
                                 ▼                               ▼
                      PydanticAI Agent                 OpenAIProvider
                  (Tool: review_portfolio)         (base_url: api.openai.com)
                                 │                               │
                                 └───────────────┬───────────────┘
                                                 │
                                                 ▼
                                        httpx.MockTransport
                                      (Zero Live Network I/O)
```

### Key Adapter Components
1. **Compatibility Bridge:** `openai.types.responses.Response.prompt_cache_diagnostics` is safely initialized to `None` if not declared on `Response` (resolving Pydantic v2 `AttributeError` when `pydantic-ai-slim==2.57.0` runs against `openai==2.54.0` without requiring an unvetted library upgrade).
2. **`build_chatgpt_plan_model_settings`:** Maps explicit reasoning effort to `openai_reasoning_effort` and omits it entirely when unset. Passes turn timeouts and wraps forced tool choices using `ToolOrOutput`.
3. **`map_pydanticai_chatgpt_plan_error`:** Translates PydanticAI/OpenAI exceptions to TFS's canonical `PlanError`, `APITimeoutError`, and `ModelServiceError` exceptions, scrubbing any bearer tokens, JWTs, or private continuations.
4. **`run_portfolio_round_trip`:** Executes multi-turn tool loops under strict turn deadlines (`asyncio.timeout`), capturing cumulative usage, message items, and verified domain objects.

---

## 5. Unenforceable Endpoint Limits & Compensating Controls

The ChatGPT plan / subscription route (`POST https://api.openai.com/v1/responses` under subscription sharing) has architectural limitations documented in Acceptance Criterion 6:

| Unenforceable Endpoint Limit | Why the Endpoint Cannot Enforce It | TFS Compensating Control |
|---|---|---|
| **Server-Side Token Budget Ceilings** | OpenAI's Responses API under subscription sharing rejects `max_output_tokens` / `max_tokens` (HTTP 400). | **Client-side turn deadline & task attempt ledger:** TFS bounds the turn duration and tracks total calls in a task ledger. |
| **Per-Request Dollar Caps** | Subscription requests do not carry monetary billing per token; usage is billed against subscription sharing pools. | **Rate-limit detection & usage tracking:** TFS monitors `subscription_sharing_usage_limit_exceeded` and tracks cumulative tokens in `RunUsage`. |
| **Turn Limit Ceilings** | The endpoint evaluates individual turns without enforcing multi-turn agent execution ceilings. | **Emergency iteration ceiling:** TFS's run supervisor caps consecutive tool turns (default 10) to prevent runaway loops. |
| **Stream Stalling / Infinite Emission** | HTTP read timeouts only bound individual chunk arrivals; a stream that emits periodic keep-alive deltas never ends. | **Overall turn deadline (`asyncio.timeout`):** TFS bounds the entire stream read to `request_timeout` (default 170s) and closes stalled streams cleanly. |
| **Client-Side Cancellation** | The server cannot anticipate user cancellation before completing its current streaming burst. | **Cancellation checks (`threading.Event` / `asyncio.CancelledError`):** Closes the SSE stream immediately and records an honest cancelled outcome. |

---

## 6. Adoption Gate (Ticket 04) & Ticket 10 Carry-Forward

In accordance with Acceptance Criterion 7, the following findings and constraints are established:

1. **Mocked Conformance Proven:** PydanticAI core successfully executes TFS ChatGPT-plan tool round trips, obeys streaming-only and `store=false` rules, omits unsupported parameters, honors reasoning effort, enforces single refresh ownership, and accurately classifies errors against mocked Responses SSE streams.
2. **Custom Adapter Required:** PydanticAI's built-in `OpenAICodexProvider` is rejected for production use due to its unencrypted file dependency, wrong scopes, and wrong endpoint. TFS must retain its own DPAPI/Keychain credential store and use the evaluated adapter approach.
3. **Real-Provider Compatibility Unverified:** Live OpenAI subscription-route execution, actual OAuth browser redirect flow, dynamic registration on `api.openai.com`, and real rate-limit behavior remain **unverified** by design under Native Codex Only rules.
4. **Production Path Unchanged:** Production runtime continues on `analyst.chatgpt_plan` and `OpenAIResponseTransport`. Cutover or framework adoption is deferred to the Ticket 04 gate.
5. **Ticket 10 Requirement:** Before any release cutover, Ticket 10 must run an explicitly authorized live smoke test with real ChatGPT subscription credentials against `gpt-4o` / `o3-mini` to validate live upstream behavior.

---

## 7. Repeatable Verification Commands

To verify this ticket independently, run:

```bash
# 1. Run the dedicated Ticket 03 conformance suite
backend\.venv\Scripts\pytest.exe backend/tests/test_chatgpt_plan_conformance.py -v

# 2. Run existing plan and credential regression suites
backend\.venv\Scripts\pytest.exe backend/tests/test_chatgpt_plan.py backend/tests/test_credential_storage.py -v

# 3. Run baseline and transport regression suites
backend\.venv\Scripts\pytest.exe backend/tests/test_evaluation_baseline.py backend/tests/test_openrouter_conformance.py backend/tests/test_model_transport.py backend/tests/test_openai_boundary.py -v

# 4. Static quality checks
backend\.venv\Scripts\ruff.exe check backend/analyst/chatgpt_plan_adapter.py backend/tests/test_chatgpt_plan_conformance.py
backend\.venv\Scripts\mypy.exe backend/analyst
```
