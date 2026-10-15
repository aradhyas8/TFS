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

Remaining limits: native Windows credential storage; one saved ChatGPT registration (reconnect preserves its identity); no new multi-account picker; real plan/model availability depends on the account; startup was not run on macOS/Linux. The referenced `.claude/skills/arc` design skill/checklist is absent; the existing installed Arc controls and checked-in Direction 5 guidance were used.

Recommendation: **CONDITIONAL GO**, pending a user-controlled real ChatGPT sign-in and inference smoke test. Mocked authentication cannot establish real account eligibility or consent behavior.

## Standards

The code-review standards pass found one P2: connection controls needed 44px touch targets below 1024px. A local responsive rule now supplies that size without modifying Arc internals; the reviewer confirmed the fix. The summary uses the existing `--radius-pill` token.

## Spec

The independent spec pass found one P1: disconnected theme clarification was incorrectly gated on AI credentials. The no-inference path now bypasses credential resolution, with an HTTP regression test. The reviewer confirmed the fix. A separate secret-leak regression led to dropping provider-controlled stream failure messages at the shared transport boundary while retaining allowlisted recovery codes and generic HTTP status.

Review totals: Standards 1 resolved finding (worst P2), Spec 1 resolved finding (worst P1); no unresolved review findings. Real OAuth remains unverified.


## Final validation

| Check | Result |
| --- | --- |
| Backend regression | 880 passed; one existing Starlette/httpx deprecation warning |
| Backend typing/lint | mypy and Ruff passed |
| Launcher behavior | 5 passed (missing virtualenv/frontend dependencies, occupied port, backend exit, frontend exit/peer cleanup) |
| Windows manual startup | backend health and frontend readiness reached; Ctrl+C released ports 8000/3000 |
| Full Playwright run | 218 passed, 3 failed in 10.9 minutes |
| Clean focused Playwright rerun | 11 passed, covering all auth states and all three earlier failures |
| Frontend typecheck | passed |
| Production build | passed against the final frontend source in `.scratch/build-check/frontend` |
| Real account OAuth/inference | not run |

The full browser run's source guard detected API-key configuration names in frontend help copy; the copy now points to README setup and the guard passes. Two other reported failures were trace-artifact ENOENT errors caused by a concurrent screenshot run clearing Playwright's shared output directory, not failed product assertions. Both were rerun independently and passed. No unresolved automated test failure remains. Subsequent capture/regression checks ran without overlapping artifact writers.

Browser tests use dedicated ChatGPT fixture storage so they cannot read the developer's saved TFS connection. The original checkout and its uncommitted UI work were left intact. Both review findings were fixed and confirmed separately by the reviewers.

A final real Windows browser smoke test used empty, isolated auth/portfolio directories with `npm run dev`: both services became ready, the browser displayed Continue with ChatGPT, and the actual Next.js proxy returned HTTP 200 with disconnected ChatGPT status and no OAuth secret fields. No login or inference was attempted. [Real disconnected local app screenshot](screenshots/real-disconnected.png).

## Real-account follow-up

A user-driven sign-in reached the callback and saved the existing registration. The user reported an authorization-update email under the legacy Greedy registration name. The subsequent authenticated public `/v1/models` request repeatedly returned a Cloudflare HTTP 520 HTML response; an unauthenticated control request returned the expected HTTP 401. No inference success is claimed.

TFS now preserves the saved login, reports the specific safe HTTP status as unavailable, and offers Refresh connection without requiring repeated sign-in. The callback text asks users to return to TFS to check connection status and select a model. A regression test covers HTTP 520, absence of response-body secrets, and recovery when the catalog becomes available. Focused auth/transport suite: 26 passed; mypy and Ruff passed. Recommendation remains CONDITIONAL GO, with live model discovery/inference blocked until the upstream failure is resolved.


## Sign out and sign in again

Open AI connection and choose **Sign out of ChatGPT**, then **Continue with ChatGPT**. Sign-out is available for a saved account even when its model catalog is unavailable. It attempts refresh-token revocation through OpenAI's discovered HTTPS revocation endpoint, then removes the encrypted local token record and clears the model picker. If remote revocation fails after a bounded retry, local sign-out completes with an explicit warning to disconnect Greedy/TFS in ChatGPT Settings if needed.

