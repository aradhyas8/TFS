# OpenRouter Conformance Evaluation Report: PydanticAI Wire Contracts & Tool Round Trip

**Authority:** [V2 specification](../../specs/tfs-agent-harness-v2.md) | [Ticket 02: Prove an OpenRouter tool round trip through PydanticAI](../issues/02-openrouter-conformance.md)  
**Status:** Conformance Proven (Mocked HTTP)  
**Compatibility Limitation Label:** **mocked contract verified; real-provider compatibility unverified**  
**Date:** 2026-10-16  

> [!IMPORTANT]
> **Adoption & Release Limitation:**  
> This evaluation gate proves client-side protocol conformance, parameter preservation, error mapping, and tool round-trip behavior exclusively through mocked HTTP (`httpx.MockTransport`). No live OpenRouter or external provider API requests were made. Actual authentication, endpoint latency, account credits, and upstream model selection remain unverified until the explicitly approved live compatibility gate in Ticket 10 passes before release. Production runtime execution remains on the current path.

---

## 1. Executive Summary

Ticket 02 evaluated PydanticAI core (`pydantic-ai-slim==2.57.0`) against TFS's existing OpenRouter integration requirements. We constructed an isolated conformance adapter (`backend/analyst/openrouter_adapter.py`) and an executable test suite (`backend/tests/test_openrouter_conformance.py`, 23 tests, 100% passing).

### Key Outcomes
- **Dependency Minimality:** Evaluated `pydantic-ai-slim==2.57.0` pinned in `backend/pyproject.toml` under `dev`. It adds zero unnecessary provider extras and avoids upgrading `openai` to `>=3.26`.
- **Protocol Equivalence & Differences:** Identified and documented the architectural differences between TFS's current Responses route (`POST /v1/responses`) and PydanticAI's Chat Completions route (`POST /v1/chat/completions`). Both preserve TFS's semantic calculation results, parameter omission, and routing guards.
- **Deterministic Tool Round Trip:** Executed a full multi-turn tool round trip where PydanticAI invoked TFS's deterministic `review_portfolio` handler, matched the Ticket 01 baseline data exactly (total value: 3600 CAD, direct Acme weight: 0.63888889, cash weight: 0.36111111), and produced a validated structured answer.
- **Security & Secret Scrubbing:** Verified that secrets (`sk-or-*`) are retained strictly server-side; mocked error bodies never leak upstream keys or tokens into exception strings shown to users.
- **Error Mapping:** Complete classification of OpenRouter failures (429 rate limit, 404 parameter/reasoning mismatch, 402 credits, 400 bad schema, 503 unavailable, timeouts, refusal, and incomplete output) into TFS's domain exceptions (`ModelServiceError`, `ModelIncomplete`, `ValueError`).
- **Conflict Safety:** Proved that unsupported combinations (e.g., forced tool choice with reasoning models that reject forcing while thinking) fail clearly with explicit configuration errors rather than silently disabling reasoning or switching models.

---

## 2. Pinned Dependencies & Versioning

To minimize surface area and prevent dependency bloat, the evaluation uses `pydantic-ai-slim`:

| Package | Evaluated Version | Purpose / Constraints |
|---|---|---|
| `pydantic-ai-slim` | `2.57.0` | Core agent execution, model adapters (`OpenRouterModel`), and graph runner. Pinned in `pyproject.toml` under `[project.optional-dependencies] -> dev`. |
| `openai` | `2.54.0` | Kept at existing TFS pin (`openai>=1.99,<3`). Evaluated without requiring `openai>=3.26`. |
| `pydantic` | `2.14.1` | Existing domain models, schemas, and structured outputs. |
| `httpx` | `0.28.1` | Mocked transport (`httpx.MockTransport`) for zero-external-network test execution. |

No external model SDKs (e.g. Anthropic, Google, Cohere, Groq) were installed or required.

---

## 3. Protocol Comparison: Responses API vs. Chat Completions API

TFS's production orchestrator currently uses OpenAI's Responses API (`OpenAIResponseTransport` at `https://openrouter.ai/api/v1/responses`). PydanticAI's built-in `OpenRouterModel` targets OpenRouter's OpenAI-compatible Chat Completions API (`POST https://openrouter.ai/api/v1/chat/completions`).

The protocol differences and compatibility requirements are documented below:

