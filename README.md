# TFS — Personal Investment Analyst

TFS is a local, chat-first investment research app. Ask questions about stocks and your portfolio, inspect dated financial evidence and conditional valuations, compare alternatives, and review proposed changes before explicitly confirming them. **TFS does not place brokerage trades.**

## Quick start: Windows

**You need:** [Git](https://git-scm.com/), **Python 3.11+**, **Node.js 20.9+** (with npm), and PowerShell. To connect using **Sign in with ChatGPT**, run the app on **native Windows**, not WSL: the current credential store uses Windows DPAPI. An eligible ChatGPT account or an OpenAI API key is needed for AI-powered requests; portfolio viewing and editing do not require an AI connection.

### 1. Get the code

Open PowerShell:

~~~powershell
git clone https://github.com/aradhyas8/TFS.git
cd TFS
~~~

You need access to the repository. **While PR #48 remains unmerged**, switch to the branch containing this feature:

~~~powershell
git switch feat/dev-start-chatgpt
~~~

After the feature is merged into the branch you use, the extra switch is unnecessary. If you already have the project, open its root directory in PowerShell instead.

### 2. Install once

From the **TFS repository root**:

~~~powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./backend[dev]"
Copy-Item backend/.env.example backend/.env
npm --prefix frontend ci
~~~

These commands create an isolated Python environment, install the backend, copy the sample configuration, and install the frontend. You do **not** need to add an API key to use the ChatGPT connection option. Keep `LLM_PROVIDER=` and `OPENAI_API_KEY=` blank in a new `backend/.env` for the default ChatGPT-first setup. Do not commit `backend/.env` or any credentials.

### 3. Start TFS

Each time you want to use TFS, run this **single command from the repository root**:

~~~powershell
npm run dev
~~~

Wait until the terminal prints that TFS is ready, then open **http://localhost:3000**. The launcher starts FastAPI on `127.0.0.1:8000` and Next.js on `localhost:3000`, checks readiness, and labels both servers' logs. Keep the terminal open. Press **Ctrl+C** when you are done; it stops both services.

On subsequent visits, just open the project root and run `npm run dev` again. No reinstall or repeated sign-in should be necessary while your saved connection is valid.

### 4. Connect ChatGPT to the AI analyst

1. In TFS, click **Continue with ChatGPT** near the top of the page.
2. Complete the authorization in the system browser opened by TFS. Return to the TFS tab afterward.
3. Open **AI connection** if it is not already expanded. When connected, check that your account is displayed and select a model from the **ChatGPT model** dropdown.
4. Click **Use ChatGPT plan**. TFS uses that selected model for **future** AI requests. If the model list does not appear, try **Refresh connection**. If permission or authorization fails, use **Reconnect ChatGPT**.

Only models available to your account through this connection are listed; availability and plan usage limits vary. This feature uses supported **ChatGPT-plan inference through OpenAI's Responses API**. It **does not** run the Codex coding agent, reuse Codex CLI credentials, or import ChatGPT conversations.

The browser sees connection status, model names and display-account information; credentials and OAuth tokens stay on the local Python backend. This sign-in is **for the AI provider**, not a TFS account login. You can still browse and edit a local portfolio while disconnected.

**Note:** PR #48's simulated OAuth tests and disconnected UI have been verified, but a real-account sign-in and inference smoke test **have not yet been completed**. Do not interpret a successful mocked test as confirmation of account eligibility.

### 5. Ask your first question

You can test the AI **without importing any holdings**. Type this into the TFS chat composer and submit:

> Explain the difference between a stock's market price and intrinsic value in two sentences.

You should receive an AI answer after the connection is active. For portfolio-specific analysis:

1. On the initial **Start with what you own** screen, download a holdings **Template**, or choose an existing holdings CSV.
2. Choose the holdings date and reporting currency, import the file, and resolve any holdings TFS could not identify.
3. **Review changes** and explicitly **Confirm changes** to save the portfolio revision. Importing or previewing alone does not commit it.
4. Open **Portfolio** to inspect holdings and rules, or ask **Review my portfolio for concentration and risk** in chat. You can also choose a workflow such as **Analyze a stock** or **Place new cash**.

The app keeps confirmed portfolio revisions, proposals and conversations on your local backend. Analysis figures are dated; missing prices, FX, financial facts and risk inputs may produce unknown results rather than estimates. **Nothing in TFS executes a real stock trade.**

### Alternative: Use an OpenAI API key

If you cannot use ChatGPT-plan sign-in, edit `backend/.env` and set:

~~~dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=an_available_responses_model
~~~

Use an actual model available to your API account that supports the features TFS needs; replace the placeholders. Restart `npm run dev` after changing backend environment variables. If offered, select **Use API-key provider** in **AI connection**. Never put the API key in frontend code or a `NEXT_PUBLIC_*` variable. API billing and ChatGPT subscription usage are separate.

### Troubleshooting

| Problem | What to do |
| --- | --- |
| `Missing Python virtual environment` | Run the one-time Python setup in step 2 from the repository root. |
| `Missing frontend dependencies` | Run `npm --prefix frontend ci`, then `npm run dev`. |
| Port `3000` or `8000` is already in use | Stop the existing server you own, or close the other TFS session. The launcher does not kill unrelated processes. |
| ChatGPT sign-in is unavailable on WSL/Linux/macOS | Use native Windows for DPAPI-backed ChatGPT login, or configure the API-key provider. Cross-platform launcher startup has not been manually validated on every OS. |
| Sign-in callback port `1455` is busy | Finish or cancel the other sign-in attempt, then retry. |
| Connection expired, permission missing, or no models listed | Open **AI connection**; try **Refresh connection**, then **Reconnect ChatGPT** if necessary. Check account eligibility and permissions. |
| Changing the provider is refused | Finish or cancel the running AI request or sign-in, then try again. |
| The app loads, but AI analysis fails | Check the AI connection/model, backend terminal output, and whether live account/model access actually works. Portfolio management works without AI. |

### macOS/Linux local development

The launcher also has macOS/Linux paths, but **real startup has only been verified on Windows**, and ChatGPT DPAPI storage is not supported on these systems. Use the **API-key provider** there:

~~~sh
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
cp backend/.env.example backend/.env
npm --prefix frontend ci
npm run dev
~~~

For reproducible dependency versions, see `backend/requirements-dev.lock` and `frontend/package-lock.json`. For OAuth lifecycle, test results and security implementation, see [local startup and ChatGPT connection notes](docs/dev-chatgpt/README.md). This is intended for a **personal, loopback-only installation**, not a publicly exposed server.

## Developer and technical documentation

The sections below describe the original financial input formats, calculations, tools and API behavior. For up-to-date architecture and source qualifications, also see the corresponding files in `docs/`.

## Snapshot entry and CSV

Set an as-of date and reporting currency, add accounts, then holdings or cash rows. For direct stocks, use a stable **company ID shared across all accounts and listings** and a company name. A ticker plus exchange identifies the supplied listing. ETFs need a ticker and listing; indirect company exposure stays unknown. Identities remain user supplied unless independently reviewed backend source records resolve and verify them. Verified source metadata can fill omitted issuer/listing fields; conflicts remain unresolved.

Supply shares, an unadjusted mark, its actual date and a source label. Cash uses a balance and currency. For foreign-currency positions, supply a direct FX pair into the reporting currency, its rate, actual date and source. Rates are reporting-currency units per one local-currency unit. Marks and FX must match the snapshot date to support valuation; older, future or missing data stays unknown. No cross rates or older dates are inferred. The optional Bank of Canada adapter supports dated CAD rates and discloses inverse conversion. Quote capture times and price basis can be added in the editor; unknown capture times remain unknown.

Download the CSV header template in the app. A worked fictional example is [examples/portfolio.csv](examples/portfolio.csv), dated **2026-09-30** in **CAD**. Its expected totals are CAD 3,600 portfolio value, CAD 2,300 stock value, CAD 1,300 cash, and CAD 2,300 direct Acme exposure (63.89%). It contains two accounts, two listings and two cash balances. This is a calculation fixture, not an investment suggestion.

The exact CSV header is:

```csv
row_type,id,account_id,account_name,ticker,listing,company_id,company_name,shares,cash,currency,mark,mark_date,mark_source,to_currency,fx_rate,fx_date,fx_source
```

Use `stock`, `etf`, `cash`, `fx`, or `account` row types. An `account` row preserves an empty account. Position rows carry a unique position `id` and consistent `account_id,account_name`; FX rows use `currency,to_currency,fx_rate,fx_date,fx_source`. Fields irrelevant to the row type must be empty. Import replaces the editor snapshot after successful validation; malformed input leaves the current snapshot intact. Files are limited to 1 MB, accounts to 100, positions to 2,000 and FX pairs to 100. This milestone accepts nonnegative shares and cash, and rejects shorts or negative balances explicitly.

## Shared decision boundary

`POST /api/analyze` accepts `{question, portfolio, settings?, proposed_changes?, comparison?, stock?, new_cash?}` and returns `{status, question, portfolio, recommendation, proposals, comparison, stock, allocation}`. `/docs` on FastAPI exposes the complete typed contract. `POST /api/portfolio/csv` accepts `{csv, as_of, reporting_currency}` and returns the same snapshot shape accepted by analysis.

The backend validates the request, binds the snapshot from the data provider, asks the model to call `review_portfolio`, dispatches the allowlisted Python tool, returns its computed result to the model, validates the final recommendation and preserves the deterministic result in the response. The portfolio tool has no arguments: a model cannot replace the submitted holdings or valuation inputs. The loop is bounded to eight turns for exposure review, twelve for selected-stock research, and eighteen for bounded new-cash discovery. Backend-bound `resolve_identities`, `get_quotes` and `get_fx` tools share one request-local evidence set with `review_portfolio`. All take no financial inputs from the model. When alternatives are selected, the model must call `calculate_comparison` with explained future-driver judgments before answering. Production uses one backend-owned OpenAI Responses integration, without extra orchestration frameworks or a database. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) document the integration pattern.