Host identity and the account/client/subject mapping are retained. Re-login uses fresh state, nonce and PKCE without the deleted ID-token hint. The retained registration remains bound to its original account; this is not a new multi-account picker. Sign-out is rejected during active AI work or an in-progress login, and closes an idle ChatGPT transport. API-key selection is preserved. If retaining account identity fails, credentials are left intact and sign-out returns a safe storage error.

Validation: the mocked external-boundary auth/transport suite covers revocation, repeat logout, sign-in again with the same client/host, failed-revocation local cleanup, blocked sign-out during active work, cross-origin rejection, storage-write failure and rejecting a mismatched identity. Browser sign-out control passed; production build and frontend typecheck passed. No real session was revoked by the automated tests.

Final sign-out validation: **885 backend tests passed**, mypy/Ruff passed, the focused browser sign-out test passed, and frontend typecheck/production build passed. Review's identity-storage failure finding was fixed and confirmed; no unresolved sign-out review finding remains. TFS was restarted with the control available. Real revocation is left to the user's explicit Sign out action; automated tests used temporary mocked sessions only.

## Troubleshooting

| Problem | Action |
| --- | --- |
| ChatGPT sign-in is unavailable on WSL, Linux or macOS | Use native Windows, or configure the API-key provider in `backend/.env`. |
| Sign-in callback port `1455` is busy | Finish or cancel the other sign-in attempt. Then try again. |
| The connection expired, a permission is missing or no models show | Open **AI connection**. Select **Refresh connection**. If that fails, select **Reconnect ChatGPT**. |
| TFS refuses a provider change | Finish or cancel the running AI request or sign-in. Then try again. |
| The app loads, but AI analysis fails | Check the AI connection, the selected model and the backend terminal output. Portfolio management works without AI. |
| `npm run dev` in `cmd.exe` asks `Terminate batch job (Y/N)?` after Ctrl+C | Both services are already stopped. Type `Y`. PowerShell does not show this prompt. |

## macOS and Linux

The launcher has macOS and Linux paths, but real startup is verified only on Windows. ChatGPT sign-in needs Windows DPAPI, so use the API-key provider:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
cp backend/.env.example backend/.env
npm --prefix frontend ci
npm run dev
```

For reproducible dependency versions, see `backend/requirements-dev.lock` and `frontend/package-lock.json`. TFS is a personal, loopback-only installation. Do not expose it as a public server.

## Final startup audit (2026-10-14)

Integration base: PR #49 merge `5d9bad2` on `ticket-02-to-09-actions-and-persistence`, merged into this branch without conflicts.

| Check | Result |
| --- | --- |
| Clean clone and README install commands (Windows 11, Python 3.11.0, Node 24.18.0) | passed: pip install, `npm --prefix frontend ci` with 0 vulnerabilities |
| `npm run dev` from a clean clone | ready in 15.8 s (first run), 10.2–10.7 s on restarts; backend `127.0.0.1:8000` from the repository root, frontend `localhost:3000` from `frontend/` |
| Real console Ctrl+C (four clean-clone runs and one existing session) | launcher, Uvicorn and Next.js exited within 0.7 s; graceful Uvicorn shutdown; ports 8000 and 3000 released |
| Restart | three restarts reached readiness; saved portfolio revision 1 and threads loaded |
| Existing portfolio data | a copied real `portfolio.db` was logically identical (SQLite `iterdump`) after four start/stop cycles; `current.json` hash unchanged |
| Second `npm run dev` while running | exit 1 with `Port 8000 on 127.0.0.1 is unavailable`; the running session kept serving |
| Provider selection and API-key fallback | ChatGPT-first with an empty `.env`; an `LLM_PROVIDER=openai` key and model selected the API-key provider without exposing the key |
| Launcher tests | 5 passed |
| Regression | backend 887 passed (one existing deprecation warning); mypy and Ruff passed; Playwright 246 passed; frontend typecheck and production build passed |

Defect fixed: `POST /api/ai/provider` accepted the API-key provider without a configured key and model, and saved that selection across restarts. AI requests then had no usable provider. The endpoint now returns 422 and saves nothing; a regression test covers it.

Not verified: real ChatGPT sign-in and inference, a real OpenAI API-key answer, and macOS or Linux startup.