| Feature | TFS Existing Route (`/v1/responses`) | PydanticAI Integration (`/v1/chat/completions`) | Deliberate Difference / Adapter Solution |
|---|---|---|---|
| **Endpoint Path** | `POST /v1/responses` | `POST /v1/chat/completions` | OpenRouter supports both. Chat completions is the standard OpenRouter route supported natively by PydanticAI's `OpenRouterModel`. |
| **Request Envelope** | Top-level `input` array of message items. | Top-level `messages` array (`role: user/assistant/tool`). | Standard schema mapping; messages mapped transparently by PydanticAI. |
| **Tool Calling Envelope** | Output items of type `function_call` with `id`, `call_id`, `name`, `arguments`. | `choices[0].message.tool_calls` array with `id`, `type="function"`, `function: {name, arguments}`. | Handled by `OpenRouterModel` without application code changes. |
| **Continuation Exchange** | Prior `function_call` item re-sent in `input` followed by `function_call_output` item. | Assistant message with `tool_calls` followed by message with `role: "tool"` and `tool_call_id`. | PydanticAI preserves tool-call ID and tool return identity across turns. |
| **Structured Output** | `text: {format: {type: "json_schema", name: ..., schema: ..., strict: True}}`. | `response_format: {type: "json_schema", json_schema: {name: ..., schema: ..., strict: True}}`. | Enabled via profile `{"default_structured_output_mode": "native", "supports_json_schema_output": True}`. |
| **Reasoning Parameter** | Sent in top-level `reasoning: {effort: str}`. | Sent in `extra_body: {reasoning: {effort: str}}`. | Handled by `build_openrouter_model_settings` mapping to `OpenRouterReasoning`. |
| **Unset Reasoning** | Parameter omitted completely (`reasoning: omit`). | Parameter omitted completely (no `reasoning` key in `extra_body`). | Tested and verified: unset effort leaves `extra_body` free of reasoning parameters so OpenRouter's `require_parameters` does not fail models without reasoning. |
| **Routing / Fallbacks** | `extra_body: {provider: {allow_fallbacks: False, require_parameters: True, ...}}`. | `extra_body: {provider: {allow_fallbacks: False, require_parameters: True, ...}}`. | Mapped by `build_openrouter_provider_config` into `OpenRouterProviderConfig`. Zero fallbacks and free price caps (`max_price={prompt: 0, completion: 0}`) verified. |
| **Citations / Annotations** | `content[0].annotations` has flat `{type: 'url_citation', url: str, title: str, start_index, end_index}`. | `choices[0].message.annotations` has nested `{type: 'url_citation', url_citation: {url, title, start_index, end_index}}`. | Adapter extracts citations from `msg.provider_details['annotations']` and standardizes representation. |
| **Usage Tracking** | `usage.input_tokens`, `usage.output_tokens`, `usage.cost`. | Cumulative `RunUsage` (`input_tokens`, `output_tokens`, `requests`, `tool_calls`). | PydanticAI sums across all turns of an agent run (e.g. 165 turn 1 + 200 turn 2 = 365 tokens). |

---

## 4. Conformance Test Suite & Verification Results

All 23 conformance tests in `backend/tests/test_openrouter_conformance.py` pass cleanly:

```bash
pytest backend/tests/test_openrouter_conformance.py -v
```

```
============================= test session starts =============================
platform win32 -- Python 3.11.0, pytest-8.4.2, pluggy-1.6.0
collected 23 items

tests/test_openrouter_conformance.py::test_conformance_pinned_dependencies PASSED
tests/test_openrouter_conformance.py::test_conformance_protocol_comparison_documented PASSED
tests/test_openrouter_conformance.py::test_conformance_preserves_model_and_routing_restrictions[vendor/model:free-True-False] PASSED
tests/test_openrouter_conformance.py::test_conformance_preserves_model_and_routing_restrictions[vendor/model-False-False] PASSED
tests/test_openrouter_conformance.py::test_conformance_preserves_model_and_routing_restrictions[openai/gpt-4o-mini-False-True] PASSED
tests/test_openrouter_conformance.py::test_conformance_preserves_model_and_routing_restrictions[openai/gpt-4o:free-True-True] PASSED
tests/test_openrouter_conformance.py::test_conformance_unset_parameter_omission_for_reasoning[None-False] PASSED
tests/test_openrouter_conformance.py::test_conformance_unset_parameter_omission_for_reasoning[medium-True] PASSED
tests/test_openrouter_conformance.py::test_conformance_unset_parameter_omission_for_reasoning[low-True] PASSED
tests/test_openrouter_conformance.py::test_conformance_tool_round_trip_to_deterministic_handler[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_invalid_structured_output_fails_validation[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-429-Rate limit reached for free tier-rate_limit-rate limit reached] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-404-No endpoints found that support the provided 'reasoning' value-model_service-OPENAI_REASONING_EFFORT] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-404-No endpoints found that can handle the requested parameters.-model_service-does not support every parameter TFS requires] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-404-No endpoints found for vendor/model.-model_service-is unavailable] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-400-Invalid schema provided-model_service-rejected the request (HTTP 400)] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-402-Insufficient credits-model_service-insufficient credits] PASSED
tests/test_openrouter_conformance.py::test_conformance_error_mapping_without_secrets[asyncio-503-Cloudflare upstream gateway error-model_service-is unavailable (HTTP 503)] PASSED
tests/test_openrouter_conformance.py::test_conformance_refusal_mapping[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_incomplete_tool_call_mapping[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_forced_tool_and_reasoning_handling[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_unsupported_forced_tool_and_reasoning_conflict_fails_clearly[asyncio] PASSED
tests/test_openrouter_conformance.py::test_conformance_parallel_calls_deterministic_execution[asyncio] PASSED

============================= 23 passed in 6.33s ==============================
```