Python uses Decimal arithmetic, pandas grouping across accounts, and NumPy completeness checks. Values are serialized as decimal strings; weights are fractions rounded to eight decimal places. The browser only formats these fields. No adjusted-price returns, dividends, splits, costs or tax effects are inferred from a snapshot.

Missing valuation makes the complete total and all portfolio weights `null`. A separately labeled known subtotal remains available. Missing baseline and guardrails remain `null`. Recommendations at this milestone are limited to review, clarification or no action, and amount must be `null`. Unknown tools, invalid arguments, extra output fields, unsupported numeric prose, trade direction, refusals and incomplete answers fail without a completed recommendation. Explicit hypothetical changes can be checked by `check_proposed_changes`; they are previews, never orders or justified sizing. Qualitative claims still require separate human judgment; schema and arithmetic checks do not establish reasoning quality.

Ticket 02 adds dated financial evidence and refreshed valuation to `analyst.pipeline.analyze`. See [docs/financial-sources.md](docs/financial-sources.md) for source qualification, broker fallback, independently reviewed backend references and optional Bank of Canada FX. Quote source, status, as-of date, capture time and age appear in the same result. Cached/manual valuations are provisional; unusable identity, quote or FX leaves values unknown. `source_inputs_usable` describes source completeness; `sizing_eligible` stays false because this milestone checks exposures without establishing justified sizing. Issuer research, ETF look-through and saved decisions remain in later tickets.

