# Local startup and ChatGPT connection

Integration base: `ba400e2` on `ticket-02-to-09-actions-and-persistence`, verified after fetching origin. Direction 5 phases 1–4 are merged as PRs #44–#47. Implementation is isolated from the original checkout's uncommitted UI work.

## Lifecycle

```text
npm run dev
  check existing dependencies and free ports
  start owned Uvicorn -> wait /api/health
  start owned Next.js -> wait HTTP 200
  Ctrl+C or service failure -> stop both owned processes

Continue with ChatGPT
  local POST -> background existing OAuth flow -> system browser
  loopback callback -> validate state, PKCE, ID token and plan scope
  DPAPI save -> account-specific GET /v1/models -> browser model picker
  Use ChatGPT plan -> persist nonsecret selection
  next AI request -> current access token + selected model -> new transport if needed
```

Settings no longer loads or refreshes ChatGPT tokens during application startup. Each accepted AI request resolves backend credentials. Refresh rotation is serialized across threads and native Windows processes. Orchestrator transports are immutable for their runs. A shared activation lock serializes provider selection and request configuration; changes, refresh and login return 409 while AI work is active. Once idle, a changed configuration closes the old transport and creates its replacement. Cancellation uses the owning orchestrator without requiring a working provider. The legacy analysis endpoint takes the same connection snapshot and blocks connection changes until completion. No portfolio calculations or persistence contracts were changed.

Explicit `LLM_PROVIDER` wins on startup; browser selection applies during the session. Without an explicit preference, the saved selection is restored. Login alone never changes providers. The chosen model must be in the live account catalog. API-key configuration remains backend-owned. There is no new account system.

## Security review

- Auth endpoints require a loopback peer, a local Host, allowed Origin, and, on mutations, `X-TFS-Local: 1`. Cross-site requests and nonlocal forwarded peers are rejected. Both development servers bind to loopback. This is not a remote-hosting authentication boundary.
- The existing OAuth flow retains PKCE S256, fresh random state/nonce, RS256 ID-token signature/issuer/audience/expiry/subject/nonce validation, scope validation, and same-identity checks before replacement. Invalid/duplicate callback fields cannot complete a login.
- Authorization URLs are never logged or returned by the API. Callback queries are not logged. The browser receives only an explicit status allowlist. Tests check browser localStorage and HTTP/log output for dummy secrets.
- One login task per app plus an exclusive native file lock prevents parallel TFS logins across processes. Refresh rotation has a separate native lock. Callback port conflicts, cancellation, a 10-minute deadline and exchange failures leave existing credentials intact.
- Tokens remain encrypted by the current Windows user's DPAPI. Unsupported Linux/WSL/macOS execution reports the Windows/API-key recovery path before login. No plaintext fallback and no Codex credential reuse.
- The historical `%LOCALAPPDATA%/Greedy/chatgpt-plan` directory is intentionally retained. Existing host/client identity and encrypted files are reused in place; only new registration display metadata says TFS. `CHATGPT_PLAN_DIR` remains supported. `selection.json` contains provider/model only.
- The launcher terminates only its own tracked child process trees. Occupied ports are reported without killing their owners. Windows Next.js shutdown uses tree termination; Uvicorn has a graceful shutdown signal and a five-second fallback.

## Evidence and limitations

OAuth tests are **mocked**, with real loopback handling, PKCE/JWT checks, Windows DPAPI, endpoint handling and Responses SDK streaming. OpenAI HTTP and browser authorization are replaced at external boundaries. The RSA fixture is a generated dummy test key with no account access. **No real account sign-in or live inference is claimed.** No source or credentials were sent to another model provider.

Connection-state screenshots use mocked status responses and a fictional account:

| State | Screenshot |
| --- | --- |
| Disconnected | [Continue with ChatGPT](screenshots/disconnected.png) |
| Connecting | [Browser sign-in and cancel](screenshots/connecting.png) |
| Connected | [Account and supported model](screenshots/connected.png) |
| Expired | [Reconnect and refresh](screenshots/expired.png) |
| Unavailable | [Native Windows guidance](screenshots/unavailable.png) |
| Failed | [Missing permission recovery](screenshots/failed.png) |
| Mobile | [Phone connection controls](screenshots/mobile.png) |

Remaining limits: native Windows credential storage; one saved ChatGPT registration (reconnect preserves its identity); no new multi-account picker or sign-out/revocation feature; real plan/model availability depends on the account; startup was not run on macOS/Linux. The referenced `.claude/skills/arc` design skill/checklist is absent; the existing installed Arc controls and checked-in Direction 5 guidance were used.

Recommendation: **CONDITIONAL GO**, pending a user-controlled real ChatGPT sign-in and inference smoke test. Mocked authentication cannot establish real account eligibility or consent behavior.

## Standards

The code-review standards pass found one P2: connection controls needed 44px touch targets below 1024px. A local responsive rule now supplies that size without modifying Arc internals; the reviewer confirmed the fix. The summary uses the existing `--radius-pill` token.

## Spec

The independent spec pass found one P1: disconnected theme clarification was incorrectly gated on AI credentials. The no-inference path now bypasses credential resolution, with an HTTP regression test. The reviewer confirmed the fix. A separate secret-leak regression led to dropping provider-controlled stream failure messages at the shared transport boundary while retaining allowlisted recovery codes and generic HTTP status.

Review totals: Standards 1 resolved finding (worst P2), Spec 1 resolved finding (worst P1); no unresolved review findings. Real OAuth remains unverified.