### Nearby Regression Testing
Existing provider-boundary and transport test suites were verified to ensure zero collateral regressions:
```bash
pytest backend/tests/test_model_transport.py backend/tests/test_openai_boundary.py backend/tests/test_stock_openrouter_e2e.py -v
# 101 passed in 11.39s

pytest backend/tests/test_evaluation_baseline.py -v
# 9 passed in 3.42s
```

### Static Type & Lint Checks
```bash
ruff check backend/analyst backend/tests/test_openrouter_conformance.py
# All checks passed!

mypy backend/analyst
# Success: no issues found in 33 source files
```

---

## 5. Adapter Architecture (`backend/analyst/openrouter_adapter.py`)

The evaluation adapter provides a reusable bridge between TFS domain models and PydanticAI:

```
                    ┌────────────────────────────────────────────────────────┐
                    │               TFS Application Domain                   │
                    │   (Settings, Snapshot, Evidence, Calculations, DB)     │
                    └──────────────────────────┬─────────────────────────────┘
                                               │
                                               ▼
                    ┌────────────────────────────────────────────────────────┐
                    │             OpenRouterEvaluationAdapter                │
                    │  - Invariant config: allow_fallbacks=False, price caps │
                    │  - Unset parameter omission for reasoning              │
                    │  - Secret protection (scrubs sk-or-*)                  │
                    │  - Safe error classification (ModelServiceError)       │
                    └────────────┬───────────────────────────────┬───────────┘
                                 │                               │
                                 ▼                               ▼
                     PydanticAI Agent                  OpenRouterModel
                     (Tool: review_portfolio)          (/chat/completions)
                                 │                               │
                                 └───────────────┬───────────────┘
                                                 │
                                                 ▼
                                        httpx.MockTransport
                                      (Zero Live Network I/O)
```

### Key Adapter Components
1. **`build_openrouter_provider_config(model)`**: Translates TFS routing logic to `OpenRouterProviderConfig`, enforcing zero model/provider fallbacks, parameter requirement, and prompt/completion price caps on free models.
2. **`build_openrouter_model_settings(settings, reasoning_effort, forced_tool)`**: Enforces parameter omission (omitting `openrouter_reasoning` if effort is unset) and wraps forced tool choices using `ToolOrOutput` to permit final turn completion.
3. **`map_pydanticai_openrouter_error(exc, model)`**: Translates PydanticAI's internal exceptions (`ModelHTTPError`, `ContentFilterError`, `IncompleteToolCall`, `UnexpectedModelBehavior`, `UserError`) into TFS's canonical domain exceptions (`ModelServiceError`, `ModelIncomplete`, `ValueError`), scrubbing any sensitive tokens or raw bodies.
4. **`OpenRouterEvaluationAdapter.run_portfolio_round_trip`**: Executes the deterministic tool round trip, capturing cumulative `RunUsage` and URL annotations into a structured `ConformanceResult`.

---

## 6. Adoption Gate (Ticket 04) & Ticket 10 Carry-Forward

In accordance with Acceptance Criterion 7, the following conclusions and constraints are established for Ticket 04 (Adoption Gate) and Ticket 10 (Live Release Verification):

1. **Mocked Conformance Proven:** PydanticAI core successfully executes TFS tool round trips, obeys model selection, enforces server-side routing guards, omits unset reasoning parameters, and accurately classifies errors against mocked OpenRouter HTTP.
2. **Real-Provider Compatibility Unverified:** Real OpenRouter behavior (upstream endpoint routing, real token rate limits, account credit billing, and dynamic model capabilities) remains **unverified** by design under Native Codex Only rules.
3. **Seam Compatibility:** Production runtime continues on `OpenAIResponseTransport` (`/v1/responses`). Cutover or framework adoption is deferred to the Ticket 04 gate.
4. **Ticket 10 Requirement:** Before any release cutover, Ticket 10 must run an explicitly authorized live smoke test with real OpenRouter credentials against free-tier models to validate live upstream behavior.

---

## 7. Repeatable Verification Commands

To verify this ticket independently, run:

```bash
# 1. Run the dedicated Ticket 02 conformance suite
backend\.venv\Scripts\pytest.exe backend/tests/test_openrouter_conformance.py -v

# 2. Run the baseline and regression suites
backend\.venv\Scripts\pytest.exe backend/tests/test_evaluation_baseline.py backend/tests/test_model_transport.py backend/tests/test_openai_boundary.py -v

# 3. Static quality checks
backend\.venv\Scripts\ruff.exe check backend/analyst backend/tests/test_openrouter_conformance.py
backend\.venv\Scripts\mypy.exe backend/analyst
```
