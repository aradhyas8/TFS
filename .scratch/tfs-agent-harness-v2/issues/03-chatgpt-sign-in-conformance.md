# 03: Prove the existing ChatGPT sign-in and streamed tool round trip

**What to build:** An isolated evaluation that uses TFS's existing credential contract to complete a mocked subscription-route tool exchange without weakening sign-in or secret storage.

**Blocked by:** 01 — Establish the evaluation baseline and invariants.

**Status:** ready-for-agent

**Authority:** [V2 specification](../../../specs/tfs-agent-harness-v2.md).

## Acceptance criteria

- [ ] Compare TFS's actual sign-in endpoint, token audience/scopes and account identity with the framework's Codex integration; document whether a custom adapter is required.
- [ ] Keep TFS's DPAPI/Keychain credential ownership, sign-out and account separation. No dependency on another application's credential file is introduced.
- [ ] Establish one refresh owner; rotated tokens are persisted through the existing secure store and concurrent refresh does not reuse invalidated tokens.
- [ ] Verify streaming-only behavior, store=false, unsupported parameter omission, tool continuation, reasoning state and deadline handling with fake credentials and mocked HTTP.
- [ ] Expired/revoked grants and interrupted/malformed streams produce safe existing error outcomes; logs/public records contain no tokens or private continuation.
- [ ] Document request-budget limits the endpoint cannot enforce, and the compensating run/time controls.
- [ ] Deliver a mocked-contract verdict and minimum adapter approach while production sign-in remains unchanged. Label actual sign-in, refresh and real-provider compatibility unverified until the approved live gate in 10 passes.

## Verification

Extend existing sign-in/stream tests and run a framework tool round trip against a mocked stream. Mock OS credential boundaries as needed; do not read real credentials or perform an actual login/model call. Run focused tests, Ruff and mypy.