## Explicit portfolio settings and previews

Ticket 03 adds optional `settings`: `single_company_cap`, `active_budget`, `baseline`, `indirect_cap_policy`, and `cash_is_deliberate_tilt`. Weights are decimal fractions between zero and one. No limits, baseline, cash intent or risk score are inferred. Settings live in the frontend form and request-local backend context; there is no persistence or risk-profile subsystem. Clearing a field restores unknown. A baseline can partially specify `stocks`, `diversified_etfs`, `sector_theme_etfs`, and `cash`; supplied weights cannot sum above one. Missing targets remain unknown and are not normalized or filled.

Direct company caps aggregate all accounts and listings. Existing breaches show a conditional reduction to cash retained in the whole portfolio; this is a dated exposure gap before unknown costs and taxes, not an order or an approved exception. Individual stocks count toward the active budget. Each ETF needs an explicit `etf_role` of `diversified` or `sector_theme`, available in the editor after CSV import. Diversified ETFs are excluded; sector/theme ETFs count. Deliberate excess cash contributes `max(cash - baseline.cash × portfolio total, 0)` only with explicit cash intent and a cash baseline. An explicit false cash intent excludes that tilt; absent intent or baseline leaves the contribution unknown. A known active subtotal may breach a budget, but missing contributions cannot clear it.

Choose `direct_only` or `include_known_indirect` as the indirect cap policy. ETF holdings data is still unavailable until ticket 09. If ETFs are present and a cap could depend on overlap, a below-cap direct position does not clear the cap; missing overlap stays unknown. A direct breach remains a breach and its reduction is only a lower bound when overlap could count.

`proposed_changes` contains `new_cash: [{cash_position_id, amount}]` and `trades: [{position_id, shares_change, cash_position_id}]`. Both arrays are required when a preview is supplied. It references existing rows: new cash uses the selected balance's currency, and each share change must use a cash balance in the same account and quote currency. Positive share changes consume cash; negative changes return proceeds to cash. Duplicate changes, short positions and negative final cash are disallowed. The backend calculates pre/post weights with all existing cash and explicit new cash using the same dated marks and FX. It does not infer transfers, currency execution, costs or tax effects.

