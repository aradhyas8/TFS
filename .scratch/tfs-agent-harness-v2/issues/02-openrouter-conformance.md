# 02: Prove an OpenRouter tool round trip through PydanticAI

**What to build:** An isolated PydanticAI evaluation that receives a question, calls an existing TFS tool and returns a validated answer through mocked OpenRouter HTTP.

**Blocked by:** 01 — Establish the evaluation baseline and invariants.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Pin the evaluated framework/provider versions and use the smallest necessary dependency extras.
- [ ] Verify the chosen OpenRouter integration against TFS's existing Responses route before changing protocols; document any deliberate protocol difference.
- [ ] Preserve selected model, routing/price restrictions, explicit supported reasoning, unset-parameter omission, schema validation and tool-call/result identity.
- [ ] Verify tool-to-answer continuation, citations, usage and refusal/incomplete/rate-limit error mapping through recorded requests and fake responses.
- [ ] Exercise forced-tool/reasoning conflicts and parallel calls; unsupported explicit combinations fail clearly instead of silently disabling reasoning or changing models.
- [ ] Keep routing guards and secrets server-side; no external model request is made.
- [ ] Record pass/fail and the adapter code needed for compatibility. Label the result "mocked contract verified; real-provider compatibility unverified" and carry that limitation into 04 and 10. Production execution remains on the current path.

## Verification

Use existing provider-boundary tests with mocked HTTP, plus a real framework tool round trip to a deterministic TFS handler. Run focused tests, Ruff and mypy on changed backend code.