The model's `check_proposed_changes` tool uses the same Python checks and cannot change holdings identities, valuation inputs or settings. Model previews must use exactly the user-supplied new cash, or none if absent. Every checked preview remains in the result. A cap/budget breach produces `blocked`; missing applicable inputs produce `unknown`. Either prevents the final recommendation from clearing the preview, regardless of model conviction. `within_limits` means only that the supplied limits passed under the dated valuation; allocation amount remains unknown. Automated tests cover the API and rendered browser result using fake providers only.

## Conditional ETF, cash and no-action comparison

Ticket 04 extends this same request with optional selected alternatives. Enable the comparison, select actual holdings/cash as a common capital basis, and choose a supplied diversified ETF, cash or short government bills, and/or no action. A candidate ETF can be entered with zero shares and a dated mark. No action keeps each actual selected holding and cash balance; retained stock outcomes remain unknown until stock-specific analysis is available.

Supply available dated fund exposure, costs and income; blanks mean unknown and explicit zero means known zero. Optional transaction cost and terminal tax amounts are in reporting currency with their date and source. The backend validates model judgments, calculates all three cases and exposes future paths separately from supplied facts. Known subtotals exclude unknown effects; fully specified values remain conditional, nominal outcomes. Five years is not a sell date, and long-term real-wealth aspirations are not forecast hurdles. See [docs/etf-cash-comparison.md](docs/etf-cash-comparison.md) for the contract, formulas and fixture review.

## Verify

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest
..\.venv\Scripts\python.exe -m mypy analyst
..\.venv\Scripts\python.exe -m ruff check .
cd ../frontend
npm run typecheck
npm test
npm run build
```

Browser tests use installed Microsoft Edge in headless mode and start dedicated Next.js and FastAPI test servers on ports 3100 and 8100. On another OS, set `TEST_PYTHON` to the virtualenv executable and change Playwright's browser channel to an installed browser. Every automated journey uses fake model/data providers, with real request handling and Python calculations. An HTTP transport fixture also exercises the production OpenAI SDK request shape. Tests prohibit external network connections and use dummy credentials only; no live model or market-data request occurs. Browser tests check rendered values, question correspondence, failure display, and actual browser-script absence of the dummy key.

Tests are at the two seams approved by the ticket: the public FastAPI request/result boundary and the whole browser input-to-result journey. Fixed scripted answers validate the integration and rendering, not investment reasoning quality. No live answer-quality assessment is claimed.


## US stock questions

Ticket 05 adds the US stock selector to the existing question form and returns dated SEC/issuer evidence, company operating-driver cases, exit sensitivity and portfolio-aware conditional actions. Company, fund, cash and no-action alternatives use the shared comparison pipeline. Configure backend-only `RESEARCH_REFERENCE_FILE` with independently reviewed primary excerpts; missing records stay unknown. This adapter does not claim live filing retrieval. Allocation amounts remain undetermined. See [docs/us-stock-analysis.md](docs/us-stock-analysis.md) for provenance, methods, formulas, setup and verification limits.


## New-cash questions

Ticket 06 adds **Analyze new cash** beside the existing question input. Confirm the additional amount, intended account/currency balance, and loss/withdrawal context. The same backend pipeline performs a request-local bounded opportunity screen, researches at most two decision-changing stocks, compares them with broad ETF/cash/no action, and calculates an approximate range only when source inputs and post-allocation caps/budget justify it. Missing inputs produce completed conditional guidance. Optional backend-only `DISCOVERY_REFERENCE_FILE` widens the dated screen beyond supplied holdings; unavailable or older coverage is openly qualified. See [docs/new-cash-allocation.md](docs/new-cash-allocation.md) for the contracts, arithmetic, coverage limits and controlled answer review.


## Portfolio re-underwriting

Ticket 07 adds **Review holdings and rebalance** to the existing question flow. Supply optional dated prior company theses, risk context, limits and a baseline. The shared backend reviews current evidence and conditional cases, challenges material thesis changes, checks proposed outcomes and provides an approximate adjustment range only when all required inputs are supported. Baseline-free exposure review works; unknown ETF overlap and uncovered company cases remain explicit. See [docs/portfolio-reunderwriting.md](docs/portfolio-reunderwriting.md) for contracts, formulas, setup and limitations.


## Theme discovery

Ticket 08 adds **Explore a theme** to the existing question flow. Agree an economic mechanism, supplied US stock/ETF shortlist and tool-call effort before research. The same backend tests the mechanism against dated primary evidence, calculates instrument-appropriate cases, compares ETF/cash/no action and applies portfolio guardrails. A theme can lead to no action; unsupported amounts remain undetermined. See [docs/theme-discovery.md](docs/theme-discovery.md) for agreement, bounds, evidence and verification limits.

## ETF indirect overlap and look-through

Ticket 09 adds dated ETF company overlap look-through, recursive nested fund decomposition, honest coverage labeling (`full`, `partial`, `unknown`, `stale`, `none`), and enforcement of user's explicit `indirect_cap_policy` (`direct_only` vs `include_known_indirect`) without double counting or blocking sizing on partial coverage. Optional backend-only `SPONSOR_HOLDINGS_REFERENCE_FILE` supplies dated sponsor constituents; outdated or contradictory snapshots are marked stale. See [docs/etf-indirect-overlap.md](docs/etf-indirect-overlap.md) for contracts, formulas, cycle prevention, and limitations.

## Canadian stock evidence support

Ticket 10 extends stock assessment to Canadian issuers using permitted issuer evidence and exact user-opened SEDAR+ verification links (`www.sedarplus.ca`). Supported Canadian exchanges include `XTSE` (TSX), `XTSX` (TSX Venture), `NEOE` (Cboe Canada), and `XCNQ` (CSE). The single shared FastAPI decision pipeline dispatches `get_sedar_filings` dynamically for Canadian securities while forbidding SEC retrieval for non-US issuers and SEDAR retrieval for US issuers (enforced via 502 Bad Gateway). SEDAR+ links must target valid, specific document URLs; automated SEDAR+ scraping or local database construction is strictly prohibited. The feature integrates seamlessly across stock assessment, new cash allocation, portfolio review (re-underwriting), and theme exploration through the common evidence boundary. See [docs/canadian-stock-evidence.md](docs/canadian-stock-evidence.md) for contracts, evidence boundaries, and verification procedures.

## Portfolio Actions and SQLite Persistence

The application uses an explicit action model and an ACID SQLite store (`portfolio.db`) located in the portfolio directory (configured via `PORTFOLIO_DIR`, defaulting to `backend/data/portfolio`).

### Coordinated Deployment and Cutover
- **Atomic Deployment**: Deploy the coordinated frontend and backend together. Tickets 02–09 must not be deployed as partial cutovers.
- **Zero Direct-Write Paths**: There are no write bypasses, compatibility switches, or dual writes. All mutations (imports, manual edits, ETF roles, benchmarks, holding identifications) prepare immutable proposals (`POST /api/portfolio/import`, `PUT /api/portfolio`, `POST /api/portfolio/identify`) and require explicit user confirmation via `POST /api/portfolio/proposals/{id}/confirm` to commit new revisions.
- **Rollback Limits & Stale State**: If rolling back to the legacy JSON-only application after SQLite commits have occurred, the legacy system will only see the old `current.json` file. Any revisions committed to SQLite will not be visible to the legacy application, resulting in stale portfolio state. Do not roll back to the JSON-only version once SQLite revisions have been committed.

### Legacy Migration
- On first startup with SQLite persistence, if a legacy `current.json` file exists and the database has no prior revisions, the database automatically performs a one-time migration.
- All portfolio fields (snapshot date, reporting currency, accounts, positions, cash, FX, average costs, rules/settings, benchmark, and unresolved holdings) are copied into Revision 1.
- The original `current.json` is preserved untouched as a historical record.
- Re-running or restarting the server is idempotent and will not re-import or increment revisions once the migration marker is recorded.
- Malformed legacy JSON halts startup with a `LegacyMigrationError` to prevent silent corruption or empty portfolio initialization.

### Backups and Disaster Recovery
- **Backups**: To back up the portfolio database, use SQLite backup API or copy `portfolio.db` (along with `-wal` and `-shm` files if SQLite is running in WAL mode). For cold backups, simply copy `portfolio.db` while the server process is stopped.
- **Recovery**: To restore from a backup, stop the application, replace `portfolio.db` with the backup file, and remove any leftover `-wal` or `-shm` files. On startup, the application verifies the schema and resumes from the restored revision.
- **Restart Guarantees**: App restarts preserve all committed revisions, pending proposals, and receipts. Unconfirmed proposals remain in `pending` status and can be reviewed or confirmed after restart.


